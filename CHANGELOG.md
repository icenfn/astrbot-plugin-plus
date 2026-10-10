## 0.3.1

### 新增

- 🧩 **注册为 AstrBot 消息平台适配器**：新增 `plus_platform.py`，通过 `@register_platform_adapter("astrbot_plus", ...)` 注册。现在可在 AstrBot WebUI「机器人 → 创建机器人」中选择 **AstrBot+** 平台，创建出的机器人拥有独立的 Provider / 人格与会话上下文。
- 🔀 **事件桥接**：客户端经 Socket.io 发来的消息会被构造成 AstrBot 事件投入对话管线（`chat:send` → 事件 → 流式回复），回复再由 `send` / `send_streaming` 经 Socket.io 回推；未绑定适配器机器人时自动回退到原有 Webchat 直连逻辑。
- 🖼️ **平台图标**：新增 `logo.png` 作为适配器图标。

### 变更

- 🔖 `metadata.yaml` 描述更新为「注册 astrbot_plus 消息平台」。
- 🔗 **版本对齐**：插件版本 `0.3.1`，与配套客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.3.x` 大版本保持一致。

### 说明

- ⚠️ 使用「创建机器人」中的 AstrBot+ 平台前，请先在插件配置中设置 `access_key`，并在客户端「设置」中填入同一密钥与插件地址。

## 0.3.0

### 新增

- 🔌 **内置 Socket.io 服务端**：插件单独监听一个端口（默认 `6199`），对外只暴露这一个端口；客户端通过 `ws://<host>:<port>` 直接连接，无需再走 AstrBot 自身的 HTTP 接口。
- 🔐 **API Key 鉴权**：握手阶段校验 `auth.token` 与配置项 `access_key`，不一致直接拒绝连接；留空表示不校验。
- 🤖 **机器人列表桥接**：`bots:list` 事件代理 AstrBot 的机器人配置接口，返回 WebUI「创建机器人」页面的机器人列表。
- 💬 **对话（Webchat 会话）管理**：提供 `dialogs:list` / `dialogs:create` / `dialogs:delete` / `dialogs:history` 事件，支持列出、新建、删除对话并拉取历史。
- 🌊 **流式聊天转发**：`chat:send` 事件由服务端请求 AstrBot 的 SSE 接口，解析后以 `chat:delta` / `chat:done` / `chat:error` 增量回推给客户端。
- 📇 **注册表事件**：提供 `registry:list` 及用户 / 群聊的增删事件，供客户端同步 AI 好友与群聊。

### 变更

- 🧩 **配置项调整**：新增 `access_key`、`listen_host`、`listen_port`、`astrbot_base_url`、`astrbot_api_key`、`enable_group_fanout`、`max_ai_per_group`。
- 📦 **依赖声明**：`requirements.txt` 显式声明 `python-socketio` 与 `aiohttp`。
- ♻️ **保留原有 Web API**：`/users`、`/groups` 等 HTTP 接口继续保留，便于兼容与调试。

### 说明

- 🔖 版本号提升至 `0.3.0`，与配套客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.3.0` 保持一致。
- ⚠️ **升级提示**：需确保 AstrBot 环境可安装 `python-socketio`；安装依赖并重启后，Socket.io 服务端会在插件加载时自动启动。
# Changelog

所有值得注意的变更都会记录在此文件中。

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范，
版本号与配套客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) 保持一致。

## 0.2.1

- 🔖 版本号与配套客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) 同步提升至 `0.2.1`。
- 🤝 与 astrbot-plus v0.2.1 的发布保持一致（Android 仅 arm64、Linux 仅 arm64 构建调整不涉及本插件逻辑）。

## 0.2.0

- 🎉 首次发布，作为 AstrBot+ v0.2.0 的配套插件。
- 🧑‍🤝‍🧑 支持管理 AI 用户（好友）：创建 / 列出 / 删除，可绑定 provider 与 persona。
- 👥 支持管理群聊：把多个 AI 用户拉进同一个群聊，发送时扇出给每位成员。
- 🔒 群聊中每位 AI 成员使用独立会话上下文（独立 UMO）。
- 🔌 提供插件 Web API：`/users`、`/groups`、`/health`。
- 💬 提供 `/plus` 指令。
- 🎨 Logo 沿用 astrbot-plus 品牌图标，版本号与 astrbot-plus 保持一致。
