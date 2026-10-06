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
    out = F.races(plan, None, TODAY, predict=lambda e, c=None: None, gpx=no_gpx,
                   finish=lambda e, c: None)
    assert [r["event_id"] for r in out] == ["a", "b"]
    one = F.races(plan, None, TODAY, event_id="c", predict=lambda e, c=None: None, gpx=no_gpx,
                   finish=lambda e, c: None)
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
    monkeypatch.setattr(F, "trail_finish", lambda e, c: None)
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
    out = F.races(plan, None, TODAY, predict=lambda e, c=None: None, gpx=lambda e: None,
                   finish=lambda e, c: None)
    assert [r["event_id"] for r in out] == ["a"] and out[0]["readiness"]["checks"]
    c = F.races(plan, None, TODAY, event_id="c", predict=lambda e, c=None: None, gpx=lambda e: None,
                   finish=lambda e, c: None)[0]
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


def test_step_source_names_the_industry_rules():
    """SP-282: 24 months = the UTMB Index's validity / Hardrock's window, two classes = a little looser
    than the UTMB Finals' one category; only the ITRA ↔ UTMB mapping is 推估. The rule is unchanged."""
    s = F.SRC_STEP
    assert "24 個月" in s and "UTMB 指數的有效期" in s and "Hardrock" in s and "最多往上跳一級" in s
    assert s.count("推估") == 1 and "ITRA 兩級約等於 UTMB 一級，這個對應是推估" in s
    assert (F.STEP_MONTHS, F.STEP_OVER) == (24, 2)
    e = ev(distance_km=80, climbing_m=4500, est_hours=16.0)
    r = F.assess(e, line(e), TODAY, hist(km=90.0, climb=5000.0, hours=11.0), best={"ep": 30.0, "date": date(2026, 3, 1)})
    assert s in r["src"]


def _step(km, climb, best_ep):
    e = ev(distance_km=km, climbing_m=climb, est_hours=16.0)
    r = F.assess(e, line(e), TODAY, hist(km=90.0, climb=5000.0, hours=11.0), best={"ep": best_ep, "date": date(2026, 3, 1)})
    return r, next(c for c in r["checks"] if c["id"] == "step")


def test_step_shows_the_ep_multiple_but_judges_by_class_only():
    """SP-283: 「比賽最難那天是你 24 個月內最大單日的 N 倍」 shown; the level is still the classes'."""
    r, st = _step(80, 3000, 45.0)                         # EP 110 (M) vs 45 (S): one up, ×2.44
    assert st["ep_ratio"] == pytest.approx(110 / 45, abs=0.01) and st["level"] == "ok" and st["up"] == 1
    assert "最大單日的 2.4 倍" in st["text"] and "24 個月內" in st["text"]
    assert "級數只高一級，但距離和爬升是兩倍以上" in st["text"] and "70 % 是推估" in st["text"]
    assert r["level"] != "over" and not any("低一級" in s for s in r["suggestions"])
    r, st = _step(80, 3000, 60.0)                         # ×1.83, one up: no reminder
    assert st["level"] == "ok" and "1.8 倍" in st["text"] and "兩倍以上" not in st["text"]
    r, st = _step(45, 3000, 44.0)                         # EP 75 (M) vs 44 (XS): two up at only ×1.7 → over
    assert st["level"] == "over" and st["up"] == 2 and st["ep_ratio"] == pytest.approx(75 / 44, abs=0.01)
    assert "兩倍以上" not in st["text"]
    r, st = _step(80, 4500, 30.0)                         # three up at ×4.2: over, the reminder is for one up only
    assert st["level"] == "over" and "兩倍以上" not in st["text"]
    r, st = _step(20, 500, 40.0)                          # EP 25 vs 40: below, ×0.6
    assert st["level"] == "ok" and st["ep_ratio"] == pytest.approx(25 / 40, abs=0.01)
    assert "兩倍以上" not in st["text"]
    # the same class at ≥ 2× (owner 2026-10-06): the reminder too, the level unchanged
    r, st = _step(400, 2000, 210.0)                       # EP 420 vs 210: both XXL, ×2.0
    assert st["up"] == 0 and st["level"] == "ok" and r["level"] != "over"
    assert "級數相同，但距離和爬升是兩倍以上" in st["text"] and "70 % 是推估" in st["text"]
    r, st = _step(300, 2000, 210.0)                       # EP 320 vs 210: both XXL, ×1.5
    assert st["up"] == 0 and "兩倍以上" not in st["text"]
    r, st = _step(20, 500, 0.0)                          # a zero best day: no multiple, the class still judged
    assert st["ep_ratio"] is None and "倍" not in st["text"] and st["level"] == "ok"


