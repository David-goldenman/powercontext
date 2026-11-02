const fs = require("fs");
const path = require("path");

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/i);
    if (!m) continue;
    if (process.env[m[1]] === undefined) {
      process.env[m[1]] = m[2].trim().replace(/^"(.*)"$/, "$1");
    }
  }
}

loadDotEnv(path.join(__dirname, "..", ".env"));

const config = {
  host: process.env.DB_HOST || "127.0.0.1",
  port: Number(process.env.DB_PORT || 2896),
  user: process.env.DB_USER || "root",
  password: process.env.DB_PASSWORD || "",
  // 本项目专有数据库，代码保证只创建/修改该库，绝不触碰其他数据库。
  database: process.env.DB_NAME || "qoder_evidence",
  httpPort: Number(process.env.PORT || 3210),
};

if (!/^[A-Za-z0-9_]+$/.test(config.database)) {
  throw new Error("DB_NAME 只允许字母/数字/下划线");
}

module.exports = { config };
