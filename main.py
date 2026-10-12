"""astrbot-plugin-plus — 配套 AstrBot+ 客户端的平台适配器插件。

本插件把 AstrBot+ 客户端注册为 AstrBot 的一个**消息平台**（平台适配器
``astrbot_plus``），从而出现在 WebUI「机器人 → 创建机器人」的平台选择列表
中。客户端通过一个独立的 Socket.io 端口（默认 ``6199``）与其通信，完成
**Agent（机器人）列表**拉取与**流式对话**。

* 平台适配器在 :mod:`plus_platform` 中通过 ``@register_platform_adapter`` 注册；
* Socket.io 服务端在 :mod:`plus_socket` 中实现（单端口）；
* 机器人列表直接读取本进程内已实例化的平台适配器，无需 AstrBot OpenAPI；
* 另外注册了 ``/plus`` 文本指令，用于在聊天中快速查看插件状态。

配置项（见 ``_conf_schema.json``）：``listen_host`` / ``listen_port``。
"""

from __future__ import annotations

import asyncio
import re
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

_VALID_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

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

__version__ = "0.4.6"


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

        # WebUI 平台 logo 令牌的跨版本兼容处理（修复「创建机器人」中 logo 404）。
        _pp = globals().get("plus_platform")
        if _pp is not None:
            try:
                _pp.install_logo_resilience()
            except Exception as _exc:  # noqa: BLE001
                logger.debug(f"[AstrBot+] logo 兼容补丁安装跳过：{_exc}")

        # 插件重载/重装后自愈平台适配器实例（AstrBot 的 reload 只重载插件代码，
        # 不会重新实例化已有的 astrbot_plus 平台，导致「不识别之前的机器人」）。
        self._start_adapter_rescue()

        # ---- 注册 WebUI 可视化视图（「机器人管理」页面）的后端 API -------
        self._register_web_apis()

    # ------------------------------------------------ WebUI 可视化视图后端 API
    def _register_web_apis(self) -> None:
        """注册「机器人管理」视图所需的后端 API（AstrBot >= 4.18 可视化视图规范）。

        路由以插件名为前缀；视图端 bridge endpoint 不含插件名，由 Dashboard
        转发到 ``/api/v1/plugins/extensions/<plugin_name>/...``。
        旧版 AstrBot 无 ``register_web_api``，此时跳过并记录日志。
        """
        register_web_api = getattr(self.context, "register_web_api", None)
        if not callable(register_web_api):
            logger.info(
                "[AstrBot+] 当前 AstrBot 版本不支持插件可视化视图（缺少 register_web_api），已跳过「机器人管理」页面注册。"
            )
            return
        try:
            register_web_api(f"/{PLUGIN_NAME}/bots", self.page_list_bots, ["GET"], "列出 AstrBot+ 机器人")
            register_web_api(f"/{PLUGIN_NAME}/bots/create", self.page_create_bot, ["POST"], "创建 AstrBot+ 机器人")
            register_web_api(f"/{PLUGIN_NAME}/bots/toggle", self.page_toggle_bot, ["POST"], "启用/停用 AstrBot+ 机器人")
            register_web_api(f"/{PLUGIN_NAME}/bots/delete", self.page_delete_bot, ["POST"], "删除 AstrBot+ 机器人")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 「机器人管理」视图 API 注册失败：{exc}")
            return
        logger.info("[AstrBot+] 已注册「机器人管理」可视化视图后端 API。")

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
        """返回全局配置中的平台列表（可变引用），缺失时创建空列表。"""
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

    @staticmethod
    def _bot_view(plat: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(plat.get("id") or ""),
            "nickname": str(plat.get("nickname") or ""),
            "enable": bool(plat.get("enable", False)),
            "type": str(plat.get("type") or ""),
        }

    def _plus_bots(self) -> list[dict[str, Any]]:
        name = self._adapter_name()
        bots = [
            self._bot_view(p)
            for p in self._platform_configs()
            if isinstance(p, dict) and p.get("type") == name
        ]
        bots.sort(key=lambda b: b["id"])
        return bots

    async def _load_platform(self, plat: dict[str, Any]) -> None:
        pm = getattr(self.context, "platform_manager", None)
        if pm is None or not hasattr(pm, "load_platform"):
            return
        try:
            await pm.load_platform(dict(plat))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 加载机器人 {plat.get('id')} 失败：{exc}")

    async def _terminate_platform(self, bot_id: str) -> None:
        pm = getattr(self.context, "platform_manager", None)
        if pm is None or not hasattr(pm, "terminate_platform"):
            return
        try:
            await pm.terminate_platform(bot_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AstrBot+] 停止机器人 {bot_id} 失败：{exc}")

    def _notify_bots_changed(self) -> None:
        """机器人配置发生变化后广播给客户端，触发其列表实时刷新。"""
        sock = getattr(self, "_socket", None)
        if sock is None:
            return
        try:
            sock._broadcast("bots:changed", {"plugin": PLUGIN_NAME})
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------- 视图 API handlers
    async def page_list_bots(self):
        """GET /astrbot_plugin_plus/bots — 返回全部 AstrBot+ 机器人。"""
        try:
            bots = self._plus_bots()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取机器人列表失败：{exc}")
        return json_response({"bots": bots, "plugin": PLUGIN_NAME})

    async def page_create_bot(self):
        """POST /astrbot_plugin_plus/bots/create — 新建机器人并持久化。"""
        payload = await request.json(default={}) or {}
        bot_id = str(payload.get("id") or "").strip()
        nickname = str(payload.get("nickname") or "").strip()
        enable = bool(payload.get("enable", True))
        if not bot_id:
            return error_response("机器人 ID 不能为空", status_code=400)
        if not _VALID_ID_RE.match(bot_id):
            return error_response("机器人 ID 仅允许字母、数字、下划线、连字符", status_code=400)
        platforms = self._platform_configs()
        if any(isinstance(p, dict) and str(p.get("id")) == bot_id for p in platforms):
            return error_response(f"机器人 ID「{bot_id}」已存在", status_code=409)
        bot = {"type": self._adapter_name(), "enable": enable, "id": bot_id, "nickname": nickname}
        platforms.append(bot)
        await self._save_config()
        self._notify_bots_changed()
        if enable:
            await self._load_platform(bot)
        logger.info(f"[AstrBot+] 通过可视化视图创建机器人：{bot_id}（enable={enable}）")
        return json_response({"created": self._bot_view(bot)})

    async def page_toggle_bot(self):
        """POST /astrbot_plugin_plus/bots/toggle — 启用/停用机器人。"""
        payload = await request.json(default={}) or {}
        bot_id = str(payload.get("id") or "").strip()
        enable = bool(payload.get("enable", False))
        if not bot_id:
            return error_response("机器人 ID 不能为空", status_code=400)
        name = self._adapter_name()
        target = None
        for p in self._platform_configs():
            if isinstance(p, dict) and str(p.get("id")) == bot_id and p.get("type") == name:
                target = p
                break
        if target is None:
            return error_response(f"未找到机器人「{bot_id}」", status_code=404)
        target["enable"] = enable
        await self._save_config()
        self._notify_bots_changed()
        if enable:
            await self._load_platform(dict(target))
        else:
            await self._terminate_platform(bot_id)
        logger.info(f"[AstrBot+] 通过可视化视图{'启用' if enable else '停用'}机器人：{bot_id}")
        return json_response({"updated": self._bot_view(target)})

    async def page_delete_bot(self):
        """POST /astrbot_plugin_plus/bots/delete — 删除机器人。"""
        payload = await request.json(default={}) or {}
        bot_id = str(payload.get("id") or "").strip()
        if not bot_id:
            return error_response("机器人 ID 不能为空", status_code=400)
        name = self._adapter_name()
        platforms = self._platform_configs()
        remain = [
            p
            for p in platforms
            if not (isinstance(p, dict) and str(p.get("id")) == bot_id and p.get("type") == name)
        ]
        if len(remain) == len(platforms):
            return error_response(f"未找到机器人「{bot_id}」", status_code=404)
        platforms[:] = remain
        await self._save_config()
        self._notify_bots_changed()
        await self._terminate_platform(bot_id)
        logger.info(f"[AstrBot+] 通过可视化视图删除机器人：{bot_id}")
        return json_response({"deleted": bot_id})

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
        if {"listen_port", "listen_host"} & set(cfg.keys()):
            return dict(cfg)
        return {}

    def _start_socket(self) -> PlusSocketServer:
        conf = self._plugin_conf()
        server = PlusSocketServer(
            host=str(conf.get("listen_host") or "0.0.0.0"),
            port=int(conf.get("listen_port") or 6199),
        )
        server.start()
        return server

    # -------------------------------------------------------- 平台适配器自愈
    def _start_adapter_rescue(self) -> None:
        """调度适配器自愈检查任务。

        插件重载 / 重装时，AstrBot 会解绑旧插件（注销平台适配器注册、清理旧模块）
        并重新导入新插件代码，但**不会**重新实例化已有的平台适配器：旧实例仍是
        旧模块的类对象，其模块级状态（``plus_platform._ADAPTERS`` 等）与新模块
        完全隔离，于是新插件实例「看不到」之前创建的 astrbot_plus 机器人
        （客户端 Agent 列表为空、消息无法派发）。这里异步检测陈旧 / 缺失的
        适配器实例，并通过平台管理器重新实例化。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[AstrBot+] 当前不在事件循环中，跳过适配器自愈检查。")
            return
        self._rescue_task = loop.create_task(self._ensure_platform_adapters())

    async def _ensure_platform_adapters(self) -> None:
        """带重试的适配器自愈：等平台管理器就绪后执行一次完整检查。"""
        for attempt in range(6):
            await asyncio.sleep(1.5 if attempt == 0 else 2.5)
            try:
                if await self._rescue_platform_adapters():
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[AstrBot+] 适配器自愈检查异常（{attempt + 1}/6）：{exc}")
        logger.debug("[AstrBot+] 适配器自愈检查结束（平台管理器未就绪或无配置）。")

    async def _rescue_platform_adapters(self) -> bool:
        """检查并恢复 astrbot_plus 平台适配器实例，返回本次是否完成检查。"""
        _pp = globals().get("plus_platform")
        if _pp is None:
            return True  # 适配器模块不可用，无需（也无法）自愈
        pm = getattr(self.context, "platform_manager", None)
        if pm is None:
            return False  # 平台管理器尚未就绪，稍后重试

        adapter_name = _pp.ADAPTER_NAME
        adapter_cls = _pp.AstrBotPlusAdapter
        insts = list(getattr(pm, "platform_insts", None) or [])

        # 1) 陈旧实例：类来自旧模块副本（isinstance 判定失败）→ 重新加载。
        for inst in insts:
            try:
                meta = inst.meta()
            except Exception:  # noqa: BLE001
                continue
            if getattr(meta, "name", None) != adapter_name:
                continue
            if isinstance(inst, adapter_cls):
                continue  # 当前模块的实例，状态一致，无需处理
            cfg = getattr(inst, "platform_config", None)
            if isinstance(cfg, dict) and hasattr(pm, "reload"):
                logger.info(
                    f"[AstrBot+] 检测到旧版适配器实例 {getattr(meta, 'id', '?')}，正在重新实例化…"
                )
                try:
                    await pm.reload(dict(cfg))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[AstrBot+] 适配器重载失败：{exc}")

        # 2) 配置存在但实例缺失（如插件重装后从未加载过）→ 直接加载。
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
                logger.info(f"[AstrBot+] 机器人 {plat.get('id')} 未实例化，正在加载…")
                try:
                    await pm.load_platform(dict(plat))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[AstrBot+] 机器人 {plat.get('id')} 加载失败：{exc}")
        return True

    # ------------------------------------------------------------------- 指令
    @filter.command("plus")
    async def plus_status(self, event: AstrMessageEvent):
        """查看 AstrBot+ 插件状态：/plus"""
        ready = "就绪" if (self._socket and self._socket.available) else "未就绪"
        bots = 0
        try:
            bots = len(plus_platform.list_bots())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        logger.info("[AstrBot+] 收到 /plus 状态查询指令。")
        yield event.plain_result(
            f"🌟 AstrBot+ 配套插件 v{__version__}\nSocket.io 服务端：{ready}\n已加载 Agent：{bots} 个"
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