def test_weeks_to_grows_10_percent_with_a_recovery_week_every_4th():
    assert F.weeks_to(100.0, 90.0) == 0 and F.weeks_to(100.0, 100.0) == 0
    assert F.weeks_to(100.0, 121.0) == 2 and F.weeks_to(100.0, 133.1) == 3
    assert F.weeks_to(100.0, 140.0) == 5                  # week 4 is a recovery week: no growth
    assert F.weeks_to(0.0, 10.0) is None and F.weeks_to(1.0, 1e9) is None


def test_step_over_gives_milestones_and_a_date_not_years():
    """SP-284: over → a race one class lower (its EP range) and the weekly volume (UA's EP, Koop's
    hours for an ultra) with the date it's reached at +10 % a week; never 「N 年後」."""
    e = ev(distance_km=80, climbing_m=4500, est_hours=16.0)                  # EP 125: L; Koop's 9 h row
    best = {"ep": 30.0, "date": date(2026, 3, 1)}                             # XS
    r = F.assess(e, line(e), TODAY, hist(km=40.0, climb=1500.0, hours=6.0), best=best)
    ms = r["milestone"]
    assert lv(r, "step") == "over" and (ms["cls"], ms["ep_lo"], ms["ep_hi"]) == ("M", 75.0, 115.0)
    # EP 55 → 112.5 (90 %): 8 builds = week 10; 6 h → 9 h: 5 builds = week 6 → week 10 = 2026-12-14
    assert ms["unit"] == "ep" and ms["weekly"] == pytest.approx(112.5) and ms["koop_h"] == 9.0
    assert ms["weeks"] == 10 and ms["date"] == "2026-12-14"
    t = r["suggestions"][1]
    assert t == ms["text"] and "EP 75–115（M 級）" in t and "每週 9 小時（Koop）" in t and "最快 2026 年 12 月" in t
    assert "推估" in t and "年後" not in t and "幾年" not in t
    assert "這是週量練到的時間，不代表到時候跨級就一定判可以" in t
    assert "低一級（M）" in r["suggestions"][0]                                # the old advice stays first
    assert any("Hoffman 2013：第一場超馬前跑了 3–15 年都有" in s for s in r["src"])
    # the weekly volume already there: no weeks, no date
    r = F.assess(e, line(e), TODAY, hist(km=90.0, climb=5000.0, hours=11.0), best=best)
    ms = r["milestone"]
    assert ms["weeks"] == 0 and ms["date"] is None and "已經夠了" in ms["text"] and "最快" not in ms["text"]
    # no records lately: no date
    r = F.assess(e, line(e), TODAY, hist(km=0.0, climb=0.0, hours=0.0), best=best)
    assert r["milestone"]["date"] is None and "推算不出日期" in r["milestone"]["text"]
    # ok (one class up): no milestone
    r = F.assess(e, line(e), TODAY, hist(km=40.0, climb=1500.0, hours=6.0), best={"ep": 80.0, "date": date(2026, 3, 1)})
    assert lv(r, "step") == "ok" and "milestone" not in r and not any("里程碑" in s for s in r["suggestions"])
    assert not any("Hoffman 2013" in s for s in r["src"])


