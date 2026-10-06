"""Owner decision 2026-10-06 (SP-210 follow-up): on a GPX course each
segment's race-day temperature follows its own height — the weather point's
temperature (at heat_ref_alt_m) moved by −0.0065 K/m, RH kept — in the
single-value and the hourly mode. Synthetic courses only."""
import datetime as dt

import pytest

from backend.engine.racepower import course as CO
from backend.engine.racepower import env as ENV
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.engine.racepower import weather as WX
from backend.tests.test_racepower_export import DATE, hot_later, rows_from
from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track

approx = pytest.approx
BASE_M = 200.0


def climb():
    """200 m up to 1700 m and back over 30 km (≈ 10 % grade)."""
    tr = synthetic_track({"len": 30000, "z": lambda x: BASE_M + (x * 0.1 if x < 15000 else (30000 - x) * 0.1)})
    return CO.build_course(tr)


def hot_v1(c, temp=30.0, rh=70.0):
    v1 = fake_v1("trail", 30.0, c["totals"]["gain_m"], alt=BASE_M)
    v1["env"]["to"].update(temp_c=temp, rh_pct=rh)
    return v1


def run(c, v1, **opts):
    return PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), validated={}, effort_validated=False,
                       opts={"mode": "auto", "date": DATE, "start_time": "06:00", **opts})


def test_single_value_moves_to_each_segments_height():
    c = climb()
    v1 = hot_v1(c)
    flat = run(c, v1)
    lapsed = run(c, v1, heat_ref_alt_m=BASE_M)
    assert all(s["temp_c"] == 30.0 for s in flat["segments"])               # no reference → as before
    for s in lapsed["segments"]:
        t = 30.0 - 0.0065 * (s["z_mean"] - BASE_M)
        assert s["temp_c"] == approx(t) and s["rh_pct"] == approx(70.0) and s["heat_src"] == "single"
        assert s["dew_c"] == approx(ENV.dew_point(t, 70.0)["dew_c"])
        assert s["heat_pct"] == approx(ENV.heat_penalty_pct(t, 70.0))
    top = max(lapsed["segments"], key=lambda s: s["z_mean"])
    assert top["temp_c"] < 30.0 - 4.0                                        # ~1 km up: > 4 °C cooler
    # cooler high ground → less heat penalty → a faster race
    assert lapsed["summary"]["time_s"] < flat["summary"]["time_s"]
    assert lapsed["summary"]["heat"]["ref_alt_m"] == BASE_M
    assert any("天氣點（200 m）" in w for w in lapsed["warnings"])


def test_hourly_rows_are_moved_to_each_segments_height_too():
    c = climb()
    v1 = hot_v1(c)
    fc = rows_from(hot_later)
    p = run(c, v1, hourly=fc, heat_ref_alt_m=BASE_M)
    rows = sorted((WX._hour_row(r["t"], r["temp_c"], r["rh_pct"], r.get("dew_c")) for r in fc), key=lambda r: r["t"])
    assert p["summary"]["heat"]["mode"] == "hourly"
    for s in p["segments"]:
        assert s["heat_src"] == "hourly"
        h, m = (int(x) for x in s["heat_clock"].split(":"))
        at = WX.hourly_at(rows, dt.datetime.fromisoformat(DATE) + dt.timedelta(hours=h, minutes=m))
        # the clock is shown to the minute: ±1 min of the hourly slope
        assert s["temp_c"] == approx(at["temp_c"] - 0.0065 * (s["z_mean"] - BASE_M), abs=0.06)
        assert s["rh_pct"] == approx(at["rh_pct"], abs=0.5)


def test_the_reference_at_the_courses_own_height_changes_nothing():
    tr = synthetic_track({"len": 10000, "z": lambda x: 800.0})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 10.0, 0.0, alt=800.0)
    v1["env"]["to"].update(temp_c=28.0, rh_pct=75.0)
    a, b = run(c, v1), run(c, v1, heat_ref_alt_m=800.0)
    assert b["summary"]["time_s"] == approx(a["summary"]["time_s"], abs=1e-6)
    assert [s["M"] for s in b["segments"]] == approx([s["M"] for s in a["segments"]], abs=1e-12)


def test_a_manual_course_has_one_height_and_is_not_lapsed():
    v1 = fake_v1("road", 42.195)
    v1["env"]["to"].update(temp_c=30.0, rh_pct=70.0)
    kw = dict(v1=v1, course=CO.manual_course(42.195, 0.0, 0.0, "km"), grade_re=GM.GradeRE(RE0),
              validated={}, effort_validated=False)
    a = PL.plan_run(opts={"mode": "auto", "date": DATE, "start_time": "06:00"}, **kw)
    b = PL.plan_run(opts={"mode": "auto", "date": DATE, "start_time": "06:00", "heat_ref_alt_m": 2000.0}, **kw)
    assert b["summary"]["time_s"] == approx(a["summary"]["time_s"], abs=1e-9)
    assert "ref_alt_m" not in b["summary"]["heat"]
