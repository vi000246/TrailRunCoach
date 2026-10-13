/* 這台裝置的離線資料 (SP-322): 設定 › 資料同步 shows what the offline copy (SP-314) keeps on this
 * device, so a runner knows whether 總覽 / 課表 will open without signal and whether the browser
 * may delete it.
 *
 *   用量       navigator.storage.estimate().usage (everything this site keeps here) against the
 *              fixed 20 MB budget (the same BUDGET as offline_core.js; the user does not pick it),
 *              and the copy's own size (the worker's bookkeeping)
 *   P0/P1/P2   per tier: kept / partly / not kept / pushed out over the budget / nothing to keep /
 *              not yet — from the worker's status() (AppOffline.status(), shell.js): the tiers are
 *              offline_core.js's, never recomputed here
 *   持久保存   navigator.storage.persisted(); not yet → a button asks (navigator.storage.persist())
 *   最後更新   the newest tier refill
 *   加到主畫面 on an iPhone / iPad browser tab (not the home-screen app): Safari deletes a tab's
 *              storage after 7 days of Safari use without opening the site (fit-on-device.md §3.2)
 * A browser without one of these APIs shows 無法取得 for it; nothing here throws.
 *
 * window.DeviceStorage = {BUDGET, isIosTab, envOf, read, view, html, ask, mount}. Pure apart from
 * mount (tested under node: backend/tests/test_device_storage_sp322.py). Texts: settings.device.*.
 */
