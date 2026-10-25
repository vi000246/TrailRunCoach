"""
離線看看過的活動 (SP-758): the service worker keeps 單次活動 (the viewer page, what it reads, and each
opened activity's charts / cards / samples …) in p2 — only the activities of the last 30 days that were
opened, each answer under the activity the server says it is about (X-TRC-Activity), never a drawing still
being computed, never one activity's answer for another's; over the budget the oldest activity goes first.
Plus: the server's header and `act_key`, the viewer's check, and 設定's P2 line. Synthetic only.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import Response

from backend.api import wko5views
from backend.tests import test_device_storage_sp322 as DSTEST
from backend.tests.test_offline_pwa import run
from backend.tests.test_workout_samples import fake_ds  # noqa: F401 — the fixture

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
VIEWER = Path(__file__).resolve().parents[1] / "static" / "charts.html"

# the clock of test_offline_pwa's engine is 2026-10-13: 10-10 is within 30 days, 09-01 is not
ROUTES = r"""
person("alice", 7);
const ACTS = { 3: "2026-10-10T07:00", 4: "2026-10-11T06:30", 5: "2026-09-01T07:00" };
const actJson = (v, i, extra = {}) => new Response(JSON.stringify(v), { headers: { "content-type": "application/json",
  ...(i in ACTS ? { "x-trc-activity": ACTS[i] } : {}), ...extra } });
const CH = "/api/v1/app/views/v/dashboards/0/charts/1";
server.routes[CH] = (u) => { const i = +u.searchParams.get("workout");
  return actJson({ who: "alice", workout: i, act_key: ACTS[i], ...(server.stale ? { stale: true } : {}) }, i); };
for (const i of [3, 4, 5, 6]) server.routes[`/api/v1/app/workouts/${i}/samples`] = () => actJson({ workout: i, t: [1, 2] }, i);
server.routes["/api/v1/app/viewer"] = () => html("<!doctype html><html><head></head><body>alice viewer</body></html>");
for (const p of ["/api/v1/app/views", "/api/v1/app/workouts", "/api/v1/app/config"]) server.routes[p] = (u) => json({ who: "alice", path: u.pathname });
const E = mk();
await E.setUser("u7");
const chart = (i, stale = true) => `${CH}?workout=${i}&parity=false${stale ? "&stale=1" : ""}`;
const p2 = () => (caches.urls("trc-u-u7-p2") || []).slice().sort();
"""


def test_what_single_activity_keeps_and_where():
    out = run(r"""
const c = (p, nav) => { const x = O.classify(p.startsWith("http") ? p : ORIGIN + p, ORIGIN, !!nav); return x ? x.kind + ":" + x.tier + (x.act ? ":act" : "") : null; };
OUT = {
  page: c("/api/v1/app/viewer", true), views: c("/api/v1/app/views"), list: c("/api/v1/app/workouts?begin=2026-09-01&end=2026-10-13"),
  meta: c("/api/v1/app/injuries/meta"), chart: c("/api/v1/app/views/單次活動判讀/dashboards/0/charts/1?workout=3&stale=1"),
  season: c("/api/v1/app/views/x/dashboards/0/charts/1?begin=2026-01-01&end=2026-10-13"),
  samples: c("/api/v1/app/workouts/3/samples"), kind: c("/api/v1/app/workouts/3/kind?parity=false"),
  pain: c("/api/v1/app/workouts/3/pain"), zs: c("/api/v1/app/workouts/3/zone-sets"), seg: c("/api/v1/app/workouts/3/segments"),
  patch: c("/api/v1/app/workouts/3/activity"), review: c("/api/v1/app/workouts/3/review"),
  echarts: c("https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"),
  leaflet: c("https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"),
  key: O.activityKey(ORIGIN + "/api/v1/app/views/v/dashboards/0/charts/1?workout=3&parity=false&stale=1").replace(ORIGIN, ""),
  keeps: [O.keepsActivity("2026-10-10T07:00", "2026-10-13"), O.keepsActivity("2026-09-13T07:00", "2026-10-13"),
    O.keepsActivity("2026-09-12T23:59", "2026-10-13"), O.keepsActivity("2026-10-20T07:00", "2026-10-13"),
    O.keepsActivity(null, "2026-10-13"), O.keepsActivity("10/10", "2026-10-13")],
  installs: O.CDN.length,
};
""")
    assert out["page"] == "page:p2"
    assert out["views"] == out["list"] == out["meta"] == "api:p2"
    for k in ("chart", "samples", "kind", "pain", "zs", "seg"):
        assert out[k] == "api:p2:act", k
    assert out["season"] is None and out["patch"] is None and out["review"] is None
    assert out["echarts"] == out["leaflet"] == "app:p0"
    assert out["installs"] == 1                               # kept on use, not fetched at install for everyone
    assert out["key"] == "/api/v1/app/views/v/dashboards/0/charts/1?workout=3&parity=false"
    assert out["keeps"] == [True, True, False, False, False, False]


def test_an_opened_activity_is_there_offline_and_never_answers_for_another():
    out = run(ROUTES + r"""
