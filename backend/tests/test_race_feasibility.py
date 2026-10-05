"""
engine/race_feasibility.py (SP-105): weekly volume vs the race's hardest day (UA), Koop's
minimum hours, the cutoff / the 百岳 summit vs the turnaround, < 3 weeks. Synthetic events
and weeks; nothing reads the athlete's data.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import race_feasibility as F
from backend.engine.panels import race_refs as RR
from backend.engine.planning import Event, Plan

TODAY = date(2026, 10, 5)               # a Monday


def ev(start="2027-01-30", eid="e1", **kw):
    a = dict(kind="race", priority="A", distance_km=30, climbing_m=2000, est_hours=6.0)
    a.update(kw)
    return Event(eid, "測試賽", start, **a)


def hist(km=40.0, climb=1500.0, hours=6.0, n=4):
    return [{"monday": (TODAY - dt.timedelta(weeks=n - i)).isoformat(), "km": km, "climb_m": climb, "hours": hours}
            for i in range(n)]


def line(e, hours=None):
    return RR.race_line(e, hours, "x")


def lv(r, cid):
    return next(c["level"] for c in r["checks"] if c["id"] == cid)


def test_weeks_ahead_grow_10_percent_with_a_recovery_week_every_4th():
    ws = F.weeks_ahead(TODAY, date(2026, 11, 28))       # race Sat; window ends the week holding 11/7
    assert ws[0]["monday"] == date(2026, 10, 12)
    assert [w["recovery"] for w in ws[:4]] == [False, False, False, True]
    inw = [w for w in ws if w["in_window"]]
    assert inw[-1]["monday"] == date(2026, 11, 2)       # race − 21 days = 11/7, its Monday
    pk = F.peak_week({"km": 40.0, "climb_m": 1000.0, "hours": 5.0}, ws)
    assert pk["builds"] == 3 and pk["km"] == pytest.approx(40 * 1.1 ** 3)


def test_base_week_is_the_mean_or_the_last_week_if_higher():
    h = hist(km=40.0)
    h[-1]["km"] = 60.0
    assert F.base_week(h)["km"] == 60.0
    h[-1]["km"] = 20.0
    assert F.base_week(h)["km"] == pytest.approx((40 * 3 + 20) / 4)


def test_ok_when_the_peak_week_reaches_ua_90_percent():
    e = ev()                                             # 30 km ↑2000 in 6 h: a longer event (≥ 6 h)
    r = F.assess(e, line(e), TODAY, hist(km=40.0, climb=2000.0))
    assert r["level"] == "ok" and lv(r, "weekly") == "ok"
    w = next(c for c in r["checks"] if c["id"] == "weekly")
    assert w["ok_at"] == F.WEEK_OK_LONG and w["ratio"] >= 0.9
    assert not r["suggestions"]


def test_shorter_events_need_more_than_100_percent():
    e = ev(est_hours=3.0)
    r = F.assess(e, line(e), date(2027, 1, 4), hist(km=30.0, climb=1900.0))   # ~4 weeks: little growth
    w = next(c for c in r["checks"] if c["id"] == "weekly")
    assert w["ok_at"] == F.WEEK_OK_SHORT and 0.9 <= w["ratio"] < 1.0 and w["level"] == "tight"


def test_below_half_is_tight_with_a_downgrade_never_over():
    """SP-112: the weekly volume does not tell finishers from non-finishers (Hoffman & Fogard 2011):
    at most tight, the shorter course only suggested."""
    e = ev(distance_km=100, climbing_m=6000, est_hours=20.0, start="2026-12-12")
    r = F.assess(e, line(e), TODAY, hist(km=20.0, climb=500.0, hours=3.0))
    assert lv(r, "weekly") == "tight" and r["level"] != "over"
    d = r["downgrade"]
    assert 0 < d["km"] < 100 and 0 < d["climb_m"] < 6000
    assert any("短一點的組別" in s for s in r["suggestions"]) and not any("先不跑" in s for s in r["suggestions"])
    assert any("Hoffman" in s for s in r["src"])


def test_the_hardest_day_not_the_mean_for_a_multi_day_trip():
    e = ev(kind="baiyue", days=2, distance_km=30, climbing_m=2400, est_hours=16)
    ln = line(e)
    ln["per_day"][0].update(km=20.0, climb_m=2000, hours=10.0, cc=40.0)
    ln["per_day"][1].update(km=10.0, climb_m=400, hours=6.0, cc=15.0)
    assert F.hardest_day(ln)["km"] == 20.0


def test_koop_minimum_for_ultras_only():
    e = ev(distance_km=100, climbing_m=5000, est_hours=16.0)
    r = F.assess(e, line(e), TODAY, hist(km=80.0, climb=4000.0, hours=10.0))
    assert lv(r, "hours") == "ok" and r["koop"]["need_h"] == 9.0
    r = F.assess(e, line(e), TODAY, hist(km=80.0, climb=4000.0, hours=4.0))
    assert lv(r, "hours") == "tight"                     # never worse than tight
    e2 = ev(distance_km=30, est_hours=4.5)               # 馬拉松級: no Koop
    assert not any(c["id"] == "hours" for c in F.assess(e2, line(e2), TODAY, hist())["checks"])


def test_koop_reads_the_race_size_not_the_km():
    """SP-111: 30 km ↑2000 m in 7 h is an ultra (Koop's 50 km row); a flat 55 km road in 4.5 h is
    not; 70 km ↑3500 m (EP 105) takes the 100 km row; a 100 英里級 by time too."""
    e = ev(distance_km=30, est_hours=7.0)
    assert F.koop_need(line(e), e) == F.KOOP[1]
    e = ev(kind="road", distance_km=55, climbing_m=0, est_hours=4.5)
    assert F.koop_need(line(e), e) is None                 # road: not Koop's anyway
    e = ev(kind="other", distance_km=55, climbing_m=0, est_hours=4.5)
    assert F.koop_need(line(e), e) is None                 # 馬拉松級 by time
    e = ev(distance_km=70, climbing_m=3500, est_hours=12.0)
    assert F.koop_need(line(e), e) == F.KOOP[0]
    e = ev(distance_km=80, climbing_m=1000, est_hours=21.0)
    assert F.koop_need(line(e), e) == F.KOOP[0]


def test_late_under_three_weeks_suggests_b_or_c():
    e = ev(start=(TODAY + dt.timedelta(days=14)).isoformat())
    r = F.assess(e, line(e), TODAY, hist(km=80.0, climb=4000.0))
    assert r["level"] == "late" and any("B 或 C" in s for s in r["suggestions"])
    assert e.priority == "A"                              # only suggested, never changed


def test_c_races_are_not_assessed():
    e = ev(priority="C")
    r = F.assess(e, line(e), TODAY, hist(km=1.0))
    assert r.get("skipped") and not r["checks"]


def test_no_training_data_is_unknown_not_over():
    e = ev()
    r = F.assess(e, line(e), TODAY, hist(km=0.0, climb=0.0, hours=0.0))
    assert r["level"] == "unknown" and lv(r, "weekly") == "unknown"


def test_race_cutoff():
    e = ev(cutoff_hours=6.5)
    r = F.assess(e, line(e, [6.0]), TODAY, hist(km=40.0, climb=2000.0))
    assert lv(r, "cutoff") == "tight"                    # 92 %
    e = ev(cutoff_hours=5.5)
    r = F.assess(e, line(e, [6.0]), TODAY, hist(km=40.0, climb=2000.0))
    assert lv(r, "cutoff") == "over" and r["level"] == "over"
    assert "關門" in r["suggestions"][0]


def test_summit_eta_and_the_turnaround():
    course = {"days": [{"day": 1, "km": 10.0, "gain_m": 1000.0, "loss_m": 1000.0}]}
    # out and back: summit at 5 km, all the climb before it → (5 + 10) / (10 + 10) of 8 h = 6 h
    s = F.summit_eta(course, [8.0], 5.0)
    assert s["hours"] == pytest.approx(6.0) and s["assumed_climb"]
    pieces = [{"day": 1, "start_km": 0.0, "end_km": 5.0, "gain_m": 1000.0},
              {"day": 1, "start_km": 5.0, "end_km": 10.0, "gain_m": 0.0}]
    assert F.summit_eta(course, [8.0], 2.5, pieces)["hours"] == pytest.approx(8 * (2.5 + 5) / 20)
    e = ev(kind="baiyue", distance_km=10, climbing_m=1000, est_hours=8, cutoff_hours=5.5)
    r = F.assess(e, line(e), TODAY, hist(km=30.0, climb=2000.0), s)
    assert lv(r, "cutoff") == "over" and "不適合現在的你" in r["suggestions"][0]
    e = ev(kind="baiyue", distance_km=10, climbing_m=1000, est_hours=8, cutoff_hours=6.2)
    assert lv(F.assess(e, line(e), TODAY, hist(km=30.0, climb=2000.0), s), "cutoff") == "tight"
    r = F.assess(e, line(e), TODAY, hist(km=30.0, climb=2000.0), None)
    assert lv(r, "cutoff") == "unknown"                   # no GPX, no summit km


def test_summit_on_the_second_day():
    course = {"days": [{"day": 1, "km": 12.0, "gain_m": 1200.0, "loss_m": 200.0},
                       {"day": 2, "km": 8.0, "gain_m": 600.0, "loss_m": 1600.0}]}
    s = F.summit_eta(course, [7.0, 6.0], 15.0)
    assert s["day"] == 2 and s["hours"] == pytest.approx(6.0 * (3 + 6) / (8 + 6))


def test_event_fields_are_validated():
    p = Plan()
    e = p.upsert_event({"name": "x", "date": "2027-01-01", "cutoff_hours": "12", "summit_km": ""})
    assert e.cutoff_hours == 12.0 and e.summit_km is None
    with pytest.raises(ValueError):
        p.upsert_event({"name": "x", "date": "2027-01-01", "cutoff_hours": -1})
    with pytest.raises(ValueError):
        p.upsert_event({"name": "x", "date": "2027-01-01", "summit_km": 0})


def test_races_lists_a_and_b_only(monkeypatch):
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: hist(km=40.0, climb=2000.0, n=weeks))
    monkeypatch.setattr(F, "activity_rows", lambda ds, today, days=42: [])
    plan = Plan(events=[ev(eid="a"), ev(eid="b", priority="B"), ev(eid="c", priority="C"),
                        ev(eid="old", start="2026-09-01")])
    no_gpx = lambda e: None
    out = F.races(plan, None, TODAY, predict=lambda e, c=None: None, gpx=no_gpx)
    assert [r["event_id"] for r in out] == ["a", "b"]
    one = F.races(plan, None, TODAY, event_id="c", predict=lambda e, c=None: None, gpx=no_gpx)
    assert one[0]["skipped"]


def test_api_lists_the_races_and_404s_an_unknown_event(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.api import overview as OA
    from backend.engine import planning as P
    from backend.engine.wko5expr.dataset import date_to_day
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    Plan(events=[ev(eid="a"), ev(eid="c", priority="C")]).save()
    monkeypatch.setattr(OA, "_dataset", lambda: type("DS", (), {"today": date_to_day(TODAY)})())
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: hist(km=40.0, climb=2000.0, n=weeks))
    monkeypatch.setattr(F, "activity_rows", lambda ds, today, days=42: [])
    monkeypatch.setattr(RR, "calculator_hours", lambda e, c=None: None)
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    r = OA.feasibility()
    assert r["today"] == TODAY.isoformat() and [x["event_id"] for x in r["races"]] == ["a"]
    assert r["races"][0]["level"] == "ok" and r["levels"]["over"]
    assert OA.feasibility(event_id="c")["races"][0]["skipped"]
    with pytest.raises(HTTPException):
        OA.feasibility(event_id="nope")


def test_late_does_not_repeat_the_tight_advice():
    e = ev(start=(TODAY + dt.timedelta(days=10)).isoformat(), est_hours=3.0)
    r = F.assess(e, line(e), TODAY, hist(km=25.0, climb=1500.0))
    assert lv(r, "weekly") == "tight" and r["level"] == "late"
    assert len(r["suggestions"]) == 1 and "B 或 C" in r["suggestions"][0]


def test_road_races_are_judged_and_described_on_km_only():
    e = ev(kind="road", distance_km=21.1, climbing_m=0, est_hours=2.0)
    r = F.assess(e, line(e), TODAY, hist(km=40.0, climb=700.0))
    w = next(c for c in r["checks"] if c["id"] == "weekly")
    assert w["ratio_climb"] is None and "爬升" not in w["text"] and "比賽距離的" in w["text"]
    assert r["label"] == "來得及"


def test_api_links_the_specific_phase_dashboard(monkeypatch):
    from backend.api import overview as OA
    monkeypatch.setattr("backend.engine.wko5expr.customviews.load_custom_views",
                        lambda: {"周期化訓練": {"dashboards": [{"id": "base", "charts": []}, {"id": "build", "charts": []}]}})
    assert OA._specific_chart_href() == "/api/v1/wko5/viewer?view=%E5%91%A8%E6%9C%9F%E5%8C%96%E8%A8%93%E7%B7%B4&dash=1"


def acts(*rows):
    """(date, km, climb, hours) foot activities."""
    return [{"date": d, "km": km, "climb_m": cl, "descent_m": cl, "hours": h, "minutes": h * 60, "idx": i, "foot": True}
            for i, (d, km, cl, h) in enumerate(rows)]


def test_readiness_long_day_by_course_constant_against_the_hardest_day():
    e = ev(est_hours=5.5)                                # < 6 h: no B2B check in the way
    ln = line(e)
    cc = ln["per_day"][0]["cc"]
    r = F.readiness(e, ln, TODAY, hist(km=60.0, climb=3000.0, n=9), acts((TODAY - dt.timedelta(days=5), 25, 1700, 5.0)))
    lg = next(c for c in r["checks"] if c["id"] == "long")
    assert lg["ratio"] >= F.LONG_OK and lg["level"] == "ok" and r["label"] == "準備好了"
    r = F.readiness(e, ln, TODAY, hist(km=60.0, climb=3000.0, n=9), acts((TODAY - dt.timedelta(days=5), 12, 600, 2.0)))
    assert next(c for c in r["checks"] if c["id"] == "long")["level"] == "short" and r["label"] == "還不夠"
    assert cc > 30


def test_readiness_caps_the_long_day_target_at_6_hours():
    e = ev(distance_km=80, climbing_m=5000, est_hours=16.0)
    r = F.readiness(e, line(e), TODAY, hist(km=60.0, climb=3000.0, n=9), acts((TODAY - dt.timedelta(days=3), 32, 2200, 6.0)))
    lg = next(c for c in r["checks"] if c["id"] == "long")
    assert lg["level"] == "ok" and "6 小時" in lg["text"]


def test_readiness_road_uses_km_and_old_sessions_do_not_count():
    e = ev(kind="road", distance_km=42.2, climbing_m=0, est_hours=3.5)
    old = TODAY - dt.timedelta(days=60)
    r = F.readiness(e, line(e), TODAY, hist(km=50.0, climb=0.0, n=9),
                    acts((old, 34, 0, 3.0), (TODAY - dt.timedelta(days=4), 25, 0, 2.2)))
    lg = next(c for c in r["checks"] if c["id"] == "long")
    assert "25.0 km" in lg["text"] and lg["level"] == "tight"          # 25 / 35 = 71 %: the 60-day 34 km is ignored
    assert not any(c["id"] == "b2b" for c in r["checks"])


def test_readiness_weekly_koop_and_b2b():
    e = ev(distance_km=100, climbing_m=5000, est_hours=16.0)
    h = hist(km=70.0, climb=4000.0, hours=10.0, n=9)
    sat = TODAY - dt.timedelta(days=9)
    r = F.readiness(e, line(e), TODAY, h, acts((sat, 30, 1500, 4.0), (sat + dt.timedelta(days=1), 25, 1200, 3.5)))
    by = {c["id"]: c for c in r["checks"]}
    assert by["hours"]["level"] == "ok"
    assert by["b2b"]["count"] == 1 and by["b2b"]["level"] == "tight"
    assert by["weekly"]["level"] in ("tight", "short")


def test_readiness_before_the_specific_phase_says_so():
    e = ev(start="2027-04-01")
    r = F.readiness(e, line(e), TODAY, hist(n=9), acts((TODAY - dt.timedelta(days=2), 20, 1000, 3.0)))
    assert r.get("note")


def test_races_attach_readiness_but_not_to_c_races(monkeypatch):
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: hist(km=40.0, climb=2000.0, n=weeks))
    monkeypatch.setattr(F, "activity_rows", lambda ds, today, days=42: acts((TODAY - dt.timedelta(days=3), 20, 1200, 4.0)))
    plan = Plan(events=[ev(eid="a"), ev(eid="c", priority="C")])
    out = F.races(plan, None, TODAY, predict=lambda e, c=None: None, gpx=lambda e: None)
    assert [r["event_id"] for r in out] == ["a"] and out[0]["readiness"]["checks"]
    c = F.races(plan, None, TODAY, event_id="c", predict=lambda e, c=None: None, gpx=lambda e: None)[0]
    assert "readiness" not in c


def test_a_multi_day_trip_is_a_long_event_and_warns_about_the_equal_split():
    """大小霸 on the NAS (2026-10-05): 3 days, 65 km ↑3500, no GPX — equal days of 21.7 km ↑1167 in
    ~4.7 h. Under 6 h a day, but a 3-day trip is a long event: UA's 90 %, not 100 %."""
    e = ev(kind="baiyue", days=3, distance_km=65, climbing_m=3500, est_hours=None, priority="B", start="2026-12-04")
    ln = line(e, [4.74, 4.74, 4.74])
    assert F.week_ok_at(e, ln) == F.WEEK_OK_LONG
    r = F.assess(e, ln, TODAY, hist(km=21.7, climb=743.0, hours=3.8))
    w = next(c for c in r["checks"] if c["id"] == "weekly")
    assert w["ok_at"] == F.WEEK_OK_LONG and w["ratio"] >= 0.9 and w["level"] == "ok"       # EP (SP-112)
    assert lv(r, "climb") == "ok"
    assert r["days"] == 3 and "平均分配" in r["split_note"]
    one = ev(est_hours=3.0)
    assert F.week_ok_at(one, line(one)) == F.WEEK_OK_SHORT and "split_note" not in F.assess(one, line(one), TODAY, hist())



# ---- SP-112: EP weekly ratio, the climb sub-check, 跨級 ------------------------------------------

def test_weekly_is_one_ep_ratio():
    e = ev()                                             # 30 km ↑2000: EP 50
    r = F.assess(e, line(e), TODAY, hist(km=30.0, climb=1500.0))
    w = next(c for c in r["checks"] if c["id"] == "weekly")
    pk = r["peak_week"]
    assert w["ratio"] == pytest.approx((pk["km"] + pk["climb_m"] / 100) / 50, abs=0.01) and w["ratio_ep"] == w["ratio"]
    assert "EP" in w["text"]


def test_weekly_40_percent_otherwise_fine_is_tight_not_over():
    e = ev(start="2026-11-14")
    r = F.assess(e, line(e), TODAY, hist(km=10.0, climb=700.0, hours=3.0),
                 best={"ep": 55.0, "date": date(2026, 5, 1)})
    w = next(c for c in r["checks"] if c["id"] == "weekly")
    assert w["ratio"] < 0.5 and w["level"] == "tight" and r["level"] == "tight"


def test_step_xs_best_against_an_l_race_is_over():
    e = ev(distance_km=80, climbing_m=4500, est_hours=16.0)                 # EP 125: L
    r = F.assess(e, line(e), TODAY, hist(km=90.0, climb=5000.0, hours=11.0),
                 best={"ep": 30.0, "date": date(2026, 3, 1)})                 # XS
    st = next(c for c in r["checks"] if c["id"] == "step")
    assert st["level"] == "over" and (st["best_class"], st["race_class"], st["up"]) == ("XS", "L", 3)
    assert r["level"] == "over" and "低一級（M）" in r["suggestions"][0] and "B／C 賽" in r["suggestions"][0]
    r = F.assess(e, line(e), TODAY, hist(km=90.0, climb=5000.0, hours=11.0), best={"ep": 80.0, "date": date(2026, 3, 1)})
    assert lv(r, "step") == "ok"                                              # M → L: one class up
    r = F.assess(e, line(e), TODAY, hist(km=90.0, climb=5000.0, hours=11.0))
    assert not any(c["id"] == "step" for c in r["checks"])                    # no records: not judged


def test_itra_classes_and_best_day():
    assert [F.ITRA_CLASSES[F.itra_class(x)][0] for x in (10, 25, 50, 80, 120, 160, 250)] == \
        ["XXS", "XS", "S", "M", "L", "XL", "XXL"]
    d = TODAY - dt.timedelta(days=100)
    b = F.best_day_ep(acts((d, 20, 1000, 3.0), (d, 5, 0, 0.5), (TODAY - dt.timedelta(days=3), 28, 500, 3.0)))
    assert b["ep"] == pytest.approx(35.0) and b["date"] in (d, TODAY - dt.timedelta(days=3))
    assert F.best_day_ep([]) is None


def test_enough_distance_but_40_percent_climb_is_tight():
    e = ev(start="2026-11-07")                                                # 30 km ↑2000
    r = F.assess(e, line(e), TODAY, hist(km=60.0, climb=600.0, hours=8.0))
    cl = next(c for c in r["checks"] if c["id"] == "climb")
    assert cl["ratio"] < 0.5 and cl["level"] == "tight" and "爬升練得比距離少" in cl["text"]
    assert lv(r, "weekly") == "ok" and r["level"] == "tight"


def test_over_only_from_the_cutoff_or_the_step():
    e = ev(distance_km=100, climbing_m=6000, est_hours=20.0, start="2026-12-12")
    r = F.assess(e, line(e), TODAY, hist(km=5.0, climb=100.0, hours=1.0))
    assert r["level"] in ("tight", "unknown") and all(c["level"] != "over" for c in r["checks"])
