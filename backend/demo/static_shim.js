/* TrailRunCoach static demo shim (backend/demo/export_static.py injects it first in every page).
 *
 * The static demo has no server. This file
 *   - maps the pages' /api/v1/... requests to the JSON files the export saved
 *     (data/<fnv64(key)>.json; key = decoded path + "?" + query sorted by name);
 *   - answers the view-only computations (race calculator ...) the export
 *     precomputed for the pages' default inputs (data/p<fnv64(method key body)>.json);
 *   - computes the schedule's structure editor itself (POST /steps/check, /steps/derive:
 *     `Steps`, a port of engine/workout_steps.py on data/steps_ctx.json);
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
  const ENGINE_FAIL_MSG = "示範版的計算引擎載入失敗（要連得到 cdn.jsdelivr.net）：請確認網路後重新整理";
  const ENGINE_ERR_MSG = "示範版算不出這組輸入，請換一組數字再試";
  // the race calculator's computations: the precomputed answer, else the engine in the browser
  // (static/trc_racepower_worker.js: engine/racepower/calc.py with Pyodide; backend/demo/static_racepower.py)
  const RACEPOWER_POSTS = /^\/api\/v1\/racepower\/(predict|plan|course\/event\/[^/]+|export\/csv)$/;
  const RACEPOWER_WORKER = "trc_racepower_worker.js";
  const RACEPOWER_SAVED = "racepower_saved.json";             // static_racepower.SAVED_FILE
  const OV_KEY = "trc.static.overlay.v1";
  const PAPI = "/api/v1/overview/plan";
  const STEPS_FILE = "steps_ctx.json";

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

  // ---------------------------------------------------------------- steps (pure)
  // The structure editor's POST /steps/check and /steps/derive (api/plan_sessions.py) for the
  // static site: a port of engine/workout_steps.py view() / derive() and
  // engine/target_policy.py, on data/steps_ctx.json (backend/demo/static_steps.py: the athlete
  // context, the engine's tables, derive() of the stored sessions / test templates).
  // backend/tests/test_static_steps.py compares it with the real endpoints.
  const Steps = (() => {
    class StepsError extends Error {
      constructor(errors) { super(errors.join("；")); this.errors = errors; }
    }
    // Python round(x, nd) (half to even on the exact binary value) and f"{x:.nf}"
    function pyRound(x, nd = 0) {
      if (x == null || !isFinite(x)) return x;
      const s = Math.abs(x).toFixed(nd + 30), [ip, fp = ""] = s.split(".");
      const rest = fp.slice(nd), d = rest.charCodeAt(0) - 48, tail = /[1-9]/.test(rest.slice(1));
      let n = BigInt(ip + fp.slice(0, nd));
      if (d > 5 || (d === 5 && (tail || n % 2n === 1n))) n += 1n;
      let t = n.toString().padStart(nd + 1, "0");
      if (nd) t = t.slice(0, -nd) + "." + t.slice(-nd);
      const v = Number(t);
      return x < 0 ? -v : v;
    }
    const fx = (x, nd = 0) => pyRound(x, nd).toFixed(nd);
    const fmtG = (x) => (x === 0 ? "0" : String(Number(x.toPrecision(6))));
    const mmss = (sec) => { const s = pyRound(sec); return `${Math.floor(s / 60)}:${String(((s % 60) + 60) % 60).padStart(2, "0")}`; };
    const fmtS = (sec) => { const s = pyRound(sec); if (s < 60) return `${s} 秒`; const m = Math.floor(s / 60), r = s % 60; return r ? `${m}:${String(r).padStart(2, "0")}` : `${m} 分`; };
    const isDict = (x) => x != null && typeof x === "object" && !Array.isArray(x);
    const repr = (v) => (v == null ? "None" : typeof v === "string" ? `'${v}'` : v === true ? "True" : v === false ? "False" : String(v));
    const cut = (s, n) => Array.from(s).slice(0, n).join("");
    function toFloat(x) {                         // Python float(); undefined = TypeError / ValueError
      if (typeof x === "number") return x;
      if (typeof x === "boolean") return +x;
      if (typeof x === "string") {
        const t = x.trim();
        if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(t)) return Number(t);
        if (/^[+-]?(inf|infinity)$/i.test(t)) return t[0] === "-" ? -Infinity : Infinity;
        if (/^[+-]?nan$/i.test(t)) return NaN;
      }
      return undefined;
    }
    function toInt(x) {                           // Python int(); undefined = error
      if (typeof x === "number") return isFinite(x) ? Math.trunc(x) : undefined;
      if (typeof x === "boolean") return +x;
      if (typeof x === "string" && /^\s*[+-]?\d+\s*$/.test(x)) return parseInt(x, 10);
      return undefined;
    }

    // ---- normalize (the stored shape)
    function normalize(d, K) {
      const W = K.ws;
      if (typeof d === "string") { try { d = JSON.parse(d); } catch (_) { throw new StepsError(["結構不是 JSON"]); } }
      if (!isDict(d) || !Array.isArray(d.items)) throw new StepsError(["結構要有 items"]);
      const errs = [], seen = new Set();
      let n = 0, cnt = 0;
      const nextId = () => `n${++n}`;
      const f = (x, name, lo, hi) => {
        const v = toFloat(x);
        if (v === undefined) { errs.push(`${name} 要是數字`); return null; }
        if (v !== v || (lo != null && v < lo) || (hi != null && v > hi)) { errs.push(`${name} 超出範圍`); return null; }
        return v;
      };
      const OPEN = () => ({ type: "auto", intent: "open" });
      const zoneIds = (ty) => new Set(W.zones[ty].map((r) => r[0]));
      const target = (t) => {
        if (!isDict(t)) return OPEN();
        const ty = t.type === undefined ? "auto" : t.type;
        if (!["auto", "power", "hr", "pace", "rpe", "none"].includes(ty)) { errs.push(`目標類型不對：${repr(ty)}`); return OPEN(); }
        if (ty === "none") return { type: "none" };
        if (ty === "rpe") {                         // 技術地形／下坡 (SP-62)
          const P = W.rpe, lo = f(t.lo, "RPE 下限", P.min, P.max), hi = f(t.hi === undefined ? t.lo : t.hi, "RPE 上限", P.min, P.max);
          const out = { type: "rpe", lo: pyRound(lo || P.min), hi: pyRound(hi || lo || P.min) };
          if (out.lo > out.hi) errs.push("RPE 下限比上限高");
          for (const [k, name] of [["up", "爬升"], ["down", "下降"]]) {
            if (![undefined, null, "", 0].includes(t[k])) { const v = f(t[k], name, 0, P.max_climb); if (v) out[k] = pyRound(v); }
          }
          return out;
        }
        if (ty === "auto") {
          const it = t.intent === undefined ? "open" : t.intent;
          if (!["easy", "band", "open"].includes(it)) { errs.push(`自動目標的類型不對：${repr(it)}`); return OPEN(); }
          const out = { type: "auto", intent: it };
          if (it === "easy" && t.plo != null) { out.plo = f(t.plo, "功率下限", 0.3, 2.5); out.phi = f(t.phi, "功率上限", 0.3, 2.5); }
          if (it === "band") {
            out.lo = f(t.lo, "強度下限", 0.3, 2.5); out.hi = f(t.hi, "強度上限", 0.3, 2.5);
            out.cls = String(t.cls || "");
            if (Array.isArray(t.hr) && t.hr.length === 2) out.hr = [Math.trunc(f(t.hr[0], "心率", 40, 230) || 0), Math.trunc(f(t.hr[1], "心率", 40, 230) || 0)];
            if (Array.isArray(t.hrp) && t.hrp.length === 2) out.hrp = [f(t.hrp[0], "心率 %", 0.5, 1.2), f(t.hrp[1], "心率 %", 0.5, 1.2)];
          }
          return out;
        }
        const mode = t.mode === undefined ? "pct" : t.mode;
        if (!["pct", "zone", "abs"].includes(mode)) { errs.push(`目標填法不對：${repr(mode)}`); return OPEN(); }
        const out = { type: ty, mode };
        if (mode === "zone") {
          const z = String(t.zone || "");
          if (!zoneIds(ty).has(z)) errs.push(`沒有這個區間：${repr(z)}`);
          out.zone = z;
          return out;
        }
        const rng = { "power|pct": [0.2, 3.0], "hr|pct": [0.3, 1.3], "pace|pct": [0.5, 2.5], "power|abs": [20, 1500], "hr|abs": [40, 230], "pace|abs": [120, 1200] }[`${ty}|${mode}`];
        out.lo = f(t.lo, "目標下限", ...rng); out.hi = f(t.hi, "目標上限", ...rng);
        if (ty === "pace" && mode === "pct" && Array.isArray(t.hrp) && t.hrp.length === 2) out.hrp = [f(t.hrp[0], "心率 %", 0.5, 1.2), f(t.hrp[1], "心率 %", 0.5, 1.2)];
        return out;
      };
      const item = (x, depth) => {
        if (!isDict(x)) { errs.push("步驟格式不對"); return null; }
        cnt++;
        let iid = cut(String(x.id || ""), 16);
        if (!iid || seen.has(iid)) { iid = nextId(); while (seen.has(iid)) iid = nextId(); }
        seen.add(iid);
        const note = cut(String(x.note || "").trim(), W.max_note);
        const k = x.kind;
        if (k === "repeat") {
          if (depth >= W.max_depth) { errs.push("重複最多兩層"); return null; }
          let times = toInt(x.times);
          if (times === undefined) times = 0;
          if (!(times >= 1 && times <= W.max_times)) { errs.push(`重複次數要在 1–${W.max_times}`); times = Math.max(1, Math.min(W.max_times, times || 1)); }
          const kids = (Array.isArray(x.items) ? x.items : []).map((c) => item(c, depth + 1)).filter(Boolean);
          if (!kids.length) errs.push("重複區塊裡沒有步驟");
          return { id: iid, kind: "repeat", times, last_rest: x.last_rest !== false, note, items: kids };
        }
        if (!["warm", "work", "rest", "cool", "other"].includes(k)) { errs.push(`步驟類型不對：${repr(k)}`); return null; }
        let dur = x.dur || {};
        const dt = isDict(dur) ? dur.type : undefined;
        if (!["time", "distance", "open"].includes(dt)) { errs.push("時長類型要是 時間／距離／直到按下計圈"); dur = { type: "open" }; }
        else if (dt === "time") { const v = f(dur.value, "時間", 5, 6 * 3600); dur = v ? { type: "time", value: pyRound(v) } : { type: "open" }; }
        else if (dt === "distance") { const v = f(dur.value, "距離", 50, 100000); dur = v ? { type: "distance", value: pyRound(v) } : { type: "open" }; }
        else { const v = dur.est ? f(dur.est, "直到按下計圈的預估時間", 5, 6 * 3600) : null; dur = v ? { type: "open", est: pyRound(v) } : { type: "open" }; }
        return { id: iid, kind: k, dur, target: target(x.target), note };
      };
      const items = d.items.map((x) => item(x, 0)).filter(Boolean);
      if (!items.length) errs.push("至少要有一個步驟");
      if (cnt > W.max_items) errs.push(`步驟太多（> ${W.max_items}）`);
      if (errs.length) throw new StepsError([...new Set(errs)]);
      let origin = String(d.origin || "user");
      if (!(origin === "derived" || origin === "user" || origin.startsWith("template:"))) origin = "user";
      return { v: 1, origin: cut(origin, 40), items };
    }

    // ---- the context (Ctx.of, target_policy, day_cap)
    function ctxOf(th, basis, hrCap, sp) {
      th = th || {}; sp = sp || {};
      const f = (d, k) => { const v = d[k]; if (!v) return null; const n = toFloat(v); return n === undefined ? null : n; };
      const ter = ["trail", "hike"].includes(sp.terrain) ? "trail" : "road";
      return { cp: f(th, "cp"), lthr: f(th, "lthr"), aet: f(th, "aet"), tpace: f(th, "tpace"),
        basis: ["hr", "power", "none"].includes(basis) ? basis : "hr", hr_cap: !!hrCap,
        v_easy: f(sp, "v_easy"), v_easy_src: String(sp.v_easy_src || ""), ep_kmh: f(sp, "ep_kmh"),
        terrain: ter, climb_per_km: ter === "trail" ? Math.max(0, f(sp, "climb_per_km") || 0) : 0 };
    }
    function aetProtocol(title, K) {
      const m = String(title || "").match(new RegExp(K.aet.title_re));
      if (!m) return null;
      return { "徐國峰": "xu90", Evoke: "evoke60", Friel: "friel" }[m[1]] || (+m[2] >= 60 ? "ua60" : "ua40");
    }
    const isAet = (s, K) => s.protocol === K.aet.protocol || s.kind === K.aet.protocol || s.id === "test_aet" ||
      s.gen_key === "test_aet" || String(s.title || "").includes("AeT");
    function sessionType(s, K) {
      const kind = s.kind, title = String(s.title || "");
      if (kind === "test") return isAet(s, K) ? "aet_test" : "cp_test";
      if (title.includes("下坡") && ["easy", "long", "quality"].includes(kind)) return "downhill";
      if (title.includes("長爬坡")) return "climb";
      if (kind === "mountain") return "trail_long";
      if (kind === "quality") return title.includes("爬坡") || title.includes("上坡") || s.terrain === "trail" ? "hill" : "interval";
      if (kind === "hike") return "hike";
      if (kind === "long") return ["trail", "hike"].includes(s.terrain) || title.includes("山路") ? "trail_long" : "long";
      if (kind === "easy" || kind === "heat_passive") return ["trail", "hike"].includes(s.terrain) || title.includes("山路") || title.includes("越野") ? "trail_easy" : "easy";
      return "other";
    }
    const TYPE_NAME = { easy: "輕鬆跑", trail_easy: "越野輕鬆跑", long: "長跑", trail_long: "山路長天", hike: "越野跑", interval: "間歇", hill: "爬坡重複",
      climb: "長爬坡", downhill: "下坡練習", cp_test: "CP 測試", aet_test: "AeT 測試", other: "其他" };
    function targetPolicy(s, D, th) {
      const K = D, L = K.tp.label;
      const t = sessionType(s, K), own = s.target_basis;
      const chosen = ["hr", "power"].includes(own) ? own : D.prefs.basis;
      let [base, key] = K.tp.auto[t];
      if (base == null) { const p = aetProtocol(s.title, K); base = ["xu90", "friel"].includes(p) ? "hr" : "power"; }
      let basis = base, why = `自動：${TYPE_NAME[t]}看${L[base]}`;
      if (["hr", "power"].includes(chosen) && !["cp_test", "aet_test", "downhill", "climb"].includes(t)) {
        basis = chosen;
        why = (["hr", "power"].includes(own) ? "這次課表你選了" : "課表偏好：") + L[chosen];
      }
      let fb = "";
      th = th || {};
      const thOn = Object.keys(th).length > 0;
      if (basis === "power" && !["hr", "power"].includes(chosen) && !D.auto_power_ok) { basis = "hr"; fb = "功率來源不是 Stryd：用心率"; }
      if (basis === "power" && thOn && !th.cp) { basis = "hr"; fb = "沒有 CP：改用心率"; }
      if (basis === "hr" && thOn && !(th.aet || th.lthr)) { basis = "none"; fb = "沒有 AeT／LTHR：不設目標"; }
      return { basis, chosen: ["hr", "power"].includes(own) ? own : chosen, type: t, why: why + (fb ? `（${fb}）` : ""),
        source: K.tp.src[key], hr_cap: basis === "power" && ["interval", "hill", "easy", "long"].includes(t), fallback: fb };
    }
    function dayCap(prefs, day) {
      if (!prefs || !prefs.active || !day) return null;
      const wd = (new Date(String(day).slice(0, 10) + "T00:00:00Z").getUTCDay() + 6) % 7;
      const c = wd >= 5 ? prefs.long_cap : prefs.cap_weekday;
      return c != null ? +c : null;
    }
    const STEP_FIELDS = ["kind", "title", "minutes", "target", "detail", "source", "terrain", "protocol", "day", "variant_key", "variant_reps",
      "variant_blocks", "variant_adj", "rung_key", "heat", "target_basis", "climb_per_km", "distance_km", "climb_m"];
    function sessionOf(body, stored) {
      const s = { ...(stored || {}) };
      for (const k of STEP_FIELDS) if (body[k] !== undefined && body[k] !== null) s[k] = body[k];
      if ("target_basis" in body && [undefined, null, "", "auto"].includes(body.target_basis)) s.target_basis = null;
      s.minutes = toInt(s.minutes || 0) ?? Math.trunc(+s.minutes || 0);
      return s;
    }
    function climbPerKm(s) {
      if (s.climb_per_km != null) { const v = toFloat(s.climb_per_km); if (v !== undefined) return v; }
      if (s.distance_km && s.climb_m != null) { const a = toFloat(s.climb_m), b = toFloat(s.distance_km); if (a !== undefined && b) return a / b; }
      return null;
    }
    function env(s, D) {
      const th = { ...D.th };
      const pol = targetPolicy(s, D, th);
      const sp = { ...D.speeds, terrain: s.kind === "hike" || ["trail", "hike"].includes(s.terrain) ? "trail" : "road", climb_per_km: climbPerKm(s) };
      const c = ctxOf(th, pol.basis, !!pol.hr_cap, sp);
      let rung = null;
      if (s.kind === "quality") rung = s.rung_key || D.il.variant_rung[s.variant_key] || null;
      const cap = s.kind !== "test" ? dayCap(D.prefs, s.day) : null;
      return { ctx: c, th, policy: pol, cap, cap_mode: D.prefs.cap_mode || "soft", rung: rung && D.il.canonical[rung] ? rung : null };
    }
    function context(e, D) {
      const th = e.th, pol = e.policy, W = D.ws;
      const thresholds = {};
      for (const k of ["cp", "lthr", "aet", "tpace", "cp_source", "lthr_source", "aet_source"]) thresholds[k] = th[k] ?? null;
      return { thresholds, tpace_link: D.tpace_link ?? null, zones: zonesTable(e.ctx, D), policy: pol,
        basis_label: `目標用：${D.tp.label[pol.basis]}（${pol.why}）`, cap: e.cap, cap_mode: e.cap_mode, rung: e.rung,
        kinds: W.kind_label, types: W.type_label, rules: W.rules };
    }

    // ---- resolving one step's target
    const OPENT = { type: "auto", intent: "open" };
    function easyHr(c) {
      const hi = c.aet || (c.lthr ? 0.89 * c.lthr : null);
      if (!hi) return null;
      let lo = c.lthr ? 0.75 * c.lthr : hi - 25;
      lo = Math.min(lo, hi - 10);
      return ["hr", pyRound(lo), pyRound(hi)];
    }
    const power = (c, lo, hi) => (c.cp ? ["power", pyRound(lo * c.cp), pyRound(hi * c.cp)] : null);
    function workHr(c, tg, D) {
      if (tg.hr && tg.hr.length) return ["hr", Math.trunc(tg.hr[0]), Math.trunc(tg.hr[1])];
      if (tg.hrp && tg.hrp.length && c.lthr) return ["hr", pyRound(tg.hrp[0] * c.lthr), pyRound(tg.hrp[1] * c.lthr)];
      const [a, b] = D.ws.hr_work[tg.cls || ""] || [0.95, 1.0];
      if (!c.lthr) return null;
      const lo = a === "aet" ? c.aet : a * c.lthr;
      return ["hr", pyRound(lo || 0.89 * c.lthr), pyRound(b * c.lthr)];
    }
    function pzone(f, D) {
      for (const [z, lo, hi] of D.ws.zones.power) if (lo <= f && f < hi) return `Z${z}`;
      return f >= 1.5 ? "Z7" : "Z1A";
    }
    const level = (f) => (f == null ? 0 : f < 0.75 ? 1 : f < 0.88 ? 2 : f < 1.01 ? 3 : f < 1.06 ? 4 : 5);
    function hrToP(f, D) {
      const P = D.ws.hr_p;
      if (f <= P[0][0]) return P[0][1];
      for (let i = 0; i + 1 < P.length; i++) {
        const [a0, b0] = P[i], [a1, b1] = P[i + 1];
        if (f <= a1) return b0 + (f - a0) / (a1 - a0) * (b1 - b0);
      }
      return P[P.length - 1][1];
    }
    const zoneOf = (ty, zid, D) => { const r = D.ws.zones[ty].find((x) => x[0] === zid); return r ? [r[1], r[2]] : [null, null]; };
    const R = (type, o = {}) => ({ type, lo: null, hi: null, frac: null, text: "", sub: "", auto: true, warn: "", err: "", intensity: null, need: "", ...o });
    function fromInt(it, c, D, auto = true, warn = "") {
      if (!it) return R("none", { text: "不設目標", auto, warn });
      const [typ, lo, hi] = it;
      if (typ === "power") {
        const f = c.cp ? (lo + hi) / 2 / c.cp : null;
        const sub = c.cp ? `${fx(lo / c.cp * 100)}–${fx(hi / c.cp * 100)}% CP · ${pzone(f, D)}` : "";
        return R("power", { lo, hi, frac: f, text: `${fx(lo)}–${fx(hi)} W`, sub, auto, warn, intensity: it });
      }
      const f = c.lthr ? hrToP((lo + hi) / 2 / c.lthr, D) : 0.7;
      const sub = c.aet && Math.abs(hi - c.aet) < 1 ? "≤ 輕鬆跑上限" : c.lthr ? `${fx(lo / c.lthr * 100)}–${fx(hi / c.lthr * 100)}% LTHR` : "";
      return R("hr", { lo, hi, frac: f, text: `${fx(lo)}–${fx(hi)} bpm`, sub, auto, warn, intensity: it });
    }
    // RPE targets (workout_steps rpe_frac / rpe_text / climb_text / rpe_hint)
    const rpeFrac = (lo, hi, D) => { const P = D.ws.rpe, k = (x) => Math.max(P.min, Math.min(P.max, pyRound(x))); return (P.frac[k(lo)] + P.frac[k(hi)]) / 2; };
    const rpeText = (lo, hi) => (lo === hi ? fmtG(lo) : `${fmtG(lo)}–${fmtG(hi)}`);
    const climbText = (tg) => [["up", "爬升"], ["down", "下降"]].filter(([k]) => tg[k]).map(([k, n]) => `${n} ${tg[k]} m`).join(" · ");
    function rpeHint(lo, hi, c, D) {
      const P = D.ws.rpe, m = (lo + hi) / 2;
      let txt = "";
      if (m <= P.easy_max) { const cap = c.aet || (c.lthr ? 0.88 * c.lthr : null); txt = cap ? `≤ ${fx(cap)} bpm` : ""; }
      else if (m < P.hard_min - 0.5) { const a = c.aet || (c.lthr ? 0.88 * c.lthr : null), b = c.lthr ? 0.95 * c.lthr : null; txt = a && b ? `${fx(a)}–${fx(b)} bpm` : ""; }
      else txt = c.lthr ? `≥ ${fx(0.95 * c.lthr)} bpm` : "";
      return txt ? `參考心率 ${txt}（不當目標）` : "";
    }
    function resolveRpe(tg, c, D) {
      const P = D.ws.rpe, lo = tg.lo || P.min, hi = tg.hi || tg.lo || P.min;
      const a = P.word[pyRound(lo)] || "", b = P.word[pyRound(hi)] || "";
      const sub = [climbText(tg), a === b ? a : `${a}～${b}`, rpeHint(lo, hi, c, D)].filter(Boolean).join(" · ");
      return R("rpe", { lo, hi, frac: rpeFrac(lo, hi, D), text: rpeText(lo, hi), sub, auto: false, err: lo > hi ? "RPE 下限比上限高" : "" });
    }
    function rpeRole(items, D) {
      const his = flat(items || []).map((r) => r.st).filter((st) => ["work", "other"].includes(st.kind) && (st.target || {}).type === "rpe")
        .map((st) => +(st.target.hi || 0));
      return his.length ? (Math.max(...his) >= D.ws.rpe.hard_min ? "quality" : "easy") : null;
    }
    function resolve(st, c, D) {
      const tg = st.target || OPENT, ty = tg.type || "auto";
      if (ty === "none") return R("none", { text: "不設目標", auto: false });
      if (ty === "rpe") return resolveRpe(tg, c, D);
      if (ty === "auto") {
        const it = tg.intent || "open";
        if (it === "open") return R("none", { text: "不設目標" });
        if (it === "easy") {
          if (tg.plo != null && c.basis === "power") {
            const p = power(c, tg.plo, tg.phi);
            if (p) return fromInt(p, c, D);
            return fromInt(easyHr(c), c, D, true, "沒有 CP：改用心率");
          }
          if (tg.plo != null && c.basis === "none") return R("none", { text: "不設目標" });
          const e = easyHr(c);
          return fromInt(e, c, D, true, e ? "" : "沒有 AeT／LTHR：不設目標");
        }
        if (c.basis === "none") return R("none", { text: "不設目標" });
        if (c.basis === "hr") {
          const h = workHr(c, tg, D);
          if (h) return fromInt(h, c, D);
          const p = power(c, tg.lo, tg.hi);
          return fromInt(p, c, D, true, p ? "沒有 LTHR：改用功率" : "沒有 LTHR／CP：不設目標");
        }
        const p = power(c, tg.lo, tg.hi);
        if (p) return fromInt(p, c, D);
        const h = workHr(c, tg, D);
        return fromInt(h, c, D, true, h ? "沒有 CP：改用心率" : "沒有 CP／LTHR：不設目標");
      }
      const mode = tg.mode || "pct";
      let lo = tg.lo, hi = tg.hi, r;
      if (ty === "power") {
        if (mode === "zone") [lo, hi] = zoneOf("power", tg.zone, D);
        if (mode !== "abs") {
          if (!c.cp) return R("none", { text: "不設目標", auto: false, err: "選了功率卻沒有 CP" });
          lo *= c.cp; hi *= c.cp;
        }
        r = fromInt(["power", pyRound(lo), pyRound(hi)], c, D, false);
      } else if (ty === "hr") {
        if (mode === "zone" && tg.zone === "aet") {
          const e = easyHr(c);
          if (!e) return R("none", { text: "不設目標", auto: false, err: "選了心率卻沒有 AeT／LTHR" });
          r = fromInt(e, c, D, false);
        } else {
          if (mode === "zone") [lo, hi] = zoneOf("hr", tg.zone, D);
          if (mode !== "abs") {
            if (!c.lthr) return R("none", { text: "不設目標", auto: false, err: "選了心率卻沒有 LTHR" });
            lo *= c.lthr; hi *= c.lthr;
          }
          r = fromInt(["hr", pyRound(lo), pyRound(hi)], c, D, false);
        }
      } else {
        if (mode === "zone") [lo, hi] = zoneOf("pace", tg.zone, D);
        if (mode !== "abs") {
          if (!c.tpace) {
            if (tg.hrp && tg.hrp.length && c.lthr) {
              const x = fromInt(["hr", pyRound(tg.hrp[0] * c.lthr), pyRound(tg.hrp[1] * c.lthr)], c, D, false, D.ws.no_tpace);
              x.need = "tpace";
              return x;
            }
            return R("none", { text: "不設目標", auto: false, warn: D.ws.no_tpace, need: "tpace" });
          }
          lo *= c.tpace; hi *= c.tpace;
        }
        const a = Math.min(lo, hi), b = Math.max(lo, hi);
        const f = c.tpace ? 1 / ((a + b) / 2 / c.tpace) : 0.8;
        const sub = c.tpace ? `${fx(a / c.tpace * 100)}–${fx(b / c.tpace * 100)}% 閾值配速（推估）` : "";
        r = R("pace", { lo: a, hi: b, frac: f, text: `${mmss(a)}–${mmss(b)} /km`, sub, auto: false, intensity: ["pace", pyRound(a), pyRound(b)] });
      }
      if (r.lo != null && r.hi != null && r.lo > r.hi && r.type !== "pace") r.err = "下限比上限高";
      return r;
    }
    const asDict = (r, D) => ({ type: r.type, lo: r.lo, hi: r.hi, frac: r.frac, text: r.text, sub: r.sub, auto: r.auto, warn: r.warn,
      err: r.err, need: r.need, level: level(r.frac), label: D.ws.type_label[r.type] ?? r.type });

    // ---- flatten, totals
    function iterRep(it) {
      const passes = [];
      for (let i = 0; i < it.times; i++) {
        const kids = it.items.slice();
        if (i === it.times - 1 && it.last_rest === false) while (kids.length && kids[kids.length - 1].kind === "rest") kids.pop();
        passes.push(kids);
      }
      return passes;
    }
    function flat(items, ctx) {
      let out = [];
      for (const it of items) {
        if (it.kind === "repeat") iterRep(it).forEach((kids, i) => { out = out.concat(flat(kids, (ctx || []).concat([[it.id, i, it.times]]))); });
        else out.push({ st: it, rep: ctx || [] });
      }
      return out;
    }
    function speedKmh(f, c, D) {
      const W = D.ws, vE = c.v_easy, vT = c.tpace ? 3600 / c.tpace : null;
      if (f == null) f = W.easy_f;
      f = Math.max(0.45, Math.min(1.4, f));
      let v, how;
      if (vE && vT && vT > vE) { v = vE + (f - W.easy_f) / (1 - W.easy_f) * (vT - vE); how = "easy+tpace"; }
      else if (vE) { v = vE * f / W.easy_f; how = "easy"; }
      else if (vT) { v = vT * f; how = "tpace"; }
      else return [3600 / W.dist_pace_default, "default"];
      return [Math.max(v, 0.55 * (vE || vT)), how];
    }
    function secs(st, r, c, D) {
      const d = st.dur, W = D.ws;
      if (d.type === "time") return [d.value, false];
      if (d.type === "distance") {
        const km = d.value / 1000;
        if (r.type === "pace" && r.lo) return [km * (r.lo + r.hi) / 2, true];
        if (r.frac == null && st.kind === "rest") return [km / W.walk_kmh * 3600, true];
        const f = r.frac != null ? r.frac : (W.none_if[st.kind] ?? W.easy_f);
        const [v] = speedKmh(f, c, D);
        if (c.terrain === "trail") {
          const ep = km * (1 + c.climb_per_km / 100);
          if (c.ep_kmh && c.v_easy) return [ep / (c.ep_kmh * v / c.v_easy) * 3600, true];
          return [ep / v * 3600, true];
        }
        return [km / v * 3600, true];
      }
      if (d.est) return [d.est, true];
      return [0, false];
    }
    function estimateNote(steps, c, D) {
      const rows = flat(steps.items).map((x) => x.st);
      const dist = rows.some((s) => s.dur.type === "distance"), lap = rows.some((s) => s.dur.type === "open" && s.dur.est);
      const parts = [];
      if (dist) {
        const [, how] = speedKmh(D.ws.easy_f, c, D);
        const src = how === "easy+tpace" ? (c.v_easy && c.tpace ? `你的輕鬆路跑速度 ${fx(c.v_easy, 1)} km/h（${c.v_easy_src || "近期紀錄"}）和閾值配速 ${mmss(c.tpace || 0)}/km 之間，依每段的目標強度內插` : "")
          : how === "easy" ? `你的輕鬆路跑速度 ${fx(c.v_easy || 0, 1)} km/h 依目標強度等比例放大`
          : how === "tpace" ? `你的閾值配速 ${mmss(c.tpace || 0)}/km 依目標強度換算` : "沒有你的速度資料，先用 6:00/km";
        parts.push("距離段：" + src);
        if (c.terrain === "trail") parts.push(`越野：努力距離 EP = km × (1 + 爬升 ${fx(c.climb_per_km)} m/km ÷ 100)` +
          (c.ep_kmh ? `，用你的越野 EP 速度 ${fx(c.ep_kmh, 1)} km/h` : "，你的越野紀錄不夠，先用路跑速度"));
      }
      if (lap) parts.push("「直到按下計圈」段：用課表原本寫的最短時間");
      return parts.length ? parts.join("；") + "（推估）" : "";
    }
    function totals(steps, c, D) {
      let sec = 0, tss = 0, hard = 0, z5 = 0, nOpen = 0, est = false;
      for (const row of flat(steps.items)) {
        const st = row.st, r = resolve(st, c, D), [s, e] = secs(st, r, c, D);
        est = est || e;
        if (st.dur.type === "open" && !s) { nOpen++; continue; }
        sec += s;
        const f = r.frac != null ? r.frac : (D.ws.none_if[st.kind] ?? 0.7);
        tss += s * f * f * 100 / 3600;
        if (f >= 0.88) hard += s;
        if (r.frac != null && r.frac >= D.ws.z5_frac && st.kind === "work") z5 += s;
      }
      return { sec: pyRound(sec), open: nOpen, est, tss: pyRound(tss, 1), hard_s: pyRound(hard), z5_s: pyRound(z5),
        est_note: est ? estimateNote(steps, c, D) : "" };
    }

    // ---- issues
    const bandMid = (tg) => (tg.lo + tg.hi) / 2;
    function isZ5(st, r, D) {
      const tg = st.target || {};
      if (tg.type === "auto" && tg.intent === "band") return bandMid(tg) >= D.ws.z5_frac;
      return r.type === "power" && r.frac != null && r.frac >= D.ws.z5_frac;
    }
    function isZ3(st, r, D) {
      const tg = st.target || {}, CR = D.il.class_range;
      let m;
      if (tg.type === "auto" && tg.intent === "band") m = bandMid(tg);
      else if (r.type === "power" && r.frac != null) m = r.frac;
      else return false;
      return CR.Z3sub[0] <= m && m < CR.Z3near[1];
    }
    function hasRestAfter(rows, st) {
      const i = rows.findIndex((x) => x.st === st);
      return i >= 0 && i + 1 < rows.length && rows[i + 1].st.kind === "rest";
    }
    function issues(steps, c, D, cap, capMode, rung) {
      const out = [], seen = new Set(), W = D.ws;
      const add = (lv, text, id = null) => { const k = JSON.stringify([lv, text, id]); if (!seen.has(k)) { seen.add(k); out.push({ level: lv, text, id }); } };
      const rows = flat(steps.items);
      for (const row of rows) {
        const st = row.st, r = resolve(st, c, D);
        if (r.err) add("err", r.err, st.id);
        if (r.warn) add("warn", r.warn, st.id);
        if (st.kind === "work" && st.dur.type === "time" && isZ5(st, r, D) && st.dur.value < W.rules.z5_min_rep_s)
          add("err", `5 區每趟至少 2 分鐘（台灣教練）：這段只有 ${mmss(st.dur.value)}`, st.id);
        if (st.kind === "work" && st.dur.type === "time" && isZ3(st, r, D) && st.dur.value < W.rules.z3_min_rep_s && hasRestAfter(rows, st))
          add("warn", `3 區每趟至少 3 分鐘（Haugen 2022 的下緣）：這段只有 ${mmss(st.dur.value)}`, st.id);
      }
      const z5w = rows.map((x) => x.st).filter((st) => st.kind === "work" && st.dur.type === "time" && isZ5(st, resolve(st, c, D), D));
      if (z5w.length) {
        const short = Math.min(...z5w.map((x) => x.dur.value));
        rows.forEach((row, i) => {
          const st = row.st;
          if (st.kind !== "rest" || st.dur.type !== "time" || i === 0) return;
          const prev = rows[i - 1].st;
          if (z5w.includes(prev) && st.dur.value > Math.min(short, W.rules.z5_max_rest_s) && !(i + 1 < rows.length && rows[i + 1].st.kind === "rest"))
            if (i + 1 < rows.length && z5w.includes(rows[i + 1].st))
              add("warn", `5 區休息 ${mmss(st.dur.value)} 比一趟長或超過 3 分鐘（Buchheit 工休比）`, st.id);
        });
      }
      const t = totals(steps, c, D);
      if (cap) {
        const mins = t.sec / 60;
        if (mins > cap + 0.5) {
          const hard = capMode === "hard";
          add(hard ? "err" : "warn", `總時間 ${fx(mins)} 分超過這天上限 ${fx(cap)} 分（課表偏好：${hard ? "硬上限" : "軟上限，只提醒"}）`);
        }
      }
      if (t.open) add("info", `${t.open} 段「直到按下計圈」不算進總時間`);
      const role = rpeRole(steps.items, D);
      if (role) add("info", "RPE 目標：心率、功率只當參考，負荷照手錶記錄算（不用 RPE 校正）；這堂依 RPE 算"
        + (role === "quality" ? "強度課（RPE ≥ 7：和其他強度課隔 48 小時、算進每週強度預算）" : "輕鬆課"));
      for (const it of steps.items) if (it.kind === "repeat" && it.items.some((x) => x.kind === "repeat")) add("warn", "重複裡再放重複：COROS 只確定一層，推送時會攤平", it.id);
      const n = corosCount(steps, c, D);
      if (n > W.coros_max_steps) add("warn", `推到手錶是 ${n} 段，超過 ${W.coros_max_steps} 段：COROS 的上限未驗證`);
      if (rung) { const eq = equivalence(steps, rung, c, D); if (eq) add("info", eq.text); }
      return out;
    }

    // ---- progression: the structure as a library variant (interval_library.equivalent)
    const works = (v) => (v.pattern ? v.pattern.map((x) => Math.trunc(x)) : Array(v.reps * v.sets).fill(Math.trunc(v.work_s)));
    const classOf = (v, D) => { const m = (v.lo + v.hi) / 2; for (const [k, [a, b]] of Object.entries(D.il.class_range)) if (a <= m && m < b) return k; return null; };
    const sum = (xs) => xs.reduce((a, x) => a + x, 0);
    function structure(v) {
      const ws = works(v);
      if (v.sets > 1) return `${v.sets} 組 × ${v.reps}×${fmtS(v.work_s)}／${fmtS(v.rest_s)}`;
      if (ws.length === 1 && !v.rest_s) return `連續 ${fmtS(v.work_s)}`;
      if (v.pattern) {
        const mins = (s) => { const x = s / 60; return Math.abs(x - pyRound(x)) < 0.01 ? fx(x) : fx(x, 1); };
        return v.pattern.map(mins).join("-") + " 分" + (v.pattern.length >= 5 ? "金字塔" : "");
      }
      return `${ws.length}×${fmtS(v.work_s)}`;
    }
    function equivalent(v, ref, D) {
      const why = [], IL = D.il, W = D.ws, wv = works(v), wr = works(ref);
      if (!v.listed_equiv) why.push("列為非同等（30/15：每趟 < 2 分，證據方向不一致）");
      if (classOf(v, D) !== classOf(ref, D) || v.cls !== ref.cls) why.push(`強度類別不同（${v.cls} vs ${ref.cls}）`);
      const t = sum(wv), tr = sum(wr);
      if (tr && Math.abs(t / tr - 1) > IL.tiz_tol + 1e-9) why.push(`目標區時間 ${fx(t / 60)} 分，和 ${fx(tr / 60)} 分差 > 15%`);
      const wp = (x, ws) => sum(ws.map((w) => Math.max(0, (x.lo + x.hi) / 2 - 1) * w)) / Math.max(1, ws.length);
      if (v.cls === "Z5") {
        if (Math.min(...wv) < W.rules.z5_min_rep_s) why.push("5 區每趟 < 2 分（台灣教練）");
        if (v.rest_s > Math.min(...wv) || v.rest_s > W.rules.z5_max_rest_s) why.push("組休比每趟長或 > 3 分");
        const a = wp(v, wv), b = wp(ref, wr);
        if (b && !(IL.wprime_ratio[0] <= a / b && a / b <= IL.wprime_ratio[1])) why.push(`每趟 W′ 是標準課表的 ${fx(a / b, 2)} 倍（範圍 0.7–1.5）`);
      } else if (!(wv.length === 1 && !v.rest_s)) {
        if (Math.min(...wv) < W.rules.z3_min_rep_s) why.push("3 區每趟 < 3 分");
        const ratio = v.rest_s ? (sum(wv) / wv.length) / v.rest_s : null;
        if (ratio == null || !(IL.z3_ratio[0] - 1e-9 <= ratio && ratio <= IL.z3_ratio[1] + 1e-9)) why.push("工休比不在 3:1–6:1");
      }
      return [!why.length, why];
    }
    function workBand(st, c, D) {
      const tg = st.target || {}, ty = tg.type;
      if (ty === "auto" && tg.intent === "band") return [tg.lo, tg.hi, false];
      if (ty === "power") {
        if (tg.mode === "pct") return [tg.lo, tg.hi, false];
        if (tg.mode === "zone") { const [lo, hi] = zoneOf("power", tg.zone, D); return lo != null ? [lo, hi, false] : null; }
        if (c && c.cp) return [tg.lo / c.cp, tg.hi / c.cp, false];
        return null;
      }
      if (ty === "hr") {
        let m;
        if (tg.mode === "pct") m = (tg.lo + tg.hi) / 2;
        else if (tg.mode === "zone" && tg.zone !== "aet") { const [lo, hi] = zoneOf("hr", tg.zone, D); m = (lo + hi) / 2; }
        else if (tg.mode === "abs" && c && c.lthr) m = (tg.lo + tg.hi) / 2 / c.lthr;
        else return null;
        const cls = m >= 1.03 ? "Z5" : m >= 1.0 ? "Z4" : m >= 0.95 ? "Z3near" : m >= 0.88 ? "Z3sub" : null;
        if (cls == null) return null;
        return [...D.ws.hr_class_band[cls], true];
      }
      return null;
    }
    function variantFromSteps(steps, rung, c, D) {
      const rows = flat(steps.items || []), ws = [], rests = [], bands = [];
      let est = false, lastWork = null;
      rows.forEach((row, i) => {
        const st = row.st;
        if (st.kind === "work") {
          const b = workBand(st, c, D);
          if (b == null || st.dur.type !== "time") return;
          ws.push(st.dur.value); bands.push(b.slice(0, 2)); est = est || b[2]; lastWork = i;
        } else if (st.kind === "rest" && lastWork != null && st.dur.type === "time") {
          const nxt = rows.slice(i + 1).map((r) => r.st).find((x) => x.kind === "work" || x.kind === "cool");
          if (nxt && nxt.kind === "work") rests.push(st.dur.value);
        }
      });
      if (!ws.length) return null;
      const lo = sum(bands.map((b) => b[0])) / bands.length, hi = sum(bands.map((b) => b[1])) / bands.length;
      const cls = classOf({ lo, hi }, D);
      if (cls == null) return null;
      const same = new Set(ws).size === 1;
      return { key: "user", rung: rung || "", cls, reps: ws.length, work_s: ws[0], rest_s: Math.trunc(rests.length ? Math.max(...rests) : 0),
        rest_mode: rests.length ? "walk" : "none", lo: pyRound(lo, 3), hi: pyRound(hi, 3), terrain: "flat", canonical: false,
        src_kind: est ? "推估" : "peer", pattern: same ? null : ws, sets: 1, set_rest_s: 0, listed_equiv: true };
    }
    function equivalence(steps, rung, c, D) {
      const canon = rung && D.il.canonical[rung];
      if (!canon) return null;
      const name = D.il.rung_name[rung] || rung;
      const v = variantFromSteps(steps, rung, c, D);
      if (v == null) return { ok: false, why: ["找不到有強度的主課段"], text: `${name}：找不到有功率／心率目標的主課段，這堂不算進階` };
      const [ok, why] = equivalent(v, canon, D);
      const est = v.src_kind === "推估" ? "（心率結構換算強度，推估）" : "";
      return { ok, why, text: `和 ${name} 標準課表 ${structure(canon)} ` + (ok ? "等效：這堂算進階" : "不等效：這堂不算進階（" + why.join("；") + "）") + est };
    }

    // ---- the watch (sync/coros_workouts.build_program as lines)
    const EX = { warm: 1, work: 2, other: 2, rest: 4, cool: 3 };
    const EX_LABEL = { 1: "暖身", 2: "訓練", 3: "緩和", 4: "休息" };
    const fmtDur = (d) => (d.type === "time" ? fmtS(d.value) : d.type === "distance" ? (d.value >= 1000 ? `${fmtG(d.value / 1000)} km` : `${d.value} m`) : "直到按下計圈");
    function stepsToCoros(steps, c, D) {
      const em = { n: 0 }, out = [];
      const name = (st, r, grouped) => {
        const tg = st.target || {};
        if (tg.type === "rpe") {
          const extra = [`RPE ${rpeText(tg.lo, tg.hi)}`, climbText(tg)].filter(Boolean).join(" · ");
          return st.note ? `${st.note} · ${extra}` : extra;
        }
        if (st.note) return st.note;
        if (st.kind === "work" && tg.type === "auto" && tg.intent === "easy" && tg.plo != null) return r.type === "hr" ? "心率 ≤ 輕鬆跑上限" : r.type === "power" ? "功率區間" : "照感覺";
        if (st.kind === "work" && !grouped) { em.n++; return `第 ${em.n} 趟 ${fmtDur(st.dur)}`; }
        return "";
      };
      const one = (st, grouped) => {
        const r = resolve(st, c, D), d = st.dur;
        return { kind: EX[st.kind], seconds: Math.trunc(d.type === "time" ? d.value : 0), intensity: r.intensity, name: name(st, r, grouped),
          meters: Math.trunc(d.type === "distance" ? d.value : 0) };
      };
      const plain = (xs) => xs.every((x) => x.kind !== "repeat");
      const emit = (items) => {
        for (const it of items) {
          if (it.kind !== "repeat") out.push(one(it, false));
          else if (plain(it.items) && it.last_rest !== false) out.push({ sets: it.times, steps: it.items.map((x) => one(x, true)), name: it.note || "間歇" });
          else iterRep(it).forEach(emit);
        }
      };
      emit(steps.items);
      return out;
    }
    const corosCount = (steps, c, D) => sum(stepsToCoros(steps, c, D).map((x) => 1 + (x.steps ? x.steps.length : 0)));
    function exLine(st, D) {
      const dur = st.meters ? `${fmtG(st.meters * 100 / 100000)} km` : st.seconds ? fmtS(st.seconds) : "直到按下計圈";
      let tgt = "不設目標";
      if (st.intensity) {
        const [typ, lo, hi] = st.intensity;
        if (typ === "hr") tgt = `心率 ${Math.trunc(lo)}–${Math.trunc(hi)} bpm`;
        else if (typ === "power") tgt = `功率 ${Math.trunc(lo)}–${Math.trunc(hi)} W`;
        else if (typ === "pace") { const [a, b] = [pyRound(lo), pyRound(hi)].sort((x, y) => x - y); tgt = `配速 ${mmss(a)}–${mmss(b)} /km`; }
      }
      return { kind: EX_LABEL[st.kind] || "訓練", dur, target: tgt, name: st.name || D.ws.step_name[st.kind] || "" };
    }
    function watchPreview(steps, c, D) {
      const lines = [];
      let n = 0, total = 0;
      for (const x of stepsToCoros(steps, c, D)) {
        n++;
        if (x.steps) {
          lines.push({ group: x.name, sets: x.sets, steps: x.steps.map((s) => exLine(s, D)) });
          n += x.steps.length;
          total += sum(x.steps.map((s) => s.seconds)) * x.sets;
        } else { lines.push(exLine(x, D)); total += x.seconds; }
      }
      const res = flat(steps.items).map((row) => [row.st, resolve(row.st, c, D)]);
      const hasPower = res.some(([, r]) => r.type === "power");
      const unrolled = steps.items.some((it) => it.kind === "repeat" && (it.last_rest === false || !it.items.every((x) => x.kind !== "repeat")));
      const dist = res.some(([st]) => st.dur.type === "distance");
      const limits = [
        { key: "watts", hit: hasPower, text: "只收絕對瓦數：跑步沒有 % CP，送的是換算後的 W；CP 更新後這堂會標成「已過期」，要重推" },
        { key: "one", hit: !!(c.hr_cap && hasPower), text: "每段只有一個目標：功率段的心率上限只寫在文字，手錶不會提醒" },
        { key: "ramp", hit: false, text: "沒有漸進（ramp）步驟：漸進只寫在步驟名稱" },
      ];
      if (res.some(([, r]) => r.type === "rpe")) limits.push({ key: "rpe", hit: true, text: D.ws.rpe.limit });
      const lost = [];
      if (res.some(([, r]) => r.need === "tpace")) lost.push(D.ws.no_tpace);
      if (unrolled) lost.push("「最後一趟不休息」或重複裡的重複：COROS 群組做不到，推送時攤平成一段一段");
      if (dist) lost.push("距離段：COROS 欄位（公分）依第三方整理，這個 app 還沒實際送過（未驗證）");
      if (n > D.ws.coros_max_steps) lost.push(`${n} 段超過 ${D.ws.coros_max_steps} 段：COROS 的上限未驗證`);
      return { lines, n, limits, lost, seconds: total };
    }

    // ---- text
    function stepsText(steps, c, D) {
      const parts = [], rows = flat(steps.items);
      let work = rows.filter((r) => r.st.kind === "work").map((r) => r.st);
      if (!work.length) work = rows.slice(0, 1).map((r) => r.st);
      for (const st of work) {
        const r = resolve(st, c, D);
        if (r.type === "none") continue;
        const t = `${D.ws.type_label[r.type]} ${r.text}` + (r.sub ? `（${r.sub.split(" · ")[0]}）` : "");
        if (!parts.includes(t)) parts.push(t);
      }
      return cut(parts.slice(0, 3).join(" · "), 200);
    }
    function structureText(steps, D) {
      const one = (x) => (x.kind === "repeat" ? `${x.times}×(` + x.items.map(one).join("＋") + ")"
        : ["work", "rest", "other"].includes(x.kind) ? fmtDur(x.dur) : `${D.ws.kind_label[x.kind]} ${fmtDur(x.dur)}`);
      return cut(steps.items.map(one).join(" · "), 300);
    }
    function zonesTable(c, D) {
      const rows = (ty) => D.ws.zones[ty].map(([z, lo, hi]) => {
        if (ty === "hr" && z === "aet") { const e = easyHr(c); return { id: "aet", label: "≤ 輕鬆跑上限", text: e ? `${e[1]}–${e[2]} bpm` : "" }; }
        const base = { power: c.cp, hr: c.lthr, pace: c.tpace }[ty];
        const text = !base ? "" : ty === "pace" ? `${mmss(lo * base)}–${mmss(hi * base)} /km` : `${fx(lo * base)}–${fx(hi * base)} ${ty === "power" ? "W" : "bpm"}`;
        return { id: z, label: `Z${z}`, lo, hi, text };
      });
      return { power: rows("power"), hr: rows("hr"), pace: rows("pace") };
    }
    function view(steps, c, D, cap, capMode, rung) {
      const byId = {}, order = [];
      for (const row of flat(steps.items)) {
        const st = row.st, r = resolve(st, c, D), [s, e] = secs(st, r, c, D);
        if (!(st.id in byId)) byId[st.id] = asDict(r, D);
        order.push({ id: st.id, kind: st.kind, sec: pyRound(s), est: e, open: st.dur.type === "open", frac: r.frac, level: level(r.frac),
          type: r.type, rep: row.rep.map(([a, i, n]) => ({ id: a, i, n })) });
      }
      const eq = rung ? equivalence(steps, rung, c, D) : null;
      return { resolved: byId, order, totals: totals(steps, c, D), issues: issues(steps, c, D, cap, capMode, rung), watch: watchPreview(steps, c, D),
        summary: stepsText(steps, c, D), structure: structureText(steps, D), rpe_role: rpeRole(steps.items, D), equiv: eq ? { ok: eq.ok, why: eq.why, text: eq.text } : null };
    }

    // ---- derive (engine/workout_steps.derive): stored / exported (data.derive) / the easy and long kinds here
    const SIG_FIELDS = ["kind", "title", "minutes", "target", "detail", "source", "protocol", "variant_key", "variant_reps", "variant_blocks", "variant_adj", "heat", "gen_key"];
    const sortKeys = (x) => (Array.isArray(x) ? x.map(sortKeys) : isDict(x) ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, sortKeys(x[k])])) : x);
    const sigVal = (k, v) => (v == null || v === false ? "" : v === true ? "1" : k === "minutes" ? String(toInt(v || 0) ?? 0)
      : typeof v === "object" ? JSON.stringify(sortKeys(v)) : String(v));
    const deriveSig = (s) => SIG_FIELDS.map((k) => sigVal(k, s[k])).join("\u001f");
    function deriveHere(s, D) {
      let n = 0;
      const ids = () => `s${++n}`;
      const EASY = { type: "auto", intent: "easy" }, OPEN = { type: "auto", intent: "open" };
      const easy = (plo, phi) => (plo != null ? { ...EASY, plo, phi } : { ...EASY });
      const step = (kind, dur, target, note = "") => ({ id: ids(), kind, dur: typeof dur === "number" ? (dur ? { type: "time", value: Math.trunc(dur) } : { type: "open" }) : dur,
        target: target ? { ...target } : { ...OPEN }, note });
      const doc = (items) => ({ v: 1, origin: "derived", items });
      const kind = s.kind, sec = (toInt(s.minutes || 0) || 0) * 60, title = String(s.title || "");
      if (["race", "rest", "strength", "heat_passive"].includes(kind)) return null;
      if (kind === "notice") return doc([step("warm", 60, OPEN, "課表待確認：到總覽頁同意／拒絕")]);
      if (kind === "quality" || kind === "test") return undefined;          // only from data.derive
      if (sec <= 0) return null;
      const M = D.ws.mp;
      const mpm = kind === "long" ? (title.match(/馬拉松配速\s*(\d+)\s*分/) || [])[1] : null;
      if (mpm && sec - +mpm * 60 - M.tail_s >= 600) {
        const g = String(s.detail || "").match(/目標配速\s*(\d+):(\d{2})\s*\/km/);
        const gp = g ? +g[1] * 60 + +g[2] : null;
        const mt = gp ? { type: "pace", mode: "abs", lo: pyRound(gp * (1 - M.goal_band)), hi: pyRound(gp * (1 + M.goal_band)) }
          : { type: "pace", mode: "pct", lo: M.pace[0], hi: M.pace[1], hrp: M.hr.slice() };
        const a = step("work", sec - +mpm * 60 - M.tail_s, easy(0.8, 0.88), "輕鬆");
        return doc([a, step("work", +mpm * 60, mt, "馬拉松配速"), step("cool", M.tail_s, easy(0.75, 0.8), "輕鬆收操")]);
      }
      if (["long", "mountain", "hike"].includes(kind)) { const [lo, hi] = kind === "long" ? [0.8, 0.88] : [0.75, 0.88]; return doc([step("work", sec, easy(lo, hi))]); }
      if (kind === "easy" && (s.heat || title.includes("熱適應")) && sec >= 1200)
        return doc([step("warm", 600, EASY, "熱適應：慢慢進入"), step("work", sec - 900, EASY, "熱適應：照心率、配速放慢"), step("cool", 300, OPEN, "走路降溫")]);
      if (kind === "easy") {
        const m = title.match(/(\d+)\s*[×xX]\s*(\d+)\s*秒/);
        if (m) {
          const k = +m[1], sp = +m[2], base = sec - k * (sp + 60);
          if (base >= 600) {
            const flatRun = title.includes("加速跑");
            const [w, r, rn] = flatRun ? [`${sp} 秒加速跑（平路）`, "慢跑回來", `加速跑 ${k}×${sp} 秒`] : [`${sp} 秒上坡衝刺`, "走下來", `衝刺 ${k}×${sp} 秒`];
            const a = step("work", base, easy(0.75, 0.8), "心率 ≤ 輕鬆跑上限");
            const kids = [step("work", sp, OPEN, w), step("rest", 60, OPEN, r)];
            return doc([a, { id: ids(), kind: "repeat", times: k, last_rest: true, note: rn, items: kids }]);   // rep() takes its id after its kids
          }
        }
        return doc([step("work", sec, easy(0.75, 0.8))]);
      }
      return null;
    }
    const NO_STEPS = "這種課不推到手錶，或標題看不出結構（用「＋ 步驟」自己排）";
    const STATIC_NO_STEPS = "示範版排不出這堂課的結構：用「插入範本」或「＋ 步驟」自己排";

    // POST /steps/derive and /steps/check: (body, the stored session or null, data) -> response body
    function derive(body, stored, D) {
      body = body || {};
      const s = sessionOf(body, stored), e = env(s, D), ctx = context(e, D);
      if (stored && stored.steps && !body.rederive) return { steps: stored.steps, derived: false, context: ctx };
      const k = deriveSig(s);
      let d = Object.prototype.hasOwnProperty.call(D.derive || {}, k) ? D.derive[k] : deriveHere(s, D);
      const unknown = d === undefined;
      if (unknown) d = null;
      return { steps: d, derived: true, context: ctx, reason: d ? "" : unknown ? STATIC_NO_STEPS : NO_STEPS };
    }
    function check(body, stored, D) {
      body = body || {};
      const s = sessionOf(body, stored), st = normalize(body.steps, D), e = env(s, D);
      return { ...view(st, e.ctx, D, e.cap, e.cap_mode, e.rung), basis_label: context(e, D).basis_label, policy: e.policy };
    }
    // GET /steps/templates/recs from data.recs (export_static.pass_steps): this length, else the day's
    // 推薦 for no particular length, else the nearest exported day of the same weekday (the day's cap)
    const recsTerrain = (kind, t) => (kind === "hike" || ["trail", "hike"].includes(t) ? "trail" : "road");
    const recsKey = (kind, day, ter, m) => `${kind}|${day}|${ter}|${m && isFinite(+m) && +m ? Math.trunc(+m) : ""}`;
    function recsFor(D, kind, day, terrain, minutes) {
      const R = (D.recs || {}).keys || {}, pool = (D.recs || {}).pool || [], ter = recsTerrain(kind, terrain);
      const at = (i) => (i == null ? null : pool[i] || null);
      const hit = at(R[recsKey(kind, day, ter, minutes)] ?? R[recsKey(kind, day, ter)]);
      if (hit || !day) return hit;
      const t = Date.parse(day + "T00:00:00Z"), wd = new Date(t).getUTCDay();
      let best = null, bd = Infinity;
      for (const k of Object.keys(R)) {
        const [kk, dd, tt, mm] = k.split("|");
        if (kk !== kind || tt !== ter || mm) continue;
        const u = Date.parse(dd + "T00:00:00Z");
        if (new Date(u).getUTCDay() === wd && Math.abs(u - t) < bd) { bd = Math.abs(u - t); best = at(R[k]); }
      }
      return best;
    }
    return { StepsError, pyRound, normalize, derive, check, deriveSig, targetPolicy, view, ctxOf, recsFor, recsKey };
  })();

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
    let stepsP = null;                  // data/steps_ctx.json (backend/demo/static_steps.py), loaded once
    const stepsData = () => (stepsP ||= loadData(STEPS_FILE).then((d) => (d && d.v === 1 ? d : null)));
    const unwrap = (j) => (j && typeof j === "object" && !Array.isArray(j) && "__trc_status" in j
      ? { status: j.__trc_status, body: j.body } : { status: 200, body: j });

    // -- the race calculator's engine (a Web Worker with Pyodide), started on the first
    //    computation the export did not precompute; a small pill shows its progress
    const Engine = (() => {
      let worker = null, readyP = null, seq = 0, loadMs = null;
      const waiting = new Map();
      const pill = (text) => {
        let el = doc.getElementById("trc-engine");
        if (!text) { if (el) el.hidden = true; return; }
        if (!el) {
          el = doc.createElement("div"); el.id = "trc-engine"; el.setAttribute("role", "status");
          el.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:80;padding:7px 12px;border-radius:999px;background:#1f2937;color:#fff;font:12.5px/1.3 system-ui,sans-serif;box-shadow:0 6px 18px rgba(0,0,0,.22);opacity:.92";
          doc.body.appendChild(el);
        }
        el.textContent = text; el.hidden = false;
      };
      function start() {
        if (readyP) return readyP;
        readyP = new Promise((resolve, reject) => {
          try { worker = new win.Worker(STATIC + RACEPOWER_WORKER, { type: "module" }); } catch (e) { reject(e); return; }
          worker.onmessage = (ev) => {
            const m = ev.data || {};
            if (m.type === "progress") pill(`載入計算引擎… ${m.text}`);
            else if (m.type === "ready") { loadMs = m.ms; resolve(m.ms); }
            else if (m.type === "fail") reject(new Error(m.error));
            else if (m.type === "result") { const w = waiting.get(m.id); if (w) { waiting.delete(m.id); w(m.out); } }
          };
          worker.onerror = (e) => reject(new Error((e && e.message) || "worker error"));
          worker.postMessage({ type: "init", base: STATIC, data: DATA });
        });
        pill("載入計算引擎…（第一次要下載約 10 MB）");
        readyP.then(() => pill(null), () => pill(null));
        return readyP;
      }
      // -> {status, body} | {status, csv, filename}
      async function call(method, path, body) {
        try { await start(); }
        catch (e) {
          if (win.console) win.console.warn("trc static engine:", e);
          readyP = null; if (worker) { try { worker.terminate(); } catch (_) {} worker = null; }   // retry next time
          return { status: 503, body: { detail: ENGINE_FAIL_MSG } };
        }
        const id = ++seq;
        const slow = setTimeout(() => pill("計算中…"), 400);
        const out = await new Promise((res) => { waiting.set(id, res); worker.postMessage({ type: "call", id, method, path, body }); });
        clearTimeout(slow); pill(null);
        let r;
        try { r = JSON.parse(out); } catch (_) { r = { status: 500, body: { detail: ENGINE_ERR_MSG } }; }
        if (r.error && win.console) win.console.warn("trc static engine:", r.error, r.trace || "");
        if (r.status === 503) r.body = { detail: ENGINE_FAIL_MSG };
        return r;
      }
      return { call, start, get loadMs() { return loadMs; } };
    })();
    const engineResponse = (r) => {
      if (r.csv != null) {
        return new win.Response("﻿" + r.csv, { status: 200, headers: { "Content-Type": "text/csv; charset=utf-8",
          "X-Filename": encodeURIComponent(r.filename || "racepower.csv") } });
      }
      return json(r.status, r.body);
    };
    // GET /racepower/weather the export did not save: what the server answers without network
    const offlineWeather = (url) => {
      const p = url.searchParams, date = p.get("date") || "";
      const lead = date ? Math.round((Date.parse(date.slice(0, 10) + "T00:00:00Z") - Date.parse((CFG.today || CFG.snapshot) + "T00:00:00Z")) / 864e5) : null;
      const num = (k) => (p.get(k) == null || p.get(k) === "" ? null : Number(p.get(k)));
      return { provider: "manual", label: "手動 / 預設", values: null, hourly: null, fetched_at: null, lead_days: lead,
        tried: [{ provider: "weather", ok: false, reason: "示範版不連網查天氣：比賽日溫度、濕度請自己填" }],
        location: { name: p.get("peak"), lat: num("lat"), lon: num("lon"), elevation_m: num("elevation") }, peak: null };
    };

    // data/racepower_saved.json: which race-calculator answers the export saved (none listed: ask
    // the engine without a 404 first); an export without it: try the file as before
    let rpSavedP = null;
    const rpSaved = () => (rpSavedP ||= loadData(RACEPOWER_SAVED).then((d) => (d && Array.isArray(d.files) ? new Set(d.files) : null)));
    const rpMissing = async (path, file) => {          // only where a miss has an answer (engine / offline)
      if (!RACEPOWER_POSTS.test(path) && !/^\/api\/v1\/racepower\/(weather|heat-status)$/.test(path)) return false;
      const s = await rpSaved();
      return !!s && !s.has(file);
    };

    async function getApi(url) {
      const key = dataKey(url.pathname, url.search);
      if (await rpMissing(url.pathname, fnv64(key) + ".json")) { misses.push(key); return null; }
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
        if (path === PAPI + "/steps/templates/recs") {       // 插入範本's 推薦: its query follows every edit
          const D = await stepsData(), p = url.searchParams;
          const st = p.get("uid") ? findSession(p.get("uid")) : null;
          const r = D && Steps.recsFor(D, p.get("kind") || "easy", p.get("day") || (st && st.day) || "", p.get("terrain") || (st && st.terrain), p.get("minutes"));
          if (r) return json(200, r);
        }
        const got = await getApi(url);
        if (!got && path === "/api/v1/racepower/weather") return json(200, offlineWeather(url));
        if (!got && path === "/api/v1/racepower/heat-status")
          return engineResponse(await Engine.call("GET", path, JSON.stringify({ date: url.searchParams.get("date") || null })));
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
      // the structure editor: computed here (Steps; the session as shown, overlay edits included)
      if (method === "POST" && (path === PAPI + "/steps/check" || path === PAPI + "/steps/derive")) {
        let parsed = null;
        try { parsed = typeof body === "string" && body ? JSON.parse(body) : null; } catch (_) {}
        const D = await stepsData();
        if (D && parsed) {
          const stored = parsed.uid ? findSession(parsed.uid) : null;
          try {
            return json(200, path.endsWith("/check") ? Steps.check(parsed, stored, D) : Steps.derive(parsed, stored, D));
          } catch (e) {
            if (e instanceof Steps.StepsError) return json(400, { detail: { errors: e.errors } });
            if (win.console) win.console.warn("trc static steps:", e);
          }
        }
        return json(403, errBody("STATIC_PRECOMPUTED_ONLY", CALC_MSG));
      }
      if (method === "POST" && RACEPOWER_POSTS.test(path) && (typeof body === "string" || body == null)) {
        const pf = typeof body === "string" ? postFile(method, path, url.search, body) : null;
        if (pf && !(await rpMissing(path, pf))) {     // the export's answer to the page's default inputs: instant
          const j = await loadData(pf);
          if (j !== null) { const u = unwrap(j); if (u.status === 200) return json(200, u.body); }
        }
        return engineResponse(await Engine.call(method, path, body == null ? null : body));
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
    Steps, STEPS_FILE, RO_MSG, MISS_MSG, CALC_MSG,
  };
});
