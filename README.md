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

## 指令

- `/plus`：聊天中查看插件状态。

## 许可

见 [LICENSE](./LICENSE)。
