# 公开站点发布核对

- 网址：<https://oceanbase-evidence-desk-g99bxzw9gge.qoder.website>。
- siteId：`01a19b32-f2ff-7a98-881c-32ccfd725d3a`。
- projectId：`01a19b32-f2fd-754d-ba23-ccdcaa0a5d3d`。
- 正常 Qoder 桌面会话：`da8b0549-6292-4e01-9baf-d41f32ee27c8`。
- 日期：2026-11-02。

首次发布复用已有站点，随后两次更新截图和说明，均未新建站点。最终更新
actionId 为 `1d7a9c4e-5b2f-4e83-9a61-7c0d2f8b4e15`；Qoder 内置 Sites
工具核对结果为 `published: true`、`committed: true`、
`readiness: publication_completed`，访问策略仍为 `public`。

调用方另以不带 Cookie 的独立 HTTP 请求读取网址，实际取得包含项目标题、
`screenshots/demo.jpg` 及最终修正说明的 HTML。截图路径返回
`200 OK`、`Content-Type: image/jpeg`。这些是匿名 HTTP 检查，浏览器无痕检查
仍待浏览器控制工具恢复后执行。

截图为真实 Qoder 原生预览与对话，不作图像修改；只含本项目内容。公开页面的
交互演示使用浏览器内存样例；真实 seekdb 后端按 DEPLOY.md/README.md 在本地
复现。没有宣称真实用户使用规模或提效数据。

账号 UID 的独立核对见 [ACCOUNT.md](ACCOUNT.md)。正式比赛提交及评分仍待执行，
发布状态不代表已获得官方20分。