def test_step_milestone_of_a_road_race_is_in_km():
    e = ev(kind="road", distance_km=50, climbing_m=0, est_hours=5.0)          # EP 50: S; < 6 h → 100 %
    r = F.assess(e, line(e), TODAY, hist(km=20.0, climb=0.0, hours=2.0), best={"ep": 5.0, "date": date(2026, 3, 1)})
    ms = r["milestone"]
    assert lv(r, "step") == "over" and (ms["cls"], ms["unit"], ms["koop_h"]) == ("XS", "km", None)
    assert ms["weekly"] == pytest.approx(50 * F.WEEK_OK_SHORT) and "每週跑到 50 km（比賽距離的 100 %" in ms["text"]
    assert ms["date"] and "最快" in ms["text"]


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


# ---- SP-112 items 6–7: 百岳 climb rate and climb power ------------------------------------------

def wins(n, vam, hr, lthr=170.0, z=1000.0):
    return [{"vam": vam, "hr": hr, "lthr": lthr, "z": z, "g": 0.2} for _ in range(n)]


WALK75 = 150.0      # 75 % of a 200 max HR: 「走得順」 (SP-115 / owner 2026-10-06)


def rates_of(ws):
    return F.climb_rates(ws, WALK75)


def trip(**kw):
    a = dict(kind="baiyue", distance_km=16, climbing_m=1600, est_hours=8.0, start="2026-12-05")
    a.update(kw)
    return ev(**a)


def test_climb_rates_by_hr_band_and_the_shrinkage():
    r = rates_of(wins(30, 500.0, 150.0) + wins(10, 650.0, 165.0))
    assert r["steady"]["vam"] == 500.0 and r["steady"]["n"] == 30 and r["hard"]["vam"] == 650.0
    assert r["steady_max"] == 150


def test_steady_is_hr_at_most_75_percent_of_max_hr():
    """SP-112's 走得順 = HR ≤ 75 % HRmax (SP-115's walking cap; owner 2026-10-06), no longer AeT … 0.95
    LTHR: a window at 155 bpm (< 0.95 × 170) was steady before and is not now; 走得辛苦 unchanged."""
    ws = wins(20, 480.0, 145.0) + wins(20, 560.0, 155.0) + wins(10, 650.0, 165.0)
    r = F.climb_rates(ws, 150.0)
    assert r["steady"]["n"] == 20 and r["steady"]["vam"] == 480.0
    assert r["hard"]["n"] == 10 and r["hard"]["vam"] == 650.0
    assert F.climb_rates(ws, 160.0)["steady"]["n"] == 40                       # a higher max HR, more windows
    none = F.climb_rates(ws)                                                    # no max HR / age: 山本's default
    assert none["steady"]["n"] == 0 and none["steady"]["vam"] is None and none["hard"]["n"] == 10
    assert "75 %" in F.SRC_VAM and "AeT 到 0.95 LTHR" not in F.SRC_VAM


def test_baiyue_inputs_use_the_walk_cap(monkeypatch):
    from backend.engine import hr_profile as HP
    from backend.engine.planning import Plan
    from backend.engine.racepower import athlete as A
    monkeypatch.setattr(A, "hike_workouts", lambda ds, today: [])
    monkeypatch.setattr(A, "hike_hr_windows", lambda ds, hikes: (wins(5, 480.0, 148.0) + wins(5, 560.0, 156.0), {}))
    monkeypatch.setattr(HP, "walk_cap_for", lambda ds, day, aet, profile=None: {"value": 150, "mhr": 200})
    climb, _p = F.baiyue_inputs(Plan(), object(), TODAY, trip())
    assert climb["rates"]["steady"]["n"] == 5 and climb["rates"]["steady_max"] == 150
    assert F._shrink(500.0, 30, 430.0) == pytest.approx((30 * 500 + 10 * 430) / 40)
    assert F._shrink(None, 0, 430.0) == 430.0
    assert F.pack_factor(0.10) == 1.0 and F.pack_factor(0.0) == pytest.approx(475 / 430)
    assert F.pack_factor(0.2) == pytest.approx(395 / 430)


