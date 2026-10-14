/* 賽事評估 (GET /api/v1/overview/feasibility = engine/race_feasibility.py, SP-105): one card per race
 * on the season-plan page — first 可行性 (來不來得及練: projected), then 完備程度 (現在練得夠不夠:
 * what was actually done), each a level, a number or two and the main advice; the checks behind
 * them under 詳細. The trend charts are in 圖表分析 → 專項期. Plain words on the page (owner
 * 2026-10-05); the sources only inside 詳細. Only advice: nothing here changes the event.
 *
 *   <script src="/api/v1/static/feasibility.js"></script>
 *   const r = await Feas.load();          // { races, chart_href }; Feas.load(eventId) = one event
 *   const st = await Hints.state();       // 提示多寡 (static/hints.js); without it: 標準
 *   el.innerHTML = r.races.map((x) => Feas.html(x, st)).join("");
 *
 * 提示多寡 (SP-486 = docs/research/ui-hint-audit.md §7 T5, docs/spec/ui-hints.spec.md): the card keeps the
 * verdict and what changes a decision —
 *   the pills (PL7) and the cutoff / turnaround (PL14): every level
 *   跨級 「分級 a → b」 (PL10 ●◐▷): 完整 always; 標準 when it jumps ≥ 2 classes or hides × 2; 精簡 when it
 *     jumps ≥ 2 classes. The EP multiple (PL11, R13) next to it: 完整 always, 標準 at ≥ 2 times, 精簡 never
 *     (the × 2 reminder, PL12, says it — a class-1 line in every level)
 *   Koop's weekly hours (PL17 ●▷▷): a number in 完整 only, else under 詳細
 *   the other numbers (週量 PL9, 長天／最大週量／B2B PL16, 百岳 …): 完整、標準; 精簡 only 有點趕／不足
 * The lines under the rows are engine/race_feasibility.card_hints()'s hint objects through Hints.render
 * (class 1 in one box in every level, at most 3 reminders, ▷ under 詳細).
 *
 * Colours come from the page's --good / --watch / --bad (fallbacks for pages without them);
 * every level also says its word, never colour alone. i18n: common.feas.* (common is inlined
 * on every page); the checks' texts come translated from the engine.
 */
