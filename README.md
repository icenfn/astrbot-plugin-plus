<div align="center">
  <img src="logo.svg" alt="AstrBot+ Logo" width="110" height="110">

  # astrbot-plugin-plus

  **AstrBot+ 客户端的配套插件 — AI 用户与群聊管理**

  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
  [![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.18-4B8BBE)](https://astrbot.app)

</div>

---

## ✨ 简介

本插件是开源跨平台客户端 [**AstrBot+**](https://github.com/icenfn/astrbot-plus) 的配套后端，
为客户端提供 **AI 用户（好友）** 与 **群聊** 的注册表管理能力，并通过插件 Web API 暴露给客户端。

> Logo 沿用 astrbot-plus 的品牌图标，版本号与 astrbot-plus 保持一致。

## 🚀 功能

- 🧑‍🤝‍🧑 **AI 用户管理**：创建 / 列出 / 删除 AI 用户。每个 AI 用户可绑定一个对话配置
  （provider）与人格（persona）。
- 👥 **群聊管理**：把多个 AI 用户拉进同一个群聊；发送消息时文本会扇出给每位成员。
- 🔒 **独立会话上下文**：群聊中 **每位 AI 成员拥有独立 UMO / session**，互不串扰
  （客户端按 `webchat:GroupMessage:<groupId>:<friendId>` 为每位成员维护独立会话）。
- 💾 **持久化**：数据保存在 `data/plugin_data/astrbot_plugin_plus/registry.json`。

## 📦 安装

将本仓库放入 AstrBot 的 `data/plugins/` 目录（或直接在 WebUI 插件市场安装），
然后在插件管理页点击 **重载插件**。

## 🔌 Web API

所有接口挂在 `/api/plugin/astrbot_plugin_plus/` 下，返回统一信封
`{"status": "ok"|"error", "message": ..., "data": ...}`。

| 路由 | 方法 | 说明 |
| --- | --- | --- |
| `/users` | `GET` | 列出全部 AI 用户 |
| `/users` | `POST` | 新建 / 更新 AI 用户（按 `id` 幂等） |
| `/users?id=<id>` | `DELETE` | 删除 AI 用户 |
| `/groups` | `GET` | 列出全部群聊 |
| `/groups` | `POST` | 新建 / 更新群聊 |
| `/groups?id=<id>` | `DELETE` | 删除群聊 |
| `/health` | `GET` | 健康检查 |

### 数据结构

```jsonc
// AI 用户
{ "id": "f_xxx", "name": "小助手", "configId": "default", "personaId": null,
  "avatarSeed": "小助手", "createdAt": 1720000000000 }

// 群聊
{ "id": "g_xxx", "name": "头脑风暴群", "memberIds": ["f_a", "f_b"],
  "avatarSeed": "头脑风暴群", "createdAt": 1720000000000 }
```

## 💬 指令

- `/plus` — 查看插件状态（AI 用户与群聊列表）。

## 📄 许可证

本项目基于 [MIT](./LICENSE) 许可证开源。

## ⚠️ 免责声明

astrbot-plugin-plus 是社区开发的第三方插件，与 AstrBot 官方无隶属关系。
