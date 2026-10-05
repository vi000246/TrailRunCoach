/* 賽事評估 (GET /api/v1/overview/feasibility = engine/race_feasibility.py, SP-105): one card per race
 * on the season-plan page — first 可行性 (來不來得及練: projected), then 完備程度 (現在練得夠不夠:
 * what was actually done), each a level, a number or two and the main advice; the checks behind
 * them under 詳細. The trend charts are in 圖表分析 → 專項期. Plain words on the page (owner
 * 2026-10-05); the sources only inside 詳細. Only advice: nothing here changes the event.
 *
 *   <script src="/api/v1/static/feasibility.js"></script>
 *   const r = await Feas.load();          // { races, chart_href }; Feas.load(eventId) = one event
 *   el.innerHTML = r.races.map(Feas.html).join("");
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
.feas-a { grid-column: 2; }
.feas details { margin-top: 6px; font-size: 12.5px; }
.feas details > summary { color: var(--muted, #6b7280); cursor: pointer; width: fit-content; }
.feas details h4 { margin: 8px 0 2px; font-size: 12.5px; }
.feas ul { margin: 2px 0 0; padding-left: 0; list-style: none; display: grid; gap: 4px; }
.feas li { display: grid; grid-template-columns: auto 1fr; gap: 8px; align-items: baseline; }
.feas ul.feas-more li { display: list-item; list-style: disc; margin-left: 18px; }
.feas p { margin: 6px 0 0; }
@media (max-width: 520px) { .feas-r { grid-template-columns: 1fr; } .feas-a { grid-column: 1; } }`;
    document.head.appendChild(s);
  }

  const pill = (lv, label) => `<span class="feas-pill ${TONE[lv] || "na"}">${esc(label)}</span>`;

  // the numbers worth a glance (the whole sentence is the hover)
  // a multi-day trip is compared with its hardest DAY, a one-day race with the race
  function nums(checks, multi) {
    const out = [], d = multi ? "_day" : "";
    for (const c of checks || []) {
      let t = null;
      if (c.id === "weekly" && c.ratio != null) t = T("k_week" + d, { p: pct(c.ratio) });
      else if (c.id === "long" && c.ratio != null) t = T("k_long" + d, { p: pct(c.ratio) });
      else if (c.id === "cutoff" && c.eta_h != null) t = T("k_summit", { eta: f1(c.eta_h), c: f1(c.cutoff_h) });
      else if (c.id === "cutoff" && c.finish_h != null) t = T("k_finish", { f: f1(c.finish_h), c: f1(c.cutoff_h) });
      else if (c.id === "b2b" && c.count != null) t = T("k_b2b", { n: c.count });
      else if (c.id === "step" && c.best_class) t = T("k_step", { a: c.best_class, b: c.race_class });
      if (t) out.push(`<span title="${esc(c.text)}">${esc(t)}</span>`);
    }
    return out.join("");
  }

  const list = (checks) => (checks || []).length
    ? `<ul>${checks.map((c) => `<li>${pill(c.level, c.label)}<span>${esc(c.text)}</span></li>`).join("")}</ul>` : "";

  function html(r) {
    style();
    const meta = [r.date, r.priority ? T("grade", { g: r.priority }) : "", r.days_to >= 0 ? T("days_to", { n: r.days_to }) : ""]
      .filter(Boolean).join(" · ");
    const out = [`<div class="feas"><div class="feas-h"><b>${esc(r.name)}</b><span class="feas-m">${esc(meta)}</span></div>`];
    if (r.skipped) { out.push(`<div class="feas-m">${esc(r.skipped)}</div></div>`); return out.join(""); }
    const sg = r.suggestions || [], rd = r.readiness, multi = (r.days || 1) > 1;
    out.push(`<div class="feas-r"><span class="q">${esc(T("feas"))}</span><span class="feas-k">${pill(r.level, r.label)}${nums(r.checks, multi)}</span>`);
    if (sg.length) out.push(`<span class="feas-a">→ ${esc(sg[0])}</span>`);
    if (rd) {
      out.push(`<span class="q">${esc(T("ready"))}</span><span class="feas-k">${pill(rd.level, rd.label)}${nums(rd.checks, multi)}</span>`);
      if (rd.note) out.push(`<span class="feas-a feas-m">${esc(rd.note)}</span>`);
    }
    if (r.split_note) out.push(`<span class="feas-a feas-m">⚠ ${esc(r.split_note)}</span>`);
    out.push(`</div><details><summary>${esc(T("detail"))}</summary><h4>${esc(T("feas"))}</h4>${list(r.checks)}`);
    if (sg.length > 1) out.push(`<ul class="feas-more">${sg.slice(1).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`);
    if (rd) out.push(`<h4>${esc(T("ready"))}</h4>${list(rd.checks)}`);
    const src = [...new Set([...(r.src || []), ...((rd && rd.src) || [])])];
    out.push(`<p class="feas-m">${esc(T("how_text"))}</p><h4>${esc(T("src"))}</h4>${src.map((s) => `<p class="feas-m">${esc(s)}</p>`).join("")}</details></div>`);
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
