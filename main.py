"""astrbot-plugin-plus — 基于 HTTP API + Socket.IO 混合架构的 IM 服务端插件。

本插件把 AstrBot+ 客户端接入为 AstrBot 的一个**消息平台**（平台适配器
``astrbot_plus``），并在独立端口（默认 ``6199``）上同时提供：

* ``/api/*``      —— 登录 / Agent 列表 / 好友列表 / 消息漫游（REST，JWT 鉴权）；
* ``/socket.io``  —— 流式对话、私聊推送（握手 JWT 鉴权）；
* ``/health``     —— 健康检查。

另外通过 ``context.register_web_api`` 在 AstrBot WebUI 端口上注册
``/{PLUGIN_NAME}/admin/*`` 管理接口（用户 / Agent CRUD），管理视图文件位于
``views/plus-admin/``，**不暴露在 IM 端口**。

配置项（见 ``_conf_schema.json``）：``listen_host`` / ``listen_port``。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:  # AstrBot >= 4.18 的插件可视化视图 Web API helper；旧版本无此模块。
    from astrbot.api.web import error_response, json_response, request
except Exception:  # noqa: BLE001
    error_response = None  # type: ignore[assignment]
    json_response = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]

def _import_sibling(name: str) -> Any:
    """导入同目录兄弟模块。

    只有当「模块定位失败」（没有父包 / 找不到模块）时才回退到顶层导入；
    兄弟模块内部真正抛出的异常必须原样向外传播 —— 否则会被下面的裸导入
    再吞一次，最终只剩下一个误导性的 ``No module named 'xxx'``。
    """
    import importlib
    import importlib.util
    import sys

    def _is_lookup_error(exc: BaseException) -> bool:
        if not isinstance(exc, ImportError):
            return False
        msg = str(exc)
        return (
            "no known parent package" in msg
            or "attempted relative import" in msg
            or "No module named" in msg
        )

    pkg = __package__
    if pkg:
        try:
            mod = importlib.import_module(f".{name}", pkg)
            # 登记裸模块名别名，保证后续 ``import plus_db`` 命中同一对象，
            # 不会二次执行同一文件（否则 SQLModel 会重复声明同名表）。
            sys.modules.setdefault(name, mod)
            return mod
        except ImportError as exc:
            if not _is_lookup_error(exc):
                raise

    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        if not _is_lookup_error(exc):
            raise

    # 兜底：按文件路径加载，并登记到 sys.modules 供后续复用
    file = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, file)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件模块 {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


plus_socket = _import_sibling("plus_socket")
PlusSocketServer = plus_socket.PlusSocketServer

db = _import_sibling("plus_db")

try:  # 导入平台适配器模块，触发 @register_platform_adapter 注册。
    plus_platform: Any = _import_sibling("plus_platform")
except Exception as exc:  # noqa: BLE001
    plus_platform = None  # type: ignore[assignment]
    logger.warning(f"[AstrBot+] 平台适配器模块加载失败，astrbot_plus 将不可用：{exc}")

PLUGIN_NAME = "astrbot_plugin_plus"


def _read_plugin_version() -> str:
    """从 ``metadata.yaml`` 读取版本号 —— 全插件**唯一**的版本来源。"""
    try:
        meta = Path(__file__).with_name("metadata.yaml")
        for line in meta.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("version:"):
                value = line.split(":", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    except Exception:  # noqa: BLE001
        pass
    return "0.0.0"


__version__ = _read_plugin_version()


@register(
    PLUGIN_NAME,
    "icenfn",
    "AstrBot+ 配套插件：HTTP API + Socket.IO 混合 IM 服务端（用户体系 / 私聊 / 流式对话）",
    __version__,
)
class AstrBotPlusPlugin(Star):
    """AstrBot+ 配套插件：启动 IM 服务端并注册平台适配器与 WebUI 管理 API。"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self._socket: PlusSocketServer | None = None

        data_dir = self._resolve_data_dir()
        # ---- 启动 IM 服务端（独立端口；对外只暴露这一个端口） -----------------
        self._socket = self._start_socket(data_dir)
        if self._socket is None or not self._socket.available:
            logger.warning("[AstrBot+] IM 服务端不可用（缺少依赖）。")
        else:
            logger.info(f"[AstrBot+] 插件已加载（v{__version__}），IM 服务端就绪。")

        # WebUI 平台 logo 令牌的跨版本兼容处理。
        _pp = globals().get("plus_platform")
        if _pp is not None:
            try:
                _pp.install_logo_resilience()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[AstrBot+] logo 兼容补丁安装跳过：{exc}")

        # 注册 WebUI 管理 API（AstrBot WebUI 端口，不暴露在 IM 端口）
        self._register_admin_routes()

        # 插件重载/重装后自愈平台适配器实例。
        self._start_adapter_rescue()

    # ------------------------------------------------------------ 数据目录
    def _resolve_data_dir(self) -> Path:
        """确定插件数据目录（DB / secret.key 落在这里）。"""
        candidates: list[Path] = []
        getter = getattr(self.context, "get_data_dir", None)
        if callable(getter):
            try:
                d = getter()
                if d:
                    candidates.append(Path(d))
            except Exception:  # noqa: BLE001
                pass
        # 常见回退：AstrBot 工作目录下 data/<plugin_name>
        candidates.append(Path.cwd() / "data" / PLUGIN_NAME)
        for c in candidates:
            try:
                c.mkdir(parents=True, exist_ok=True)
                return c
            except Exception:  # noqa: BLE001
                continue
        return Path(".")

    # ------------------------------------------------------ 配置 / 平台适配器辅助
    @staticmethod
    def _adapter_name() -> str:
        _pp = globals().get("plus_platform")
        return getattr(_pp, "ADAPTER_NAME", "astrbot_plus") if _pp else "astrbot_plus"

    def _global_config(self) -> Any:
        try:
            return self.context.get_config()
        except Exception:  # noqa: BLE001
            return None

    def _platform_configs(self) -> list[dict[str, Any]]:
        cfg = self._global_config()
        if cfg is None:
            return []
        platforms = cfg.get("platform")
        if not isinstance(platforms, list):
            platforms = []
            try:
                cfg["platform"] = platforms
            except Exception:  # noqa: BLE001
                return []
        return platforms

    async def _save_config(self) -> None:
        cfg = self._global_config()
        if cfg is None:
            return
        saver = getattr(cfg, "save_config_async", None)
        if callable(saver):
            await saver()
            return
        saver = getattr(cfg, "save_config", None)
        if callable(saver):
            saver()

    def _notify_bots_changed(self) -> None:
        sock = getattr(self, "_socket", None)
        if sock is None:
            return
        try:
            sock.broadcast("bots:changed", {"plugin": PLUGIN_NAME})
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 插件配置读取
    def _plugin_conf(self) -> dict[str, Any]:
        candidates: list[Any] = []
        getter = getattr(self.context, "get_config", None)
        if callable(getter):
            try:
                candidates.append(getter())
            except Exception:  # noqa: BLE001
                pass
        for attr in ("config", "plugin_config"):
            val = getattr(self, attr, None)
            if val is not None:
                candidates.append(val)
        for cfg in candidates:
            data = self._extract_plugin_conf(cfg)
            if data:
                return data
        return {}

    @staticmethod
    def _extract_plugin_conf(cfg: Any) -> dict[str, Any]:
        if not isinstance(cfg, dict):
            return {}
        if isinstance(cfg.get(PLUGIN_NAME), dict):
            return dict(cfg[PLUGIN_NAME])
        plug = cfg.get("plugin")
        if isinstance(plug, dict) and isinstance(plug.get(PLUGIN_NAME), dict):
            return dict(plug[PLUGIN_NAME])
        if {"listen_port", "listen_host"} & set(cfg.keys()):
            return dict(cfg)
        return {}

    def _start_socket(self, data_dir: Path) -> PlusSocketServer | None:
        """构建并启动 IM 服务端；失败时只记录日志，不影响插件其余功能加载。"""
        conf = self._plugin_conf()
        try:
            server = PlusSocketServer(
                host=str(conf.get("listen_host") or "0.0.0.0"),
                port=int(conf.get("listen_port") or 6199),
                data_dir=data_dir,
                version=__version__,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AstrBot+] IM 服务端初始化失败：{exc}")
            return None
        server.start()
        return server

    # ============================================================  WebUI 管理 API
    def _register_admin_routes(self) -> None:
        """通过 context.register_web_api 注册 /{PLUGIN_NAME}/admin/* 路由。

        这些路由跑在 AstrBot WebUI 端口上，本身不做登录鉴权（WebUI 已登录）。
        IM 端口的 /api/* 才是 JWT 保护的面向客户端的接口。
        """
        reg = getattr(self.context, "register_web_api", None)
        if not callable(reg):
            logger.warning("[AstrBot+] context.register_web_api 不可用，管理 API 未注册。")
            return
        # route 必须以插件名开头：AstrBot 把插件 API 挂在
        # /api/v1/plugins/extensions/<plugin_name>/... 上，并用
        # f"/{plugin_path}" 与注册的 route 做正则全匹配。
        # 路径参数必须写成 <name> / <path:name>（不是 {name}）。
        prefix = f"/{PLUGIN_NAME}/admin"
        routes = [
            ("GET", f"{prefix}/stats", self._h_admin_stats, "AstrBot+ 总览统计"),
            ("GET", f"{prefix}/users", self._h_admin_list_users, "用户列表"),
            ("POST", f"{prefix}/users", self._h_admin_create_user, "新建用户"),
            ("PATCH", f"{prefix}/users/<id>", self._h_admin_update_user, "修改用户"),
            ("DELETE", f"{prefix}/users/<id>", self._h_admin_delete_user, "删除用户"),
            ("GET", f"{prefix}/agents", self._h_admin_list_agents, "Agent 列表"),
            ("POST", f"{prefix}/agents", self._h_admin_create_agent, "新建 Agent"),
            ("PATCH", f"{prefix}/agents/<id>", self._h_admin_update_agent, "修改 Agent"),
            ("DELETE", f"{prefix}/agents/<id>", self._h_admin_delete_agent, "删除 Agent"),
        ]
        for method, route, handler, desc in routes:
            try:
                # AstrBot 真实签名：register_web_api(route, view_handler, methods, desc)
                reg(route, handler, [method], desc)
            except TypeError:
                # 兼容旧版 (method=, path=, handler=) 关键字签名。
                try:
                    reg(method=method, path=route, handler=handler)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[AstrBot+] 注册 {method} {route} 失败：{exc}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[AstrBot+] 注册 {method} {route} 失败：{exc}")

    @staticmethod
    def _ok(data: Any) -> Any:
        if json_response is not None:
            try:
                return json_response(data)
            except Exception:  # noqa: BLE001
                pass
        return data

    @staticmethod
    def _err(msg: str, code: int = 400) -> Any:
        # astrbot.api.web.error_response(message, *, status_code=..., data=...)
        if error_response is not None:
            try:
                return error_response(msg, status_code=code)
            except Exception:  # noqa: BLE001
                pass
        return {"status": "error", "message": msg, "data": None}

    async def _read_body(self) -> dict[str, Any]:
        if request is not None:
            try:
                data = await request.json()
            except Exception:  # noqa: BLE001
                return {}
            return data if isinstance(data, dict) else {}
        return {}

    # ---- stats ----
    async def _h_admin_stats(self) -> Any:
        _pp = globals().get("plus_platform")
        bots = _pp.list_bots() if _pp else []
        online = self._socket.online_count if self._socket else 0
        return self._ok(
            {
                "users": db.admin_list_users().__len__(),
                "agents": len(bots),
                "todayMessages": db.admin_today_message_count(),
                "online": online,
            }
        )

    # ---- users ----
    async def _h_admin_list_users(self) -> Any:
        rows = db.admin_list_users()
        for r in rows:
            try:
                r["allowed_agents"] = json.loads(r.get("allowed_agents") or "[]")
            except Exception:  # noqa: BLE001
                r["allowed_agents"] = []
            r["tokenUsage"] = db.admin_token_usage(r["id"])
            r.pop("password_hash", None)
        return self._ok(rows)

    async def _h_admin_create_user(self) -> Any:
        body = await self._read_body()
        email = str(body.get("email") or "").strip().lower()
        username = str(body.get("username") or "").strip() or email
        password = str(body.get("password") or "")
        if not email or not password:
            return self._err("email 与 password 必填")
        try:
            import bcrypt

            pw_hash = bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            return self._err(f"bcrypt 哈希失败：{exc}")
        try:
            user = db.admin_create_user(email, username, pw_hash)
        except Exception as exc:  # noqa: BLE001
            return self._err(f"创建用户失败（邮箱可能已存在）：{exc}")
        # 生成快连 URL：ap://email:password@host:port
        conf = self._plugin_conf()
        host = str(conf.get("listen_host") or "0.0.0.0")
        port = int(conf.get("listen_port") or 6199)
        display_host = "localhost" if host in ("0.0.0.0", "::") else host
        quick_link = f"ap://{email}:{password}@{display_host}:{port}"
        return self._ok({"user": user, "quickLink": quick_link})

    async def _h_admin_update_user(self, id: int | None = None) -> Any:  # noqa: A002
        body = await self._read_body()
        uid = self._path_id(id)
        if uid is None:
            return self._err("缺少用户 id")
        fields: dict[str, Any] = {}
        if "email" in body:
            email = str(body["email"] or "").strip()
            if not email or "@" not in email:
                return self._err("邮箱格式不正确")
            exists = db.admin_find_user_by_email(email)
            if exists is not None and int(exists["id"]) != uid:
                return self._err("该邮箱已被其他用户使用", 409)
            fields["email"] = email
        if "password" in body:
            raw_pw = str(body["password"] or "")
            if len(raw_pw) < 6:
                return self._err("密码至少 6 位")
            import bcrypt as _bcrypt

            fields["password_hash"] = _bcrypt.hashpw(
                raw_pw.encode("utf-8"), _bcrypt.gensalt()
            ).decode("utf-8")
        if "username" in body:
            fields["username"] = str(body["username"])
        if "status" in body:
            try:
                fields["status"] = 1 if int(body["status"]) else 0
            except Exception:  # noqa: BLE001
                pass
        if "allowed_agents" in body:
            if isinstance(body["allowed_agents"], list):
                fields["allowed_agents"] = db.dumps_allowed_agents(body["allowed_agents"])
        if not fields:
            return self._ok({"updated": 0})
        db.admin_update_user(uid, fields)
        return self._ok({"updated": 1})

    async def _h_admin_delete_user(self, id: int | None = None) -> Any:  # noqa: A002
        uid = self._path_id(id)
        if uid is None:
            return self._err("缺少用户 id")
        db.admin_delete_user(uid)
        return self._ok({"deleted": 1})

    # ---- agents ----
    async def _h_admin_list_agents(self) -> Any:
        out = []
        for p in self._platform_configs():
            if p.get("type") != self._adapter_name():
                continue
            out.append(
                {
                    "id": str(p.get("id") or ""),
                    "botname": p.get("botname") or "",
                    "enable": bool(p.get("enable", True)),
                    "botdf": p.get("botdf") or "",
                }
            )
        return self._ok(out)

    async def _h_admin_create_agent(self) -> Any:
        body = await self._read_body()
        botname = str(body.get("botname") or "").strip()
        if not botname:
            return self._err("botname 必填")
        import uuid as _uuid

        bot_id = body.get("id") or _uuid.uuid4().hex[:12]
        try:
            import hashlib

            botdf = hashlib.md5(
                f"{self._adapter_name()}:{bot_id}".encode("utf-8")
            ).hexdigest()
        except Exception:  # noqa: BLE001
            botdf = _uuid.uuid4().hex
        cfg = {
            "type": self._adapter_name(),
            "enable": True,
            "id": str(bot_id),
            "botname": botname,
            "botdf": botdf,
        }
        platforms = self._platform_configs()
        platforms.append(cfg)
        await self._save_config()
        pm = getattr(self.context, "platform_manager", None)
        if pm is not None and hasattr(pm, "load_platform"):
            try:
                await pm.load_platform(dict(cfg))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[AstrBot+] 加载新 agent {bot_id} 失败：{exc}")
        self._notify_bots_changed()
        return self._ok(cfg)

    async def _h_admin_update_agent(self, id: str | None = None) -> Any:  # noqa: A002
        body = await self._read_body()
        bot_id = str(id or "")
        if not bot_id:
            return self._err("缺少 agent id")
        for p in self._platform_configs():
            if p.get("type") != self._adapter_name() or str(p.get("id")) != bot_id:
                continue
            if "botname" in body:
                p["botname"] = str(body["botname"])
            if "enable" in body:
                p["enable"] = bool(body["enable"])
            await self._save_config()
            self._notify_bots_changed()
            return self._ok(p)
        return self._err("agent 不存在", 404)

    async def _h_admin_delete_agent(self, id: str | None = None) -> Any:  # noqa: A002
        bot_id = str(id or "")
        platforms = self._platform_configs()
        for i, p in enumerate(platforms):
            if p.get("type") == self._adapter_name() and str(p.get("id")) == bot_id:
                platforms.pop(i)
                break
        else:
            return self._err("agent 不存在", 404)
        await self._save_config()
        pm = getattr(self.context, "platform_manager", None)
        if pm is not None and hasattr(pm, "terminate_platform"):
            try:
                await pm.terminate_platform(bot_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[AstrBot+] 停止 agent {bot_id} 失败：{exc}")
        self._notify_bots_changed()
        return self._ok({"deleted": 1})

    @staticmethod
    def _path_id(raw: Any) -> int | None:
        try:
            return int(raw) if raw is not None else None
        except Exception:  # noqa: BLE001
            return None

    # -------------------------------------------------------- 平台适配器自愈
    def _start_adapter_rescue(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._rescue_task = loop.create_task(self._ensure_platform_adapters())

    async def _ensure_platform_adapters(self) -> None:
        for attempt in range(6):
            await asyncio.sleep(1.5 if attempt == 0 else 2.5)
            try:
                if await self._rescue_platform_adapters():
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[AstrBot+] 适配器自愈检查异常（{attempt + 1}/6）：{exc}")

    async def _rescue_platform_adapters(self) -> bool:
        _pp = globals().get("plus_platform")
        if _pp is None:
            return True
        pm = getattr(self.context, "platform_manager", None)
        if pm is None:
            return False

        adapter_name = _pp.ADAPTER_NAME
        adapter_cls = _pp.AstrBotPlusAdapter
        insts = list(getattr(pm, "platform_insts", None) or [])

        for inst in insts:
            try:
                meta = inst.meta()
            except Exception:  # noqa: BLE001
                continue
            if getattr(meta, "name", None) != adapter_name:
                continue
            if isinstance(inst, adapter_cls):
                continue
            cfg = getattr(inst, "platform_config", None)
            if isinstance(cfg, dict) and hasattr(pm, "reload"):
                try:
                    await pm.reload(dict(cfg))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[AstrBot+] 适配器重载失败：{exc}")

        live_ids = set()
        for inst in getattr(pm, "platform_insts", None) or []:
            try:
                live_ids.add(inst.meta().id)
            except Exception:  # noqa: BLE001
                continue
        for plat in list(getattr(pm, "platforms_config", None) or []):
            if not isinstance(plat, dict):
                continue
            if plat.get("type") != adapter_name or not plat.get("enable", True):
                continue
            if plat.get("id") in live_ids:
                continue
            if hasattr(pm, "load_platform"):
                try:
                    await pm.load_platform(dict(plat))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[AstrBot+] agent {plat.get('id')} 加载失败：{exc}")
        return True

    # ------------------------------------------------------------------- 指令
    @filter.command("plus")
    async def plus_status(self, event: AstrMessageEvent):
        """查看 AstrBot+ 插件状态：/plus"""
        ready = "就绪" if (self._socket and self._socket.available) else "未就绪"
        users = 0
        try:
            users = len(db.admin_list_users())
        except Exception:  # noqa: BLE001
            pass
        online = self._socket.online_count if self._socket else 0
        yield event.plain_result(
            f"🌟 AstrBot+ v{__version__}\nIM 服务端：{ready}\n"
            f"注册用户：{users} · 在线连接：{online}"
        )

    async def terminate(self):
        _pp = globals().get("plus_platform")
        if _pp is not None:
            try:
                _pp.stop_logo_keepalive()
            except Exception:  # noqa: BLE001
                pass
        if getattr(self, "_socket", None) is not None:
            self._socket.stop()
        logger.info("[AstrBot+] 插件已卸载。")
