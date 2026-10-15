/* 比賽計算機結果的提示 (SP-485 = docs/research/ui-hint-audit.md §7 T4, on SP-482's static/hints.js).
 *
 * One "page" for §5.2's cap: the result card, the 補給 card and the 分段 card share
 *   - one box on top of the result card: every class-1 hint (§4.1 必看) — 目標太快 (RC6), 超出可持續 (RC14),
 *     CP 低於實測 (RC16), rain by R1 (RC2), 冷風／失溫 (RC3w/h) — in every level, never capped, ordered by §5.2's
 *     priority; SP-547: the 補給 hints — 沒填補給站 (RC21), 脫水 > 2 % (RC20), 賽前超補 (RC19) and the rest of the
 *     fuel engine's warnings — in the same kind of box on top of the 補給 card (#fh-box / #fh-rem / #fh-how);
 *   - at most Hints.CAP reminders on the two cards together, the rest under 「還有 N 則提醒」;
 *   - 「這次怎麼算的」 (collapsed): the method lines (RC1, RC7, RC8, RC9, RC11, RC15, RC22) and whatever the
 *     level puts in 「詳細」 (▷, a ◐ whose condition does not hold, 知道了).
 * The rules of the page (pure, so test_racehints_sp485.py runs them under node):
 *   R1  rainPlace: the top box only when PoP ≥ 50 % and ≥ 5 mm (or ≥ 10 mm), road twice the amounts —
 *       the server decides (`top`, weather.rain_alert); a lighter rain is one line on the 環境 card
 *   R8  poles: one line above the segments (完整 and 標準; 精簡 none), per segment only in 完整 and only in the
 *       compact table
 *   R10 est: 完整 a 「推估」 badge on every number (as before); 標準 one 「推估 ?」 per card, its ? listing the
 *       numbers; 精簡 no badge, the card's ? lists them
 *   R11 heatShown: the heat-acclimatisation block when race-day heat ≥ 1 % or acclimatised vs not ≥ 1 %
 *   R12 is the server's (planner: RC10's `smart`, extrapolated time ≥ 15 %)
 *
 *   const r = RaceHints.page(list, {level, acks, how});  // → {box, reminders, how, counts}; opts.how = hints that go
 *                                                       //   to 「這次怎麼算的」 whatever they are (the page's own)
 *   RaceHints.count(root)                              // the hints a reader sees without opening anything
 */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const LEVELS = ["full", "standard", "lean"];
  const lvl = (l) => (LEVELS.includes(l) ? l : "standard");
  // the method lines (§7 T4): never a reminder in 標準／精簡, never dropped (✕ rows go to 「這次怎麼算的」 too)
  const METHOD_IDS = ["RC1", "RC7", "RC8", "RC9", "RC11", "RC15", "RC22"];
  // the catalog rows (engine/hints.py CATALOG: 類, 完／標／精, priority) of the hints this page makes itself —
  // the server's warnings carry theirs; test_racehints_sp485.py checks these against the catalog
  const ROWS = {
    RC1: [2, "●▷▷", 6], RC2: [1, "◐◐◐", 1], RC3: [1, "●●●", 1], RC3w: [1, "●●●", 1], RC4: [2, "●◐▷", 6],
    RC5: [2, "●●●", 6], RC6: [1, "●●●", 3], RC19: [1, "●●●", 5], RC23: [3, "●◐✕", 6], RC24: [2, "●◐✕", 6],
    RC26: [3, "●▷✕", 6], RC27: [3, "▷▷✕", 6],
  };
  /** a hint object of one of ROWS (the page's own: the method line, the heat block, the goal, rain…) */
  function make(id, text, extra = {}) {
    const [cls, show, prio] = ROWS[id];
    return { id, cls, show, prio, smart: true, text: String(text ?? ""), detail: null, ack: false, fp: null, ...extra };
  }
  const HEAT_MIN_PCT = 1;          // R11 (推估): 1 % of finish time is the smallest heat effect worth a line
  const H = () => window.Hints;

  /** the catalog symbol of a hint (an object, or one of ROWS by id) at this level (●●● without hints.js) */
  const sym = (h, level) => {
    if (typeof h === "string") h = ROWS[h] ? make(h, "") : { id: h };
    return H() ? H().sym(h, level) : "●";
  };

  /** R1: "top" (the box), "card" (one line on the 環境 card) or null (nothing to say). The server's `top`
   *  is R1; an older answer without it keeps its `alert` on top, as before. */
  function rainPlace(r) {
    if (!r || !r.alert) return null;
    if (r.top === undefined) return "top";
    return r.top ? "top" : "card";
  }

  /** R8: what of the poles to show at this level */
  function poleShow(level) {
    const l = lvl(level);
    return { line: l !== "lean", perSeg: l === "full" };
  }

  /** R8: the one line from the segment rows' pole_hint ({key: up | down}): the gentlest pole climb (whole %),
   *  the first pole segment's km; null when no row has a hint */
  function poleSummary(rows) {
    const hit = (rows || []).filter((r) => r && r.pole_hint);
    if (!hit.length) return null;
    const up = hit.filter((r) => r.pole_hint.key === "up"), down = hit.filter((r) => r.pole_hint.key === "down");
    const first = Math.min(...hit.map((r) => Number(r.start_km) || 0));
    const minUp = up.length ? Math.floor(Math.min(...up.map((r) => (Number(r.grade) || 0) * 100))) : null;
    return { up: up.length, down: down.length, up_from_pct: minUp, first_km: first,
             src: [...new Set(hit.map((r) => r.pole_hint.src).filter(Boolean))].join("；") };
  }
  function poleLine(sum, t) {
    if (!sum) return "";
    const parts = [];
    if (sum.up) parts.push(t("poles.up", { g: sum.up_from_pct }));
    if (sum.down) parts.push(t("poles.down"));
    return t("poles.line", { what: parts.join(t("poles.sep")), km: Number(sum.first_km).toFixed(1) });
  }

  /** R10: "each" (完整: every number), "card" (標準: one 「推估 ?」 per card), "tip" (精簡: only in the card's ?) */
  function estMode(level) {
    return { full: "each", standard: "card", lean: "tip" }[lvl(level)];
  }

  /** R11 on summary.heat_accl: the block shows when the race-day heat or the acclimatisation changes ≥ 1 % */
  function heatSmart(ha) {
    const b = ha && ha.band;
    if (!b) return false;
    const raw = Math.abs(Number(b.raw_penalty_pct) || 0), c = Math.abs(Number(b.center && b.center.penalty_pct) || 0);
    return raw >= HEAT_MIN_PCT || Math.abs(raw - c) >= HEAT_MIN_PCT;
  }

  /** where a hint goes: "box" | "remind" | "how" | "hidden" (§4.4 with the method rule) */
  function place(h, level, acks) {
    const n = H() ? H().norm(h) : h;
    if (n.cls === 1) return "box";
    const s = sym(n, level);
    if (METHOD_IDS.includes(n.id) && s !== "●") return "how";
    return "rest";
  }

  /** the three parts of the page from every hint of the result (strings or hint objects; an `html` field is
   *  shown as it is, else the escaped text) */
  function page(list, opts = {}) {
    const level = lvl(opts.level), acks = opts.acks || [], t = opts.t || ((k) => k);
    const methods = [], rest = [];
    (list || []).forEach((h) => (place(h, level, acks) === "how" ? methods : rest).push(h));
    const item = (h) => `<span class="hint-text">${h.html != null ? h.html : esc(h.text)}</span>`;
    const norm = (h) => (H() ? H().norm(h) : (typeof h === "object" ? h : { text: String(h), cls: 2 }));
    const ones = rest.filter((h) => norm(h).cls === 1), others = rest.filter((h) => norm(h).cls !== 1);
    let r1, r;
    if (H()) {
      r1 = H().render(ones, { level, acks, item });
      r = H().render(others, { level, acks, cap: opts.cap, item, settingsLink: opts.settingsLink });
    } else {                                       // no hints.js: every hint, as before
      const row = (h) => `<div class="hint-row">${item(h)}</div>`;
      const a = ones.map(norm), b = others.map(norm);
      r1 = { html: a.length ? `<div class="hint-must" role="alert">${a.map(row).join("")}</div>` : "", f: { must: a } };
      r = { html: b.map(row).join(""), f: { must: [], shown: b, more: [], detail: [], hidden: [] } };
    }
    const box = r1.html, reminders = r.html;
    r.f.must = r1.f.must;
    const how = (opts.how || []).concat(methods).map((h) => (H() ? H().norm(h) : h)).concat(r.f.detail.filter((h) => !h.acked))
      .concat(r.f.hidden.filter((h) => METHOD_IDS.includes(h.id)));
    const acked = r.f.detail.filter((h) => h.acked);
    const li = (h) => `<li data-hint-id="${esc(h.id || "")}">${h.html != null ? h.html : esc(h.text)}${h.acked && h.ack
      ? ` <button type="button" class="hint-ack" data-hint-ack="${esc(h.id)}" data-hint-fp="${esc(h.fp)}" data-hint-undo="1">${esc(window.I18N && I18N.t ? I18N.t("common.hints.unack") : "common.hints.unack")}</button>` : ""}</li>`;
    const howHtml = how.length || acked.length ? `<ul class="hint-detail">${how.concat(acked).map(li).join("")}</ul>` : "";
    return { box, reminders, how: howHtml, counts: { box: r.f.must.length, shown: r.f.shown.length, more: r.f.more.length, how: how.length + acked.length },
             f: r.f };
  }

  /** the hints a reader sees without opening anything (SP-485 驗收: ≤ 12 in 標準 on the demo trail race): every
   *  element marked data-rh, each box / reminder row, the 「還有 N 則提醒」 line — when it is rendered and not inside
   *  a closed <details> or a hover tip */
  function visible(el) {
    if (!el || !el.isConnected) return false;
    if (el.closest(".tip")) return false;
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (p.tagName === "DETAILS" && !p.open && !(el.closest("summary") && el.closest("summary").parentElement === p)) return false;
      if (p.classList && p.classList.contains("hidden")) return false;
    }
    if (typeof el.checkVisibility === "function") return el.checkVisibility();
    return !!(el.offsetParent || el.getClientRects().length);
  }
  const SEL = ["[data-rh]", ...["rh", "fh"].flatMap((p) => [`#${p}-box .hint-row`, `#${p}-rem > .hint-row`, `#${p}-rem .hint-more > summary`])].join(", ");
  function count(root) {
    const doc = root || document;
    const els = [...doc.querySelectorAll(SEL)];
    return els.filter(visible).length;
  }
  function list(root) {
    const doc = root || document;
    return [...doc.querySelectorAll(SEL)]
      .filter(visible).map((e) => (e.dataset.rh || e.dataset.hintId || "more") + ": " + e.textContent.trim().slice(0, 60));
  }

  window.RaceHints = { ROWS, make, sym, METHOD_IDS, HEAT_MIN_PCT, rainPlace, poleShow, poleSummary, poleLine, estMode, heatSmart, place, page, count, list };
})();
