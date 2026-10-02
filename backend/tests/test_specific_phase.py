"""
engine/specific_phase.py: the 專項期 long day following the next A race's コース定數, the
race GPX's climbs / descents in the sessions, and the race simulation suggestion.
Synthetic plans, a tiny synthetic GPX (tmp DB / folder); nothing reads the athlete's data.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import event_gpx as EG
from backend.engine import specific_phase as SP
from backend.engine import suggestions as SG
from backend.engine.algorithms.chart_metrics import course_constant
from backend.engine.panels import race_refs as RR
from backend.engine.planning import Event, Plan
from backend.tests.test_event_gpx import synth_gpx

MON = date(2026, 9, 28)
# 20 km ↑1500 ↓1500: up 800 m over 5 km (16 %), down 300, up 700, down 1200 m over 8 km
GPX = synth_gpx([(5, 800), (3, -300), (4, 700), (8, -1200)])


def _ev(start="2026-11-07", eid="e1", **kw):          # Sat 11/7: the week of 9/28 is 賽前第 6 週
    a = dict(kind="race", priority="A", distance_km=30, climbing_m=2000, est_hours=5.0)
    a.update(kw)
    return Event(eid, "合歡山越野", start, **a)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    EG._memo.clear()
    return tmp_path


def _race(store=None, **kw):
    if store is not None:
        EG.save("e1", GPX, "hehuan.gpx")
    return SP.race_day(Plan(events=[_ev(**kw)]), MON - dt.timedelta(days=1))


def test_legs_find_the_longest_climb_and_descent():
    km = [i * 0.1 for i in range(201)]
    z = [1000 + min(i, 50) * 16 - max(0, min(i, 80) - 50) * 10 + max(0, min(i, 120) - 80) * 17.5
         - max(0, i - 120) * 15 for i in range(201)]        # up 800 / 5 km, down 300, up 700, down 1200
    ls = SP.legs(km, z)
    assert [x["dir"] for x in ls] == [1, -1, 1, -1]
    f = SP.features_of({"km": km, "z": z}, {"gain_m": 1500, "loss_m": 1500}, 0.15)
    assert f["climb"]["dz"] == pytest.approx(800) and f["climb"]["grade"] == pytest.approx(16, abs=0.1)
    assert f["climb"]["minutes"] == round((5 + 8) * 0.15 * 60)          # effort-km × the race's h / effort-km
    assert f["descent"]["dz"] == pytest.approx(1200) and f["steep_descent"]["grade"] == pytest.approx(15, abs=0.1)
    # a 20 m dip inside a climb doesn't end it
    assert len(SP.legs([0, 1, 2, 3], [0, 300, 280, 600])) == 1


def test_race_day_uses_race_refs_numbers_and_the_gpx(store):
    r = _race(store)
    ln = RR.race_line(_ev(), None, "", RR.course_of(_ev()))
    assert r["goal"] == pytest.approx(RR.goal_of(ln), abs=0.1)
    assert r["goal"] == pytest.approx(course_constant(5.0, r["km"], r["climb_m"], r["descent_m"]), abs=0.2)
    assert r["km"] == pytest.approx(20, abs=0.2) and r["descent_m"] == pytest.approx(1500, rel=0.08)  # the GPX, not 30 km
    f = r["features"]
    assert f["climb"]["dz"] == pytest.approx(800, rel=0.1) and f["climb"]["grade"] == pytest.approx(16, abs=1.5)
    assert f["descent"]["dz"] == pytest.approx(1200, rel=0.1) and f["loss_m"] == pytest.approx(1500, rel=0.08)
    # the race calculator's time wins over the plan's estimate
    r2 = SP.race_day(Plan(events=[_ev()]), MON, predict=lambda e, c: [6.0])
    assert r2["hours"] == 6.0 and r2["goal"] > r["goal"]
    # multi-day: one average day (the per-day target); no GPX = no features
    m = SP.race_day(Plan(events=[_ev(eid="e2", days=2, kind="baiyue", distance_km=24, climbing_m=2400, est_hours=14)]), MON)
    assert m["days"] == 2 and m["day"]["hours"] == pytest.approx(7) and m["features"] is None
    assert m["goal"] == pytest.approx(course_constant(7, 12, 1200, 1200), abs=0.2)
    assert SP.race_day(Plan(events=[_ev(priority="B")]), MON) is None


def test_progression_reaches_the_band_twice_in_weeks_6_to_3():
    r = _race()
    hit = [w for w in range(3, 7) if SP.FRAC[w] >= RR.BAND_LO]
    assert len(hit) in (1, 2) and all(SP.FRAC[w] < RR.BAND_LO for w in range(7, 11))
    assert SP.FRAC[10] < SP.FRAC[8] < SP.FRAC[6]
    ctx = lambda m, **kw: SP.week_context(kind="specific", mode="specific", monday=m, race=r, **kw)
    a = ctx(MON)
    assert a["active"] and a["weeks_out"] == 6 and a["frac"] == 0.85
    # plenty of base: 85 % of the race day; the +15 % rule caps it otherwise
    assert SP.long_minutes(a, 400) == pytest.approx(0.85 * 300)
    assert SP.long_minutes(a, 150) == pytest.approx(150 * 1.15)
    assert ctx(MON - dt.timedelta(weeks=5))["frac"] == SP.FRAC[10]             # a longer 專項期: the first step
    assert not ctx(MON + dt.timedelta(weeks=4))["active"]                      # taper
    assert not SP.week_context(kind="base", mode="base", monday=MON, race=r)["active"]
    assert not ctx(MON, tsb=-25).get("climb")


def test_decorate_says_the_target_and_the_route(store):
    r = _race(store)
    info = SP.week_context(kind="specific", mode="specific", monday=MON, race=r)
    ss = [{"id": "long", "kind": "long", "minutes": 170, "title": "長時間輕鬆（山路）", "source": "Koop",
           "detail": "挑每公里爬升 ≥ 47 m 的路線；全程心率壓在 AeT 以下，爬坡可以走"}]
    SP.decorate(ss, info)
    s = ss[0]
    pct = round(170 / 300 * 100)
    assert s["detail"].startswith(f"這次目標定數約 {0.01 * pct * r['goal']:.0f}（單日目標的 {pct}%）")
    assert "挑每公里爬升" not in s["detail"] and "全程心率壓在 AeT 以下" in s["detail"]
    assert "+15%" in s["detail"]                                               # 85 % wanted, capped
    assert s["distance_km"] == pytest.approx(r["km"] * 170 / 300, abs=0.2)
    assert s["climb_m"] == pytest.approx(r["climb_m"] * 170 / 300, abs=2) and "江晏慶" in s["source"]
    assert info["long"]["pct"] == pct


def _week():
    d = lambda i: (MON + dt.timedelta(days=i)).isoformat()
    return [{"id": "long", "kind": "long", "day": d(5), "minutes": 170, "tss": 140.0},
            {"id": "quality", "kind": "quality", "day": d(1), "minutes": 55, "tss": 60.0},
            {"id": "easy1", "kind": "easy", "day": d(3), "minutes": 60, "tss": 40.0, "title": "輕鬆跑"},
            {"id": "easy2", "kind": "easy", "day": d(4), "minutes": 60, "tss": 40.0, "title": "輕鬆跑"},
            {"id": "easy3", "kind": "easy", "day": d(6), "minutes": 70, "tss": 45.0, "title": "輕鬆跑"}]


def test_apply_climb_turns_one_easy_run_into_the_race_climb(store):
    r = _race(store)
    info = SP.week_context(kind="specific", mode="specific", monday=MON, race=r)
    assert info["climb"]
    ss = _week()
    before = sum(s["minutes"] for s in ss if s["kind"] == "easy")
    SP.apply_climb(ss, info, aet=150.0)
    c = [s for s in ss if s["id"] == "climb"]
    assert len(c) == 1 and c[0]["day"] == (MON + dt.timedelta(days=3)).isoformat()   # ≥ 2 days from Tue / Sat
    assert "16% 坡" in c[0]["title"] and "下坡用跑的" in c[0]["detail"] and "AeT 150" in c[0]["target"]
    assert sum(s["minutes"] for s in ss if s["kind"] == "easy") == pytest.approx(before, abs=25)
    # a tight weekday cap: fewer repeats, or none
    from backend.engine import plan_prefs as PP
    tight = PP.Prefs(cap_weekday=50)
    ss = _week()
    SP.apply_climb(ss, info, aet=150.0, prefs=tight, notes=(notes := []))
    assert not any(s["id"] == "climb" for s in ss) and notes
    # no GPX: no climb session
    nogpx = SP.week_context(kind="specific", mode="specific", monday=MON, race={**r, "features": None})
    assert not nogpx.get("climb") and "GPX" in nogpx["climb_why"]


def test_race_sim_suggested_4_to_3_weeks_out(store):
    r = _race(store)
    weeks = SP.sim_weeks(date(2026, 11, 7))
    assert weeks == [date(2026, 10, 12), date(2026, 10, 19)]
    ctx = lambda m: SP.week_context(kind="specific", mode="specific", monday=m, race=r)
    assert SP.sim_suggestion(ctx(MON), MON, 200) is None                      # 賽前第 6 週: too early
    for m in (date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19)):
        sg = SP.sim_suggestion(ctx(m), m, 200, aet=150.0)
        assert sg["id"] == "race_sim:e1" and sg["type"] == "race_sim" and not sg["multi"]
    assert SP.sim_suggestion(ctx(date(2026, 10, 26)), date(2026, 10, 26), 200) is None
    sg = SP.sim_suggestion(ctx(date(2026, 10, 12)), date(2026, 10, 12), 200, aet=150.0)
    assert sg["minutes"] == [230]                                              # +15 % over 200, < the race's 300
    s = sg["sessions"][0]
    assert s["title"].startswith("賽事模擬") and s["kind"] == "long"
    assert "g 醣" in s["detail"] and "鞋" in s["detail"] and "配速" in s["detail"] and "單日目標的" in s["detail"]
    assert "Koop" in sg["help"] and "推估" in sg["help"]
    # multi-day trip: two days, a day pair
    m2 = SP.race_day(Plan(events=[_ev(eid="e2", days=2, kind="baiyue", distance_km=24, climbing_m=2400, est_hours=14)]), MON)
    sg2 = SP.sim_suggestion(SP.week_context(kind="specific", mode="specific", monday=date(2026, 10, 12), race=m2),
                            date(2026, 10, 12), 400)
    assert sg2["multi"] and len(sg2["sessions"]) == 2 and sg2["minutes"][1] < sg2["minutes"][0]
    assert "背包" in sg2["sessions"][0]["detail"]
    opts = SP.sim_day_options(sg2, date(2026, 10, 12))
    assert opts and all("end" in o for o in opts)


def test_sim_box_row_days_and_already_planned(store):
    r = _race(store)
    m = date(2026, 10, 12)
    sg = SP.sim_suggestion(SP.week_context(kind="specific", mode="specific", monday=m, race=r), m, 200)
    opts = lambda s: SP.sim_day_options(s, date(2026, 10, 13), weekday_cap=60,
                                        busy_for=lambda w: {"2026-10-17"})
    rows = SG.race_sim_rows({"cur": {"race_sim_suggestion": sg}}, opts, [])
    assert rows[0]["pick"] == "day"
    days = [o["day"] for o in rows[0]["options"]]
    assert days[:3] == ["2026-10-18", "2026-10-24", "2026-10-25"]               # weekends only (cap 60), busy Sat out
    stored = [{"title": "賽事模擬｜合歡山越野", "state": "active", "day": "2026-10-18"}]
    assert SG.race_sim_rows({"cur": {"race_sim_suggestion": sg}}, opts, stored) == []


def test_api_race_sim_accept_replaces_the_weeks_long_day(monkeypatch):
    """The floating box: 排入 stores the simulation as the user's session on the chosen day
    and tombstones the generator's long day of that week; then it's gone from the box."""
    from backend.engine import plan_prefs as PP
    from backend.tests.test_plan_store import API, Env
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs())
    race = {"id": "e1", "name": "合歡山越野", "start": "2026-10-24", "days": 1, "kind": "race", "hours": 5.0, "km": 20.0,
            "goal": 31.0, "day": {"hours": 5.0, "km": 20.0, "climb_m": 1500.0, "descent_m": 1500.0}}
    sg = SP.sim_suggestion({"active": True, "race": race}, date(2026, 9, 28), 200, aet=150.0)
    assert sg["weeks"] == ["2026-09-28", "2026-10-05"]
    with Env(monkeypatch) as e:
        e.inp["cur"]["race_sim_suggestion"] = sg
        e.c.get(f"{API}/sessions")
        row = next(s for s in e.c.get(f"{API}/suggestions").json()["suggestions"] if s["id"] == "race_sim:e1")
        assert row["pick"] == "day" and row["options"][0]["day"] == "2026-10-03"          # weekend first
        assert e.c.post(f"{API}/suggestions/accept", json={"id": row["id"], "day": "2026-09-01"}).status_code == 400
        out = e.c.post(f"{API}/suggestions/accept", json={"id": row["id"], "day": "2026-10-03"}).json()
        (s,) = out["sessions"]
        assert s["origin"] == "custom" and s["kind"] == "long" and s["title"].startswith("賽事模擬") and s["minutes"] == 230
        live = e.c.get(f"{API}/sessions").json()["sessions"]
        longs = [x for x in live if x["kind"] == "long" and "2026-09-28" <= x["day"] <= "2026-10-04"]
        assert [x["uid"] for x in longs] == [s["uid"]]                                     # the auto long day is gone
        assert "race_sim:e1" not in {x["id"] for x in e.c.get(f"{API}/suggestions").json()["suggestions"]}


def test_week_plan_and_projection_follow_the_target(store):
    """A built athlete (Sat 150′ long days) 6 weeks before an A trail race with a GPX: the
    long day is the +15 % step towards the target with its share and route; the
    projected 專項期 weeks keep rising (+15 % each) and get the race-climb session."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as P
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY
    EG.save("e1", GPX, "hehuan.gpx")
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = [_ev()]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    assert wp["phase"] == "specific" and wp["specific"]["active"] and wp["specific"]["weeks_out"] == 6
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["minutes"] == 170                                            # 150 × 1.15, not 85 % of 300
    assert long_s["detail"].startswith("這次目標定數約") and "單日目標的 57%" in long_s["detail"]
    assert long_s["distance_km"] and long_s["climb_m"]
    assert wp["race_sim_suggestion"] is None                                   # 賽前第 6 週
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8))
    longs = [next((x for x in w["sessions"] if x["id"] == "long"), None) for w in weeks if w["mode"] == "specific"]
    assert len(longs) >= 2 and all(x is not None and x["detail"].startswith("這次目標定數約") for x in longs)
    mins = [170] + [x["minutes"] for x in longs]
    assert all(b > a and b <= a * 1.15 + 5 for a, b in zip(mins, mins[1:]))
    assert any(x["id"] == "climb" for w in weeks for x in w["sessions"])
