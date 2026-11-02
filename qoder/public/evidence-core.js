(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.EvidenceCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STOPWORDS = new Set([
    "的", "了", "和", "与", "及", "在", "是", "a", "an", "the", "of", "to", "in",
    "is", "are", "and", "or", "for", "on", "with", "that", "this", "it", "as",
  ]);

  function tokenizeTerms(query) {
    const raw = String(query || "").trim();
    if (!raw) return [];
    const parts = raw
      .toLowerCase()
      .split(/[\s,;。，；、!?！？]+/)
      .filter((t) => t.length >= 2 && !STOPWORDS.has(t));
    return [...new Set(parts)].slice(0, 8);
  }

  function countOccurrences(haystack, needle) {
    let count = 0;
    let idx = 0;
    while ((idx = haystack.indexOf(needle, idx)) !== -1) {
      count++;
      idx += needle.length;
    }
    return count;
  }

  function firstOccurrences(haystack, terms) {
    for (const t of terms) {
      const i = haystack.indexOf(t);
      if (i !== -1) return i;
    }
    return -1;
  }

  function makeSnippet(text, terms, radius) {
    radius = radius || 60;
    const lower = text.toLowerCase();
    const pos = firstOccurrences(lower, terms);
    if (pos === -1) return { snippet: text.slice(0, radius * 2).trim(), matchedTerm: null };
    let matchedTerm = terms.find((t) => lower.indexOf(t) !== -1) || terms[0];
    for (const t of terms) {
      const i = lower.indexOf(t);
      if (i !== -1 && i < pos) matchedTerm = t;
    }
    const start = Math.max(0, pos - radius);
    const end = Math.min(text.length, pos + matchedTerm.length + radius);
    let snippet = text.slice(start, end).trim();
    if (start > 0) snippet = "…" + snippet;
    if (end < text.length) snippet = snippet + "…";
    return { snippet, matchedTerm };
  }

  /**
   * 对一批 {id,title,url,content} 笔记做证据检索。
   * 返回按匹配得分降序的证据卡（含真实来源与匹配片段）。
   */
  function searchNotes(notes, query) {
    const terms = tokenizeTerms(query);
    if (!terms.length) return [];
    const cards = [];
    for (const n of notes) {
      const title = String(n.title || "").toLowerCase();
      const content = String(n.content || "");
      const contentLower = content.toLowerCase();
      let score = 0;
      const hitTerms = [];
      for (const t of terms) {
        const inTitle = countOccurrences(title, t);
        const inContent = countOccurrences(contentLower, t);
        if (inTitle + inContent === 0) continue;
        score += inTitle * 3 + inContent;
        hitTerms.push(t);
      }
      if (score === 0) continue;
      const { snippet, matchedTerm } = makeSnippet(content, hitTerms, 70);
      const titleHit = hitTerms.find((t) => title.includes(t)) || null;
      cards.push({
        id: n.id,
        title: n.title,
        url: n.url,
        status: n.status || "unread",
        progress: n.progress != null ? n.progress : 0,
        score,
        matchedTerms: hitTerms,
        snippet,
        snippetSource: titleHit ? "title" : "content",
      });
    }
    cards.sort((a, b) => b.score - a.score || String(a.title).localeCompare(String(b.title)));
    return cards;
  }

  return { tokenizeTerms, makeSnippet, searchNotes };
});
