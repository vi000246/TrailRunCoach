"""
SP-114 slice 3: a multi-day 百岳 is mountaineering — the race check judges it on the 攻頂日模擬 (one day
climbing the summit day's whole climb with a pack, 3 of them from 8 weeks out), not the weekly volume;
the 專項期 schedules the simulation; the taper is 7–10 days. A 單攻百岳 (entered as a trail race) and the
trail races keep their rules. Synthetic events and activities; nothing reads the athlete's data.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import baiyue_multiday as BM
from backend.engine import planning as P
from backend.engine import race_feasibility as F
from backend.engine import specific_phase as SP
from backend.engine.panels import race_refs as RR

TODAY = date(2026, 10, 5)                      # a Monday
DXB = [{"km": 20.0, "gain_m": 1500, "loss_m": 300}, {"km": 15.0, "gain_m": 900, "loss_m": 900},
       {"km": 30.0, "gain_m": 400, "loss_m": 2600}]


def trip(start="2026-12-19", **kw):
    a = dict(kind="baiyue", priority="A", days=3, distance_km=65, climbing_m=2800, est_hours=None, pack_kg=12.0,
             day_plan=DXB)
    a.update(kw)
    return P.Event("t", "大小霸", start, **a)


def line(e, hours=(8.0, 6.0, 7.0)):
    return RR.race_line(e, list(hours), "x", RR.course_of(e, lambda e: None))


def hist(km=40.0, climb=1500.0, hours=6.0, n=9):
    return [{"monday": (TODAY - dt.timedelta(weeks=n - i)).isoformat(), "km": km, "climb_m": climb, "hours": hours}
            for i in range(n)]


def row(days_ago, climb, pack=None):
    return {"date": TODAY - dt.timedelta(days=days_ago), "climb_m": climb, "km": 15.0, "hours": 5.0, "pack_kg": pack}


def ids(checks):
    return [c["id"] for c in checks]


def run(e, rows, today=TODAY):
    ln = line(e)
    r = F.assess(e, ln, today, hist()[-4:])
    r["readiness"] = F.readiness(e, ln, today, hist(), [])
    BM.apply(r, e, ln, today, None, rows)
    return r


def test_only_a_multi_day_baiyue():
    assert BM.applies(trip()) and BM.applies(trip(days=2, day_plan=DXB[:2]))
    assert not BM.applies(trip(days=1, day_plan=None))                      # 單攻百岳
    assert not BM.applies(trip(kind="race"))                                # a multi-day trail race


def test_the_summit_day_is_the_day_with_the_most_climb():
    sd = BM.summit_day(line(trip()))
    assert sd["day"] == 1 and sd["climb_m"] == 1500


def test_feasibility_counts_the_simulations_still_possible_not_the_weekly_volume():
    r = run(trip(), [row(10, 1200.0)])                    # 11 weeks out, 1200 m now: 1500 m in 3 weeks of +10 %
    assert "weekly" not in ids(r["checks"]) and "hours" not in ids(r["checks"])
    c = next(c for c in r["checks"] if c["id"] == "summit_sim")
    assert c["level"] == "ok" and c["projected"] >= BM.SIM_NEED and c["need_m"] == 1500
    assert "攻頂日是第 1 天" in c["text"] and "要 3 次" in c["text"]
    assert r["level"] == "ok" and r["race_day"]["climb_m"] == 1500
    assert any("Training for Mountaineering" in s for s in r["src"]) and not any("Big Vert" in s for s in r["src"])


def test_feasibility_over_suggests_a_lower_route_one_more_day_or_later():
    e = trip(start="2026-11-14")                          # 6 weeks out, 400 m now
    r = run(e, [row(5, 400.0)])
    c = next(c for c in r["checks"] if c["id"] == "summit_sim")
    assert c["level"] == "over" and r["level"] == "over" and c["projected"] == 0
    assert "低一級的路線" in r["suggestions"][0] and not any("組別" in s for s in r["suggestions"])
    assert "downgrade" not in r


def test_tight_with_one_or_two_and_the_ones_done_count():
    e = trip(start="2026-11-14")
    r = run(e, [row(5, 1250.0)])                          # reaches 1500 m by about 4 weeks out: 1–2 left
    c = next(c for c in r["checks"] if c["id"] == "summit_sim")
    assert c["level"] == "tight" and 1 <= c["projected"] < 3
    r = run(e, [row(5, 1250.0), row(3, 1550.0, 12.0), row(10, 1500.0, 10.0)])
    c = next(c for c in r["checks"] if c["id"] == "summit_sim")
    assert c["done"] == 2 and c["level"] == "ok" and "已經做了 2 次" in c["text"]


def test_late_and_turnaround_are_kept():
    e = trip(start=(TODAY + dt.timedelta(days=14)).isoformat())
    r = run(e, [row(3, 1600.0, 12.0)])
    assert "late" in ids(r["checks"]) and r["level"] == "late"
    assert any("B 或 C" in s for s in r["suggestions"])


def test_readiness_counts_simulations_with_a_pack():
    e = trip(start="2026-11-14")                          # 8 weeks out = 9/19
    rows = [row(2, 1520.0, 12.0), row(9, 1600.0, 10.0),   # pack ≥ 80 % of 12 kg: both count
            row(4, 1700.0, None), row(6, 1550.0, 5.0),    # no pack recorded / too light
            row(12, 900.0, 12.0), row(30, 2000.0, 12.0)]  # too little climb / before the 8 weeks
    r = run(e, rows)
    rd = r["readiness"]
    assert ids(rd["checks"])[0] == "summit_sim" and "long" not in ids(rd["checks"]) and "weekly" not in ids(rd["checks"])
    c = rd["checks"][0]
    assert c["count"] == 2 and c["no_pack"] == 2 and c["level"] == "tight" and rd["label"] == "差一點"
    assert "記下那次背多少" in c["text"]
    rd2 = run(e, rows + [row(1, 1500.0, 11.0)])["readiness"]
    assert rd2["checks"][0]["level"] == "ok"
    assert run(e, [])["readiness"]["checks"][0]["level"] == "short"


def test_races_hook_and_trail_races_unchanged(monkeypatch):
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: hist(n=weeks))
    monkeypatch.setattr(F, "activity_rows", lambda ds, today, days=42: [])
    monkeypatch.setattr(BM, "sim_rows", lambda ds, today, days=56: [row(10, 1000.0)])
    plan = P.Plan(events=[trip(),
                          P.Event("r", "合歡山越野", "2026-12-19", kind="race", distance_km=30, climbing_m=2000, est_hours=6.0),
                          # a 單攻百岳 is entered as a trail race
                          P.Event("s", "玉山單攻", "2026-12-26", kind="race", distance_km=24, climbing_m=1700, est_hours=9.0)])
    out = {r["event_id"]: r for r in F.races(plan, None, TODAY, predict=lambda e, c=None: None, gpx=lambda e: None)}
    assert "summit_sim" in ids(out["t"]["checks"]) and "weekly" not in ids(out["t"]["checks"])
    assert "weekly" in ids(out["r"]["checks"]) and "summit_sim" not in ids(out["r"]["checks"])
    assert "weekly" in ids(out["s"]["checks"])


def test_taper_7_to_10_days():
    assert P.taper_days(trip()) == 7 and P.taper_days(trip(days=2, day_plan=DXB[:2])) == 7
    assert P.taper_days(trip(days=5, day_plan=DXB + DXB[:2])) == 10
    assert P.taper_days(trip(days=1, day_plan=None)) == 14


# ---- the 專項期 schedules the simulation ------------------------------------------------------

def race_of(e, today):
    return SP.race_day(P.Plan(events=[e]), today, predict=lambda e, c=None: [8.0, 6.0, 7.0], gpx=lambda e: None)


def test_specific_phase_long_day_is_the_summit_simulation_from_8_weeks_out():
    e = trip()
    r = race_of(e, TODAY)
    assert r["summit"]["climb_m"] == 1500 and r["summit"]["pack_kg"] == 12.0
    mon = date(2026, 11, 2)                               # 賽前第 7 週
    info = SP.week_context(kind="specific", mode="specific", monday=mon, race=r)
    assert info["summit_sim"]
    full = SP.summit_minutes(r["summit"])
    assert SP.long_minutes(info, 1000) == pytest.approx(full)                # the whole climb, no 6-h cap
    assert SP.long_minutes(info, 120) == pytest.approx(120 * 1.15)           # +15 % still rules
    ss = [{"id": "long", "kind": "long", "minutes": 138, "title": "LSD（山路）", "source": "Koop",
           "detail": "有山路就走山路，陡坡用走的；全程心率壓在輕鬆跑上限以下，爬坡可以走"}]
    SP.decorate(ss, info)
    s = ss[0]
    assert s["title"] == "攻頂日模擬｜大小霸" and "背 12 kg 的背包" in s["detail"]
    assert "+15%" in s["detail"] and s["climb_m"] < 1500 and "全程心率壓在輕鬆跑上限以下" in s["detail"]
    assert "Training for Mountaineering" in s["source"] and info["long"]["summit_sim"]
    ss = [{"id": "long", "kind": "long", "minutes": round(full), "title": "LSD（山路）", "detail": ""}]
    SP.decorate(ss, info)
    assert ss[0]["climb_m"] == 1500 and "+15%" not in ss[0]["detail"]


def test_specific_phase_before_8_weeks_and_the_last_week():
    r = race_of(trip(), TODAY)
    early = SP.week_context(kind="specific", mode="specific", monday=date(2026, 10, 12), race=r)   # 第 10 週
    assert early["active"] and not early.get("summit_sim")
    last = SP.week_context(kind="specific", mode="specific", monday=date(2026, 11, 30), race=r)    # 第 3 週: the last
    assert last["active"] and last["summit_sim"]
    rec = SP.week_context(kind="specific", mode="recovery_week", monday=date(2026, 11, 2), race=r)
    assert not rec.get("summit_sim")
    # a one-day 百岳 and a trail race: no simulation
    one = race_of(trip(days=1, day_plan=None, distance_km=20, climbing_m=1500, est_hours=9.0), TODAY)
    assert one["summit"] is None


def test_week_plan_puts_the_simulation_on_the_long_day():
    """A built athlete (150′ long days) 5–6 weeks before a 3-day 百岳: the week's long day is the
    攻頂日模擬 with the trip's pack, its climb the share the +15 % step allows."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_quality_gate import TODAY as T0
    ds = _history(T0)
    plan = _plan_with("2026-12-05", 1, T0)
    plan.events = [trip(start="2026-11-07", est_hours=21.0)]
    ds.plan = plan
    st = Status(ds, plan, T0, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, T0)
    assert wp["phase"] == "specific" and wp["specific"]["summit_sim"]
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["title"] == "攻頂日模擬｜大小霸" and "背 12 kg 的背包" in long_s["detail"]
    assert 0 < long_s["climb_m"] < 1500 and wp["specific"]["long"]["need_m"] == 1500
