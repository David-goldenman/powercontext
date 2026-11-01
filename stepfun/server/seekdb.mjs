// seekdb 访问层：server 模式，向量由客户端计算后传入
import { SeekdbClient, Schema, VectorIndexConfig, FulltextIndexConfig } from 'seekdb';

const HOST = process.env.SEEKDB_HOST || '127.0.0.1';
const PORT = Number(process.env.SEEKDB_PORT || 2881);
const USER = process.env.SEEKDB_USER || 'root';
const PASS = process.env.SEEKDB_PASS || '';
const DB = process.env.SEEKDB_DB || 'test';
export const COLLECTION = process.env.SEEKDB_COLLECTION || 'kb_docs';

let client = null;

export async function getClient() {
  if (client) return client;
  client = new SeekdbClient({ host: HOST, port: PORT, user: USER, password: PASS, database: DB });
  // 触发一次真实查询，确认可达
  await client.execute('select 1 as ok');
  return client;
}

export async function getCollection(dim) {
  const c = await getClient();
  const exists = await c.hasCollection(COLLECTION);
  if (exists) {
    const coll = await c.getCollection({ name: COLLECTION, embeddingFunction: null });
    return coll;
  }
  const schema = new Schema({
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension: dim, distance: 'l2', m: 16, ef_construction: 200, ef_search: 64 },
    }),
    fulltextIndex: new FulltextIndexConfig('ngram2', { min_ngram_size: 1, max_ngram_size: 2 }),
  });
  // embeddingFunction: null —— 向量由浏览器端计算，数据库只负责存储与检索
  return c.createCollection({ name: COLLECTION, schema, embeddingFunction: null });
}

export async function dropCollection() {
  const c = await getClient();
  if (await c.hasCollection(COLLECTION)) await c.deleteCollection(COLLECTION);
}

export async function stats() {
  const c = await getClient();
  if (!(await c.hasCollection(COLLECTION))) return { exists: false, count: 0 };
  const coll = await c.getCollection({ name: COLLECTION, embeddingFunction: null });
  return { exists: true, count: await coll.count(), name: COLLECTION };
}

export async function close() {
  if (client) { await client.close(); client = null; }
}
