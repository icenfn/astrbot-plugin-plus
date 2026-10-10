"""astrbot_plugin_plus 的 Socket.io 服务端。

设计要点（对应客户端 AstrBot+ 的需求）：

* 插件单独监听一个端口（默认 ``6199``），对外 **只暴露这一个端口**；AstrBot 的
  WebUI / 主服务端口保持不变。客户端只需连接 ``ws://<host>:<port>``。
* 全流程走 Socket.io：机器人列表、对话（Webchat 会话）列表 / 新建 / 删除、
  历史记录、以及流式聊天，全部通过事件完成，不再依赖插件 HTTP Web API。
* 服务端拉取 + 客户端本地缓存：本服务端负责调用 AstrBot 自身的 HTTP API
  （``/api/config/platform/list``、``/api/v1/chat/...``）并把结果转发给客户端。
* 鉴权使用 API key：客户端在握手 ``auth`` 中携带 ``token``，与服务端配置的
  ``access_key`` 一致才允许连接。

事件一览（客户端 → 服务端，除 ``chat:send`` 外均为「带 ack 的请求」）：

=========================================  ==================================
事件                                        说明
=========================================  ==================================
ping                                      心跳，返回 {pong: true}
config                                    返回服务端能力 / 默认配置
bots:list                                 机器人列表（WebUI「创建机器人」页）
dialogs:list                              对话（Webchat 会话）列表
dialogs:create                            新建对话（绑定到一个机器人）
dialogs:delete                            删除对话
dialogs:history                           拉取某个对话的历史消息
chat:send                                 发送消息（服务端流式回推 chat:delta / chat:done）
registry:list                             列出本地注册表（users / groups）
registry:user:upsert / user:delete        维护 AI 好友
registry:group:upsert / group:delete      维护群聊（保留，暂不细化）
=========================================  ==================================
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable

try:  # aiohttp 随 AstrBot 一并提供
    import aiohttp
except Exception:  # noqa: BLE001
    aiohttp = None  # type: ignore[assignment]

try:
    import socketio
except Exception:  # noqa: BLE001
    socketio = None  # type: ignore[assignment]

try:
    from aiohttp import web
except Exception:  # noqa: BLE001
    web = None  # type: ignore[assignment]

from astrbot.api import logger

try:  # 平台适配器：注册后 AstrBot WebUI 的「创建机器人」会出现 astrbot_plus
    from . import plus_platform
except Exception:  # noqa: BLE001
    try:
        import plus_platform  # type: ignore
    except Exception:  # noqa: BLE001
        plus_platform = None  # type: ignore[assignment]

DEFAULT_PORT = 6199


class PlusSocketServer:
    """在独立后台线程中运行的 Socket.io 服务端。"""

    def __init__(
        self,
        host: str,
        port: int,
        access_key: str,
        astrbot_base_url: str,
        astrbot_api_key: str,
        registry: dict[str, list[dict[str, Any]]],
        save_cb: Callable[[], None],
    ) -> None:
        self.host = host or "0.0.0.0"
        self.port = int(port or DEFAULT_PORT)
        self.access_key = (access_key or "").strip()
        self.base_url = (astrbot_base_url or "http://127.0.0.1:6185").rstrip("/")
        self.astrbot_api_key = (astrbot_api_key or "").strip()
        self.registry = registry
        self._save = save_cb
        # 入站会话 -> 出站路由（sid / reqId），用于把机器人回复回推到正确的客户端
        self._routes: dict[str, dict[str, Any]] = {}
        self._outbound_registered = False

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: Any = None
        self._available = socketio is not None and web is not None and aiohttp is not None

        self.sio = socketio.AsyncServer(
            async_mode="aiohttp",
            cors_allowed_origins="*",
            logger=False,
            engineio_logger=False,
        ) if self._available else None
        self._app = web.Application() if self._available else None
        if self._available:
            self.sio.attach(self._app)
            self._register_handlers()

    # ------------------------------------------------------------------ 生命周期
    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> None:
        if not self._available:
            logger.warning(
                "[AstrBot+] 未安装 python-socketio / aiohttp，Socket.io 服务端未启动。"
                "请在插件 requirements 中声明依赖后重启 AstrBot。"
            )
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="astrbot-plus-socket", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._register_outbound()
        try:
            self._runner = web.AppRunner(self._app)
            loop.run_until_complete(self._runner.setup())
            site = web.TCPSite(self._runner, self.host, self.port)
            loop.run_until_complete(site.start())
            logger.info(f"[AstrBot+] Socket.io 服务端已启动：ws://{self.host}:{self.port}")
            loop.run_forever()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AstrBot+] Socket.io 服务端启动失败：{exc}")
        finally:
            try:
                if self._runner is not None:
                    loop.run_until_complete(self._runner.cleanup())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("[AstrBot+] Socket.io 服务端已停止。")

    # ------------------------------------------------------------------ 事件注册
    def _register_handlers(self) -> None:
        sio = self.sio

        @sio.event
        async def connect(sid, environ, auth):  # noqa: ANN001
            token = ""
            if isinstance(auth, dict):
                token = str(auth.get("token") or auth.get("apiKey") or auth.get("api_key") or "")
            if self.access_key and token != self.access_key:
                logger.warning("[AstrBot+] 客户端鉴权失败，已拒绝连接。")
                raise socketio.exceptions.ConnectionRefusedError("invalid access key")
            logger.info(f"[AstrBot+] 客户端已连接：{sid}")

        @sio.event
        async def disconnect(sid, reason=None):  # noqa: ANN001
            logger.info(f"[AstrBot+] 客户端已断开：{sid}")

        @sio.on("ping")
        async def _ping(sid, data=None):  # noqa: ANN001
            return {"pong": True}

        @sio.on("config")
        async def _config(sid, data=None):  # noqa: ANN001
            return {
                "plugin": "astrbot_plugin_plus",
                "version": self._plugin_version(),
                "astrbotBaseUrl": self.base_url,
            }

        @sio.on("bots:list")
        async def _bots_list(sid, data=None):  # noqa: ANN001
            return await self._safe(self._fetch_bots)

        @sio.on("dialogs:list")
        async def _dialogs_list(sid, data=None):  # noqa: ANN001
            return await self._safe(self._fetch_dialogs)

        @sio.on("dialogs:create")
        async def _dialogs_create(sid, data=None):  # noqa: ANN001
            data = data or {}
            return await self._safe(lambda: self._create_dialog(data))

        @sio.on("dialogs:delete")
        async def _dialogs_delete(sid, data=None):  # noqa: ANN001
            data = data or {}
            sid_ = str(data.get("id") or data.get("sessionId") or "")
            return await self._safe(lambda: self._delete_dialog(sid_))

        @sio.on("dialogs:history")
        async def _dialogs_history(sid, data=None):  # noqa: ANN001
            data = data or {}
            sid_ = str(data.get("id") or data.get("sessionId") or "")
            return await self._safe(lambda: self._dialog_history(sid_))

        @sio.on("chat:send")
        async def _chat_send(sid, data=None):  # noqa: ANN001
            data = data or {}
            session_id = str(data.get("sessionId") or data.get("session_id") or data.get("id") or "")
            bot_id = str(data.get("botId") or data.get("bot_id") or "")
            req_id = str(data.get("reqId") or "")
            if session_id:
                self._routes[session_id] = {"sid": sid, "reqId": req_id}
            # 若客户端绑定的是 AstrBot 平台适配器机器人，则交给 AstrBot 平台管线处理；
            # 否则回退到直连 AstrBot Webchat HTTP 接口的原有逻辑。
            if bot_id and plus_platform is not None and plus_platform.has_adapters():
                self.sio.start_background_task(self._dispatch_incoming, sid, session_id, data)
            else:
                self.sio.start_background_task(self._run_chat, sid, data)
            return {"accepted": True}

        @sio.on("registry:list")
        async def _registry_list(sid, data=None):  # noqa: ANN001
            return {"status": "ok", "data": self.registry}

        @sio.on("registry:user:upsert")
        async def _user_upsert(sid, data=None):  # noqa: ANN001
            return self._upsert_user(data or {})

        @sio.on("registry:user:delete")
        async def _user_delete(sid, data=None):  # noqa: ANN001
            return self._delete_user((data or {}).get("id"))

        @sio.on("registry:group:upsert")
        async def _group_upsert(sid, data=None):  # noqa: ANN001
            return self._upsert_group(data or {})

        @sio.on("registry:group:delete")
        async def _group_delete(sid, data=None):  # noqa: ANN001
            return self._delete_group((data or {}).get("id"))

    # ------------------------------------------------------ 平台适配器桥接 / 出站
    def _register_outbound(self) -> None:
        if plus_platform is not None and not self._outbound_registered:
            plus_platform.set_outbound(self._outbound)
            self._outbound_registered = True

    def _outbound(self, session_id: str, text: str, meta: dict[str, Any]) -> None:
        """平台适配器（AstrBot 事件循环线程）回调，切回 Socket.io 事件循环。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._emit_out(session_id, text, meta), loop)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 出站调度失败：{exc}")

    async def _emit_out(self, session_id: str, text: str, meta: dict[str, Any]) -> None:
        route = self._routes.get(session_id)
        if not route:
            return
        sid = route.get("sid")
        req_id = route.get("reqId", "")
        try:
            if meta.get("done"):
                await self.sio.emit(
                    "chat:done",
                    {"reqId": req_id, "sessionId": session_id, "text": text},
                    to=sid,
                )
                self._routes.pop(session_id, None)
            elif text:
                await self.sio.emit(
                    "chat:delta", {"reqId": req_id, "delta": text}, to=sid
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 出站推送失败：{exc}")

    async def _dispatch_incoming(self, sid: str, session_id: str, data: dict[str, Any]) -> None:
        text = str(data.get("text") or data.get("message") or "")
        bot_id = str(data.get("botId") or data.get("bot_id") or "")
        try:
            ok = await plus_platform.dispatch_incoming(  # type: ignore[union-attr]
                bot_id or plus_platform.ADAPTER_NAME,  # type: ignore[union-attr]
                session_id,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.warning(f"[AstrBot+] 平台适配器派发失败，回退 Webchat：{exc}")
        if not ok:
            await self._run_chat(sid, data)

    # ------------------------------------------------------------------ 工具
    async def _safe(self, fn: Callable[[], Any]) -> dict[str, Any]:
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                result = await result
            return {"status": "ok", "data": result}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] Socket 处理异常：{exc}")
            return {"status": "error", "message": str(exc), "data": None}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.astrbot_api_key:
            headers["Authorization"] = f"Bearer {self.astrbot_api_key}"
        return headers

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=self._headers()) as resp:
                text = await resp.text()
                try:
                    return json.loads(text)
                except Exception:  # noqa: BLE001
                    return {"raw": text}

    # ------------------------------------------------------------------ AstrBot 桥接
    async def _fetch_bots(self) -> list[dict[str, Any]]:
        """机器人列表：WebUI「创建机器人」页面中的平台配置。"""
        # 新版：GET /api/config/bots；兼容旧版：GET /api/config/platform/list
        for path, extractor in (
            ("/api/config/platform/list", lambda b: (b.get("data") or {}).get("platforms")),
            ("/api/config/bots", lambda b: (b.get("data") or {}).get("bots")),
        ):
            try:
                body = await self._get_json(path)
            except Exception:  # noqa: BLE001
                continue
            items = extractor(body if isinstance(body, dict) else {})
            if items:
                return [self._normalize_bot(b) for b in items]
        return []

    @staticmethod
    def _normalize_bot(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {"id": str(raw), "name": str(raw)}
        cfg = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        bot_id = str(raw.get("id") or raw.get("bot_id") or cfg.get("id") or "")
        name = (
            raw.get("name")
            or cfg.get("name")
            or cfg.get("platform")
            or bot_id
        )
        return {
            "id": bot_id,
            "name": str(name),
            "platform": str(raw.get("type") or raw.get("platform") or cfg.get("type") or ""),
            "enabled": bool(raw.get("enabled", True)),
            "raw": raw,
        }

    async def _fetch_dialogs(self) -> list[dict[str, Any]]:
        """对话列表：Webchat 会话。"""
        body = await self._get_json(
            "/api/v1/chat/sessions", {"platform_id": "webchat"}
        )
        data = body.get("data") if isinstance(body, dict) else body
        items = data if isinstance(data, list) else (data or {}).get("sessions", [])
        return [self._normalize_dialog(s) for s in items or []]

    @staticmethod
    def _normalize_dialog(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {"id": str(raw), "title": str(raw)}
        sid = str(raw.get("session_id") or raw.get("id") or raw.get("cid") or "")
        return {
            "id": sid,
            "title": str(raw.get("title") or raw.get("display_name") or "新对话"),
            "botId": str(raw.get("bot_id") or raw.get("botId") or ""),
            "personaId": raw.get("persona_id") or None,
            "createdAt": raw.get("created_at"),
            "updatedAt": raw.get("updated_at"),
            "raw": raw,
        }

    async def _create_dialog(self, data: dict[str, Any]) -> dict[str, Any]:
        params = {"platform_id": "webchat"}
        bot_id = str(data.get("botId") or data.get("bot_id") or "")
        if bot_id:
            params["bot_id"] = bot_id
        body = await self._get_json("/api/v1/chat/sessions/new", params)
        payload = body.get("data") if isinstance(body, dict) else body
        if isinstance(payload, dict):
            dialog = self._normalize_dialog(payload)
            dialog["botId"] = dialog.get("botId") or bot_id
            return dialog
        return {"id": "", "title": "新对话", "botId": bot_id}

    async def _delete_dialog(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            raise ValueError("缺少会话 id")
        url = f"{self.base_url}/api/v1/chat/sessions/{session_id}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self._headers()) as resp:
                await resp.text()
        return {"deleted": True, "id": session_id}

    async def _dialog_history(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            raise ValueError("缺少会话 id")
        body = await self._get_json(
            f"/api/v1/chat/sessions/{session_id}",
            {"page": 1, "page_size": 1000},
        )
        data = body.get("data") if isinstance(body, dict) else body
        history: Any = []
        if isinstance(data, dict):
            history = data.get("history") or data.get("messages") or []
        elif isinstance(data, list):
            history = data
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except Exception:  # noqa: BLE001
                history = []
        return {"id": session_id, "messages": history}

    @staticmethod
    def _event_text(event: dict[str, Any]) -> str:
        """从 AstrBot 的 SSE 事件中提取文本增量。"""
        data = event.get("data")
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("text", "content", "delta"):
                if isinstance(data.get(key), str):
                    return data[key]
        for key in ("text", "message", "content"):
            if isinstance(event.get(key), str):
                return event[key]
        return ""

    async def _run_chat(self, sid: str, data: dict[str, Any]) -> None:
        """执行一次流式聊天：解析 AstrBot SSE，仅把文本增量回推给客户端。"""
        session_id = str(data.get("sessionId") or data.get("session_id") or data.get("id") or "")
        text = str(data.get("text") or data.get("message") or "")
        bot_id = str(data.get("botId") or data.get("bot_id") or "")
        req_id = str(data.get("reqId") or "")
        payload: dict[str, Any] = {
            "session_id": session_id,
            "message": text,
            "platform_id": "webchat",
        }
        if bot_id:
            payload["bot_id"] = bot_id
        full = ""
        streamed = False
        try:
            url = f"{self.base_url}/api/v1/chat"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=self._headers()
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", "ignore").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if not chunk or chunk == "[DONE]":
                            continue
                        try:
                            event = json.loads(chunk)
                        except Exception:  # noqa: BLE001
                            event = None
                        if isinstance(event, dict):
                            etype = str(event.get("type") or event.get("t") or "")
                            if etype in ("session_id", "session_bound") and event.get("session_id"):
                                await self.sio.emit(
                                    "chat:session",
                                    {"reqId": req_id, "sessionId": event["session_id"]},
                                    to=sid,
                                )
                            elif etype == "plain":
                                piece = self._event_text(event)
                                if piece:
                                    full += piece
                                    streamed = True
                                    await self.sio.emit(
                                        "chat:delta", {"reqId": req_id, "delta": piece}, to=sid
                                    )
                            elif etype == "complete":
                                piece = self._event_text(event)
                                if piece and not streamed:
                                    full = piece
                                    streamed = True
                                    await self.sio.emit(
                                        "chat:delta", {"reqId": req_id, "delta": piece}, to=sid
                                    )
                            elif etype == "error":
                                raise RuntimeError(self._event_text(event) or "聊天出错")
                        else:
                            full += chunk
                            streamed = True
                            await self.sio.emit(
                                "chat:delta", {"reqId": req_id, "delta": chunk}, to=sid
                            )
            await self.sio.emit(
                "chat:done",
                {"reqId": req_id, "sessionId": session_id, "text": full},
                to=sid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 聊天转发失败：{exc}")
            await self.sio.emit(
                "chat:error",
                {"reqId": req_id, "sessionId": session_id, "message": str(exc)},
                to=sid,
            )

    # ------------------------------------------------------------------ 注册表（AI 好友 / 群聊）
    def _upsert_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name 不能为空")
        uid = str(payload.get("id") or "").strip() or f"f_{int(asyncio.get_event_loop().time()*1000)}"
        user = {
            "id": uid,
            "name": name,
            "configId": payload.get("configId") or None,
            "personaId": payload.get("personaId") or None,
            "avatarSeed": str(payload.get("avatarSeed") or name),
            "createdAt": int(payload.get("createdAt") or 0) or self._now_ms(),
        }
        users = self.registry.setdefault("users", [])
        for idx, existing in enumerate(users):
            if existing.get("id") == uid:
                user["createdAt"] = int(existing.get("createdAt") or user["createdAt"])
                users[idx] = user
                break
        else:
            users.append(user)
        self._save()
        return {"status": "ok", "data": user}

    def _delete_user(self, uid: Any) -> dict[str, Any]:
        uid = str(uid or "")
        if not uid:
            raise ValueError("缺少 id")
        self.registry["users"] = [u for u in self.registry.get("users", []) if u.get("id") != uid]
        for grp in self.registry.get("groups", []):
            grp["memberIds"] = [m for m in grp.get("memberIds", []) if m != uid]
        self.registry["groups"] = [
            g for g in self.registry.get("groups", []) if len(g.get("memberIds", [])) >= 2
        ]
        self._save()
        return {"status": "ok", "data": {"id": uid}}

    def _upsert_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("群名称不能为空")
        member_ids = payload.get("memberIds") or []
        valid = {u["id"] for u in self.registry.get("users", [])}
        seen: list[str] = []
        for mid in member_ids:
            mid = str(mid)
            if mid in valid and mid not in seen:
                seen.append(mid)
        if len(seen) < 2:
            raise ValueError("群聊至少需要 2 个有效的 AI 用户")
        gid = str(payload.get("id") or "").strip() or f"g_{self._now_ms()}"
        group = {
            "id": gid,
            "name": name,
            "memberIds": seen,
            "avatarSeed": str(payload.get("avatarSeed") or name),
            "createdAt": int(payload.get("createdAt") or 0) or self._now_ms(),
        }
        groups = self.registry.setdefault("groups", [])
        for idx, existing in enumerate(groups):
            if existing.get("id") == gid:
                group["createdAt"] = int(existing.get("createdAt") or group["createdAt"])
                groups[idx] = group
                break
        else:
            groups.append(group)
        self._save()
        return {"status": "ok", "data": group}

    def _delete_group(self, gid: Any) -> dict[str, Any]:
        gid = str(gid or "")
        if not gid:
            raise ValueError("缺少 id")
        self.registry["groups"] = [g for g in self.registry.get("groups", []) if g.get("id") != gid]
        self._save()
        return {"status": "ok", "data": {"id": gid}}

    # ------------------------------------------------------------------ 杂项
    @staticmethod
    def _now_ms() -> int:
        import time

        return int(time.time() * 1000)

    def _plugin_version(self) -> str:
        try:
            from . import __version__  # type: ignore

            return __version__
        except Exception:  # noqa: BLE001
            return "0.3.0"
