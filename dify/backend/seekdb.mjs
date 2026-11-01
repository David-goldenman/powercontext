// seekdb 数据层：发明图鉴的存储、查重与全文检索（MySQL 协议 + ngram 全文索引）
import mysql from 'mysql2/promise';

const DB = process.env.SEEKDB_DB || 'fusion';
const TABLE = 'fusions';

export const pool = { p: null };

function cfg(withDb = true) {
  return {
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(process.env.SEEKDB_PORT || 2881),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '',
    database: withDb ? DB : undefined,
    charset: 'utf8mb4',
    waitForConnections: true,
    connectionLimit: 8,
    namedPlaceholders: false,
  };
}

export async function connect() {
  // 1. 先在不指定库的连接上建库（seekdb 的 createDatabase 需要能连上目标库，故不能用 SDK 的便捷方法）
  const root = await mysql.createConnection(cfg(false));
  await root.query(`CREATE DATABASE IF NOT EXISTS \`${DB}\` DEFAULT CHARACTER SET utf8mb4`);
  await root.end();

  // 2. 建表：ngram 全文索引 + 两个结构化索引
  pool.p = mysql.createPool(cfg(true));
  await pool.p.query(`
    CREATE TABLE IF NOT EXISTS ${TABLE} (
      id VARCHAR(64) PRIMARY KEY,
      item_a VARCHAR(40) NOT NULL,
      item_b VARCHAR(40) NOT NULL,
      name VARCHAR(80) NOT NULL,
      effect VARCHAR(400) NOT NULL,
      side_effect VARCHAR(400) NOT NULL DEFAULT '',
      rarity VARCHAR(10) NOT NULL DEFAULT '普通',
      creator VARCHAR(40) NOT NULL DEFAULT '匿名发明家',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FULLTEXT KEY ft_content (name, effect) WITH PARSER ngram,
      KEY idx_a (item_a),
      KEY idx_b (item_b)
    ) DEFAULT CHARSET = utf8mb4
  `);
  return pool.p;
}

const q = async (sql, args = []) => {
  const [rows] = await pool.p.query(sql, args);
  return rows;
};

/** 精确查重：同一对物品（不区分先后）是否已被发明过 */
export async function findByPair(a, b) {
  const rows = await q(
    `SELECT id, item_a, item_b, name, effect, side_effect, rarity, creator, created_at
       FROM ${TABLE}
      WHERE (item_a = ? AND item_b = ?) OR (item_a = ? AND item_b = ?)
      LIMIT 1`,
    [a, b, b, a],
  );
  return rows[0] || null;
}

/**
 * 相似检索：结构化命中（任一物品相同）优先，其次靠 ngram 全文相关性召回效果描述相近的发明
 * 这正是 seekdb「向量/全文 + 结构化条件」里后两者的组合用法
 */
export async function searchSimilar(a, b, limit = 3) {
  const kw = [a, b].filter(Boolean).join(' ');
  const rows = await q(
    `SELECT id, item_a, item_b, name, effect, rarity,
            (CASE WHEN item_a IN (?, ?) THEN 1 ELSE 0 END)
          + (CASE WHEN item_b IN (?, ?) THEN 1 ELSE 0 END) AS struct_score,
            MATCH(name, effect) AGAINST(? IN NATURAL LANGUAGE MODE) AS ft_score
       FROM ${TABLE}
      WHERE item_a IN (?, ?) OR item_b IN (?, ?)
            OR MATCH(name, effect) AGAINST(? IN NATURAL LANGUAGE MODE)
      ORDER BY struct_score DESC, ft_score DESC, created_at DESC
      LIMIT ?`,
    [a, b, a, b, kw, a, b, a, b, kw, limit],
  );
  return rows.map(r => ({
    name: r.name,
    combo: `${r.item_a} + ${r.item_b}`,
    effect: r.effect,
    rarity: r.rarity,
    struct_score: Number(r.struct_score),
    ft_score: Number(r.ft_score),
  }));
}

export async function insert(rec) {
  const id = rec.id || `f_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  await q(
    `INSERT INTO ${TABLE} (id, item_a, item_b, name, effect, side_effect, rarity, creator, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))`,
    [id, rec.a, rec.b, rec.name, rec.effect || '',
      rec.side_effect || '', rec.rarity || '普通',
      rec.creator || '匿名发明家', rec.created_at || null],
  );
  return id;
}

export async function count() {
  const rows = await q(`SELECT COUNT(*) AS n FROM ${TABLE}`);
  return Number(rows[0].n);
}

export async function stats() {
  const total = await count();
  const byRarity = await q(
    `SELECT rarity, COUNT(*) AS n FROM ${TABLE} GROUP BY rarity ORDER BY n DESC`,
  );
  const latest = await q(
    `SELECT name, item_a, item_b, rarity, creator, created_at
       FROM ${TABLE} ORDER BY created_at DESC, id DESC LIMIT 5`,
  );
  return {
    total,
    by_rarity: byRarity.map(r => ({ rarity: r.rarity, count: Number(r.n) })),
    latest: latest.map(r => ({
      name: r.name,
      combo: `${r.item_a} + ${r.item_b}`,
      rarity: r.rarity,
      creator: r.creator,
      created_at: r.created_at,
    })),
  };
}

export async function listAll(limit = 200) {
  return q(
    `SELECT id, item_a, item_b, name, effect, side_effect, rarity, creator, created_at
       FROM ${TABLE} ORDER BY created_at DESC, id DESC LIMIT ?`,
    [limit],
  );
}

export async function truncate() {
  await q(`TRUNCATE TABLE ${TABLE}`);
}
