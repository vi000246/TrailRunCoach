"""主要訓練項目 (engine/primary_sport.py; docs/plans/generalize-athlete.plan.md §5): the
suggestion, the chart tags in views/*.json, the road charts' expressions, the planner's road
template (no B2B / steep walk / mountain long run; marathon-pace long run in the 專項期), the
step builders and the 插入範本 推薦. Synthetic data only — no WKO5 folder, no ~/.wko5coach."""
import datetime as dt
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import primary_sport as PS
from backend.engine import projection as P
from backend.engine.planning import Event, Plan, Threshold
from backend.engine.status import Status
from backend.engine.wko5expr.customviews import CustomViewError, parse_view
from backend.tests.test_b2b import _history, _phases
from backend.tests.test_quality_gate import TODAY

VIEWS = Path(__file__).resolve().parents[2] / "views"
UTC = timezone.utc


def _view(name):
    return json.loads((VIEWS / f"{name}.json").read_text("utf-8"))


def _charts(name):
    return {c["id"]: c for d in _view(name)["dashboards"] for c in d["charts"]}


# ---- the suggestion ----------------------------------------------------------------

def _ev(kind, start="2026-12-05", priority="A"):
    return Event(id="e", name="目標", date=start, kind=kind, priority=priority, days=1, distance_km=42.195)


def test_suggestion_follows_the_next_a_race_then_the_trail_share():
    ds = _history(TODAY)                                   # road runs only (FakeWorkout sport run)
    assert PS.suggest(ds, [_ev("road")], TODAY)["sport"] == "road"
    assert PS.suggest(ds, [_ev("race")], TODAY)["basis"] == "event"
    assert PS.suggest(ds, [_ev("baiyue")], TODAY)["sport"] == "trail"
    assert PS.suggest(ds, [_ev("race", priority="B")], TODAY)["basis"] == "share"     # only A races decide
    sh = PS.suggest(ds, [], TODAY)
    assert sh["sport"] == "road" and sh["trail_share"] == 0.0 and "推估" in sh["reason"]
    for w in ds.workouts[::2]:                             # half the runs on trails
        w.tags = ["runningtrail"]
    assert PS.suggest(ds, [], TODAY)["sport"] == "trail"
    assert PS.suggest(None, [], TODAY) == {**PS.suggest(None, [], TODAY), "sport": "trail", "basis": "default"}


def test_setting_wins_over_the_suggestion():
    ds = _history(TODAY)
    assert PS.resolve(ds, [_ev("race")], TODAY, setting="road")["sport"] == "road"
    r = PS.resolve(ds, [_ev("road")], TODAY, setting="auto")
    assert r["sport"] == "road" and r["setting"] == "auto" and r["suggested"]["basis"] == "event"
    assert PS.resolve(ds, [], TODAY, setting="bogus")["setting"] == "auto"
    assert PS.stored() == "auto"                           # no settings DB in the tests


def test_setting_is_validated():
    from backend.settings.repository import DEFAULTS, validate
    assert DEFAULTS["athlete.primary_sport"] == "auto"
    for v in ("auto", "trail", "road"):
        validate("athlete.primary_sport", v)
    with pytest.raises(ValueError):
        validate("athlete.primary_sport", "bike")


# ---- chart tags -------------------------------------------------------------------

def test_chart_tags_parse_and_are_checked():
    v = {"name": "x", "dashboards": [{"title": "d", "charts": [
        {"title": "a", "sports": ["road"], "order": {"road": -1}}, {"title": "b"}]}]}
    c = parse_view(v)["dashboards"][0]["charts"]
    assert c[0]["sports"] == ["road"] and c[0]["order"] == {"road": -1} and "sports" not in c[1]
    assert PS.shows(c[0], "road") and not PS.shows(c[0], "trail") and PS.shows(c[1], "trail")
    for bad in ({"sports": ["bike"]}, {"sports": []}, {"sports": "road"}, {"order": {"bike": 1}}):
        with pytest.raises(CustomViewError):
            parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [{"title": "a", **bad}]}]})


TRAIL_ONLY = {
    "training": {"weekly-downhill-load", "uphill-vam", "steady-climb-vam-hr", "downhill-rate",
                 "route-difficulty", "climb-density"},
    "periodization": {"route-difficulty-specific", "longest-session", "climb-density-vs-race", "uphill-vam"},
    "workout": {"route-difficulty", "climbs", "grades", "form-grades"},
}
ROAD_ONLY = {
    "training": {"long-run-km", "long-run-pace", "mp-weekly"},
    "periodization": {"long-run-km-specific", "long-run-pace-specific", "mp-weekly-specific"},
    "workout": set(),
}


