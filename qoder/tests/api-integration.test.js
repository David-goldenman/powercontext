// 真实数据库集成测试：需要本地 seekdb（默认 127.0.0.1:2896）在线。
// 运行：npm test （或 node --test tests/api-integration.test.js）
const test = require("node:test");
const assert = require("node:assert");
const { spawn } = require("node:child_process");
const path = require("node:path");
const { getPool, closePool } = require("../src/db");

const SERVER = path.join(__dirname, "..", "src", "server.js");
let child = null;
let port = 0;
const createdIds = [];

function spawnServer(timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    if (child) {
      try { child.kill(); } catch {}
    }
    child = spawn(process.execPath, [SERVER], {
      env: { ...process.env, PORT: "0" },
      cwd: path.join(__dirname, ".."),
    });
    let out = "";
    const timer = setTimeout(() => reject(new Error("服务启动超时。stdout: " + out)), timeoutMs);
    child.stdout.on("data", (d) => {
      out += d.toString();
      const m = out.match(/127\.0\.0\.1:(\d+)/);
      if (m) {
        clearTimeout(timer);
        port = Number(m[1]);
        resolve();
      }
    });
    child.stderr.on("data", (d) => (out += d.toString()));
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (!port) reject(new Error("服务进程退出，code=" + code + "，输出: " + out));
    });
  });
}

