"""
SP-87: 每週爬升量目標 (engine/vert_target.py; docs/research/trail-terrain-and-climb-sessions.md §7) —
Uphill Athlete's absolute rule before an A race with a climb: 賽前第 20 → 3 週 from 50 % of the race's
single-day climb (R) straight up to the peak (long race 95 %, short 110 %), ≤ +10 % a week, a recovery
week 50 % of the week before, 減量期 week 1 75 % of the peak, race week 40 %; 100 mi+: a 3-week taper
75 → 55 → 40 %. Display only (課表 week rows: target / planned / done / the gap). Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import vert_target as VT
from backend.engine.planning import Event

RACE = date(2027, 3, 27)                        # a Saturday


def ev(km=50, climb=3000, hours=8.0, kind="race", days=1, day_plan=None, start=RACE, priority="A"):
    return Event(id="e1", name="春季越野", date=start.isoformat(), kind=kind, priority=priority, distance_km=km,
                 climbing_m=climb, est_hours=hours, days=days, day_plan=day_plan)


def monday_out(w, race=RACE):
    """The Monday of 賽前第 w 週."""
    return race - dt.timedelta(days=race.weekday()) - dt.timedelta(weeks=w - 1)


def weeks(first_w, last_w=1, recovery=(), race=RACE):
    return [{"start": monday_out(w, race).isoformat(), "mode": "recovery_week" if w in recovery else "base"}
            for w in range(first_w, last_w - 1, -1)]


def test_race_climb_and_race_length(monkeypatch):
    from backend.engine.panels import race_refs as RR
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    assert VT.race_climb(ev()) == 3000
    trip = ev(km=30, climb=None, kind="baiyue", days=3, day_plan=[{"km": 10, "gain_m": 900}, {"km": 12, "gain_m": 1400},
                                                                 {"km": 8, "gain_m": 300}])
    assert VT.race_climb(trip) == 1400                       # the biggest single day
    assert VT.race_climb(ev(climb=0)) is None and VT.race_climb(ev(km=None, climb=None)) is None
    # a stored GPX wins (race_refs.course_of)
    monkeypatch.setattr(RR, "stored_course", lambda e: {"totals": {"km": 52.0, "gain_m": 3300, "loss_m": 3300},
                                                         "days": [{"day": 1, "km": 52.0, "gain_m": 3300, "loss_m": 3300}],
                                                         "split_source": "single"})
    assert VT.race_climb(ev()) == 3300
    # long = predicted ≥ 6 h; without a time ≥ 50 km (推估, §7.3)
    assert VT.is_long(ev(hours=6.0)) and not VT.is_long(ev(hours=5.5))
    assert VT.is_long(ev(km=50, hours=None)) and not VT.is_long(ev(km=42, hours=None))
    assert VT.hundred(ev(km=165)) and not VT.hundred(ev(km=100))


def test_the_long_race_example_of_the_report(monkeypatch):
    """§7.3: 50 km / 3,000 m (long): 1,500 m at week 20, ~2,300 at 10, 2,850 at the peak (week 3),
    ~2,150 in the taper's first week, ~1,150 in the race week — no recovery week here."""
    from backend.engine.panels import race_refs as RR
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    out = VT.plan(weeks(22), [ev()], avg4=None)
    t = {VT_w(k): v["target"] for k, v in out.items() if v.get("target") is not None}
    assert VT_w(monday_out(22).isoformat()) not in t and VT_w(monday_out(21).isoformat()) not in t
    assert t[20] == 1500 and t[10] == pytest.approx(2290, abs=10) and t[3] == 2850
    assert t[2] == pytest.approx(2140, abs=10) and t[1] == pytest.approx(1140, abs=10)
    assert all(t[w] <= t[w - 1] for w in range(20, 3, -1))            # a straight rise to the peak
    info = out[monday_out(3).isoformat()]
    assert info["race"]["name"] == "春季越野" and info["r"] == 3000 and info["long"] and info["weeks_out"] == 3
    # short race (25 km / 1,500 m, 3 h): 750 → 1,650 (110 %)
    out = VT.plan(weeks(20), [ev(km=25, climb=1500, hours=3.0)], avg4=None)
    t = {VT_w(k): v["target"] for k, v in out.items()}
    assert t[20] == 750 and t[3] == 1650


def VT_w(monday_iso):
    return VT.weeks_out(RACE, date.fromisoformat(monday_iso))


def test_recovery_weeks_the_ten_percent_cap_and_the_peak(monkeypatch):
    from backend.engine.panels import race_refs as RR
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    # 專項期 recovery weeks 5 and 3 (SP-97): half the week before; the peak is week 4's; after a recovery
    # week the target goes back to the line (the 10 % counts from the last normal week)
    out = VT.plan(weeks(8, recovery=(5, 3)), [ev()], avg4=None)
    t = {VT_w(k): v["target"] for k, v in out.items()}
    assert t[5] == pytest.approx(t[6] / 2, abs=10) and t[3] == pytest.approx(t[4] / 2, abs=10)
    assert out[monday_out(5).isoformat()]["recovery"]
    assert t[2] == pytest.approx(0.75 * t[4], abs=10) and t[1] == pytest.approx(0.40 * t[4], abs=10)
    # a late start: the last 4 weeks averaged 1,000 m → ≤ +10 % a week from there, the peak lower
    out = VT.plan(weeks(10), [ev()], avg4=1000.0)
    t = {VT_w(k): v["target"] for k, v in out.items()}
    assert t[10] == 1100 and t[9] == 1210 and t[8] == 1330
    assert out[monday_out(10).isoformat()]["capped"] and t[3] < 2850
    assert t[2] == pytest.approx(0.75 * t[3], abs=10)
    # more than the line already: the line
    out = VT.plan(weeks(10), [ev()], avg4=5000.0)
    assert out[monday_out(10).isoformat()]["target"] == pytest.approx(2290, abs=10)


