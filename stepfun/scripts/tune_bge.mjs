// 决定 bge-small-zh-v1.5 的最佳用法：池化方式 × 查询指令前缀
import { pipeline, env } from '@huggingface/transformers';
import path from 'node:path';

const ROOT = 'D:/ob-race/stepfun';
env.allowLocalModels = true;
env.allowRemoteModels = false;
env.localModelPath = path.join(ROOT, 'models');
env.backends.onnx.logLevel = 'error';

const MODEL = 'Xenova/bge-small-zh-v1.5';
const INSTR = '为这个句子生成表示以用于检索相关文章：';

const CORPUS = [
  '星巴克西湖店，环境安静，二楼有插座，适合带电脑看书或写代码，步行约3分钟。',
  '独立咖啡馆，主打手冲咖啡，店内只有三张桌子，声音较杂，周末经常排队，步行约8分钟。',
  '城市公园，面积大，有环湖步道，适合散步和慢跑，步行约12分钟。',
  '市图书馆自习室，非常安静，需要刷身份证进入，座位紧张，步行约15分钟。',
  '小面馆，人均20元，上菜快，中午高峰需要等位，步行约6分钟。',
  'WebGPU 后端在支持的设备上比 WASM 快数倍，不支持时需要自动降级。',
];
const QUERIES = [
  { q: '哪里适合安静看书', gold: [0, 3] },
  { q: '附近有咖啡馆吗', gold: [0, 1] },
  { q: '可以散步的地方', gold: [2] },
  { q: '便宜又快的吃饭地方', gold: [4] },
  { q: '浏览器里推理快不快', gold: [5] },
];

const cos = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };
const topK = (vs, q, k) => vs.map((v, i) => ({ i, s: cos(v, q) })).sort((x, y) => y.s - x.s).slice(0, k).map(x => x.i);

(async () => {
  const ex = await pipeline('feature-extraction', MODEL, { dtype: 'q8', device: 'cpu' });

  for (const pooling of ['mean', 'cls']) {
    const dv = (await ex(CORPUS, { pooling, normalize: true })).tolist();
    for (const useInstr of [false, true]) {
      let hit1 = 0, hit3 = 0;
      const detail = [];
      for (const it of QUERIES) {
        const text = useInstr ? INSTR + it.q : it.q;
        const qv = (await ex([text], { pooling, normalize: true })).tolist()[0];
        const t1 = topK(dv, qv, 1), t3 = topK(dv, qv, 3);
        if (it.gold.includes(t1[0])) hit1++;
        if (t3.some(x => it.gold.includes(x))) hit3++;
        detail.push(`${it.q}->${t1[0]}`);
      }
      console.log(`pooling=${pooling.padEnd(4)} instr=${useInstr ? 'Y' : 'N'} | Top1 ${hit1}/${QUERIES.length}  Top3 ${hit3}/${QUERIES.length} | ${detail.join(' ')}`);
    }
  }
  await ex.dispose?.();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
