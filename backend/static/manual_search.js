/* 操作手冊的全文搜尋 (SP-558): the rules, without the page — loaded by manual.html and by the node tests
 * (backend/tests/test_manual_sp558.py). The index is GET /manual/index.json (api/manual.py chunks():
 * one chunk per section part, {url, chapter, chapter_title, anchor, title, part: simple | deep, text});
 * the page keeps it, so the search also works offline (static/offline_core.js MANUAL).
 *
 *   const hits = ManualSearch.search(index, "推到手錶", { limit: 8 });
 *   // [{url, title, chapter_title, part, score, snippet (HTML, the words marked)}]
 *
 * A query is split on spaces; every word must be found (in the title, the chapter's title or the
 * text). Chinese has no spaces: a word of Chinese is matched as it is, else by its two-character
 * pieces (most of them present counts, with a lower score), so 「推到手錶」 still finds 「推送到手錶」.
 * Latin words: case-insensitive, anywhere in a word. A title hit counts most; 「簡單說」 before 「深入」.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ManualSearch = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const CJK = /[぀-ヿ㐀-鿿豈-﫿]/;
  const norm = (s) => String(s == null ? "" : s).normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // punctuation around a word is not part of it (「」, quotes, brackets, commas…)
  const PUNCT = /^[\s"'`“”‘’「」『』（）()\[\]【】,，.。、:：;；!！?？]+|[\s"'`“”‘’「」『』（）()\[\]【】,，.。、:：;；!！?？]+$/g;

  /** the query's words (normalised, without surrounding punctuation, no duplicates) */
  function terms(q) {
    const out = [];
    for (const w of norm(q).split(" ")) {
      const t = w.replace(PUNCT, "");
      if (t && !out.includes(t)) out.push(t);
    }
    return out;
  }

  const bigrams = (t) => { const b = []; for (let i = 0; i + 1 < t.length; i++) b.push(t.slice(i, i + 2)); return b; };

  /** how well one word is in a text: 1 found as it is, 0.5–0.9 most of its Chinese pieces, else 0 */
  function wordScore(t, text) {
    if (!t) return 0;
    if (text.includes(t)) return 1;
    if (CJK.test(t) && [...t].length >= 3) {
      const bs = bigrams(t);
      const have = bs.filter((b) => text.includes(b)).length / bs.length;
      if (have >= 0.6) return 0.5 + 0.4 * have;
    }
    return 0;
  }

  /** the text around the first word found, the words marked (<mark>), escaped */
  function snippet(text, all, width) {
    width = width || 48;
    // short Latin words ("to", "a") would mark half the text: only when they are the whole query
    const big = all.filter((t) => CJK.test(t) || t.length >= 3);
    const ts = big.length ? big : all;
    const src = String(text || "");
    const low = src.toLowerCase();        // the same length as src: the positions line up
    let at = -1, len = 0;
    for (const t of ts) {
      const i = low.indexOf(t);
      if (i >= 0 && (at < 0 || i < at)) { at = i; len = t.length; }
      if (i < 0 && CJK.test(t)) {
        for (const b of bigrams(t)) { const j = low.indexOf(b); if (j >= 0 && (at < 0 || j < at)) { at = j; len = 2; } }
      }
    }
    const a = at < 0 ? 0 : Math.max(0, at - width);
    const b = Math.min(src.length, (at < 0 ? 0 : at + len) + width * 2);
    let piece = src.slice(a, b);
    let out = esc(piece);
    const marks = ts.flatMap((t) => (low.includes(t) ? [t] : CJK.test(t) ? bigrams(t).filter((x) => low.includes(x)) : []))
      .filter((t) => t.length > 0).sort((x, y) => y.length - x.length);
    if (marks.length) {
      const rx = new RegExp(marks.map((m) => esc(m).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
      out = out.replace(rx, (m) => `<mark>${m}</mark>`);
    }
    return (a > 0 ? "…" : "") + out + (b < src.length ? "…" : "");
  }

  /** the best chunks for a query: every word found somewhere in the chunk; best first */
  function search(index, q, opts) {
    opts = opts || {};
    const limit = opts.limit || 8;
    const ts = terms(q);
    const chunks = (index && index.chunks) || [];
    if (!ts.length) return [];
    const hits = [];
    for (const c of chunks) {
      const title = norm(c.title), chap = norm(c.chapter_title), text = norm(c.text);
      let score = 0, all = true;
      for (const t of ts) {
        const st = wordScore(t, title), sc = wordScore(t, chap), sx = wordScore(t, text);
        const best = Math.max(st, sc, sx);
        if (!best) { all = false; break; }
        score += st * 6 + sc * 1 + sx * 2 + (text.split(t).length - 1 > 1 ? 0.5 : 0);
      }
      if (!all) continue;
      if (c.part === "simple") score += 0.75;
      hits.push({ url: c.url, title: c.title, chapter: c.chapter, chapter_title: c.chapter_title, anchor: c.anchor,
        part: c.part, score, snippet: snippet(c.text, ts) });
    }
    hits.sort((x, y) => y.score - x.score || (x.part === "simple" ? -1 : 1));
    // one hit per section part, at most `limit`
    const seen = new Set();
    return hits.filter((h) => (seen.has(h.url) ? false : (seen.add(h.url), true))).slice(0, limit);
  }

  return { terms, search, snippet, wordScore, norm };
});