const base = () => `http://127.0.0.1:${port}`;
async function j(p, opts) {
  const r = await fetch(base() + p, opts);
  let data = null;
  try { data = await r.json(); } catch {}
  return { status: r.status, data, headers: r.headers };
}
function postNote(body) {
  return j("/api/notes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
function patchNote(id, body) {
  return j("/api/notes/" + id, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const MARK = "evdesk" + Date.now().toString(36);
const NOTE_A = {
  title: `集成测试笔记 ${MARK}`,
  url: "https://arxiv.org/abs/1706.03762",
  content: `这是一条集成测试笔记 ${MARK}，讨论 transformer 注意力机制与残差连接 residual connection，正文片段应出现在证据卡匹配结果里。`,
};

test("启动真实服务进程并连接 seekdb", async () => {
  await spawnServer();
  assert.ok(port > 0);
});

test("health 报告真实 seekdb 版本与专有库名", async () => {
  const { status, data } = await j("/api/health");
  assert.strictEqual(status, 200);
  assert.strictEqual(data.ok, true);
  assert.match(data.version, /seekdb/i);
  assert.strictEqual(data.database, "qoder_evidence");
});

test("导入笔记写入数据库并可列出", async () => {
  const { status, data } = await postNote(NOTE_A);
  assert.strictEqual(status, 201);
  assert.ok(data.note.id);
  createdIds.push(data.note.id);
  assert.strictEqual(data.note.status, "unread");
  assert.strictEqual(data.note.progress, 0);

  const list = await j("/api/notes?limit=500");
  assert.strictEqual(list.status, 200);
  const found = list.data.notes.find((n) => n.id === data.note.id);
  assert.ok(found, "列表应包含刚导入的笔记");
  assert.strictEqual(found.url, NOTE_A.url);
});

test("检索返回带真实来源 URL 与匹配片段的证据卡", async () => {
  const { status, data } = await j("/api/search?q=" + encodeURIComponent(MARK + " 注意力"));
  assert.strictEqual(status, 200);
  assert.ok(data.terms.length >= 2);
  const card = data.cards.find((c) => c.title.includes(MARK));
  assert.ok(card, "证据卡应命中导入的笔记");
  assert.strictEqual(card.url, NOTE_A.url, "证据卡必须携带笔记里的原始来源 URL");
  assert.ok(card.snippet.includes(MARK), "片段应包含命中关键词的原文");
  assert.ok(card.matchedTerms.includes(MARK.toLowerCase()));
  assert.strictEqual(card.status, "unread");
});

test("输入校验：非法 URL / 空标题 / 过短正文返回 400", async () => {
  let r = await postNote({ title: "t", url: "ftp://x", content: "abcdefghij" });
  assert.strictEqual(r.status, 400);
  r = await postNote({ title: "", url: "https://x", content: "abcdefghij" });
  assert.strictEqual(r.status, 400);
  r = await postNote({ title: "t", url: "https://x", content: "短" });
  assert.strictEqual(r.status, 400);
});

test("进度校验：非法状态与越界百分比返回 400，未知 id 返回 404", async () => {
  const { data } = await postNote({ ...NOTE_A, title: NOTE_A.title + "-B" });
  createdIds.push(data.note.id);
  let r = await patchNote(data.note.id, { status: "finished" });
  assert.strictEqual(r.status, 400);
  r = await patchNote(data.note.id, { progress: 101 });
  assert.strictEqual(r.status, 400);
  r = await patchNote(data.note.id, { progress: -1 });
  assert.strictEqual(r.status, 400);
  r = await j("/api/notes/00000000-0000-4000-8000-000000000000");
  assert.strictEqual(r.status, 404);
});

test("SQL 注入式输入被当作字面量，表结构不受影响", async () => {
  const evil = {
    title: `evil'); DROP TABLE notes; --${MARK}`,
    url: "https://example.com/SQL_injection_probe",
    content: `攻击载荷字面量测试 DROP TABLE notes 应被参数化查询安全存储与检索 ${MARK}`,
  };
  const { status, data } = await postNote(evil);
  assert.strictEqual(status, 201);
  createdIds.push(data.note.id);

  // 恶意字符串原样存回
  const got = await j("/api/notes/" + data.note.id);
  assert.strictEqual(got.data.note.title, evil.title);
  assert.ok(got.data.note.content.includes("DROP TABLE notes"));

  // 用注入串做检索：只应作为普通词处理，不得破坏数据库
  const search = await j("/api/search?q=" + encodeURIComponent("DROP TABLE notes"));
  assert.strictEqual(search.status, 200);

  // 表仍然存在且可正常查询
  const list = await j("/api/notes?limit=500");
  assert.strictEqual(list.status, 200);
  assert.ok(list.data.notes.some((n) => n.id === data.note.id));
});

test("LIKE 通配符与百分号输入不会泄漏全库内容", async () => {
  const r = await j("/api/search?q=" + encodeURIComponent("%_%"));
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.data.cards, [], "通配符应被转义为字面量，无命中");
});

test("候选超过单页时游标分页全量扫描：位于后页的最佳条目不得丢失（回归：无序 LIMIT 截断）", async () => {
  const pool = getPool();
  const BULK_URL = `https://example.com/bulk-probe-${MARK}`;
  const WORD = "bulkprobe" + MARK;
  const pad = (s) => (s + "0".repeat(36)).slice(0, 36);

  // 205 条普通候选：命中词各出现一次；id 前缀 "fill"，十六进制 uuid 均小于它
  for (let i = 0; i < 205; i++) {
    const id = pad("fill" + MARK + String(i).padStart(3, "0"));
    await pool.execute(
      "INSERT INTO notes (id, title, url, content, status, progress) VALUES (?, ?, ?, ?, 'unread', 0)",
      [id, `批量候选 ${i}`, BULK_URL, `普通候选条目编号 ${i}，仅出现一次 ${WORD} 用于游标分页回归 filler`]
    );
  }
  // 最佳条目：id 前缀 "z"，按 id 升序必然落在最后一页；标题 3 次 + 正文 4 次命中
  const bestId = pad("zbest" + MARK);
  await pool.execute(
    "INSERT INTO notes (id, title, url, content, status, progress) VALUES (?, ?, ?, ?, 'unread', 0)",
    [bestId, `最佳 ${WORD} 命中 ${WORD} 排名 ${WORD}`, BULK_URL,
      `${WORD} 详细论证一 ${WORD} 详细论证二 ${WORD} 详细论证三 ${WORD} 详细论证四，本条应排名首位`]
  );

  try {
    const r = await j("/api/search?q=" + encodeURIComponent(WORD));
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.data.matchedCandidates, 206, "必须统计到全部 206 条候选，而非只扫第一页");
    assert.ok(r.data.scanned >= 206);
    assert.strictEqual(r.data.cards[0].id, bestId, "后页最佳条目必须出现在结果首位");
    assert.ok(r.data.cards[0].score > r.data.cards[1].score);
    assert.strictEqual(r.data.truncated, true, "候选数超过返回上限时必须声明截断");
    assert.ok(r.data.cards.length <= 50);

    const after = await j("/api/notes?limit=500");
    assert.strictEqual(after.status, 200);
  } finally {
    // 只清理本测试用标记 URL 创建的行
    await pool.execute("DELETE FROM notes WHERE url = ?", [BULK_URL]);
  }
  const clean = await j("/api/search?q=" + encodeURIComponent(WORD));
  assert.strictEqual(clean.data.cards.length, 0, "测试数据必须清理干净");
});

test("列表 limit 参数钳制为整数：1.5/Infinity/-3/abc/0 均返回 200 而非 SQL 语法错误", async () => {
  for (const bad of ["1.5", "Infinity", "-3", "abc", "0", "1e99"]) {
    const r = await j("/api/notes?limit=" + bad);
    assert.strictEqual(r.status, 200, "limit=" + bad + " 应被钳制而非报错，实际: " + r.status);
    assert.ok(Array.isArray(r.data.notes));
    assert.ok(r.data.notes.length <= 500);
  }
});

test("阅读进度更新后，杀掉服务进程重新拉起仍然保留（真实重启持久化）", async () => {
  const { data } = await postNote({ ...NOTE_A, title: NOTE_A.title + "-持久化" });
  const id = data.note.id;
  createdIds.push(id);

  let r = await patchNote(id, { status: "reading", progress: 40 });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.note.status, "reading");
  assert.strictEqual(r.data.note.progress, 40);

  const oldPort = port;
  await spawnServer(); // 内部会先 kill 旧进程，全新进程与连接池
  assert.ok(port > 0 && oldPort > 0);

  const after = await j("/api/notes/" + id);
  assert.strictEqual(after.status, 200);
  assert.strictEqual(after.data.note.status, "reading", "重启后状态必须来自数据库");
  assert.strictEqual(after.data.note.progress, 40, "重启后进度必须来自数据库");

  r = await patchNote(id, { status: "read", progress: 100 });
  assert.strictEqual(r.status, 200);
  const done = await j("/api/notes/" + id);
  assert.strictEqual(done.data.note.status, "read");
});

test("状态过滤：按 status 查询只返回对应状态笔记", async () => {
  const r = await j("/api/notes?status=read");
  assert.strictEqual(r.status, 200);
  assert.ok(r.data.notes.length > 0);
  assert.ok(r.data.notes.every((n) => n.status === "read"));
});

test("静态页面可访问：展示页 / 应用页 / 样例 JSON", async () => {
  const home = await fetch(base() + "/");
  assert.strictEqual(home.status, 200);
  const homeText = await home.text();
  assert.ok(homeText.includes("科研证据卡"));
  assert.ok(homeText.includes("模拟"), "展示页必须标注内置演示为模拟环境");
  assert.ok(homeText.includes("github.com/David-goldenman/powercontext"));

  const app = await fetch(base() + "/app");
  assert.strictEqual(app.status, 200);
  assert.ok((await app.text()).includes("真实 seekdb 后端"));

  const samples = await j("/samples/sample-notes.json");
  assert.strictEqual(samples.status, 200);
});

test("路径穿越请求不会泄露 public 之外的文件", async () => {
  const r = await fetch(base() + "/..%2F..%2Fpackage.json");
  assert.ok(r.status === 403 || r.status === 404, "实际状态码: " + r.status);
});

test("清理测试数据并关闭服务", async () => {
  for (const id of createdIds) {
    const r = await j("/api/notes/" + id, { method: "DELETE" });
    assert.strictEqual(r.status, 200);
  }
  assert.ok(createdIds.length >= 3);
});

test.after(async () => {
  if (child) {
    try { child.kill(); } catch {}
    child = null;
  }
  await closePool().catch(() => {}); // 关闭测试进程自己创建的第二个连接池
});
