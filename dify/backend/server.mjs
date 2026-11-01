// 离谱发明工坊 — seekdb 数据层 HTTP 服务
// Dify 通过 HTTP Request 节点调用：/lookup 查历史、/save 存新发明、/stats 看图鉴
import { createServer } from 'node:http';
import { connect, findByPair, searchSimilar, insert, count, stats, listAll, truncate } from './seekdb.mjs';

const PORT = Number(process.env.PORT || 3000);

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  });
  res.end(body);
}

function readBody(req, limit = 1 << 20) {
  return new Promise((resolve, reject) => {
    let n = 0; const chunks = [];
    req.on('data', c => {
      n += c.length;
      if (n > limit) { reject(new Error('body too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) return resolve({});
      try { resolve(JSON.parse(raw)); } catch { reject(new Error('invalid json')); }
    });
    req.on('error', reject);
  });
}

const clean = s => String(s ?? '').trim().replace(/\s+/g, ' ').slice(0, 40);

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const path = url.pathname.replace(/\/+$/, '') || '/';

  if (req.method === 'OPTIONS') return json(res, 204, {});

  try {
    if (req.method === 'GET' && path === '/health') {
      return json(res, 200, { ok: true, table: 'fusions', total: await count() });
    }

    if (req.method === 'POST' && path === '/lookup') {
      const b = await readBody(req);
      const a = clean(b.a), c = clean(b.b);
      if (!a || !c) return json(res, 400, { error: '需要 a 和 b 两件物品' });

      const exact = await findByPair(a, c);
      const similar = (await searchSimilar(a, c, 4))
        .filter(x => !(exact && x.name === exact.name))
        .slice(0, 3);

      return json(res, 200, {
        input: `${a} + ${c}`,
        exact_hit: !!exact,
        exact: exact ? {
          name: exact.name,
          effect: exact.effect,
          side_effect: exact.side_effect,
          rarity: exact.rarity,
          creator: exact.creator,
          created_at: exact.created_at,
        } : null,
        similar,
      });
    }

    if (req.method === 'POST' && path === '/save') {
      const b = await readBody(req);
      const a = clean(b.a), c = clean(b.b), name = clean(b.name);
      if (!a || !c || !name) return json(res, 400, { error: '需要 a、b、name' });

      const dup = await findByPair(a, c);
      if (dup) {
        return json(res, 200, { saved: false, reason: 'duplicate', existing: { name: dup.name, effect: dup.effect }, total: await count() });
      }
      await insert({
        a, b: c, name,
        effect: String(b.effect || '').slice(0, 300),
        side_effect: String(b.side_effect || '').slice(0, 300),
        rarity: clean(b.rarity) || '普通',
        creator: clean(b.creator) || '匿名发明家',
      });
      return json(res, 200, { saved: true, name, total: await count() });
    }

    if (req.method === 'GET' && path === '/stats') return json(res, 200, await stats());
    if (req.method === 'GET' && path === '/list') return json(res, 200, { items: await listAll(Number(url.searchParams.get('limit') || 100)) });

    if (req.method === 'POST' && path === '/reset') {
      await truncate();
      return json(res, 200, { reset: true, total: await count() });
    }

    return json(res, 404, { error: 'not found', path });
  } catch (e) {
    console.error('[error]', path, e);
    return json(res, 500, { error: String((e && e.message) || e) });
  }
});

connect()
  .then(async () => {
    const total = await count();
    server.listen(PORT, '0.0.0.0', () => {
      console.log(`[fusion-api] :${PORT}  seekdb=${process.env.SEEKDB_HOST || '127.0.0.1'}:${process.env.SEEKDB_PORT || 2881}  fusions=${total}`);
    });
  })
  .catch(e => { console.error('[fusion-api] seekdb 连接失败：', e); process.exit(1); });
