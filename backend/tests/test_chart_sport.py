"""
SP-218: the single-activity charts tagged `"sports": ["trail"]` (爬坡與地形, 路線難度, 跑姿依坡度) follow
the activity itself — a trail run, hike or 百岳 day — not the athlete's 主要訓練項目. Season charts keep
the primary sport. Backend: sport_map.chart_sport and GET /workouts/{i}/kind (the user's 爬山 mark
counts); the viewer's chart-sport block runs under node. Synthetic data and a tmp tags DB only.
"""
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import activity_tags as AT
from backend.engine import sport_map as SM
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

PAGE = Path(__file__).resolve().parents[1] / "static" / "wko5_viewer.html"
VIEWS = Path(__file__).resolve().parents[2] / "views"
NODE = shutil.which("node")
T0 = dt.datetime(2026, 9, 28, 7, 0)


def test_chart_sport_of_each_kind():
    assert [SM.chart_sport(k) for k in ("trail", "hike", "baiyue")] == ["trail"] * 3
    assert SM.chart_sport("road") == "road"
    assert SM.chart_sport("bike") == "bike" and SM.chart_sport("walk") == "walk"    # no trail / road chart applies
    assert SM.chart_sport(None) is None and SM.chart_sport("") is None


def _ds():
    road = FakeWorkout(start=T0, sport="run", tags=["running"], sport_type="running",
                       channels={"elapsedtime": [0.0, 1.0]}, metrics={"duration": 1800.0})
    trail = FakeWorkout(start=T0 + dt.timedelta(days=1), sport="run", tags=["running", "runningtrail"],
                        sport_type="trail running", channels={"elapsedtime": [0.0, 1.0]}, metrics={"duration": 3600.0})
    return FakeDataset([road, trail], T0.date() + dt.timedelta(days=2))


