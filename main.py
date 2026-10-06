"""astrbot-plugin-plus — 配套 AstrBot+ 客户端的 AI 用户 / 群聊管理插件。

本插件为桌面 / 移动客户端 **AstrBot+** 提供后端注册表：

* ✨ **AI 用户（好友）**：一个 AI 用户 = 一套可选的对话配置（provider）与人格，
  并拥有独立的会话上下文。
* 👥 **群聊**：把多个 AI 用户拉进同一个群聊；发消息时文本会同时发给每位成员，
  且 **每位成员在群内拥有独立的会话上下文（独立 UMO / session）**，互不串扰。

数据保存在 ``data/plugin_data/astrbot_plugin_plus/registry.json``，并通过插件 Web API
（``/api/plugin/astrbot_plugin_plus/...``）暴露给客户端，供其增删改查。

Web API 一览（供 AstrBot+ 调用）：

===============================================  ======  =================================
路由                                             方法    说明
===============================================  ======  =================================
/{name}/health                                   GET     健康检查
/{name}/users                                    GET     列出全部 AI 用户
/{name}/users                                    POST    新建 / 更新一个 AI 用户
/{name}/users                                    DELETE  删除一个 AI 用户（?id=）
/{name}/groups                                   GET     列出全部群聊
/{name}/groups                                   POST    新建 / 更新一个群聊
/{name}/groups                                   DELETE  删除一个群聊（?id=）
===============================================  ======  =================================

此外还注册了 ``/plus`` 文本指令，方便在聊天中快速查看插件状态。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_plus"

__version__ = "0.2.0"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _ok(data: Any = None) -> Any:
    """AstrBot+ 客户端期望的统一响应信封。"""
    return json_response({"status": "ok", "message": None, "data": data})


@register(PLUGIN_NAME, "icenfn", "AstrBot+ 配套插件：管理 AI 用户与群聊", __version__)
class AstrBotPlusPlugin(Star):
    """AI 用户与群聊的注册表管理插件。"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context

        # 数据文件位于 data/plugin_data/astrbot_plugin_plus/registry.json
        self.data_dir: Path = StarTools.get_data_dir(PLUGIN_NAME)
        self.store_file: Path = self.data_dir / "registry.json"
        self._data: dict[str, list[dict[str, Any]]] = {"users": [], "groups": []}
        self._load()

        # ---- 注册插件 Web API（客户端据此做增删改查） -----------------------
        base = f"/{PLUGIN_NAME}"
        context.register_web_api(f"{base}/health", self.health, ["GET"], "插件健康检查")
        context.register_web_api(f"{base}/users", self.list_users, ["GET"], "列出 AI 用户")
        context.register_web_api(f"{base}/users", self.upsert_user, ["POST"], "新建 / 更新 AI 用户")
        context.register_web_api(f"{base}/users", self.delete_user, ["DELETE"], "删除 AI 用户")
        context.register_web_api(f"{base}/groups", self.list_groups, ["GET"], "列出群聊")
        context.register_web_api(f"{base}/groups", self.upsert_group, ["POST"], "新建 / 更新群聊")
        context.register_web_api(f"{base}/groups", self.delete_group, ["DELETE"], "删除群聊")

        logger.info(
            f"[AstrBot+] 已加载，{len(self._data['users'])} 个 AI 用户 / "
            f"{len(self._data['groups'])} 个群聊。"
        )

    # ------------------------------------------------------------------ 持久化
    def _load(self) -> None:
        try:
            if self.store_file.exists():
                raw = json.loads(self.store_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data["users"] = list(raw.get("users") or [])
                    self._data["groups"] = list(raw.get("groups") or [])
        except Exception as exc:  # noqa: BLE001 - 数据损坏时回退为空，避免插件加载失败
            logger.warning(f"[AstrBot+] 读取注册表失败，将使用空数据：{exc}")
            self._data = {"users": [], "groups": []}

    def _save(self) -> None:
        try:
            self.store_file.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AstrBot+] 写入注册表失败：{exc}")

    # -------------------------------------------------------------- 规范化字段
    @staticmethod
    def _normalize_user(payload: dict[str, Any]) -> dict[str, Any] | None:
        name = str(payload.get("name") or "").strip()
        if not name:
            return None
        uid = str(payload.get("id") or "").strip() or _new_id("f")
        config_id = payload.get("configId", payload.get("config_id"))
        persona_id = payload.get("personaId", payload.get("persona_id"))
        created = payload.get("createdAt", payload.get("created_at"))
        return {
            "id": uid,
            "name": name,
            "configId": (str(config_id).strip() if config_id else None) or None,
            "personaId": (str(persona_id).strip() if persona_id else None) or None,
            "avatarSeed": str(payload.get("avatarSeed") or name),
            "createdAt": int(created) if created else _now_ms(),
        }

    def _normalize_group(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        name = str(payload.get("name") or "").strip()
        if not name:
            return None, "群名称不能为空"
        member_ids = payload.get("memberIds", payload.get("member_ids")) or []
        if not isinstance(member_ids, list):
            return None, "memberIds 必须是数组"
        # 只保留真实存在的 AI 用户 id，并去重。
        valid = {u["id"] for u in self._data["users"]}
        seen: list[str] = []
        for mid in member_ids:
            mid = str(mid)
            if mid in valid and mid not in seen:
                seen.append(mid)
        if len(seen) < 2:
            return None, "群聊至少需要 2 个有效的 AI 用户"
        gid = str(payload.get("id") or "").strip() or _new_id("g")
        created = payload.get("createdAt", payload.get("created_at"))
        return {
            "id": gid,
            "name": name,
            "memberIds": seen,
            "avatarSeed": str(payload.get("avatarSeed") or name),
            "createdAt": int(created) if created else _now_ms(),
        }, ""

    # ------------------------------------------------------------------ Web API
    async def health(self):
        return _ok(
            {
                "plugin": PLUGIN_NAME,
                "version": __version__,
                "users": len(self._data["users"]),
                "groups": len(self._data["groups"]),
            }
        )

    async def list_users(self):
        return _ok({"users": self._data["users"]})

    async def upsert_user(self):
        payload = await request.json(default={}) or {}
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象")
        user = self._normalize_user(payload)
        if user is None:
            return error_response("name 不能为空")
        replaced = False
        for idx, existing in enumerate(self._data["users"]):
            if existing.get("id") == user["id"]:
                # 保留原创建时间。
                user["createdAt"] = int(existing.get("createdAt") or user["createdAt"])
                self._data["users"][idx] = user
                replaced = True
                break
        if not replaced:
            self._data["users"].append(user)
        self._save()
        return _ok(user)

    async def delete_user(self):
        uid = request.query.get("id")
        if not uid:
            return error_response("缺少 id 参数")
        before = len(self._data["users"])
        self._data["users"] = [u for u in self._data["users"] if u.get("id") != uid]
        # 一并从群聊成员中移除。
        for grp in self._data["groups"]:
            members = [m for m in grp.get("memberIds", []) if m != uid]
            grp["memberIds"] = members
        # 清理成员不足 2 人的群聊。
        self._data["groups"] = [g for g in self._data["groups"] if len(g.get("memberIds", [])) >= 2]
        self._save()
        deleted = before - len(self._data["users"])
        return _ok({"deleted": deleted, "id": uid})

    async def list_groups(self):
        return _ok({"groups": self._data["groups"]})

    async def upsert_group(self):
        payload = await request.json(default={}) or {}
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象")
        group, err = self._normalize_group(payload)
        if group is None:
            return error_response(err)
        replaced = False
        for idx, existing in enumerate(self._data["groups"]):
            if existing.get("id") == group["id"]:
                group["createdAt"] = int(existing.get("createdAt") or group["createdAt"])
                self._data["groups"][idx] = group
                replaced = True
                break
        if not replaced:
            self._data["groups"].append(group)
        self._save()
        return _ok(group)

    async def delete_group(self):
        gid = request.query.get("id")
        if not gid:
            return error_response("缺少 id 参数")
        before = len(self._data["groups"])
        self._data["groups"] = [g for g in self._data["groups"] if g.get("id") != gid]
        self._save()
        return _ok({"deleted": before - len(self._data["groups"]), "id": gid})

    # ------------------------------------------------------------------- 指令
    @filter.command("plus")
    async def plus_status(self, event: AstrMessageEvent):
        """查看 AstrBot+ 插件状态：/plus"""
        users = "、".join(u["name"] for u in self._data["users"]) or "（无）"
        groups = "、".join(g["name"] for g in self._data["groups"]) or "（无）"
        yield event.plain_result(
            "🌟 AstrBot+ 配套插件\n"
            f"AI 用户（{len(self._data['users'])}）：{users}\n"
            f"群聊（{len(self._data['groups'])}）：{groups}"
        )

    async def terminate(self):
        self._save()
        logger.info("[AstrBot+] 插件已卸载，注册表已保存。")
