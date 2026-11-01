# 离谱发明工坊

> 2026 OceanBase 数据库大赛 · Dify 生态赛道（20 分）
>
> 塞两件日常物品进去，现场发明一件没人要的东西。图鉴的**存储与检索全部由 seekdb 承担**。

---

## 1. 这是什么

一个整活的文字游戏。你说「猫 + 微波炉」，机器现场发明「猫波炉」，附赠一条副作用；说「面条 + 路由器」，得到「无线吸汤路由」。每件发明自动进图鉴。

有意思的地方在于**图鉴会记住**：

- 同一对物品再问一次，不会再发明一遍，而是直接把图鉴里那条记录调出来 —— 包括当初的稀有度和副作用。
- 顺序无关，「路由器 + 面条」和「面条 + 路由器」认作同一个配方。
- 新发明时，机器会先看图鉴里有没有思路相近的东西，在回复末尾小声提一句「这个思路有点像「反向遛猫器」」。

所以玩得越多，图鉴越厚，越能撞见自己或别人以前造的东西。这是一个**会积累的**整活。

## 2. Dify 与 seekdb 的分工

| 组成 | 承担什么 | 证据 |
|---|---|---|
| **Dify**（1.9.1，必选） | 核心流程编排：解析输入 → 分支判断 → 调 seekdb → 调模型 → 回写 → 组回复，共 15 个节点 | `screenshots/01_workflow_canvas.png`、`dsl/fusion_lab.yml` |
| **seekdb**（1.4.0） | 图鉴的**存储 + 全文检索**：表结构、ngram 全文索引、查重、相似召回 | `evidence/seekdb_query.txt`、`backend/seekdb.mjs` |

seekdb 不是"装了就算"，它承担的是这个作品的核心机制：**没有图鉴的记忆，这个游戏就退化成一次性笑话生成器**。命中路径完全不调模型（实测 0.29–0.37s），新发明路径才调（1.26s），这个 4 倍的时间差本身就是 seekdb 在起作用的证据。

### 具体用了 seekdb 哪些能力

1. **结构化索引**：`idx_a`、`idx_b` 两个索引，支撑「不区分顺序的精确查重」——
   ```sql
   WHERE (item_a=? AND item_b=?) OR (item_a=? AND item_b=?)
   ```
2. **ngram 全文索引**：`FULLTEXT KEY ft_content (name, effect) WITH PARSER ngram`，
   支撑「按效果描述召回相似发明」，`MATCH ... AGAINST(... IN NATURAL LANGUAGE MODE)` 出相关性分数。
3. **两者组合排序**：相似检索先按结构化命中数、再按全文相关性排序 ——
   ```sql
   ORDER BY struct_score DESC, ft_score DESC, created_at DESC
   ```

实测：查「拖鞋 + 洗衣机」时，「拖鞋巡洋舰」（共享"拖鞋"）排第一，「通勤洗衣一体机」（共享"洗衣机"）排第二，第三名靠全文相关性召回了「三分钟出面机」。

## 3. 运行环境

| 组件 | 版本 | 说明 |
|---|---|---|
| Dify | 1.9.1 | Docker Compose 部署，社区版 |
| seekdb | 1.4.0 | 独立实例，MySQL 协议，端口 2895 |
| Node.js | ≥ 18（实测 24.20） | 跑 seekdb 数据层服务 |
| Docker | 29.1.3 + Compose v2 | 跑 Dify |
| 模型 | OpenAI 兼容 LLM | 本例用 `deepseek-pro`，见 §5 换绑 |

> **注意**：seekdb 在这里是**应用数据层**，不是 Dify 自身的元数据库。Dify 的元数据仍在它自带的 PostgreSQL 里。这样部署更省事，也符合题面「seekdb 为应用提供数据存储、查询与检索能力」这一条。

## 4. 从零跑起来

### 4.1 启动 seekdb

```bash
# 任选一种：本机已有 seekdb 二进制
seekdb --base-dir /data/dify --port 2895 \
       --parameter datafile_size=2G --parameter log_disk_size=2G
```

确认监听：