def test_kind_endpoint_reads_the_activity_and_the_users_mark(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.api import wko5views as V
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _ds()
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    assert V.get_kind(0) == {"workout": 0, "kind": "road", "chart_sport": "road"}
    assert V.get_kind(1) == {"workout": 1, "kind": "trail", "chart_sport": "trail"}
    # the user marked the road run 爬山 (activity list): a mountain day — the trail charts show
    AT.upsert(db, start_local=AT.key_of(T0), activity_type="hike")
    assert V.get_kind(0) == {"workout": 0, "kind": "hike", "chart_sport": "trail"}
    AT.upsert(db, start_local=AT.key_of(T0), activity_type="baiyue_group")
    assert V.get_kind(0)["chart_sport"] == "trail"
    with pytest.raises(HTTPException):
        V.get_kind(5)


def test_the_trail_charts_are_the_tagged_single_activity_ones():
    v = json.loads((VIEWS / "workout.json").read_text("utf-8"))
    tagged = {c["id"] for d in v["dashboards"] for c in d["charts"] if c.get("sports")}
    assert tagged == {"route-difficulty", "climbs", "grades", "form-grades"}
    assert all(c["sports"] == ["trail"] for d in v["dashboards"] for c in d["charts"] if c.get("sports"))


def _block() -> str:
    m = re.search(r"// ---- chart sport \(SP-218\) ----.*?// ---- end chart sport ----", PAGE.read_text(encoding="utf-8"), re.S)
    assert m, "chart sport block not found"
    return m.group(0)


def _run(expr: str, **data):
    js = r"""
const vm = require("vm");
const DATA = JSON.parse(process.argv[2]);
const ctx = { DATA, S: DATA.S || {} };
vm.runInNewContext(process.argv[1] + "\nOUT = (" + process.argv[3] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, _block(), json.dumps(data), expr], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_viewer_reads_the_tag_against_the_activity_on_workout_pages():
    # primary sport road, a trail activity: the 爬坡與地形 charts show (the bug of SP-218: they vanished)
    assert _run("chartSportFor('workout', 'road', 'trail')") == "trail"
    assert _run("chartSportFor('workout', 'trail', 'road')") == "road"
    assert _run("chartSportFor('workout', 'road', null)") is None          # not known yet: no filter
    assert _run("chartSportFor('season', 'road', 'trail')") == "road"      # season pages: the primary sport
    assert _run("chartSportFor('season', null, null)") == "trail"
    climbing = {"charts": [{"index": 0, "id": "climbs", "sports": ["trail"]}, {"index": 1, "id": "grades", "sports": ["trail"]}]}
    ids = "visChartsFor(DATA.d, DATA.sp, true).map((c) => c.id)"
    assert _run(ids, d=climbing, sp="trail") == ["climbs", "grades"]
    assert _run(ids, d=climbing, sp="road") == []
    assert _run(ids, d=climbing, sp=None) == ["climbs", "grades"]
    mixed = {"charts": [{"id": "a"}, {"id": "p", "power": True}, {"id": "r", "sports": ["road"], "order": {"road": -1}},
                        {"id": "n", "needs": "poles", "needs_met": False}]}
    assert _run(ids, d=mixed, sp="road") == ["r", "a", "p"]                  # `order` moves it first in that mode
    assert _run("visChartsFor(DATA.d, 'trail', false).map((c) => c.id)", d=mixed) == ["a"]


# ---- SP-218 review: M1 (no wait on /kind), M2 (the picked page stays) ---------------------------

CLIMBING = {"_mode": "workout", "charts": [{"index": 0, "id": "climbs", "sports": ["trail"]},
                                           {"index": 1, "id": "grades", "sports": ["trail"]}]}


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_viewer_kind_rule_matches_the_backend():
    kinds = list(SM.FILTER_KINDS) + [None, ""]
    got = _run("DATA.kinds.map((k) => chartSportOfKind(k))", kinds=kinds)
    assert got == [SM.chart_sport(k) for k in kinds]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_kind_comes_from_the_activity_list_without_a_request():
    acts = [{"index": 3, "kind": "baiyue"}, {"index": 4, "kind": "road"}]
    assert _run("[settleActSport(), S.actSport]", S={"workout": 3, "acts": acts}) == \
        [True, {"workout": 3, "sport": "trail", "kind": "baiyue"}]
    assert _run("[settleActSport(), S.actSport]", S={"workout": 4, "acts": acts})[1]["sport"] == "road"
    # outside the list (a deep link beyond the date range): false = ask /kind in the background
    assert _run("[settleActSport(), S.actSport || null]", S={"workout": 9, "acts": acts}) == [False, None]
    # an older answer for another index is not reused
    assert _run("[settleActSport(), S.actSport.workout]",
                S={"workout": 4, "acts": acts, "actSport": {"workout": 3, "sport": "trail"}}) == [True, 4]


def _load_fn() -> str:
    html = PAGE.read_text(encoding="utf-8")
    return html[html.index("async function load() {"):html.index("// ---- one chart card")]


def test_load_draws_before_any_wait():
    """M1: an activity's page draws its loading cards at once; /kind (only for an activity outside the list)
    runs in the background — nothing in load() awaits before the cards are on the page."""
    body = _load_fn()
    head = body[:body.index("const queue = visCharts(d)")]
    assert "await" not in head and "settleActSport()" in head and "/kind?" in head
    # an exclusion change rebuilds the dataset and shifts the indices: the cached kind is dropped with them
    assert "S.workout = null; S.workoutLabel = null; S.actSport = null; loadActs();" in PAGE.read_text(encoding="utf-8")


def test_tree_keeps_the_picked_page_for_another_activity():
    """M2: the redirect to the first page with charts reads pageCharts (the page's charts whatever the activity),
    so a road run in between never moves / saves the picked 爬坡與地形 away; that page says viewer.trail_only."""
    html = PAGE.read_text(encoding="utf-8")
    tree = html[html.index("function renderTree() {"):html.index("function markHit(")]
    assert "!pageCharts(v.dashboards[S.dash[S.mode]]).length" in tree and "visCharts(v.dashboards[S.dash" not in tree
    assert 'esc(t("viewer.trail_only"))' in _load_fn()


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_charts_ignore_the_activity_but_shown_charts_follow_it():
    S = {"workout": 4, "actSport": {"workout": 4, "sport": "road"}, "usePower": True, "sport": "trail"}
    ids = "(f) => f(DATA.d).map((c) => c.id)"
    assert _run(f"({ids})(visCharts)", S=S, d=CLIMBING) == []                         # -> the trail_only message
    assert _run(f"({ids})(pageCharts)", S=S, d=CLIMBING) == ["climbs", "grades"]       # -> the page stays picked
    season = {**CLIMBING, "_mode": "season"}
    assert _run(f"({ids})(pageCharts)", S={**S, "sport": "road"}, d=season) == []      # season: primary sport
