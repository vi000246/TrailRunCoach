"""
Target-race reference lines on 「每次路線難度（コース定数）」 (engine/panels/race_refs.py).
Synthetic plans and a fake race calculator; nothing reads the athlete's data.
"""
import datetime as dt
import json
from pathlib import Path

import pytest

from backend.engine.algorithms import chart_metrics as CM
from backend.engine.panels import race_refs as RR
from backend.engine.planning import Event, Plan
from backend.engine.wko5expr.customviews import CustomViewError, parse_view

TODAY = dt.date(2026, 10, 2)


def plan(*evs):
    return Plan(events=list(evs))


A = Event("a", "大霸尖山", "2026-11-28", kind="race", priority="A", distance_km=40, climbing_m=2400, est_hours=9)
B = Event("b", "鳶嘴稍來", "2026-10-25", kind="race", priority="B", distance_km=20, climbing_m=1500)
C = Event("c", "練跑", "2026-10-11", kind="race", priority="C", distance_km=15, climbing_m=800, est_hours=2)


def fake(hours):
    return lambda e, c=None: hours.get(e.id)


def chart(series=None):
    pts = {"kind": "points", "x": "datetime", "points": [["2026-09-20T07:00:00", 20.0]]}
    return {"title": "每次路線難度（コース定数）", "description": "原本的說明。", "series": series if series is not None else [
        {"name": "越野跑", "y_axis": "NONE", "unit": {"id": "NONE"}, "x_unit": {"id": "DATE"}, "data": pts},
        {"name": "20 一般", "y_axis": "NONE", "unit": {"id": "NONE"}, "data": {"kind": "hline", "y": 20}}]}


def test_formula_is_the_charts_own():
    # the chart expression and the racepower walking model use the same Yamamoto formula
    from backend.engine.racepower.predict import yamamoto_cc
    assert CM.course_constant(6, 12, 1000, 1000) == pytest.approx(yamamoto_cc(6, 12, 1000, 1000))
    ln = RR.race_line(A, [8.0], "x")
    assert ln["cc"] == pytest.approx(round(1.8 * 8 + 0.3 * 40 + 10 * 2.4 + 0.6 * 2.4, 1))
    assert ln["descent_m"] == 2400 and ln["descent_assumed"]


def test_calculator_time_first_then_the_plans_estimate():
    r = RR.course_constant_refs(plan(A, B), TODAY, fake({"a": [8.0]}))
    names = [x["name"] for x in r["lines"]]
    assert names == ["大霸尖山"]                      # B has neither a prediction nor est_hours
    assert r["lines"][0]["time_source"].startswith("賽事計算器")
    r = RR.course_constant_refs(plan(A, B), TODAY, fake({"b": [3.5]}))
    a = next(x for x in r["lines"] if x["priority"] == "A")
    assert a["hours"] == 9 and a["time_source"] == "賽季計畫的預估移動時間"
    assert [x["priority"] for x in r["lines"]] == ["B", "A"]           # by date
    assert r["target"]["event_id"] == "b"                                # the nearest race
    assert a["goal"] == a["cc"] and r["band"] is a                      # single day: its constant


def test_the_next_two_races_any_grade_nearest_first():
    # owner 2026-10-02: two lines only, any grade (C counts), past races don't
    past = Event("p", "已跑完", "2026-09-01", kind="race", priority="A", distance_km=10, est_hours=1)
    r = RR.course_constant_refs(plan(A, B, C, past), TODAY, fake({"a": [8.0], "b": [3.0]}))
    assert [x["event_id"] for x in r["lines"]] == ["c", "b"]            # 10-11, 10-25; A (11-28) is third
    assert r["target"]["event_id"] == "c" and r["band"] is None          # no A among the two: no band


def test_multi_day_is_the_whole_trip_with_the_hardest_day_in_the_breakdown():
    # one number for the trip (信州 grading: the whole route's constant), per day in the hover
    hike = Event("h", "南湖大山", "2026-12-10", kind="baiyue", priority="A", days=3, distance_km=36, climbing_m=3000)
    r = RR.course_constant_refs(plan(hike), TODAY, fake({"h": [5.0, 8.0, 4.0]}))
    ln = r["lines"][0]
    assert ln["days"] == 3 and ln["hours"] == 17.0
    assert ln["cc"] == pytest.approx(round(CM.course_constant(17.0, 36, 3000, 3000), 1))
    assert ln["day_max"] == pytest.approx(round(CM.course_constant(8.0, 12, 1000, 1000), 1)) and ln["hardest_day"] == 2


