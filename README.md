<div align="center">
  <img src="logo.svg" alt="AstrBot+ Logo" width="110" height="110">

  # astrbot-plugin-plus

  **AstrBot+ 客户端的配套插件 — Socket.io 通信 + 机器人与对话管理**

  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
  [![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.18-4B8BBE)](https://astrbot.app)

</div>

---

## ✨ 简介

本插件是开源跨平台客户端 [**AstrBot+**](https://github.com/icenfn/astrbot-plus) 的配套后端。
它内置一个 **Socket.io 服务端**，在独立的端口上为客户端提供：

- 🤖 **机器人列表**：来自 AstrBot WebUI「创建机器人」页面；
- 💬 **对话（Webchat 会话）**：列出 / 新建 / 删除 / 拉取历史；
- 🌊 **流式聊天**：把客户端消息转发给 AstrBot，并把回复增量回推；
- 📇 **AI 好友与群聊注册表**：本地维护、持久化。

> 插件**单独监听一个端口**（默认 `6199`），对外只暴露这一个端口；客户端只需连接
> `ws://<host>:<port>`，无需再走 AstrBot 自身的 HTTP 接口。Logo 沿用 astrbot-plus
> 品牌图标，版本号与 astrbot-plus 保持一致。

## 📦 安装

将本仓库放入 AstrBot 的 `data/plugins/` 目录（或直接在 WebUI 插件市场安装），
然后在插件管理页点击 **重载插件**。首次使用请确保依赖已安装（`requirements.txt`
中的 `python-socketio` 与 `aiohttp`）。

## ⚙️ 配置

在 AstrBot 的插件配置页设置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `access_key` | *(空)* | 客户端连接时使用的访问密钥；留空表示不校验。 |
| `listen_host` | `0.0.0.0` | Socket.io 服务端监听地址。 |
| `listen_port` | `6199` | Socket.io 服务端监听端口。 |
| `astrbot_base_url` | `http://127.0.0.1:6185` | 插件据此拉取机器人列表与 Webchat 会话。 |
| `astrbot_api_key` | *(空)* | 插件向 AstrBot 拉取数据时使用的 API Key。 |
| `enable_group_fanout` | `true` | 群聊消息扇出。 |
| `max_ai_per_group` | `8` | 单个群聊允许的最大 AI 成员数。 |

## 🔌 Socket.io 事件

客户端与服务端均通过 Socket.io 事件通信。握手时在 `auth` 中携带
`{ "token": "<access_key>" }` 完成鉴权。

| 事件 | 方向 | 说明 |
| --- | --- | --- |
| `ping` | C → S | 心跳，返回 `{ pong: true }`。 |
| `config` | C → S | 返回插件版本 / AstrBot 地址等能力信息。 |
| `bots:list` | C → S | 返回机器人列表（WebUI「创建机器人」页）。 |
| `dialogs:list` | C → S | 返回对话（Webchat 会话）列表。 |
| `dialogs:create` | C → S | 新建对话，可传 `botId` 绑定机器人。 |
| `dialogs:delete` | C → S | 删除对话（按会话 id）。 |
| `dialogs:history` | C → S | 拉取某对话的历史消息。 |
| `chat:send` | C → S | 发送消息，服务端流式回推 `chat:delta` / `chat:done`。 |
| `registry:list` | C → S | 列出本地注册表（AI 好友 / 群聊）。 |
| `registry:user:upsert` / `user:delete` | C → S | 维护 AI 好友。 |
| `registry:group:upsert` / `group:delete` | C → S | 维护群聊。 |
| `chat:delta` / `chat:done` / `chat:error` | S → C | 流式回复增量 / 完成 / 出错。 |

除流式事件外，所有请求型事件都会返回统一信封
`{ "status": "ok"|"error", "data": ..., "message": ... }`。

### 数据结构

```jsonc
// 机器人
{ "id": "xxx", "name": "小助手", "platform": "webchat", "enabled": true }

// 对话（Webchat 会话）
{ "id": "session_id", "title": "新对话", "botId": "xxx",
  "personaId": null, "createdAt": 1720000000000 }

// AI 好友
{ "id": "f_xxx", "name": "小助手", "configId": "default", "personaId": null,
  "avatarSeed": "小助手", "createdAt": 1720000000000 }

// 群聊
{ "id": "g_xxx", "name": "头脑风暴群", "memberIds": ["f_a", "f_b"],
  "avatarSeed": "头脑风暴群", "createdAt": 1720000000000 }
```

## 🧩 兼容的 Web API

为便于调试与兼容，原有的 HTTP Web API 继续保留（挂在
`/api/plugin/astrbot_plugin_plus/` 下，返回统一信封）：

| 路由 | 方法 | 说明 |
| --- | --- | --- |
| `/users` | `GET` | 列出全部 AI 用户 |
| `/users` | `POST` | 新建 / 更新 AI 用户（按 `id` 幂等） |
| `/users?id=<id>` | `DELETE` | 删除 AI 用户 |
| `/groups` | `GET` | 列出全部群聊 |
| `/groups` | `POST` | 新建 / 更新群聊 |
| `/groups?id=<id>` | `DELETE` | 删除群聊 |
| `/health` | `GET` | 健康检查 |

## 💬 指令

- `/plus` — 查看插件状态（AI 用户与群聊列表）。

## 📄 许可证

本项目基于 [MIT](./LICENSE) 许可证开源。

## ⚠️ 免责声明

astrbot-plugin-plus 是社区开发的第三方插件，与 AstrBot 官方无隶属关系。
