"use strict";

// Execute the delivered browser scripts and HTML. Only the HTTP boundary is
// replaced; real rendering/layout and seekdb are covered by the browser check.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { parseHTML } = require("linkedom");
const publicDir = path.join(__dirname, "..", "public");

function response(status, data) {
  return { status, ok: status >= 200 && status < 300, json: async () => data };
}

function fixture(t, { delayedHealth = false, initialMode = "ok" } = {}) {
  const { document } = parseHTML(fs.readFileSync(path.join(publicDir, "app.html"), "utf8"));
  const timers = new Set();
  const state = { mode: initialMode, missing: false, failPatch: false, alerts: [], patches: [] };
  const note = { id: "note-a", title: "Alpha note", url: "https://example.test/a",
    content: "alpha evidence for a frontend regression", status: "unread", progress: 0,
    created_at: "2026-01-01", updated_at: "2026-01-01" };
  const health = { ok: true, database: "test_db", version: "test-only" };
  let releaseHealth;
  const oldHealth = new Promise((resolve) => {
    releaseHealth = (status = 200) => resolve(response(status,
      status === 200 ? health : { error: "old health failure" }));
  });
  const fetch = async (request, options = {}) => {
    const url = new URL(request, "http://fixture.test");
    if (url.pathname === "/api/health" && delayedHealth) return oldHealth;
    if (state.mode === "network") throw new Error("connection refused");
    // The real server returns tokenless searches before accessing seekdb.
    if (url.pathname === "/api/search" && url.searchParams.get("q").trim().length < 2)
      return response(200, { terms: [], cards: [], matchedCandidates: 0, scanned: 0, truncated: false });
    if (state.mode === "server") return response(500, { error: "database unavailable" });
    if (url.pathname === "/api/health") return response(200, health);
    if (url.pathname === "/api/search") {
      const data = {
      query: url.searchParams.get("q"), terms: ["alpha"], matchedCandidates: 1,
      scanned: 1, truncated: false, note: "", cards: [note, ...(state.extraNotes || [])].map(n => ({ ...n, score: 1,
        snippet: n.content, matchedTerms: ["alpha"] })),
      };
      if (state.holdNextSearch) {
        state.holdNextSearch = false;
        return new Promise((resolve) => { state.releaseSearch = () => resolve(response(200, data)); });
      }
      return response(200, data);
    }
    if (url.pathname === "/api/notes") {
      const limit = url.searchParams.has("limit") ? Number(url.searchParams.get("limit")) : 1;
      const data = { notes: [note, ...(state.extraNotes || [])]
        .filter(n => !url.searchParams.get("status") || url.searchParams.get("status") === n.status)
        .slice(0, limit).map(n => ({ ...n, preview: n.content })) };
      if (state.holdNextList) {
        state.holdNextList = false;
        return new Promise((resolve) => { state.releaseList = () => resolve(response(200, data)); });
      }
      return response(200, data);
    }
    if (url.pathname === "/api/notes/note-a" && options.method === "PATCH") {
      if (state.holdPatch) {
        state.holdPatch = false;
        await new Promise((resolve) => { state.releasePatch = resolve; });
      }
      if (state.failPatch) return response(500, { error: "save failed" });
      const patch = JSON.parse(options.body);
      Object.assign(note, patch);
      state.patches.push({ ...patch });
      return response(200, { note: { ...note } });
    }
    if (url.pathname === "/api/notes/note-a") return state.missing
      ? response(404, { error: "note missing" }) : response(200, { note: { ...note } });
    throw new Error("Unexpected HTTP request " + request);
  };
  const context = vm.createContext({ document, fetch, console,
    alert: (message) => state.alerts.push(message),
    setTimeout: (fn, ms) => {
      const timer = setTimeout(() => { timers.delete(timer); fn(); }, ms);
      timers.add(timer);
      return timer;
    },
    clearTimeout: (timer) => { timers.delete(timer); clearTimeout(timer); },
  });
  for (const script of ["persist-queue.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(publicDir, script), "utf8"), context, { filename: script });
  }
  t.after(() => { for (const timer of timers) clearTimeout(timer); });
  const byId = (id) => document.getElementById(id);
  async function search(query = "alpha") {
    byId("q").value = query;
    byId("go").onclick();
    await until(() => !byId("results").textContent.includes("检索中"));
  }
  return { document, state, note, byId, search, releaseHealth };
}

async function until(predicate) {
  const deadline = Date.now() + 1500;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, "UI did not reach the expected observable state");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

async function connected(ui) {
  await until(() => ui.byId("dbstate").textContent.includes("seekdb 已连接"));
}

test("a backend 500 marks a previously connected database disconnected", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  ui.state.mode = "server";
  await ui.search();
  assert.match(ui.byId("results").textContent, /检索失败/);
  assert.ok(ui.byId("dbdot").classList.contains("down"));
  assert.ok(!ui.byId("dbdot").classList.contains("up"));
  assert.doesNotMatch(ui.byId("dbstate").textContent, /已连接/);
});

