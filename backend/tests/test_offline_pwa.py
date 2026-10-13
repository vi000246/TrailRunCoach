"""
離線唯讀快取 (SP-314): the service worker's rules and engine (static/offline_core.js) under node,
with a fake Cache Storage and a fake server. Synthetic only.

  - what is kept: only GET, only the listed paths; writes are never cached or queued
  - offline: the copy answers (marked with its time); a page from the copy says so
  - online: the network answers and refreshes the copy (also after a slow answer was served)
  - whose copy: another person, a sign-out, a 401 or a redirect to sign-in deletes it
  - tiers: refill p0 → p1 → p2 (a cleared tier at once); over the 20 MB budget p2 goes first
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
CORE = STATIC / "offline_core.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

ORIGIN = "https://coach.test"

PRELUDE = r"""
const O = require(process.argv[1]);
const ORIGIN = "https://coach.test";
class FakeCache {
  constructor(store) { this.m = new Map(); this.store = store; }
  key(r) { return typeof r === "string" ? r : r.url; }
  async match(r) { const x = this.m.get(this.key(r)); return x ? x.clone() : undefined; }
  async put(r, resp) {
    if (this.store.full && this.store.full(this.key(r), this)) { const e = new Error("full"); e.name = "QuotaExceededError"; throw e; }
    this.m.set(this.key(r), resp);
  }
  async delete(r) { return this.m.delete(this.key(r)); }
  async keys() { return [...this.m.keys()].map((url) => ({ url })); }
}
class FakeCaches {
  constructor() { this.c = new Map(); this.full = null; }
  async open(n) { if (!this.c.has(n)) this.c.set(n, new FakeCache(this)); return this.c.get(n); }
  async has(n) { return this.c.has(n); }
  async delete(n) { return this.c.delete(n); }
  async keys() { return [...this.c.keys()]; }
  async match(r, o) {
    if (o && o.cacheName) { const c = this.c.get(o.cacheName); return c ? c.match(r) : undefined; }
    for (const c of this.c.values()) { const x = await c.match(r); if (x) return x; }
  }
  urls(n) { const c = this.c.get(n); return c ? [...c.m.keys()].map((u) => u.replace(ORIGIN, "")) : null; }
}
// the server: path (+query) -> handler; down = network error; delay = ms before answering
const server = { routes: {}, down: false, delay: 0, calls: [], version: 1 };
const json = (v, status = 200) => new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } });
const html = (s) => new Response(s, { headers: { "content-type": "text/html; charset=utf-8" } });
async function fakeFetch(req, init) {
  const url = typeof req === "string" ? req : req.url;
  const method = (init && init.method) || (typeof req === "object" && req.method) || "GET";
  server.calls.push(method + " " + url.replace(ORIGIN, ""));
  if (server.delay) await new Promise((r) => setTimeout(r, server.delay));
  if (server.down) throw new TypeError("Failed to fetch");
  const u = new URL(url);
  const h = server.routes[u.pathname + u.search] || server.routes[u.pathname];
  if (!h) return json({ detail: "not found" }, 404);
  return h(u, req);
}
const req = (path, o = {}) => ({ url: path.startsWith("http") ? path : ORIGIN + path, method: o.method || "GET", mode: o.mode || "cors" });
const nav = (path) => req(path, { mode: "navigate" });
let clock = Date.parse("2026-10-13T08:00:00Z");
const caches = new FakeCaches();
const pending = [];
const mk = (o = {}) => O.createEngine({ caches, fetch: fakeFetch, origin: ORIGIN, version: "v1", now: () => clock,
  timeouts: { page: 40, app: 40, api: 40 }, ...o });
