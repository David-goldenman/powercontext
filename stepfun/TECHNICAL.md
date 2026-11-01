# 端侧 AI 推理性能优化与 seekdb 混合检索

> 2026 OceanBase 数据库大赛 · 阶跃星辰赛道 · 参见 `stepfun/`
>
> 一个在**浏览器本地**完成向量化、用 **seekdb** 做向量与全文混合检索的迷你知识库。

---

## 1. 结论先行

| 项目 | 结果 |
|---|---|
| 端侧推理 | Transformers.js + ONNX Runtime Web，文档与查询向量**全部在用户设备计算** |
| 模型 | `Xenova/bge-small-zh-v1.5`，512 维，中文语义 |
| WebGPU 路径 | `fp16` 权重，向量化 **69.0 条/秒** |
| WASM 路径 | `q8`（INT8）权重，向量化 **40.5 条/秒**，不支持 WebGPU 时自动降级 |
| seekdb 检索 | 混合检索 **3.0–6.1 ms**（客户端到端 10–15 ms） |
| 检索质量（5 题 Top1） | 混合 **5/5**，仅向量 4/5，仅全文 4/5 |

一个必须记录的实测发现：**ONNX Runtime 的 WebGPU 执行器在 INT8 权重上会产生退化向量**，两个语义无关的句子余弦相似度高达 0.983（正常约 0.5），导致检索全部失准。因此 WebGPU 用 fp16、WASM 用 q8。详见第 5 节。

---

## 2. 运行方式

### 2.1 前置

- Node.js ≥ 18
- 一个可用的 seekdb（server 模式，默认 `127.0.0.1:2881`）
- 支持 WebGPU 的 Chrome / Edge（不支持时自动降级 WASM，功能不受影响）

### 2.2 启动 seekdb

```bash
bash scripts/start-seekdb.sh 2881 ./.seekdb
```

脚本会启动 seekdb 并等待端口就绪。若已有实例，跳过此步，用环境变量指向它：

```bash
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASS= SEEKDB_DB=test
```

### 2.3 安装依赖并启动应用

```bash
npm install --legacy-peer-deps   # seekdb 的 peer 依赖里带 workspace:* 声明，需此参数
npm start                        # http://127.0.0.1:8787
```

`--legacy-peer-deps` 是必要的：`@seekdb/default-embed` 在 package.json 中把 peerDependencies 写成了 `"seekdb": "workspace:*"`，标准解析会报 `EUNSUPPORTEDPROTOCOL`。本项目向量由浏览器计算，不使用该包。

### 2.4 模型

模型放在 `models/Xenova/bge-small-zh-v1.5/`，由服务端同源提供，浏览器从 `/models/...` 加载。若缺失：

```bash
bash scripts/fetch-model-zh.sh
```

> 推理在浏览器内执行。模型文件经 HTTP 传输只影响加载来源，不改变计算位置；不涉及任何云端推理调用。

### 2.5 验证降级路径

同一个浏览器里直接加 URL 参数即可，无需改代码或换机器：

- `http://127.0.0.1:8787/` —— 自动探测，优先 WebGPU
- `http://127.0.0.1:8787/?force=wasm` —— 强制 WASM
- `http://127.0.0.1:8787/?force=webgpu` —— 强制 WebGPU

---

## 3. 模型与量化

### 3.1 模型选择

| 候选 | 5 题 Top1 命中 | 结论 |
|---|---|---|
| `all-MiniLM-L6-v2`（英文） | 4/5（80%） | 中文语义弱，「散步」类查询把咖啡馆排第一 |
| `bge-small-zh-v1.5`（中文） | **5/5（100%）** | 采用 |

选用 `Xenova/bge-small-zh-v1.5`，ONNX 格式，来源 `https://huggingface.co/Xenova/bge-small-zh-v1.5`（镜像 `hf-mirror.com`）。

用法按 bge 官方约定：**CLS 池化**；**查询侧加检索指令前缀**「为这个句子生成表示以用于检索相关文章：」，文档侧不加。四种组合实测（INT8，CPU）：

| 池化 | 查询前缀 | Top1 | Top3 |
|---|---|---|---|
| mean | 无 | 5/5 | 5/5 |
| mean | 有 | 4/5 | 5/5 |
| cls | 无 | 5/5 | 5/5 |
| **cls** | **有** | **5/5** | **5/5** |

### 3.2 量化方案与体积

模型文件实际大小（字节）：

| 精度 | 文件 | 体积 | 压缩比 |
|---|---|---|---|
| fp32（基线） | `model.onnx` | 94 851 877 B = **90.46 MB** | 1.00× |
| fp16 | `model_fp16.onnx` | 47 497 427 B = **45.30 MB** | **2.00×** |
| INT8 | `model_quantized.onnx` | 24 010 842 B = **22.90 MB** | **3.95×** |