def test_apply_draws_two_graded_lines_and_the_a_band():
    # owner 2026-10-02: the next two races, grade in the label, A red / B orange / other blue,
    # the 80–100 % band only under the A race
    res = RR.apply(chart(), plan(A, B), TODAY, fake({"a": [8.0], "b": [3.0]}))
    refs = [s for s in res["series"] if s.get("role") == "race_ref"]
    lines = [s for s in refs if s["data"]["kind"] == "hline"]
    assert len(lines) == 2
    b, a = lines                                                          # nearest first
    tg, band_ln = res["race_ref"]["target"], res["race_ref"]["band"]
    assert tg["event_id"] == "b" and band_ln["event_id"] == "a"
    cc = band_ln["cc"]
    assert a["name"] == f"A 大霸尖山 {cc:.0f}" and a["data"]["y"] == cc and a["color"] == RR.COLOR["A"]
    assert b["name"] == f"B 鳶嘴稍來 {tg['cc']:.0f}" and b["color"] == RR.COLOR["B"]
    assert a["line_style"] == "dash" and a["label_end"]
    assert a["unit"] == {"id": "NONE"} and a["y_axis"] == "NONE"
    assert "コース定數" in a["tip"] and "鳶嘴稍來" not in a["tip"]
    band = [s for s in refs if s["data"]["kind"] == "band"]
    assert len(band) == 1 and band[0]["data"]["range"] == [round(0.8 * cc, 1), cc]
    assert band[0]["color"] == RR.COLOR["A"] and not band[0].get("label_end")
    assert tg["goal"] == tg["cc"]                                         # the point hover reads the nearest
    # the ?: a short guide, the formula on its last line
    g = res["description"].split("\n")
    assert len(g) == 5 and g[0].startswith("虛線＝接下來 2 場賽事") and "山本正嘉" in g[-1]
    assert "推估" in g[2] and "80–100%" in g[2] and g[2].startswith("大霸尖山")


def test_other_grades_are_blue_and_without_an_a_race_no_band():
    res = RR.apply(chart(), plan(C, B), TODAY, fake({"b": [3.0]}))
    lines = [s for s in res["series"] if s.get("role") == "race_ref" and s["data"]["kind"] == "hline"]
    assert [s["name"].split()[0] for s in lines] == ["C", "B"]
    assert lines[0]["color"] == RR.COLOR_OTHER
    assert not any(s["data"]["kind"] == "band" for s in res["series"] if s.get("role") == "race_ref")
    assert len(res["description"].split("\n")) == 4 and "80–100%" not in res["description"]


def test_multi_day_trip_line_is_its_per_day_average():
    hike = Event("h", "南湖大山", "2026-12-10", kind="baiyue", priority="A", days=3, distance_km=36, climbing_m=3000)
    res = RR.apply(chart(), plan(hike), TODAY, fake({"h": [5.0, 8.0, 4.0]}))
    lines = [s for s in res["series"] if s.get("role") == "race_ref" and s["data"]["kind"] == "hline"]
    tg = res["race_ref"]["target"]
    assert len(lines) == 1 and lines[0]["data"]["y"] == tg["day_mean"] == tg["goal"]
    assert lines[0]["name"] == f"A 南湖大山 每天 {tg['day_mean']:.0f}"
    assert f"整趟 3 天コース定數 {tg['cc']:.0f}" in lines[0]["tip"] and "（最難）" in lines[0]["tip"]


def test_no_races_leaves_the_chart_and_says_why():
    before = chart()
    past = Event("p", "已跑完", "2026-09-01", kind="race", priority="A", distance_km=10, est_hours=1)
    res = RR.apply(before, plan(B, past), TODAY, fake({}))               # B: no time to compute it
    assert res["series"] == before["series"] and res["race_ref"]["target"] is None
    assert RR.NOTE_NONE in res["description"]
    only_b = RR.apply(chart(), plan(B), TODAY, fake({"b": [3.0]}))
    assert only_b["race_ref"]["target"]["event_id"] == "b"
    assert only_b["description"].startswith("虛線＝接下來 1 場賽事")


def test_the_view_turns_it_on_and_validates_it():
    root = Path(__file__).resolve().parents[2] / "views" / "training.json"
    v = parse_view(json.loads(root.read_text("utf-8")), root)
    ch = [c for d in v["dashboards"] for c in d["charts"] if c["title"] == "每次路線難度（コース定数）"]
    assert ch and ch[0]["race_refs"] == "course_constant"
    bad = {"name": "x", "dashboards": [{"title": "d", "charts": [{"title": "c", "race_refs": "nope"}]}]}
    with pytest.raises(CustomViewError):
        parse_view(bad)