test("a network error marks the connection disconnected", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  ui.state.mode = "network";
  await ui.search();
  assert.ok(ui.byId("dbdot").classList.contains("down"));
});

test("a missing note is a user-level 404, not a database outage", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  await ui.search();
  ui.state.missing = true;
  await ui.document.querySelector(".evidence").onclick();
  assert.match(ui.state.alerts[0], /note missing/);
  assert.ok(ui.byId("dbdot").classList.contains("up"));
  assert.ok(!ui.byId("dbdot").classList.contains("down"));
});

test("a successful retry restores the connection without retaining the down class", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  ui.state.mode = "server";
  await ui.search();
  assert.ok(ui.byId("dbdot").classList.contains("down"));
  ui.state.mode = "ok";
  await ui.search();
  assert.ok(ui.byId("dbdot").classList.contains("up"));
  assert.ok(!ui.byId("dbdot").classList.contains("down"));
  assert.match(ui.byId("dbstate").textContent, /seekdb 已连接/);
});

test("a tokenless search cannot claim database recovery without a database query", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  ui.state.mode = "server";
  await ui.search();
  assert.ok(ui.byId("dbdot").classList.contains("down"));
  for (const query of ["", "  ", "x"]) {
    await ui.search(query);
    assert.match(ui.byId("results").textContent, /没有命中/);
    assert.ok(ui.byId("dbdot").classList.contains("down"), "tokenless response does not prove seekdb recovery");
    assert.ok(!ui.byId("dbdot").classList.contains("up"));
  }
});

test("a delayed old health success cannot overwrite a newer failed operation", async (t) => {
  const ui = fixture(t, { delayedHealth: true });
  ui.state.mode = "server";
  await ui.search();
  ui.releaseHealth();
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(ui.byId("dbdot").classList.contains("down"));
  assert.ok(!ui.byId("dbdot").classList.contains("up"));
});

test("saved read status and progress update the existing search card after close", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  await ui.search();
  await ui.document.querySelector(".evidence").onclick();
  ui.byId("rRead").onclick();
  await ui.byId("rClose").onclick();
  assert.equal(ui.note.status, "read");
  assert.equal(ui.note.progress, 100);
  assert.equal(ui.document.querySelector("#results .status-pill").textContent, "已读");
  assert.equal(ui.document.querySelector("#results .bar i").style.width, "100%");
});

test("an old failed health request cannot erase newer successful search results", async (t) => {
  const ui = fixture(t, { delayedHealth: true });
  await ui.search();
  assert.ok(ui.document.querySelector("#results .evidence"));
  ui.releaseHealth(500);
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(ui.document.querySelector("#results .evidence"));
  assert.ok(ui.byId("dbdot").classList.contains("up"));
});

test("marking a note read removes it from the active unread library filter", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  ui.document.querySelector('[data-tab="library"]').onclick();
  await until(() => ui.document.querySelector("#list .note-item"));
  ui.document.querySelector('[data-f="unread"]').onclick();
  await until(() => ui.document.querySelector("#list .note-item"));
  await ui.document.querySelector("#list .note-item .btn").onclick();
  ui.byId("rRead").onclick();
  await ui.byId("rClose").onclick();
  await until(() => !ui.document.querySelector("#list .note-item") && ui.byId("list").textContent.includes("暂无"));
  assert.match(ui.byId("list").textContent, /暂无/);
});

test("an older library refresh cannot overwrite a newer status filter", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  ui.document.querySelector('[data-tab="library"]').onclick();
  await until(() => ui.document.querySelector("#list .note-item"));
  await ui.document.querySelector("#list .note-item .btn").onclick();
  ui.state.holdNextList = true;
  ui.byId("rRead").onclick();
  await ui.byId("rClose").onclick();
  await until(() => ui.state.releaseList);
  ui.document.querySelector('[data-f="unread"]').onclick();
  await until(() => ui.byId("list").textContent.includes("暂无"));
  ui.state.releaseList();
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(!ui.document.querySelector("#list .note-item"), "the newer unread filter must stay empty");
  assert.match(ui.byId("list").textContent, /暂无/);
});

