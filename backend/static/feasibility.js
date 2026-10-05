/* 賽事可行性 (GET /api/v1/overview/feasibility = engine/race_feasibility.py, SP-105): a race's
 * verdict as HTML, shared by the overview card and the season-plan page (after saving an event).
 * Only advice: nothing here changes the event.
 *
 *   <script src="/api/v1/static/feasibility.js"></script>
 *   const r = await Feas.load();          // { races, levels }; Feas.load(eventId) = that one event
 *   el.innerHTML = r.races.map(Feas.html).join("");
 *
 * Colours come from the page's --good / --watch / --bad (fallbacks for pages without them);
 * every level also says its word, never colour alone. i18n: common.feas.* (common is inlined
 * on every page); the checks' texts come translated from the engine.
 */
(() => {
  const T = (k, p) => (window.I18N ? window.I18N.t("common.feas." + k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TONE = { ok: "good", tight: "watch", over: "bad", late: "bad", unknown: "na" };

  let styled = false;
  function style() {
    if (styled) return;
    styled = true;
    const s = document.createElement("style");
    s.textContent = `
.feas { border: 1px solid var(--line, #d0d7de); border-radius: 8px; padding: 8px 12px; margin: 6px 0; }
.feas-h { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; }
.feas-h b { font-size: 14px; }
.feas-pill { display: inline-block; font-size: 12px; font-weight: 600; padding: 0 8px; border-radius: 10px;
  border: 1px solid currentColor; white-space: nowrap; }
.feas-pill.good { color: var(--good, #16a34a); } .feas-pill.watch { color: var(--watch, var(--warn, #d97706)); }
.feas-pill.bad { color: var(--bad, #dc2626); } .feas-pill.na { color: var(--muted, #6b7280); }
.feas ul { margin: 6px 0 0; padding-left: 0; list-style: none; display: grid; gap: 4px; }
.feas li { display: grid; grid-template-columns: auto 1fr; gap: 8px; align-items: baseline; font-size: 13px; }
.feas .feas-s { margin: 6px 0 0; font-size: 13px; }
.feas .feas-s li { display: list-item; list-style: disc; margin-left: 18px; }
.feas .feas-m { color: var(--muted, #6b7280); font-size: 12px; margin: 6px 0 0; }
.feas details { margin-top: 4px; font-size: 12px; color: var(--muted, #6b7280); }`;
    document.head.appendChild(s);
  }

  const pill = (lv, label) => `<span class="feas-pill ${TONE[lv] || "na"}">${esc(label)}</span>`;

  function html(r) {
    style();
    const meta = [r.date, r.priority ? T("grade", { g: r.priority }) : "", r.days_to >= 0 ? T("days_to", { n: r.days_to }) : ""]
      .filter(Boolean).join(" · ");
    const out = [`<div class="feas"><div class="feas-h"><b>${esc(r.name)}</b><span class="feas-m" style="margin:0">${esc(meta)}</span>${pill(r.level, r.label)}</div>`];
    if (r.skipped) { out.push(`<p class="feas-m">${esc(r.skipped)}</p></div>`); return out.join(""); }
    if ((r.checks || []).length)
      out.push(`<ul>${r.checks.map((c) => `<li>${pill(c.level, c.label)}<span>${esc(c.text)}</span></li>`).join("")}</ul>`);
    if ((r.suggestions || []).length)
      out.push(`<div class="feas-s"><b>${esc(T("suggest"))}</b><ul>${r.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>`);
    if (r.long_day_note) out.push(`<p class="feas-m">${esc(r.long_day_note)}</p>`);
    out.push(`<details><summary>${esc(T("how"))}</summary><p>${esc(T("how_text"))}</p>${(r.src || []).map((s) => `<p>${esc(s)}</p>`).join("")}</details></div>`);
    return out.join("");
  }

  async function load(eventId) {
    const q = eventId ? "?event_id=" + encodeURIComponent(eventId) : "";
    const r = await fetch("/api/v1/overview/feasibility" + q);
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
    return r.json();
  }

  window.Feas = { html, load, pill };
})();
