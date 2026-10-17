"""astrbot_plugin_plus 的 Socket.io 服务端。

设计要点（对应客户端 AstrBot+ 的需求）：

* 插件单独监听一个端口（默认 ``6199``），对外 **只暴露这一个端口**；AstrBot 的
  WebUI / 主服务端口保持不变。客户端只需连接 ``ws://<host>:<port>``。
* 全流程走 Socket.io：**Agent（机器人）列表**与**流式对话**都通过事件完成。
* 机器人列表直接来自 AstrBot 内部已实例化的 ``astrbot_plus`` 平台适配器，
  插件运行在同一进程内，无需回调 AstrBot 的 HTTP OpenAPI。
* 访问方式保持简单：客户端直接连接即可，不再要求访问密钥。

事件一览（客户端 → 服务端）：

=========================================  ==================================
事件                                        说明
=========================================  ==================================
bots:list                                 机器人（Agent）列表（WebUI「创建机器人」页）
chat:history                              拉取某个会话的历史消息（客户端历史同步）
chat:send                                 发送消息（服务端流式回推 chat:delta / chat:done）
=========================================  ==================================

服务端 → 客户端：``chat:session`` / ``chat:delta`` / ``chat:done`` / ``chat:error`` /
``message:new``（新消息广播，含用户与机器人消息，用于消息列表红点 / 副标题 / 系统通知）。

另外在根路径 ``/`` 与 ``/health`` 暴露一个健康检查页，便于在浏览器中确认
Socket.io 服务端是否存活（浏览器直接访问端口不再返回 404）。
"""

from __future__ import annotations