```bash
mysql -h 127.0.0.1 -P 2895 -u root -e "SELECT VERSION();"
# 应返回 5.7.25-OceanBase seekdb-v1.4.0.0
```

### 4.2 启动数据层服务

```bash
cd backend
npm install --legacy-peer-deps      # 见 §7 为什么需要这个参数
SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2895 PORT=3000 node seed.mjs   # 灌入 24 条种子发明
SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2895 PORT=3000 node server.mjs
```

自检：

```bash
curl -s http://127.0.0.1:3000/health
# {"ok":true,"table":"fusions","total":24}
```

接口一览（全部 JSON）：

| 接口 | 用途 |
|---|---|
| `POST /lookup {a,b}` | 精确查重 + 相似召回，Dify 调用 |
| `POST /save {a,b,name,effect,side_effect,rarity}` | 写入新发明，重复则拒绝 |
| `GET /stats` | 图鉴统计（总数、按稀有度分布、最新 5 条） |
| `GET /list?limit=N` | 列出图鉴 |
| `POST /reset` | 清空（用于恢复初始状态） |

### 4.3 启动 Dify

```bash
git clone --depth 1 --branch 1.9.1 https://github.com/langgenius/dify.git
cd dify/docker && cp .env.example .env
docker compose up -d
```

首次访问 `http://127.0.0.1/install` 设置管理员账户。

**必须调的两个环境变量**（写在 `dify/docker/.env`）：

```bash
PIP_MIRROR_URL=https://pypi.tuna.tsinghua.edu.cn/simple
PLUGIN_DAEMON_ACTIVITY_TIMEOUT=600
```

不设第一项，插件依赖会因拉不到 PyPI 而**卡在 running 后被杀**（报 `init process exited due to no activity for 120 seconds`）。这是我实际踩到的坑，见 §7。

### 4.4 装模型插件

Dify 1.9 把模型供应商拆成了插件。在 **插件 → 探索 Marketplace** 搜 `OpenAI-API-compatible` 安装，或走 API：

```bash
curl -X POST http://127.0.0.1/console/api/workspaces/current/plugin/install/marketplace \
  -H "Authorization: Bearer $CONSOLE_TOKEN" -H "Content-Type: application/json" \
  -d '{"plugin_unique_identifiers":["langgenius/openai_api_compatible:0.0.73@4cf123ffaa4556f079bed6c47f3a462cc825504b859b183239e74eb19136a4b5"]}'
```

## 5. 导入 DSL 并换绑

### 5.1 导入

**应用 → 导入 DSL 文件 → 选 `dsl/fusion_lab.yml`**。

DSL 导出的版本是 `0.3.0`，Dify 1.9.1 会自动升到 `0.4.0`，导入提示 `completed-with-warnings` 属正常（版本迁移提示），节点与连线完整。

### 5.2 换绑模型（必做）

DSL 里写死的模型是 `deepseek-pro`，provider 是 `langgenius/openai_api_compatible/openai_api_compatible`。如果你用自己的模型：

1. 在 **设置 → 模型供应商 → OpenAI-API-compatible** 里添加模型，填你的 `endpoint_url` 和 `api_key`；
2. 打开工作流，点「发明生成」节点，重新选一次模型；
3. 发布。

DSL 里**不含任何真实凭据**，`api_key` 是占位值。

### 5.3 换绑数据层地址（可能要做）

DSL 里两个 HTTP 节点指向 `http://172.30.224.1:3000`（那是 Docker 容器访问宿主机的网关地址）。

- 如果 Dify 和 `backend` 跑在**同一台机器、Dify 非容器**：改成 `http://127.0.0.1:3000`。
- 如果 Dify 在 Docker 里、backend 在宿主机：保持不变；Linux 上通常是 `http://172.17.0.1:3000`，WSL 里是 `ip route | awk '/default/{print $3}'` 的返回值。
- 如果两者都在 Docker 里：用 compose 服务名，如 `http://fusion-api:3000`。

改法：工作流里点开「查图鉴历史」和「存档进图鉴」两个节点，改 URL，然后发布。

**连通性自检**（在 api 容器里执行）：