@pytest.mark.parametrize("name", ["training", "periodization", "workout"])
def test_views_tag_the_mountain_and_marathon_charts(name):
    cs = _charts(name)
    assert {k for k, c in cs.items() if c.get("sports") == ["trail"]} == TRAIL_ONLY[name]
    assert {k for k, c in cs.items() if c.get("sports") == ["road"]} == ROAD_ONLY[name]
    parse_view(_view(name), VIEWS / f"{name}.json")                  # still a valid view
    # every dashboard keeps at least one chart in each mode (except the workout 爬坡與地形 page)
    for d in _view(name)["dashboards"]:
        for sp in PS.SPORTS:
            if not (name == "workout" and d["id"] == "climbing" and sp == "road"):
                assert any(PS.shows(c, sp) for c in d["charts"]), (name, d["id"], sp)


def test_list_views_carries_the_tags(monkeypatch):
    from backend.api import wko5views as W
    monkeypatch.setattr(W, "_wko5_views", lambda parity=True: {})
    v = next(x for x in W.list_views() if x["name"] == _view("training")["name"])
    cs = {c["id"]: c for d in v["dashboards"] for c in d["charts"]}
    assert cs["uphill-vam"]["sports"] == ["trail"] and cs["mp-weekly"]["sports"] == ["road"]
    assert cs["weekly-volume"]["order"] == {"road": -1} and "sports" not in cs["pmc-all"]
    # the 專項期 page has a 路跑 text (the viewer shows it instead of the trail one)
    pv = next(x for x in W.list_views() if x["name"] == _view("periodization")["name"])
    build = next(d for d in pv["dashboards"] if d["id"] == "build")
    assert "馬拉松" in build["descriptions"]["road"] and "越野" in build["description"]
    with pytest.raises(CustomViewError):
        parse_view({"name": "x", "dashboards": [{"title": "d", "descriptions": {"bike": "x"}, "charts": []}]})


# ---- the road charts' expressions (synthetic FITs) ----------------------------------

def _fit_ds(tmp_path, runs, tpace_min=5.0):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    for i, kw in enumerate(runs):
        (d / f"{i:03d}.fit").write_bytes(build_run(**kw))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=TODAY,
                          estimate_thresholds=False, tz=UTC)
    ds.plan = Plan()
    orig = ds.setting
    ds.setting = lambda name, day: tpace_min if name == "runtpace" else orig(name, day)
    return ds


def _vals(r):
    vs = r.values() if callable(r.values) else r.values
    return [v for v in vs if v == v]


def _series(view, cid, i=0):
    return _charts(view)[cid]["series"][i]["expression"]


def test_long_run_charts_count_long_road_runs_only(tmp_path):
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.evaluator import Evaluator
    ds = _fit_ds(tmp_path, [
        dict(start=datetime(2026, 9, 1, tzinfo=UTC), seconds=6000, speed_m_s=3.0),          # 100 min, 18 km
        dict(start=datetime(2026, 9, 2, tzinfo=UTC), seconds=3000, speed_m_s=3.0),          # 50 min: not long
        dict(start=datetime(2026, 9, 3, tzinfo=UTC), seconds=6000, speed_m_s=2.0, sub_sport=3),   # trail
    ])
    b = date_to_day(date(2026, 9, 1))
    ev = Evaluator(ds, b, b + 5)
    km = _vals(ev.evaluate(_series("training", "long-run-km")))
    assert km == [pytest.approx(18.0, rel=0.03)]
    pace = _vals(ev.evaluate(_series("training", "long-run-pace")))
    assert pace == [pytest.approx(1000 / 3.0, rel=0.03)]                     # s/km
    assert _series("periodization", "long-run-km-specific") == _series("training", "long-run-km")


def test_marathon_pace_time_counts_the_mp_band(tmp_path):
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.evaluator import Evaluator
    # threshold pace 5:00/km → MP band 5:12–5:24/km (1.04–1.08 ×): 3.15 m/s (5:17/km) is inside
    ds = _fit_ds(tmp_path, [
        dict(start=datetime(2026, 9, 1, tzinfo=UTC), seconds=1800, speed_m_s=3.15),
        dict(start=datetime(2026, 9, 2, tzinfo=UTC), seconds=1800, speed_m_s=2.5),          # easy: outside
        dict(start=datetime(2026, 9, 3, tzinfo=UTC), seconds=1200, speed_m_s=3.15, sub_sport=3),   # trail
    ])
    b = date_to_day(date(2026, 8, 31))                                       # a Monday
    r = Evaluator(ds, b, b + 6).evaluate(_series("training", "mp-weekly"))
    got = _vals(r)
    assert got == [pytest.approx(1800, rel=0.05)]


# ---- the planner ---------------------------------------------------------------------

