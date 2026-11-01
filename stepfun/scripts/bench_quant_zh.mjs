// 中文模型量化对比 + 中英文模型检索质量对比
// 指标：模型体积、压缩比、与 fp32 基线的余弦一致性、Top-K 重合、检索命中率
import { pipeline, env } from '@huggingface/transformers';
import path from 'node:path';
import fs from 'node:fs';

const ROOT = 'D:/ob-race/stepfun';
env.allowLocalModels = true;
env.allowRemoteModels = false;
env.localModelPath = path.join(ROOT, 'models');
env.backends.onnx.logLevel = 'error';

const CORPUS = [
  '星巴克西湖店，环境安静，二楼有插座，适合带电脑看书或写代码，步行约3分钟。',
  '独立咖啡馆，主打手冲咖啡，店内只有三张桌子，声音较杂，周末经常排队，步行约8分钟。',
  '城市公园，面积大，有环湖步道，适合散步和慢跑，步行约12分钟。',
  '市图书馆自习室，非常安静，需要刷身份证进入，座位紧张，步行约15分钟。',
  '小面馆，人均20元，上菜快，中午高峰需要等位，步行约6分钟。',
  'WebGPU 后端在支持的设备上比 WASM 快数倍，不支持时需要自动降级。',
];

// 每条查询标注正确的目标文档下标（用于算命中率）
const QUERIES = [
  { q: '哪里适合安静看书', gold: [0, 3] },
  { q: '附近有咖啡馆吗', gold: [0, 1] },
  { q: '可以散步的地方', gold: [2] },
  { q: '便宜又快的吃饭地方', gold: [4] },
  { q: '浏览器里推理快不快', gold: [5] },
];

function cos(a, b) { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; }
function topK(vecs, qv, k) {
  return vecs.map((v, i) => ({ i, s: cos(v, qv) })).sort((x, y) => y.s - x.s).slice(0, k).map(x => x.i);
}
function sizeOf(p) { try { return fs.statSync(p).size; } catch { return 0; } }

async function runModel(modelId, dtypes) {
  const dir = path.join(ROOT, 'models', modelId, 'onnx');
  const out = {};
  for (const [dtype, file] of Object.entries(dtypes)) {
    const p = path.join(dir, file);
    const size = sizeOf(p);
    if (!size) { console.log(`  skip ${dtype} (missing ${file})`); continue; }
    const t0 = Date.now();
    const ex = await pipeline('feature-extraction', modelId, { dtype, device: 'cpu' });
    const loadMs = Date.now() - t0;
    const t1 = Date.now();
    const dv = (await ex(CORPUS, { pooling: 'mean', normalize: true })).tolist();
    const embedMs = Date.now() - t1;
    const t2 = Date.now();
    const qv = [];
    for (const it of QUERIES) qv.push((await ex([it.q], { pooling: 'mean', normalize: true })).tolist()[0]);
    const qMs = Date.now() - t2;
    out[dtype] = { size, loadMs, embedMs, qMs, dv, qv, dim: dv[0].length };
    console.log(`  [${dtype}] size=${(size/1048576).toFixed(2)}MB load=${loadMs}ms embed6=${embedMs}ms`);
    await ex.dispose?.();
  }
  return out;
}

// 检索命中率：Top1 是否落在 gold 集合
function hitRate(res, k = 3) {
  let h = 0;
  QUERIES.forEach((it, qi) => {
    const top = topK(res.dv, res.qv[qi], k);
    if (top.some(x => it.gold.includes(x))) h++;
  });
  return h / QUERIES.length;
}

(async () => {
  const report = {};
  const plan = {
    'Xenova/all-MiniLM-L6-v2': { fp32: 'model.onnx', fp16: 'model_fp16.onnx', q8: 'model_quantized.onnx' },
    'Xenova/bge-small-zh-v1.5': { fp32: 'model.onnx', fp16: 'model_fp16.onnx', q8: 'model_quantized.onnx' },
  };

  for (const [mid, dtypes] of Object.entries(plan)) {
    console.log('\n### ' + mid);
    const r = await runModel(mid, dtypes);
    const base = r.fp32;
    if (!base) { console.log('  无 fp32 基线，跳过对比'); continue; }

    const entry = { dim: base.dim, baseSizeMB: +(base.size / 1048576).toFixed(2), variants: {} };
    for (const [dtype, v] of Object.entries(r)) {
      let meanCos = null, overlap = null;
      if (dtype !== 'fp32') {
        let cs = 0, n = 0;
        for (let d = 0; d < CORPUS.length; d++) { cs += cos(base.dv[d], v.dv[d]); n++; }
        meanCos = cs / n;
        let hit = 0, tot = 0;
        for (let qi = 0; qi < QUERIES.length; qi++) {
          const a = topK(base.dv, base.qv[qi], 3), b = topK(v.dv, v.qv[qi], 3);
          hit += a.filter(x => b.includes(x)).length; tot += a.length;
        }
        overlap = hit / tot;
      }
      entry.variants[dtype] = {
        sizeMB: +(v.size / 1048576).toFixed(2),
        compressionRatio: +(base.size / v.size).toFixed(2),
        loadMs: v.loadMs,
        embed6Ms: v.embedMs,
        meanDocCosineVsFp32: meanCos === null ? null : +meanCos.toFixed(4),
        top3OverlapVsFp32: overlap === null ? null : +overlap.toFixed(4),
        retrievalHitRateTop3: +hitRate(v, 3).toFixed(3),
      };
      const e = entry.variants[dtype];
      console.log(`  ${dtype}: ${e.sizeMB}MB x${e.compressionRatio} `
        + `cos=${e.meanDocCosineVsFp32 ?? '-'} overlap=${e.top3OverlapVsFp32 ?? '-'} `
        + `命中率@3=${(e.retrievalHitRateTop3 * 100).toFixed(0)}%`);
    }
    report[mid] = entry;
  }

  fs.writeFileSync(path.join(ROOT, 'bench_quant_zh.json'), JSON.stringify(report, null, 2));
  console.log('\nsaved bench_quant_zh.json');
})().catch(e => { console.error('FAIL', e); process.exit(1); });