(() => {
  const T = (k, p) => (window.I18N ? window.I18N.t("common.feas." + k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TONE = { ok: "good", tight: "watch", over: "bad", late: "bad", short: "bad", unknown: "na" };
  const f1 = (v) => (Math.round(v * 10) / 10).toString();
  const pct = (v) => Math.round(v * 100);

  let styled = false;
  function style() {
    if (styled) return;
    styled = true;
    const s = document.createElement("style");
    s.textContent = `
.feas { border: 1px solid var(--line, #d0d7de); border-radius: 8px; padding: 8px 12px; margin: 8px 0 0; }
.feas-h { display: flex; flex-wrap: wrap; gap: 2px 8px; align-items: baseline; }
.feas-h b { font-size: 14px; }
.feas-m { color: var(--muted, #6b7280); font-size: 12px; }
.feas-pill { display: inline-block; font-size: 12px; font-weight: 600; padding: 0 8px; border-radius: 10px;
  border: 1px solid currentColor; white-space: nowrap; }
.feas-pill.good { color: var(--good, #16a34a); } .feas-pill.watch { color: var(--watch, var(--warn, #d97706)); }
.feas-pill.bad { color: var(--bad, #dc2626); } .feas-pill.na { color: var(--muted, #6b7280); }
.feas-r { display: grid; grid-template-columns: minmax(7.5em, auto) 1fr; gap: 2px 10px; margin-top: 6px; font-size: 13px; }
.feas-r > .q { color: var(--muted, #6b7280); }
.feas-k { display: flex; flex-wrap: wrap; gap: 2px 12px; align-items: baseline; font-variant-numeric: tabular-nums; }
.feas details { margin-top: 6px; font-size: 12.5px; }
.feas details > summary { color: var(--muted, #6b7280); cursor: pointer; width: fit-content; }
.feas details h4 { margin: 8px 0 2px; font-size: 12.5px; }
.feas ul { margin: 2px 0 0; padding-left: 0; list-style: none; display: grid; gap: 4px; }
.feas li { display: grid; grid-template-columns: auto 1fr; gap: 8px; align-items: baseline; }
.feas ul.feas-more li { display: list-item; list-style: disc; margin-left: 18px; }
.feas p { margin: 6px 0 0; }
.feas-hints { margin-top: 6px; font-size: 13px; display: grid; gap: 2px; }
.feas-hints .hint-must { border: 0; border-left: 3px solid var(--bad, #dc2626); border-radius: 0; padding: 1px 0 1px 8px; margin: 0 0 2px; }
.feas-hints .hint-row > .feas-l { flex: 1; min-width: 0; }
@media (max-width: 520px) { .feas-r { grid-template-columns: 1fr; } }`;
    document.head.appendChild(s);
  }

  const pill = (lv, label) => `<span class="feas-pill ${TONE[lv] || "na"}" data-fi>${esc(label)}</span>`;
  const LEVELS = ["full", "standard", "lean"];
  const levelOf = (o) => {
    const l = (o && o.level) || (window.HINT_STATE && window.HINT_STATE.level);
    return LEVELS.includes(l) ? l : "standard";
  };
  const BEHIND = new Set(["tight", "over", "late", "short"]);       // 有點趕／不足 (and worse)

  // SP-486: is this check's number shown next to its pill in level `lv`?
  function chipOn(c, lv) {
    if (c.id === "cutoff") return true;                              // PL14, class 1
    if (c.id === "hours") return lv === "full";                      // PL17 Koop ●▷▷
    if (c.id === "step") return lv === "full" || c.up >= 2 || (lv === "standard" && !!c.x2);   // PL10 ●◐▷
    return lv !== "lean" || BEHIND.has(c.level);                     // PL9 / PL16: 精簡 only what falls short
  }

  // the numbers worth a glance (the whole sentence is the hover)
  // a multi-day trip is compared with its hardest DAY, a one-day race with the race
  function nums(checks, multi, lv) {
    const out = [], d = multi ? "_day" : "";
    for (const c of checks || []) {
      if (!chipOn(c, lv)) continue;
      let t = null;
      if (c.id === "weekly" && c.ratio != null) t = T("k_week" + d, { p: pct(c.ratio) });
      else if (c.id === "long" && c.ratio != null) t = T("k_long" + d, { p: pct(c.ratio) });
      else if (c.id === "cutoff" && c.eta_h != null) t = T("k_summit", { eta: f1(c.eta_h), c: f1(c.cutoff_h) });
      else if (c.id === "cutoff" && c.finish_h != null) t = T("k_finish", { f: f1(c.finish_h), c: f1(c.cutoff_h) });
      else if (c.id === "b2b" && c.count != null) t = T("k_b2b", { n: c.count });
      else if (c.id === "step" && c.best_class) {
        t = T("k_step", { a: c.best_class, b: c.race_class });
        // PL11 (R13): the EP multiple on the card in 完整, in 標準 from 2 times
        if (c.ep_ratio != null && (lv === "full" || (lv === "standard" && c.ep_ratio >= 2))) t += " · " + T("k_mult", { x: f1(c.ep_ratio) });
      }
      else if (c.id === "hours" && c.run != null) t = T("k_hours", { h: c.need_h, n: c.run, k: c.need });
      else if (c.id === "vam" && c.need != null) t = T("k_vam", { n: c.need, s: c.steady });
      else if (c.id === "power" && c.w_per_kg != null) t = T("k_power", { w: c.w_per_kg });
      // SP-114 a multi-day 百岳: summit-day simulations still possible / done
      else if (c.id === "summit_sim" && c.projected != null) t = T("k_sims_plan", { n: c.projected + (c.done || 0), k: c.need });
      else if (c.id === "summit_sim" && c.count != null) t = T("k_sims", { n: c.count, k: c.need });
      if (t) out.push(`<span title="${esc(c.text)}" data-fi>${esc(t)}</span>`);
    }
    return out.join("");
  }

  const list = (checks) => (checks || []).length
    ? `<ul>${checks.map((c) => `<li>${pill(c.level, c.label)}<span>${esc(c.text)}</span></li>`).join("")}</ul>` : "";

  // SP-486: the card's lines (engine card_hints) through 提示多寡; without hints.js every line, as before
  const lineItem = (h) => `<span class="feas-l" data-fi>${h.mark ? esc(h.mark) + " " : ""}${esc(h.text)}</span>`;
  function lines(r, opts, lv) {
    const hs = r.hints || [];
    if (!hs.length) return { html: "", detailHtml: "" };
    if (!window.Hints) return { html: hs.map((h) => `<div class="hint-row">${lineItem(h)}</div>`).join(""), detailHtml: "" };
    return Hints.render(hs, { level: lv, acks: (opts && opts.acks) || [], item: lineItem });
  }

  function html(r, opts) {
    style();
    const lv = levelOf(opts);
    const meta = [r.date, r.priority ? T("grade", { g: r.priority }) : "", r.days_to >= 0 ? T("days_to", { n: r.days_to }) : ""]
      .filter(Boolean).join(" · ");
    const out = [`<div class="feas" data-event="${esc(r.event_id || "")}"><div class="feas-h"><b>${esc(r.name)}</b><span class="feas-m">${esc(meta)}</span></div>`];
    if (r.skipped) { out.push(`<div class="feas-m">${esc(r.skipped)}</div></div>`); return out.join(""); }
    const sg = r.suggestions || [], rd = r.readiness, multi = (r.days || 1) > 1;
    // SP-292: 資料等級 0 / 1 — the data the verdict stands on (「依你填的資料」／「資料還少」, translated by the
    // engine); its sentence is a line (PL20) below
    const src = r.data_source;
    out.push(`<div class="feas-r"><span class="q">${esc(T("feas"))}</span><span class="feas-k">${pill(r.level, r.label)}${src ? pill("unknown", src.label) : ""}${nums(r.checks, multi, lv)}</span>`);
    if (rd) out.push(`<span class="q">${esc(T("ready"))}</span><span class="feas-k">${pill(rd.level, rd.label)}${nums(rd.checks, multi, lv)}</span>`);
    out.push(`</div>`);
    const ln = lines(r, opts, lv);
    if (ln.html) out.push(`<div class="feas-hints">${ln.html}</div>`);
    // the advice not on the card (the milestone, a second tight advice …) stays under 詳細
    const onCard = r.hints ? new Set(r.hints.filter((h) => h.kind === "advice").map((h) => h.text)) : new Set(sg.slice(0, 1));
    const rest = sg.filter((s) => !onCard.has(s));
    out.push(`<details><summary>${esc(T("detail"))}</summary><h4>${esc(T("feas"))}</h4>${list(r.checks)}`);
    if (rest.length) out.push(`<ul class="feas-more">${rest.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`);
    if (rd) out.push(`<h4>${esc(T("ready"))}</h4>${list(rd.checks)}`);
    if (ln.detailHtml) out.push(ln.detailHtml);
    // the sources (a second `const src` here — the data level's, above — was a SyntaxError that
    // stopped this whole file: no feasibility card on 賽事周期 since SP-292)
    const refs = [...new Set([...(r.src || []), ...((rd && rd.src) || [])])];
    out.push(`<p class="feas-m">${esc(T("how_text"))}</p><h4>${esc(T("src"))}</h4>${refs.map((s) => `<p class="feas-m">${esc(s)}</p>`).join("")}</details></div>`);
    return out.join("");
  }

  // the first read takes about a minute; a server that never answers (the
  // NAS out of memory) used to leave 「評估中…」 up forever: give up after
  // TIMEOUT_MS and let the page offer 重試 (err.timeout = true)
  const TIMEOUT_MS = 90000;

  async function load(eventId) {
    const q = eventId ? "?event_id=" + encodeURIComponent(eventId) : "";
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    try {
      const r = await fetch("/api/v1/overview/feasibility" + q, { signal: ctl.signal });
      if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
      return await r.json();
    } catch (e) {
      if (e && e.name === "AbortError") { const t = new Error("timeout"); t.timeout = true; throw t; }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  window.Feas = { html, load, pill };
})();