def _plan_road(kind="road"):
    from backend.engine.planning import Weight
    plan = Plan()
    plan.thresholds.append(Threshold("2026-08-01", lthr=165, cp=250.0))
    plan.events.append(Event(id="e1", name="台北馬", date="2026-12-05", kind=kind, days=2 if kind == "baiyue" else 1,
                             distance_km=42.195, est_hours=3.5 if kind == "road" else None))
    plan.weights.append(Weight("2026-08-01", 62.0))
    return plan


def _wp(kind="road", sport=None, last_week="recovery"):
    ds = _history(TODAY, last_week)
    plan = _plan_road(kind)
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return ds, plan, O.week_plan(ds, st, TODAY, sport=sport)


def test_road_week_marathon_pace_long_run_no_b2b_no_steep_walk():
    _, plan, wp = _wp("road")                        # auto: the A race is a 路跑賽
    assert wp["phase"] == "specific" and wp["primary_sport"] == "road"
    by = {s["id"]: s for s in wp["sessions"]}
    lg = by["long"]
    assert "馬拉松配速" in lg["title"] and lg["terrain"] == "road" and "山路" not in lg["title"]
    mp = int(lg["title"].split("馬拉松配速")[1].split("分")[0])
    assert O.MP_MIN <= mp <= O.MP_MAX and mp <= lg["minutes"] - 25 and "Pfitzinger" in lg["source"]
    # the A marathon's 預估移動時間 3.5 h over 42.195 km = the MP segment's goal pace
    assert wp["mp_goal_pace_s"] == pytest.approx(3.5 * 3600 / 42.195) and "目標配速 4:59/km" in lg["detail"]
    assert not any("爬坡" in s["title"] or "坡道" in s["title"] for s in wp["sessions"])
    assert wp["b2b_suggestion"] is None and not (wp["b2b"] or {}).get("candidate")
    assert not (wp["steep_hill"] or {}).get("active")
    # the projection follows: no mountain long run, no B2B, MP long runs in the 專項期
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 22))
    assert weeks and all("b2b_suggestion" not in w and "steep_hill" not in w for w in weeks)
    ss = [s for w in weeks for s in w["sessions"]]
    assert not any("山路" in s["title"] or "爬坡" in s["title"] or s["id"] == "long2" for s in ss)
    assert any("馬拉松配速" in s["title"] for w in weeks if w["phase"] == "specific" for s in w["sessions"])


def test_road_specific_phase_follows_the_race_distance_not_the_course_constant():
    from backend.engine import specific_phase as SP
    _, plan, wp = _wp("road")
    sp = wp["specific"]
    assert sp["active"] and sp["sport"] == "road" and not sp.get("climb") and "路跑" in sp["climb_why"]
    lg = next(s for s in wp["sessions"] if s["id"] == "long")
    assert "賽事距離的" in lg["detail"] and "定數" not in lg["detail"] and "馬拉松配速" in lg["title"]
    assert not any(s["id"] == "climb" for s in wp["sessions"])
    race = {"day": {"hours": 3.5, "km": 42.195}}
    info = {"active": True, "frac": 0.8, "race": race, "sport": "road"}
    p = 3.5 * 60 / 42.195 * SP.ROAD_EASY_SLOW
    assert SP.long_minutes(info, 1e9) == pytest.approx(min(min(0.8 * 42.195, 35.0) * p, 180.0))
    assert SP.long_minutes(info, 100) == pytest.approx(115)                       # +15 % over the longest
    # the race simulation stays: a flat long run in race kit, never the whole marathon
    sg = SP.sim_suggestion({**info, "race": {**race, "id": "e1", "name": "台北馬", "start": "2026-12-05", "days": 1,
                                             "kind": "road", "hours": 3.5, "km": 42.195}},
                           date(2026, 11, 2), 170)
    assert sg and sg["minutes"][0] <= 180 and sg["sessions"][0]["terrain"] == "road" and "km" in sg["title"]
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 22))
    assert all((w.get("specific") or {}).get("sport", "road") == "road" for w in weeks)


def test_trail_setting_keeps_the_original_week_even_before_a_road_race():
    _, _, road = _wp("road")
    _, _, trail = _wp("road", sport="trail")
    assert trail["primary_sport"] == "trail"
    assert not any("馬拉松配速" in s["title"] for s in trail["sessions"])
    assert {s["id"] for s in trail["sessions"]} >= {"long"}
    assert road["target"]["hours"] == pytest.approx(trail["target"]["hours"])     # same volume rules


def test_road_setting_drops_the_mountain_tools_before_a_baiyue():
    _, _, trail = _wp("baiyue")
    _, _, road = _wp("baiyue", sport="road")
    assert trail["primary_sport"] == "trail" and trail["b2b_suggestion"] is not None   # auto: the 百岳
    assert road["b2b_suggestion"] is None and not (road["steep_hill"] or {}).get("active")
    assert not any("山路" in s["title"] for s in road["sessions"])


