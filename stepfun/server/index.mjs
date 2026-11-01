// 后端 HTTP 服务：只做 seekdb 的读写代理，不碰推理（推理全在浏览器）
import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { getCollection, dropCollection, stats, close } from './seekdb.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(express.json({ limit: '64mb' }));
app.use(express.static(path.join(__dirname, '..', 'web')));
// 本地提供 Transformers.js 浏览器 bundle 与 ONNX Runtime wasm，避免依赖 CDN
const TF_DIR = path.join(__dirname, '..', 'node_modules', '@huggingface', 'transformers', 'dist');
app.use('/vendor', express.static(TF_DIR, { maxAge: '1h' }));
// 本地模型权重，浏览器端从同源加载，推理仍在用户设备上完成
app.use('/models', express.static(path.join(__dirname, '..', 'models'), { maxAge: '1d' }));

let dim = null;

function timed(fn) {
  const t0 = process.hrtime.bigint();
  return fn().then(v => ({ v, ms: Number(process.hrtime.bigint() - t0) / 1e6 }));
}

app.get('/api/health', async (_req, res) => {
  try {
    const s = await stats();
    res.json({ ok: true, seekdb: s });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e) });
  }
});

// 批量写入：body.records = [{ id, text, vector: number[], meta?: {} }]
app.post('/api/docs', async (req, res) => {
  const { records } = req.body || {};
  if (!Array.isArray(records) || records.length === 0) {
    return res.status(400).json({ ok: false, error: 'records 不能为空' });
  }
  if (!dim) dim = records[0].vector.length;

  try {
    const coll = await getCollection(dim);
    const { ms } = await timed(() => coll.add({
      ids: records.map(r => String(r.id)),
      embeddings: records.map(r => r.vector),
      documents: records.map(r => r.text),
      metadatas: records.map(r => ({ docId: String(r.id), ...(r.meta || {}) })),
    }));
    const { ms: refreshMs } = await timed(() => coll.refresh_index());
    const total = await coll.count();
    res.json({ ok: true, inserted: records.length, total, writeMs: ms, refreshMs });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e) });
  }
});

// 混合检索：向量 + 全文，融合在 seekdb 内完成（DBMS_HYBRID_SEARCH + RRF）
// body = { vector: number[], text: string, k?: number, mode?: 'hybrid'|'vector'|'fulltext' }
app.post('/api/search', async (req, res) => {
  const { vector, text, k = 5, mode = 'hybrid' } = req.body || {};
  try {
    const coll = await getCollection(dim || (vector && vector.length) || 384);

    let opts;
    if (mode === 'vector') {
      opts = {
        knn: { queryEmbeddings: vector, nResults: k },
        nResults: k,
        include: ['documents', 'metadatas', 'distances'],
      };
      const r = await coll.query({
        queryKey: 'embedding',
        queryEmbeddings: vector,
        nResults: k,
        include: ['documents', 'metadatas', 'distances'],
      });
      return res.json({ ok: true, mode, hits: shape(r) });
    }
    if (mode === 'fulltext') {
      opts = {
        query: { whereDocument: text ? { $contains: text } : {}, nResults: k },
        nResults: k,
        include: ['documents', 'metadatas', 'distances'],
      };
    } else {
      opts = {
        query: { whereDocument: text ? { $contains: text } : {}, nResults: k },
        knn: { queryEmbeddings: vector, nResults: k },
        rank: { rrf: { rankWindowSize: 60, rankConstant: 60 } },
        nResults: k,
        include: ['documents', 'metadatas', 'distances'],
      };
    }

    const { v: r, ms } = await timed(() => coll.hybridSearch(opts));
    res.json({ ok: true, mode, searchMs: ms, hits: shape(r) });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e) });
  }
});

app.post('/api/reset', async (_req, res) => {
  try { await dropCollection(); dim = null; res.json({ ok: true }); }
  catch (e) { res.status(500).json({ ok: false, error: String(e.message || e) }); }
});

function shape(r) {
  const ids = (r.ids && r.ids[0]) || [];
  const docs = (r.documents && r.documents[0]) || [];
  const metas = (r.metadatas && r.metadatas[0]) || [];
  const dist = (r.distances && r.distances[0]) || [];
  return ids.map((id, i) => ({
    id,
    text: docs[i] || '',
    meta: metas[i] || {},
    distance: dist[i],
  }));
}

const PORT = Number(process.env.PORT || 8787);
const server = app.listen(PORT, () => {
  console.log(`[server] http://127.0.0.1:${PORT}  (seekdb ${process.env.SEEKDB_HOST || '127.0.0.1'}:${process.env.SEEKDB_PORT || 2881})`);
});

process.on('SIGINT', async () => { await close(); server.close(); process.exit(0); });
process.on('SIGTERM', async () => { await close(); server.close(); process.exit(0); });
