/* Offline read-only copy (SP-314): the rules and the engine of the service worker (/sw.js).
 *
 * Loaded by /sw.js (importScripts) and by the node tests (require). Everything it touches is
 * injected (caches, fetch, the clock), so the whole behaviour runs under node with fakes
 * (backend/tests/test_offline_pwa.py).
 *
 * What is kept, per tier (SP-319, docs/research/fit-on-device.md §9.2; one fixed budget, 20 MB):
 *   app  the program: the page scripts and the chart library            (shared, not anyone's data)
 *   p0   總覽 / 課表 pages and their data: status, this week, the calendar of this and the next
 *        weeks / months, the period totals, the PMC of the last 7 / 42 / 90 days, the settings
 *   p1   the activity list (every activity's summary) and the achievements
 *   p2   nothing yet: the FIT files and the 1-year chart data need an API first (SP-325)
 * Only GET, only the paths listed here. Writes are never cached and never queued.
 *
 * Whose copy: the person signed in (GET /api/v1/session -> userKeyOf). The p0–p2 caches are
 * named after them (trc-u-<key>-<tier>); another person signing in, a sign-out, a 401 or a page
 * answered by a redirect to the sign-in page deletes every person's copy (clearUser).
 *
 * Strategy: network first. The copy answers when the network fails, answers 5xx (the tunnel's
 * 502 / 530 while the server is down), or takes longer than TIMEOUT_MS (the network's answer
 * still refreshes the copy). Answers from the copy carry x-trc-cache: offline | slow and
 * x-trc-stored-at; a page from the copy gets window.TRC_OFFLINE_PAGE (shell.js shows the bar).
 * Refill (sync): p0 first, then p1, then p2 — at once when that tier's cache is missing (the
 * browser cleared it), else when it is older than REFRESH_MS. Over the budget, or on a
 * QuotaExceededError: p2 goes first, then p1; p0 and the program stay.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TrcOffline = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const MB = 1024 * 1024;
  const BUDGET = 20 * MB;
  const TIERS = ["p0", "p1", "p2"];
  const REFRESH_MS = { p0: 15 * 60e3, p1: 6 * 3600e3, p2: 24 * 3600e3 };
  const TIMEOUT_MS = { page: 6000, app: 6000, api: 12000 };
  const PREFIX = "trc-";
  const USER_PREFIX = "trc-u-";
  const META_CACHE = "trc-meta";
  const META_KEY = "/__trc/state";
  const HDR = { stored: "x-trc-stored-at", cache: "x-trc-cache" };
  const OFFLINE_PAGE = "/api/v1/static/offline.html";
  const SESSION = "/api/v1/session";

  // the program: every local script of the cached pages, and what shell.js loads by itself
  const APP_FILES = [
    "/api/v1/static/shell.js", "/api/v1/static/i18n/i18n.js", "/api/v1/static/offline_core.js",
    "/api/v1/static/autosync.js", "/api/v1/static/suggestions.js", "/api/v1/static/region.js",
    "/api/v1/static/setup_wizard.js", "/api/v1/static/dashicons.js", "/api/v1/static/sesschip.js",
    "/api/v1/static/z5flow.js",
    "/api/v1/static/plan_mark.js", "/api/v1/static/autoplan.js", "/api/v1/static/session_banner.js",
    "/api/v1/static/workout_editor.js", "/api/v1/static/syncrun.js", "/api/v1/static/fitimport.js",
    "/api/v1/static/painpicker.js",
  ];
  // the chart library of 總覽 (fetched with CORS, never as an opaque answer)
  const CDN = ["https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js"];
  const PAGES = {
    "/api/v1/overview/page": "p0",
    "/api/v1/overview/plan/schedule/page": "p0",
    [OFFLINE_PAGE]: "p0",
    "/api/v1/wko5/activities/page": "p1",
  };
  const API = {
    "/api/v1/overview/status": "p0",
    "/api/v1/overview/weekplan": "p0",
    "/api/v1/overview/z5": "p0",
    "/api/v1/overview/b2b": "p0",
    "/api/v1/overview/summary": "p0",
    "/api/v1/overview/pmc": "p0",
    "/api/v1/overview/plan/calendar": "p0",
    "/api/v1/overview/plan/prefs": "p0",
    "/api/v1/overview/plan/prefs/gate": "p0",
    "/api/v1/overview/plan/equivalence": "p0",
    "/api/v1/sync/settings": "p0",
    "/api/v1/region": "p0",
    "/api/v1/wko5/activities": "p1",
    "/api/v1/wko5/activities/stats": "p1",
    "/api/v1/achievements": "p1",
  };
  // a calendar for other days than asked would show an empty week: only a range that covers it
  const STRICT_RANGE = new Set(["/api/v1/overview/plan/calendar"]);
  const DATE_PARAMS = new Set(["start", "end", "begin", "anchor", "day"]);
  // navigations never touched: sign-in, the public shares, the worker itself
  const NO_NAV = [/^\/login$/, /^\/signup$/, /^\/oauth\//, /^\/api\/v1\/account\//, /^\/share(\/|$)/,
    /^\/healthz$/, /^\/sw\.js$/, /^\/manifest\.webmanifest$/];

  const SUMMARY = [["week", 16], ["month", 12], ["year", 6]];     // overview.html's units
  const PMC_DAYS = [7, 42, 90];                                    // overview.html's PMC ranges
  const WARM = {
    p0: [
      "/api/v1/overview/page", "/api/v1/overview/plan/schedule/page", OFFLINE_PAGE,
      "/api/v1/overview/status", "/api/v1/overview/weekplan", "/api/v1/overview/z5", "/api/v1/overview/b2b",
      "/api/v1/sync/settings", "/api/v1/region", "/api/v1/overview/plan/prefs", "/api/v1/overview/plan/prefs/gate",
      "/api/v1/overview/plan/equivalence",
      ...SUMMARY.map(([u, n]) => `/api/v1/overview/summary?unit=${u}&n=${n}`),
    ],
    p1: ["/api/v1/wko5/activities/page", "/api/v1/wko5/activities", "/api/v1/wko5/activities/stats",
      "/api/v1/achievements?include_hidden=true"],
    p2: [],                       // FIT and the 1-year chart data: no API yet (SP-325)
  };

  // ---------------------------------------------------------------- dates (UTC: no DST surprises)
  const isIso = (s) => typeof s === "string" && /^\d{4}-\d\d-\d\d$/.test(s);
  const dOf = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const isoOf = (d) => d.toISOString().slice(0, 10);
  const addDays = (s, n) => { const d = dOf(s); d.setUTCDate(d.getUTCDate() + n); return isoOf(d); };
  const mondayOf = (s) => addDays(s, -((dOf(s).getUTCDay() + 6) % 7));
  /** schedule.html's month view: Monday of the 1st … Sunday of the last day's week (month k from s) */
  function monthGrid(s, k) {
    const d = dOf(s), y = d.getUTCFullYear(), m = d.getUTCMonth() + k;
    const first = isoOf(new Date(Date.UTC(y, m, 1))), last = isoOf(new Date(Date.UTC(y, m + 1, 0)));
    return [mondayOf(first), addDays(mondayOf(last), 6)];
  }
  const localToday = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

  // ---------------------------------------------------------------- pure rules

  /** What a GET is: {kind: app | page | api, tier} or null (never cached). */
  function classify(input, origin, navigate) {
    let u;
    try { u = new URL(input, origin); } catch (_) { return null; }
    if (u.origin !== origin) return CDN.includes(u.href) ? { kind: "app", tier: "p0" } : null;
    const p = u.pathname;
    if (Object.prototype.hasOwnProperty.call(PAGES, p)) return { kind: "page", tier: PAGES[p] };
    if (navigate) return null;
    // the static files are the program; a page opened by file name carries its reader's session
    if (p.startsWith("/api/v1/static/")) return p.endsWith(".html") ? null : { kind: "app", tier: "p0" };
    if (Object.prototype.hasOwnProperty.call(API, p)) return { kind: "api", tier: API[p] };
    return null;
  }

  /** Whose copy a session (GET /api/v1/session, or the page's TRC_SESSION) is: "u<id>" signed in,
   * "owner" in the single-user app, null signed out / in the demo / unknown. */
  function userKeyOf(s) {
    if (!s || typeof s !== "object" || s.mode === "demo" || s.mode === "signed_out") return null;
    const id = s.user && s.user.id;
    if (Number.isInteger(id)) return "u" + id;
    if (s.login && s.login.required) return null;
    return s.mode === "owner" ? "owner" : null;
  }

  const userCache = (key, tier) => `${USER_PREFIX}${key}-${tier}`;
  const appCache = (version) => `${PREFIX}app-${version}`;

  /** p0's URLs that depend on the day: the calendars and the PMC ranges the pages ask for.
   * ctx = {today (the server's, GET /status), week: {start, end} (GET /weekplan)}. */
  function warmDerived(ctx) {
    const out = [];
    const today = ctx && ctx.today;
    if (!isIso(today)) return out;
    const C = "/api/v1/overview/plan/calendar";
    const wk = ctx.week;
    if (wk && isIso(wk.start) && isIso(wk.end)) out.push(`${C}?start=${wk.start}&end=${wk.end}`);   // 總覽's week
    for (let k = 0; k < 3; k++) {                                   // 課表 on a phone: this and the next 2 weeks
      const a = addDays(mondayOf(today), 7 * k);
      out.push(`${C}?start=${a}&end=${addDays(a, 6)}`);
    }
    for (let k = 0; k < 2; k++) {                                   // 課表 on a wide screen: this and next month
      const [a, b] = monthGrid(today, k);
      out.push(`${C}?start=${a}&end=${b}`);
    }
    for (const n of PMC_DAYS) out.push(`/api/v1/overview/pmc?begin=${addDays(today, -n)}&end=${today}`);
    return [...new Set(out)];
  }

  function rangeOf(u) {
    const a = u.searchParams.get("start") || u.searchParams.get("begin"), b = u.searchParams.get("end");
    return isIso(a) && isIso(b) ? [a, b] : null;
  }
  const spanDays = (r) => (dOf(r[1]) - dOf(r[0])) / 864e5;
  const newest = (xs) => xs.reduce((a, b) => ((b.at || 0) > (a.at || 0) ? b : a));

  /** The copy to answer with when the exact URL isn't kept (another day, another period).
   * candidates: [{url, at}]. Pages and the program: the same path, newest. Data: the same path
   * and the same non-date parameters; a range asked for prefers the narrowest copy covering it,
   * else the most overlap (never for STRICT_RANGE); newest on a tie. */
  function pickFallback(req, candidates, origin) {
    let r;
    try { r = new URL(req, origin); } catch (_) { return null; }
    const same = [];
    for (const c of candidates || []) {
      try { const u = new URL(c.url, origin); if (u.origin === r.origin && u.pathname === r.pathname) same.push({ ...c, u }); } catch (_) { /* skip */ }
    }
    if (!same.length) return null;
    if (!Object.prototype.hasOwnProperty.call(API, r.pathname)) return newest(same).url;
    const idOf = (u) => [...u.searchParams].filter(([k]) => !DATE_PARAMS.has(k)).map(([k, v]) => k + "=" + v).sort().join("&");
    const want = idOf(r);
    const pool = same.filter((c) => idOf(c.u) === want);
    if (!pool.length) return null;
    const rr = rangeOf(r);
    if (!rr) return newest(pool).url;
    const ranged = pool.map((c) => ({ ...c, r: rangeOf(c.u) })).filter((c) => c.r);
    const cover = ranged.filter((c) => c.r[0] <= rr[0] && c.r[1] >= rr[1]);
    if (cover.length) {
      const narrow = Math.min(...cover.map((c) => spanDays(c.r)));
      return newest(cover.filter((c) => spanDays(c.r) === narrow)).url;
    }
    if (STRICT_RANGE.has(r.pathname)) return null;
    const overlap = (c) => Math.max(0, (dOf(c.r[1] < rr[1] ? c.r[1] : rr[1]) - dOf(c.r[0] > rr[0] ? c.r[0] : rr[0])) / 864e5 + 1);
    const best = Math.max(0, ...ranged.map(overlap));
    const top = ranged.filter((c) => overlap(c) === best);
    return top.length ? newest(top).url : newest(pool).url;
  }

  /** Over the budget: which entries to drop. p2 first, then p1, the oldest first in a tier;
   * p0 and the program are never dropped. entries: [{key, tier, kind, bytes, at}]. */
  function planEviction(entries, budget) {
    let total = (entries || []).reduce((s, e) => s + (e.bytes || 0), 0);
    const drop = [];
    for (const tier of ["p2", "p1"]) {
      const list = entries.filter((e) => e.tier === tier && e.kind !== "app").sort((a, b) => (a.at || 0) - (b.at || 0));
      for (const e of list) {
        if (total <= budget) break;
        drop.push(e);
        total -= e.bytes || 0;
      }
    }
    return { drop, total, over: total > budget };
  }

  /** A page answered from the copy: says so to shell.js before any script of the page runs. */
  function markOfflineHtml(html, at, reason) {
    const tag = `<script>window.TRC_OFFLINE_PAGE = ${JSON.stringify({ at: at || null, reason: reason || "offline" }).replace(/</g, "\\u003c")};</script>`;
    const m = /<head\b[^>]*>/i.exec(html);
    return m ? html.slice(0, m.index + m[0].length) + tag + html.slice(m.index + m[0].length) : tag + html;
  }

  // the tiers a tier may push out when the browser says the storage is full
  const YIELDS = { p0: ["p2", "p1"], p1: ["p2"], p2: [] };
  const isQuota = (e) => !!e && (e.name === "QuotaExceededError" || e.code === 22);

  // ---------------------------------------------------------------- the engine

  /** opt: {caches, fetch, origin, version, now?, budget?, timeouts?} */
  function createEngine(opt) {
    const caches = opt.caches, net = opt.fetch, origin = opt.origin;
    const now = opt.now || Date.now;
    const version = opt.version || "dev";
    const budget = opt.budget || BUDGET;
    const timeouts = Object.assign({}, TIMEOUT_MS, opt.timeouts || {});
    const APP = appCache(version);
    let state = null;
    let gen = 0;                  // bumped by every change of person: work for the previous one stops
    let chain = Promise.resolve();
    const serial = (f) => (chain = chain.then(f, f));

    const fresh = () => ({ user: null, tiers: {}, entries: {} });
    async function load() {
      if (state) return state;
      let s = null;
      try {
        const r = await caches.match(META_KEY, { cacheName: META_CACHE });
        s = r ? await r.json() : null;
      } catch (_) { s = null; }
      if (state) return state;     // loaded meanwhile
      state = s && typeof s === "object" ? s : fresh();
      state.tiers = state.tiers || {};
      state.entries = state.entries || {};
      return state;
    }
    function save() {
      return serial(async () => {
        const snap = JSON.stringify(state || fresh());
        const c = await caches.open(META_CACHE);
        await c.put(META_KEY, new Response(snap, { headers: { "content-type": "application/json" } }));
      }).catch(() => {});
    }

    /** Forget every person's copy (signed out, another person, a 401). The program stays. */
    async function clearUser() {
      gen++;
      const st = await load();
      st.user = null;
      st.tiers = {};
      for (const k of Object.keys(st.entries)) if (st.entries[k].kind !== "app") delete st.entries[k];
      await serial(async () => {
        const names = await caches.keys();
        await Promise.all(names.filter((n) => n.startsWith(USER_PREFIX)).map((n) => caches.delete(n)));
      }).catch(() => {});
      await save();
    }

    /** The person this browser is for now (null: nobody). Another one than before: the old copy goes. */
    async function setUser(key) {
      const st = await load();
      if (key && st.user === key) return false;
      await clearUser();
      if (key) { st.user = key; await save(); }
      return true;
    }
    const setSession = (session) => setUser(userKeyOf(session));

    const entryKey = (cacheName, url) => cacheName + " " + url;
    async function bytesOf(resp) { return (await resp.clone().arrayBuffer()).byteLength; }

    function storedCopy(buf, resp) {
      const h = new Headers(resp.headers);
      h.set(HDR.stored, new Date(now()).toISOString());
      for (const k of [HDR.cache, "content-length", "content-encoding", "set-cookie"]) h.delete(k);
      return new Response(buf, { status: resp.status, statusText: resp.statusText, headers: h });
    }

    async function dropEntries(list) {
      const st = await load();
      for (const e of list) {
        try { const c = await caches.open(e.cache); await c.delete(e.url); } catch (_) { /* gone already */ }
        delete st.entries[entryKey(e.cache, e.url)];
      }
    }

    async function enforce() {
      const st = await load();
      const list = Object.values(st.entries);
      const plan = planEviction(list, budget);
      if (plan.drop.length) await dropEntries(plan.drop);
      return plan;
    }

    /** Keep an answer (a 200, not redirected) under its URL; false when it wasn't kept. */
    async function store(cacheName, url, resp, tier, kind, g) {
      if (g !== gen) return false;
      let buf;
      try { buf = await resp.arrayBuffer(); } catch (_) { return false; }
      const st = await load();
      for (let i = 0; ; i++) {
        if (g !== gen) return false;
        try {
          const cache = await caches.open(cacheName);
          await cache.put(url, storedCopy(buf, resp));
          if (g !== gen) { await cache.delete(url); return false; }      // the person changed meanwhile
          break;
        } catch (e) {
          const yields = YIELDS[tier] || [];
          if (!isQuota(e) || i >= yields.length) return false;
          await dropEntries(Object.values(st.entries).filter((x) => x.tier === yields[i] && x.kind !== "app"));
        }
      }
      st.entries[entryKey(cacheName, url)] = { cache: cacheName, url, tier, kind, bytes: buf.byteLength, at: now() };
      await enforce();
      save();
      return true;
    }

    async function findCached(cacheName, key) {
      try {
        if (!(await caches.has(cacheName))) return null;
        const cache = await caches.open(cacheName);
        const hit = await cache.match(key);
        if (hit) return hit;
        const st = await load();
        const keys = await cache.keys();
        const cands = keys.map((k) => { const url = typeof k === "string" ? k : k.url; return { url, at: (st.entries[entryKey(cacheName, url)] || {}).at || 0 }; });
        const pick = pickFallback(key, cands, origin);
        return pick ? cache.match(pick) : null;
      } catch (_) { return null; }
    }

    /** An answer from the copy, marked (header; a page also gets TRC_OFFLINE_PAGE). */
    async function served(cached, reason, isPage) {
      const at = cached.headers.get(HDR.stored) || null;
      const h = new Headers(cached.headers);
      h.set(HDR.cache, reason);
      h.delete("content-length");
      if (isPage) return new Response(markOfflineHtml(await cached.text(), at, reason), { status: 200, headers: h });
      return new Response(await cached.arrayBuffer(), { status: cached.status, statusText: cached.statusText, headers: h });
    }

    async function offlinePage(reason) {
      const st = await load();
      if (!st.user) return null;
      const hit = await findCached(userCache(st.user, "p0"), origin + OFFLINE_PAGE);
      return hit ? served(hit, reason, true) : null;
    }

    const keepable = (r) => r && r.status === 200 && !r.redirected && (r.type === "basic" || r.type === "cors" || r.type === "default");
    const signedOut = (r, c) => c.kind !== "app" && (r.status === 401 || (c.kind === "page" && (r.type === "opaqueredirect" || r.redirected)));
    const delay = (ms) => new Promise((res) => setTimeout(() => res({ timeout: true }), ms));

    async function networkFirst(request, key, c, cacheName, ctx) {
      const g = gen;
      const isPage = c.kind === "page";
      const crossApp = c.kind === "app" && !key.startsWith(origin + "/");
      const netReq = crossApp ? new Request(key, { mode: "cors", credentials: "omit" }) : request;
      const netP = Promise.resolve().then(() => net(netReq)).then(
        (r) => ({ r, copy: keepable(r) ? r.clone() : null }),
        (e) => ({ e }));
      // the network's answer refreshes the copy, also when it arrives after the copy was served
      const settle = netP.then(async ({ r, copy }) => {
        if (!r) return;
        if (signedOut(r, c)) { await clearUser(); return; }
        if (copy) await store(cacheName, key, copy, c.tier, c.kind, g);
      }).catch(() => {});
      if (ctx && ctx.waitUntil) ctx.waitUntil(settle);
      const cached = await findCached(cacheName, key);
      const res = cached ? await Promise.race([netP, delay(timeouts[c.kind] || TIMEOUT_MS.api)]) : await netP;
      if (res.r && !(res.r.status >= 500 && cached)) return res.r;
      if (cached) return served(cached, res.timeout ? "slow" : "offline", isPage);
      if (isPage) { const p = await offlinePage("offline"); if (p) return p; }
      if (res.e) throw res.e;
      return res.r;
    }

    async function navigateOnly(request) {
      try { return await net(request); } catch (e) {
        const p = await offlinePage("offline");
        if (p) return p;
        throw e;
      }
    }

    /** The worker's fetch event: a Promise of the answer, or null to leave the request alone. */
    function handleFetch(request, ctx) {
      if (!request || String(request.method || "GET").toUpperCase() !== "GET") return null;     // writes: never
      const navigate = request.mode === "navigate";
      let url;
      try { url = new URL(request.url); } catch (_) { return null; }
      const c = classify(url.href, origin, navigate);
      if (!c) {
        if (navigate && url.origin === origin && !NO_NAV.some((rx) => rx.test(url.pathname))) return navigateOnly(request);
        return null;
      }
      if (c.kind === "app") return networkFirst(request, url.href, c, APP, ctx);
      return (async () => {
        const st = await load();
        if (!st.user) return net(request);                  // nobody known yet: no copy, nothing kept
        return networkFirst(request, url.href, c, userCache(st.user, c.tier), ctx);
      })();
    }

    async function warmOne(path, tier, g) {
      const url = origin + path;
      const c = classify(url, origin, false);
      const st = await load();
      if (!c || !st.user || g !== gen) return false;
      try {
        const r = await net(url, { credentials: "same-origin" });
        if (signedOut(r, c)) { await clearUser(); return false; }
        if (!keepable(r)) return false;
        return await store(userCache(st.user, tier), url, r, tier, c.kind, g);
      } catch (_) { return false; }
    }

    async function contextOf(key) {
      const read = async (p) => {
        try { const r = await findCached(userCache(key, "p0"), origin + p); return r ? await r.json() : null; } catch (_) { return null; }
      };
      const s = await read("/api/v1/overview/status"), w = await read("/api/v1/overview/weekplan");
      return { today: (s && isIso(s.today) && s.today) || localToday(now()), week: w && w.week };
    }

    let syncing = null;
    /** Who is signed in (asks the server), then refill p0 → p1 → p2: a tier whose cache is gone
     * at once, else when older than REFRESH_MS (force: p0 now, e.g. back online). */
    function sync(o) {
      if (!syncing) syncing = doSync(o || {}).finally(() => { syncing = null; });
      return syncing;
    }
    async function doSync(o) {
      let s;
      try {
        const r = await net(origin + SESSION, { credentials: "same-origin", cache: "no-store" });
        if (!r.ok) return { ok: false };
        s = await r.json();
      } catch (_) { return { ok: false, offline: true }; }
      const key = userKeyOf(s);
      if (!key) { await clearUser(); return { ok: true, user: null, done: [] }; }
      await setUser(key);
      const g = gen;
      const st = await load();
      const done = [];
      for (const tier of TIERS) {
        const name = userCache(key, tier);
        const t = st.tiers[tier];
        const present = await caches.has(name);
        const due = !present || !t || now() - t.at >= REFRESH_MS[tier] || (o.force && tier === "p0");
        if (!due) continue;
        await caches.open(name);                            // an empty tier is there too (not "cleared")
        let ok = 0, failed = 0;
        const run = async (urls) => {
          for (const u of urls) {
            if (g !== gen) return false;
            (await warmOne(u, tier, g)) ? ok++ : failed++;
          }
          return true;
        };
        if (!(await run(WARM[tier]))) return { ok: false, aborted: true, done };
        // the day's calendars and PMC ranges: from the server's today and week just kept
        if (tier === "p0" && !(await run(warmDerived(await contextOf(key))))) return { ok: false, aborted: true, done };
        if (g !== gen) return { ok: false, aborted: true, done };
        st.tiers[tier] = { at: now(), ok, failed };
        await save();
        done.push(tier);
      }
      return { ok: true, user: key, done };
    }

    async function install() {
      const cache = await caches.open(APP);
      const st = await load();
      for (const u of APP_FILES.map((p) => origin + p).concat(CDN)) {
        try {
          const cross = !u.startsWith(origin + "/");
          const r = await net(cross ? new Request(u, { mode: "cors", credentials: "omit" }) : u, cross ? undefined : { cache: "no-cache" });
          if (!keepable(r)) continue;
          const buf = await r.arrayBuffer();
          await cache.put(u, storedCopy(buf, r));
          st.entries[entryKey(APP, u)] = { cache: APP, url: u, tier: "p0", kind: "app", bytes: buf.byteLength, at: now() };
        } catch (_) { /* offline or blocked: kept when a page loads it */ }
      }
      await save();
    }

    /** A new version: the older programs go (the people's copies stay). */
    async function activate() {
      const st = await load();
      const names = await caches.keys();
      const old = names.filter((n) => n.startsWith(PREFIX + "app-") && n !== APP);
      await Promise.all(old.map((n) => caches.delete(n)));
      for (const k of Object.keys(st.entries)) if (st.entries[k].kind === "app" && st.entries[k].cache !== APP) delete st.entries[k];
      await save();
      return old;
    }

    /** For the settings page (SP-322): whose copy, how big per tier, when refreshed, and whether
     * each tier's cache is still there (the browser may delete one without telling the bookkeeping). */
    async function status() {
      const st = await load();
      const by = { app: 0, p0: 0, p1: 0, p2: 0 };
      for (const e of Object.values(st.entries)) by[e.kind === "app" ? "app" : e.tier] += e.bytes || 0;
      const present = {};
      for (const tier of TIERS) {
        try { present[tier] = !!st.user && (await caches.has(userCache(st.user, tier))); } catch (_) { present[tier] = false; }
      }
      return { user: st.user, version, budget, bytes: by, total: by.app + by.p0 + by.p1 + by.p2, tiers: st.tiers, present };
    }

    return { handleFetch, sync, setUser, setSession, clearUser, install, activate, status, enforce, bytesOf,
      _state: () => state, _gen: () => gen };
  }

  return {
    BUDGET, TIERS, REFRESH_MS, TIMEOUT_MS, APP_FILES, CDN, PAGES, API, WARM, OFFLINE_PAGE, HDR, NO_NAV,
    USER_PREFIX, META_CACHE, STRICT_RANGE,
    classify, userKeyOf, userCache, appCache, warmDerived, pickFallback, planEviction, markOfflineHtml,
    monthGrid, mondayOf, addDays, createEngine,
  };
});
