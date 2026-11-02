# 科研证据卡 · Evidence Desk

面向阅读论文和课程资料的学生，解决三件高频痛点：**笔记丢了出处**、**同一概念反复重新检索**、**长文阅读进度一关就没**。

每条研究笔记（标题 + 来源 URL + 正文）存入真实的 **seekdb（OceanBase，MySQL 协议）** 数据库；检索返回**证据卡**——按匹配度排序、携带原始 URL 和命中关键词周围的真实原文片段；阅读器支持 **未读 / 阅读中 / 已读** 三态与 0–100% 进度，全部持久化在数据库中，重启服务后依然在。

- 技术栈：Node.js LTS（内置 http，无 Web 框架）+ mysql2 + 原生 HTML/CSS/JS；运行时仅依赖 mysql2，DOM 回归测试另用固定版本 linkedom 0.18.15（开发依赖）
- 诚实性设计：数据库不可用时 API 明确返回 500 错误，**绝不返回假数据或空壳成功**
- GitHub：<https://github.com/David-goldenman/powercontext/tree/2026-competition-ecosystem/qoder>
- 公开站点（Qoder Sites）：<https://oceanbase-evidence-desk-g99bxzw9gge.qoder.website>

从干净环境部署数据库的完整命令见 [DEPLOY.md](DEPLOY.md)。已在独立的官方
seekdb 1.4.0.0 容器中运行全套测试：34/34 通过，退出码 0；原始输出见
[development/test-stock-seekdb.log](development/test-stock-seekdb.log)。导出的开发操作记录见
[development/qoder-session.jsonl](development/qoder-session.jsonl)，已排除私有推理内容，不含凭据。

后续真实浏览器验收发现并修复了连接提示、保存后卡片/筛选状态不同步的问题。
原 34 项测试保持不变，新增 10 项 DOM 回归后 **44/44 通过**；
日志与真实数据库浏览器截图见 [development/ui-state/REPORT.md](development/ui-state/REPORT.md)。
这次补充修复由 Codex 完成，原 Qoder 开发记录仍单独保留。

## 目录结构

```
qoder/
├── src/
│   ├── config.js          # .env / 环境变量读取，默认 127.0.0.1:2896 root 空密码
│   ├── db.js              # seekdb 连接池；只创建/修改本项目专有库 qoder_evidence
│   └── server.js          # HTTP API + 静态页面服务
├── public/
│   ├── evidence-core.js   # 检索评分/片段提取核心（Node 与浏览器共用的唯一源文件）
│   ├── index.html         # 展示页（公开静态，可独立发布；内置模拟演示）
│   ├── demo.js / app.js / app.html / style.css
│   └── samples/sample-notes.json   # 8 篇公开主题的非私人样例笔记
├── scripts/seed.js        # 样例灌库脚本
├── tests/                 # node:test 测试套件（见下文）
├── README.md / DEV_LOG.md / .env.example
```

## 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Node.js | ≥ 20（开发实测 v24.18.0，npm 11.16.0） | 需支持 `node --test` 与全局 `fetch` |
| seekdb | MySQL 协议可达（开发实测 `5.7.25-OceanBase seekdb-v1.4.0.0`） | 本项目使用 WSL Ubuntu-22.04 中已运行的 seekdb2896 |
| npm 运行依赖 | `mysql2` | `npm ci` 按锁文件安装 |
| npm 开发依赖 | `linkedom` 0.18.15 | 仅 DOM 测试使用；生产部署可 `npm ci --omit=dev` |

> ⚠️ 默认连接参数 `127.0.0.1:2896 / root / 空密码` **仅适用于本地隔离开发**。数据库口令等配置一律从环境变量 / `.env` 读取，本项目代码只创建和修改专有库 `qoder_evidence`，不会触碰其他数据库或服务。

## 安装与启动

```bash
cd qoder
npm ci                      # 按锁文件安装运行及测试依赖
cp .env.example .env        # 默认值即可（Windows 可用 copy）
npm run seed                # 建库建表 + 导入 8 篇公开样例笔记
npm start                   # 默认 http://127.0.0.1:3210
```

启动成功会打印：`Evidence Desk 已启动: http://127.0.0.1:<端口> （数据库 127.0.0.1:2896/qoder_evidence）`。
**若数据库不可用，进程直接报错退出（退出码非 0），不会提供假服务。**

- 展示页：`http://127.0.0.1:3210/`（内置样例演示为浏览器内存模拟，不连数据库）
- 真实应用：`http://127.0.0.1:3210/app`（读写真实 seekdb）

