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