const ctx = { waitUntil: (p) => pending.push(p) };
const settle = () => Promise.all(pending.splice(0));
async function get(E, r) { const p = E.handleFetch(r, ctx); if (!p) return null; const res = await p; await settle(); return res; }
async function body(res) { return res ? await res.text() : null; }
// a person's server: status / weekplan / calendar ... answer with the person's name in them
function person(name, id) {
  server.routes["/api/v1/session"] = () => json(id == null ? { mode: "signed_out", user: null, login: { required: true, signed_in: false } }
    : { mode: "user", user: { id, name }, login: { required: true, signed_in: true } });
  const page = (title) => () => html(`<!doctype html><html><head><title>${title}</title></head><body>${name} ${title}</body></html>`);
  server.routes["/api/v1/overview/page"] = page("overview");
  server.routes["/api/v1/overview/plan/schedule/page"] = page("schedule");
  server.routes["/api/v1/static/offline.html"] = page("offline");
  server.routes["/api/v1/wko5/activities/page"] = page("activities");
  server.routes["/api/v1/overview/status"] = () => json({ who: name, today: "2026-10-14" });
  server.routes["/api/v1/overview/weekplan"] = () => json({ who: name, week: { start: "2026-10-12", end: "2026-10-18" } });
  server.routes["/api/v1/overview/plan/calendar"] = (u) => json({ who: name, start: u.searchParams.get("start"), end: u.searchParams.get("end"), v: server.version });
  for (const p of ["/api/v1/overview/z5", "/api/v1/overview/b2b", "/api/v1/overview/summary", "/api/v1/overview/pmc",
    "/api/v1/sync/settings", "/api/v1/region", "/api/v1/overview/plan/prefs", "/api/v1/overview/plan/prefs/gate",
    "/api/v1/overview/plan/equivalence", "/api/v1/wko5/activities", "/api/v1/wko5/activities/stats", "/api/v1/achievements"])
    server.routes[p] = (u) => json({ who: name, path: u.pathname, q: u.search });
}
let OUT;
(async () => {
__BODY__
})().then(() => console.log(JSON.stringify(OUT === undefined ? null : OUT)), (e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def run(body: str):
    js = PRELUDE.replace("__BODY__", body)
    r = subprocess.run([NODE, "-e", js, str(CORE)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------- the rules

def test_only_listed_gets_are_kept():
    out = run(r"""
const c = (p, nav) => { const x = O.classify(p.startsWith("http") ? p : ORIGIN + p, ORIGIN, !!nav); return x ? x.kind + ":" + x.tier : null; };
OUT = {
  status: c("/api/v1/overview/status"), calendar: c("/api/v1/overview/plan/calendar?start=2026-10-12&end=2026-10-18"),
  acts: c("/api/v1/wko5/activities"), page: c("/api/v1/overview/page", true), sched: c("/api/v1/overview/plan/schedule/page?day=2026-10-14", true),
  shell: c("/api/v1/static/shell.js"), echarts: c("https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js"),
  otherCdn: c("https://cdn.example.com/x.js"), session: c("/api/v1/session"), fresh: c("/api/v1/overview/plan/fresh"),
  staticHtml: c("/api/v1/static/compare.html"), offlineHtml: c("/api/v1/static/offline.html"), settingsPage: c("/api/v1/wko5/settings", true),
  login: c("/login", true), dataset: c("/api/v1/wko5/dataset/status"), samples: c("/api/v1/wko5/workouts/3/samples"),
  account: c("/api/v1/account/logout"),
};
const E = mk();
OUT.post = E.handleFetch(req("/api/v1/overview/status", { method: "POST" }), ctx);
OUT.patch = E.handleFetch(req("/api/v1/overview/plan/sessions/x", { method: "PATCH" }), ctx);
OUT.loginNav = E.handleFetch(nav("/login"), ctx);
OUT.oauthNav = E.handleFetch(nav("/oauth/google/callback?code=1"), ctx);
OUT.shareNav = E.handleFetch(nav("/share/abc"), ctx);
OUT.unlisted = E.handleFetch(req("/api/v1/plan"), ctx);
OUT.calls = server.calls;
""")
    assert out["status"] == "api:p0" and out["calendar"] == "api:p0" and out["acts"] == "api:p1"
    assert out["page"] == "page:p0" and out["sched"] == "page:p0" and out["offlineHtml"] == "page:p0"
    assert out["shell"] == "app:p0" and out["echarts"] == "app:p0"
    for k in ("otherCdn", "session", "fresh", "staticHtml", "settingsPage", "login", "dataset", "samples", "account"):
        assert out[k] is None, k
    # writes, sign-in and the shares are left to the browser: never answered, never kept
    for k in ("post", "patch", "loginNav", "oauthNav", "shareNav", "unlisted"):
        assert out[k] is None, k
    assert out["calls"] == []


def test_whose_copy():
    out = run(r"""
OUT = [O.userKeyOf({ mode: "owner", caps: [], user: null }),                                        // single-user app
  O.userKeyOf({ mode: "user", user: { id: 7 }, login: { required: true, signed_in: true } }),
  O.userKeyOf({ mode: "owner", user: { id: 1 }, login: { required: true, signed_in: true } }),      // the owner, signed in
  O.userKeyOf({ mode: "signed_out", user: null, login: { required: true, signed_in: false } }),
  O.userKeyOf({ mode: "demo", caps: [] }), O.userKeyOf(null), O.userKeyOf({ mode: "owner", login: { required: true } }),
  O.userCache("u7", "p1")];
""")
    assert out == ["owner", "u7", "u1", None, None, None, None, "trc-u-u7-p1"]


def test_the_days_calendars_and_pmc_ranges():
    out = run(r"""OUT = O.warmDerived({ today: "2026-10-14", week: { start: "2026-10-12", end: "2026-10-18" } });""")
    cal = [u for u in out if "/calendar?" in u]
    assert cal == [
        "/api/v1/overview/plan/calendar?start=2026-10-12&end=2026-10-18",     # this week (總覽 and 課表 on a phone)
        "/api/v1/overview/plan/calendar?start=2026-10-19&end=2026-10-25",
        "/api/v1/overview/plan/calendar?start=2026-10-26&end=2026-11-01",
        "/api/v1/overview/plan/calendar?start=2026-09-28&end=2026-11-01",     # October's month grid (Mon … Sun)
        "/api/v1/overview/plan/calendar?start=2026-10-26&end=2026-12-06",     # November's
    ]
    assert [u for u in out if "/pmc?" in u] == [
        "/api/v1/overview/pmc?begin=2026-10-07&end=2026-10-14",
        "/api/v1/overview/pmc?begin=2026-09-02&end=2026-10-14",
        "/api/v1/overview/pmc?begin=2026-07-16&end=2026-10-14",
    ]
    assert run(r"""OUT = O.warmDerived({ today: null });""") == []


def test_fallback_for_another_day():
    out = run(r"""
const C = ORIGIN + "/api/v1/overview/plan/calendar", P = ORIGIN + "/api/v1/overview/pmc", S = ORIGIN + "/api/v1/overview/summary";
const cands = [
  { url: C + "?start=2026-10-12&end=2026-10-18", at: 1 }, { url: C + "?start=2026-09-28&end=2026-11-01", at: 2 },
  { url: P + "?begin=2026-09-02&end=2026-10-14", at: 1 }, { url: P + "?begin=2026-10-07&end=2026-10-14", at: 2 },
  { url: S + "?unit=week&n=16", at: 1 }, { url: S + "?unit=month&n=12", at: 1 },
  { url: ORIGIN + "/api/v1/overview/plan/schedule/page", at: 1 },
];
const f = (u) => { const x = O.pickFallback(u, cands, ORIGIN); return x && x.replace(ORIGIN, ""); };
OUT = {
  weekInMonth: f(C + "?start=2026-10-19&end=2026-10-25"),       // covered by the month grid
  thisWeek: f(C + "?start=2026-10-12&end=2026-10-14"),          // the narrowest copy covering it
  notCovered: f(C + "?start=2026-12-07&end=2026-12-13"),        // a calendar never answers for other days
  pmcNextDay: f(P + "?begin=2026-09-03&end=2026-10-15"),        // most overlap
  summaryOtherPeriod: f(S + "?unit=week&n=16&anchor=2026-08-03"),
  summaryOtherUnit: f(S + "?unit=year&n=6"),
  pageWithQuery: f(ORIGIN + "/api/v1/overview/plan/schedule/page?day=2026-10-15"),
  otherPath: f(ORIGIN + "/api/v1/overview/status"),
};
""")
    assert out == {
        "weekInMonth": "/api/v1/overview/plan/calendar?start=2026-09-28&end=2026-11-01",
        "thisWeek": "/api/v1/overview/plan/calendar?start=2026-10-12&end=2026-10-18",
        "notCovered": None,
        "pmcNextDay": "/api/v1/overview/pmc?begin=2026-09-02&end=2026-10-14",
        "summaryOtherPeriod": "/api/v1/overview/summary?unit=week&n=16",
        "summaryOtherUnit": None,
        "pageWithQuery": "/api/v1/overview/plan/schedule/page",
        "otherPath": None,
    }


def test_eviction_drops_p2_then_p1_never_p0_or_the_program():
    out = run(r"""
const MB = 1024 * 1024;
const e = (key, tier, mb, at, kind = "api") => ({ key, tier, kind, bytes: mb * MB, at });
const all = [e("app", "p0", 2, 0, "app"), e("a0", "p0", 4, 1), e("b1", "p1", 6, 1), e("c1", "p1", 3, 5), e("d2", "p2", 5, 9), e("f2", "p2", 4, 2)];
const keys = (p) => p.drop.map((x) => x.key);
OUT = { under: keys(O.planEviction(all, 24 * MB)), small: keys(O.planEviction(all, 18 * MB)),
  smaller: keys(O.planEviction(all, 10 * MB)), tiny: O.planEviction(all, 1 * MB), budget: O.BUDGET / MB };
OUT.tiny = { drop: OUT.tiny.drop.map((x) => x.key), over: OUT.tiny.over };
""")
    assert out["budget"] == 20
    assert out["under"] == []
    assert out["small"] == ["f2", "d2"]                       # p2 first, the oldest first
    assert out["smaller"] == ["f2", "d2", "b1"]               # then p1, until under the budget
    assert out["tiny"] == {"drop": ["f2", "d2", "b1", "c1"], "over": True}     # p0 and the program stay


def test_a_page_from_the_copy_says_so_before_its_scripts():
    out = run(r"""OUT = [O.markOfflineHtml('<!doctype html><html lang="zh-TW"><head><meta charset="utf-8"><script src="/x.js"></script></head></html>', "2026-10-13T08:00:00.000Z", "offline"),
  O.markOfflineHtml("<p>x</p>", null, "slow"), O.markOfflineHtml("<head></head>", "</script><b>", "offline")];""")
    a, b, c = out
    assert a.startswith('<!doctype html><html lang="zh-TW"><head><script>window.TRC_OFFLINE_PAGE = {"at":"2026-10-13T08:00:00.000Z","reason":"offline"};</script><meta')
    assert b.startswith('<script>window.TRC_OFFLINE_PAGE = {"at":null,"reason":"slow"};</script><p>')
    assert "</script><b>" not in c and "\\u003c/script>" in c


# ---------------------------------------------------------------------------- the engine

def test_offline_the_copy_answers_and_online_the_network_refreshes_it():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.setUser("u7");
const C = "/api/v1/overview/plan/calendar?start=2026-10-12&end=2026-10-18";
const first = await get(E, req(C));
const r1 = { status: first.status, cache: first.headers.get("x-trc-cache"), body: await first.json() };
const stored = await (await caches.open("trc-u-u7-p0")).match(ORIGIN + C);
server.down = true; clock += 3600e3;
const off = await get(E, req(C));
const r2 = { status: off.status, cache: off.headers.get("x-trc-cache"), at: off.headers.get("x-trc-stored-at"), body: await off.json() };
const missing = await E.handleFetch(req("/api/v1/overview/plan/calendar?start=2027-01-04&end=2027-01-10"), ctx).then(() => "answered", (e) => e.name);
server.down = false; server.version = 2;
const back = await get(E, req(C));
const r3 = { cache: back.headers.get("x-trc-cache"), body: await back.json() };
server.down = true;
const again = await (await get(E, req(C))).json();
OUT = { r1, storedAt: stored.headers.get("x-trc-stored-at"), r2, missing, r3, again };
""")
    assert out["r1"] == {"status": 200, "cache": None, "body": {"who": "alice", "start": "2026-10-12", "end": "2026-10-18", "v": 1}}
    assert out["storedAt"] == "2026-10-13T08:00:00.000Z"
    # offline: the copy, marked with the time it was kept
    assert out["r2"] == {"status": 200, "cache": "offline", "at": "2026-10-13T08:00:00.000Z", "body": out["r1"]["body"]}
    assert out["missing"] == "TypeError"                       # nothing kept for those days: the network error stands
    # back online: the network's answer, and the copy refreshed with it
    assert out["r3"] == {"cache": None, "body": {"who": "alice", "start": "2026-10-12", "end": "2026-10-18", "v": 2}}
    assert out["again"]["v"] == 2


def test_a_slow_network_gets_the_copy_and_the_late_answer_refreshes_it():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.setUser("u7");
const S = "/api/v1/overview/status";
await get(E, req(S));
server.routes[S] = () => json({ who: "alice", today: "2026-10-15" });
server.delay = 200;
const r = await get(E, req(S));                 // get() waits for the late answer to be kept
server.delay = 0; server.down = true;
const after = await (await get(E, req(S))).json();
OUT = { cache: r.headers.get("x-trc-cache"), first: await r.json(), after };
""")
    assert out["cache"] == "slow" and out["first"]["today"] == "2026-10-14"
    assert out["after"]["today"] == "2026-10-15"


def test_a_server_error_with_a_copy_answers_from_the_copy():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.setUser("u7");
await get(E, req("/api/v1/overview/weekplan"));
server.routes["/api/v1/overview/weekplan"] = () => new Response("Bad gateway", { status: 502 });
const r = await get(E, req("/api/v1/overview/weekplan"));
server.routes["/api/v1/overview/z5"] = () => new Response("Bad gateway", { status: 502 });
const z = await get(E, req("/api/v1/overview/z5"));          // no copy: the 502 stands
OUT = { status: r.status, cache: r.headers.get("x-trc-cache"), who: (await r.json()).who, z5: z.status };
""")
    assert out == {"status": 200, "cache": "offline", "who": "alice", "z5": 502}


def test_pages_offline_the_copy_or_the_offline_page():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.setUser("u7");
await E.sync({});
server.down = true;
const p = await get(E, nav("/api/v1/overview/plan/schedule/page?day=2026-10-15"));
const other = await get(E, nav("/api/v1/wko5/settings"));               // never kept: the offline page instead
const login = E.handleFetch(nav("/login"), ctx);
OUT = { page: await body(p), pageCache: p.headers.get("x-trc-cache"), other: await body(other), login };
""")
    assert out["pageCache"] == "offline"
    assert "window.TRC_OFFLINE_PAGE" in out["page"] and "alice schedule" in out["page"]
    assert "window.TRC_OFFLINE_PAGE" in out["other"] and "alice offline" in out["other"]
    assert out["login"] is None


def test_nobody_known_nothing_kept():
    out = run(r"""
person("alice", 7);
const E = mk();
const r = await get(E, req("/api/v1/overview/status"));
OUT = { status: r.status, kept: caches.urls("trc-u-u7-p0"), names: await caches.keys() };
""")
    assert out["status"] == 200 and out["kept"] is None
    assert not [n for n in out["names"] if n.startswith("trc-u-")]


def test_sync_refills_p0_then_p1_then_p2_with_the_days_urls():
    out = run(r"""
person("alice", 7);
const E = mk();
const res = await E.sync({});
const p0 = caches.urls("trc-u-u7-p0"), p1 = caches.urls("trc-u-u7-p1"), p2 = caches.urls("trc-u-u7-p2");
const order = server.calls.map((c) => c.split(" ")[1]);
const lastP0 = Math.max(...p0.map((u) => order.indexOf(u))), firstP1 = Math.min(...p1.map((u) => order.indexOf(u)));
server.calls = [];
const again = await E.sync({});                       // nothing due: only who is signed in is asked
const callsAgain = server.calls.slice();
await caches.delete("trc-u-u7-p1");                   // the browser cleared a tier
server.calls = [];
const refill = await E.sync({});
const p1back = caches.urls("trc-u-u7-p1");
clock += 16 * 60e3; server.calls = [];
const later = await E.sync({});
server.calls = [];
const forced = await E.sync({ force: true });
OUT = { res, p0, p1, p2, firstIsSession: order[0], p0BeforeP1: lastP0 < firstP1, again, callsAgain, refill, p1back, later, forced: forced.done,
  status: await E.status() };
""")
    assert out["res"] == {"ok": True, "user": "u7", "done": ["p0", "p1", "p2"]}
    assert out["firstIsSession"] == "/api/v1/session"
    assert out["p0BeforeP1"] is True
    p0 = set(out["p0"])
    for u in ("/api/v1/overview/page", "/api/v1/overview/plan/schedule/page", "/api/v1/static/offline.html",
              "/api/v1/overview/status", "/api/v1/overview/weekplan", "/api/v1/sync/settings",
              "/api/v1/overview/summary?unit=week&n=16",
              "/api/v1/overview/plan/calendar?start=2026-10-12&end=2026-10-18",     # from the server's today (10-14)
              "/api/v1/overview/plan/calendar?start=2026-10-26&end=2026-11-01",
              "/api/v1/overview/pmc?begin=2026-07-16&end=2026-10-14"):
        assert u in p0, u
    assert set(out["p1"]) == {"/api/v1/wko5/activities/page", "/api/v1/wko5/activities",
                              "/api/v1/wko5/activities/stats", "/api/v1/achievements?include_hidden=true"}
    assert out["p2"] == []                                    # no API for FIT / chart data yet (SP-325)
    assert out["again"]["done"] == [] and out["callsAgain"] == ["GET /api/v1/session"]
    assert out["refill"]["done"] == ["p1"] and len(out["p1back"]) == 4
    assert out["later"]["done"] == ["p0"]                     # p0 every 15 min, p1 every 6 h, p2 daily
    assert out["forced"] == ["p0"]
    st = out["status"]
    assert st["user"] == "u7" and st["budget"] == 20 * 1024 * 1024 and st["bytes"]["p0"] > 0 and st["bytes"]["p1"] > 0


def test_another_person_never_sees_the_copy():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.sync({});
const aliceKept = caches.urls("trc-u-u7-p0").length;
person("bob", 8);                          // alice's session ended; bob signed in on this browser
const res = await E.sync({});
server.down = true;
const r = await get(E, req("/api/v1/overview/status"));
const p = await get(E, nav("/api/v1/overview/page"));
OUT = { aliceKept, res, names: (await caches.keys()).filter((n) => n.startsWith("trc-u-")), who: (await r.json()).who, page: await body(p) };
""")
    assert out["aliceKept"] > 10
    assert out["res"]["user"] == "u8"
    assert out["names"] and all(n.startswith("trc-u-u8-") for n in out["names"])
    assert out["who"] == "bob" and "bob overview" in out["page"] and "alice" not in out["page"]


def test_a_page_telling_another_person_clears_the_copy_at_once():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.sync({});
await E.setSession({ mode: "user", user: { id: 8 }, login: { required: true, signed_in: true } });
server.down = true;
const r = await E.handleFetch(req("/api/v1/overview/status"), ctx).then((x) => x.status, (e) => e.name);
OUT = { names: (await caches.keys()).filter((n) => n.startsWith("trc-u-")), r, user: (await E.status()).user };
""")
    assert out == {"names": [], "r": "TypeError", "user": "u8"}


@pytest.mark.parametrize("how", ["logout", "signed_out", "401", "redirect"])
def test_signing_out_deletes_the_copy(how):
    out = run(r"""
person("alice", 7);
const E = mk();
await E.sync({});
const HOW = process.argv[3];
if (HOW === "logout") await E.clearUser();
if (HOW === "signed_out") { person("alice", null); await E.sync({}); }
if (HOW === "401") { server.routes["/api/v1/overview/status"] = () => json({ code: "LOGIN_REQUIRED" }, 401); await get(E, req("/api/v1/overview/status")); }
if (HOW === "redirect") { server.routes["/api/v1/overview/page"] = () => ({ status: 0, type: "opaqueredirect", redirected: false, headers: new Headers(), clone() { return this; } }); await get(E, nav("/api/v1/overview/page")); }
server.down = true;
const r = await E.handleFetch(req("/api/v1/overview/weekplan"), ctx).then((x) => x.status, (e) => e.name);
const meta = await caches.match("/__trc/state", { cacheName: "trc-meta" });
OUT = { names: await caches.keys(), r, metaUser: meta ? (await meta.json()).user : "none" };
""".replace("process.argv[3]", json.dumps(how)))
    assert not [n for n in out["names"] if n.startswith("trc-u-")], out["names"]
    assert out["r"] == "TypeError"                            # offline: nothing of alice's answers
    assert out["metaUser"] is None


def test_writes_go_to_the_network_untouched_and_are_never_kept():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.setUser("u7");
server.routes["/api/v1/overview/plan/calendar"] = () => json({ ok: true });
const p = E.handleFetch(req("/api/v1/overview/plan/calendar", { method: "POST" }), ctx);
OUT = { p, kept: caches.urls("trc-u-u7-p0"), calls: server.calls };
""")
    assert out == {"p": None, "kept": None, "calls": []}


def test_over_the_budget_p2_then_p1_go_and_p0_stays():
    out = run(r"""
person("alice", 7);
const big = (kb) => () => new Response("x".repeat(kb * 1024), { headers: { "content-type": "application/json" } });
server.routes["/api/v1/wko5/activities"] = big(300);
server.routes["/api/v1/overview/status"] = big(300);
const E = mk({ budget: 500 * 1024 });
await E.setUser("u7");
await get(E, req("/api/v1/wko5/activities"));            // p1, 300 kB
await get(E, req("/api/v1/overview/status"));            // p0, 300 kB: over 500 kB, the p1 entry goes
OUT = { p0: caches.urls("trc-u-u7-p0"), p1: caches.urls("trc-u-u7-p1"), st: await E.status() };
""")
    assert out["p0"] == ["/api/v1/overview/status"] and out["p1"] == []
    assert out["st"]["bytes"]["p1"] == 0 and out["st"]["total"] <= 500 * 1024


def test_storage_full_pushes_out_less_important_tiers_then_retries():
    out = run(r"""
person("alice", 7);
const E = mk();
await E.setUser("u7");
await get(E, req("/api/v1/wko5/activities"));            // p1
let refused = 0;
caches.full = (url, cache) => { const p1 = caches.c.get("trc-u-u7-p1"); if (url.endsWith("/status") && p1 && p1.m.size) { refused++; return true; } return false; };
const r = await get(E, req("/api/v1/overview/status"));      // p0: full while p1 is there (p2 dropped first: nothing)
caches.full = (url) => url.endsWith("/api/v1/wko5/activities/stats");
await get(E, req("/api/v1/wko5/activities/stats"));          // p1 may not push out p0
OUT = { refused, status: r.status, p0: caches.urls("trc-u-u7-p0"), p1: caches.urls("trc-u-u7-p1") };
""")
    assert out == {"refused": 2, "status": 200, "p0": ["/api/v1/overview/status"], "p1": []}


def test_install_keeps_the_program_and_a_new_version_drops_the_old_one():
    len_app = run("OUT = O.APP_FILES.length;")
    out = run(r"""
for (const p of O.APP_FILES) server.routes[p] = () => new Response("// " + p, { headers: { "content-type": "text/javascript" } });
server.routes["/ajax/libs/echarts/5.5.0/echarts.min.js"] = () => new Response("// echarts");
const realFetch = fakeFetch;
const E1 = mk({ version: "v1" });
await E1.install();
const v1 = caches.urls("trc-app-v1").length;
person("alice", 7);
await E1.setUser("u7");
await get(E1, req("/api/v1/overview/status"));
const E2 = O.createEngine({ caches, fetch: fakeFetch, origin: ORIGIN, version: "v2", now: () => clock });
await E2.install();
const dropped = await E2.activate();
server.down = true;
const js = await get(E2, req("/api/v1/static/shell.js?v=v2"));
OUT = { v1, dropped, names: await caches.keys(), js: await body(js), jsCache: js.headers.get("x-trc-cache"),
  cdn: server.calls.filter((c) => c.includes("echarts")).length };
""")
    assert out["v1"] == len_app + 1                           # the local files and the chart library
    assert out["dropped"] == ["trc-app-v1"]
    assert "trc-app-v2" in out["names"] and "trc-u-u7-p0" in out["names"]     # the person's copy stays
    assert out["js"] == "// /api/v1/static/shell.js" and out["jsCache"] == "offline"
    assert out["cdn"] == 2                                    # asked once per version, with CORS


def test_the_chart_library_is_fetched_with_cors():
    out = run(r"""
const seen = [];
const E = O.createEngine({ caches, origin: ORIGIN, version: "v1", now: () => clock, timeouts: { app: 40 },
  fetch: async (r) => { seen.push({ url: typeof r === "string" ? r : r.url, mode: r.mode, cred: r.credentials });
    return new Response("// echarts", { headers: { "content-type": "text/javascript" } }); } });
const u = "https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js";
const r = await get(E, { url: u, method: "GET", mode: "no-cors" });
OUT = { seen, text: await body(r), kept: caches.urls("trc-app-v1") };
""")
    assert out["seen"] == [{"url": "https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js", "mode": "cors", "cred": "omit"}]
    assert out["text"] == "// echarts" and out["kept"] == ["https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js"]


def test_status_says_which_tiers_are_still_there():
    """設定 › 這台裝置的離線資料 (SP-322) reads status(): bytes per tier, when each was refilled, and
    whether its cache is still there (the browser may delete one; the bookkeeping alone can't tell)."""
    out = run(r"""
person("alice", 7);
const E = mk();
const before = await E.status();
await E.sync({});
const kept = await E.status();
await caches.delete("trc-u-u7-p1");                   // the browser cleared a tier
const cleared = await E.status();
await E.clearUser();
const gone = await E.status();
OUT = { before: before.present, kept: kept.present, keptTiers: Object.keys(kept.tiers), cleared: cleared.present, gone: gone.present,
  goneUser: gone.user };
""")
    assert out["before"] == {"p0": False, "p1": False, "p2": False}
    assert out["kept"] == {"p0": True, "p1": True, "p2": True} and out["keptTiers"] == ["p0", "p1", "p2"]
    assert out["cleared"] == {"p0": True, "p1": False, "p2": True}
    assert out["gone"] == {"p0": False, "p1": False, "p2": False} and out["goneUser"] is None