def test_vam_ok_tight_and_faster_than_hard():
    e = trip()                                            # 16 km ↑1600 in 8 h: EP 32, climb share 0.5
    hd = {"km": 16.0, "climb_m": 1600.0, "hours": 8.0}
    rates = rates_of(wins(40, 500.0, 150.0) + wins(40, 700.0, 165.0))
    vc = F.vam_check(e, hd, rates, None, None, 100.0)
    assert vc["need"] == 400 and vc["level"] == "ok" and "沒有背包資料" in vc["text"]
    hd2 = {"km": 10.0, "climb_m": 2000.0, "hours": 5.0}    # 2000 / (5 × 0.667) = 600 m/h
    vc = F.vam_check(trip(), hd2, rates, None, None, 100.0)
    assert vc["level"] == "tight" and "接近閾值" in vc["text"]
    vc = F.vam_check(trip(cutoff_hours=3.0), hd2, rates, None, None, 100.0)     # before the turnaround: 1000
    assert vc["level"] == "tight" and "最用力的爬升還快" in vc["text"]


def test_vam_few_windows_use_yamamoto_altitude_and_pack():
    hd = {"km": 16.0, "climb_m": 1600.0, "hours": 8.0}
    vc = F.vam_check(trip(), hd, rates_of(wins(2, 700.0, 150.0)), None, None, 100.0)
    assert "資料不足（2 段）" in vc["text"] and vc["steady"] < 480                 # pulled to 430
    high = F.vam_check(trip(), hd, rates_of(wins(40, 500.0, 150.0, z=1000.0)), 3000.0, None, 100.0)
    low = F.vam_check(trip(), hd, rates_of(wins(40, 500.0, 150.0, z=1000.0)), None, None, 100.0)
    assert high["steady"] == pytest.approx(low["steady"] * (1 - 0.063 * 2), abs=1) and "Wehrlin" in high["text"]
    packed = F.vam_check(trip(pack_kg=14.0), hd, rates_of(wins(40, 500.0, 150.0)), None, 70.0, 100.0)
    assert packed["steady"] < low["steady"] and "背包 14 kg" in packed["text"]


def test_baiyue_checks_in_assess_never_over():
    e = trip(distance_km=10, climbing_m=2000, est_hours=5.0)
    rates = rates_of(wins(40, 300.0, 150.0) + wins(40, 350.0, 165.0))
    r = F.assess(e, line(e), TODAY, hist(km=40.0, climb=3000.0), climb={"rates": rates, "top_m": None, "weight": 70.0},
                 power={"cp": 70.0, "kg": 70.0})
    by = {c["id"]: c for c in r["checks"]}
    assert by["vam"]["level"] == "tight" and by["power"]["level"] == "tight" and "低於 1.2" in by["power"]["text"]
    assert r["level"] != "over"
    r = F.assess(e, line(e), TODAY, hist(km=40.0, climb=3000.0), power={"cp": 120.0, "kg": 70.0})
    assert next(c for c in r["checks"] if c["id"] == "power")["level"] == "ok"
    r = F.assess(e, line(e), TODAY, hist(km=40.0, climb=3000.0))
    assert not any(c["id"] in ("vam", "power") for c in r["checks"])               # no power meter: not shown
    road = ev(kind="road", distance_km=21.1, climbing_m=0, est_hours=2.0)
    r = F.assess(road, line(road), TODAY, hist(), climb={"rates": rates}, power={"cp": 300.0, "kg": 70.0})
    assert not any(c["id"] in ("vam", "power") for c in r["checks"])               # 百岳 only


def test_baiyue_inputs_need_a_power_meter():
    from backend.engine.planning import Plan, Threshold, Weight
    plan = Plan(events=[], thresholds=[Threshold(date="2026-01-01", cp=250.0)], weights=[Weight("2026-01-01", 65.0)])
    e = trip()
    climb, power = F.baiyue_inputs(plan, None, TODAY, e)
    assert climb is None and power is None                                          # no ds; no power meter
    plan.profile = {"power_meter": "stryd"}
    assert F.baiyue_inputs(plan, None, TODAY, e)[1] == {"cp": 250.0, "kg": 65.0}


