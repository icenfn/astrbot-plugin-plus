"""astrbot_plugin_plus 的 Socket.io 服务端。

设计要点（对应客户端 AstrBot+ 的需求）：

* 插件单独监听一个端口（默认 ``6199``），对外 **只暴露这一个端口**；AstrBot 的
  WebUI / 主服务端口保持不变。客户端只需连接 ``ws://<host>:<port>``。
* 全流程走 Socket.io：**Agent（机器人）列表**与**流式对话**都通过事件完成。
* 服务端拉取 + 客户端本地缓存：本服务端负责调用 AstrBot 自身的 HTTP API
  （``/api/config/platform/list``）读取「创建机器人」页面的机器人列表。
* 鉴权使用 API key：客户端在握手 ``auth`` 中携带 ``token``，与服务端配置的
  ``access_key`` 一致才允许连接。

事件一览（客户端 → 服务端）：

=========================================  ==================================
事件                                        说明
=========================================  ==================================
ping                                      心跳，返回 {pong: true}
config                                    返回服务端能力 / 默认配置
bots:list                                 机器人（Agent）列表（WebUI「创建机器人」页）
chat:send                                 发送消息（服务端流式回推 chat:delta / chat:done）
=========================================  ==================================

服务端 → 客户端：``chat:session`` / ``chat:delta`` / ``chat:done`` / ``chat:error``。
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
    ) -> None:
        self.host = host or "0.0.0.0"
        self.port = int(port or DEFAULT_PORT)
        self.access_key = (access_key or "").strip()
        self.base_url = (astrbot_base_url or "http://127.0.0.1:6185").rstrip("/")
        self.astrbot_api_key = (astrbot_api_key or "").strip()
        # 入站会话 -> 出站路由（sid / reqId），用于把机器人回复回推到正确的客户端
        self._routes: dict[str, dict[str, Any]] = {}
        self._outbound_registered = False

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: Any = None
        self._available = socketio is not None and web is not None and aiohttp is not None

        self.sio = (
            socketio.AsyncServer(
                async_mode="aiohttp",
                cors_allowed_origins="*",
                logger=False,
                engineio_logger=False,
            )
            if self._available
            else None
        )
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
        self._thread = threading.Thread(
            target=self._run, name="astrbot-plus-socket", daemon=True
        )
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
                token = str(
                    auth.get("token") or auth.get("apiKey") or auth.get("api_key") or ""
                )
            if self.access_key and token != self.access_key:
                logger.warning("[AstrBot+] 客户端鉴权失败，已拒绝连接。")
                raise socketio.exceptions.ConnectionRefusedError("invalid access key")
            logger.info(f"[AstrBot+] 客户端已连接：{sid}")

        @sio.event
        async def disconnect(sid, reason=None):  # noqa: ANN001
            for key in [k for k, v in self._routes.items() if v.get("sid") == sid]:
                self._routes.pop(key, None)
            logger.info(f"[AstrBot+] 客户端已断开：{sid}（reason={reason}）")

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

        @sio.on("chat:send")
        async def _chat_send(sid, data=None):  # noqa: ANN001
            data = data or {}
            req_id = str(data.get("reqId") or "")
            bot_id = str(data.get("botId") or data.get("bot_id") or "")
            session_id = str(
                data.get("sessionId") or data.get("session_id") or ""
            ).strip() or f"plus_{bot_id or sid}"

            if plus_platform is None or not plus_platform.has_adapters():
                logger.warning("[AstrBot+] 尚无 astrbot_plus 平台适配器实例，无法处理聊天。")
                await self.sio.emit(
                    "chat:error",
                    {
                        "reqId": req_id,
                        "sessionId": session_id,
                        "message": "尚未创建 AstrBot+ 机器人，请先在 WebUI「创建机器人」中添加。",
                    },
                    to=sid,
                )
                return {"accepted": False}

            self._routes[session_id] = {"sid": sid, "reqId": req_id}
            logger.info(
                f"[AstrBot+] 收到聊天请求 sid={sid} bot={bot_id or '-'} session={session_id}"
            )
            # 先告知客户端本次使用的稳定 session 标识，便于其复用会话上下文。
            await self.sio.emit(
                "chat:session",
                {"reqId": req_id, "sessionId": session_id},
                to=sid,
            )
            self.sio.start_background_task(
                self._dispatch_incoming,
                sid,
                session_id,
                {**data, "sessionId": session_id, "botId": bot_id},
            )
            return {"accepted": True}

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

    async def _dispatch_incoming(
        self, sid: str, session_id: str, data: dict[str, Any]
    ) -> None:
        text = str(data.get("text") or data.get("message") or "")
        bot_id = str(data.get("botId") or data.get("bot_id") or "")
        req_id = str(data.get("reqId") or "")
        try:
            ok = await plus_platform.dispatch_incoming(  # type: ignore[union-attr]
                bot_id or plus_platform.ADAPTER_NAME,  # type: ignore[union-attr]
                session_id,
                text,
            )
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.warning(f"[AstrBot+] 平台适配器派发失败：{exc}")
        if not ok:
            await self.sio.emit(
                "chat:error",
                {
                    "reqId": req_id,
                    "sessionId": session_id,
                    "message": "无法将消息交给机器人处理。",
                },
                to=sid,
            )

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
            (
                "/api/config/platform/list",
                lambda b: (b.get("data") or {}).get("platforms"),
            ),
            ("/api/config/bots", lambda b: (b.get("data") or {}).get("bots")),
        ):
            try:
                body = await self._get_json(path)
            except Exception:  # noqa: BLE001
                continue
            items = extractor(body if isinstance(body, dict) else {})
            if items:
                bots = [self._normalize_bot(b) for b in items]
                # 优先只展示由 AstrBot+ 平台创建的机器人；若一个都没有，则回退展示全部。
                plus_bots = [b for b in bots if "astrbot_plus" in (b.get("platform") or "")]
                return plus_bots or bots
        return []

    @staticmethod
    def _normalize_bot(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {"id": str(raw), "name": str(raw)}
        cfg = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        bot_id = str(raw.get("id") or raw.get("bot_id") or cfg.get("id") or "")
        name = raw.get("name") or cfg.get("name") or cfg.get("platform") or bot_id
        return {
            "id": bot_id,
            "name": str(name),
            "platform": str(
                raw.get("type") or raw.get("platform") or cfg.get("type") or ""
            ),
            "enabled": bool(raw.get("enabled", True)),
            "raw": raw,
        }

    # ------------------------------------------------------------------ 杂项
    def _plugin_version(self) -> str:
        try:
            from . import __version__  # type: ignore

            return __version__
        except Exception:  # noqa: BLE001
            return "0.3.2"
