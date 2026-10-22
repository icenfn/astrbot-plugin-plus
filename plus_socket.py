"""astrbot_plugin_plus 的 IM 服务端（HTTP API + Socket.IO 混合架构）。

单端口（默认 ``6199``）同时挂载：

* ``/api/*``      —— REST 风格 HTTP API（登录、agent 列表、好友列表、消息漫游）；
* ``/socket.io``  —— 实时双向通道（流式对话、私聊推送）；
* ``/health`` / ``/`` —— 健康检查页。

握手鉴权统一用 JWT：

* HTTP  ``Authorization: Bearer <jwt>``；
* Socket.IO  ``io(url, {auth:{token:"<jwt>"}})``。

数据库用 SQLite（:mod:`plus_db`），私聊与 agent 对话历史全部落库，支持漫游。
用户与 agent 的会话隔离通过 ``session_id = f"u{user_id}:a{bot_id}"`` 实现。
"""

from __future__ import annotations

import asyncio
import html
import time
import uuid
from pathlib import Path
from typing import Any

try:  # aiohttp 随 AstrBot 一并提供
    from aiohttp import web
except Exception:  # noqa: BLE001
    web = None  # type: ignore[assignment]

try:
    import socketio
    from socketio.exceptions import ConnectionRefusedError
except Exception:  # noqa: BLE001
    socketio = None  # type: ignore[assignment]
    ConnectionRefusedError = ConnectionRefusedError  # type: ignore[misc,assignment]

try:
    import bcrypt
except Exception:  # noqa: BLE001
    bcrypt = None  # type: ignore[assignment]

try:
    import jwt as pyjwt
except Exception:  # noqa: BLE001
    pyjwt = None  # type: ignore[assignment]

from sqlmodel import select

try:
    from astrbot.api import logger
except Exception:  # noqa: BLE001
    import logging as _logging

    logger = _logging.getLogger("astrbot_plugin_plus")

