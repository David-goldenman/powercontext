const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { initSchema, closePool } = require("../src/db");

async function main() {
  const file = path.join(__dirname, "..", "public", "samples", "sample-notes.json");
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  const pool = await initSchema();
  let inserted = 0;
  for (const n of data.notes) {
    const id = crypto.randomUUID();
    await pool.execute(
      "INSERT INTO notes (id, title, url, content, status, progress) VALUES (?, ?, ?, ?, 'unread', 0)",
      [id, n.title, n.url, n.content]
    );
    inserted++;
  }
  console.log(`已导入 ${inserted} 条样例笔记到 qoder_evidence.notes（重复运行会追加新行）。`);
  await closePool();
}

main().catch((e) => {
  console.error("seed 失败:", e.message);
  process.exit(1);
});