await get(E, nav("/api/v1/app/viewer"));
await get(E, req("/api/v1/app/views"));
await get(E, req("/api/v1/app/workouts?begin=2026-09-14&end=2026-10-13"));
const online = await (await get(E, req(chart(3)))).json();
await get(E, req("/api/v1/app/workouts/3/samples"));
const kept = p2();
server.down = true;
const page = await body(await get(E, nav("/api/v1/app/viewer")));
const r = await get(E, req(chart(3)));
const again = await get(E, req(chart(3, false)));            // asked again without stale (the card's retry)
const samples = await (await get(E, req("/api/v1/app/workouts/3/samples"))).json();
const other = await E.handleFetch(req(chart(4)), ctx).then(() => "answered", (e) => e.name);
const otherSamples = await E.handleFetch(req("/api/v1/app/workouts/4/samples"), ctx).then(() => "answered", (e) => e.name);
const list = await (await get(E, req("/api/v1/app/workouts?begin=2026-09-20&end=2026-10-13"))).json();
OUT = { online, kept, page, r: { cache: r.headers.get("x-trc-cache"), body: await r.json() }, again: (await again.json()).workout,
  samples, other, otherSamples, list, st: await E.status() };
""")
    assert out["online"]["act_key"] == "2026-10-10T07:00"
    assert "/api/v1/app/views/v/dashboards/0/charts/1?workout=3&parity=false" in out["kept"]       # without stale
    assert "/api/v1/app/viewer" in out["kept"] and "/api/v1/app/workouts/3/samples" in out["kept"]
    assert "alice viewer" in out["page"] and "TRC_OFFLINE_PAGE" in out["page"]
    assert out["r"]["cache"] == "offline" and out["r"]["body"]["workout"] == 3
    assert out["again"] == 3 and out["samples"]["workout"] == 3
    assert out["other"] == "TypeError" and out["otherSamples"] == "TypeError"   # never activity 3's answer
    assert out["list"]["who"] == "alice"                      # the list: the copy covering the days asked
    assert out["st"]["acts"] == 1 and out["st"]["bytes"]["p2"] > 0


def test_only_the_last_30_days_and_only_finished_drawings_are_kept():
    out = run(ROUTES + r"""
await get(E, req(chart(5)));                                     // 2026-09-01: older than 30 days
await get(E, req("/api/v1/app/workouts/6/samples"));             // no X-TRC-Activity: not kept
server.stale = true;
await get(E, req(chart(4)));                                     // the previous drawing, 更新中
server.stale = false;
server.routes["/api/v1/app/workouts/3/kind"] = () => actJson({ pending: { message: "back-filling" } }, 3);
await get(E, req("/api/v1/app/workouts/3/kind"));
await get(E, req(chart(3)));
OUT = { kept: p2() };
""")
    assert out["kept"] == ["/api/v1/app/views/v/dashboards/0/charts/1?workout=3&parity=false"]


def test_the_free_tier_keeps_no_activity():
    out = run(ROUTES + r"""
server.routes["/api/v1/session"] = () => json({ mode: "user", user: { id: 7, name: "alice" }, login: { required: true, signed_in: true },
  membership: { scopes: ["charts:5"] } });
await E.sync({});
await get(E, nav("/api/v1/app/viewer"));
await get(E, req(chart(3)));
OUT = { p2: caches.urls("trc-u-u7-p2"), st: await E.status() };
""")
    assert not out["p2"] and out["st"]["acts"] == 0


def test_over_the_budget_the_oldest_activity_goes_first_and_old_ones_expire():
    plan = run(r"""
