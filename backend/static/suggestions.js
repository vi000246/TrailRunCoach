/* The floating suggestion box: what the planner suggests but never schedules
 * by itself (engine/suggestions.py; GET /api/v1/overview/plan/suggestions):
 * a B2B weekend (pick the day pair), a due CP / AeT test or 基線測試 (pick the day), a
 * zone retest (pick the test and the day; the LTHR 30-min / max-HR test as a
 * 「安排課表」 link, `links`), a zone update, a high-altitude trip's acclimatisation
 * reminder (information).
 *
 *   「排入」 POST …/suggestions/accept {id, day, test?}
 *   「不要」 POST …/suggestions/dismiss {id, action: "declined"} (SP-515: on a test, for every later week too —
 *            the settings page's 不再建議的測試 has 恢復)
 *    ✕      POST …/suggestions/dismiss {id, action: "dismissed"}
 *   「稍後提醒」 POST …/suggestions/dismiss {id, action: "snoozed"} (SP-507: back in 7 days, not if the test
 *            was done meanwhile; an altitude row: the day before its trip, SP-546 R1; every row but injury_done,
 *            whose 「還沒」 already is one)
 *
 * Dismissals live on the server (they survive reloads and devices); a
 * suggestion comes back only when a new one arises. Loaded by shell.js on
 * every page; styled from the page's tokens (--panel, --text, --muted,
 * --line, --accent). The collapsed state is a per-browser convenience
 * (localStorage). Pages can call AppSuggestions.refresh() after a change (AppSuggestions.open(): the
 * box opened, for a page's 「在建議框」 link, SP-483) and
 * listen for the "suggestions:changed" event (fired after 排入); "suggestions:loaded"
 * ({rows, declined}) follows every GET (static/eptest.js shows the easy-power test rows from it);
 * "suggestions:dismissed" ({id, type, action}) follows a 「不要」 / ✕ (the overview's 測試, SP-503);
 * "suggestions:snoozed" ({id, type, until}) a 稍後提醒 — still the box's (SP-507: the loaded event's `snoozed`).
 * SP-851: on a page with <html data-sugbox-dock> (the 課表), while it shows on a desktop (≥ 1000 px), <html> has
 * class `sugbox-docked` and `--sugbox-w` (the room it takes at the right edge): the page pads its right side by it.
 * SP-913: never below --float-clear (shell.js: a page's own bottom bar it would cover, e.g. the 活動列表's 比較 bar).
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
  const KIND = { b2b: "B2B", test: T("kind.test"), baseline: T("kind.baseline"), zone_test: T("kind.zone_test"), zone_update: T("kind.zone_update"),
    hr_zone_switch: T("kind.zone_update"),        // SP-493: the 自動 課表心率區間 switched (told once)
    injury_rest: T("kind.injury"), injury_hold: T("kind.injury"), injury_pattern: T("kind.injury"), injury_done: T("kind.injury"),
    altitude: T("kind.altitude") };

  const CSS = `
  .sugbox { --sg-acc: var(--accent, #2563eb); position: fixed; right: 16px; bottom: max(16px, var(--float-clear, 0px)); z-index: 60; width: 340px;
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
    .sugbox { right: 8px; left: 8px; width: auto; max-width: none; bottom: max(calc(64px + env(safe-area-inset-bottom)), var(--float-clear, 0px)); }
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
      if (!(r.options || []).length) return `<span class="sg-none">${esc(T(r.pick === "pair" ? "no_pair" : "no_day"))}</span>`;
      return `<select data-role="day" aria-label="${esc(T(r.pick === "pair" ? "pick_pair" : "pick_day"))}">${opts(r.options)}</select>`;
    }
    if (r.pick === "test_day") {
      const ts = r.tests || [];
      return (ts.length > 1 ? `<select data-role="test" aria-label="${esc(T("pick_test"))}">${ts.map((t) => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join("")}</select>` :
        `<span class="sg-none" style="flex-basis:100%">${esc(ts[0].label)}</span>`) +
        `<select data-role="day" aria-label="${esc(T("pick_day"))}">${opts(ts[0].options || [])}</select>`;
    }
    return "";
  }

  // SP-507: 稍後提醒 — not on injury_done (its 「還沒」 is the same 7 days); SP-546 R1: an altitude row comes back
  // the day before its trip
  const snoozeTip = (r) => (r.type === "altitude" ? "snooze_tip_altitude" : testy(r) ? "snooze_tip_test" : "snooze_tip");
  // SP-493: nor on hr_zone_switch (told once: ✕ only)
  const snoozeBtn = (r) => (r.type === "injury_done" || r.type === "hr_zone_switch" ? "" :
    `<button type="button" class="sg-btn" data-act="snooze" title="${esc(T(snoozeTip(r)))}">${esc(T("snooze"))}</button>`);
  const testy = (r) => ["test", "baseline", "zone_test"].includes(r.type) || (r.links || []).some((l) => l.test);
  // SP-515: 「不要」 on a test holds that test in every later week (settings: 不再建議的測試 → 恢復)
  const declineTip = (r) => (["test", "baseline"].includes(r.type) ? ` title="${esc(T("decline_tip_test"))}"` : "");

  // SP-947: `owner_only` (a coach viewing a student, on 「看起來好了？」): only the student says it is healed — the
  // text in place of 「好了」 (the server refuses that accept while viewing)
  function item(r) {
    const canPick = r.owner_only ? false : (r.pick === "pair" || r.pick === "day") ? (r.options || []).length > 0
      : r.pick === "test_day" ? (r.tests || []).some((t) => (t.options || []).length) : r.pick === "confirm";
    return `<div class="sg-item" data-id="${esc(r.id)}">
      <div class="sg-tools">
        ${r.help ? `<button type="button" class="sg-ico" data-act="help" aria-expanded="false" aria-label="${esc(T("help"))}" title="${esc(T("help"))}">?</button>` : ""}
        <button type="button" class="sg-ico" data-act="dismiss" aria-label="${esc(T("dismiss"))}" title="${esc(T("dismiss_tip"))}">✕</button>
      </div>
      <div class="sg-title"><span class="sg-tag">${esc(KIND[r.type] || T("title"))}</span>${esc(r.title)}</div>
      ${r.reason ? `<div class="sg-reason">${esc(r.reason)}</div>` : ""}
      ${r.help ? `<div class="sg-help" hidden>${esc(r.help)}</div>` : ""}
      ${(r.links || []).length ? `<div class="sg-act">${r.wait_cool ? `<span class="sg-none" style="flex-basis:100%">${esc(T("wait_cool"))}</span>` : ""}
        ${r.links.map((l) => `<a class="sg-btn" href="${esc(l.href)}">${esc(l.label)}</a>`).join("")}${r.pick ? "" : snoozeBtn(r)}</div>` : ""}
      ${r.pick ? `<div class="sg-act">${picker(r)}${r.owner_only ? `<span class="sg-none">${esc(r.owner_only)}</span>` : ""}
        ${canPick ? `<button type="button" class="sg-btn pri" data-act="accept">${esc(r.accept_label || T("accept"))}</button>` : ""}
        <button type="button" class="sg-btn" data-act="decline"${declineTip(r)}>${esc(r.decline_label || T("decline"))}</button>${snoozeBtn(r)}</div>`
        : (r.links || []).length || !snoozeBtn(r) ? "" : `<div class="sg-act">${snoozeBtn(r)}</div>`}
    </div>`;
  }

  // SP-851: on a page that asks for it (<html data-sugbox-dock>: the 課表, whose 週統計 column it covered), the
  // room the box takes at the right edge (its width + its 16px offset) as --sugbox-w on <html> and the class
  // sugbox-docked, on a desktop (≥ DOCK_MIN wide) while it shows, else 0px / no class: the page pads its right
  // side by it (askai.js stacks its button above the box then). Other pages: the box floats over them as before.
  const DOCK_MIN = 1000;
  const wide = () => !(window.matchMedia && !matchMedia(`(min-width: ${DOCK_MIN}px)`).matches);
  function dock() {
    const root = document.documentElement;
    if (!root || !root.hasAttribute) return;
    const on = root.hasAttribute("data-sugbox-dock");
    const w = on && !box.hidden && box.isConnected && wide() ? Math.ceil(box.getBoundingClientRect().width) + 16 : 0;
    root.style.setProperty("--sugbox-w", `${w}px`);
    root.classList.toggle("sugbox-docked", w > 0);
  }
  const mq = window.matchMedia && matchMedia(`(min-width: ${DOCK_MIN}px)`);
  if (mq && mq.addEventListener) mq.addEventListener("change", dock);
  else if (mq && mq.addListener) mq.addListener(dock);        // Safari < 14

  function render() {
    box.hidden = !rows.length;
    if (!rows.length) { box.innerHTML = ""; dock(); return; }
    box.classList.toggle("collapsed", ls.get());
    box.innerHTML = `<div class="sg-card"><button type="button" class="sg-head" data-act="toggle" aria-expanded="${!ls.get()}">
        ${ICON}<span>${esc(T("title"))}</span><span class="sg-n" aria-label="${esc(T("n", { n: rows.length }))}">${rows.length}</span>
        <svg class="sg-chev" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></svg>
      </button><div class="sg-list">${rows.map(item).join("")}</div></div>`;
    dock();
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
        // a server message (SP-273 「已記成好了」) wins over the 已排入 / 已設成不排課 texts
        el.querySelector(".sg-act").outerHTML = out.message ? `<div class="sg-ok">${esc(out.message)}</div>` : bl.length
          ? `<div class="sg-ok">${esc(T("blacked_out", { days: bl.map((b) => `${md(b.start)}–${md(b.end)}`).join(T("sep")) }))} <a href="${SCHEDULE}">${esc(T("to_schedule"))}</a></div>`
          : `<div class="sg-ok">${esc(T("scheduled", { list: ss.map((s) => T("item", { day: md(s.day), title: s.title })).join(T("sep")) }))} <a href="${SCHEDULE}">${esc(T("to_schedule"))}</a></div>`;
        window.dispatchEvent(new CustomEvent("suggestions:changed", { detail: { id, sessions: ss } }));
        drop(id, 6000);
      } else if (act === "snooze") {
        const out = await post(`${API}/dismiss`, { id, action: "snoozed" });
        const type = (rows.find((x) => x.id === id) || {}).type;
        el.querySelectorAll(".sg-act").forEach((x, i) => { if (i) x.remove(); });
        el.querySelector(".sg-act").outerHTML = `<div class="sg-ok">${esc(T("snoozed", { day: out.until ? md(out.until) : "" }))}</div>`;
        window.dispatchEvent(new CustomEvent("suggestions:snoozed", { detail: { id, type, until: out.until } }));
        drop(id, 2500);
      } else {
        const action = act === "decline" ? "declined" : "dismissed";
        await post(`${API}/dismiss`, { id, action });
        // SP-503: a page that points here for this row (the overview's 測試) takes it back
        window.dispatchEvent(new CustomEvent("suggestions:dismissed", { detail: { id, action, type: (rows.find((x) => x.id === id) || {}).type } }));
        drop(id);
      }
    } catch (err) {
      el.querySelectorAll("button").forEach((x) => { x.disabled = false; });
      el.insertAdjacentHTML("beforeend", `<div class="sg-err">${esc(err.message)}</div>`);
    }
  });

  // one GET per page load (SP-362): the box and the page (課表's 排入測試 ▸, schedule.html
  // sugCount) share it; `fresh` asks again (after a change). Resolves to the rows or null — `all`: the whole
  // answer (SP-507 R1: the 課表 also offers the tests 稍後提醒 holds, `snoozed_tests`)
  let shared = null, sharedAll = null;
  function load(fresh, all) {
    if (!fresh && shared) return all ? sharedAll : shared;
    const req = fetch(API, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    sharedAll = req;
    shared = req.then((j) => (j ? j.suggestions || [] : null));
    // SP-471: every load's rows, for a page's own view of them (static/eptest.js); SP-503: with the
    // rows 「不要」 / ✕ hid ({id, type, action}: the overview's 測試 待辦 row comes back for those)
    // SP-507: and the rows 稍後提醒 hid ({id, type, until}: still the box's, the overview keeps pointing there)
    req.then((j) => { if (j) window.dispatchEvent(new CustomEvent("suggestions:loaded", { detail: { rows: j.suggestions || [], declined: j.declined || [], snoozed: j.snoozed || [] } })); });
    return all ? sharedAll : shared;
  }

  async function refresh(fresh = true) {
    const got = await load(fresh);
    if (got == null) return;                 // the box is optional: no box when it can't load
    rows = got;
    render();
  }

  // SP-483: a page's 「在建議框」 link — the box opened (not collapsed) and brought into view
  function open() {
    if (!rows.length) return false;
    if (ls.get()) { ls.set(false); render(); }
    const head = box.querySelector(".sg-head");
    box.scrollIntoView && box.scrollIntoView({ block: "nearest" });
    if (head) head.focus();
    return true;
  }

  window.AppSuggestions = { refresh, load, open };
  window.dispatchEvent(new CustomEvent("suggestions:ready"));
  const start = () => { mount(); setTimeout(() => refresh(false), 900); };   // after the page's own first requests
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
