"""astrbot-plugin-plus — 配套 AstrBot+ 客户端的平台适配器插件。

本插件把 AstrBot+ 客户端注册为 AstrBot 的一个**消息平台**（平台适配器
``astrbot_plus``），从而出现在 WebUI「机器人 → 创建机器人」的平台选择列表
中。客户端通过一个独立的 Socket.io 端口（默认 ``6199``）与其通信，完成
**Agent（机器人）列表**拉取与**流式对话**。

* 平台适配器在 :mod:`plus_platform` 中通过 ``@register_platform_adapter`` 注册；
* Socket.io 服务端在 :mod:`plus_socket` 中实现（单端口）；
* 另外注册了 ``/plus`` 文本指令，用于在聊天中快速查看插件状态。

配置项（见 ``_conf_schema.json``）：``listen_host`` / ``listen_port`` /
``access_key`` / ``astrbot_base_url`` / ``astrbot_api_key``。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:  # AstrBot 通常以包形式加载插件；回退保证独立加载也能工作。
    from .plus_socket import PlusSocketServer
except ImportError:  # noqa: BLE001
    from plus_socket import PlusSocketServer

try:  # 导入平台适配器模块，触发 @register_platform_adapter 注册，
    # 使 astrbot_plus 出现在 WebUI「创建机器人」的平台列表中。
    from . import plus_platform  # noqa: F401
except Exception:  # noqa: BLE001
    try:
        import plus_platform  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        logger.warning("[AstrBot+] 平台适配器模块加载失败，astrbot_plus 将不可用。")

PLUGIN_NAME = "astrbot_plugin_plus"

__version__ = "0.3.2"


@register(
    PLUGIN_NAME,
    "icenfn",
    "AstrBot+ 配套插件：把客户端接入为消息平台（Agent 列表 + 流式对话）",
    __version__,
)
class AstrBotPlusPlugin(Star):
    """AstrBot+ 配套插件：启动 Socket.io 服务端并注册平台适配器。"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self._socket: PlusSocketServer | None = None

        # ---- 启动 Socket.io 服务端（插件独立端口；对外只暴露这一个端口） ------
        self._socket = self._start_socket()
        if self._socket is None or not self._socket.available:
            logger.warning(
                "[AstrBot+] Socket.io 服务端不可用（缺少 python-socketio / aiohttp 依赖）。"
            )
        else:
            logger.info(f"[AstrBot+] 插件已加载（v{__version__}），Socket.io 服务端就绪。")

    # ------------------------------------------------------------ 插件配置读取
    def _plugin_conf(self) -> dict[str, Any]:
        """尽力从 AstrBot 读取本插件配置，兼容多种访问方式，失败则回退默认值。"""
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
        if {"listen_port", "access_key", "astrbot_base_url"} & set(cfg.keys()):
            return dict(cfg)
        return {}

    def _start_socket(self) -> PlusSocketServer:
        conf = self._plugin_conf()
        server = PlusSocketServer(
            host=str(conf.get("listen_host") or "0.0.0.0"),
            port=int(conf.get("listen_port") or 6199),
            access_key=str(conf.get("access_key") or ""),
            astrbot_base_url=str(conf.get("astrbot_base_url") or "http://127.0.0.1:6185"),
            astrbot_api_key=str(conf.get("astrbot_api_key") or ""),
        )
        server.start()
        return server

    # ------------------------------------------------------------------- 指令
    @filter.command("plus")
    async def plus_status(self, event: AstrMessageEvent):
        """查看 AstrBot+ 插件状态：/plus"""
        ready = "就绪" if (self._socket and self._socket.available) else "未就绪"
        logger.info("[AstrBot+] 收到 /plus 状态查询指令。")
        yield event.plain_result(
            f"🌟 AstrBot+ 配套插件 v{__version__}\nSocket.io 服务端：{ready}"
        )

    async def terminate(self):
        if getattr(self, "_socket", None) is not None:
            self._socket.stop()
        logger.info("[AstrBot+] 插件已卸载。")