```bash
docker compose exec -T api sh -c 'curl -s http://172.30.224.1:3000/health'
```

## 6. 验证样例

完整步骤、预期表现和实测结果见 **[VERIFY.md](./VERIFY.md)**。三组样例分别覆盖：

| 样例 | 验证什么 |
|---|---|
| A. `猫 + 微波炉` | 命中路径：seekdb 里有记录，直接读出来，不调模型 |
| B. `面条 + 路由器` → 再问一次 | 写入 + 复用：第一次发明并入库，第二次命中 |
| C. `路由器 + 面条` | 顺序无关的查重逻辑 |
| D. `猫`（只给一件） | 分支兜底：不报错，引导补一件 |

## 7. 踩过的坑

**`npm install` 必须加 `--legacy-peer-deps`。**
`seekdb@1.4.0` 的 peerDependencies 里有个 `@seekdb/default-embed` 声明了 `"seekdb": "workspace:*"`（发包缺陷），标准 npm 解析会直接失败。

**Dify 插件装不上，卡在 running。**
报 `init environment failed: failed to install dependencies: signal: killed, output: init process exited due to no activity for 120 seconds`。根因是容器内拉不到 PyPI。设 `PIP_MIRROR_URL` 指到国内镜像即可，同时把 `PLUGIN_DAEMON_ACTIVITY_TIMEOUT` 提到 600 留余量。

**Docker Hub 直连被阻断。**
需要配 `registry-mirrors`。可用的加速器实测：`docker.m.daocloud.io`、`docker.1ms.run`、`docker.xuanyuan.me`（都返回 401，是 registry v2 的正常鉴权响应，能正常拉取）。

**seekdb 的 ngram 全文索引默认按 2-gram 切分，单字查不到。**
`MATCH ... AGAINST('猫')` 返回空，`LIKE '%猫%'` 能中。所以物品名里如果只有一个字（「猫」），靠全文索引用不了，**必须走结构化 `WHERE item_a=?`**。这也是我把相似检索写成「结构化命中 + 全文相关性」两段式的原因之一。

**seekdb 的 JS SDK 的 `createCollection` 不能用。**
`collection.add()` 强制要求 `documents` 必须配 `embeddings`，纯全文场景下无解（报 `Either embeddings or documents must be provided`，而只传 documents 又报 `Documents provided but no embeddings`）。另外 `client.createDatabase()` 会先连目标库，库不存在时报 `ER_BAD_DB_ERROR`。所以我改用 `mysql2` 直连 SQL —— 这也更贴近「真的在用数据库」。

**`sys.query` 不能直接当代码节点的入参名。**
Dify 里 `sys.query` 是合法变量选择器，但代码节点会把它当 Python 关键字参数传入，报 `main() got an unexpected keyword argument 'sys.query'`。变量名必须改成合法标识符（如 `query`），`value_selector` 仍指向 `["sys","query"]`。

## 8. 已知限制

- 图鉴是全局的，没有按用户隔离。加 `user_id` 字段就能分开，但本题是单人整活，没做。
- 稀有度由模型自己给，没有做配额约束，理论上可能连续出「传说」。
- 相似召回只用了结构化 + 全文，**没用向量**。物品名太短（2-4 字），向量区分度不如直接匹配；要做语义召回得引入 embedding 服务，会破坏"开箱即跑"。
- ngram 切分下，单字物品（「猫」）的相似召回只能靠结构化命中。
- 依赖一个可用的 OpenAI 兼容 LLM 端点。

## 9. 文件说明

```
dify/
├── dsl/fusion_lab.yml          Dify Chatflow（15 节点，导入用）
├── backend/
│   ├── seekdb.mjs              seekdb 数据层：建库建表、查重、相似检索、写入
│   ├── server.mjs              HTTP 服务（/lookup /save /stats /list /reset）
│   ├── seed.mjs                24 条种子发明
│   └── package.json
├── gen_dsl.py                  DSL 生成脚本（DSL 由它产出，便于复现与改流程）
├── VERIFY.md                   验证样例与实测记录
├── README.md                   本文件
└── screenshots/                运行证据
```