test("a failed save leaves the persisted card unchanged and reports the failure", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  await ui.search();
  await ui.document.querySelector(".evidence").onclick();
  ui.state.failPatch = true;
  ui.byId("rRead").onclick();
  await until(() => ui.byId("rSaveMsg").textContent.includes("保存失败"));
  assert.equal(ui.note.status, "unread");
  assert.equal(ui.note.progress, 0);
  assert.equal(ui.document.querySelector("#results .status-pill").textContent, "未读");
  assert.equal(ui.document.querySelector("#results .bar i").style.width, "0%");
  assert.match(ui.byId("rStatusPill").textContent, /未保存/);
});

test("a search snapshot taken before PATCH cannot erase confirmed saved state or change another note", async (t) => {
  const ui = fixture(t);
  ui.state.extraNotes = [{ ...ui.note, id: "note-b", title: "Other note" }];
  await connected(ui);
  await ui.search();
  await ui.document.querySelector(".evidence").onclick();
  ui.state.holdPatch = true;
  ui.byId("rRead").onclick();
  const closing = ui.byId("rClose").onclick();
  await until(() => ui.state.releasePatch);
  ui.state.holdNextSearch = true;
  ui.byId("go").onclick();
  await until(() => ui.state.releaseSearch);
  ui.state.releasePatch();
  await closing;
  ui.state.releaseSearch();
  await until(() => ui.document.querySelectorAll("#results .evidence").length === 2);
  assert.equal(ui.document.querySelector('[data-note-id="note-a"] .status-pill').textContent, "已读");
  assert.equal(ui.document.querySelector('[data-note-id="note-a"] .bar i').style.width, "100%");
  assert.equal(ui.document.querySelector('[data-note-id="note-b"] .status-pill').textContent, "未读");
  assert.equal(ui.document.querySelector('[data-note-id="note-b"] .bar i').style.width, "0%");
});

test("closing after a failed save keeps a visible warning and permits retrying the same intent", async (t) => {
  const ui = fixture(t);
  await connected(ui);
  await ui.search();
  await ui.document.querySelector(".evidence").onclick();
  ui.state.failPatch = true;
  ui.byId("rRead").onclick();
  await ui.byId("rClose").onclick();
  assert.ok(ui.byId("saveNotice"), "failed close needs a persistent notice");
  assert.ok(!ui.byId("saveNotice").hidden);
  assert.match(ui.byId("saveNoticeText").textContent, /未保存/);
  assert.equal(ui.note.status, "unread");
  ui.state.failPatch = false;
  await ui.byId("retrySaves").onclick();
  await until(() => ui.note.status === "read");
  assert.equal(ui.note.progress, 100);
  assert.ok(ui.byId("saveNotice").hidden);
  assert.equal(ui.document.querySelector("#results .status-pill").textContent, "已读");
});

test("the library explicitly requests multiple notes rather than inheriting the server's one-row default", async (t) => {
  const ui = fixture(t);
  ui.state.extraNotes = [{ ...ui.note, id: "note-b", title: "Other note" }];
  await connected(ui);
  ui.document.querySelector('[data-tab="library"]').onclick();
  await until(() => ui.document.querySelector("#list .note-item"));
  assert.equal(ui.document.querySelectorAll("#list .note-item").length, 2);
});

test("an old health failure cannot contradict a newer successful library request", async (t) => {
  const ui = fixture(t, { delayedHealth: true });
  ui.document.querySelector('[data-tab="library"]').onclick();
  await until(() => ui.document.querySelector("#list .note-item"));
  ui.releaseHealth(500);
  await new Promise((resolve) => setImmediate(resolve));
  assert.doesNotMatch(ui.byId("results").textContent, /无法连接/);
  assert.ok(ui.byId("dbdot").classList.contains("up"));
});

test("a successful library recovery clears the earlier startup failure", async (t) => {
  const ui = fixture(t, { initialMode: "server" });
  await until(() => ui.byId("results").textContent.includes("无法连接"));
  ui.state.mode = "ok";
  ui.document.querySelector('[data-tab="library"]').onclick();
  await until(() => ui.document.querySelector("#list .note-item"));
  assert.doesNotMatch(ui.byId("results").textContent, /无法连接/);
  assert.ok(ui.byId("dbdot").classList.contains("up"));
});
