# Changelog

## v0.4.0

### 变更

- 🏥 **健康检查页**：浏览器直接访问插件端口（如 `http://<host>:6199`）不再返回 `404: Not Found`，
  而是展示一张服务端状态页（运行状态 / 连接地址 / 已加载 Agent 列表），便于确认插件存活。
- 🔓 **移除访问密钥**：简化连接体验，客户端连接插件不再需要 `access_key`；
  平台适配器的默认配置模板同步移除该字段。请确保端口仅在内网 / 可信网络中暴露。
- 🧩 **机器人列表改走内部通道**：`bots:list` 现在直接读取本进程内已实例化的
  `astrbot_plus` 平台适配器，不再回调 AstrBot HTTP 服务。
- ⚙️ **移除 AstrBot OpenAPI 配置项**：插件与 AstrBot 同进程运行，无需再配置
  `astrbot_base_url` / `astrbot_api_key`。配置项精简为 `listen_host` / `listen_port`。

### 说明

- 🔖 版本号提升至 `0.4.0`；配合客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.4.x`。

## v0.3.2

### 变更

- 🧹 **精简平台能力**：移除群聊（group）与 Webchat 相关代码，插件现在只服务 **Agent**（WebUI「创建机器人」页面创建的机器人）。Socket.io 事件仅保留 `ping` / `config` / `bots:list` / `chat:send`。
- 🤖 **术语统一**：原先的「AI 好友」表述统一为 **Agent**，与客户端一致。
- 🪵 **完善日志**：按 AstrBot 插件开发规范补充/规范 `logger` 输出（连接、鉴权、断开、请求派发、出站推送、异常等关键路径），便于排查问题。
- 🧱 **依赖声明**：明确声明 `python-socketio` / `aiohttp` 依赖，缺失时给出清晰告警而非静默失败。

### 修复

- 🔧 出站回调切回 Socket.io 事件循环时增加运行态校验，避免事件循环未就绪导致的调度异常。

### 说明

- 🔖 版本号提升至 `0.3.2`；配合客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.3.x`。

## v0.3.1

### 新增

- 🔌 **注册平台适配器 `astrbot_plus`**：插件通过 `@register_platform_adapter` 把 AstrBot+ 客户端注册为 AstrBot 的一个消息平台，使其出现在 WebUI「机器人 → 创建机器人」的平台选择列表中。
- 🧩 新增 `plus_platform.py`：实现 `AstrBotPlusAdapter` / `AstrBotPlusEvent`，将客户端消息投入 AstrBot 事件管线，并把回复经 Socket.io 回推。

### 说明

- 🔖 版本号提升至 `0.3.1`。

## v0.3.0

### 新增

- 🌐 内置 Socket.io 服务端（单端口，默认 `6199`），客户端所有交互改为事件驱动。
- 🔐 支持访问密钥（API key）鉴权。

### 说明

- 🔖 版本号提升至 `0.3.0`；配合客户端 `0.3.0`。
