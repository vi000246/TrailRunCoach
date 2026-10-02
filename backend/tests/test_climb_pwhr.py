"""Steady-climb Pw:HR on trail runs (engine/climb_pwhr.py, panels/climb_pwhr.py).
Synthetic data only — never the WKO5 folder or the real DB."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from backend.engine import climb_pwhr as CP


def _act(parts):
    """parts: [(seconds, grade, speed_kmh, power, hr, cadence)] → 1-s channels."""
    t, d, z, p, h, c, v = [], [], [], [], [], [], []
    tt, dd, zz = 0.0, 0.0, 100.0
    for sec, grade, kmh, pw, hr, cad in parts:
        for i in range(int(sec)):
            ms = kmh / 3.6
            dd += ms
            zz += ms * grade
            t.append(tt); d.append(dd); z.append(zz)
            p.append(pw(i) if callable(pw) else pw)
            h.append(hr(i) if callable(hr) else hr)
            c.append(cad(i) if callable(cad) else cad)
            v.append(kmh)
            tt += 1.0
    return dict(t=t, dist_m=d, elev=z, power=p, hr=h, cadence=c, speed_kmh=v)


def _ex(parts, **kw):
    a = _act(parts)
    return CP.extract_segments(a["t"], a["dist_m"], a["elev"], a["power"], a["hr"], a["cadence"],
                               a["speed_kmh"], **kw)


FLAT = (300, 0.0, 10.0, 220, 140, 85)
DOWN = (300, -0.06, 11.0, 150, 135, 88)


def test_one_steady_climb_gives_one_segment_with_its_numbers():
    r = _ex([FLAT, (900, 0.05, 8.0, 250, 150, 85), DOWN])
    assert r["reason"] is None and len(r["segments"]) == 1
    s = r["segments"][0]
    # the ±50 m grade baseline blurs each end by ~ 50 m (~ 22 s at 8 km/h)
    assert 860 <= s["duration_s"] <= 940
    assert s["measured_s"] == pytest.approx(s["duration_s"] - 120)
    assert s["avg_power"] == pytest.approx(250, abs=0.5) and s["avg_hr"] == pytest.approx(150, abs=0.5)
    assert s["pwhr"] == pytest.approx(250 / 150, abs=1e-3)
    assert s["grade"] == pytest.approx(0.05, abs=0.002)
    assert s["vi"] == pytest.approx(1.0, abs=0.01) and s["cadence_spm"] == 170


def test_hr_lag_minutes_are_dropped():
    # HR still rising during the first 2 min of the climb: it does not count
    ramp = lambda i: 110 + min(i, 120) / 120 * 40
    s = _ex([FLAT, (900, 0.05, 8.0, 250, ramp, 85), DOWN])["segments"][0]
    assert s["avg_hr"] == pytest.approx(150, abs=0.6)


@pytest.mark.parametrize("grade", [0.10, 0.02, -0.05])
def test_grades_outside_3_to_8_percent_are_not_segments(grade):
    assert _ex([FLAT, (900, grade, 8.0, 250, 150, 85), DOWN])["segments"] == []


def test_walking_cadence_is_hiking_not_a_segment():
    assert _ex([FLAT, (900, 0.05, 5.0, 200, 140, 55), DOWN])["segments"] == []


def test_short_climb_is_listed_as_rejected_short():
    r = _ex([FLAT, (420, 0.05, 8.0, 250, 150, 85), DOWN])
    assert r["segments"] == [] and [x["reason"] for x in r["rejected"]] == ["short"]


def test_unsteady_power_fails_the_vi_rule():
    surge = lambda i: 150 if (i // 60) % 2 else 360
    r = _ex([FLAT, (900, 0.05, 8.0, surge, 150, 85), DOWN])
    assert r["segments"] == [] and r["rejected"][0]["reason"] == "vi" and r["rejected"][0]["vi"] > 1.04


def test_a_short_break_joins_a_long_one_splits():
    brief = lambda i: 50 if 400 <= i < 405 else 85        # 5 s walking step: still one climb
    assert len(_ex([FLAT, (900, 0.05, 8.0, 250, 150, brief), DOWN])["segments"]) == 1
    long_ = lambda i: 50 if 400 <= i < 460 else 85       # a 1-min hike splits it into two short ones
    assert _ex([FLAT, (900, 0.05, 8.0, 250, 150, long_), DOWN])["segments"] == []


def test_missing_channels_say_why():
    a = _act([FLAT, (900, 0.05, 8.0, 250, 150, 85)])
    r = CP.extract_segments(a["t"], a["dist_m"], a["elev"], a["power"], a["hr"], None)
    assert r["reason"] == "no_cadence" and r["segments"] == []
    r = CP.extract_segments(a["t"], a["dist_m"], a["elev"], None, a["hr"], a["cadence"])
    assert r["reason"] == "no_power"


def test_rolling_median_is_over_the_last_8_weeks():
    pts = [(0, 1.0), (10, 3.0), (20, 2.0), (70, 10.0), (90, 4.0)]
    # day 70: (14, 70] holds 2 and 10; day 90: (34, 90] holds 10 and 4
    assert CP.rolling_median(pts) == [1.0, 2.0, 2.0, 6.0, 7.0]


# ---------------------------------------------------------------------------
# panel: route grouping, Stryd only, heat adjustment
# ---------------------------------------------------------------------------

from backend.engine.panels import climb_pwhr as PANEL           # noqa: E402
from backend.engine.wko5expr.dataset import date_to_day         # noqa: E402
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout   # noqa: E402

TODAY = dt.date(2026, 9, 30)


def _fw(day, power=250, hr=150, trail=True, sport="run"):
    a = _act([FLAT, (900, 0.05, 8.0, power, hr, 85), DOWN])
    ch = {"elapsedtime": a["t"], "elapseddistance": [x / 1000 for x in a["dist_m"]], "_elevation": a["elev"],
          "power": a["power"], "heartrate": a["hr"], "cadence": a["cadence"], "speed": a["speed_kmh"]}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport=sport,
                       tags=["runningtrail"] if trail else ["running"], sport_type="trail running" if trail else "running",
                       channels=ch)


class DS(FakeDataset):
    def __init__(self, acts, watch=()):
        super().__init__(acts, TODAY)
        self._watch = set(watch)

    def power_source(self, w):
        return "watch" if w.entry.file in self._watch else "stryd"

    def aethr(self, w):
        return 145.0

    def sport_setting(self, kind, w):
        return 165.0 if kind == "thr" else None


def _routes(groups):
    """groups: [(route id, name, [files], {file: dir})]"""
    return {"routes": [{"id": rid, "auto_name": name, "members": files, "dirs": dirs or {}}
                       for rid, name, files, dirs in groups]}


def test_panel_groups_by_route_stryd_only_and_defaults_to_the_most_run_route():
    days = [dt.date(2026, 8, 1) + dt.timedelta(days=7 * k) for k in range(6)]
    acts = [_fw(d, power=240 + 4 * k) for k, d in enumerate(days)] + [_fw(dt.date(2026, 9, 20), trail=False)]
    ds = DS(acts, watch={"fake/5.wko4"})
    files = [w.entry.file for w in ds.workouts]
    idx = _routes([("rA", "山 A 路線", files[:4], {}), ("rB", "山 B 路線", files[4:6], {})])
    wx = {files[0]: {"hadley": 150.0, "temp_c": 28.0}}
    b, e = date_to_day(dt.date(2026, 6, 1)), date_to_day(TODAY)
    res = PANEL.compute(ds, b, e, {}, route_index=idx, weather=wx, names={})
    assert res["kind"] == "climbpwhr"
    # rA: 4 Stryd runs; rB: one Stryd run (the other is watch power); the road run is not a trail run
    assert [(r["id"], r["runs"]) for r in res["routes"]] == [("rA", 4), ("rB", 1)]
    assert res["route"]["id"] == "rA" and len(res["points"]) == 4
    assert res["counts"]["watch_power"] == 1
    p0 = res["points"][0]
    assert p0["pwhr"] == pytest.approx(240 / 150, abs=2e-3)
    # β-adjusted HR: 150 − 0.224·(150 − 120) = 143.28 (推估)
    assert p0["hr_adj"] == pytest.approx(143.28, abs=0.05) and p0["pwhr_adj"] == pytest.approx(240 / 143.28, abs=2e-3)
    assert res["points"][1]["hr_adj"] is None             # no weather: no adjustment
    assert p0["zone"] == "AeT–LTHR"                       # 145 ≤ 150 < 165
    assert [round(m, 3) for m in res["median"]][-1] == pytest.approx(np.median([240, 244, 248, 252]) / 150, abs=2e-3)
    other = PANEL.compute(ds, b, e, {"route": "rB"}, route_index=idx, weather=wx, names={})
    assert other["route"]["id"] == "rB" and len(other["points"]) == 1


def test_panel_keeps_reversed_runs_apart_and_says_when_there_is_nothing():
    acts = [_fw(dt.date(2026, 9, 1)), _fw(dt.date(2026, 9, 8))]
    ds = DS(acts)
    f = [w.entry.file for w in ds.workouts]
    idx = _routes([("rA", "A", f, {f[1]: "reversed"})])
    b, e = date_to_day(dt.date(2026, 6, 1)), date_to_day(TODAY)
    res = PANEL.compute(ds, b, e, {}, route_index=idx, weather={}, names={"rA": "我的 A"})
    keys = {r["id"]: r for r in res["routes"]}
    assert set(keys) == {"rA", "rA~rev"} and keys["rA"]["name"] == "我的 A" and "反向" in keys["rA~rev"]["name"]
    empty = PANEL.compute(DS([]), b, e, {}, route_index={"routes": []}, weather={}, names={})
    assert empty["points"] == [] and empty["empty"]


def test_panel_matches_another_sources_files_by_start_time():
    # the route index and the weather are keyed by WKO5 .wko4 names; the charts read FITs
    acts = [_fw(dt.date(2026, 9, 1)), _fw(dt.date(2026, 9, 8))]
    ds = DS(acts)
    idx = {"routes": [{"id": "rA", "auto_name": "A", "members": ["2026/X_2026_09_01_07_00.wko4", "2026/X_2026_09_08_07_01.wko4"],
                       "dirs": {}, "efforts": [{"file": "2026/X_2026_09_01_07_00.wko4", "start": "2026-09-01T07:00:30"},
                                               {"file": "2026/X_2026_09_08_07_01.wko4", "start": "2026-09-08T07:01:40"}]}]}
    wx = {"2026/X_2026_09_08_07_01.wko4": {"hadley": 120.0, "temp_c": 20.0}}
    b, e = date_to_day(dt.date(2026, 6, 1)), date_to_day(TODAY)
    res = PANEL.compute(ds, b, e, {}, route_index=idx, weather=wx, names={})
    assert res["route"]["id"] == "rA" and res["route"]["runs"] == 2
    assert [p["hadley"] for p in res["points"]] == [None, 120.0]
    assert res["points"][1]["hr_adj"] == pytest.approx(150.0, abs=0.05)


def test_view_wiring_puts_the_chart_in_ability_and_the_api_renders_it():
    from backend.api.wko5views import _render
    from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
    v = load_custom_views([REPO_VIEWS])["我的訓練"]
    dash = next(d for d in v["dashboards"] if d["title"] == "能力")
    ch = [c for c in dash["charts"] if c["kind"] == "climbpwhr"]
    assert len(ch) == 1 and "Gravina-Cognetti" in ch[0]["description"] and "Berzosa" in ch[0]["description"]
    titles = [c["title"] for c in dash["charts"]]
    assert abs(titles.index(ch[0]["title"]) - titles.index("上坡腳程（每小時爬升，心率 < LTHR）")) <= 1
    PANEL.SOURCES_OVERRIDE = {"route_index": {"routes": []}, "weather": {}, "names": {}}
    try:
        res = _render(ch[0], DS([]), 0, date_to_day(TODAY), None, None, params={})
    finally:
        PANEL.SOURCES_OVERRIDE = None
    assert res["kind"] == "climbpwhr" and res["title"] == ch[0]["title"]
