// 端侧 embedding + seekdb 混合检索
// 推理全在本浏览器内完成（WebGPU 优先，不支持自动降级 WASM），禁止任何云端推理。
// transformers 由 index.html 以本地 vendor bundle 注入到 window.__hf
const { pipeline, env } = window.__hf;

const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
const BATCH_SIZE = 16;
// bge 系列官方用法：CLS 池化；查询侧加检索指令前缀，文档侧不加
const POOLING = 'cls';
const QUERY_INSTRUCTION = '为这个句子生成表示以用于检索相关文章：';

// 精度按后端选择：ONNX Runtime 的 WebGPU 执行器在 q8(INT8) 权重上会产出退化向量
// （实测两个无关句子的余弦相似度高达 0.983），因此 WebGPU 用 fp16，WASM 用 q8。
const DTYPE_BY_DEVICE = { webgpu: 'fp16', wasm: 'q8' };

// 模型从同源 /models 加载；推理发生在浏览器内，不调用任何云端接口
env.allowLocalModels = true;
env.allowRemoteModels = false;
env.localModelPath = '/models/';
env.useBrowserCache = true;
if (env.backends?.onnx?.wasm) {
  env.backends.onnx.wasm.wasmPaths = '/vendor/';
}

const $ = (id) => document.getElementById(id);
const state = { extractor: null, backend: null, dim: null, ready: false };

function setBadge(el, text, cls) {
  el.textContent = text;
  el.className = 'badge' + (cls ? ' ' + cls : '');
}
function log(el, msg) { el.textContent = msg; }

// ---------- 1. 选择推理后端 ----------
async function pickDevice() {
  // 支持 ?force=wasm / ?force=webgpu，便于在同一个浏览器里验证降级路径
  const forced = new URLSearchParams(location.search).get('force');
  if (forced === 'wasm') return { device: 'wasm', reason: 'URL 参数 force=wasm，强制走 WASM 以验证降级路径' };
  if (forced === 'webgpu') return { device: 'webgpu', reason: 'URL 参数 force=webgpu，强制走 WebGPU' };

  const hasGpu = typeof navigator !== 'undefined' && 'gpu' in navigator;
  if (!hasGpu) return { device: 'wasm', reason: '浏览器未提供 navigator.gpu（WebGPU 不可用）' };
  try {
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return { device: 'wasm', reason: '未取到 GPU adapter（显卡/驱动不支持）' };
    return { device: 'webgpu', reason: '已获取 GPU adapter' };
  } catch (e) {
    return { device: 'wasm', reason: 'WebGPU 初始化失败：' + (e.message || e) };
  }
}

// ---------- 2. 加载模型 ----------
async function loadModel() {
  const { device, reason } = await pickDevice();
  state.backend = device;
  const badge = $('badge-backend');
  setBadge(badge, `推理后端：${device === 'webgpu' ? 'WebGPU' : 'WASM（自动降级）'}`, device === 'webgpu' ? 'on' : 'off');
  log($('import-log'), `后端选择：${device}\n原因：${reason}\n模型：${MODEL_ID}\n`);

  setBadge($('badge-model'), '模型：加载中…', 'off');
  const t0 = performance.now();
  const dtype = DTYPE_BY_DEVICE[device] || 'q8';
  try {
    state.extractor = await pipeline('feature-extraction', MODEL_ID, {
      device,               // 'webgpu' | 'wasm'
      dtype,                // WebGPU→fp16，WASM→q8（INT8）
      progress_callback: (p) => {
        if (p.status === 'progress' && p.progress != null) {
          log($('import-log'), `模型下载 ${p.file || ''} ${p.progress.toFixed(0)}%`);
        }
      },
    });
  } catch (e) {
    // WebGPU 路径失败时自动降级 WASM
    if (device === 'webgpu') {
      log($('import-log'), `WebGPU 加载失败，降级 WASM：${e.message || e}`);
      state.backend = 'wasm';
      setBadge($('badge-backend'), '推理后端：WASM（WebGPU 失败后降级）', 'off');
      state.dtype = DTYPE_BY_DEVICE.wasm;
      state.extractor = await pipeline('feature-extraction', MODEL_ID, { device: 'wasm', dtype: DTYPE_BY_DEVICE.wasm });
    } else { throw e; }
  }
  state.dtype = state.dtype || dtype;
  const ms = performance.now() - t0;

  const out = await state.extractor(['预热'], { pooling: POOLING, normalize: true });
  state.dim = out.dims[1];
  state.ready = true;

  setBadge($('badge-model'), `模型：已加载（${state.dim} 维）`, 'on');
  $('st-backend').textContent = state.backend === 'webgpu' ? 'WebGPU' : 'WASM';
  $('st-model').textContent = `${MODEL_ID}（${state.dtype} / ${POOLING} 池化）`;
  $('st-dim').textContent = String(state.dim);
  $('st-batch').textContent = String(BATCH_SIZE);
  log($('import-log'), `模型加载完成：${(ms / 1000).toFixed(2)}s，维度 ${state.dim}`);
  await refreshCount();
}

// ---------- 3. 向量化（本地） ----------
async function embed(texts, isQuery = false) {
  const t0 = performance.now();
  const inputs = isQuery ? texts.map(t => QUERY_INSTRUCTION + t) : texts;
  const out = await state.extractor(inputs, { pooling: POOLING, normalize: true });
  const data = out.tolist();
  const ms = performance.now() - t0;
  $('st-embed').textContent = `${ms.toFixed(0)} ms（${texts.length} 条，${(texts.length / (ms / 1000)).toFixed(1)} 条/秒）`;
  return { vectors: data, ms };
}

