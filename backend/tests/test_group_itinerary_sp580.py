"""
SP-580 (跟團 G4): a multi-day 百岳 trip's own start time per day (a group starts its summit day at 03:00).

  * PlanIn.day_starts ('HH:MM', day 1 first): each day's clock (ETA), its night marks and night
    slowdown, its cold / wind points and its 補給 schedule run from that day's start;
  * the hours into the day (f_time, 日內疲勞) count from the day's own start as before — the times
    don't move with the clock; a one-day plan is start_time as before;
  * the page fills the days from a group timetable (Cairn import).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from pytest import approx

from backend.engine.racepower import cold as CD
from backend.engine.racepower import course as CO
from backend.engine.racepower import fuel as FU
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import night as NI
from backend.engine.racepower import planner as PL
from backend.tests.test_baiyue_capacity import _synthetic_cap, _v1
from backend.tests.test_racepower_v2 import synthetic_track

ROOT = Path(__file__).resolve().parents[2]
DATE = "2099-01-10"
SUN = [{"date": f"2099-01-{d}", "sunrise": f"2099-01-{d}T06:30", "sunset": f"2099-01-{d}T17:30"} for d in (10, 11, 12)]


def course():
    return CO.build_course(synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)}))


def plan(**opts):
    return PL.plan_hike(v1=_v1(), course=course(), hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                        opts={"mode": "auto", "day_splits_km": [8.0], "start_time": "07:00", **opts}, validated={},
                        capacity=_synthetic_cap())


def mins(eta):
    h, m = eta.split(" ")[0].split(":")
    return int(h) * 60 + int(m)


def test_the_day_starts_resolve():
    assert PL.day_starts({"start_time": "07:00"}, 1) == ["07:00"]
    assert PL.day_starts({"start_time": "07:00", "day_starts": ["06:00", "03:00", "x", None]}, 4) == \
        ["07:00", "03:00", "07:00", "07:00"]                       # day 1 is start_time; bad / empty = day 1's
    assert PL.day_starts({"start_time": None, "day_starts": [None, "03:00"]}, 2) == [None, "03:00"]


def test_each_day_clocks_from_its_own_start_and_the_times_dont_move():
    base, grp = plan(), plan(day_starts=["07:00", "03:00"])
    assert grp["summary"]["day_starts"] == ["07:00", "03:00"]
    assert [s["t"] for s in grp["segments"]] == [s["t"] for s in base["segments"]]          # f_time: hours into the day
    assert [s["f_time"] for s in grp["segments"]] == [s["f_time"] for s in base["segments"]]
    ratio = grp["summary"]["moving_ratio"]
    d2 = [s for s in grp["segments"] if s["day"] == 2]
    start2 = d2[0]["cum_s"] - d2[0]["t"]
    for s in d2:
        assert mins(s["eta"]) == approx((3 * 60 + (s["cum_s"] - start2) / ratio / 60) % 1440, abs=1)
    for a, b in zip(base["segments"], grp["segments"]):
        if a["day"] == 1:
            assert a["eta"] == b["eta"]
        else:
            assert (mins(a["eta"]) - mins(b["eta"])) % 1440 == 240                          # 07:00 → 03:00


def test_a_one_day_trip_is_unchanged():
    one = dict(day_splits_km=[])
    assert plan(**one)["segments"] == plan(**one, day_starts=["07:00", "03:00"])["segments"]


def test_the_night_and_the_cold_points_follow_the_day_start():
    late = NI.apply(plan(day_starts=["07:00", "07:00"]), date=DATE, start_time="07:00", sun=SUN)
    early = NI.apply(plan(day_starts=["07:00", "03:00"]), date=DATE, start_time="07:00", sun=SUN)
    d2l = [s for s in late["segments"] if s["day"] == 2]
    d2e = [s for s in early["segments"] if s["day"] == 2]
    assert not d2l[0]["night"] and d2e[0]["night"]                  # 03:00 on day 2 is before sunrise
    assert early["night"]["n"] > late["night"]["n"]
    pts = CD.points(early, DATE, "07:00", None, 2)
    first2 = next(p for p, s in zip(pts, early["segments"]) if s["day"] == 2)
    assert first2["begin"] == dt.datetime(2099, 1, 11, 3, 0)
    # the night slowdown (10 %) lengthens only the dark part of day 2's early start
    slow = NI.apply(plan(day_starts=["07:00", "03:00"]), date=DATE, start_time="07:00", sun=SUN, slow_pct=10)
    assert slow["night"]["added_s"] > 0
    d2 = [s for s in slow["segments"] if s["day"] == 2]
    assert mins(d2[0]["eta"]) < 6 * 60 + 30


def test_the_fuel_schedule_starts_each_day_at_its_hour():
    p = plan(day_starts=["07:00", "03:00"])
    f = FU.plan_fuel(p, weight=68, start_time="07:00")
    starts = {e["day"]: e["eta"] for e in f["schedule"] if e["kind"] == "start"}
    assert starts == {1: "07:00", 2: "03:00"}


def test_the_page_has_the_day_starts_and_fills_them_from_a_timetable():
    html = (ROOT / "backend/static/racepower.html").read_text("utf-8")
    assert 'id="daystarts"' in html and "function renderDayStarts" in html and "body.day_starts = dayStartsBody()" in html
    # SP-699: the race's timetable (set in 賽事管理) fills them on a first look at the race
    fn = html[html.index("function applyGroupDefaults"):html.index("// ---------------- the plan event's stored GPX")]
    assert "fillDayStarts(sc)" in fn
    from backend.engine.racepower import calc as CALC
    assert "day_starts" in CALC.PlanIn.model_fields
