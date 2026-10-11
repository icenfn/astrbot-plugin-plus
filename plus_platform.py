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
from pathlib import Path
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


def list_bots() -> list[dict[str, Any]]:
    """返回当前已实例化的 AstrBot+ 机器人（Agent）列表。

    插件与 AstrBot 运行在同一进程中：WebUI「创建机器人」里每启用一个
    astrbot_plus 平台，就会实例化一个 :class:`AstrBotPlusAdapter` 并登记到
    :data:`_ADAPTERS`。因此这里直接从内存读取即可，无需回调 AstrBot 的
    HTTP OpenAPI（也不需要 AstrBot 的 API Key）。
    """
    bots: list[dict[str, Any]] = []
    for platform_id, adapter in _ADAPTERS.items():
        cfg = getattr(adapter, "platform_config", None) or {}
        # 昵称：WebUI「创建机器人」中填写的 nickname；未填则回退到机器人 id，
        # 客户端消息列表显示该名字。
        nickname = (
            cfg.get("nickname")
            or cfg.get("name")
            or cfg.get("bot_name")
            or ""
        )
        name = str(nickname) if nickname else str(platform_id)
        bots.append(
            {
                "id": str(platform_id),
                "name": name,
                "nickname": str(nickname),
                "platform": ADAPTER_NAME,
                "enabled": bool(cfg.get("enable", True)),
            }
        )
    # 稳定排序，避免每次刷新列表顺序跳动。
    bots.sort(key=lambda b: b["id"])
    return bots


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


# --- 单轮消息聚合（修复「偶尔漏掉 AI 回复」） ------------------------------------
# 当 AstrBot 开启「输出工具调用信息 / 显示思考内容」等配置时，一个回合内会产生多次
# send()/send_streaming() 调用（工具状态、思考内容、最终回答…）。旧实现每次 send()
# 都携带 done=True，Socket 层收到第一个 done 即结束该路由，导致同回合后续消息丢失。
# 现改为：同一会话、同一回合内的文本先累积，并在短暂静默后统一发送一次 done。
_TURN_BUFFER: dict[str, list[str]] = {}
_TURN_TIMERS: dict[str, Any] = {}
_TURN_DEBOUNCE_SECONDS = 0.4


def _flush_turn(session_id: str) -> None:
    """静默后将本回合累积的文本作为完整回复一次性下发。"""
    _TURN_TIMERS.pop(session_id, None)
    parts = _TURN_BUFFER.pop(session_id, [])
    if not parts:
        return
    _push(session_id, "".join(parts), done=True)


def _schedule_turn_flush(session_id: str, delay: float = _TURN_DEBOUNCE_SECONDS) -> None:
    """安排（或重置）本回合的结束定时器；重复调用会取消上一个定时器。"""
    loop = _MAIN_LOOP
    if loop is None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
    old = _TURN_TIMERS.pop(session_id, None)
    if old is not None:
        try:
            old.cancel()
        except Exception:  # noqa: BLE001
            pass
    if loop is None:
        _flush_turn(session_id)
        return
    try:
        _TURN_TIMERS[session_id] = loop.call_later(delay, _flush_turn, session_id)
    except Exception:  # noqa: BLE001
        _flush_turn(session_id)


