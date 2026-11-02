(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STATUS_CN = { unread: "未读", reading: "阅读中", read: "已读" };
  let requestSequence = 0;
  let connectionSequence = 0;
  let databaseName = "";
  let searchHasRun = false;

  function showConnection(connected, sequence, database) {
    if (sequence < connectionSequence) return;
    connectionSequence = sequence;
    if (database) databaseName = database;
    $("dbdot").classList.toggle("up", connected);
    $("dbdot").classList.toggle("down", !connected);
    $("dbstate").textContent = connected
      ? "seekdb 已连接" + (databaseName ? " · " + databaseName : "")
      : "数据库不可用（后端会明确报错，不会返回假数据）";
  }

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function hl(text, terms) {
    let html = esc(text);
    for (const t of terms || []) {
      if (!t) continue;
      html = html.replace(new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), (m) => "<mark>" + m + "</mark>");
    }
    return html;
  }
  async function api(path, opts) {
    const sequence = ++requestSequence;
    let r;
    try { r = await fetch(path, opts && opts.method ? opts : undefined); }
    catch (e) { showConnection(false, sequence); throw e; }
    let data = null;
    try { data = await r.json(); } catch {}
    if (!r.ok) {
      if (r.status >= 500) showConnection(false, sequence);
      throw new Error((data && data.error) || "HTTP " + r.status);
    }
    showConnection(true, sequence, path === "/api/health" && data && data.database);
    return data;
  }

  // ---- 数据库健康状态 ----
  (async function health() {
    try {
      await api("/api/health");
    } catch (e) {
      if (!searchHasRun) {
        $("results").innerHTML = '<div class="empty err">无法连接后端/数据库：' + esc(e.message) + "。请先运行 npm start 并确认 seekdb 在线。</div>";
      }
    }
  })();

  // ---- 标签页 ----
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
      document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + b.dataset.tab));
      if (b.dataset.tab === "library") loadList(currentFilter);
    };
  });

  // ---- 检索 ----
  function runSearch(q) {
    searchHasRun = true;
    $("results").innerHTML = '<div class="empty">检索中…</div>';
    api("/api/search?q=" + encodeURIComponent(q))
      .then(({ cards, terms, matchedCandidates, scanned, truncated, note }) => {
        if (!cards.length) {
          $("results").innerHTML = '<div class="empty">没有命中' + (terms.length ? "（检索词：" + terms.map(esc).join("、") + "）" : "") + "。可先导入笔记。</div>";
          return;
        }
        $("results").innerHTML = "";
        if (truncated && note) {
          const tip = document.createElement("div");
          tip.className = "notice info";
          tip.textContent = "ℹ " + note + "（已扫描 " + scanned + " 条候选）";
          $("results").appendChild(tip);
        }
        cards.forEach((c) => {
          const el = document.createElement("div");
          el.className = "evidence";
          el.dataset.noteId = c.id;
          el.innerHTML =
            '<div><b>' + esc(c.title) + '</b><span class="status-pill ' + c.status + '">' + STATUS_CN[c.status] + '</span><span style="float:right;font-size:12.5px;color:var(--ink-2);">匹配度 ' + c.score + "</span></div>" +
            '<div class="src">来源：<a href="' + esc(c.url) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">' + esc(c.url) + "</a></div>" +
            '<div class="snippet">' + hl(c.snippet, c.matchedTerms) + "</div>" +
            '<div class="bar"><i style="width:' + c.progress + '%"></i></div>';
          el.style.cursor = "pointer";
          el.onclick = () => openReader(c.id, q);
          $("results").appendChild(el);
        });
      })
      .catch((e) => { $("results").innerHTML = '<div class="empty err">检索失败：' + esc(e.message) + "</div>"; });
  }
  $("go").onclick = () => runSearch($("q").value);
  $("q").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(e.target.value); });

  // ---- 笔记库 ----
  let currentFilter = "";
  let listSequence = 0;
  document.querySelectorAll("#panel-library .chip").forEach((ch) => {
    ch.onclick = () => {
      document.querySelectorAll("#panel-library .chip").forEach((x) => { x.style.borderColor = ""; x.style.color = ""; });
      ch.style.borderColor = "var(--accent)"; ch.style.color = "var(--accent)";
      currentFilter = ch.dataset.f;
      loadList(currentFilter);
    };
  });
  function loadList(status) {
    const sequence = ++listSequence;
    const box = $("list");
    box.innerHTML = '<div class="empty">加载中…</div>';
    api("/api/notes" + (status ? "?status=" + status : ""))
      .then(({ notes }) => {
        if (sequence !== listSequence) return;
        if (!notes.length) { box.innerHTML = '<div class="empty">暂无笔记，去「导入笔记」添加，或运行 npm run seed 灌入公开样例。</div>'; return; }
        box.innerHTML = "";
        notes.forEach((n) => {
          const el = document.createElement("div");
          el.className = "note-item";
          el.innerHTML =
            "<div style=\"flex:1;min-width:200px;\"><div class=\"t\">" + esc(n.title) +
            ' <span class="status-pill ' + n.status + '">' + STATUS_CN[n.status] + " " + n.progress + "%</span></div>" +
            '<div class="u">' + esc(n.url) + " · " + esc(n.preview) + "…</div></div>" +
            '<div class="rowline"><button class="btn small">打开阅读</button><button class="btn ghost small del">删除</button></div>';
          el.querySelector(".btn").onclick = () => openReader(n.id);
          el.querySelector(".del").onclick = async () => {
            if (!confirm("删除这条笔记？")) return;
            try { await api("/api/notes/" + n.id, { method: "DELETE" }); loadList(currentFilter); }
            catch (e) { alert("删除失败：" + e.message); }
          };
          box.appendChild(el);
        });
      })
      .catch((e) => {
        if (sequence === listSequence) box.innerHTML = '<div class="empty err">加载失败：' + esc(e.message) + "</div>";
      });
  }

  function showSavedNote(id, patch) {
    document.querySelectorAll("#results .evidence").forEach((el) => {
      if (el.dataset.noteId !== id) return;
      const pill = el.querySelector(".status-pill");
      if (patch.status) {
        pill.className = "status-pill " + patch.status;
        pill.textContent = STATUS_CN[patch.status];
      }
      if (patch.progress !== undefined) el.querySelector(".bar i").style.width = patch.progress + "%";
    });
    if ($("panel-library").classList.contains("active")) loadList(currentFilter);
  }

  // ---- 导入 ----
  $("importForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    $("importMsg").className = "ok-msg";
    $("importMsg").textContent = "保存中…";
    try {
      await api("/api/notes", {
        method: "POST",
        body: JSON.stringify({ title: f.title.value, url: f.url.value, content: f.content.value }),
      });
      $("importMsg").textContent = "已写入 seekdb ✓";
      f.reset();
    } catch (e) {
      $("importMsg").className = "err";
      $("importMsg").textContent = "失败：" + e.message;
    }
  });
  $("loadSample").onclick = async () => {
    try {
      const d = await (await fetch("samples/sample-notes.json")).json();
      const n = d.notes[Math.floor(Math.random() * d.notes.length)];
      const f = $("importForm");
      f.title.value = n.title; f.url.value = n.url; f.content.value = n.content;
    } catch {}
  };

  // ---- 阅读器 + 进度持久化（经 persist-queue：按笔记 id 合并快照、串行保存、切换/关闭时 flush）----
  let reading = null;
  const persistQ = createPersistQueue(
    (id, patch) =>
      api("/api/notes/" + id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }),
    {
      debounceMs: 800,
      onSaved: (id, patch) => {
        showSavedNote(id, patch);
        if (reading && reading.id === id) flashSaveMsg("进度已存入数据库 ✓", false);
      },
      onFailed: (id, e) => { if (reading && reading.id === id) flashSaveMsg("保存失败：" + e.message, true); },
    }
  );
  function flashSaveMsg(text, isErr) {
    const m = $("rSaveMsg");
    m.className = isErr ? "err" : "ok-msg";
    m.textContent = text;
    if (!isErr) setTimeout(() => { if (m.textContent === text) m.textContent = ""; }, 2000);
  }

  async function openReader(id, backQuery) {
    if (reading) await persistQ.flush(reading.id); // 切换前把旧笔记进度落库，不带入新笔记
    try {
      const { note } = await api("/api/notes/" + id);
      reading = note;
      $("rTitle").textContent = note.title;
      $("rLink").href = note.url;
      $("rBody").textContent = note.content;
      setStatusUi(note.status, note.progress);
      $("readerMask").classList.add("open");
      $("rSaveMsg").textContent = "";
      $("rBody").scrollTop = Math.max(0, Math.round((note.progress / 100) * ($("rBody").scrollHeight - $("rBody").clientHeight)));
      if (backQuery) $("q").value = backQuery;
    } catch (e) { alert("打开失败：" + e.message); }
  }
  function setStatusUi(status, progress) {
    const pill = $("rStatusPill");
    pill.className = "status-pill " + status;
    pill.textContent = STATUS_CN[status];
    $("rBar").style.width = progress + "%";
    $("rPct").textContent = progress + "%";
  }
  $("rBody").addEventListener("scroll", () => {
    if (!reading) return;
    const max = $("rBody").scrollHeight - $("rBody").clientHeight;
    if (max <= 0) return;
    const pct = Math.min(100, Math.round(($("rBody").scrollTop / max) * 100));
    if (Math.abs(pct - reading.progress) < 5) return;
    reading.progress = pct;
    if (reading.status === "unread") reading.status = "reading";
    setStatusUi(reading.status, pct);
    // 快照按当前笔记 id 入队；关闭/切换时由 flush 落库，防抖窗口内不会再丢
    persistQ.schedule(reading.id, { status: reading.status, progress: pct });
  });
  $("rRead").onclick = () => {
    if (!reading) return;
    reading.status = "read";
    reading.progress = 100;
    setStatusUi("read", 100);
    $("rBody").scrollTop = $("rBody").scrollHeight;
    persistQ.schedule(reading.id, { status: "read", progress: 100 });
    persistQ.flush(reading.id);
  };
  async function closeReader() {
    const id = reading && reading.id;
    reading = null;
    $("readerMask").classList.remove("open");
    if (id) await persistQ.flush(id); // 关闭时强制保存未完成防抖窗口内的进度
  }
  $("rClose").onclick = closeReader;
  $("readerMask").addEventListener("click", (e) => { if (e.target === $("readerMask")) closeReader(); });
})();