def test_hundred_miles_tapers_three_weeks(monkeypatch):
    from backend.engine.panels import race_refs as RR
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    out = VT.plan(weeks(6), [ev(km=170, climb=9000, hours=30.0)], avg4=None)
    t = {VT_w(k): v["target"] for k, v in out.items()}
    peak = t[4]
    assert peak == pytest.approx(0.95 * 9000, abs=10)                # the peak a week earlier
    assert t[3] == pytest.approx(0.75 * peak, abs=10) and t[2] == pytest.approx(0.55 * peak, abs=10)
    assert t[1] == pytest.approx(0.40 * peak, abs=10)


def test_no_target_without_an_a_race_with_a_climb(monkeypatch):
    from backend.engine.panels import race_refs as RR
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    for evs in ([], [ev(kind="road", climb=200)], [ev(climb=0)], [ev(priority="B")]):
        assert all(v.get("target") is None for v in VT.plan(weeks(10), evs, avg4=None).values()), evs
    assert VT.plan(weeks(10), [ev()], avg4=None, road=True) == {}


def test_annotate_reads_the_plan_weeks_and_the_last_four_weeks(monkeypatch):
    """_build_inputs' pass: this week (week_plan) + the projected weeks, the 4 complete weeks before this
    one from the activity rows (foot activities only)."""
    from backend.engine.panels import race_refs as RR
    monkeypatch.setattr(RR, "stored_course", lambda e: None)
    mon = monday_out(10)
    cur = {"week": {"start": mon.isoformat()}, "mode": "specific", "primary_sport": "trail"}
    nxt = [{"start": (mon + dt.timedelta(weeks=1)).isoformat(), "mode": "specific"}]
    acts = [{"date": (mon - dt.timedelta(days=d)).isoformat(), "category": c, "climbing_m": m}
            for d, c, m in ((3, "trail", 1200), (10, "road", 400), (17, "hike", 800), (24, "trail", 0),
                            (5, "bike", 3000), (30, "trail", 9000), (1, "trail", None))]
    assert VT.avg_before(acts, mon) == pytest.approx((1200 + 400 + 800) / 4)
    out = VT.annotate(cur, nxt, [ev()], acts)
    assert out[mon.isoformat()]["target"] == round(1.1 * 600, -1)
    assert set(out) == {mon.isoformat(), nxt[0]["start"]}
    assert VT.annotate({**cur, "primary_sport": "road"}, nxt, [ev()], acts) == {}


def test_calendar_week_rows_show_the_target_planned_done_and_the_gap(monkeypatch):
    """The 課表 week rows (GET /calendar): `climb` = {target, planned (the week's sessions' climb_m),
    done (the foot activities' climb), short (target − planned, > 0)}; a week with no target shows the
    done / planned climb only; 主要訓練項目 路跑: no `climb` at all (the 課表 unchanged)."""
    from backend.api import plan_sessions
    from backend.tests.test_plan_store import API, Env
    acts = [{"index": 3, "date": "2026-09-29", "category": "trail", "category_label": "越野跑", "moving_s": 3600,
             "tss": 50, "climbing_m": 420},
            {"index": 4, "date": "2026-09-30", "category": "bike", "category_label": "自行車", "moving_s": 3600,
             "tss": 40, "climbing_m": 900}]
    monkeypatch.setattr(plan_sessions, "_range_extras",
                        lambda s, e: {"activities": [a for a in acts if s <= a["date"] <= e], "phases": []})
    with Env(monkeypatch) as e:
        long = next(s for s in e.inp["cur"]["sessions"] if s["id"] == "long")
        long["climb_m"] = 600.0
        e.inp["climb_targets"] = {"2026-09-28": {"target": 1500.0, "r": 3000, "share": 0.5, "weeks_out": 20,
                                                 "race": {"id": "e1", "name": "春季越野", "start": "2027-02-13"},
                                                 "long": True, "recovery": False, "capped": False, "taper": False}}
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-11").json()
        w0, w1 = b["week_rows"]
        c = w0["climb"]
        assert c["target"] == 1500 and c["planned"] == 600 and c["done"] == 420 and c["short"] == 900
        assert c["race"] == "春季越野" and c["weeks_out"] == 20
        assert w1["climb"] is None                                   # no target, nothing planned / done
        e.inp["cur"]["primary_sport"] = "road"
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-11").json()
        assert all("climb" not in w for w in b["week_rows"])
