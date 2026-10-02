"""Steady-climb VAM:HR on trail runs and hikes (engine/climb_vam.py, panels/climb_vam.py).
Synthetic data only — never the WKO5 folder or the real DB."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from backend.engine import climb_vam as CV


def _act(parts):
    """parts: [(seconds, grade, speed_kmh, hr, cadence)] → 1-s channels."""
    t, d, z, h, c, v = [], [], [], [], [], []
    tt, dd, zz = 0.0, 0.0, 100.0
    for sec, grade, kmh, hr, cad in parts:
        for i in range(int(sec)):
            ms = kmh(i) / 3.6 if callable(kmh) else kmh / 3.6
            dd += ms
            zz += ms * grade
            t.append(tt); d.append(dd); z.append(zz)
            h.append(hr(i) if callable(hr) else hr)
            c.append(cad(i) if callable(cad) else cad)
            v.append(ms * 3.6)
            tt += 1.0
    return dict(t=t, dist_m=d, elev=z, hr=h, cadence=c, speed_kmh=v)


def _ex(parts, **kw):
    a = _act(parts)
    return CV.extract_climbs(a["t"], a["dist_m"], a["elev"], a["hr"], a["cadence"], a["speed_kmh"], **kw)


FLAT = (300, 0.0, 5.0, 120, 55)
DOWN = (300, -0.15, 4.0, 115, 55)
# a walked 15 % climb at 3 km/h: VAM = 3000 m/h × 0.15 = 450 m/h
HIKE = (900, 0.15, 3.0, 150, 55)


def test_a_walked_climb_gives_vam_hr_and_is_marked_walking():
    r = _ex([FLAT, HIKE, DOWN])
    assert r["reason"] is None and len(r["segments"]) == 1
    s = r["segments"][0]
    assert 860 <= s["duration_s"] <= 940
    assert s["measured_s"] == pytest.approx(s["duration_s"] - 120)
    assert s["vam"] == pytest.approx(450, abs=8)
    assert s["avg_hr"] == pytest.approx(150, abs=0.5)
    assert s["vam_hr"] == pytest.approx(450 / 150, abs=0.06)
    assert s["grade"] == pytest.approx(0.15, abs=0.005)
    assert s["mode"] == "走" and s["run_share"] == 0.0 and s["cadence_spm"] == 110


def test_a_run_climb_is_marked_running_and_no_cadence_means_unknown():
    s = _ex([FLAT, (900, 0.09, 7.0, 160, 82), DOWN])["segments"][0]
    assert s["mode"] == "跑" and s["run_share"] == 1.0
    a = _act([FLAT, HIKE, DOWN])
    s = CV.extract_climbs(a["t"], a["dist_m"], a["elev"], a["hr"], None, a["speed_kmh"])["segments"][0]
    assert s["mode"] is None and s["run_share"] is None


def test_hr_lag_minutes_are_dropped():
    ramp = lambda i: 110 + min(i, 120) / 120 * 40
    s = _ex([FLAT, (900, 0.15, 3.0, ramp, 55), DOWN])["segments"][0]
    assert s["avg_hr"] == pytest.approx(150, abs=0.6)


def test_a_gentle_grade_or_a_descent_is_not_a_climb():
    assert _ex([FLAT, (900, 0.05, 6.0, 140, 80), DOWN])["segments"] == []
    assert _ex([FLAT, (900, -0.12, 5.0, 140, 60), FLAT])["segments"] == []


def test_short_climb_is_rejected_and_reports_its_length():
    r = _ex([FLAT, (360, 0.15, 3.0, 150, 55), DOWN])
    assert r["segments"] == [] and [x["reason"] for x in r["rejected"]] == ["short"]
    assert 320 <= r["longest_s"] <= 400


def test_a_short_stand_joins_a_long_rest_splits():
    brief = lambda i: 0.0 if 400 <= i < 420 else 3.0      # 20 s standing: one climb
    assert len(_ex([FLAT, (900, 0.15, brief, 150, 55), DOWN])["segments"]) == 1
    rest = lambda i: 0.0 if 400 <= i < 520 else 3.0       # a 2-min rest: two climbs under 8 min
    assert _ex([FLAT, (900, 0.15, rest, 150, 55), DOWN])["segments"] == []


def test_steep_slow_hiking_still_counts_as_moving():
    # 35 % at 1.2 km/h (under WKO5's 1.6 km/h moving threshold): VAM 420 m/h
    s = _ex([FLAT, (900, 0.35, 1.2, 155, 50), DOWN])["segments"][0]
    assert s["vam"] == pytest.approx(420, abs=10)


def test_more_height_than_distance_is_a_glitch():
    r = _ex([FLAT, (900, 1.5, 1.2, 150, 50), DOWN])
    assert r["segments"] == [] and r["rejected"][0]["reason"] == "glitch"


def test_missing_hr_says_why():
    a = _act([FLAT, HIKE])
    r = CV.extract_climbs(a["t"], a["dist_m"], a["elev"], None, a["cadence"])
    assert r["reason"] == "no_hr" and r["segments"] == []


def test_rolling_median_is_over_the_last_8_weeks():
    pts = [(0, 1.0), (10, 3.0), (20, 2.0), (70, 10.0), (90, 4.0)]
    assert CV.rolling_median(pts) == [1.0, 2.0, 2.0, 6.0, 7.0]


# ---------------------------------------------------------------------------
# panel: trail runs + hikes, route grouping, heat adjustment
# ---------------------------------------------------------------------------

from backend.engine.panels import climb_vam as PANEL           # noqa: E402
from backend.engine.wko5expr.dataset import date_to_day         # noqa: E402
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout   # noqa: E402


@pytest.fixture(autouse=True)
def _author_beta(monkeypatch):
    """These synthetic runs follow the author's fitted heat β 0.224 ± 0.036 bpm/Hadley
    (engine/heat_calib.hr_beta; a new athlete starts from the 0.3 default)."""
    from backend.engine import heat_calib as HC
    monkeypatch.setattr(HC, "hr_beta", lambda: {"beta": 0.224, "se": 0.036, "n": 271, "source": "fitted",
                                                "src": "熱 β 0.224 bpm／Hadley（測試：作者的擬合）"})

TODAY = dt.date(2026, 9, 30)


def _fw(day, kmh=3.0, hr=150, kind="trail"):
    a = _act([FLAT, (900, 0.15, kmh, hr, 55), DOWN])
    ch = {"elapsedtime": a["t"], "elapseddistance": [x / 1000 for x in a["dist_m"]], "_elevation": a["elev"],
          "heartrate": a["hr"], "cadence": a["cadence"], "speed": a["speed_kmh"]}
    sport, tags, st = {"trail": ("run", ["runningtrail"], "trail running"),
                       "hike": ("walk", ["hiking"], "hiking"),
                       "road": ("run", ["running"], "running")}[kind]
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport=sport, tags=tags, sport_type=st, channels=ch)


class DS(FakeDataset):
    def __init__(self, acts):
        super().__init__(acts, TODAY)

    def aethr(self, w):
        return 145.0

    def sport_setting(self, kind, w):
        return 165.0 if (kind == "thr" and w.sport == "run") else None


def _routes(groups):
    """groups: [(route id, name, [files], {file: dir})]"""
    return {"routes": [{"id": rid, "auto_name": name, "members": files, "dirs": dirs or {}}
                       for rid, name, files, dirs in groups]}


def test_panel_takes_trail_runs_and_hikes_by_route_and_defaults_to_the_most_done():
    days = [dt.date(2026, 8, 1) + dt.timedelta(days=7 * k) for k in range(6)]
    acts = [_fw(d, kmh=3.0 + 0.1 * k, kind="hike" if k == 2 else "trail") for k, d in enumerate(days)] \
        + [_fw(dt.date(2026, 9, 20), kind="road")]
    ds = DS(acts)
    files = [w.entry.file for w in ds.workouts]
    idx = _routes([("rA", "七星山 A", files[:4], {}), ("rB", "B", files[4:6], {})])
    wx = {files[0]: {"hadley": 150.0, "temp_c": 28.0}}
    b, e = date_to_day(dt.date(2026, 6, 1)), date_to_day(TODAY)
    res = PANEL.compute(ds, b, e, {}, route_index=idx, weather=wx, names={})
    assert res["kind"] == "climbvam"
    assert res["counts"]["trail_runs"] == 5 and res["counts"]["hikes"] == 1      # the road run is not in it
    assert [(r["id"], r["runs"]) for r in res["routes"]] == [("rA", 4), ("rB", 2)]
    assert res["routes"][0]["kinds"] == ["hike", "trail"]
    assert res["route"]["id"] == "rA" and len(res["points"]) == 4
    p0 = res["points"][0]
    assert p0["vam"] == pytest.approx(450, abs=8) and p0["mode"] == "走"
    # β-adjusted HR: 150 − 0.224·(150 − 120) = 143.28 (推估)
    assert p0["hr_adj"] == pytest.approx(143.28, abs=0.05)
    assert p0["vam_hr_adj"] == pytest.approx(p0["vam"] / 143.28, abs=0.01)
    assert res["points"][1]["hr_adj"] is None
    assert p0["zone"] == "AeT–LTHR" and res["points"][2]["zone"] == "AeT–LTHR"   # the hike: the last run's thresholds
    assert res["median"][-1] == pytest.approx(np.median([p["vam_hr"] for p in res["points"]]), abs=0.002)
    other = PANEL.compute(ds, b, e, {"route": "rB"}, route_index=idx, weather=wx, names={})
    assert other["route"]["id"] == "rB" and len(other["points"]) == 2


def test_panel_keeps_reversed_runs_apart_and_falls_back_when_nothing_compares():
    acts = [_fw(dt.date(2026, 9, 1) + dt.timedelta(days=k)) for k in range(5)]
    ds = DS(acts)
    f = [w.entry.file for w in ds.workouts]
    b, e = date_to_day(dt.date(2026, 6, 1)), date_to_day(TODAY)
    # 2 the usual way, 2 reversed, 1 mixed (left out); route B has a single activity: not listed
    idx = _routes([("rA", "A", f[:4] + [f[4]], {f[2]: "reversed", f[3]: "reversed", f[4]: "mixed"})])
    res = PANEL.compute(ds, b, e, {}, route_index=idx, weather={}, names={"rA": "我的 A"})
    keys = {r["id"]: r for r in res["routes"]}
    assert set(keys) == {"rA", "rA~rev"} and keys["rA"]["name"] == "我的 A" and "反向" in keys["rA~rev"]["name"]
    assert keys["rA"]["runs"] == 2 and keys["rA~rev"]["runs"] == 2
    one = PANEL.compute(ds, b, e, {}, route_index=_routes([("rB", "B", f[:1], {})]), weather={}, names={})
    assert one["routes"] == [] and "2 次以上" in one["empty"]
    lone = PANEL.compute(ds, b, e, {}, route_index={"routes": []}, weather={}, names={})
    assert lone["points"] == [] and lone["empty"] and len(lone["runs_longest"]) == 5
    empty = PANEL.compute(DS([]), b, e, {}, route_index={"routes": []}, weather={}, names={})
    assert empty["points"] == [] and empty["empty"]


def test_panel_matches_another_sources_files_by_start_time():
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
    ch = [c for c in dash["charts"] if c["kind"] == "climbvam"]
    assert len(ch) == 1 and "推估" in ch[0]["description"] and "Uphill Athlete" in ch[0]["description"]
    titles = [c["title"] for c in dash["charts"]]
    assert abs(titles.index(ch[0]["title"]) - titles.index("上坡腳程（每小時爬升，心率 < LTHR）")) <= 1
    PANEL.SOURCES_OVERRIDE = {"route_index": {"routes": []}, "weather": {}, "names": {}}
    try:
        res = _render(ch[0], DS([]), 0, date_to_day(TODAY), None, None, params={})
    finally:
        PANEL.SOURCES_OVERRIDE = None
    assert res["kind"] == "climbvam" and res["title"] == ch[0]["title"]
