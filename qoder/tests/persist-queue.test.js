// 回归：阅读进度防抖保存在快速关闭/切换 A→B/同笔记并发下的行为。
// 对应缺陷：800ms 防抖窗口内关闭阅读器 reading=null 丢进度；切到 B 时 A 的定时器把补丁存成"B 保存成功"。
const test = require("node:test");
const assert = require("node:assert");
const createPersistQueue = require("../public/persist-queue");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test("防抖窗口内两次 schedule 合并为一次保存，且保存最新快照", async () => {
  const saves = [];
  const q = createPersistQueue(async (id, patch) => { saves.push([id, patch]); }, { debounceMs: 20 });
  q.schedule("A", { status: "reading", progress: 10 });
  q.schedule("A", { progress: 60 });
  await sleep(60);
  assert.strictEqual(saves.length, 1);
  assert.strictEqual(saves[0][0], "A");
  assert.deepStrictEqual(saves[0][1], { status: "reading", progress: 60 });
});

test("关闭时 flush 后，旧定时器不得触发第二次保存（回归：立即关闭丢进度）", async () => {
  const saves = [];
  const q = createPersistQueue(async (id, patch) => { saves.push([id, patch]); }, { debounceMs: 30 });
  q.schedule("A", { status: "reading", progress: 40 });
  await q.flush("A"); // 模拟关闭阅读器前强制落库
  assert.strictEqual(saves.length, 1);
  assert.deepStrictEqual(saves[0][1], { status: "reading", progress: 40 });
  await sleep(80); // 原定时器到点：不得再有保存
  assert.strictEqual(saves.length, 1, "flush 后定时器必须被清理");
});

test("A→B 快速切换：各笔记补丁按 id 归属，不得互相冒领（回归：A 的 timer 当成 B）", async () => {
  const saves = [];
  const done = [];
  const q = createPersistQueue(async (id, patch) => { saves.push([id, patch]); }, {
    debounceMs: 25,
    onSaved: (id) => done.push(id),
  });
  q.schedule("A", { status: "reading", progress: 33 });
  await q.flush("A"); // openReader(B) 前 flush 旧笔记
  q.schedule("B", { status: "reading", progress: 70 });
  await q.flush("B");
  assert.deepStrictEqual(saves, [
    ["A", { status: "reading", progress: 33 }],
    ["B", { status: "reading", progress: 70 }],
  ]);
  assert.deepStrictEqual(done, ["A", "B"], "onSaved 必须携带正确 id，UI 只能给对应笔记提示成功");
});

test("同一笔记并发 flush 串行执行：saveFn 不重入，顺序与补丁一致", async () => {
  let active = 0;
  let overlaps = 0;
  const order = [];
  const q = createPersistQueue(async (id, patch) => {
    active++;
    if (active > 1) overlaps++;
    await sleep(25);
    order.push(patch.progress);
    active--;
  }, { debounceMs: 25 });
  q.schedule("A", { progress: 40 });
  const p1 = q.flush("A");
  q.schedule("A", { progress: 75 });
  const p2 = q.flush("A"); // 第一次保存仍在进行中
  await Promise.all([p1, p2]);
  assert.strictEqual(overlaps, 0, "同一 id 的保存必须串行，不得并发写库");
  assert.deepStrictEqual(order, [40, 75]);
});

test("保存失败回调携带正确 id 与错误，队列后续仍可保存", async () => {
  const fails = [];
  const saves = [];
  let boom = true;
  const q = createPersistQueue(async (id, patch) => {
    if (boom) { boom = false; throw new Error("数据库不可用"); }
    saves.push([id, patch]);
  }, { debounceMs: 10, onFailed: (id, e) => fails.push([id, e.message]) });
  await q.schedule("A", { progress: 20 });
  await q.flush("A");
  assert.strictEqual(fails.length, 1);
  assert.strictEqual(fails[0][0], "A");
  assert.match(fails[0][1], /数据库不可用/);
  q.schedule("A", { progress: 55 });
  await q.flush("A");
  assert.strictEqual(saves.length, 1);
  assert.deepStrictEqual(saves[0][1], { progress: 55 });
});

test("flush 未知/无待保存 id：不产生保存，返回可等待的 Promise", async () => {
  let count = 0;
  const q = createPersistQueue(async () => { count++; }, { debounceMs: 10 });
  const p = q.flush("ghost");
  assert.ok(p instanceof Promise);
  await p;
  assert.strictEqual(count, 0);
  assert.strictEqual(q.hasPending("ghost"), false);
});