## 数据库初始化细节

`npm run seed` 与服务启动都会执行 `initSchema()`：

1. 以 MySQL 协议连接 `DB_HOST:DB_PORT`；
2. `CREATE DATABASE IF NOT EXISTS qoder_evidence`（仅此库，库名经正则校验）；
3. `CREATE TABLE IF NOT EXISTS notes`（id CHAR(36) 主键、title、url、content、status、progress、created_at、updated_at，utf8mb4）。

## API 一览

| 方法与路径 | 功能 |
|---|---|
| `GET /api/health` | 返回真实数据库版本与库名；库不可用 → 500 |
| `POST /api/notes` | 导入笔记（校验标题/URL/正文，参数化 INSERT） |
| `GET /api/notes?status=&limit=` | 笔记列表（可按 unread/reading/read 过滤） |
| `GET /api/notes/:id` | 单条完整正文 |
| `PATCH /api/notes/:id` | 更新 status（三态枚举校验）与 progress（0–100 整数校验） |
| `DELETE /api/notes/:id` | 删除笔记 |
| `GET /api/search?q=` | 证据卡检索：以稳定 id 游标**分页扫描全部候选**（参数化 + LIKE 通配符转义）、逐页词频评分、保留全局得分前 50 张；响应含 `matchedCandidates / scanned / truncated / note` 计数与截断说明 |

所有 SQL 均使用占位符参数化（`?`），用户输入不拼接进 SQL。

## 测试

```bash
npm test           # 全部（需本机 seekdb 在线，集成测试真实读写 qoder_evidence）
npm run test:offline   # 不依赖数据库的 5 个文件（seekdb 离线时快速验证）
```

> 说明：`node --test tests/`（目录形式）在 Windows + Node v24.18.0 实测退出码 1、报 `MODULE_NOT_FOUND`（调用方实测），故 `test` 脚本改为跨平台、Node ≥ 20 有效的**显式文件列表**。

| 文件 | 内容 | 是否需要 seekdb |
|---|---|---|
| `tests/evidence-core.test.js` | 检索核心单元测试：分词/停用词、片段截取与省略号、标题加权排序、来源 URL 透传、大小写 | 否 |
| `tests/persist-queue.test.js` | 进度持久化队列回归：防抖合并最新快照、**flush 后旧定时器不得二次保存（快速关闭丢进度回归）**、A→B 切换按 id 归属不冒领、同一笔记并发保存串行不重入、失败回调携带正确 id | 否 |
| `tests/http-robustness.test.js` | 原始 socket HTTP 回归：**非法 `Host: [` 请求不得使进程崩溃**（ERR_INVALID_URL 回归），随后服务仍正常返回展示页；绝对形式请求行可路由 | 否 |
| `tests/db-failure.test.js` | 数据库故障诚实性：启动时死库 → 进程非零退出码；运行时死库 → 各 API 返回 500 且带 error 字段、不得出现 ok:true 或空 cards 假正常 | 否（故意连死端口） |
| `tests/ui-state.test.js` | 在真实 HTML/应用脚本中控制 HTTP 边界：失败/恢复连接提示、延迟健康响应、4xx 校验、保存后卡片与状态筛选、过期列表响应、保存失败保持原状态（10 项） | 否 |
| `tests/api-integration.test.js` | **真实数据库集成测试**：health 报真实版本、导入→列表、检索证据卡含真实 URL 与命中片段、非法输入 400/404、SQL 注入串作为字面量且表结构完好、LIKE 通配符不泄漏全库、**206 条候选时游标分页扫描全部页、位于后页的最佳条目排名第一且返回 matchedCandidates/scanned/truncated 计数（回归）**、limit 参数 1.5/Infinity/-3 等钳制为整数、**更新进度后杀掉服务进程重新拉起仍保留（真实重启）**、状态过滤、静态页内容与穿越防护、清理测试数据 | 是 |

说明：集成测试会自行 spawn / 终止**它自己创建的** `src/server.js` 子进程（用于验证真实重启持久化），不会操作其他任何进程或服务。测试只读写 `qoder_evidence`，并在结束时删除自己创建的笔记。

## 完整验收步骤（人工）

