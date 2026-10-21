# AstrBot+ 配套插件（astrbot-plugin-plus）

把 [AstrBot+](https://github.com/icenfn/astrbot-plus) 客户端接入 AstrBot 的配套插件：

- 🔌 注册平台适配器 `astrbot_plus`，出现在 WebUI「机器人 → 创建机器人」平台列表；
- 🌐 内置 Socket.io 服务端（单端口，默认 `6199`），客户端经它拉取 **Agent 列表**并进行**流式对话**；
- 🏥 浏览器访问 `http://<host>:6199` 显示服务状态页（便于确认插件存活）。

## 安装

1. 在 AstrBot「插件市场」安装本插件，或将本仓库放入 `data/plugins/` 后重启；
2. 客户端「设置 → 连接」填入 `http://<服务器IP>:6199` 即可（无需密钥）。

## 配置

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| `listen_host` | Socket.io 监听地址 | `0.0.0.0` |
| `listen_port` | Socket.io 监听端口 | `6199` |

## Socket.io 事件

客户端 → 服务端：`ping` / `config` / `bots:list` / `chat:send`；
服务端 → 客户端：`chat:session` / `chat:delta` / `chat:done` / `chat:error`。

## 管理界面（插件 Pages）

管理界面遵循 AstrBot 插件 Pages 规范，放在 `pages/admin/`，**只通过 AstrBot WebUI 访问**，不暴露在 IM 端口上。

打开方式：WebUI → 插件 → 本插件详情页 → Pages → “AstrBot+ 控制台”。

| 页面 | 能力 |
| --- | --- |
| 概览 | 注册用户数、智能体总数、今日消息数、在线连接数 |
| 用户 | 新建（邮箱 / 用户名 / 密码）、编辑资料与重置密码、启用 / 禁用、删除、设置可用智能体白名单、查看 Token 用量 |
| 智能体 | 新建 / 重命名 / 启用停用 / 删除 `astrbot_plus` 适配器，变更即时热加载 |

新建用户成功后会显示一次性快连链接 `ap://<邮箱>:<密码>@<主机>:<端口>`，可在客户端「快速连接」直接粘贴登录。

技术说明：

- 页面脚本通过 `window.AstrBotPluginPage` 桥接（`apiGet` / `apiPost`）访问后端，运行在 WebUI 的受限 iframe 中，**不使用裸 `fetch`**，也不读取 Dashboard 的 Cookie / LocalStorage；
- 后端 API 统一注册在 `/<插件名>/admin/*`，全部为 **GET（查询）+ POST（写入）**，写操作目标由请求体 `id` 指定；
- 主题跟随 WebUI（`data-theme`），文案直接内置于页面模板，不依赖 i18n 资源文件；
- 响应式适配桌面、平板与手机：窄屏下导航变顶部横向条，表格改为横向滚动，抽屉自底部升起。

## 指令

- `/plus`：聊天中查看插件状态。

## 许可

见 [LICENSE](./LICENSE)。
