## 未发布（main 分支）

> 版本号保持 `0.9.0` 不变，本次仅提交代码改造，不创建新 tag / release。

### 新增
- 🏷️ **智能体备注**：新建 / 编辑智能体新增「备注」字段（限 64 字符），管理列表在「显示名」后展示备注列，过长自动省略；对应适配器配置模板同步加入 `remark` 项，平台配置页可直接查看与修改。
- ⏳ **登录 token 有效期配置**：新增 `token_expire_days` 配置项，默认 `30` 天，填 `0` 表示永久有效（此时签发不写入 `exp` 声明）。插件启动时读取并注入 Socket 服务端。
- 🌐 **快连链接使用真实对外地址**：`ap://` 快连链接的主机名改为按优先级自动解析 —— 优先读取 AstrBot 系统设置「对外可达的回调接口地址」(`callback_api_base`)，其次使用新增的 `quick_link_host` 配置项，再退化为代码探测的本机对外 IPv4（UDP 出口地址，不发起外部请求），随后回落到 `listen_host`，最后兜底 `localhost`。此前固定回落到 `localhost`，导致异机客户端无法直接使用快连链接。
- 📋 **编辑用户可查看指纹**：编辑用户弹窗新增只读的「指纹」字段，便于核对客户端身份；服务端更新接口不接受该字段，无法被篡改。

### 变更
- 🔐 **「可用智能体」白名单语义调整**：由原来的「空列表 = 允许全部」改为「只有列表内的 agent 可用，空列表 = 没有任何可用 agent」。管理端新建用户时默认全选现有智能体，取消勾选即代表该用户无可用智能体；客户端 `/api/agents` 与消息路由均按新语义过滤。
- 🖥️ **配置项文案调整**：「Socket.io 服务端监听地址、端口」改为「IM 服务端地址、端口」，与当前 IM 服务端定位一致。
- 🚫 **可用智能体为空不再显示「全部」**：用户列表的「可用智能体」列在该用户没有任何可用智能体时显示 `—`，与白名单语义保持一致。

### 优化
- 🪟 **编辑弹窗铺满整页**：抽屉面板由右侧 `min(440px, 100%)` 固定宽度改为 `inset: 0` 全屏，内容区限宽 720px 居中以便长表单阅读；手机端不再自底部升起，统一满屏展示。

### 重构
- 🎨 **管理界面重写并迁移到官方插件 Pages 规范**：旧视图放在 `views/plus-admin/`，不是 AstrBot 插件页面规范目录，WebUI 无法识别。现按官方规范改为 `pages/admin/`（`index.html` + `style.css` + `app.js`），旧目录整体移除。界面重做为左侧导航（概览 / 用户 / 智能体）+ 右侧内容区 + 抽屉式表单，支持明暗主题与窄屏自适应。
- 🌓 **主题跟随 WebUI**：读取 `bridge.getContext().isDark` 同步 `<html data-theme>`，并 `onContext()` 订阅主题切换，始终与 Dashboard 明暗模式一致。页面文案直接内置于模板，不引入 i18n 资源文件。

### 修复
- 🐛 **「加载失败 Failed to fetch」**：根因是插件页面运行在 WebUI 的受限 iframe（opaque origin）中，页面里用裸 `fetch` 请求相对地址会被拦截。现统一改走 `window.AstrBotPluginPage` 桥接的 `apiGet` / `apiPost`，由 Dashboard 转发并注入鉴权，不再有跨域失败。
- 🐛 **管理 API 方法与桥接能力不兼容**：桥接只提供 GET / POST，旧后端使用 `PATCH` / `DELETE` 且把目标对象放在路径参数里，前端根本发不出来。现九条路由全部改为 **GET（查询）+ POST（写入）**，写操作目标由请求体 `id` 指定，不再使用路径参数。
- 🐛 **桥接 endpoint 前缀缺失**：后端注册路径是 `/<plugin_name>/admin/**`，桥接会自动补插件名前缀，因此页面侧 endpoint 必须保留 `admin/` 段（如 `admin/users`），否则转发后 404。
- 🐛 **原生 `alert` / `confirm` 在沙箱内被拦截**：改为自绘确认框与 Toast 提示，删除等破坏性操作可正常二次确认。