# ---- SP-220: the cutoff of a trail race = the calculator's moving time + the stops ----------------

def fin(moving_h=7.0, stop_h=0.0, src=None, **kw):
    return {"moving_h": moving_h, "stop_h": stop_h, "stop_src": src, "stop_user_min": kw.pop("user_min", 0.0),
            "short_min": kw.pop("short_min", None), "n_runs": kw.pop("n_runs", None), "effort": kw.pop("effort", 1.0),
            **kw}


def test_trail_cutoff_adding_the_stops_turns_ok_into_tight():
    """SP-220 acceptance: moving 7.0 h against an 8 h cutoff is 87.5 % (ok); with 30 minutes of aid
    stations it is 7.5 h = 93.8 % (tight). The race line's own hours (the old CP + Riegel time) are
    not used while the HR model gives a moving time."""
    e = ev(cutoff_hours=8.0)
    ln = line(e, [5.0])
    r = F.assess(e, ln, TODAY, hist(km=40.0, climb=2000.0), finish=fin(7.0))
    c = next(c for c in r["checks"] if c["id"] == "cutoff")
    assert c["level"] == "ok" and c["finish_h"] == 7.0 and c["moving_h"] == 7.0 and c["stop_h"] == 0.0
    assert "沒有算停留" in c["text"] and c["time_method"] == "trail_hr"
    r = F.assess(e, ln, TODAY, hist(km=40.0, climb=2000.0), finish=fin(7.0, 0.5, "user", user_min=30.0))
    c = next(c for c in r["checks"] if c["id"] == "cutoff")
    assert c["level"] == "tight" and c["finish_h"] == 7.5 and c["stop_src"] == "user"
    assert "移動 7.0 小時" in c["text"] and "停留 30 分鐘" in c["text"] and "7.5 小時" in c["text"]
    assert "補給站 30 分鐘" in c["text"] and "沒有算停留" not in c["text"]


def test_trail_cutoff_stops_from_past_races_and_over():
    e = ev(cutoff_hours=7.0)
    f = fin(6.5, 0.75, "history", n_runs=4, short_min=12.0)
    r = F.assess(e, line(e, [5.0]), TODAY, hist(km=40.0, climb=2000.0), finish=f)
    c = next(c for c in r["checks"] if c["id"] == "cutoff")
    assert c["level"] == "over" and "過去 4 場比賽" in c["text"] and "關門" in r["suggestions"][0]
    f = fin(5.0, 0.5, "user", user_min=20.0, n_runs=3, short_min=10.0)
    c = next(c for c in F.assess(e, line(e, [5.0]), TODAY, hist(km=40.0, climb=2000.0), finish=f)["checks"]
             if c["id"] == "cutoff")
    assert "補給站 20 分鐘" in c["text"] and "零碎停頓約 10 分鐘" in c["text"] and "3 場" in c["text"]


def test_trail_cutoff_without_an_hr_model_falls_back_and_says_so():
    e = ev(cutoff_hours=8.0)
    r = F.assess(e, line(e, [6.0]), TODAY, hist(km=40.0, climb=2000.0),
                 finish=fin(None, 0.5, "user", user_min=30.0, fallback="power"))
    c = next(c for c in r["checks"] if c["id"] == "cutoff")
    assert c["moving_h"] == 6.0 and c["finish_h"] == 6.5 and c["time_method"] == "power"
    assert "功率模型" in c["text"] and "停留 30 分鐘" in c["text"]
    c = next(c for c in F.assess(e, line(e, [6.0]), TODAY, hist(km=40.0, climb=2000.0),
                                 finish=fin(None, fallback="plan"))["checks"] if c["id"] == "cutoff")
    assert "賽季計畫" in c["text"] and c["time_method"] == "plan"


def test_trail_cutoff_says_a_lower_effort_target():
    e = ev(cutoff_hours=10.0)
    c = next(c for c in F.assess(e, line(e, [6.0]), TODAY, hist(km=40.0, climb=2000.0),
                                 finish=fin(7.0, effort=0.9))["checks"] if c["id"] == "cutoff")
    assert "努力目標 90%" in c["text"]