def _load_sibling(name: str) -> Any:
    """以稳定模块名导入同目录兄弟模块。

    AstrBot 既可能以包形式（``astrbot_plugin_plus.plus_socket``）加载插件，
    也可能以顶层模块名加载；若两条路径各导入一次，同一份 ``plus_db`` 会被
    执行两次，从而在同一个 ``SQLModel.metadata`` 上重复声明 ``users`` 等表，
    抛出 ``Table 'users' is already defined``。这里统一先按所属包导入，并把
    结果登记到 ``sys.modules``，保证裸导入命中同一个模块对象。
    """
    import importlib
    import importlib.util
    import sys

    pkg = __package__
    if pkg:  # 1) 包内相对导入（生产环境）
        try:
            mod = importlib.import_module(f".{name}", pkg)
            sys.modules.setdefault(name, mod)
            return mod
        except Exception:  # noqa: BLE001
            pass

    cached = sys.modules.get(name)
    if cached is not None:  # 2) 复用已加载的顶层模块
        return cached
    try:  # 3) 顶层导入（插件目录可能已在 sys.path 中）
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001
        pass

    # 4) 兜底：按文件路径加载，仍登记到 sys.modules 以便后续复用
    file = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, file)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件模块 {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


try:  # 平台适配器：注册后 AstrBot WebUI 的「创建机器人」会出现 astrbot_plus
    plus_platform: Any = _load_sibling("plus_platform")
except Exception:  # noqa: BLE001
    plus_platform = None  # type: ignore[assignment]

db = _load_sibling("plus_db")

DEFAULT_PORT = 6199
JWT_ALG = "HS256"
# token 有效期默认 30 天；配置为 0 表示永久有效（payload 不带 exp）。
JWT_EXPIRE_DAYS = 30


class PlusSocketServer:
    """IM 服务端：在独立后台线程中运行的 HTTP + Socket.IO 混合服务。"""

    def __init__(
        self,
        host: str,
        port: int,
        data_dir: str | Path = ".",
        version: str = "0.9.0",
        token_expire_days: int = JWT_EXPIRE_DAYS,
    ) -> None:
        self.host = host or "0.0.0.0"
        self.port = int(port or DEFAULT_PORT)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.version = version
        # token 有效期（天）；0 = 永久有效。
        self.token_expire_days = max(0, int(token_expire_days))

        # 初始化数据库（引擎创建是同步操作，建表在事件循环里做）。
        db.init_db(self.data_dir / "astrbot_plugin_plus.db")
        db.set_db_path(self.data_dir / "astrbot_plugin_plus.db")
        db.admin_ensure_schema()

        # JWT 密钥：首次启动生成一个写进 data_dir/secret.key，之后复用。
        self._secret = self._load_or_create_secret()

        # 连接 / 路由状态
        self._sid_user: dict[str, dict[str, Any]] = {}  # sid -> {id,email,username}
        self._user_sids: dict[int, set[str]] = {}      # user_id -> set(sid)
        # agent 对话路由：session_id(u{uid}:a{botid}) -> {sid,reqId,userId,botId}
        self._routes: dict[str, dict[str, Any]] = {}
        self._stream_buf: dict[str, str] = {}
        self._outbound_registered = False

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Any = None
        self._runner: Any = None
        self._site: Any = None
        # 生命周期状态：_started 在启动流程（建表 / 监听）结束后置位；
        # _stopping 记录「启动过程中已被要求停止」，避免在 bootstrap 尚未完成时
        # 调用 loop.stop() 触发 "Event loop stopped before Future completed"。
        import threading as _threading

        self._lifecycle_lock = _threading.Lock()
        self._started = _threading.Event()
        self._stopping = False
        self._available = (
            socketio is not None and web is not None and bcrypt is not None and pyjwt is not None
        )

        self.sio = (
            socketio.AsyncServer(
                async_mode="aiohttp",
                cors_allowed_origins="*",
                logger=False,
                engineio_logger=False,
                ping_interval=25,
                ping_timeout=60,
            )
            if self._available
            else None
        )
        self._app = (
            web.Application(middlewares=[self._cors_middleware])
            if self._available
            else None
        )
        if self._available:
            self.sio.attach(self._app)
            self._register_http()
            self._register_handlers()

    # ------------------------------------------------------------------ secret
    def _load_or_create_secret(self) -> str:
        path = self.data_dir / "secret.key"
        try:
            if path.exists():
                data = path.read_text(encoding="utf-8").strip()
                if data:
                    return data
            data = uuid.uuid4().hex + uuid.uuid4().hex
            path.write_text(data, encoding="utf-8")
            try:
                path.chmod(0o600)
            except Exception:  # noqa: BLE001
                pass
            return data
        except Exception:  # noqa: BLE001
            # 兜底：内存里的随机密钥（重启后旧 token 全部失效，但不致崩）
            return uuid.uuid4().hex

    # ------------------------------------------------------------------ JWT
    def _issue_token(self, user: db.User) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "email": user.email,
            "iat": now,
        }
        # expire_days == 0 表示永久有效，此时不写入 exp 声明。
        if self.token_expire_days > 0:
            payload["exp"] = now + self.token_expire_days * 86400
        return pyjwt.encode(payload, self._secret, algorithm=JWT_ALG)  # type: ignore[union-attr]

    def _decode_token(self, token: str) -> dict[str, Any] | None:
        try:
            return pyjwt.decode(token, self._secret, algorithms=[JWT_ALG])  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[AstrBot+] JWT decode failed: {exc!r}")
            return None

    async def _auth_request(self, request: Any) -> db.User | None:
        """从 HTTP Authorization 头解析 JWT 并加载用户；失败返回 None。"""
        hdr = request.headers.get("Authorization", "")
        if not hdr.startswith("Bearer "):
            return None
        payload = self._decode_token(hdr[7:].strip())
        if not payload:
            return None
        try:
            uid = int(payload.get("sub"))
        except Exception:  # noqa: BLE001
            return None
        async with db.session() as s:
            user = (await s.exec(select(db.User).where(db.User.id == uid))).first()
            if user is None or user.status != 1:
                return None
            return user

    # ------------------------------------------------------------------ 生命周期
    @property
    def available(self) -> bool:
        return self._available

    @property
    def online_count(self) -> int:
        return len(self._sid_user)

    def start(self) -> None:
        if not self._available:
            logger.warning(
                "[AstrBot+] 缺少 aiohttp/socketio/bcrypt/PyJWT 依赖，IM 服务端未启动。"
            )
            return
        import threading

        with self._lifecycle_lock:
            if self._thread and getattr(self._thread, "is_alive", lambda: False)():
                return
            self._stopping = False
            self._started.clear()
            self._thread = threading.Thread(
                target=self._run, name="astrbot-plus-im", daemon=True
            )
            self._thread.start()

    async def _bootstrap(self) -> None:
        """在当前线程的事件循环里完成建表与监听，全部成功后才返回。"""
        try:
            await db.create_all()
        except Exception as exc:  # noqa: BLE001
            # 异步建表失败不致命：admin_ensure_schema 会用 sqlite3 同步补齐
            # 同样的表结构（CREATE TABLE IF NOT EXISTS），服务仍可继续启动。
            logger.warning(f"[AstrBot+] 异步建表失败，改用同步建表兜底：{exc}")
            db.admin_ensure_schema()

        self._runner = web.AppRunner(self._app)  # type: ignore[union-attr]
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._register_outbound()
        try:
            loop.run_until_complete(self._bootstrap())
            self._started.set()
            if self._stopping:  # 启动过程中已要求停止：直接走清理
                logger.info("[AstrBot+] 启动完成前收到停止请求，IM 服务端不进入服务状态。")
                return
            logger.info(f"[AstrBot+] IM 服务端已启动：http://{self.host}:{self.port}")
            loop.run_forever()
        except Exception as exc:  # noqa: BLE001
            import traceback

            logger.error(f"[AstrBot+] IM 服务端启动失败：{exc}\n{traceback.format_exc()}")
        finally:
            self._started.set()
            self._teardown(loop)

    def _teardown(self, loop: asyncio.AbstractEventLoop) -> None:
        """清理 aiohttp 资源并关闭事件循环（幂等、可重入）。"""
        runner = self._runner
        self._runner = None
        self._site = None
        if runner is not None and not loop.is_closed():
            try:
                loop.run_until_complete(runner.cleanup())
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[AstrBot+] 清理 IM 服务端资源时忽略异常：{exc}")
        if not loop.is_closed():
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    def stop(self, timeout: float = 5.0) -> None:
        """停止 IM 服务端。

        只有启动流程已经结束（``_started`` 置位）时才向事件循环投递 ``stop()``，
        否则会在 ``run_until_complete(self._bootstrap())`` 尚未完成时中断它，
        表现为 ``Event loop stopped before Future completed``。
        """
        import threading

        with self._lifecycle_lock:
            self._stopping = True
            loop = self._loop
            thread = self._thread
            started = self._started.is_set()

        if loop is not None and not loop.is_closed():
            if started and loop.is_running():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:  # 循环已关闭
                    pass
            elif not started:
                # 启动中：投递一个唤醒回调，让 run_until_complete 结束后尽快退出
                try:
                    loop.call_soon_threadsafe(lambda: None)
                except RuntimeError:
                    pass

        if (
            thread is not None
            and getattr(thread, "is_alive", lambda: False)()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("[AstrBot+] IM 服务端已停止。")

    def call_on_loop(self, coro: Any) -> Any:
        """从任意线程把协程调度到 IM 事件循环并返回 concurrent.futures.Future。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("IM 事件循环未运行")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    # ------------------------------------------------------------------ HTTP API
    @staticmethod
    @web.middleware
    async def _cors_middleware(request: Any, handler: Any) -> Any:
        """为 IM 端口的 HTTP API 补上跨域响应头。

        客户端是 Tauri webview / 浏览器，与服务端不同源；socket.io 层由
        `cors_allowed_origins` 处理，但 aiohttp 的 `/api/*` 之前完全没有跨域头，
        浏览器会把响应拦在 JS 之外，`fetch` 直接抛 `Failed to fetch`。
        """
        if request.method == "OPTIONS":
            response: Any = web.Response(status=204)
        else:
            try:
                response = await handler(request)
            except web.HTTPException as exc:  # 404/405 等也要带上跨域头
                response = exc
        origin = request.headers.get("Origin") or "*"
        headers = response.headers
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Accept, X-Requested-With"
        )
        headers["Access-Control-Max-Age"] = "86400"
        headers["Vary"] = "Origin"
        return response

    async def _handle_options(self, request: Any) -> Any:
        """兜底 OPTIONS：路由未命中时 aiohttp 会在中间件之前 405，故显式兜住。"""
        return web.Response(status=204)

    def _register_http(self) -> None:
        app = self._app
        assert app is not None  # noqa: S101
        app.router.add_route("OPTIONS", "/{tail:.*}", self._handle_options)
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/health", self._handle_health)
        app.router.add_post("/api/auth/login", self._api_login)
        app.router.add_get("/api/agents", self._api_agents)
        app.router.add_get("/api/users", self._api_users)
        app.router.add_get("/api/messages", self._api_messages)

    @staticmethod
    def _json(data: Any, status: int = 200) -> Any:
        return web.json_response(data, status=status)

    async def _handle_health(self, request: Any) -> Any:
        return self._json(
            {
                "ok": True,
                "version": self.version,
                "service": "astrbot-plugin-plus",
            }
        )

    async def _handle_index(self, request: Any) -> Any:
        version = html.escape(self.version)
        page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>AstrBot+ IM Server</title></head>
<body style="font-family:sans-serif;background:#0e1621;color:#e9edf1;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0">
<div style="max-width:480px;padding:24px">
<h1 style="margin:0 0 8px">AstrBot+ IM Server ✅</h1>
<p style="color:#9ccc9c">v{version} · online connections: {self.online_count}</p>
<p style="color:#8fa3b5">REST: /api/* · Socket.IO: /socket.io · Health: /health</p>
</div></body></html>"""
        return web.Response(text=page, content_type="text/html")

    async def _api_login(self, request: Any) -> Any:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        if not email or not password:
            return self._json({"error": "email and password required"}, status=400)
        async with db.session() as s:
            user = (
                await s.exec(select(db.User).where(db.User.email == email))
            ).first()
            if user is None or user.status != 1:
                return self._json({"error": "invalid credentials"}, status=401)
            try:
                ok = bcrypt.checkpw(  # type: ignore[union-attr]
                    password.encode("utf-8"),
                    (user.password_hash or "").encode("utf-8"),
                )
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                return self._json({"error": "invalid credentials"}, status=401)
            token = self._issue_token(user)
            return self._json(
                {
                    "token": token,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "username": user.username,
                        "df": user.df,
                    },
                }
            )

    async def _api_agents(self, request: Any) -> Any:
        user = await self._auth_request(request)
        if user is None:
            return self._json({"error": "unauthorized"}, status=401)
        bots = self._list_bots()
        allowed = db.parse_allowed_agents(user.allowed_agents)
        out: list[dict[str, Any]] = []
        for b in bots:
            bid = str(b.get("id"))
            # 语义：只有白名单内的 agent 可用；空列表 = 无可用 agent。
            if bid not in allowed:
                continue
            out.append(
                {
                    "id": bid,
                    "name": b.get("name") or bid,
                    "botdf": b.get("botdf") or "",
                    # 管理端填写的备注，供客户端在 Agent 详情页展示
                    "remark": b.get("remark") or "",
                    "enabled": True,
                }
            )
        return self._json(out)

    async def _api_users(self, request: Any) -> Any:
        me = await self._auth_request(request)
        if me is None:
            return self._json({"error": "unauthorized"}, status=401)
        async with db.session() as s:
            rows = (
                await s.exec(select(db.User).where(db.User.status == 1))
            ).all()
        out = [db.user_public(u) for u in rows if u.id != me.id]
        return self._json(out)

    async def _api_messages(self, request: Any) -> Any:
        me = await self._auth_request(request)
        if me is None:
            return self._json({"error": "unauthorized"}, status=401)
        qs = request.query
        peer_type = qs.get("peerType", "")
        peer_id = qs.get("peerId", "")
        if peer_type not in ("agent", "user") or not peer_id:
            return self._json({"error": "peerType=agent|user & peerId required"}, status=400)
        try:
            limit = max(1, min(200, int(qs.get("limit", 50))))
        except Exception:  # noqa: BLE001
            limit = 50
        before = qs.get("before")
        try:
            before_f = float(before) if before else None
        except Exception:  # noqa: BLE001
            before_f = None

        async with db.session() as s:
            stmt = (
                select(db.Message)
                .where(db.Message.user_id == me.id)
                .where(db.Message.peer_type == peer_type)
                .where(db.Message.peer_id == str(peer_id))
            )
            if before_f is not None:
                stmt = stmt.where(db.Message.ts < before_f)
            stmt = stmt.order_by(db.Message.ts.desc()).limit(limit)
            rows = (await s.exec(stmt)).all()

        out: list[dict[str, Any]] = []
        for m in reversed(rows):  # 返回按时间正序
            from_uid = m.from_user_id
            if peer_type == "agent":
                role = "user" if from_uid == me.id else "assistant"
                to_uid = peer_id
            else:
                role = "user" if from_uid == me.id else "assistant"
                to_uid = int(peer_id) if from_uid == me.id else from_uid
            out.append(
                {
                    "id": m.id,
                    "peerType": peer_type,
                    "peerId": peer_id,
                    "fromUserId": from_uid,
                    "toUserId": to_uid,
                    "text": m.text,
                    "ts": m.ts,
                    "role": role,
                }
            )
        return self._json(out)

    # ------------------------------------------------------------------ Socket.IO
    def _register_handlers(self) -> None:
        sio = self.sio
        assert sio is not None  # noqa: S101

        @sio.event
        async def connect(sid: str, environ: Any, auth: Any) -> None:
            auth = auth or {}
            token = auth.get("token") if isinstance(auth, dict) else None
            token = str(token or "")
            payload = self._decode_token(token)
            if not payload:
                logger.info(f"[AstrBot+] socket 连接被拒（无/坏 token）：{sid}")
                raise ConnectionRefusedError("unauthorized")
            try:
                uid = int(payload.get("sub"))
            except Exception:  # noqa: BLE001
                raise ConnectionRefusedError("unauthorized")
            async with db.session() as s:
                user = (await s.exec(select(db.User).where(db.User.id == uid))).first()
            if user is None or user.status != 1:
                raise ConnectionRefusedError("unauthorized")
            self._sid_user[sid] = {
                "id": user.id,
                "email": user.email,
                "username": user.username,
            }
            self._user_sids.setdefault(user.id, set()).add(sid)
            logger.info(f"[AstrBot+] 用户 {user.username}({user.id}) 已连接 sid={sid}")

        @sio.event
        async def disconnect(sid: str) -> None:
            info = self._sid_user.pop(sid, None)
            if info and info.get("id") in self._user_sids:
                self._user_sids[info["id"]].discard(sid)
            # 清理以该 sid 为目标的路由
            dead = [k for k, v in self._routes.items() if v.get("sid") == sid]
            for k in dead:
                self._routes.pop(k, None)
            logger.info(f"[AstrBot+] 客户端断开：{sid}")

        @sio.on("chat:send")
        async def chat_send(sid: str, data: Any = None) -> None:
            data = data or {}
            me = self._sid_user.get(sid)
            if me is None:
                return
            req_id = str(data.get("reqId") or "")
            bot_id = str(data.get("botId") or "")
            text = str(data.get("text") or "")

            # 校验 bot 是否存在且允许该用户使用
            bots = self._list_bots()
            bot = next((b for b in bots if str(b.get("id")) == bot_id), None)
            if bot is None:
                await self.sio.emit(
                    "chat:error",
                    {"reqId": req_id, "message": "agent 不存在或已停用。"},
                    to=sid,
                )
                return
            async with db.session() as s:
                user = (
                    await s.exec(select(db.User).where(db.User.id == me["id"]))
                ).first()
            allowed = db.parse_allowed_agents(user.allowed_agents) if user else []
            # 语义：只允许白名单内的 agent；空列表表示该用户没有任何可用 agent。
            if bot_id not in allowed:
                await self.sio.emit(
                    "chat:error",
                    {"reqId": req_id, "message": "你没有权限使用该 agent。"},
                    to=sid,
                )
                return

            session_id = f"u{me['id']}:a{bot_id}"
            self._routes[session_id] = {
                "sid": sid,
                "reqId": req_id,
                "userId": me["id"],
                "botId": bot_id,
                "username": me.get("username", ""),
            }

            # 落库用户消息（漫游）
            now = time.time()
            async with db.session() as s:
                s.add(
                    db.Message(
                        peer_type="agent",
                        user_id=me["id"],
                        peer_id=bot_id,
                        from_user_id=me["id"],
                        text=text,
                        ts=now,
                    )
                )
                await s.commit()

            await self.sio.emit(
                "chat:session",
                {"reqId": req_id, "sessionId": session_id},
                to=sid,
            )
            # 列表副标题 / 未读数刷新
            await self._emit_to_user(
                me["id"],
                "message:new",
                {
                    "peerType": "agent",
                    "peerId": bot_id,
                    "role": "user",
                    "text": text,
                    "ts": now,
                },
            )

            if plus_platform is None or not plus_platform.has_adapters():
                await self.sio.emit(
                    "chat:error",
                    {"reqId": req_id, "message": "尚无可用的 AstrBot+ agent。"},
                    to=sid,
                )
                self._routes.pop(session_id, None)
                return

            self.sio.start_background_task(
                self._dispatch_chat, sid, session_id, bot_id, text, req_id
            )

        @sio.on("private:send")
        async def private_send(sid: str, data: Any = None) -> None:
            data = data or {}
            me = self._sid_user.get(sid)
            if me is None:
                return
            req_id = str(data.get("reqId") or "")
            to_uid_raw = data.get("toUserId")
            text = str(data.get("text") or "")
            try:
                to_uid = int(to_uid_raw)
            except Exception:  # noqa: BLE001
                await self.sio.emit(
                    "private:message",
                    {"id": 0, "fromUserId": me["id"], "fromUsername": me["username"],
                     "text": "", "ts": 0, "error": "bad toUserId"},
                    to=sid,
                )
                return
            now = time.time()
            # 校验接收方存在且启用
            async with db.session() as s:
                target = (
                    await s.exec(select(db.User).where(db.User.id == to_uid))
                ).first()
                if target is None or target.status != 1:
                    await self.sio.emit(
                        "chat:error",
                        {"reqId": req_id, "message": "接收方不存在或已禁用。"},
                        to=sid,
                    )
                    return
                # 双方会话各写一行
                msg_id = None
                row_me = db.Message(
                    peer_type="user",
                    user_id=me["id"],
                    peer_id=str(to_uid),
                    from_user_id=me["id"],
                    text=text,
                    ts=now,
                )
                s.add(row_me)
                row_peer = db.Message(
                    peer_type="user",
                    user_id=to_uid,
                    peer_id=str(me["id"]),
                    from_user_id=me["id"],
                    text=text,
                    ts=now,
                )
                s.add(row_peer)
                await s.commit()
                msg_id = row_me.id

            # 回推给发送方自己（带 reqId 便于客户端匹配 pending；toUserId 指明会话对象）
            mine = {
                "id": msg_id,
                "reqId": req_id,
                "fromUserId": me["id"],
                "fromUsername": me["username"],
                "toUserId": to_uid,
                "text": text,
                "ts": now,
            }
            await self._emit_to_user(me["id"], "private:message", mine)
            # 推给接收方
            theirs = {
                "id": msg_id,
                "reqId": req_id,
                "fromUserId": me["id"],
                "fromUsername": me["username"],
                "toUserId": to_uid,
                "text": text,
                "ts": now,
            }
            await self._emit_to_user(to_uid, "private:message", theirs)
            # 列表刷新
            await self._emit_to_user(
                me["id"],
                "message:new",
                {"peerType": "user", "peerId": str(to_uid),
                 "role": "user", "text": text, "ts": now},
            )
            await self._emit_to_user(
                to_uid,
                "message:new",
                {"peerType": "user", "peerId": str(me["id"]),
                 "role": "assistant", "text": text, "ts": now},
            )

    # ------------------------------------------------------------------ 广播辅助
    async def _emit_to_user(self, user_id: int, event: str, payload: dict[str, Any]) -> None:
        """把事件发给某用户的所有在线连接。"""
        sids = list(self._user_sids.get(user_id, set()))
        for sid in sids:
            try:
                await self.sio.emit(event, payload, to=sid)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[AstrBot+] emit {event} -> {sid} 失败：{exc}")

    async def broadcast(self, event: str, payload: dict[str, Any]) -> None:
        """广播给所有在线连接（线程安全）。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self.sio.emit(event, payload), loop)

    # ------------------------------------------------------------------ 出站桥接
    def _register_outbound(self) -> None:
        if plus_platform is not None and not self._outbound_registered:
            plus_platform.set_outbound(self._outbound)
            self._outbound_registered = True

    def _outbound(self, session_id: str, text: str, meta: dict[str, Any]) -> None:
        """平台适配器（AstrBot 主循环线程）回调，切回 IM 事件循环。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._emit_out(session_id, text, meta), loop
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 出站调度失败：{exc}")

    async def _emit_out(self, session_id: str, text: str, meta: dict[str, Any]) -> None:
        route = self._routes.get(session_id)
        if not route:
            return
        sid = route["sid"]
        req_id = route.get("reqId", "")
        user_id = route["userId"]
        bot_id = route["botId"]
        try:
            if meta.get("done"):
                streamed = self._stream_buf.pop(session_id, "")
                full = text if text else streamed
                if full:
                    # 落库 assistant 回复
                    now = time.time()
                    async with db.session() as s:
                        s.add(
                            db.Message(
                                peer_type="agent",
                                user_id=user_id,
                                peer_id=bot_id,
                                from_user_id=None,
                                text=full,
                                ts=now,
                            )
                        )
                        await s.commit()
                    await db.bump_token_usage(user_id, len(full))
                    await self.sio.emit(
                        "chat:done",
                        {"reqId": req_id, "text": full, "sessionId": session_id},
                        to=sid,
                    )
                    await self._emit_to_user(
                        user_id,
                        "message:new",
                        {
                            "peerType": "agent",
                            "peerId": bot_id,
                            "role": "assistant",
                            "text": full,
                            "ts": now,
                        },
                    )
                else:
                    await self.sio.emit(
                        "chat:done",
                        {"reqId": req_id, "text": "", "sessionId": session_id},
                        to=sid,
                    )
                self._routes.pop(session_id, None)
            elif text:
                self._stream_buf[session_id] = self._stream_buf.get(session_id, "") + text
                await self.sio.emit(
                    "chat:delta", {"reqId": req_id, "delta": text}, to=sid
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 出站推送失败：{exc}")

    async def _dispatch_chat(
        self, sid: str, session_id: str, bot_id: str, text: str, req_id: str
    ) -> None:
        me = self._sid_user.get(sid) or {}
        try:
            ok = await plus_platform.dispatch_incoming(  # type: ignore[union-attr]
                bot_id,
                session_id,
                text,
                sender_id=str(me.get("id", "client")),
                sender_name=str(me.get("username", "AstrBot+ 用户")),
            )
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.warning(f"[AstrBot+] 派发到平台失败：{exc}")
        if not ok:
            await self.sio.emit(
                "chat:error",
                {"reqId": req_id, "message": "无法将消息交给 agent 处理。"},
                to=sid,
            )
            self._routes.pop(session_id, None)

    # ------------------------------------------------------------------ bots
    def _list_bots(self) -> list[dict[str, Any]]:
        if plus_platform is None:
            return []
        try:
            return plus_platform.list_bots()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 读取机器人列表失败：{exc}")
            return []
