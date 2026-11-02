# 调用方独立验证

日期：2026-11-02。Qoder CLI 1.1.81，会话
`c3059891-7272-48d9-b8a8-48db4f197890`；Node v24.18.0。

项目主要源码、测试与修复由 Qoder 生成。调用方另加 Docker 复现配置/说明、
真实操作记录导出和此验证记录，并修复新 HTTP 测试中的响应解码：使用 UTF-8
而非 latin1，保持“响应包含中文展示页内容”的原断言。

| 验证 | 退出码 | 结果 |
|---|---:|---|
| 修改前 core+API 集成测试 | 0 | 20 passed |
| 修改前数据库故障测试 | 0 | 2 passed，观察到未清理的 30 秒看门狗 |
| 修改前 `npm test` | 1 | 测试目录入口在 Node24 下 MODULE_NOT_FOUND |
| 独立 malformed-Host worker（修改前） | 1 | ERR_INVALID_URL，worker 退出1 |
| 新完整测试首次运行 | 1 | 33 passed，1 中文响应因测试 latin1 解码失败 |
| 修正解码后 `npm test`，原本地 seekdb | 0 | 34 passed |
| `DB_PORT=2936 npm test`，全新官方镜像实例 | 0 | 34 passed，原始日志 test-stock-seekdb.log |
| 独立 malformed-Host worker（修改后） | 0 | HTTP/1.1 200 OK，进程存活 |
| `npm run seed`，本地开发库 | 0 | 导入8条公开主题样例 |

独立官方镜像实例由本目录 Compose 以唯一项目名启动，2936 端口，cpu_count=2，
memory_limit=2G，数据/日志各2G；验证后停止其容器，保留命名卷。没有重启已有服务。
镜像 digest 已固定在 docker-compose.yml，实际数据库版本为 seekdb 1.4.0.0。

回归涵盖 206 个匹配候选中的后页最佳条目、异常 Host 后的继续服务、
快速关闭/切换阅读进度、同笔记串行保存、真实数据库重启持久化、SQL 输入、
数据库故障显式报错。队列测试是代码级行为验证，浏览器端完整体验和截图仍待
浏览器工具恢复后完成。公开站点、账号 UID 和官方20分验收尚未据此宣称完成。

## 后续公开站点与材料核对

2026-11-02 15:53 起，由正常 Qoder 桌面会话
`da8b0549-6292-4e01-9baf-d41f32ee27c8` 内置 Sites 工具发布既有站点：
<https://oceanbase-evidence-desk-g99bxzw9gge.qoder.website>。
Sites 返回 `published: true`、`readiness: publication_completed`、
`hasPublishedContent: true`，访问策略为 `public`。

调用方独立以不带 Cookie 的 HTTP 请求取得项目 HTML；截图路径也返回
`200`、`image/jpeg`。这证明当时匿名 HTTP 可访问；没有将它写成浏览器无痕检查。
Qoder 内置预览的「注意力」查询显示两张证据卡、来源链接与匹配片段，
实际截图保存在 `public/screenshots/demo.jpg`。该演示使用浏览器内存样例，
不作为数据库后端浏览器端到端测试或真实用户效果证据。

真实的发布对话截图见 `screenshots/qoder-sites-publication.jpg`；
账号 UID 已从设置→个人资料→编辑的只读账号 ID 字段核对，见
[ACCOUNT.md](ACCOUNT.md)。未保存邮箱或凭据。

首轮 Qoder 开发在停止冒烟服务时执行了范围过宽的按进程名终止，导致浏览器
控制工具断开；已向用户说明，后续开发恢复时完全禁用该会话的 Bash 工具。
本记录与导出的操作证据保留真实经过。官方 Qoder 赛题20分仍待正式提交和评测。