def test_calc_stops_as_the_page_sends_them():
    inp = {"stops": [{"km": 5.0, "type": "aid", "minutes": None}, {"km": 10.0, "type": "big", "minutes": 8},
                     {"km": 12.0, "type": "water", "minutes": ""}, {"km": 0.0, "type": "aid", "minutes": 3},
                     {"km": 40.0, "type": "aid", "minutes": 3}, {"km": 20.0, "type": "bogus", "minutes": 1},
                     {"km": 25.0, "minutes": 1}, {"km": 25.02, "minutes": 1}, {"km": "x"}, "junk"]}
    got = F.calc_stops(inp, 30.0)
    assert [(s["km"], s["type"], s["minutes"]) for s in got] == [
        (5.0, "aid", 2.0), (10.0, "big", 8.0), (12.0, "water", 0.5), (20.0, "aid", 1.0)]
    assert F.calc_stops({}, 30.0) == [] and F.calc_stops({"stops": "x"}, 30.0) == []
    assert F.calc_effort({"S": {"mode": "auto", "effort": 0.95}}) == 0.95
    assert F.calc_effort({"S": {"mode": "time", "effort": 0.95}}) == 1.0 and F.calc_effort({}) == 1.0


def _course(filename=None):
    return {"totals": {"km": 30.0, "gain_m": 2000.0, "loss_m": 1900.0}, "days": [], "filename": filename,
            "descent_assumed": filename is None}


def test_trail_finish_takes_the_calculators_moving_time_and_stops(monkeypatch):
    seen = {}
    monkeypatch.setattr(F, "calc_inputs", lambda e: {"S": {"mode": "auto", "effort": 1}, "stops": [
        {"km": 10.0, "type": "aid", "minutes": 4}, {"km": 20.0, "type": "big", "minutes": None}]})

    def plan(body):
        seen["body"] = body
        return {"summary": {"total_method": "trail_hr", "time_s": 6.0 * 3600, "stops_s": 540.0,
                            "nonmoving": {"total_s": 900.0, "short_s": 360.0, "method": "user", "n_runs": 3}}}
    out = F.trail_finish(ev(cutoff_hours=8.0), _course("race.gpx"), plan_fn=plan)
    b = seen["body"]
    assert b.type == "trail" and b.mode == "auto" and b.course.event_id == "e1" and b.course.manual is None
    assert [(s.km, s.minutes) for s in b.stops] == [(10.0, 4.0), (20.0, 5.0)]
    assert out["moving_h"] == 6.0 and out["stop_h"] == 0.25 and out["stop_src"] == "user"
    assert out["stop_user_min"] == 9.0 and out["n_runs"] == 3 and out["short_min"] == 6.0


def test_trail_finish_without_a_gpx_profile_or_hr_model(monkeypatch):
    monkeypatch.setattr(F, "calc_inputs", lambda e: {"stops": [{"km": 10.0, "type": "aid", "minutes": 6}]})
    seen = {}

    def plan(body):
        seen["body"] = body
        return {"summary": {"total_method": "v1", "time_s": 5.0 * 3600, "stops_s": 360.0, "nonmoving": None}}
    out = F.trail_finish(ev(cutoff_hours=8.0), _course(), plan_fn=plan)
    assert seen["body"].course.manual == {"km": 30.0, "gain": 2000.0, "loss": None}
    assert out["moving_h"] is None and out["stop_h"] == 0.1 and out["stop_src"] == "user"

    def boom(body):
        raise ValueError("沒有 CP")
    out = F.trail_finish(ev(cutoff_hours=8.0), _course(), plan_fn=boom)
    assert out["moving_h"] is None and out["stop_h"] == 0.1
    monkeypatch.setattr(F, "calc_inputs", lambda e: {})
    out = F.trail_finish(ev(cutoff_hours=8.0), _course(), plan_fn=lambda b: {
        "summary": {"total_method": "trail_hr", "time_s": 3600.0, "nonmoving": {
            "total_s": 600.0, "short_s": 120.0, "method": "rate", "n_runs": 5}}})
    assert out["stop_src"] == "history" and out["stop_h"] == pytest.approx(600 / 3600)