### 优化
- 📱 **页面 UI 响应式重做**：新增 1080px / 860px / 560px / 380px 四档断点。平板收窄侧栏并让四指标变两列；窄屏时导航转为顶部横向条（可横向滑动）；手机端表格改为横向滚动而非压缩裁切，抽屉改为自底部升起、操作按钮占满宽度；超窄屏导航仅保留图标，指标改单列。
- 📐 **视口适配修正**：使用 `100dvh` 替代 `100vh`，避免移动端地址栏伸缩导致导航被截断；补充 `-webkit-text-size-adjust` 防止横竖屏切换时字号被放大。

### 变更
- 🗂️ **`metadata.yaml` 改用 PyYAML 解析**：`_read_plugin_version()` 的行扫描（`line.startswith("version:")`）替换为 `yaml.safe_load()` 的 `_read_metadata()`，正确支持引号、多行与嵌套结构；解析失败只记 debug 日志并回退，不阻塞插件加载。`metadata.yaml` 仍是版本号唯一来源。
- 🧩 **管理端逻辑收敛与加固**：抽出 `_hash_password` / `_agent_configs` / `_load_platform` / `_terminate_platform` / `_sync_platform` 等辅助方法，统一 Agent 启停与热加载路径；补齐邮箱格式、邮箱唯一性（409）、密码长度（≥6）、用户名非空、`allowed_agents` 数组类型等输入校验；列表接口永不回传 `password_hash`。
- 🧪 **新增管理 API 自测**：`tests/admin_api_test.py` 覆盖九条路由的注册前缀/方法约束与创建、更新、删除、异常分支（共 51 项断言），不依赖真实 AstrBot 与外部端口。

## v0.9.0

### 重构
- 🏗️ **整体重构为「python-socketio + HTTP API」混合架构的 IM 服务端**：IM 端口（默认 `6199`）只提供 `/api/*`（REST + JWT）、`/socket.io`（实时双向）与 `/health`，插件可视化视图仅通过 AstrBot WebUI 访问，不从 IM 端口暴露，也不需要插件自身登录或管理员账号。

### 新增
- 👤 **用户管理**：新建用户（邮箱 / 用户名 / 密码，bcrypt 加盐哈希存储）、编辑资料（邮箱唯一性校验、用户名、重置密码）、禁用 / 启用、删除、查看 token 用量、设置用户可用 Agent 白名单（空列表 = 允许全部）。
- 🔗 **快连链接（一次可见）**：新建用户成功后返回 `ap://email:password@host:port` 形式的快连链接，界面提示「只显示一次、请长按复制保存」，客户端粘贴即可免手动录入完成登录。
- 🤖 **Agent 管理**：基于 AstrBot 平台配置创建 / 重命名 / 停用启用 / 删除 `astrbot_plus` 适配器，变更后即时热加载与摘除。
- 💬 **用户私聊与消息漫游**：用户之间可发起私聊，消息落 SQLite（`messages` 表按会话归属方各存一行），支持历史漫游；不同用户与 Agent 的会话通过 `u{uid}:a{bot}` 会话键严格隔离。
- 🔐 **JWT 鉴权**：`POST /api/auth/login` 登录成功下发一次 Token，后续 HTTP API 通过 `Authorization: Bearer` 携带，Socket.IO 握手用同一 Token 鉴权，无效 token 直接拒绝连接。
- 📊 **总览统计**：注册用户数、Agent 总数、今日消息数、在线连接数。

### 修复
- 🐛 **管理 API 注册签名错误**：原先以 `register_web_api(method=..., path=..., handler=...)` 关键字方式调用，与 AstrBot 实际签名 `register_web_api(route, view_handler, methods, desc)` 不符，导致 WebUI 管理接口从未注册成功、用户与 Agent 管理完全不可用。现按真实签名注册，并保留旧版关键字签名兜底。
- 🐛 **路径参数路由不匹配**：路由占位符由 `{id}` 改为 AstrBot 识别的 `<id>`，否则 `PATCH` / `DELETE` 必然匹配失败。
- 🐛 **请求体与错误响应**：`await request.json` 缺少调用括号、`error_response(message=, status=)` 参数名错误，均已修正为 `await request.json()` 与 `error_response(msg, status_code=...)`。
- 🐛 **编辑资料字段缺失**：`PATCH /users/<id>` 补齐邮箱修改（含唯一性冲突 409）与密码重置（bcrypt 重算哈希，最短 6 位校验）。

### 说明
- 🔖 版本号提升至 `0.9.0`，与客户端 [astrbot-plus](https://github.com/icenfn/astrbot-plus) `0.9.0` 配套。

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
