/* SP-471: 「心率和功率對不起來：建議做測試」 — a block of its own on 總覽 and 課表 for the test the
 * easy-power conflict points at (engine/base_check.easy_power_tests: AeT / LTHR for the HR side, CP for
 * the power side; overview.easy_power_suggestions). The rows are the floating box's own
 * (GET …/plan/suggestions, the rows carrying `easy_power`): no request of its own — the box's load
 * (static/suggestions.js) fires "suggestions:loaded" with them.
 *
 *   <div id="eptest" hidden></div><script src="/api/v1/static/eptest.js" defer></script>
 *
 *   「排入」 POST …/suggestions/accept {id, day}   「不要」 POST …/suggestions/dismiss {id, action: "declined"}
 *
 * After 排入 it fires "suggestions:changed" ({id, sessions, from: "eptest"}) like the box, and the box
 * reloads. Styled from the page's tokens (--panel, --line, --text, --muted, --accent, --watch). Rows are
 * `.sg-item`, so the 課表 page's stale-view guard on 排入 covers them too. i18n: common.eptest.*.
 */
(() => {
  if (window.EPTest) return;
  const API = "/api/v1/overview/plan/suggestions";
  const SCHEDULE = "/api/v1/overview/plan/schedule/page";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const T = (k, p) => (window.I18N ? window.I18N.t("common.eptest." + k, p) : k);
  const md = (iso) => (window.I18N && I18N.fmt && I18N.fmt.date ? I18N.fmt.date(iso, "md") :
    (() => { const d = new Date(iso + "T00:00:00"); return `${d.getMonth() + 1}/${d.getDate()}`; })());
  const ICON = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6M10 3v6.5L4.6 18.2A2 2 0 0 0 6.3 21h11.4a2 2 0 0 0 1.7-2.8L14 9.5V3"/><path d="M7.5 15h9"/></svg>`;
  const CSS = `
  .eptest { --ep-c: var(--watch, #b45309); background: color-mix(in srgb, var(--ep-c) 7%, var(--panel, #fff));
    border: 1px solid color-mix(in srgb, var(--ep-c) 45%, var(--line, #e1e5ea)); border-left: 5px solid var(--ep-c);
    border-radius: 12px; padding: 12px 16px 14px; display: grid; gap: 8px; color: var(--text, #111); }
  .eptest[hidden] { display: none; }
  .eptest .ep-h { display: flex; align-items: center; gap: 8px; margin: 0; font-size: 15px; font-weight: 700; }
  .eptest .ep-h svg { width: 20px; height: 20px; color: var(--ep-c); flex: none; }
  .eptest .ep-sum { margin: 0; font-size: 13.5px; font-variant-numeric: tabular-nums; }
  .eptest .ep-list { display: grid; gap: 8px; }
  .eptest .ep-row { background: var(--panel, #fff); border: 1px solid var(--line, #e1e5ea); border-radius: 10px; padding: 10px 12px; display: grid; gap: 4px; }
  .eptest .ep-t { font-weight: 650; font-size: 13.5px; }
  .eptest .ep-tag { display: inline-block; font-size: 11px; font-weight: 700; padding: 0 7px; margin-right: 6px; border-radius: 4px;
    color: #fff; background: var(--ep-c); vertical-align: 1px; }
  .eptest .ep-why { font-size: 13px; color: var(--muted, #667); font-variant-numeric: tabular-nums; }
  .eptest .ep-act { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 4px; }
  .eptest select { flex: 0 1 220px; min-width: 0; font: inherit; font-size: 13px; padding: 5px 8px; border-radius: 6px;
    border: 1px solid var(--line, #e1e5ea); background: var(--panel, #fff); color: inherit; }
  .eptest .ep-btn { font: inherit; font-size: 13px; padding: 5px 13px; border-radius: 6px; cursor: pointer; text-decoration: none;
    border: 1px solid var(--line, #e1e5ea); background: var(--panel, #fff); color: inherit; display: inline-block; }
  .eptest .ep-btn.pri { background: var(--accent, #2563eb); border-color: var(--accent, #2563eb); color: #fff; font-weight: 650; }
  .eptest .ep-btn:disabled { opacity: .55; cursor: default; }
  .eptest .ep-none, .eptest .ep-help { font-size: 12.5px; color: var(--muted, #667); }
  .eptest .ep-ok { color: var(--good, #1a7f37); font-size: 13px; }
  .eptest .ep-ok a { color: inherit; }
  .eptest .ep-err { color: var(--bad, #c62828); font-size: 12.5px; }
  .eptest button:focus-visible, .eptest select:focus-visible, .eptest a:focus-visible { outline: 2px solid var(--accent, #2563eb); outline-offset: 1px; }
  @media (max-width: 699px) { .eptest { padding: 10px 12px 12px; } .eptest select { flex: 1 1 100%; } }
  @media print { .eptest { display: none; } }
  `;
  let rows = [];
  const done = {};                 // id → the 已排入 line (kept for a few seconds after 排入)
  const els = () => [...document.querySelectorAll("#eptest")];

  const kindLabel = (r) => (r.kind === "aet" ? T("kind_aet") : T("kind_cp"));
  function rowHtml(r) {
    const os = r.options || [];
    const pick = os.length ? `<select data-role="day" aria-label="${esc(T("pick"))}">${os.map((o) =>
      `<option value="${esc(o.day)}">${esc(o.label || md(o.day))}${o.note ? " · " + esc(o.note) : ""}</option>`).join("")}</select>
      <button type="button" class="ep-btn pri" data-act="accept">${esc(T("accept"))}</button>` :
      `<span class="ep-none">${esc(T("no_day"))}</span>`;
    const links = (r.links || []).map((l) => `<a class="ep-btn" href="${esc(l.href)}">${esc(l.label)}</a>`).join("");
    return `<div class="ep-row sg-item" data-id="${esc(r.id)}">
      <div class="ep-t"><span class="ep-tag">${esc(kindLabel(r))}</span>${esc(r.title)}</div>
      <div class="ep-why">${esc(r.easy_power.why)}</div>
      ${done[r.id] ? `<div class="ep-ok">${done[r.id]}</div>` : `<div class="ep-act">${pick}${links}
        <button type="button" class="ep-btn" data-act="decline">${esc(T("decline"))}</button></div>`}
    </div>`;
  }
  function render() {
    const show = rows.filter((r) => r.easy_power);
    for (const el of els()) {
      el.hidden = !show.length;
      if (!show.length) { el.innerHTML = ""; continue; }
      el.classList.add("eptest");
      el.setAttribute("role", "region");
      el.setAttribute("aria-label", T("title"));
      el.innerHTML = `<h2 class="ep-h">${ICON}<span>${esc(T("title"))}</span></h2>
        <p class="ep-sum">${esc(show[0].easy_power.summary)}</p>
        <div class="ep-list">${show.map(rowHtml).join("")}</div>
        <div class="ep-help">${esc(T("help"))}</div>`;
    }
  }
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `HTTP ${r.status}`);
    return j;
  }
  // the floating box reloads after a change here — once: a page that already asked again on
  // "suggestions:changed" (課表's sugCount) left a fresh shared GET, which the box then reuses
  function boxReload(before) {
    const B = window.AppSuggestions;
    if (!B) return;
    const after = B.load(false);
    B.refresh(after === before);
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".eptest [data-act]");
    if (!b || e.defaultPrevented) return;
    const el = b.closest(".ep-row"), id = el && el.dataset.id;
    if (!id) return;
    el.querySelectorAll("button").forEach((x) => { x.disabled = true; });
    el.querySelector(".ep-err")?.remove();
    const before = window.AppSuggestions ? window.AppSuggestions.load(false) : null;
    try {
      if (b.dataset.act === "accept") {
        const day = el.querySelector("select[data-role=day]")?.value;
        const out = await post(`${API}/accept`, { id, day });
        const ss = out.sessions || [];
        done[id] = T("done", { what: ss.map((s) => `${md(s.day)}「${esc(s.title)}」`).join("、") }) +
          (location.pathname === SCHEDULE ? "" : `（<a href="${SCHEDULE}">${esc(T("to_schedule"))}</a>）`);
        render();
        window.dispatchEvent(new CustomEvent("suggestions:changed", { detail: { id, sessions: ss, from: "eptest" } }));
        setTimeout(() => { delete done[id]; rows = rows.filter((x) => x.id !== id); render(); }, 6000);
      } else {
        await post(`${API}/dismiss`, { id, action: "declined" });
        rows = rows.filter((x) => x.id !== id);
        render();
      }
      boxReload(before);
    } catch (err) {
      el.querySelectorAll("button").forEach((x) => { x.disabled = false; });
      el.insertAdjacentHTML("beforeend", `<div class="ep-err">${esc(err.message)}</div>`);
    }
  });
  // the box's rows (every GET, suggestions.js load()) and its own 排入 / 不要
  addEventListener("suggestions:loaded", (e) => {
    const got = (e.detail && e.detail.rows) || [];
    rows = got.filter((r) => r.easy_power && !done[r.id]).concat(rows.filter((r) => done[r.id]));
    render();
  });
  addEventListener("suggestions:changed", (e) => {
    const d = e.detail || {};
    if (d.from === "eptest" || !d.id) return;
    rows = rows.filter((x) => x.id !== d.id);
    render();
  });
  const mount = () => {
    if (document.getElementById("eptest-css")) return;
    const st = document.createElement("style");
    st.id = "eptest-css"; st.textContent = CSS;
    document.head.appendChild(st);
  };
  window.EPTest = { render, rows: () => rows.slice() };
  if (document.head) mount(); else document.addEventListener("DOMContentLoaded", mount);
})();