压缩比口径：`量化前体积 / 量化后体积`，按实际模型文件总字节数计算。

### 3.3 检索质量变化

与 fp32 基线对比（CPU / WASM 后端，同一批 6 篇文档、5 条查询）：

| 精度 | 平均文档余弦（vs fp32） | Top3 重合率 | Top1 命中率 | 加载耗时 |
|---|---|---|---|---|
| fp16 | 1.0000 | 100% | 100% | 118 ms |
| INT8 | 0.9908 | 100% | 100% | 87 ms |

INT8 的文档向量与 fp32 平均余弦 0.9908，Top3 排序完全一致，Top1 命中率无下降。

---

## 4. 端侧运行

### 4.1 运行时与后端选择

- 运行时：**Transformers.js 3.8.1** + **ONNX Runtime Web**
- 后端探测：`navigator.gpu` 存在 → `requestAdapter()` 成功 → 用 `device:'webgpu'`；否则 `device:'wasm'`
- 加载期兜底：WebGPU 初始化抛错时，捕获后直接用 WASM 重建 pipeline，界面提示"WebGPU 失败后降级"
- 精度随后端切换：WebGPU → `fp16`，WASM → `q8`

### 4.2 降级验证结果

| 路径 | 触发方式 | 实际后端 | 精度 | 向量化速度 | 检索 Top1 |
|---|---|---|---|---|---|
| 自动 | 直接访问 | WebGPU | fp16 | 69.0 条/秒 | 5/5 |
| 强制 | `?force=wasm` | WASM | q8 | 40.5 条/秒 | 5/5 |

两条路径功能完全对齐：文档数一致（6/6）、混合检索结果一致（5/5）、界面均明确显示当前后端。

### 4.3 禁止项自查

- 不调用任何云端推理 API：模型与推理均在浏览器内，服务端只做 seekdb 读写代理
- 不预计算向量：服务端代码里没有任何 embedding 逻辑，收到的是浏览器算好的向量
- 不绕过 seekdb：向量与原文都写入 seekdb，检索通过 `hybridSearch` 完成

---

## 5. WebGPU + INT8 的退化问题（关键发现）

初版 WebGPU 使用 `dtype:'q8'`，检索结果系统性错误：无论问什么都把第一篇文档排第一。定位过程：

1. 检查归一化：向量 L2 范数为 1，normalize 已生效 → 排除归一化
2. 检查语义区分度：两个**语义无关**查询的余弦相似度 = **0.9833**（正常应约 0.5）→ 向量本身已退化
3. 后端与精度矩阵实测（同一查询对，余弦应接近 0.5）：

| 后端 / 精度 | 无关查询对余弦 | 无关文档对余弦 | 判断 |
|---|---|---|---|
| wasm / fp32 | 0.5065 | 0.5431 | 正常 |
| wasm / fp16 | 0.5065 | 0.5430 | 正常 |
| wasm / q8 | 0.5244 | 0.5557 | 正常 |
| webgpu / fp32 | 0.5065 | 0.5431 | 正常 |
| webgpu / fp16 | 0.5057 | 0.5435 | 正常 |
| **webgpu / q8** | **0.9833** | **0.7426** | **退化** |

结论：问题出在 ONNX Runtime Web 的 **WebGPU 执行器 + INT8 权重**这一组合，与 fp32/fp16、与 WASM+q8 都无关。修正后 WebGPU 用 fp16，同时向量化速度反而从 22.5 条/秒提升到 69.0 条/秒。

这个问题在"只看能不能跑通"的验证里不会暴露——向量照常返回、维度正确、检索照常出结果，只有检查语义区分度才会发现。因此技术说明里必须写明。

---

## 6. seekdb 使用

### 6.1 集合结构与索引

集合 `kb_docs`，schema：

```js
new Schema({
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: 512, distance: 'l2', m: 16, ef_construction: 200, ef_search: 64 },
  }),
  fulltextIndex: new FulltextIndexConfig('ngram2', { min_ngram_size: 1, max_ngram_size: 2 }),
})
```

- 建集合时显式传 `embeddingFunction: null`：向量由浏览器计算，数据库只负责存储与检索
- 全文用 `ngram2` 分析器，适配中文
- 每条记录：`ids`（唯一 ID）、`embeddings`（512 维）、`documents`（原文）、`metadatas`（`docId` / `source` / `index`）

### 6.2 混合检索方式

调用 `collection.hybridSearch()`，一次调用内由 seekdb 完成两路召回与融合，客户端**不做**结果拼接：

```js
await coll.hybridSearch({
  query: { whereDocument: { $contains: queryText } },   // 全文
  knn:   { queryEmbeddings: queryVector },              // 向量
  rank:  { rrf: { rankWindowSize: 60, rankConstant: 60 } }, // RRF 融合
  nResults: 5,
})
```

