"""
SP-114 slice 2: 賽制 of a ≥ 2 day 越野賽／其他 — 分站 (each stage's numbers, as 百岳) or 連續 (one
course, no per-day numbers, sleep points in the race calculator). A 連續 race's hardest stretch is the
one between two sleep points; without them the whole race is one piece, with a hint.
"""
import datetime as dt

from backend.engine import planning as P
from backend.engine import race_feasibility as F
from backend.engine.panels import race_refs as RR
from backend.engine.racepower import calc as C
from backend.engine.racepower import fuel as FU
from backend.engine.racepower import planner as PL

TODAY = dt.date(2026, 10, 5)


def up(**kw):
    a = {"name": "環島超馬", "date": "2027-03-10", "kind": "race", "days": 2, "distance_km": 160, "climbing_m": 8000,
         "est_hours": 40.0, "race_format": "continuous"}
    a.update(kw)
    return P.Plan().upsert_event(a)


def test_the_format_is_kept_only_where_it_is_asked():
    assert up().race_format == "continuous"
    assert up(race_format="bogus", day_plan=[{"km": 80, "gain_m": 4000}] * 2).race_format == "stage"
    assert up(days=1).race_format is None                                 # a one-day race: not asked
    assert up(kind="road").race_format is None                            # 路跑: not asked
    e = up(kind="baiyue", day_plan=[{"km": 80, "gain_m": 4000}] * 2)
    assert e.race_format is None and not e.continuous                     # 百岳 is always split by day


def test_sleep_is_a_stop_type_and_its_time_is_in_the_eta():
    assert FU.STOP_TYPES["sleep"]["minutes"] == 90.0 and FU.stop_type({"type": "sleep"}) == "sleep"
    assert C.StopIn(km=80.0, minutes=120, type="sleep").type == "sleep"
    stops = [{"km": 30.0, "minutes": 2.0, "type": "aid"}, {"km": 80.0, "minutes": 120.0, "type": "sleep"}]
    assert PL._stops_before(stops, 100.0) == (2.0 + 120.0) * 60.0          # the ETA after it carries the sleep


def test_no_sleep_points_one_piece_and_the_hint():
    e = up()
    c = RR.course_of(e, lambda e: None)
    sc, note = RR.sleep_course(e, c, [])
    assert sc is c and len(sc["days"]) == 1 and "睡眠點" in note
    assert RR.sleep_course(up(race_format="stage", day_plan=[{"km": 80, "gain_m": 4000}] * 2), c, [50.0]) == (c, None)


def test_the_hardest_stretch_is_between_two_sleep_points():
    e = up()
    c = RR.course_of(e, lambda e: None)
    sc, note = RR.sleep_course(e, c, [40.0, 130.0, 999.0])                 # a km past the finish is ignored
    assert note is None and sc["split_source"] == "sleep"
    assert [round(d["km"]) for d in sc["days"]] == [40, 90, 30]
    assert round(sc["days"][1]["gain_m"]) == 4500                          # no GPX: the climb follows the km (推估)
    ln = RR.race_line(e, [40.0], "x", sc)
    hd = F.hardest_day(ln)
    assert hd["km"] == 90.0 and hd["hours"] < 40.0
    assert "第 2 段" in RR.hardest_stretch_note(ln)


def test_races_say_which_stretch_or_ask_for_sleep_points(monkeypatch):
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: [
        {"monday": "2026-09-07", "km": 80.0, "climb_m": 4000.0, "hours": 10.0}] * weeks)
    monkeypatch.setattr(F, "activity_rows", lambda ds, today, days=42: [])
    plan = P.Plan(events=[up()])
    run = lambda: F.races(plan, None, TODAY, predict=lambda e, c=None: [40.0], gpx=lambda e: None)[0]
    monkeypatch.setattr(RR, "sleep_kms", lambda e: [])
    r = run()
    assert "睡眠點" in r["split_note"] and r["race_day"]["km"] == 160.0
    monkeypatch.setattr(RR, "sleep_kms", lambda e: [40.0, 130.0])
    r = run()
    assert "split_note" not in r and "第 2 段" in r["stretch_note"] and r["race_day"]["km"] == 90.0


# ---- slice 5: an ultra without its GPX gets a reminder, never a block --------------------------

def test_an_ultra_without_a_gpx_is_reminded(monkeypatch):
    assert up(days=1, race_format=None, distance_km=50).gpx_recommended
    assert up(days=1, kind="other", distance_km=60).gpx_recommended
    assert not up(days=1, distance_km=42).gpx_recommended                  # under 50 km
    assert not up(days=1, kind="road", distance_km=100).gpx_recommended    # 路跑
    e = up(days=1, distance_km=55, climbing_m=3000, est_hours=9.0)
    assert P.event_json(e, TODAY)["gpx_recommended"]
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: [
        {"monday": "2026-09-07", "km": 80.0, "climb_m": 4000.0, "hours": 10.0}] * weeks)
    monkeypatch.setattr(F, "activity_rows", lambda ds, today, days=42: [])
    run = lambda gpx: F.races(P.Plan(events=[e]), None, TODAY, predict=lambda e, c=None: None, gpx=gpx)[0]
    assert "GPX" in run(lambda e: None)["gpx_note"]
    with_gpx = lambda e: {"totals": {"km": 55.0, "gain_m": 3000.0, "loss_m": 3000.0}, "split_source": "single",
                          "days": [{"day": 1, "km": 55.0, "gain_m": 3000.0, "loss_m": 3000.0}], "filename": "x.gpx"}
    assert "gpx_note" not in run(with_gpx)
