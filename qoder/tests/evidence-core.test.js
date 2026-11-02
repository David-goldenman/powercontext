const test = require("node:test");
const assert = require("node:assert");
const E = require("../public/evidence-core");

test("tokenizeTerms：过滤停用词与短词、去重、限量", () => {
  assert.deepStrictEqual(E.tokenizeTerms(""), []);
  assert.deepStrictEqual(E.tokenizeTerms("的 了 a"), []);
  assert.deepStrictEqual(E.tokenizeTerms("Attention attention  残差"), ["attention", "残差"]);
  assert.ok(E.tokenizeTerms("a1 b2 c3 d4 e5 f6 g7 h8 i9").length <= 8);
});

test("makeSnippet：命中词周围截取并加省略号", () => {
  const text = "X".repeat(100) + "关键结论在中间" + "Y".repeat(100);
  const { snippet, matchedTerm } = E.makeSnippet(text, ["关键结论"], 20);
  assert.strictEqual(matchedTerm, "关键结论");
  assert.ok(snippet.startsWith("…"));
  assert.ok(snippet.endsWith("…"));
  assert.ok(snippet.includes("关键结论"));
});

test("makeSnippet：未命中时返回开头文本且 matchedTerm 为 null", () => {
  const { snippet, matchedTerm } = E.makeSnippet("hello world something", ["zzz"], 5);
  assert.strictEqual(matchedTerm, null);
  assert.ok(snippet.startsWith("hello"));
});

test("searchNotes：标题命中权重高于正文，按得分降序", () => {
  const notes = [
    { id: "1", title: "普通笔记", url: "https://a", content: "提到 transformer 一次", status: "unread", progress: 0 },
    { id: "2", title: "Transformer 架构详解", url: "https://b", content: "正文也提到 transformer", status: "reading", progress: 30 },
  ];
  const cards = E.searchNotes(notes, "transformer");
  assert.strictEqual(cards.length, 2);
  assert.strictEqual(cards[0].id, "2", "标题命中应排前");
  assert.ok(cards[0].score > cards[1].score);
});

test("searchNotes：证据卡携带真实来源 URL 与匹配片段、匹配词", () => {
  const notes = [
    { id: "x", title: "扩散模型", url: "https://arxiv.org/abs/2006.11239", content: "前向过程按余弦噪声调度逐步向数据加高斯噪声", status: "unread", progress: 0 },
  ];
  const [card] = E.searchNotes(notes, "噪声调度");
  assert.strictEqual(card.url, "https://arxiv.org/abs/2006.11239");
  assert.ok(card.snippet.includes("噪声调度"));
  assert.deepStrictEqual(card.matchedTerms, ["噪声调度"]);
  assert.strictEqual(card.status, "unread");
});

test("searchNotes：空查询或全停用词查询返回空数组", () => {
  const notes = [{ id: "1", title: "t", url: "u", content: "c" }];
  assert.deepStrictEqual(E.searchNotes(notes, ""), []);
  assert.deepStrictEqual(E.searchNotes(notes, "的了和"), []);
});

test("searchNotes：大小写不敏感", () => {
  const notes = [{ id: "1", title: "ABC Def", url: "u", content: "text with GgTorch" }];
  const cards = E.searchNotes(notes, "ggtorch");
  assert.strictEqual(cards.length, 1);
});
