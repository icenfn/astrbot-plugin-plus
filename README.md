# AstrBot+ 配套插件（astrbot-plugin-plus）

`astrbot-plugin-plus` 是 [AstrBot+](https://github.com/icenfn/astrbot-plus) 桌面 / 移动客户端的**配套插件**。

它做两件事：

1. **注册平台适配器 `astrbot_plus`**：把 AstrBot+ 客户端注册为 AstrBot 的一个消息平台，使其出现在 AstrBot WebUI 的「机器人 → 创建机器人」平台选择列表中；
2. **内置 Socket.io 服务端（单端口）**：客户端通过一个独立端口（默认 `6199`）连接，完成 **Agent（机器人）列表**拉取与**流式对话**。

> 设计上，插件只对外暴露 Socket.io 这一个端口，AstrBot 自身的 WebUI / 主服务端口保持不变。

## 能力

- 🤖 **Agent**：即 AstrBot WebUI「创建机器人」页面创建的机器人。客户端据此展示列表并发起对话。
- 💬 **流式对话**：消息经插件投入 AstrBot 事件管线，回复以增量（`chat:delta`）流式回推，结束时 `chat:done`。
- 🔐 **访问密钥鉴权**：客户端在 Socket.io 握手 `auth.token` 中携带密钥，与插件配置的 `access_key` 一致才允许接入。
- 🪵 **规范日志**：关键路径（连接 / 鉴权 / 断开 / 派发 / 出站 / 异常）均有 `logger` 输出，便于排查。

## 安装

1. 在 AstrBot「插件市场」安装本插件，或把本仓库放入 AstrBot 的 `data/plugins/` 目录；
2. 重启 AstrBot，确保依赖 `python-socketio` 与 `aiohttp` 已安装（见 `requirements.txt`）；
3. 在插件配置中设置 `listen_port`（默认 `6199`）与可选的 `access_key`。

## 配置（`_conf_schema.json`）

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| `listen_host` | Socket.io 服务端监听地址 | `0.0.0.0` |
| `listen_port` | Socket.io 服务端监听端口 | `6199` |
| `access_key` | 客户端接入所需访问密钥（留空不校验） | `""` |
| `astrbot_base_url` | AstrBot 自身 HTTP 服务地址（用于拉取机器人列表） | `http://127.0.0.1:6185` |
| `astrbot_api_key` | AstrBot OpenAPI / Dashboard 的 API Key | `""` |

## Socket.io 事件

客户端 → 服务端：

| 事件 | 说明 |
| --- | --- |
| `ping` | 心跳，返回 `{pong: true}` |
| `config` | 返回服务端能力 / 默认配置 |
| `bots:list` | 机器人（Agent）列表 |
| `chat:send` | 发送消息（携带 `text` / `botId` / `sessionId` / `reqId`） |

服务端 → 客户端：`chat:session` / `chat:delta` / `chat:done` / `chat:error`。

## 指令

- `/plus`：在聊天中查看插件状态。

## 许可

见 [LICENSE](./LICENSE)。
