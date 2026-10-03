/* TrailRunCoach static demo shim (backend/demo/export_static.py injects it first in every page).
 *
 * The static demo has no server. This file
 *   - maps the pages' /api/v1/... requests to the JSON files the export saved
 *     (data/<fnv64(key)>.json; key = decoded path + "?" + query sorted by name);
 *   - answers the view-only computations (race calculator ...) the export
 *     precomputed for the pages' default inputs (data/p<fnv64(method key body)>.json);
 *   - keeps the schedule's edits in this browser (localStorage overlay): move,
 *     edit, add, delete a session, rest days, schedule a suggested test; the
 *     calendar reads return the saved data with the overlay applied;
 *   - refuses every other write: 「唯讀示範：這個操作在示範版不能用」;
 *   - runs the clock from the export day (TRC_STATIC_CFG.snapshot, 12:00 UTC +
 *     the time since the page opened) so "today" matches the saved data;
 *   - rewrites the app's page links (/api/v1/overview/page ...) to the static files.
 *
 * The pure parts (keys, overlay) are a factory that node can require for tests
 * (backend/tests/test_static_demo_export.py).
 */
(function (root, factory) {
  const core = factory();
  if (typeof module === "object" && module.exports) module.exports = core;
  if (typeof window !== "undefined") core.install(window);
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const RO_MSG = "唯讀示範：這個操作在示範版不能用";
  const MISS_MSG = "示範版沒有這筆資料";
  const CALC_MSG = "示範版只預先算好預設輸入的結果；這組輸入要在完整版才能計算";
  const PAGE_MSG = "示範版沒有這個頁面";
  const OV_KEY = "trc.static.overlay.v1";
  const PAPI = "/api/v1/overview/plan";

  // ---------------------------------------------------------------- keys
  const MASK = (1n << 64n) - 1n, PRIME = 0x100000001b3n, OFFSET = 0xcbf29ce484222325n;
  function fnv64(s) {
    let h = OFFSET;
    for (const b of new TextEncoder().encode(s)) { h ^= BigInt(b); h = (h * PRIME) & MASK; }
    return h.toString(16).padStart(16, "0");
  }
  const dec = (s) => { try { return decodeURIComponent(s); } catch (_) { return s; } };
  // the GET key (export_static.data_key): decoded path + "?" + pairs sorted by name (stable)
  function dataKey(pathname, search) {
    const pairs = [];
    for (const [k, v] of new URLSearchParams(search || "")) pairs.push([k, v]);
    const idx = pairs.map((p, i) => [p, i]);
    idx.sort((a, b) => (a[0][0] < b[0][0] ? -1 : a[0][0] > b[0][0] ? 1 : a[1] - b[1]));
    const path = dec(pathname);
    return idx.length ? path + "?" + idx.map(([p]) => `${p[0]}=${p[1]}`).join("&") : path;
  }
  const dataFile = (pathname, search) => fnv64(dataKey(pathname, search)) + ".json";
  const postFile = (method, pathname, search, body) =>
    "p" + fnv64(`${method.toUpperCase()} ${dataKey(pathname, search)}\n${body || ""}`) + ".json";

  // ---------------------------------------------------------------- overlay (pure)
  // ov = { v: 1, snap, n, ses: { uid: { orig: session|null (added), cur: session|null (deleted) } },
  //        rest: { day: { moved: [{ uid, from }] } } }
  const emptyOverlay = (snap) => ({ v: 1, snap: snap || null, n: 0, ses: {}, rest: {} });
  const clone = (x) => (x == null ? x : JSON.parse(JSON.stringify(x)));
  const isoOf = (d) => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
  const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return isoOf(d); };
  const mondayOf = (iso) => addDays(iso, -((new Date(iso + "T00:00:00Z").getUTCDay() + 6) % 7));
  const contrib = (s) => (s && s.state !== "missed" ? { h: (+s.minutes || 0) / 60, t: +(s.tss_est ?? s.tss ?? 0) || 0 } : { h: 0, t: 0 });
  const EDIT_FIELDS = ["day", "kind", "title", "minutes", "target", "detail", "protocol", "terrain", "distance_km", "climb_m", "steps"];

  // the planned hours / TSS each week gains or loses from the overlay
  function weekDeltas(ov) {
    const d = {};
    const add = (s, sign) => {
      if (!s || !s.day) return;
      const w = mondayOf(s.day), c = contrib(s);
      const x = (d[w] ||= { h: 0, t: 0 });
      x.h += sign * c.h; x.t += sign * c.t;
    };
    for (const e of Object.values(ov.ses || {})) { add(e.orig, -1); add(e.cur, +1); }
    return d;
  }
  function overlaySessions(list, ov, start, end) {
    const out = (list || []).filter((s) => !(ov.ses || {})[s.uid]);
    for (const e of Object.values(ov.ses || {})) {
      const s = e.cur;
      if (s && (!start || s.day >= start) && (!end || s.day <= end)) out.push(clone(s));
    }
    out.sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
    return out;
  }
  function restBlackouts(ov, start, end) {
    return Object.keys(ov.rest || {}).filter((d) => (!start || d >= start) && (!end || d <= end)).sort()
      .map((d) => ({ start: d, end: d, label: "休息日", kind: "rest" }));
  }
  // GET /overview/plan/calendar?start&end with the overlay
  function applyCalendar(resp, ov, start, end) {
    if (!resp || !ov || (!Object.keys(ov.ses || {}).length && !Object.keys(ov.rest || {}).length)) return resp;
    const r = clone(resp);
    if (Array.isArray(r.sessions)) r.sessions = overlaySessions(r.sessions, ov, start, end);
    const rb = restBlackouts(ov);
    if (rb.length) {
      const have = new Set((r.blackouts || []).map((b) => `${b.start}|${b.end}|${b.kind || ""}`));
      r.blackouts = (r.blackouts || []).concat(rb.filter((b) => !have.has(`${b.start}|${b.end}|${b.kind}`)));
    }
    const dw = weekDeltas(ov);
    for (const w of r.week_rows || []) {
      const x = dw[w.start];
      if (!x) continue;
      if (w.planned_hours != null || x.h) w.planned_hours = Math.max(0, (+w.planned_hours || 0) + x.h);
      if (w.planned_tss != null || x.t) w.planned_tss = Math.max(0, (+w.planned_tss || 0) + x.t);
    }
    for (const w of r.weeks || []) {
      const x = dw[w.start];
      if (!x) continue;
      if (w.hours != null) w.hours = Math.max(0, w.hours + x.h);
      if (w.tss != null) w.tss = Math.max(0, w.tss + x.t);
    }
    return r;
  }
  // GET /overview/plan/sessions?start&end with the overlay
  function applySessionList(resp, ov, start, end) {
    if (!resp || !ov || !Object.keys(ov.ses || {}).length) return resp;
    const r = clone(resp);
    if (Array.isArray(r.sessions)) r.sessions = overlaySessions(r.sessions, ov, start, end);
    return r;
  }

  class OverlayError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }
  const editableErr = (s, today) => {
    if (!s) return new OverlayError(404, "找不到這堂課");
    if (s.state !== "active") return new OverlayError(400, "這堂課已經過了，不能修改");
    return null;
  };

  // the write ops: (ov, args, ctx) -> response body; ov is changed in place.
  // ctx = { today, find(uid) -> the session as shown now (base + overlay), suggestion(kind) }
  function opPatch(ov, uid, body, ctx) {
    body = body || {};
    if (body.variant_key) throw new OverlayError(403, RO_MSG);           // a library variant: built by the server
    const cur = (ov.ses[uid] ? ov.ses[uid].cur : null) || ctx.find(uid);
    const err = editableErr(cur, ctx.today);
    if (err) throw err;
    if (body.day && body.day < ctx.today) throw new OverlayError(400, "不能排到過去的日子");
    const orig = ov.ses[uid] ? ov.ses[uid].orig : clone(cur);
    const next = clone(cur);
    for (const k of EDIT_FIELDS) if (body[k] !== undefined) next[k] = body[k];
    if (body.steps === null) delete next.steps;
    if (body.tss != null && isFinite(+body.tss)) { next.tss = +body.tss; next.tss_est = +body.tss; }
    next.edited = true;
    if (next.coros && next.coros.status && next.coros.status !== "not_pushed") next.coros = { ...next.coros, status: "outdated" };
    ov.ses[uid] = { orig, cur: next };
    return clone(next);
  }
  function opDelete(ov, uid, ctx) {
    const cur = (ov.ses[uid] ? ov.ses[uid].cur : null) || ctx.find(uid);
    if (!cur) throw new OverlayError(404, "找不到這堂課");
    if (ov.ses[uid] && ov.ses[uid].orig == null) delete ov.ses[uid];   // one this browser added
    else ov.ses[uid] = { orig: ov.ses[uid] ? ov.ses[uid].orig : clone(cur), cur: null };
    return { ok: true, deleted: uid, coros: null };
  }
  function opAdd(ov, body, ctx) {
    body = body || {};
    if (body.variant_key) throw new OverlayError(403, RO_MSG);
    if (!body.day || body.day < ctx.today) throw new OverlayError(400, "不能排到過去的日子");
    if (!String(body.title || "").trim()) throw new OverlayError(400, "標題不能空白");
    ov.n = (ov.n || 0) + 1;
    const tss = body.tss != null && isFinite(+body.tss) ? +body.tss : null;
    const s = {
      uid: `local-${ov.n}`, day: body.day, kind: body.kind || "easy", title: String(body.title).trim(),
      minutes: +body.minutes || 0, target: body.target || "", detail: body.detail || "", tss, tss_est: tss,
      state: "active", origin: "custom", edited: true, provisional: false, coros: { status: "not_pushed" }, link_options: [],
    };
    for (const k of ["protocol", "terrain", "distance_km", "climb_m", "steps"]) if (body[k] !== undefined) s[k] = body[k];
    ov.ses[s.uid] = { orig: null, cur: s };
    return clone(s);
  }
  // 排入測試: POST /test-suggestions/schedule {kind, day} -> a test session from the saved suggestion
  function opScheduleTest(ov, body, ctx) {
    const sg = ctx.suggestion && ctx.suggestion(body && body.kind);
    if (!sg) throw new OverlayError(403, RO_MSG);
    return opAdd(ov, { day: body.day, kind: "test", title: sg.title, minutes: sg.minutes, target: sg.target || "",
      detail: sg.detail || "", tss: sg.tss, protocol: sg.protocol || sg.kind }, ctx);
  }
  // 休息日: the day's active sessions move to the nearest free day of the same week
  function opRestAdd(ov, day, ctx) {
    if (!day || day < ctx.today) throw new OverlayError(400, "過去的日子不能設成休息日");
    if ((ov.rest || {})[day]) throw new OverlayError(400, "這天已經是不排課日期或休息日");
    const week = ctx.weekSessions(day) || [];
    const mon = mondayOf(day);
    const busy = new Set(week.filter((s) => s.state === "active" && s.kind !== "strength").map((s) => s.day));
    const free = [];
    for (let i = 0; i < 7; i++) {
      const d = addDays(mon, i);
      if (d !== day && d >= ctx.today && !busy.has(d) && !(ov.rest || {})[d] && !(ctx.blocked && ctx.blocked(d))) free.push(d);
    }
    free.sort((a, b) => Math.abs(Date.parse(a) - Date.parse(day)) - Math.abs(Date.parse(b) - Date.parse(day)) || (a < b ? 1 : -1));
    const moved = [], changes = [];
    for (const s of week.filter((x) => x.day === day && x.state === "active")) {
      const to = s.kind === "strength" ? free[0] : free.shift();
      if (to) {
        opPatch(ov, s.uid, { day: to }, ctx);
        moved.push({ uid: s.uid, from: day, to });
        changes.push({ action: "move", uid: s.uid, title: s.title, from: day, to });
      } else {
        opDelete(ov, s.uid, ctx);
        moved.push({ uid: s.uid, from: day, to: null });
        changes.push({ action: "delete", uid: s.uid, title: s.title, from: day });
      }
    }
    (ov.rest ||= {})[day] = { moved };
    return { ok: true, blackouts: restBlackouts(ov), changes };
  }
  function opRestDel(ov, day) {
    const r = (ov.rest || {})[day];
    if (!r) throw new OverlayError(404, "這天不是休息日");
    const changes = [];
    for (const m of r.moved || []) {
      const e = ov.ses[m.uid];
      if (!e) continue;
      if (m.to == null && e.cur == null) { e.cur = clone(e.orig); }
      else if (e.cur && e.cur.day === m.to) e.cur.day = m.from;
      else continue;
      if (e.orig && e.cur && JSON.stringify({ ...e.cur, edited: e.orig.edited, coros: e.orig.coros }) === JSON.stringify(e.orig)) delete ov.ses[m.uid];
      changes.push({ action: "move", uid: m.uid, from: m.to, to: m.from });
    }
    delete ov.rest[day];
    return { ok: true, blackouts: restBlackouts(ov), changes };
  }

  // which writes the overlay answers: [method, regex, handler(ov, match, body, ctx)]
  const OVERLAY_RULES = [
    ["PATCH", /^\/api\/v1\/overview\/plan\/sessions\/([^/]+)$/, (ov, m, b, c) => opPatch(ov, dec(m[1]), b, c)],
    ["DELETE", /^\/api\/v1\/overview\/plan\/sessions\/([^/]+)$/, (ov, m, b, c) => opDelete(ov, dec(m[1]), c)],
    ["POST", /^\/api\/v1\/overview\/plan\/sessions$/, (ov, m, b, c) => opAdd(ov, b, c)],
    ["POST", /^\/api\/v1\/overview\/plan\/rest-days$/, (ov, m, b, c) => opRestAdd(ov, b && b.day, c)],
    ["DELETE", /^\/api\/v1\/overview\/plan\/rest-days\/([^/]+)$/, (ov, m) => opRestDel(ov, dec(m[1]))],
    ["POST", /^\/api\/v1\/overview\/plan\/test-suggestions\/schedule$/, (ov, m, b, c) => opScheduleTest(ov, b, c)],
  ];
  function overlayRule(method, path) {
    for (const [m, rx, fn] of OVERLAY_RULES) {
      if (m !== method) continue;
      const mm = path.match(rx);
      if (mm) return (ov, body, ctx) => fn(ov, mm, body, ctx);
    }
    return null;
  }

  // ---------------------------------------------------------------- the browser part
  function install(win) {
    const CFG = win.TRC_STATIC_CFG || {};
    const doc = win.document;
    const BASE = new URL(".", win.location.href);           // the pages are flat: their folder is the site root
    const DATA = new URL("data/", BASE).href;
    const STATIC = new URL("static/", BASE).href;
    const PAGES = CFG.pages || {};
    const COMPUTE = CFG.compute ? new RegExp(CFG.compute) : /^$/;
    const misses = [];

    // -- the clock: the export day at 12:00 UTC (the same date from UTC-11 to UTC+11) + time since load
    if (CFG.snapshot && !win.__trcClock) {
      const RealDate = win.Date;
      const [y, mo, d] = CFG.snapshot.split("-").map(Number);
      const off = RealDate.UTC(y, mo - 1, d, 12, 0, 0) - RealDate.now();
      const now = () => RealDate.now() + off;
      function FDate(...a) {
        if (!new.target) return new RealDate(now()).toString();
        return a.length ? new RealDate(...a) : new RealDate(now());
      }
      FDate.prototype = RealDate.prototype;
      FDate.now = now; FDate.parse = RealDate.parse; FDate.UTC = RealDate.UTC;
      Object.defineProperty(FDate, "name", { value: "Date" });
      win.Date = FDate;
      win.__trcClock = true;
    }
    if (CFG.clockOnly) return;            // the export's crawl: same clock, the real app behind it

    // -- storage (never throws: without storage the site is read-only)
    const store = {
      get() { try { const v = JSON.parse(win.localStorage.getItem(OV_KEY) || "null"); return v && v.v === 1 && v.snap === (CFG.snapshot || null) ? v : null; } catch (_) { return null; } },
      set(v) { try { win.localStorage.setItem(OV_KEY, JSON.stringify(v)); return true; } catch (_) { return false; } },
      clear() { try { win.localStorage.removeItem(OV_KEY); } catch (_) {} },
    };
    const overlay = () => store.get() || emptyOverlay(CFG.snapshot);
    const hasEdits = () => { const o = store.get(); return !!(o && (Object.keys(o.ses || {}).length || Object.keys(o.rest || {}).length)); };

    // -- responses
    const realFetch = win.fetch.bind(win);
    const json = (status, body) => new win.Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
    const errBody = (code, message) => ({ code, detail: { code, message, errors: [message] } });
    const calCache = new Map();         // calendar / sessions responses read so far (base, no overlay)
    let sugCache = null;

    async function loadData(name) {
      let r;
      try { r = await realFetch(DATA + name); } catch (_) { return null; }
      if (!r.ok) return null;
      try { return await r.json(); } catch (_) { return null; }
    }
    const unwrap = (j) => (j && typeof j === "object" && !Array.isArray(j) && "__trc_status" in j
      ? { status: j.__trc_status, body: j.body } : { status: 200, body: j });

    async function getApi(url) {
      const key = dataKey(url.pathname, url.search);
      const j = await loadData(fnv64(key) + ".json");
      if (j === null) { misses.push(key); return null; }
      return unwrap(j);
    }
    const findSession = (uid) => {
      const o = overlay();
      if (o.ses[uid]) return o.ses[uid].cur;
      for (const v of calCache.values()) for (const s of (v.sessions || [])) if (s.uid === uid) return s;
      return null;
    };
    const ctxFor = (o) => ({
      today: CFG.today || CFG.snapshot,
      find: (uid) => {
        if (o.ses[uid]) return o.ses[uid].cur;
        for (const v of calCache.values()) for (const s of (v.sessions || [])) if (s.uid === uid) return s;
        return null;
      },
      weekSessions: (day) => {
        const mon = mondayOf(day), sun = addDays(mon, 6), seen = new Map();
        for (const v of calCache.values()) for (const s of (v.sessions || [])) if (s.day >= mon && s.day <= sun) seen.set(s.uid, s);
        return overlaySessions([...seen.values()], o, mon, sun);
      },
      blocked: (d) => {
        for (const v of calCache.values()) for (const b of (v.blackouts || [])) if (b.start <= d && d <= (b.end || b.start)) return true;
        return false;
      },
      suggestion: (kind) => ((sugCache && (sugCache.suggestions || sugCache.tests || sugCache.items)) || []).find((x) => x.kind === kind) || null,
    });

    async function handle(method, url, body) {
      const path = url.pathname;
      if (method === "GET" || method === "HEAD") {
        if (path === "/api/v1/session") return json(200, { ...(win.TRC_SESSION || {}), csrf: "static" });
        if (path === "/api/v1/wko5/dataset/status") return json(200, { state: "ready", message: null });
        const got = await getApi(url);
        // plain text: the pages show `${status} ${body text}` -> 「404 示範版沒有這筆資料」
        if (!got) return new win.Response(MISS_MSG, { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
        if (got.status === 200 && (path === PAPI + "/calendar" || path === PAPI + "/sessions")) {
          calCache.set(dataKey(path, url.search), got.body);
          const p = url.searchParams, o = store.get();
          const b = !o ? got.body : path.endsWith("/calendar") ? applyCalendar(got.body, o, p.get("start"), p.get("end"))
            : applySessionList(got.body, o, p.get("start"), p.get("end"));
          return json(200, b);
        }
        if (got.status === 200 && path === PAPI + "/test-suggestions") sugCache = got.body;
        return json(got.status, got.body);
      }
      // writes
      const rule = overlayRule(method, path);
      if (rule) {
        let parsed = null;
        try { parsed = typeof body === "string" && body ? JSON.parse(body) : null; } catch (_) {}
        if (path === PAPI + "/test-suggestions/schedule" && !sugCache) {
          const g = await getApi(new URL(PAPI + "/test-suggestions", url));
          if (g && g.status === 200) sugCache = g.body;
        }
        const o = overlay();
        try {
          const out = rule(o, parsed, ctxFor(o));
          if (!store.set(o)) return json(403, errBody("STATIC_READONLY", RO_MSG + "（這個瀏覽器不能存資料）"));
          win.dispatchEvent(new win.CustomEvent("trc-static-overlay"));
          return json(200, out);
        } catch (e) {
          if (e instanceof OverlayError) return json(e.status, errBody(e.status === 403 ? "STATIC_READONLY" : "STATIC_INVALID", e.message));
          throw e;
        }
      }
      if (COMPUTE.test(path)) {
        if (typeof body === "string") {
          const j = await loadData(postFile(method, path, url.search, body));
          if (j !== null) { const u = unwrap(j); return json(u.status, u.body); }
        }
        return json(403, errBody("STATIC_PRECOMPUTED_ONLY", CALC_MSG));
      }
      // the race calculator saves its inputs on its own after each result: refused quietly (no toast)
      if (/^\/api\/v1\/racepower\/saved\//.test(path)) return json(403, errBody("STATIC_SILENT", "唯讀示範：輸入不會儲存"));
      return json(403, errBody("STATIC_READONLY", RO_MSG));
    }

    // -- page links: /api/v1/<page> -> the static file
    function mapHref(href) {
      if (href == null) return null;
      const s = String(href);
      if (!/^\/(api\/|demo\b|$|\?|#)/.test(s) && !s.startsWith(win.location.origin + "/api/")) return null;
      let u;
      try { u = new URL(s, win.location.origin); } catch (_) { return null; }
      if (u.origin !== win.location.origin) return null;
      const p = u.pathname.replace(/\/+$/, "") || "/";
      const f = PAGES[p];
      if (f === undefined) {
        if (p.startsWith("/api/v1/static/") && !p.endsWith(".html")) return STATIC + p.slice("/api/v1/static/".length) + u.search;
        return false;                      // an app page / download the static demo doesn't have
      }
      const [file, hash] = f.split("#");
      return new URL(file, BASE).href + u.search + (u.hash || (hash ? "#" + hash : ""));
    }
    const toast = (m) => {
      let el = doc.getElementById("an-toast");
      if (!el) {
        el = doc.createElement("div"); el.id = "an-toast"; el.className = "an-toast"; el.setAttribute("role", "status");
        el.style.cssText = "position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:80;max-width:min(92vw,460px);padding:9px 14px;border-radius:9px;background:#1f2937;color:#fff;font:13px/1.4 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25)";
        doc.body.appendChild(el);
      }
      el.textContent = m; el.hidden = false;
      clearTimeout(el._t); el._t = setTimeout(() => { el.hidden = true; }, 3500);
    };
    function fixLink(a) {
      const raw = a.getAttribute("href");
      if (!raw || a.dataset.trcHref === raw) return;
      const m = mapHref(raw);
      if (m === null) return;
      if (m === false) { a.dataset.trcNone = "1"; a.setAttribute("aria-disabled", "true"); a.dataset.trcHref = raw; return; }
      a.setAttribute("href", m);
      a.dataset.trcHref = m;
    }
    function fixLinks(rootEl) {
      if (!rootEl || !rootEl.querySelectorAll) return;
      if (rootEl.matches && rootEl.matches("a[href]")) fixLink(rootEl);
      rootEl.querySelectorAll("a[href]").forEach(fixLink);
    }

    // -- fetch
    win.fetch = async function (input, init) {
      const isReq = typeof win.Request !== "undefined" && input instanceof win.Request;
      const method = String((init && init.method) || (isReq ? input.method : "GET")).toUpperCase();
      let url;
      try { url = new URL(isReq ? input.url : String(input), win.location.href); } catch (_) { return realFetch(input, init); }
      if (url.origin !== win.location.origin) return realFetch(input, init);
      if (url.pathname.startsWith("/api/v1/static/")) return realFetch(STATIC + url.pathname.slice("/api/v1/static/".length) + url.search, init);
      if (!url.pathname.startsWith("/api/")) return realFetch(input, init);
      let body = init && init.body;
      if (body === undefined && isReq && method !== "GET" && method !== "HEAD") { try { body = await input.clone().text(); } catch (_) { body = null; } }
      return handle(method, url, body);
    };

    win.TRC_STATIC = {
      cfg: CFG, misses, mapHref, hasEdits, toast,
      href: (u) => { const m = mapHref(u); return m || (m === false ? "#" : u); },
      reset() { store.clear(); win.dispatchEvent(new win.CustomEvent("trc-static-overlay")); },
      messages: { readonly: RO_MSG, missing: MISS_MSG, calc: CALC_MSG, page: PAGE_MSG },
    };

    const start = () => {
      fixLinks(doc.body);
      new win.MutationObserver((ms) => {
        for (const m of ms) {
          if (m.type === "attributes") fixLink(m.target);
          else for (const n of m.addedNodes) if (n.nodeType === 1) fixLinks(n);
        }
      }).observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["href"] });
      doc.addEventListener("click", (e) => {
        const a = e.target.closest && e.target.closest("a[href]");
        if (!a) return;
        if (a.dataset.trcNone) { e.preventDefault(); e.stopImmediatePropagation(); toast(PAGE_MSG); return; }
        const m = mapHref(a.getAttribute("href"));
        if (m) { e.preventDefault(); win.location.href = m; }
        else if (m === false) { e.preventDefault(); e.stopImmediatePropagation(); toast(PAGE_MSG); }
      }, true);
    };
    if (doc.body) start(); else doc.addEventListener("DOMContentLoaded", start);
  }

  return {
    fnv64, dataKey, dataFile, postFile, emptyOverlay, applyCalendar, applySessionList, weekDeltas, mondayOf,
    opPatch, opDelete, opAdd, opRestAdd, opRestDel, opScheduleTest, overlayRule, OverlayError, install,
    RO_MSG, MISS_MSG, CALC_MSG,
  };
});