def test_races_use_the_trail_finish_for_one_piece_trail_races_only(monkeypatch):
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: hist(km=40.0, climb=2000.0, n=weeks))
    monkeypatch.setattr(F, "activity_rows", lambda ds, today, days=42: [])
    plan = Plan(events=[ev(eid="t", cutoff_hours=8.0), ev(eid="r", kind="road", distance_km=42.2, climbing_m=100,
                                                         est_hours=4.0, cutoff_hours=6.0),
                        ev(eid="s", days=3, race_format="stage", cutoff_hours=30.0, est_hours=20.0),
                        ev(eid="n", cutoff_hours=None)])
    asked = []

    def finish(e, course):
        asked.append(e.id)
        return fin(7.0, 0.5, "user", user_min=30.0)
    out = {r["event_id"]: r for r in F.races(plan, None, TODAY, predict=lambda e, c=None: [5.0],
                                             gpx=lambda e: None, finish=finish)}
    assert asked == ["t", "n"]                          # every trail race in one piece, cutoff or not
    ct = next(c for c in out["t"]["checks"] if c["id"] == "cutoff")
    assert ct["finish_h"] == 7.5 and ct["level"] == "tight"
    # owner 2026-10-06: the other checks read the same HR-model moving time, not the CP + Riegel 5 h
    assert out["n"]["race_day"]["hours"] == 7.0 and out["t"]["race_day"]["hours"] == 7.0
    assert out["s"]["race_day"]["hours"] != 7.0
    wk = next(c for c in out["n"]["checks"] if c["id"] == "weekly")
    assert wk["ok_at"] == F.WEEK_OK_LONG                # 7 h ≥ 6 h: a long event (the old 5 h was short)
    cr = next(c for c in out["r"]["checks"] if c["id"] == "cutoff")
    assert "moving_h" not in cr and "預估" in cr["text"]
    # the calculator gives no moving time: the old (CP + Riegel) hours, said so
    out = F.races(plan, None, TODAY, event_id="t", predict=lambda e, c=None: [5.0], gpx=lambda e: None,
                  finish=lambda e, c: None)
    ct = next(c for c in out[0]["checks"] if c["id"] == "cutoff")
    assert ct["finish_h"] == 5.0 and ct["time_method"] == "power" and "功率模型" in ct["text"]


def test_tight_weekly_advice_no_longer_says_slow_the_first_half():
    """SP-224 (SP-198 §2.3, §4.6): nothing supports slowing the first half for runners near the cutoff."""
    e = ev(est_hours=3.0)
    r = F.assess(e, line(e), date(2027, 1, 4), hist(km=30.0, climb=1900.0))
    assert lv(r, "weekly") == "tight"
    assert "照分段的心率上限跑，補給站少停" in r["suggestions"]
    assert not any("前半段" in s for s in r["suggestions"])


def test_calculator_page_names_the_trail_strategy_and_the_hr_fade():
    """SP-224: the 「even」 button reads 均勻努力 on trail, 均速 on road; the HR cap note is in the chart's
    ? and, for a trail race on the HR chart, in the legend under it (visible on a phone)."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "static"
    page = (root / "racepower.html").read_text("utf-8")
    assert 'T(t === "trail" ? "strategy.even_trail" : "strategy.even_road")' in page
    assert 'm === "hr" && P.type === "trail"' in page and 'T("legend.hr_fade")' in page
    assert 'data-i18n="racepower.strategy.tip"' in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((root / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert all(cat.get(k) for k in ("strategy.even_road", "strategy.even_trail", "strategy.tip", "legend.hr_fade"))
    zh = json.loads((root / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    assert zh["strategy.even_trail"] == "均勻努力" and zh["strategy.even_road"] == "均速"
    assert "後段心率會自己下降" in zh["legend.hr_fade"] and "後段心率會自己下降" in zh["chart.tip"]