def test_road_base_week_flat_strides():
    from backend.engine import workout_steps as WS
    s = {"kind": "easy", "title": "輕鬆跑" + O.ROAD_STRIDES[0], "minutes": 50}
    items = WS.derive(s)["items"]
    assert items[1]["items"][0]["note"] == "20 秒加速跑（平路）" and items[1]["times"] == 6
    hill = WS.derive({"kind": "easy", "title": "輕鬆跑" + O.HILL_STRIDES[0], "minutes": 50})["items"]
    assert hill[1]["items"][0]["note"] == "10 秒上坡衝刺"
    # the 課表偏好 shaping still sees it as the strides run
    c = PP.Ctx(kind="base", mode="base", allow_quality=True, rates={"road": 50.0})
    out = PP._easy({**s, "kind": "easy", "id": "easy1", "target": "", "detail": "", "source": ""}, 0, 50, PP.Prefs(), c)
    assert "加速跑" in out["title"]


def test_mp_long_run_steps_editor_and_coros():
    from backend.engine import workout_steps as WS
    from backend.sync import coros_workouts as CW
    s = O.road_long_session(150, "specific", 145.0, 50.0)
    assert s["minutes"] == 150 and WS.mp_minutes(s) == O.mp_minutes(150) == 60
    items = WS.derive(s)["items"]
    assert [it["dur"]["value"] for it in items] == [80 * 60, 60 * 60, 10 * 60]
    assert items[1]["target"] == {"type": "pace", "mode": "pct", "lo": WS.MP_PACE[0], "hi": WS.MP_PACE[1],
                                  "hrp": list(WS.MP_HR)}
    assert WS.normalize(WS.derive(s))["items"][1]["target"]["hrp"] == list(WS.MP_HR)     # survives a save
    # threshold pace known: a pace target (s/km); none: the HR band with the 沒有閾值配速 warning
    r = WS.resolve(items[1], WS.Ctx(lthr=165.0, tpace=280.0))
    assert r.type == "pace" and r.intensity == ("pace", round(1.04 * 280), round(1.08 * 280))
    r = WS.resolve(items[1], WS.Ctx(lthr=165.0))
    assert r.type == "hr" and r.intensity == ("hr", round(0.88 * 165), round(0.95 * 165))
    assert r.need == "tpace" and r.warn == WS.no_tpace_text()
    steps = CW.session_steps(s, CW.Thresholds(cp=250.0, lthr=165.0, aet=145.0))
    assert [st.seconds for st in steps] == [80 * 60, 60 * 60, 10 * 60]
    assert steps[1].intensity == ("hr", round(0.88 * 165), round(0.95 * 165))
    steps = CW.session_steps(s, CW.Thresholds(cp=250.0, lthr=165.0, aet=145.0, tpace=280.0))
    assert steps[1].intensity == ("pace", round(1.04 * 280), round(1.08 * 280))
    # the race's goal pace wins (± 1.5 %), with or without a threshold pace
    g = O.road_long_session(150, "specific", 145.0, 50.0, goal_pace=300.0)
    assert "目標配速 5:00/km" in g["detail"] and WS.mp_goal_pace(g) == 300
    assert WS.derive(g)["items"][1]["target"] == {"type": "pace", "mode": "abs", "lo": 296, "hi": 304}
    assert CW.session_steps(g, CW.Thresholds(lthr=165.0))[1].intensity == ("pace", 296, 304)
    base = O.road_long_session(100, "base", 145.0, 50.0)
    assert base["title"] == "LSD（路跑）" and WS.mp_minutes(base) is None
    # 課表偏好 長跑地形 = 路跑 keeps the MP segment
    c = PP.Ctx(kind="specific", mode="specific", allow_quality=True, rates={"road": 50.0}, aet=145.0)
    t = dict(s)
    PP._terrain_long(t, PP.Prefs(terrain_long="road"), c)
    assert t["title"] == s["title"]


# ---- 插入範本 推薦 --------------------------------------------------------------------

@pytest.fixture(scope="module")
def tpl():
    from backend.engine import workout_steps as WS
    return WS.templates()


def test_recs_road_no_trail_templates_marathon_work_in_the_specific_phase(tpl):
    from backend.engine import template_recs as TR
    trail = TR.recommend(tpl, kind="quality", minutes=60, phase="specific", z5_open=True)
    road = TR.recommend(tpl, kind="quality", minutes=60, phase="specific", z5_open=True, sport="road")
    assert trail["cats"].get("trail") and road["cats"].get("trail") == []
    assert any(r["key"] in TR.ROAD_SPECIFIC for r in road["cats"]["quality"])
    assert road["inputs"]["sport"] == "road"
    long_ = TR.recommend(tpl, kind="long", minutes=120, phase="specific", sport="road")
    assert long_["cats"]["easy"][0]["key"] in TR.ROAD_LONG