底层走 seekdb 的 `DBMS_HYBRID_SEARCH` 存储过程，融合在数据库内完成。

### 6.3 增量写入与索引刷新

- 写入：`collection.add({ ids, embeddings, documents, metadatas })`
- 刷新：写完立即 `await collection.refresh_index()`，保证新数据可检索
- 实测：追加 2 篇后总数 6 → 8，查询「哪家新开的书店可以看书」新文档**立即**排到 #1

### 6.4 时间开销（单批次实测）

| 阶段 | 耗时 |
|---|---|
| 写入 6 条 | 22–26 ms |
| `refresh_index` | 503–667 ms |
| 混合检索 | 3.0–6.1 ms |
| 端到端（含浏览器查询向量化） | 10–15 ms |

`refresh_index` 是主要开销，因此采用**批量写入 + 每批一次刷新**，而非逐条刷新。

---

## 7. 状态与优化

### 7.1 状态管理

- 集合：启动时 `hasCollection` 检查，存在则复用，不存在则按 schema 创建
- 维度：由首次写入的记录向量长度确定；切换模型后可点「清空知识库」重建
- 会话：文档 ID 用时间戳 + 批内序号生成，避免重复导入时主键冲突

### 7.2 优化点与实测

| 优化 | 做法 | 效果 |
|---|---|---|
| 批处理 | 每 16 篇一批向量化 | 6 篇从 439 ms 降到 87 ms（fp16/WebGPU） |
| 索引刷新合并 | 每批刷新一次，非逐条 | 6 条只付一次 667 ms |
| 精度按后端选择 | WebGPU→fp16 | 22.5 → 69.0 条/秒，且修掉退化 |
| 模型本地托管 | 同源 `/models` | 首次加载 0.30–0.36 s（本地）；不受外网影响 |
| seekdb 单 SQL 融合 | `hybridSearch` 而非两路客户端合并 | 检索 3–6 ms，一次往返 |

### 7.3 检索质量对比（同一知识库、同一问题集）

| 模式 | Top1 命中 |
|---|---|
| 混合（向量 + 全文，RRF） | **5/5** |
| 仅向量 | 4/5 |
| 仅全文 | 4/5 |

「哪里适合安静看书」：仅向量把「图书馆自习室」排第一，混合检索回到「星巴克」（原文明确写"适合看书"）。「便宜又快的吃饭地方」：仅全文把技术文档排第一，混合检索正确给出「小面馆」。这正是混合检索要解决的问题。

---

## 8. 复现步骤

```bash
# 1. seekdb
bash scripts/start-seekdb.sh 2881 ./.seekdb

# 2. 依赖
npm install --legacy-peer-deps

# 3. 模型（若 models/ 下为空）
bash scripts/fetch-model-zh.sh

# 4. 启动
npm start
# 浏览器打开 http://127.0.0.1:8787/

# 5. 量化基准（可选，需本地已下载 fp32/fp16/q8 三档）
node scripts/bench_quant_zh.mjs
node scripts/tune_bge.mjs          # 池化 × 查询前缀的组合对比
```

测试语料见 `corpus/sample.md`；基准输出见 `bench_quant_zh.json`。

---

## 9. 目录

```
stepfun/
├── server/
│   ├── index.mjs        # HTTP 服务：seekdb 读写代理（无推理逻辑）
│   └── seekdb.mjs       # seekdb 客户端、集合 schema、hybridSearch 封装
├── web/
│   ├── index.html       # 界面：导入 / 检索 / 运行状态
│   ├── main.js          # 端侧推理 + 后端选择与降级 + 检索调用
│   └── style.css
├── scripts/
│   ├── start-seekdb.sh  # 启动 seekdb server
│   ├── fetch-model-zh.sh        # 下载中文模型
│   ├── fetch-model.sh           # 下载英文对照模型
│   ├── bench_quant_zh.mjs       # 量化体积/质量基准
│   └── tune_bge.mjs             # bge 用法（池化/前缀）调优
├── corpus/sample.md
├── models/              # 模型权重（不同源提交，见 .gitignore）
├── bench_quant_zh.json  # 基准结果
└── TECHNICAL.md         # 本文件
```

## 10. 已知限制

- 端侧模型为通用中文 embedding，专业领域语料建议换领域模型或加微调
- 全文检索走 `ngram2`，对长文档的短语匹配能力弱于专用分词器；主要依靠向量路兜底
- `refresh_index` 有 0.5–0.7 s 开销，超大语料需调整为更长的批量周期
- WebGPU 在部分集成显卡上首次编译慢，已用预热（`'预热'` 试算）摊掉
