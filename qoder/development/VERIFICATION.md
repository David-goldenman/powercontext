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
数据库故障显式报错。当时队列测试是代码级行为验证；后续浏览器验收见下方补充。
官方评分以赛题平台记录为准，本地测试不等同于官方20分。

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
本记录与导出的操作证据保留真实经过。

## 真实浏览器补充验收与状态反馈修复

2026-11-02，调用方 Codex 从正式提交的 `5bf94e3b` 导出干净源码，
在真实 seekdb 1.4、独立合成笔记库中操作浏览器，确认跨进程恢复阅读进度，
同时发现两个显示错误：SQL 已读/100 时旧搜索卡仍显示阅读中/20，
运行中断库后 API 报错但顶栏仍绿色已连接。

Codex 补充了 10 项 DOM 回归并修复前端反馈（代码提交 `a5a74dbf`）：
HTTP 500/网络失败更新连接提示，成功重试恢复提示，过期健康响应不覆盖新请求，
4xx 校验不误报断库；保存成功后更新相应卡片并刷新当前状态筛选，忽略过期列表响应。
运行依赖仍仅 mysql2；linkedom 0.18.15 只作为锁定的测试开发依赖。
本次没有新 Qoder 会话，不将 Codex 的补充修改冒记为 Qoder 生成。

原 34 项测试保持不变。新增测试在修复前出现真实断言失败，修复后完整套件
**44 passed、0 failed、0 skipped**。修复版真实浏览器还确认：
`ftp://x` 被拒、HTTPS 导入成功、真实来源搜索与高亮、滚动至10%后关闭及
重启服务仍恢复10%、标记已读后原卡变为已读/100%且移出阅读中筛选，
受控断库后提示不可用、恢复数据库直接重试后恢复已连接和真实结果。
两条合成笔记最终 SQL 均为 `read / 100`。

命令、失败/成功日志及未编辑的截图见 [ui-state/REPORT.md](ui-state/REPORT.md)。
所有笔记均明确标为合成 QA 内容，不作为真实用户效果证据。
浏览器测试只操作本任务的本地服务和独立容器，不终止其他进程或重置数据库卷。

公开站点仍为已有的静态样例展示；用户另确认 Edge InPrivate 可访问并检索出两张卡。
18:39 已提交的 `5bf94e3b` 官方任务在本次补充验收时仍待执行；上述本地通过
不冒充官方评测通过，也不表示本次修复已重新发布到 Qoder Sites。
