// 数据库故障行为测试：证明后端明确报错而不是伪造结果。无需 seekdb 在线。
const test = require("node:test");
const assert = require("node:assert");
const { spawn } = require("node:child_process");
const path = require("node:path");

const SERVER = path.join(__dirname, "..", "src", "server.js");
const DEAD_PORT = "1"; // 本机保留端口，必然连接失败

test("服务启动时数据库不可用：进程以非零退出码失败，不提供服务", async () => {
  const { code, output } = await new Promise((resolve) => {
    const child = spawn(process.execPath, [SERVER], {
      env: { ...process.env, DB_PORT: DEAD_PORT, PORT: "0" },
      cwd: path.join(__dirname, ".."),
    });
    let output = "";
    child.stdout.on("data", (d) => (output += d));
    child.stderr.on("data", (d) => (output += d));
    // 兜底看门狗：仅约束本测试自己 spawn 的 child；child 正常退出时必须清掉，
    // 否则成功路径也会把测试进程拖住 30 秒（回归点）。
    const watchdog = setTimeout(() => {
      try { child.kill(); } catch {}
      resolve({ code: -99, output });
    }, 30000);
    watchdog.unref && watchdog.unref();
    child.on("exit", (code) => {
      clearTimeout(watchdog);
      resolve({ code, output });
    });
  });
  assert.notStrictEqual(code, 0, "启动必须失败，实际退出码: " + code);
  assert.match(output, /启动失败/, "实际输出: " + output);
});

test("运行中的服务遇到死库：API 返回 500 与真实错误信息，绝不返回 ok:true 或假数据", async () => {
  process.env.DB_PORT = DEAD_PORT;
  const { createServer } = require("../src/server");
  const server = createServer();
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  try {
    const h = await fetch(`http://127.0.0.1:${port}/api/health`);
    assert.strictEqual(h.status, 500);
    const hj = await h.json();
    assert.ok(hj.error && typeof hj.error === "string", "必须携带 error 字段");
    assert.ok(!hj.ok, "不得返回 ok:true 假成功");

    const s = await fetch(`http://127.0.0.1:${port}/api/search?q=%E6%B5%8B%E8%AF%95`);
    assert.strictEqual(s.status, 500);
    const sj = await s.json();
    assert.ok(sj.error, "检索失败必须报错而不是返回空 cards 冒充正常");
    assert.strictEqual(sj.cards, undefined);

    const n = await fetch(`http://127.0.0.1:${port}/api/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "t", url: "https://a.com", content: "0123456789ab" }),
    });
    assert.strictEqual(n.status, 500);
    const nj = await n.json();
    assert.ok(nj.error);
  } finally {
    await new Promise((r) => server.close(r));
    const { closePool } = require("../src/db");
    await closePool().catch(() => {});
  }
});