import asyncio
import html
import threading
import time
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

    def __init__(self, host: str, port: int) -> None:
        self.host = host or "0.0.0.0"
        self.port = int(port or DEFAULT_PORT)
        # 入站会话 -> 出站路由（sid / reqId），用于把机器人回复回推到正确的客户端
        self._routes: dict[str, dict[str, Any]] = {}
        self._outbound_registered = False
        # 历史消息缓存（按 session 存储）与流式缓冲，用于客户端历史同步与列表实时更新。
        # Bounded in-memory history ring; the client owns durable caching.
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._history_lock = threading.Lock()
        self._stream_buf: dict[str, str] = {}

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: Any = None
        self._available = socketio is not None and web is not None

        self.sio = (
            socketio.AsyncServer(
                async_mode="aiohttp",
                cors_allowed_origins="*",
                logger=False,
                engineio_logger=False,
                # 心跳保活：更宽松的 ping 间隔 / 超时，减少移动网络下的误断开。
                ping_interval=25,
                ping_timeout=60,
            )
            if self._available
            else None
        )
        self._app = web.Application() if self._available else None
        if self._available:
            self.sio.attach(self._app)
            self._register_http()
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

    # ------------------------------------------------------------------ 历史消息
    def get_history(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """Return the most recent messages for a session.

        History is kept in a bounded in-memory ring only: the CLIENT caches every
        conversation locally and pulls it exactly once, so the server never needs to
        persist (or re-send) history across restarts.
        """
        with self._history_lock:
            items = list(self._history.get(session_id, []))
        return items[-limit:] if limit and limit > 0 else items

    def _record_message(
        self, session_id: str, role: str, text: str, bot_id: str = ""
    ) -> dict[str, Any]:
        msg = {
            "sessionId": session_id,
            "botId": bot_id,
            "role": role,
            "text": text,
            "ts": time.time(),
        }
        with self._history_lock:
            bucket = self._history.setdefault(session_id, [])
            bucket.append(msg)
            if len(bucket) > 500:
                del bucket[:-500]
        return msg

    def _record_and_broadcast(
        self, session_id: str, role: str, text: str, bot_id: str = ""
    ) -> None:
        """记录一条消息并广播给所有客户端，用于消息列表红点 / 副标题实时刷新。"""
        if not text:
            return
        msg = self._record_message(session_id, role, text, bot_id)
        self._broadcast("message:new", msg)

    def _broadcast(self, event: str, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.sio.emit(event, payload), loop)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 广播 {event} 失败：{exc}")

    # ------------------------------------------------------------------ 健康检查页
    def _register_http(self) -> None:
        """注册普通 HTTP 路由，让浏览器访问端口时能看到状态而不是 404。"""
        assert self._app is not None  # noqa: S101
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/health", self._handle_index)

    async def _handle_index(self, request: Any) -> Any:  # noqa: ANN401
        version = html.escape(self._plugin_version())
        bots = self._fetch_bots()
        rows = "".join(
            f"<li><b>{html.escape(b['name'])}</b>"
            f"<span class='pid'>{html.escape(b['id'])}</span></li>"
            for b in bots
        ) or "<li class='none'>尚未在 WebUI「创建机器人」中启用 AstrBot+ 平台</li>"
        page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AstrBot+ 插件 · Socket.io 服务端</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; padding: 24px;
    font-family: "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, sans-serif;
    background: #0e1621; color: #e9edf1;
  }}
  .card {{
    width: 100%; max-width: 520px; background: #17212b;
    border: 1px solid rgba(128,150,170,.16); border-radius: 16px;
    padding: 28px 28px 24px; box-shadow: 0 12px 40px rgba(0,0,0,.35);
  }}
  .head {{ display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }}
  .dot {{ width: 11px; height: 11px; border-radius: 50%; background: #4caf50;
         box-shadow: 0 0 0 4px rgba(76,175,80,.18); flex: 0 0 auto; }}
  h1 {{ font-size: 19px; margin: 0; font-weight: 650; }}
  .ver {{ margin-left: auto; font-size: 12px; color: #64b5f6;
         background: rgba(47,134,189,.16); padding: 3px 10px; border-radius: 999px; }}
  .ok {{ color: #9ccc9c; font-size: 14px; margin: 4px 0 18px; }}
  .label {{ font-size: 12px; letter-spacing: .06em; text-transform: uppercase;
           color: #7f93a6; margin: 0 0 8px; }}
  .endpoint {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px;
    background: #0e1621; border: 1px solid rgba(128,150,170,.2);
    padding: 10px 12px; border-radius: 10px; color: #4fc3f7; word-break: break-all; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  li {{ display: flex; align-items: center; gap: 8px; padding: 8px 2px;
       border-bottom: 1px solid rgba(128,150,170,.1); font-size: 14px; }}
  li:last-child {{ border-bottom: 0; }}
  li .pid {{ margin-left: auto; font-size: 12px; color: #7f93a6;
            font-family: ui-monospace, Menlo, monospace; }}
  li.none {{ color: #7f93a6; }}
  .tip {{ margin-top: 18px; font-size: 12.5px; color: #8fa3b5; line-height: 1.7; }}
  code {{ background: rgba(127,140,150,.22); padding: 1px 6px; border-radius: 5px;
         font-size: 12px; }}
</style>
</head>
<body>
  <div class="card">
    <div class="head"><span class="dot"></span><h1>AstrBot+ 插件服务端</h1><span class="ver">v{version}</span></div>
    <p class="ok">Socket.io 服务端运行正常 ✅</p>
    <p class="label">客户端连接地址</p>
    <div class="endpoint">ws://&lt;服务器地址&gt;:{self.port}</div>
    <p class="label" style="margin-top:16px">已加载的 Agent（{len(bots)}）</p>
    <ul>{rows}</ul>
    <p class="tip">这是 AstrBot+ 配套插件的服务端口，并非网页。请在 AstrBot+
      客户端的「连接设置」中填入上述地址（支持 <code>host:6199</code>、
      <code>http://…</code> 或 <code>ws://…</code> 写法），即可开始对话。</p>
  </div>
</body>
</html>"""
        return web.Response(text=page, content_type="text/html")

    # ------------------------------------------------------------------ 事件注册
    def _register_handlers(self) -> None:
        sio = self.sio

        @sio.event
        async def connect(sid, environ, auth):  # noqa: ANN001, ARG001
            logger.info(f"[AstrBot+] 客户端已连接：{sid}")

        @sio.event
        async def disconnect(sid, reason=None):  # noqa: ANN001
            for key in [k for k, v in self._routes.items() if v.get("sid") == sid]:
                self._routes.pop(key, None)
            logger.info(f"[AstrBot+] 客户端已断开：{sid}（reason={reason}）")

        # NOTE: no `ping` / `config` events. Socket.io has its own engine-level
        # heartbeat (see ping_interval/ping_timeout above), and the plugin version
        # is already rendered on the /health page — both were redundant round-trips.

        @sio.on("config")
        async def _config(sid, data=None):  # noqa: ANN001
            return {
                "plugin": "astrbot_plugin_plus",
                "version": self._plugin_version(),
                "port": self.port,
            }

        @sio.on("bots:list")
        async def _bots_list(sid, data=None):  # noqa: ANN001
            return await self._safe(self._fetch_bots)

        @sio.on("chat:history")
        async def _chat_history(sid, data=None):  # noqa: ANN001
            data = data or {}
            session_id = str(
                data.get("sessionId") or data.get("session_id") or ""
            ).strip()
            if not session_id:
                bot_id = str(data.get("botId") or data.get("bot_id") or "")
                session_id = f"plus_{bot_id}" if bot_id else ""
            try:
                limit = int(data.get("limit") or 200)
            except Exception:  # noqa: BLE001
                limit = 200
            return {
                "sessionId": session_id,
                "messages": self.get_history(session_id, limit),
            }

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

            self._routes[session_id] = {
                "sid": sid,
                "reqId": req_id,
                "botId": bot_id,
            }
            logger.info(
                f"[AstrBot+] 收到聊天请求 sid={sid} bot={bot_id or '-'} session={session_id}"
            )
            # 先告知客户端本次使用的稳定 session 标识，便于其复用会话上下文。
            await self.sio.emit(
                "chat:session",
                {"reqId": req_id, "sessionId": session_id},
                to=sid,
            )
            # 记录用户消息并广播，客户端据此更新消息列表（红点 / 副标题 / 通知）。
            user_text = str(data.get("text") or data.get("message") or "")
            self._record_and_broadcast(session_id, "user", user_text, bot_id)
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
            # 无对应路由：仍记录机器人主动消息，便于客户端列表实时更新与历史同步。
            if meta.get("done") and text:
                self._record_and_broadcast(session_id, "assistant", text, "")
            return
        sid = route.get("sid")
        req_id = route.get("reqId", "")
        bot_id = route.get("botId", "")
        try:
            if meta.get("done"):
                # 兼容「未开启流式输出」：done 时可能一次性携带完整文本，
                # 也可能此前只有增量（此时 text 为空），两者统一为完整内容。
                streamed = self._stream_buf.pop(session_id, "")
                # 优先使用携带的完整文本（插件层已做单轮聚合）；否则回退到增量累积。
                full = text if text else streamed
                if full:
                    self._record_and_broadcast(session_id, "assistant", full, bot_id)
                await self.sio.emit(
                    "chat:done",
                    {"reqId": req_id, "sessionId": session_id, "text": full},
                    to=sid,
                )
                self._routes.pop(session_id, None)
            elif text:
                self._stream_buf[session_id] = (
                    self._stream_buf.get(session_id, "") + text
                )
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

    # ------------------------------------------------------------------ AstrBot 桥接
    def _fetch_bots(self) -> list[dict[str, Any]]:
        """机器人列表：直接读取 AstrBot 内部已实例化的 ``astrbot_plus`` 平台适配器。

        插件运行在与 AstrBot 相同的进程中，平台适配器在「创建机器人」启用后即已
        实例化并注册到 :data:`plus_platform._ADAPTERS`，因此无需回调 HTTP OpenAPI。
        """
        if plus_platform is None:
            return []
        try:
            return plus_platform.list_bots()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 读取机器人列表失败：{exc}")
            return []

    # ------------------------------------------------------------------ 杂项
    def _plugin_version(self) -> str:
        try:
            from . import __version__  # type: ignore

            return __version__
        except Exception:  # noqa: BLE001
            return "0.4.7"