(function (root) {
  "use strict";

  const MB = 1024 * 1024;
  const BUDGET = 20 * MB;                 // offline_core.js BUDGET (a test keeps them equal)
  const TIERS = ["p0", "p1", "p2"];

  /** An iPhone / iPad browser tab (not the home-screen app). env: {ua, platform, maxTouchPoints,
   * standalone (navigator.standalone, Safari's), displayStandalone (display-mode: standalone)}. */
  function isIosTab(env) {
    const e = env || {};
    const ios = /iPhone|iPad|iPod/.test(e.ua || "") || (e.platform === "MacIntel" && (e.maxTouchPoints || 0) > 1);
    return ios && e.standalone !== true && !e.displayStandalone;
  }

  function envOf(win) {
    const w = win || {}, n = w.navigator || {};
    let dm = false;
    try { dm = !!(w.matchMedia && w.matchMedia("(display-mode: standalone)").matches); } catch (_) { dm = false; }
    return { ua: n.userAgent || "", platform: n.platform || "", maxTouchPoints: n.maxTouchPoints || 0,
      standalone: n.standalone === true, displayStandalone: dm };
  }

  async function safe(f) {
    try { return await f(); } catch (_) { return undefined; }
  }
  const fn = (o, k) => !!o && typeof o[k] === "function";
  const okStatus = (s) => !!s && typeof s === "object" && !!s.bytes && typeof s.bytes === "object";

  /** What the browser and the worker say. src: {storage: navigator.storage, offline: AppOffline}. */
  async function read(src) {
    const s = (src && src.storage) || null, off = (src && src.offline) || null;
    const est = await safe(() => (fn(s, "estimate") ? s.estimate() : undefined));
    const per = await safe(() => (fn(s, "persisted") ? s.persisted() : undefined));
    const st = await safe(() => (fn(off, "status") ? off.status() : undefined));
    const usage = est && typeof est === "object" ? est.usage : undefined;
    return {
      estimate: typeof usage === "number" && isFinite(usage) && usage >= 0
        ? { usage, quota: typeof est.quota === "number" ? est.quota : null } : null,
      persisted: typeof per === "boolean" ? per : null,
      canAsk: fn(s, "persist"),
      status: okStatus(st) ? st : null,
    };
  }

  const mbOf = (b) => ((b || 0) / MB).toFixed(1);

  function tierOf(st, tier) {
    if (!st) return { tier, state: "na" };
    const bytes = (st.bytes && st.bytes[tier]) || 0;
    const t = st.tiers && st.tiers[tier];
    const present = st.present ? !!st.present[tier] : true;      // a worker from before SP-322: trust its record
    const base = { tier, mb: mbOf(bytes) };
    if (!st.user || !t || !present) return { ...base, state: "none" };
    const ok = t.ok || 0, failed = t.failed || 0;
    let state;
    if (!ok && !failed) state = "empty";                  // nothing to keep in this tier (P2 until SP-325)
    else if (!ok) state = "failed";
    else if (!(bytes > 0)) state = "evicted";             // kept, then pushed out over the budget / quota
    else if (failed) state = "partial";
    else state = "ok";
    return { ...base, state, at: t.at || null, failed };
  }

  /** The block's model. opts: {a2hs: show the home-screen hint}. */
  function view(data, opts) {
    const d = data || {}, o = opts || {};
    const st = d.status || null;
    let usage = { state: "na" };
    if (d.estimate) {
      const u = d.estimate.usage;
      usage = { state: "ok", mb: mbOf(u), pct: Math.min(100, Math.round((u / BUDGET) * 100)) };
      if (u > BUDGET) usage.over = true;
      if (st && typeof st.total === "number") usage.copyMb = mbOf(st.total);
    }
    let updated = { state: "na" };
    if (st) {
      const ats = st.user ? Object.values(st.tiers || {}).map((x) => x && x.at).filter((x) => typeof x === "number" && isFinite(x)) : [];
      updated = ats.length ? { state: "ok", at: Math.max(...ats) } : { state: "never" };
    }
    const persisted = d.persisted === true ? "yes" : d.persisted === false ? "no" : "na";
    return { budgetMb: BUDGET / MB, usage, tiers: TIERS.map((k) => tierOf(st, k)), updated, persisted,
      canAsk: persisted === "no" && !!d.canAsk, a2hs: !!o.a2hs };
  }

  /** Ask for persistent storage: "granted" | "denied" | "na" (no API, or it threw). */
  async function ask(storage) {
    if (!fn(storage, "persist")) return "na";
    try { return (await storage.persist()) === true ? "granted" : "denied"; } catch (_) { return "na"; }
  }

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const two = (n) => String(n).padStart(2, "0");
  function when(ms) {
    const d = new Date(ms);
    return isNaN(d) ? "" : `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
  }

  /** The block's HTML. t: the page's I18N.t; extra: {asked: the answer of the last ask()}. */
  function html(m, t, extra) {
    const T = (k, p) => esc(t("settings.device." + k, p));
    const x = extra || {};
    const rows = [];
    // 用量
    const u = m.usage;
    let usage;
    if (u.state === "ok") {
      usage = `${T("usage", { mb: u.mb, budget: m.budgetMb })}`
        + (u.copyMb != null ? ` <span class="meta">${T("usage_copy", { mb: u.copyMb })}</span>` : "")
        + `<div class="ds-bar${u.over ? " over" : ""}" aria-hidden="true"><i style="width:${u.pct}%"></i></div>`
        + (u.over ? `<div class="meta ds-warn">${T("usage_over")}</div>` : "");
    } else usage = `<span class="ds-na">${T("usage_na", { budget: m.budgetMb })}</span>`;
    rows.push([T("usage_k"), usage]);
    // P0 / P1 / P2
    const STATE = {
      ok: () => T("state_ok"), partial: (r) => T("state_partial", { n: r.failed }), failed: () => T("state_failed"),
      evicted: () => T("state_evicted"), empty: () => T("state_empty"), none: () => T("state_none"), na: () => T("na"),
    };
    const WHAT = { p0: () => T("p0_what"), p1: () => T("p1_what"), p2: () => T("p2_what") };
    const LABEL = { p0: () => T("p0"), p1: () => T("p1"), p2: () => T("p2") };
    for (const r of m.tiers) {
      const size = r.state === "ok" || r.state === "partial" ? ` · ${esc(r.mb)} MB` : "";
      rows.push([LABEL[r.tier](), `<span class="ds-st ds-${r.state}">${STATE[r.state](r)}</span>${size}`
        + ` <span class="meta">${WHAT[r.tier]()}</span>`]);
    }
    // 持久保存
    let per = m.persisted === "yes" ? T("persist_yes") : m.persisted === "no" ? T("persist_no") : `<span class="ds-na">${T("na")}</span>`;
    if (m.canAsk) per += ` <button type="button" data-ds-persist>${T("persist_btn")}</button>`;
    if (x.asked === "granted") per += `<div class="meta ok-text">${T("persist_granted")}</div>`;
    else if (x.asked === "denied") per += `<div class="meta">${T("persist_denied")}</div>`;
    else if (x.asked === "na") per += `<div class="meta">${T("persist_na")}</div>`;
    rows.push([T("persist_k"), per]);
    // 最後更新
    const up = m.updated;
    const upd = up.state === "ok" ? esc(when(up.at)) : up.state === "never" ? T("updated_never") : `<span class="ds-na">${T("na")}</span>`;
    rows.push([T("updated_k"), `${upd} <button type="button" data-ds-refresh>${T("refresh")}</button>`
      + `<div class="meta">${T("updated_note")}</div>`]);
    let out = `<dl class="kv ds-kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
    if (m.a2hs) out += `<div class="ds-a2hs" role="note"><b>${T("a2hs_title")}</b><p>${T("a2hs")}</p></div>`;
    return out;
  }

  /** Read, draw, and wire the buttons into el. opts: {win, t}. */
  async function mount(el, opts) {
    const o = opts || {};
    const win = o.win || root;
    const t = o.t || ((k, p) => (win.I18N ? win.I18N.t(k, p) : k));
    const src = () => ({ storage: (win.navigator && win.navigator.storage) || null, offline: win.AppOffline || null });
    const a2hs = isIosTab(envOf(win));
    let asked = null;
    async function draw() {
      let m;
      try { m = view(await read(src()), { a2hs }); } catch (_) { m = view({}, { a2hs }); }
      el.innerHTML = html(m, t, { asked });
      const p = el.querySelector("[data-ds-persist]");
      if (p) p.addEventListener("click", async () => { p.disabled = true; asked = await ask(src().storage); await draw(); });
      const r = el.querySelector("[data-ds-refresh]");
      if (r) r.addEventListener("click", async () => { r.disabled = true; asked = null; await draw(); });
    }
    await draw();
    return { refresh: draw };
  }

  root.DeviceStorage = { BUDGET, isIosTab, envOf, read, view, html, ask, mount };
})(typeof window !== "undefined" ? window : this);
