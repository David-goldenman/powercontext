(function () {
  "use strict";
  let notes = [];
  const progressOverride = {}; // 模拟进度仅存浏览器内存

  fetch("samples/sample-notes.json")
    .then((r) => r.json())
    .then((d) => {
      notes = d.notes.map((n, i) => ({ id: "demo-" + i, ...n, status: "unread", progress: 0 }));
    })
    .catch(() => {});

  const chipsBox = document.getElementById("chips");
  ["注意力", "残差", "先验", "扩散模型", "综述"].forEach((c) => {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = c;
    b.onclick = () => {
      document.getElementById("q").value = c;
      run(c);
    };
    chipsBox.appendChild(b);
  });

  function highlight(text, terms) {
    let html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;");
    for (const t of terms) {
      if (!t) continue;
      const esc = t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      html = html.replace(new RegExp(esc, "gi"), (m) => "<mark>" + m + "</mark>");
    }
    return html;
  }

  const STATUS_CN = { unread: "未读", reading: "阅读中", read: "已读" };

  function run(q) {
    const box = document.getElementById("results");
    const cards = EvidenceCore.searchNotes(notes, q);
    if (!cards.length) {
      box.innerHTML = '<div class="empty">没有命中，换一个词试试（演示数据共 8 篇公开样例笔记）。</div>';
      return;
    }
    box.innerHTML = "";
    cards.forEach((c) => {
      const st = progressOverride[c.id] || { status: c.status, progress: c.progress };
      const el = document.createElement("div");
      el.className = "evidence";
      el.innerHTML =
        '<div><b>' + escapeHtml(c.title) + '</b><span class="status-pill ' + st.status + '">' + STATUS_CN[st.status] + "</span></div>" +
        '<div class="src">来源：<a href="' + escapeHtml(c.url) + '" target="_blank" rel="noopener">' + escapeHtml(c.url) + "</a></div>" +
        '<div class="snippet">' + highlight(c.snippet, c.matchedTerms) + "</div>" +
        '<div class="bar"><i style="width:' + st.progress + '%"></i></div>';
      const btns = document.createElement("div");
      btns.style.marginTop = "10px";
      ["unread", "reading", "read"].forEach((s) => {
        const b = document.createElement("button");
        b.className = "btn ghost small";
        b.textContent = s === "reading" ? "标记阅读中" : s === "read" ? "标记已读" : "重置未读";
        b.onclick = () => {
          progressOverride[c.id] = { status: s, progress: s === "read" ? 100 : s === "reading" ? 50 : 0 };
          run(q);
        };
        btns.appendChild(b);
      });
      el.appendChild(btns);
      box.appendChild(el);
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  document.getElementById("go").onclick = () => run(document.getElementById("q").value);
  document.getElementById("q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") run(e.target.value);
  });
})();