const MB = 1024 * 1024;
const e = (key, tier, mb, at, day) => ({ key, tier, kind: "api", bytes: mb * MB, at, ...(day ? { day } : {}) });
// the page and what it reads (no day) last; among activities the oldest day first, whenever it was opened
const all = [e("page", "p2", 2, 1), e("new", "p2", 4, 1, "2026-10-12"), e("old", "p2", 4, 9, "2026-09-20"),
  e("mid", "p2", 4, 5, "2026-10-01"), e("list", "p1", 4, 1)];
OUT = [O.planEviction(all, 13 * MB).drop.map((x) => x.key), O.planEviction(all, 3 * MB).drop.map((x) => x.key)];
""")
    # 18 MB: to 13 MB the two oldest activities go; to 3 MB every activity, then the page, then p1
    assert plan == [["old", "mid"], ["old", "mid", "new", "page", "list"]]
    out = run(ROUTES + r"""
await E.sync({});
await get(E, req(chart(3)));                                     // 2026-10-10
await get(E, req(chart(4)));                                     // 2026-10-11
const before = p2().length;
clock += 28 * 864e5;                                             // 2026-11-10: kept from 10-11 on, 10-10 goes
await E.sync({});
OUT = { before, after: p2(), st: await E.status() };
""")
    assert out["before"] == 2
    assert out["after"] == ["/api/v1/app/views/v/dashboards/0/charts/1?workout=4&parity=false"]
    assert out["st"]["acts"] == 1


# ---------------------------------------------------------------------------- the server

def test_the_server_names_the_activity_of_an_answer(fake_ds):  # noqa: F811
    r = Response()
    out = wko5views.workout_samples(0, response=r)
    assert out["workout"] == 0
    assert r.headers[wko5views.ACT_HEADER] == "2026-09-30T07:00"
    assert wko5views.workout_samples(1)["workout"] == 1        # called without a response (scripts, tests)
    k = Response()
    assert wko5views.act_header(k, fake_ds.workouts[1]) == "2026-09-30T07:00" and k.headers["x-trc-activity"]
    assert wko5views.act_header(None, fake_ds.workouts[0]) == "2026-09-30T07:00"


# ---------------------------------------------------------------------------- the viewer and 設定

def _act_block() -> str:
    m = re.search(r"// ---- activity check \(SP-758\) ----.*?// ---- end activity check ----",
                  VIEWER.read_text(encoding="utf-8"), re.S)
    assert m, "activity check block not found"
    return m.group(0)


@needs_node
def test_the_viewer_never_draws_a_chart_of_another_activity():
    js = _act_block() + r"""
const K = [12, "2026-10-10T07:00"];
console.log(JSON.stringify([
  actMismatch({ act_key: "2026-10-10T07:00" }, 12, K), actMismatch({ act_key: "2026-10-09T06:00" }, 12, K),
  actMismatch({ act_key: "2026-10-09T06:00" }, 13, K), actMismatch({}, 12, K), actMismatch({ act_key: "x" }, 12, null),
  actMismatch(null, 12, K)]));
"""
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    # same activity: drawn; another one: refused; the key known for another index, none, or no act_key: drawn
    assert json.loads(r.stdout) == [False, True, False, False, False, False]
    page = VIEWER.read_text(encoding="utf-8")
    assert 'actMismatch(res, S.workout, S.workoutKey)' in page and 't("viewer.act_mismatch")' in page
    assert "workoutKey: S.workoutKey" in page                  # remembered with the shown activity
    for lang in ("zh-TW", "en"):
        cat = json.loads((VIEWER.parent / "i18n" / lang / "viewer.json").read_text("utf-8"))
        assert cat.get("act_mismatch"), lang


@needs_node
def test_settings_shows_how_many_activities_p2_keeps():
    s = DSTEST._status(bytes={"app": 1, "p0": 1, "p1": 1, "p2": int(2.4 * DSTEST.MB)}, acts=3)
    out = DSTEST.run(f"""
const m = D.view(await D.read({{ storage: storage({{}}), offline: offline({json.dumps(s)}) }}), {{}});
OUT = {{ p2: m.tiers[2], html: D.html(m, t) }};
""")
    assert out["p2"]["state"] == "acts" and out["p2"]["n"] == 3 and out["p2"]["mb"] == "2.4"
    assert "已存 3 筆活動" in out["html"] and "2.4 MB" in out["html"] and "近 30 天打開過的單次活動" in out["html"]