1. `npm run seed` → 输出 `已导入 8 条样例笔记…`，退出码 0。
2. `npm start` → 打开 `http://127.0.0.1:3210/app`，顶栏出现绿色「seekdb 已连接 · qoder_evidence」。
3. 「检索证据卡」输入 `注意力` → 返回多张证据卡，每张含标题、**可点击的原始 URL**、命中词高亮的原文片段、状态与进度条。
4. 点击一张卡打开阅读器，滚动到底部附近 → 状态自动变为「阅读中」、进度上涨，右下角出现「进度已存入数据库 ✓」。
5. 关闭服务（Ctrl+C）再 `npm start`，重新打开该笔记 → **状态与进度仍为刚才的数值**（数据库持久化）。
6. 「导入笔记」提交一条 URL 为 `ftp://x` 的笔记 → 前端显示 400 校验错误；提交正常笔记 → 「已写入 seekdb ✓」。
7. 「笔记库」按「阅读中」过滤，打开其中一条、标记已读并关闭 → 该条移出阅读中列表；已有搜索卡片也同步为已读、100%。
8. 停掉 seekdb（或将 `.env` 的 DB_PORT 改为 1）后 `npm start` → 进程报错退出；若只改运行期故障，页面顶栏变红，操作提示明确错误，**无任何假数据出现**。
9. 打开 `http://127.0.0.1:3210/` 展示页 → 内置演示可用且页面明确标注「模拟环境、未连接数据库」。
10. 恢复 seekdb 后直接重试检索 → 顶栏恢复绿色已连接，并返回真实卡片。`npm test` → 六个测试文件全部通过。

## Qoder Sites 发布与证据

- 公开站点（2026-11-02 经 Qoder Sites 发布并验证）：<https://oceanbase-evidence-desk-g99bxzw9gge.qoder.website>
  - 发布状态：`published: true`、`readiness: publication_completed`、`hasPublishedContent: true`；访问策略为 `public`，无需登录。
  - siteId：`01a19b32-f2ff-7a98-881c-32ccfd725d3a`。
  - 账号 UID：`01a19af2-af95-7eb5-9c89-3a45c46b4bc3`。2026-11-02 在 Qoder 桌面端「设置 → 个人资料 → 编辑」的只读「账号 ID」字段核对，详见 [development/ACCOUNT.md](development/ACCOUNT.md)。
- 实际预览截图（未编辑）：[public/screenshots/demo.jpg](public/screenshots/demo.jpg)。它展示的是展示页内置的**浏览器内存样例演示**，**不是 live 数据库，也不是真实用户证据**。
- 发布对话截图：[development/screenshots/qoder-sites-publication.jpg](development/screenshots/qoder-sites-publication.jpg)。
- 发布物是 `public/` 静态目录：展示页含简介、痛点、核心功能、**内置公开样例的可交互演示**、已发布站点截图、GitHub 链接与本地运行说明。
- 展示页的演示只在本机浏览器内存中运行检索逻辑（与真实后端共用同一 `evidence-core.js`），**公网静态页面并未连接任何数据库**；页面顶部有醒目提示。真实存储、进度持久化需按上文在本地运行后端 + seekdb。

## 已知限制

1. 检索为「SQL LIKE 初筛 + 应用层词频评分」，未使用 seekdb 全文索引；中文按用户输入的整词/短语匹配，无分词器，长度不足 2 个字符的词不参与检索。
2. 演示样例数据为项目自行撰写的公开主题摘要，非真实论文原文全文。
3. 无账号体系与多用户隔离，进度全局单库共享；仅面向本地单人使用。
4. `npm run seed` 重复执行会追加重复行（无幂等去重），验收时建议只跑一次或先清空 `notes` 表。
5. root 空密码默认值仅限本地隔离开发，不可用于任何暴露网络的环境。
6. 集成测试依赖本机 seekdb 在线；`npm test` 在数据库离线时 `tests/api-integration.test.js` 会如预期报启动失败（这正是故障路径要验证的行为，但会导致该文件其余用例失败），可先用 `npm run test:offline` 验证其余 5 个文件。
7. 检索最多扫描 5000 条候选、最多返回 50 张证据卡；超出时响应中 `truncated=true` 且带说明文案，不做无限内存驻留。

## 开发记录与测试日志

见 [`DEV_LOG.md`](DEV_LOG.md)：环境检查、真实连接证据、冒烟记录的原始命令与输出均原样保留。

- Qoder 会话导出记录（已排除私有推理内容）：[development/qoder-session.jsonl](development/qoder-session.jsonl)
- 调用方独立验证记录：[development/VERIFICATION.md](development/VERIFICATION.md)
- 官方 seekdb 1.4 容器测试原始日志：[development/test-stock-seekdb.log](development/test-stock-seekdb.log)
- Qoder Sites 发布对话截图：[development/screenshots/qoder-sites-publication.jpg](development/screenshots/qoder-sites-publication.jpg)
