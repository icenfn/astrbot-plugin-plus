"""astrbot_plugin_plus 的平台适配器（Platform Adapter）。

作用：把 AstrBot+ 客户端注册为 AstrBot 的一个**消息平台**，从而出现在
WebUI「机器人 → 创建机器人」的平台选择列表中。

通信仍走 Socket.io（单端口）。工作方式：

* AstrBot 为每个「创建机器人」里创建的 astrbot_plus 实例实例化一个
  :class:`AstrBotPlusAdapter`，并调用其 ``run()`` 协程常驻；
* 客户端通过 Socket.io 发来的消息，会经
  :func:`dispatch_incoming` 找到对应适配器实例，构造 ``AstrBotMessage``
  并投入 AstrBot 的事件队列，交由 AstrBot 的对话管线处理（使用该机器人
  自带的 Provider / 人格配置）；
* 机器人产生的回复，由 :class:`AstrBotPlusEvent.send` / ``send_streaming``
  交给 Socket.io 层回推给客户端。

这样每个机器人都拥有独立配置与会话上下文，与「Agent」的语义一致。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable

from astrbot.api import logger

# --- AstrBot 平台 API（运行时由 AstrBot 提供） ---------------------------------
try:
    from astrbot.api.platform import register_platform_adapter
except Exception:  # noqa: BLE001
    from astrbot.core.platform.register import register_platform_adapter  # type: ignore

try:
    from astrbot.api.event import AstrMessageEvent
except Exception:  # noqa: BLE001
    from astrbot.core.platform.astr_message_event import AstrMessageEvent  # type: ignore

try:
    from astrbot.api.message_components import Plain
except Exception:  # noqa: BLE001
    from astrbot.core.message.components import Plain  # type: ignore

def _try_import(spec: str) -> Any:
    """按 "module:attr" 逐个导入，避免单个缺失符号拖垮整个模块。"""
    try:
        mod_name, attr = spec.rsplit(":", 1)
        return getattr(__import__(mod_name, fromlist=[attr]), attr)
    except Exception:  # noqa: BLE001
        return None


Platform = _try_import("astrbot.core.platform.platform:Platform") or object  # type: ignore[assignment]
PlatformStatus = _try_import("astrbot.core.platform.platform:PlatformStatus")
AstrBotMessage = _try_import("astrbot.core.platform.astrbot_message:AstrBotMessage") or object  # type: ignore[assignment]
MessageMember = _try_import("astrbot.core.platform.astrbot_message:MessageMember") or object  # type: ignore[assignment]
MessageSession = _try_import("astrbot.core.platform.message_session:MessageSession") or object  # type: ignore[assignment]
MessageSesion = _try_import("astrbot.core.platform.message_session:MessageSesion") or object  # type: ignore[assignment]
MessageType = _try_import("astrbot.core.platform.message_type:MessageType")
PlatformMetadata = _try_import("astrbot.core.platform.platform_metadata:PlatformMetadata") or object  # type: ignore[assignment]

ADAPTER_NAME = "astrbot_plus"
ADAPTER_DISPLAY_NAME = "AstrBot+"

# 已启动的适配器实例：platform_id -> adapter
_ADAPTERS: dict[str, "AstrBotPlusAdapter"] = {}

# 由 Socket.io 层注册的出站回调：(session_id, text, meta) -> None
_OUTBOUND: Callable[[str, str, dict[str, Any]], None] | None = None

# AstrBot 主事件循环（run() 时记录），用于把入站消息安全地投递到 AstrBot 管线
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def set_outbound(fn: Callable[[str, str, dict[str, Any]], None] | None) -> None:
    """注册出站回调，用于把机器人回复交给 Socket.io 层。"""
    global _OUTBOUND
    _OUTBOUND = fn


def get_adapter(platform_id: str) -> "AstrBotPlusAdapter | None":
    return _ADAPTERS.get(platform_id)


def has_adapters() -> bool:
    return bool(_ADAPTERS)


def _extract_text(message: Any) -> str:
    """从 MessageChain / 组件列表中提取纯文本。"""
    if isinstance(message, str):
        return message
    chain = getattr(message, "chain", message)
    if isinstance(chain, str):
        return chain
    parts: list[str] = []
    if isinstance(chain, (list, tuple)):
        for comp in chain:
            text = getattr(comp, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(comp, str):
                parts.append(comp)
    return "".join(parts)


def _push(session_id: str, text: str, *, delta: bool = False, done: bool = False) -> None:
    if _OUTBOUND is None:
        return
    try:
        _OUTBOUND(session_id, text, {"delta": delta, "done": done})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[AstrBot+] 出站推送失败：{exc}")


class AstrBotPlusEvent(AstrMessageEvent):  # type: ignore[misc]
    """AstrBot+ 的消息事件；把回复通过 Socket.io 回推给客户端。"""

    def __init__(self, *args: Any, adapter: "AstrBotPlusAdapter | None" = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._adapter = adapter

    async def send(self, message: Any) -> None:
        text = _extract_text(message)
        if text:
            _push(self.session_id, text, done=True)

    async def send_streaming(self, generator: Any, use_fallback: bool = False) -> None:  # noqa: D401
        """流式发送：逐段推送增量，最后标记完成。"""
        try:
            async for chunk in generator:
                text = _extract_text(chunk)
                if text:
                    _push(self.session_id, text, delta=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 流式发送出错：{exc}")
        _push(self.session_id, "", done=True)


@register_platform_adapter(
    ADAPTER_NAME,
    "AstrBot+ 跨平台客户端平台适配器（Socket.io 单端口）",
    default_config_tmpl={
        "type": ADAPTER_NAME,
        "enable": False,
        "id": ADAPTER_NAME,
        "access_key": "",
    },
    adapter_display_name=ADAPTER_DISPLAY_NAME,
    logo_path="logo.png",
    support_streaming_message=True,
)
class AstrBotPlusAdapter(Platform):  # type: ignore[misc]
    """AstrBot+ 平台适配器实现。"""

    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: Any) -> None:
        super().__init__(platform_config, event_queue)
        self.platform_config = platform_config or {}
        self.platform_settings = platform_settings or {}
        self._stop = asyncio.Event()
        self.metadata = PlatformMetadata(
            name=ADAPTER_NAME,
            description="AstrBot+ 跨平台客户端平台适配器（Socket.io 单端口）",
            id=self.platform_config.get("id") or ADAPTER_NAME,
            default_config_tmpl=self.platform_config.get("default_config_tmpl"),
            adapter_display_name=ADAPTER_DISPLAY_NAME,
            logo_path="logo.png",
            support_streaming_message=True,
            support_proactive_message=True,
            module_path=__name__,
        )

    # ------------------------------------------------------------------ 元数据
    def meta(self) -> Any:
        return self.metadata

    # ------------------------------------------------------------------ 生命周期
    async def run(self) -> None:
        """常驻运行。Socket.io 服务端由插件统一启动，这里只登记实例并保持存活。"""
        global _MAIN_LOOP
        try:
            _MAIN_LOOP = asyncio.get_running_loop()
        except RuntimeError:
            _MAIN_LOOP = None
        platform_id = self.meta().id
        _ADAPTERS[platform_id] = self
        try:
            if PlatformStatus is not None:
                self.status = PlatformStatus.RUNNING
        except Exception:  # noqa: BLE001
            pass
        logger.info(f"[AstrBot+] 平台适配器 astrbot_plus[{platform_id}] 已启动。")
        try:
            await self._stop.wait()
        except asyncio.CancelledError:  # noqa: PERF203
            pass
        finally:
            _ADAPTERS.pop(platform_id, None)
            logger.info(f"[AstrBot+] 平台适配器 astrbot_plus[{platform_id}] 已停止。")

    async def terminate(self) -> None:
        self._stop.set()

    def create_event(self, message: Any) -> AstrBotPlusEvent:  # type: ignore[override]
        return AstrBotPlusEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            adapter=self,
        )

    # ------------------------------------------------------------------ 入站
    async def handle_incoming(
        self,
        session_id: str,
        text: str,
        sender_id: str = "client",
        sender_name: str = "AstrBot+ 用户",
    ) -> None:
        """把客户端消息构造成 AstrBot 事件并投入事件队列。"""
        msg = AstrBotMessage()  # type: ignore[operator]
        if MessageType is not None:
            msg.type = MessageType.FRIEND_MESSAGE
        msg.self_id = self.meta().id
        msg.session_id = session_id
        msg.message_id = uuid.uuid4().hex
        msg.sender = MessageMember(user_id=sender_id, nickname=sender_name)  # type: ignore[operator]
        msg.message_str = text
        msg.raw_message = {"session_id": session_id, "text": text, "sender_id": sender_id}
        try:
            msg.message = [Plain(text=text)]  # type: ignore[operator]
        except Exception:  # noqa: BLE001
            msg.message = []
        msg.timestamp = int(time.time())
        event = self.create_event(msg)
        self.commit_event(event)

    # ------------------------------------------------------------------ 出站
    async def send_by_session(self, session: Any, message_chain: Any) -> None:
        session_id = getattr(session, "session_id", None) or str(session)
        text = _extract_text(message_chain)
        if text:
            _push(session_id, text, done=True)


async def dispatch_incoming(
    platform_id: str,
    session_id: str,
    text: str,
    sender_id: str = "client",
    sender_name: str = "AstrBot+ 用户",
) -> bool:
    """把一条入站消息派发给对应适配器；找不到或无法调度返回 False。

    入站请求来自 Socket.io 事件循环线程，而 AstrBot 的事件队列绑定在主事件
    循环上，因此这里统一调度回主循环执行，避免跨线程写 asyncio.Queue。
    """
    adapter = _ADAPTERS.get(platform_id) or _ADAPTERS.get(ADAPTER_NAME)
    if adapter is None:
        return False
    loop = _MAIN_LOOP
    if loop is None or not loop.is_running():
        logger.warning("[AstrBot+] 主事件循环不可用，无法把消息交给机器人处理。")
        return False
    try:
        fut = asyncio.run_coroutine_threadsafe(
            adapter.handle_incoming(session_id, text, sender_id, sender_name), loop
        )
        await asyncio.wrap_future(fut)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[AstrBot+] 派发到主循环失败：{exc}")
        return False
    return True


def available_platform_ids() -> list[str]:
    return list(_ADAPTERS.keys())