// ---------- 4. 导入 ----------
async function addTexts(items, label) {
  if (!state.ready) { log($('import-log'), '模型尚未就绪'); return; }
  if (!items.length) return;

  const bar = $('bar');
  let done = 0, inserted = 0, writeMs = 0, refreshMs = 0;
  const tAll = performance.now();

  for (let i = 0; i < items.length; i += BATCH_SIZE) {
    const batch = items.slice(i, i + BATCH_SIZE);
    const { vectors } = await embed(batch.map(b => b.text));
    const records = batch.map((b, k) => ({
      id: `${Date.now()}_${i + k}`,
      text: b.text,
      vector: vectors[k],
      meta: { source: b.source || label || 'paste', index: i + k },
    }));
    const r = await fetch('/api/docs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ records }),
    });
    const j = await r.json();
    if (!j.ok) { log($('import-log'), '写入失败：' + j.error); return; }
    inserted += j.inserted; writeMs = j.writeMs; refreshMs = j.refreshMs;
    done += batch.length;
    bar.style.width = `${(done / items.length) * 100}%`;
    log($('import-log'), `向量化并写入 ${done}/${items.length} …`);
  }

  const total = ((performance.now() - tAll) / 1000).toFixed(2);
  $('st-write').textContent = `${writeMs.toFixed(0)} ms（刷新索引 ${refreshMs.toFixed(0)} ms）`;
  log($('import-log'), `完成：${inserted} 条，总耗时 ${total}s`);
  await refreshCount();
}

async function refreshCount() {
  try {
    const r = await fetch('/api/health');
    const j = await r.json();
    const n = j.seekdb?.count ?? 0;
    setBadge($('badge-db'), 'seekdb：已连接', 'on');
    setBadge($('badge-count'), `文档：${n}`, n ? 'on' : '');
    $('st-count').textContent = String(n);
  } catch {
    setBadge($('badge-db'), 'seekdb：不可达', 'err');
  }
}

// ---------- 5. 检索 ----------
async function search() {
  const q = $('q').value.trim();
  if (!q) return;
  if (!state.ready) { log($('search-meta'), '模型尚未就绪'); return; }

  const mode = $('mode').value;
  const { vectors } = await embed([q], true);   // 查询向量同样在本地生成
  const t0 = performance.now();
  const r = await fetch('/api/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ vector: vectors[0], text: q, k: 5, mode }),
  });
  const j = await r.json();
  const e2e = performance.now() - t0;

  if (!j.ok) { log($('search-meta'), '检索失败：' + j.error); return; }
  $('st-search').textContent = `${(j.searchMs || 0).toFixed(1)} ms（端到端 ${e2e.toFixed(0)} ms）`;
  log($('search-meta'), `${j.mode} · 命中 ${j.hits.length} 条 · seekdb 检索 ${(j.searchMs || 0).toFixed(1)} ms`);

  const box = $('results');
  box.innerHTML = '';
  if (!j.hits.length) { box.innerHTML = '<p class="hint">没有命中，试试放宽关键词。</p>'; return; }
  j.hits.forEach((h, i) => {
    const d = document.createElement('div');
    d.className = 'hit';
    d.innerHTML =
      `<div><span class="rank">#${i + 1}</span><span class="tagm">${h.meta?.source || 'doc'}</span>` +
      `<span class="tagm">distance ${h.distance == null ? '-' : Number(h.distance).toFixed(4)}</span></div>` +
      `<div class="text">${escapeHtml(h.text).slice(0, 600)}</div>` +
      `<div class="meta">id=${h.id}</div>`;
    box.appendChild(d);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------- 6. 事件绑定 ----------
$('files').addEventListener('change', async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  const items = [];
  for (const f of files) {
    const text = await f.text();
    const chunks = chunk(text);
    chunks.forEach(t => items.push({ text: t, source: f.name }));
  }
  await addTexts(items, 'file');
});

$('btn-paste-toggle').addEventListener('click', () => {
  const w = $('paste-wrap');
  w.hidden = !w.hidden;
});
$('btn-add-paste').addEventListener('click', async () => {
  const t = $('paste').value.trim();
  if (!t) return;
  await addTexts(chunk(t).map(x => ({ text: x, source: 'paste' })), 'paste');
  $('paste').value = '';
});
$('btn-search').addEventListener('click', search);
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
$('btn-reset').addEventListener('click', async () => {
  await fetch('/api/reset', { method: 'POST' });
  await refreshCount();
  $('results').innerHTML = '';
  log($('import-log'), '知识库已清空');
});

// 按空行切段，单段超长再按字数切
function chunk(text, size = 400) {
  const paras = text.split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);
  const out = [];
  for (const p of paras) {
    if (p.length <= size) { out.push(p); continue; }
    for (let i = 0; i < p.length; i += size) out.push(p.slice(i, i + size));
  }
  return out.length ? out : [text.slice(0, size)];
}

// 启动
(async () => {
  await refreshCount();
  try {
    await loadModel();
  } catch (e) {
    log($('import-log'), '模型加载失败：' + (e.message || e));
    setBadge($('badge-model'), '模型：加载失败', 'err');
  }
})();
