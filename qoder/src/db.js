const mysql = require("mysql2/promise");
const { config } = require("./config");

let pool = null;

function makePool(overrides = {}) {
  return mysql.createPool({
    host: overrides.host || config.host,
    port: overrides.port || config.port,
    user: overrides.user || config.user,
    password: overrides.password !== undefined ? overrides.password : config.password,
    database: overrides.database || config.database,
    waitForConnections: true,
    connectionLimit: 5,
    connectTimeout: 5000,
  });
}

function getPool() {
  if (!pool) pool = makePool();
  return pool;
}

/**
 * 只创建/修改本项目专有数据库 qoder_evidence，并建表。
 * 连接或建库失败时直接抛错，调用方必须把错误返回给客户端，不得伪造数据。
 */
async function initSchema(overrides = {}) {
  const rootConn = await mysql.createConnection({
    host: overrides.host || config.host,
    port: overrides.port || config.port,
    user: overrides.user || config.user,
    password: overrides.password !== undefined ? overrides.password : config.password,
    connectTimeout: 5000,
    multipleStatements: false,
  });
  try {
    const dbName = overrides.database || config.database;
    await rootConn.query(
      `CREATE DATABASE IF NOT EXISTS \`${dbName}\` DEFAULT CHARACTER SET utf8mb4`
    );
  } finally {
    await rootConn.end();
  }
  const p = getPool();
  await p.query(`
    CREATE TABLE IF NOT EXISTS notes (
      id CHAR(36) PRIMARY KEY,
      title VARCHAR(500) NOT NULL,
      url VARCHAR(2000) NOT NULL,
      content MEDIUMTEXT NOT NULL,
      status VARCHAR(16) NOT NULL DEFAULT 'unread',
      progress INT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      INDEX idx_notes_status (status),
      INDEX idx_notes_title (title(64))
    ) DEFAULT CHARACTER SET utf8mb4
  `);
  return p;
}

async function closePool() {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

module.exports = { getPool, initSchema, closePool, makePool };