class AstrBotPlusEvent(AstrMessageEvent):  # type: ignore[misc]
    """AstrBot+ 的消息事件；把回复通过 Socket.io 回推给客户端。"""

    def __init__(self, *args: Any, adapter: "AstrBotPlusAdapter | None" = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._adapter = adapter

    async def send(self, message: Any) -> None:
        # 标记「本次事件已产生发送操作」。AstrBot 的 ProcessStage 以
        # ``not event._has_send_oper`` 作为「继续调用 LLM」的条件，
        # 若不置位，指令（如 /reset）已回复后仍会再触发一次 LLM 回复。
        self._has_send_oper = True
        text = _extract_text(message)
        if text:
            _TURN_BUFFER.setdefault(self.session_id, []).append(text)
        # 一回合内可能多次 send()（工具调用状态、思考内容、最终回答等），
        # 延迟结束本回合，等静默后再统一 done，避免漏掉同回合后续消息。
        _schedule_turn_flush(self.session_id)

    async def send_streaming(self, generator: Any, use_fallback: bool = False) -> None:  # noqa: D401
        """流式发送：逐段推送增量，最后标记完成。"""
        self._has_send_oper = True
        try:
            async for chunk in generator:
                text = _extract_text(chunk)
                if text:
                    _TURN_BUFFER.setdefault(self.session_id, []).append(text)
                    _push(self.session_id, text, delta=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 流式发送出错：{exc}")
        _schedule_turn_flush(self.session_id)


@register_platform_adapter(
    ADAPTER_NAME,
    "AstrBot+ 跨平台客户端平台适配器（Socket.io 单端口）",
    default_config_tmpl={
        "type": ADAPTER_NAME,
        "enable": False,
        "id": ADAPTER_NAME,
        "nickname": "",
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


# --- WebUI 平台 logo 令牌韧性修复 ------------------------------------------------
# AstrBot 通过「文件令牌」为平台 logo 提供访问（/api/v1/files/tokens/<token>），
# 但该机制存在已知缺陷（上游 #10294，2026-10-02 才修复）：
#   1) 旧版本中令牌是「一次性」的：图片第一次加载后令牌即被销毁，之后永远 404；
#   2) 令牌仅 1 小时有效期且存于内存，过期或服务重启后旧链接全部 404。
# 插件与 AstrBot 同进程运行，这里做两处防御性处理，让本插件 logo 在新旧版本
# AstrBot 上都能稳定显示：
#   - 包装 file_token_service.handle_file：指向本插件 logo 的令牌访问时不消费；
#   - 后台任务定期为指向本插件 logo 的令牌续期，避免 1 小时过期。

_LOGO_FILE = Path(__file__).parent / "logo.png"
_logo_keepalive_task: "asyncio.Task | None" = None


def _patch_logo_handle_file() -> None:
    """让指向本插件 logo 的文件令牌「免消费」（兼容旧版一次性令牌行为）。"""
    try:
        from astrbot.core import file_token_service as svc
    except Exception:  # noqa: BLE001
        return
    if getattr(svc, "_astrbot_plus_logo_patched", False):
        return
    orig = getattr(svc, "handle_file", None)
    if not callable(orig):
        return
    logo_abs = str(_LOGO_FILE)

    async def handle_file(file_token):  # noqa: ANN001, ANN202
        try:
            entry = svc.staged_files.get(file_token)
        except Exception:  # noqa: BLE001
            entry = None
        if entry and str(entry[0]) == logo_abs:
            if not _LOGO_FILE.exists():
                raise FileNotFoundError(f"File does not exist: {logo_abs}")
            return logo_abs  # 不消费、不销毁，供 WebUI 重复加载
        return await orig(file_token)

    svc.handle_file = handle_file  # type: ignore[method-assign]
    svc._astrbot_plus_logo_patched = True  # type: ignore[attr-defined]
    logger.info("[AstrBot+] 已启用平台 logo 令牌兼容补丁（防一次性消费）。")


async def _logo_keepalive() -> None:
    """定期为指向本插件 logo 的文件令牌续期，避免 1 小时过期后 404。"""
    try:
        from astrbot.core import file_token_service as svc
    except Exception:  # noqa: BLE001
        return
    logo_abs = str(_LOGO_FILE)
    while True:
        await asyncio.sleep(240)  # 4 分钟 < 3600 秒有效期
        try:
            now = time.time()
            staged = getattr(svc, "staged_files", None)
            if not isinstance(staged, dict):
                continue
            for token, entry in list(staged.items()):
                try:
                    if str(entry[0]) != logo_abs:
                        continue
                    # 兼容 2 元组 (path, expire) 与 3 元组 (path, expire, single_use)
                    staged[token] = (entry[0], now + 3600, *entry[2:])
                except Exception:  # noqa: BLE001
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[AstrBot+] logo 令牌续期异常：{exc}")


def install_logo_resilience() -> None:
    """安装 WebUI logo 令牌的跨版本兼容处理（幂等，可重复调用）。"""
    _patch_logo_handle_file()
    global _logo_keepalive_task
    if _logo_keepalive_task is not None and not _logo_keepalive_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        _logo_keepalive_task = loop.create_task(_logo_keepalive())


def stop_logo_keepalive() -> None:
    """插件卸载时停止续期任务。"""
    global _logo_keepalive_task
    if _logo_keepalive_task is not None:
        _logo_keepalive_task.cancel()
        _logo_keepalive_task = None
