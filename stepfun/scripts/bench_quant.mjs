// 量化前后：模型体积 + 检索质量（余弦一致性 / Recall@K）
// 用 @huggingface/transformers 的 node 后端加载本地 ONNX，保证可复现
import { pipeline, env } from '@huggingface/transformers';
import path from 'node:path';
import fs from 'node:fs';

const ROOT = 'D:/ob-race/stepfun';
env.allowLocalModels = true;
env.allowRemoteModels = false;
env.localModelPath = path.join(ROOT, 'models');
env.backends.onnx.logLevel = 'error';

const MODEL = 'Xenova/all-MiniLM-L6-v2';
const MODEL_DIR = path.join(ROOT, 'models', MODEL, 'onnx');

const FILES = {
  fp32: 'model.onnx',
  fp16: 'model_fp16.onnx',
  q8: 'model_quantized.onnx',
};

function sizeOf(f) {
  try { return fs.statSync(path.join(MODEL_DIR, f)).size; } catch { return 0; }
}

function cos(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s; // 已归一化
}

const CORPUS = [
  '星巴克西湖店，环境安静，适合看书，步行3分钟',
  '独立咖啡馆，手冲咖啡，安静，步行8分钟',
  '城市公园，适合散步和慢跑，步行12分钟',
  '市图书馆自习室，非常安静，需要刷身份证',
  '小面馆，人均20元，上菜快',
  'WebGPU 后端比 WASM 快数倍，需要在设备支持时启用',
];
const QUERIES = [
  '哪里适合安静看书',
  '附近有咖啡店吗',
  '可以散步的地方',
  '便宜的吃饭地方',
  'WebGPU 和 WASM 谁快',
];

async function embedAll(extractor, texts) {
  const out = await extractor(texts, { pooling: 'mean', normalize: true });
  return out.tolist();
}

function topK(vecs, qv, k) {
  const scored = vecs.map((v, i) => ({ i, s: cos(v, qv) })).sort((a, b) => b.s - a.s);
  return scored.slice(0, k).map(x => x.i);
}

(async () => {
  const results = {};
  const base = sizeOf(FILES.fp32);
  console.log('量化前基线 fp32 =', base, 'bytes');

  for (const [dtype, file] of Object.entries(FILES)) {
    const size = sizeOf(file);
    const t0 = Date.now();
    const extractor = await pipeline('feature-extraction', MODEL, { dtype, device: 'cpu' });
    const loadMs = Date.now() - t0;

    const tEmbed = Date.now();
    const docVecs = await embedAll(extractor, CORPUS);
    const embedMs = Date.now() - tEmbed;

    const tQ = Date.now();
    const qVecs = await embedAll(extractor, QUERIES);
    const qMs = Date.now() - tQ;

    results[dtype] = { size, loadMs, embedMs, qMs, docVecs, qVecs, dim: docVecs[0].length };
    console.log(`[${dtype}] size=${size} load=${loadMs}ms embed=${embedMs}ms dim=${docVecs[0].length}`);
    await extractor.dispose?.();
  }

  // 质量对比：与 fp32 基线的逐条余弦一致性 + Top-K 重合率
  const ref = results.fp32;
  const K = 3;
  console.log('\n=== 与 fp32 基线对比（K=' + K + '）===');
  const report = {};
  for (const dtype of ['fp16', 'q8']) {
    const r = results[dtype];
    let cosSum = 0, n = 0, hit = 0, tot = 0;
    for (let qi = 0; qi < QUERIES.length; qi++) {
      const a = topK(ref.docVecs, ref.qVecs[qi], K);
      const b = topK(r.docVecs, r.qVecs[qi], K);
      hit += a.filter(x => b.includes(x)).length;
      tot += a.length;
      for (let d = 0; d < CORPUS.length; d++) {
        cosSum += cos(ref.docVecs[d], r.docVecs[d]);
        n++;
      }
    }
    const meanCos = cosSum / n;
    const overlap = hit / tot;
    const ratio = base / r.size;
    report[dtype] = {
      sizeBytes: r.size,
      sizeMB: +(r.size / 1048576).toFixed(2),
      compressionRatio: +ratio.toFixed(2),
      meanDocCosineVsFp32: +meanCos.toFixed(4),
      topKOverlap: +overlap.toFixed(4),
      loadMs: r.loadMs,
      embed6Ms: r.embedMs,
      dim: r.dim,
    };
    console.log(`${dtype}: 体积 ${report[dtype].sizeMB}MB 压缩比 ${ratio.toFixed(2)}x `
      + `平均余弦 ${meanCos.toFixed(4)} Top${K}重合 ${(overlap * 100).toFixed(1)}% load ${r.loadMs}ms`);
  }

  report.fp32 = {
    sizeBytes: ref.size, sizeMB: +(ref.size / 1048576).toFixed(2),
    compressionRatio: 1, loadMs: ref.loadMs, embed6Ms: ref.embedMs, dim: ref.dim,
  };
  fs.writeFileSync(path.join(ROOT, 'bench_quant.json'), JSON.stringify(report, null, 2));
  console.log('\nsaved bench_quant.json');
})().catch(e => { console.error('FAIL', e); process.exit(1); });
