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
| seekdb | 1.4.0（官方镜像 `oceanbase/seekdb:latest`） | 与 Dify 同网络启动，服务名 `fusion-seekdb` |
| Node.js | ≥ 18（镜像内用 `node:24-alpine`） | 跑 seekdb 数据层服务 |
| Docker | 29.1.3 + Compose v2 | 跑 Dify 与数据层 |
| 模型 | OpenAI 兼容 LLM | 本例用 `deepseek-pro`，见 §5 换绑 |

> **seekdb 在这里是应用数据层，不是 Dify 自身的元数据库。** Dify 的元数据仍在它自带的 PostgreSQL 里。这样部署更省事，也符合题面「seekdb 为应用提供数据存储、查询与检索能力」这一条。

## 4. 从零跑起来

以下三步即可，**DSL 不需要改任何地址**。

### 4.1 启动 Dify

```bash
git clone --depth 1 --branch 1.9.1 https://github.com/langgenius/dify.git
cd dify/docker && cp .env.example .env
```

编辑 `.env`，加两项（**不设第一项，插件会装不上**，见 §7）：

```bash
PIP_MIRROR_URL=https://pypi.tuna.tsinghua.edu.cn/simple
PLUGIN_DAEMON_ACTIVITY_TIMEOUT=600
```

```bash
docker compose up -d
```

首次访问 `http://127.0.0.1/install` 设置管理员账户。

### 4.2 启动数据层（seekdb + 数据服务）

```bash
bash scripts/start-data-layer.sh /path/to/dify/docker
```

脚本会做三件事：构建 `backend` 镜像、起 `fusion-seekdb` 与 `fusion-api`、把两者接入 **Dify 的默认 compose 网络**。结束时打印：

```
==> 就绪：{"ok":true,"table":"fusions","total":24}
```

24 条是种子数据（`backend/seed.mjs`），容器首次启动时自动灌入。

> 为什么叠加到 Dify 的 compose 上：`fusion-api` 和 Dify 的 `api` 容器同网络，DSL 里写服务名 `http://fusion-api:3000` 就能直接访问。Dify 的 compose **没有** `extra_hosts`，`host.docker.internal` 在 Linux/WSL 下解析不了，用服务名是最省事且跨平台一致的做法。

### 4.3 导入 DSL

**应用 → 导入 DSL 文件 → `dsl/fusion_lab.yml`**，然后发布。

DSL 导出为 `0.3.0`，Dify 1.9.1 会升到 `0.4.0`，提示 `completed-with-warnings` 属正常（版本迁移提示），节点与连线完整。

就这三步。下面 §5 是模型换绑，§6 是验证。

接口一览（`fusion-api`，全部 JSON）：

| 接口 | 用途 |
|---|---|
| `POST /lookup {a,b}` | 精确查重 + 相似召回，Dify 调用 |
| `POST /save {a,b,name,effect,side_effect,rarity}` | 写入新发明，重复则拒绝 |
| `GET /health` | 健康检查与当前条数 |
| `GET /stats` | 图鉴统计（总数、按稀有度分布、最新 5 条） |
| `GET /list?limit=N` | 列出图鉴 |
| `POST /reset` | 清空表（用于恢复初始状态） |

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

### 5.3 数据层地址（默认已配好，一般不用动）

DSL 里两个 HTTP 节点指向 **`http://fusion-api:3000`**，也就是 §4.2 起的 compose 服务名。按 §4 的步骤走，这个地址开箱可用，**不需要任何修改**。

只有在不走 §4.2、自己另起数据层时才需要改：

| 你的部署方式 | 该填什么 |
|---|---|
| 按 §4.2 叠加 compose（推荐） | `http://fusion-api:3000`，不用改 |
| 数据层和 Dify 在**同一个 compose 网络**里的其他服务名 | 名字改成你的服务名 |
| 数据层在宿主机、Dify 在 Mac/Windows 的 Docker Desktop | `http://host.docker.internal:3000` |
| 数据层在宿主机、Dify 在 Linux/WSL 的 Docker Engine | 宿主机网关，Linux 通常是 `http://172.17.0.1:3000` |

改法有两种：工作流里点开「查图鉴历史」和「存档进图鉴」两个节点改 URL 后发布；或重新生成 DSL：

```bash
API_BASE=http://host.docker.internal:3000 python gen_dsl.py > dsl/fusion_lab.yml
```

**连通性自检**（在 Dify 的 api 容器里执行）：

```bash
docker compose exec -T api sh -c 'wget -qO- http://fusion-api:3000/health'
# {"ok":true,"table":"fusions","total":24}
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

**compose 的 `build.context` 是相对 project-directory 解析的。**
把本仓库的 compose 叠加到 Dify 的 `docker-compose.yaml` 上时，`context: ./backend` 会被解析成 `dify/docker/backend`，报 `no such file or directory`。所以 `docker-compose.data.yml` 用环境变量 `FUSION_BACKEND_DIR` 传绝对路径，由 `scripts/start-data-layer.sh` 自动算好。

**Dify 的 compose 没有 `extra_hosts`，`host.docker.internal` 在 Linux/WSL 下解析不了。**
在 api 容器里 `socket.gethostbyname("host.docker.internal")` 直接抛 `gaierror`。所以数据层不要跑在宿主机，直接叠加进 Dify 的网络用服务名 —— 这也是把数据层做成 compose 的原因。

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
├── dsl/fusion_lab.yml              Dify Chatflow（15 节点，导入用）
├── docker-compose.data.yml         数据层 compose（叠加到 Dify 的 compose 上）
├── scripts/start-data-layer.sh     一键起数据层并接入 Dify 网络
├── backend/
│   ├── seekdb.mjs                  seekdb 数据层：建库建表、查重、相似检索、写入
│   ├── server.mjs                  HTTP 服务（/lookup /save /health /stats /list /reset）
│   ├── seed.mjs                    24 条种子发明
│   ├── Dockerfile                  数据层镜像
│   └── package.json
├── gen_dsl.py                      DSL 生成脚本（DSL 由它产出，便于复现与改流程）
├── VERIFY.md                       验证样例与实测记录
├── README.md                       本文件
├── evidence/seekdb_query.txt       seekdb 直接查库的原始输出
└── screenshots/                    运行证据
```
