"""
SP-576 (跟團 G0): a 疊石 Cairn trip with a timetable — the group's total moving time fills the race
calculator's 「目標時間」; the calculator's own numbers don't change (research SP-575, Q1 answers).

  * the timetable: every visit with a day and a time, on OUR track (the POI matched to every pass);
  * the group's moving time: each leg's clock minus the stop written in Cairn (Cairn's chain: a row's
    stop is added before leaving it, not at the day's first row), or × the past group days' moving
    ratio on a day without any stop written (80 % without records, 推估);
  * the page fills the field only; the mode is the user's.

The fake Cairn of test_cairn_import_sp537 (127.0.0.1); a synthetic 3-day 嘉明湖 timetable.
"""
import json
from pathlib import Path

import pytest

from backend.engine import cairn as CA
from backend.engine.racepower import group as GR
from backend.engine.racepower import gpx as GPX
from backend.tests.test_cairn_import_sp537 import (GID, TOKEN, calc_client, cairn, line_track, plan_client,  # noqa: F401
                                                   poi, store)

ROOT = Path(__file__).resolve().parents[2]


def tv(i, day, kind, eta, rest=0, src="user", dist_m=None):
    return {"id": f"t{i}", "day": day, "eta": eta, "kind": kind, "distanceM": dist_m, "isSegment": False,
            "restMinutes": rest, "etaSource": src}


# 嘉明湖 3 days on the 30 km line: day 1 0 → 9.5 km, day 2 9.5 → 21, day 3 21 → 30
GROUP = [
    poi("g0", "向陽登山口", 0.0, [tv(0, 1, "depart", "07:00")]),
    poi("g1", "向陽森林步道口", 4.0, [tv(1, 1, "pass", "09:00")]),
    poi("g2", "向陽山屋", 9.5, [tv(2, 1, "arrive", "12:30"), tv(3, 2, "depart", "05:00")]),
    poi("g3", "水池", 15.0, [tv(4, 2, "pass", "08:30", rest=20)]),
    poi("g4", "嘉明湖避難山屋", 21.0, [tv(5, 2, "arrive", "12:00"), tv(6, 3, "depart", "06:00")]),
    poi("g5", "終點", 30.0, [tv(7, 3, "arrive", "11:30")]),
]
H = 3600.0
# day 1 and 3: no stop written → × 0.8; day 2: 水池's 20 min off its next leg
MOVING = (2 * 0.8 + 3.5 * 0.8) * H + (3.5 * H + (3.5 * H - 20 * 60)) + 5.5 * 0.8 * H


def test_the_timetable_is_read_on_our_track():
    tr = line_track()
    s = CA.schedule_of(GROUP, {"distance_m": 30000}, tr)
    pts = s["points"]
    assert [(p["name"], p["day"], p["eta"]) for p in pts][:3] == [("向陽登山口", 1, "07:00"), ("向陽森林步道口", 1, "09:00"),
                                                                 ("向陽山屋", 1, "12:30")]
    assert [round(p["km"], 1) for p in pts] == [0.0, 4.0, 9.5, 9.5, 15.0, 21.0, 21.0, 30.0]
    assert pts[4]["rest_min"] == 20 and all(p["src"] == "user" for p in pts)
    assert s["skipped"] == []
    # one time a day (a start only) or none: no timetable — the import stays the route
    assert CA.schedule_of([poi("a", "x", 0, [tv(0, 1, "depart", "07:00")]), poi("b", "y", 9, [tv(1, 2, "depart", "06:00")])],
                          {}, tr) is None
    assert CA.schedule_of([poi("a", "x", 0, [tv(0, 1, "depart", None)])], {}, tr) is None


