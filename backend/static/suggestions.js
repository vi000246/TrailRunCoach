/* The floating suggestion box: what the planner suggests but never schedules
 * by itself (engine/suggestions.py; GET /api/v1/overview/plan/suggestions):
 * a B2B weekend (pick the day pair), a due CP / AeT test (pick the day), a
 * zone retest (pick the test and the day; the LTHR 30-min / max-HR test as a
 * 「安排課表」 link, `links`), a zone update, a high-altitude trip's acclimatisation
 * reminder (information).
 *
 *   「排入」 POST …/suggestions/accept {id, day, test?}
 *   「不要」 POST …/suggestions/dismiss {id, action: "declined"}
 *    ✕      POST …/suggestions/dismiss {id, action: "dismissed"}
 *
 * Dismissals live on the server (they survive reloads and devices); a
 * suggestion comes back only when a new one arises. Loaded by shell.js on
 * every page; styled from the page's tokens (--panel, --text, --muted,
 * --line, --accent). The collapsed state is a per-browser convenience
 * (localStorage). Pages can call AppSuggestions.refresh() after a change and
 * listen for the "suggestions:changed" event (fired after 排入).
 */
(() => {
  if (window.AppSuggestions) return;
  const API = "/api/v1/overview/plan/suggestions";
  const SCHEDULE = "/api/v1/overview/plan/schedule/page";
  const LS = "suggestions.collapsed";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // no stored choice: collapsed on a phone (the open list would cover half the screen)
  const narrow = () => window.matchMedia && matchMedia("(max-width: 699px)").matches;
  const ls = { get() { try { const v = localStorage.getItem(LS); return v == null ? narrow() : v === "1"; } catch (_) { return narrow(); } },
    set(v) { try { localStorage.setItem(LS, v ? "1" : "0"); } catch (_) {} } };
  const ICON = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.1 2.2h5c0-.9.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/></svg>`;
  // i18n: common.sugg.* (static/i18n/i18n.js; the common catalog is inlined on every page)
  const T = (k, p) => (window.I18N ? window.I18N.t("common.sugg." + k, p) : k);
  const KIND = { b2b: "B2B", test: T("kind.test"), zone_test: T("kind.zone_test"), zone_update: T("kind.zone_update"),
    injury_rest: T("kind.injury"), injury_hold: T("kind.injury"), injury_pattern: T("kind.injury"),
    altitude: T("kind.altitude") };

  const CSS = `
  .sugbox { --sg-acc: var(--accent, #2563eb); position: fixed; right: 16px; bottom: 16px; z-index: 60; width: 340px;
    max-width: calc(100vw - 32px); font: 13px/1.45 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
    color: var(--text, #111); }
  .sugbox[hidden] { display: none; }
  .sugbox .sg-card { background: var(--panel, #fff); border: 1px solid var(--line, #e1e5ea); border-radius: 12px;
    box-shadow: 0 10px 30px rgba(0,0,0,.16); overflow: hidden; }
  .sugbox .sg-head { display: flex; align-items: center; gap: 8px; width: 100%; padding: 9px 12px; background: none; border: 0;
    color: inherit; font: inherit; font-weight: 650; cursor: pointer; text-align: left; }
  .sugbox .sg-head svg { width: 18px; height: 18px; color: var(--sg-acc); flex: none; }
  .sugbox .sg-head .sg-n { margin-left: auto; min-width: 20px; height: 20px; padding: 0 6px; border-radius: 10px;
    background: var(--sg-acc); color: #fff; font-size: 11.5px; display: grid; place-items: center; }
  .sugbox .sg-head .sg-chev { width: 14px; height: 14px; color: var(--muted, #667); transition: transform .15s; }
  .sugbox.collapsed .sg-head .sg-chev { transform: rotate(180deg); }
  .sugbox.collapsed .sg-list { display: none; }
  .sugbox.collapsed { width: auto; }
  .sugbox .sg-list { max-height: min(60vh, 520px); overflow: auto; border-top: 1px solid var(--line, #e1e5ea); }
  .sugbox .sg-item { position: relative; padding: 10px 12px 11px; border-bottom: 1px solid var(--line, #e1e5ea); }
  .sugbox .sg-item:last-child { border-bottom: 0; }
  .sugbox .sg-tag { display: inline-block; font-size: 10.5px; font-weight: 600; letter-spacing: .03em; padding: 0 6px;
    border-radius: 4px; color: var(--sg-acc); background: color-mix(in srgb, var(--sg-acc) 12%, transparent); margin-right: 6px; }
  .sugbox .sg-title { font-weight: 600; padding-right: 46px; }
  .sugbox .sg-reason { color: var(--muted, #667); font-size: 12.5px; margin-top: 3px; display: -webkit-box;
    -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .sugbox .sg-help { color: var(--muted, #667); font-size: 12px; margin-top: 6px; padding: 7px 9px; border-radius: 8px;
    background: color-mix(in srgb, var(--sg-acc) 6%, transparent); white-space: pre-line; }
  .sugbox .sg-help[hidden] { display: none; }
  .sugbox .sg-tools { position: absolute; top: 8px; right: 8px; display: flex; gap: 2px; }
  .sugbox .sg-ico { width: 22px; height: 22px; border-radius: 50%; border: 1px solid transparent; background: none; color: var(--muted, #667);
    font: 600 12px/1 system-ui, sans-serif; cursor: pointer; display: grid; place-items: center; padding: 0; }
  .sugbox .sg-ico:hover, .sugbox .sg-ico[aria-expanded="true"] { border-color: var(--line, #e1e5ea); color: var(--text, #111); }
  .sugbox .sg-act { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; }
  .sugbox select { flex: 1 1 150px; min-width: 0; font: inherit; font-size: 12.5px; padding: 4px 6px; border-radius: 6px;
    border: 1px solid var(--line, #e1e5ea); background: var(--panel, #fff); color: inherit; }
  .sugbox .sg-btn { font: inherit; font-size: 12.5px; padding: 4px 11px; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--line, #e1e5ea); background: none; color: inherit; }
  .sugbox .sg-btn.pri { background: var(--sg-acc); border-color: var(--sg-acc); color: #fff; font-weight: 600; }
  .sugbox .sg-btn:disabled { opacity: .55; cursor: default; }
  .sugbox a.sg-btn { text-decoration: none; display: inline-block; }
  .sugbox .sg-none { color: var(--muted, #667); font-size: 12px; }
  .sugbox .sg-ok { color: var(--good, #1a7f37); font-size: 12.5px; margin-top: 6px; }
  .sugbox .sg-err { color: var(--bad, #c62828); font-size: 12px; margin-top: 6px; }
  .sugbox .sg-ok a { color: inherit; }
  .sugbox button:focus-visible, .sugbox select:focus-visible { outline: 2px solid var(--sg-acc); outline-offset: 1px; }
  @media (max-width: 699px) {
    .sugbox { right: 8px; left: 8px; width: auto; max-width: none; bottom: calc(64px + env(safe-area-inset-bottom)); }
    .sugbox.collapsed { left: auto; }
    .sugbox .sg-list { max-height: 52vh; }
  }
  @media print { .sugbox { display: none; } }
  `;

  let rows = [];
  const box = document.createElement("aside");
  box.className = "sugbox"; box.hidden = true;
  box.setAttribute("aria-label", T("title"));
  const mount = () => {
    const st = document.createElement("style");
    st.id = "suggestions-css"; st.textContent = CSS;
    document.head.appendChild(st);
    document.body.appendChild(box);
  };

  const md = (iso) => { const d = new Date(iso + "T00:00:00"); return `${d.getMonth() + 1}/${d.getDate()}`; };
  const opts = (os) => os.map((o) => `<option value="${esc(o.day)}">${esc(o.label || md(o.day))}${o.note ? " · " + esc(o.note) : ""}</option>`).join("");

  function picker(r) {
    if (r.pick === "pair" || r.pick === "day") {
      if (!(r.options || []).length) return `<span class="sg-none">${r.pick === "pair" ? "這兩週沒有連續兩天可以排（可練日、不排課日期、你自己的課）" : "兩週內沒有適合的日子"}</span>`;
      return `<select data-role="day" aria-label="${r.pick === "pair" ? "選兩天" : "選日期"}">${opts(r.options)}</select>`;
    }
    if (r.pick === "test_day") {
      const ts = r.tests || [];
      return (ts.length > 1 ? `<select data-role="test" aria-label="選測試">${ts.map((t) => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join("")}</select>` :
        `<span class="sg-none" style="flex-basis:100%">${esc(ts[0].label)}</span>`) +
        `<select data-role="day" aria-label="選日期">${opts(ts[0].options || [])}</select>`;
    }
    return "";
  }

  function item(r) {
    const canPick = (r.pick === "pair" || r.pick === "day") ? (r.options || []).length > 0
      : r.pick === "test_day" ? (r.tests || []).some((t) => (t.options || []).length) : r.pick === "confirm";
    return `<div class="sg-item" data-id="${esc(r.id)}">
      <div class="sg-tools">
        ${r.help ? `<button type="button" class="sg-ico" data-act="help" aria-expanded="false" aria-label="說明" title="說明">?</button>` : ""}
        <button type="button" class="sg-ico" data-act="dismiss" aria-label="關掉這個建議" title="關掉（不再顯示）">✕</button>
      </div>
      <div class="sg-title"><span class="sg-tag">${esc(KIND[r.type] || T("title"))}</span>${esc(r.title)}</div>
      ${r.reason ? `<div class="sg-reason">${esc(r.reason)}</div>` : ""}
      ${r.help ? `<div class="sg-help" hidden>${esc(r.help)}</div>` : ""}
      ${(r.links || []).length ? `<div class="sg-act">${r.wait_cool ? `<span class="sg-none" style="flex-basis:100%">${esc(T("wait_cool"))}</span>` : ""}
        ${r.links.map((l) => `<a class="sg-btn" href="${esc(l.href)}">${esc(l.label)}</a>`).join("")}</div>` : ""}
      ${r.pick ? `<div class="sg-act">${picker(r)}
        ${canPick ? `<button type="button" class="sg-btn pri" data-act="accept">${esc(r.accept_label || T("accept"))}</button>` : ""}
        <button type="button" class="sg-btn" data-act="decline">${esc(T("decline"))}</button></div>` : ""}
    </div>`;
  }

  function render() {
    box.hidden = !rows.length;
    if (!rows.length) { box.innerHTML = ""; return; }
    box.classList.toggle("collapsed", ls.get());
    box.innerHTML = `<div class="sg-card"><button type="button" class="sg-head" data-act="toggle" aria-expanded="${!ls.get()}">
        ${ICON}<span>${esc(T("title"))}</span><span class="sg-n" aria-label="${rows.length} 個建議">${rows.length}</span>
        <svg class="sg-chev" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></svg>
      </button><div class="sg-list">${rows.map(item).join("")}</div></div>`;
  }

  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : (j.detail && j.detail.detail) || `HTTP ${r.status}`);
    return j;
  }

  function drop(id, later) {
    const go = () => { rows = rows.filter((x) => x.id !== id); render(); };
    if (later) setTimeout(go, later); else go();
  }

  box.addEventListener("change", (e) => {
    const sel = e.target.closest("select[data-role=test]");
    if (!sel) return;
    const el = sel.closest(".sg-item"), r = rows.find((x) => x.id === el.dataset.id);
    const t = (r.tests || []).find((x) => x.key === sel.value) || {};
    el.querySelector("select[data-role=day]").innerHTML = opts(t.options || []);
  });

  box.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "toggle") { ls.set(!ls.get()); render(); return; }
    const el = b.closest(".sg-item"), id = el && el.dataset.id;
    if (!id) return;
    if (act === "help") {
      const h = el.querySelector(".sg-help"); h.hidden = !h.hidden; b.setAttribute("aria-expanded", String(!h.hidden)); return;
    }
    el.querySelectorAll("button").forEach((x) => { x.disabled = true; });
    el.querySelector(".sg-err")?.remove();
    try {
      if (act === "accept") {
        const day = el.querySelector("select[data-role=day]")?.value;
        const test = el.querySelector("select[data-role=test]")?.value || ((rows.find((x) => x.id === id) || {}).tests || [])[0]?.key;
        const out = await post(`${API}/accept`, { id, day, test });
        const ss = out.sessions || [];
        const bl = out.blackouts || [];
        el.querySelector(".sg-act").outerHTML = bl.length
          ? `<div class="sg-ok">已設成不排課 ${bl.map((b) => `${md(b.start)}–${md(b.end)}`).join("、")}（<a href="${SCHEDULE}">到課表看</a>）</div>`
          : `<div class="sg-ok">已排入 ${ss.map((s) => `${md(s.day)}「${esc(s.title)}」`).join("、")}（<a href="${SCHEDULE}">到課表看</a>）</div>`;
        window.dispatchEvent(new CustomEvent("suggestions:changed", { detail: { id, sessions: ss } }));
        drop(id, 6000);
      } else {
        await post(`${API}/dismiss`, { id, action: act === "decline" ? "declined" : "dismissed" });
        drop(id);
      }
    } catch (err) {
      el.querySelectorAll("button").forEach((x) => { x.disabled = false; });
      el.insertAdjacentHTML("beforeend", `<div class="sg-err">${esc(err.message)}</div>`);
    }
  });

  async function refresh() {
    try {
      const r = await fetch(API, { cache: "no-store" });
      if (!r.ok) return;
      rows = (await r.json()).suggestions || [];
      render();
    } catch (_) { /* the box is optional: no box when it can't load */ }
  }

  window.AppSuggestions = { refresh };
  const start = () => { mount(); setTimeout(refresh, 900); };   // after the page's own first requests
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
