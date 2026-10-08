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
const ctx = { DATA: JSON.parse(process.argv[2]) };
vm.runInNewContext(process.argv[1] + "\nOUT = (" + process.argv[3] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, _block(), json.dumps(data), expr], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
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
