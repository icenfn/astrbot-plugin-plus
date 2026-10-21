## v0.5.0

### 新增
- 🧮 **`bots:list` 附带 `historyCount`**：返回每个机器人服务端已保存的历史消息条数。客户端据此判断该会话是否应当出现在消息列表 —— 服务端本来就有历史往来的会话会自动显示，没有往来的则等用户从「联系人 → Agent 信息 → 发消息」主动开启。

### 变更
- 🗂️ **版本号收敛为单一来源**：`__version__` 不再写死在 `main.py`，改为运行时从 `metadata.yaml` 的 `version:` 读取（`_read_plugin_version()`）。以前版本号在 `main.py` 与 `metadata.yaml` 各写一遍，发版时经常漏改其中一处，导致 WebUI 显示的版本与实际行为不符；现在只有 `metadata.yaml` 是权威来源。解析用轻量行扫描，不依赖 PyYAML；读取失败会记录警告并回退 `0.0.0`，不会阻塞插件加载。
- 🧹 **`bots:list` 精简字段**：移除 `platform` 与 `enabled`（客户端 Agent 详情页已不再展示平台适配器与启用状态），只下发 `id` / `name` / `botname` / `botdf`。

### 说明
- 🔖 版本号提升至 `0.5.0`，配合客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.4.26`。

## v0.4.9

### 移除
- 🗑️ **删除「机器人管理」可视化视图**：移除 `views/bot-manager/`（HTML/CSS/JS）以及其在 `main.py` 中注册的全部后端 API（`bots` / `bots/create` / `bots/toggle` / `bots/delete`），机器人创建统一回到 AstrBot 原生「机器人 → 创建机器人」页面，避免两套入口配置不一致。

### 新增
- 🗂️ **机器人列表新增 `botdf` 指纹（128 位 hash）**：由 `md5("astrbot_plus:<botId>")` 生成 32 位十六进制字符串（即 128 bit），在适配器**创建 / 首次实例化**时生成，确定性算法保证重启、重载后指纹不变，客户端可据此长期识别同一机器人。
- 🔌 **新增 `chat:delete` 事件**：客户端长按删除会话时调用，清空该会话的服务端内存历史与流式缓冲，并向所有客户端广播 `chat:deleted`，实现多端会话同步移除。

### 变更
- 🏷️ **适配器配置字段 `nickname` → `botname`**：`default_config_tmpl` 改为 `botname`，`bots:list` 输出 `botname` 与 `botdf`；旧配置中的 `nickname` / `name` / `bot_name` 仍兼容读取，已有机器人无需迁移。

### 说明
- 🔖 版本号提升至 `0.4.9`（`main.py` 的 `__version__` 与 `metadata.yaml` 同步），配合客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.4.25`。

# Changelog

## v0.4.7

### 变更
- 🧹 **清理无用代码**：删除从未被引用的 `get_adapter()` / `available_platform_ids()` 辅助函数，以及误拷入的 `MessageSession` / `MessageSesion`（后者为拼写错误的兼容占位）模块级占位，精简 `plus_platform.py`。
- 🔖 版本号提升至 `0.4.7`（`main.py` 的 `__version__`、`metadata.yaml` 与健康检查页的回退版本号同步）。

### 说明
- 🔗 配合客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.4.11`：客户端已移除对「思考内容 / 工具调用信息」标记的解析与占位气泡动画，本插件仅做冗余代码清理，协议与事件保持不变。

## v0.4.6

### 新增
- 🔄 在「机器人管理」视图中启用/停用/删除机器人后，向客户端广播 `bots:changed` 事件，使客户端会话列表实时刷新（无需重连或重启）。

## v0.4.5

### 新增
- 🖥️ **新增「机器人管理」可视化视图**：在 WebUI 插件详情页即可**快速创建 / 启用 / 停用 / 删除** AstrBot+ 机器人，无需再进入「机器人 → 创建机器人」逐项手动配置。遵循 AstrBot ≥4.18 可视化视图规范（`views/<view_name>/index.html` + `context.register_web_api()`）；后端 API：`GET /astrbot_plugin_plus/bots`、`POST /astrbot_plugin_plus/bots/create | toggle | delete`，创建/启停会同步写入全局配置并即时加载平台适配器，删除会终止对应实例。

### 修复
- 📨 **修复偶发漏掉 AI 回复**：开启「输出工具调用信息 / 显示思考内容」后，一个回合内 AstrBot 会多次下发消息（工具状态、思考内容、最终回答），旧实现每次下发都立即标记结束，导致同回合后续内容被丢弃。现按会话在短暂静默（0.4s）后聚合本回合文本、统一收尾，杜绝漏消息；未开启相关配置时行为与旧版一致（不改动正常路径）。

### 说明
- 🔖 版本号提升至 `0.4.5`（`main.py` 的 `__version__` 同步）。

## v0.4.3

### 新增 / 修复
- 📨 **新增 `message:new` 实时广播**：用户与机器人的每条消息都会记录并广播给所有客户端，
  使客户端消息列表可实时显示红点、更新最新内容副标题并弹出系统通知。
- 🕘 **新增 `chat:history` 与消息持久化**：插件按会话持久化历史消息（重启后保留），
  客户端打开会话时可拉取历史，修复此前「不同步历史消息」的问题。
- 💬 **兼容机器人未开启流式输出**：`chat:done` 现统一携带完整回复文本（无论是否流式），
  客户端在无 delta 的情况下也能拿到完整内容。
- 🔌 **连接保活**：Socket.io 服务端配置 `ping_interval=25` / `ping_timeout=60`，
  降低移动网络下误断开；客户端侧配合无限自动重连。
- 🏷️ **平台配置新增「昵称」字段（nickname）**：创建机器人时可填写昵称，未填默认与
  botid 一致；`bots:list` 返回解析后的显示名与昵称，供客户端消息列表展示。

## v0.4.2

### 修复

- 🔁 **修复插件重载 / 重装后「不识别」之前创建的 AstrBot+ 机器人**：插件重载或重装时，
  AstrBot 只重载插件代码而不重新实例化已有的平台适配器——旧实例仍是旧模块的类对象，
  其模块级适配器注册表与新插件模块完全隔离，导致新插件看不到既有机器人（客户端 Agent
  列表为空、消息无法派发）。插件现于启动时自动检测陈旧 / 缺失的 `astrbot_plus` 适配器
  实例，并通过 AstrBot 平台管理器重新实例化，重载 / 重装后机器人即刻恢复可用。

## v0.4.1

### 修复

- 🖼️ **修复 WebUI「创建机器人」平台选择器中插件 logo 显示 404**：AstrBot 核心的
  「文件令牌」机制存在已知缺陷（上游 #10294，2026-10-02 才修复）——旧版本中
  `/api/v1/files/tokens/<token>` 的令牌是「一次性」的，logo 首次加载后即被销毁、
  永久 404；且令牌仅 1 小时有效期、存于内存，过期或服务重启后同样失效。插件现
  内置兼容处理：为本插件 logo 的令牌关闭一次性消费，并后台定期续期，新旧版本
  AstrBot 上 logo 均可稳定显示。建议同时升级 AstrBot 到包含 #10294 的版本以获得
  系统性修复。

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