def test_the_group_moving_time_takes_the_stops_off():
    pts = CA.schedule_of(GROUP, {}, line_track())["points"]
    lg = GR.legs(pts, 0.8)
    assert lg["moving_s"] == pytest.approx(MOVING)
    assert lg["clock_s"] == pytest.approx((5.5 + 7.0 + 5.5) * H)
    by = {(x["from"], x["to"]): x for x in lg["legs"]}
    assert by[("向陽山屋", "水池")]["rest_s"] == 0                    # the day's first row: its time is the leaving time
    assert by[("水池", "嘉明湖避難山屋")]["moving_s"] == pytest.approx(3.5 * H - 1200)
    assert by[("向陽登山口", "向陽森林步道口")]["rest"] == "ratio"
    assert [d["start"] for d in lg["days"]] == ["07:00", "05:00", "06:00"]
    # a leg under 45 min (a timetable's rounding) is merged with the next one
    short = [dict(p) for p in pts]
    short.insert(1, {**short[0], "name": "岔路", "eta": "07:30", "km": 1.0})
    lg2 = GR.legs(short, 0.8)
    assert lg2["legs"][0]["from"] == "向陽登山口" and lg2["legs"][0]["to"] == "向陽森林步道口" and lg2["legs"][0]["n"] == 2
    assert lg2["moving_s"] == pytest.approx(MOVING)


def test_the_ratio_is_the_past_group_days_else_80():
    assert GR.moving_ratio([]) == {"ratio": 0.8, "n": None}
    rows = [{"moving_s": m, "elapsed_s": 10000} for m in (6000, 7000, 7500)]
    assert GR.moving_ratio(rows) == {"ratio": 0.7, "n": 3}


def test_the_calculator_gives_the_group_moving_time(cairn, calc_client, plan_client):  # noqa: F811
    # SP-699: imported into the race (賽事管理); the calculator reads it with the race's GPX
    cairn.pois = GROUP
    r = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link})
    assert r.status_code == 200, r.text
    assert TOKEN not in r.text
    j = plan_client.post("/api/v1/racepower/course/event/jm", json={}).json()
    sc = j["schedule"]
    assert sc["prefill"] is True and sc["cover"] == pytest.approx(1.0, abs=0.01)
    assert sc["moving_s"] == pytest.approx(MOVING, rel=1e-6) and sc["ratio"] == 0.8 and sc["ratio_n"] is None
    assert sc["ratio_used"] is True and sc["rest_s"] == pytest.approx(1200)
    # the target-time mode with it: the existing λ and effort bar; the auto plan is the same as before
    base = {"type": "baiyue", "course": {"course_id": j["course_id"]}, "start_time": "07:00", "days": 3,
            "day_splits_km": j["day_splits_km"]}
    auto = plan_client.post("/api/v1/racepower/plan", json=base).json()
    tm = plan_client.post("/api/v1/racepower/plan", json={**base, "mode": "time", "target_time_s": sc["moving_s"]}).json()
    assert tm["summary"]["time_s"] == pytest.approx(MOVING, rel=1e-6)
    assert tm["effort"]["r"] == pytest.approx(auto["summary"]["time_s"] / MOVING, rel=1e-6)
    assert tm["effort"]["label"]


def test_a_partial_timetable_does_not_fill_the_target(cairn, plan_client):  # noqa: F811
    cairn.pois = GROUP[:3]                                                # day 1 only: 9.5 of 30 km
    sc = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link}).json()["cairn"]["schedule"]
    assert sc["prefill"] is False and sc["km"] == pytest.approx(9.5, abs=0.05)


def test_the_event_import_gives_it_too(cairn, plan_client):  # noqa: F811
    cairn.pois = GROUP
    j = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link}).json()
    assert j["cairn"]["schedule"]["moving_s"] == pytest.approx(MOVING, rel=1e-6)


def test_the_page_fills_the_field_only():
    html = (ROOT / "backend/static/racepower.html").read_text("utf-8")
    fn = html[html.index("function schedPrefill"):html.index("function wireSchedGo")]
    assert '$("target").value = hms(sc.moving_s)' in fn and "S.mode" not in fn       # the mode is the user's
    go = html[html.index("function wireSchedGo"):html.index("function courseOpts")]
    assert 'S.mode = "time"' in go                                                     # one click shows the effort
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / f"backend/static/i18n/{loc}/racepower.json").read_text("utf-8"))
        assert all(cat[f"cairn.{k}"] for k in ("sched_line", "sched_rest", "sched_ratio", "ratio_yours",
                                                "ratio_default", "sched_go", "sched_partial", "sched_mixed"))
