# 端侧知识库：Transformers.js + seekdb 混合检索

浏览器本地向量化，seekdb 完成向量与全文混合检索。2026 OceanBase 数据库大赛 · 阶跃星辰赛道提交。

技术细节、量化数据与实测结果见 **[TECHNICAL.md](./TECHNICAL.md)**。

## 快速开始

```bash
# 1. 启动 seekdb（默认 127.0.0.1:2881）
bash scripts/start-seekdb.sh 2881 ./.seekdb

# 2. 安装依赖（seekdb 的 peer 依赖带 workspace:* 声明，必须加该参数）
npm install --legacy-peer-deps

# 3. 准备模型（首次）
bash scripts/fetch-model-zh.sh

# 4. 启动
npm start
```

打开 <http://127.0.0.1:8787/>。浏览器要求：Chrome / Edge 最新版；支持 WebGPU 时自动使用，不支持时自动降级 WASM 并在界面提示。

验证降级路径：`http://127.0.0.1:8787/?force=wasm`

## 它做什么

1. 导入 `.txt` / `.md` 或直接粘贴文本
2. 浏览器用 `bge-small-zh-v1.5`（INT8/FP16）在**本地**把内容转成 512 维向量
3. 向量与原文写入 seekdb
4. 提问时同样在本地算查询向量，由 seekdb 用**一条混合检索**（向量 + 全文，RRF 融合）返回结果

不调用任何云端推理接口。服务端只做 seekdb 读写代理。

## 实测结果

| 项目 | WebGPU（fp16） | WASM（q8） |
|---|---|---|
| 向量化 | 69.0 条/秒 | 40.5 条/秒 |
| 模型加载 | 0.36 s | 0.30 s |
| 混合检索 | 3.0–6.1 ms | 4.1 ms |
| 端到端检索 | 10–15 ms | 13 ms |
| Top1 命中（5 题） | 5/5 | 5/5 |

量化：fp32 90.46 MB → INT8 22.90 MB（**3.95×**），文档向量与 fp32 平均余弦 0.9908，Top3 排序完全一致。

检索质量：混合 5/5，仅向量 4/5，仅全文 4/5。

## 一个重要发现

ONNX Runtime 的 **WebGPU 执行器 + INT8 权重**会产生退化向量：两个语义无关句子的余弦相似度高达 0.983（正常约 0.5），检索会系统性失准，但仍然"能跑出结果"。因此 WebGPU 配 fp16、WASM 配 q8。复现矩阵见 TECHNICAL.md 第 5 节。

## 目录

```
server/   HTTP 服务 + seekdb 封装（无推理逻辑）
web/      端侧推理与界面
scripts/  启动 seekdb、下载模型、量化基准、bge 用法调优
corpus/   示例语料
models/   模型权重（不同源提交）
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEEKDB_HOST` | `127.0.0.1` | seekdb 地址 |
| `SEEKDB_PORT` | `2881` | 端口 |
| `SEEKDB_USER` | `root` | 用户 |
| `SEEKDB_PASS` | 空 | 密码 |
| `SEEKDB_DB` | `test` | 数据库 |
| `SEEKDB_COLLECTION` | `kb_docs` | 集合名 |
| `PORT` | `8787` | 应用端口 |
