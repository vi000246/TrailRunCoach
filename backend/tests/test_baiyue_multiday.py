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
    assert SP.long_minutes(info, 120) == pytest.approx(120 * SP.STEP)        # the single-run cap still rules (SP-66)
    ss = [{"id": "long", "kind": "long", "minutes": 138, "title": "LSD（山路）", "source": "Koop",
           "detail": "有山路就走山路，陡坡用走的；全程心率壓在輕鬆跑上限以下，爬坡可以走"}]
    SP.decorate(ss, info)
    s = ss[0]
    assert s["title"] == "攻頂日模擬｜大小霸" and "背 12 kg 的背包" in s["detail"]
    assert "+10%" in s["detail"] and s["climb_m"] < 1500 and "全程心率壓在輕鬆跑上限以下" in s["detail"]
    assert "Training for Mountaineering" in s["source"] and info["long"]["summit_sim"]
    ss = [{"id": "long", "kind": "long", "minutes": round(full), "title": "LSD（山路）", "detail": ""}]
    SP.decorate(ss, info)
    assert ss[0]["climb_m"] == 1500 and "+10%" not in ss[0]["detail"]


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
    攻頂日模擬 with the trip's pack, its climb the share the +10 % step (SP-66) allows."""
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
    # SP-66 × SP-114: the simulation grows like any long day, ≤ +10 % over the longest of 30 days
    from backend.engine import load_guard as LG
    assert long_s["minutes"] <= LG.LONG_CAP * wp["specific"]["longest28"] + 1e-6 and "+10%" in long_s["detail"]
    # SP-115: a walking session — the uphill cap (no max HR here: the easy cap stands in) or RPE ≤ 13
    assert "RPE ≤ 13" in long_s["target"] and "下坡看腿的感覺" in long_s["detail"]


# ---- ME instead of the uphill VO2max set (ADS ≤ 10 %) -----------------------------------------

def me_week(mode="specific", monday=date(2026, 11, 2)):
    r = race_of(trip(), TODAY)
    info = SP.week_context(kind="specific", mode=mode, monday=monday, race=r)
    d = lambda i: (monday + dt.timedelta(days=i)).isoformat()
    ss = [{"id": "long", "kind": "long", "minutes": 200, "title": "LSD（山路）", "detail": "", "day": d(5)},
          {"id": "quality", "kind": "quality", "minutes": 60, "title": "VO2max 間歇 5×4 分上坡", "tss": 75.0, "day": d(1)},
          {"id": "easy1", "kind": "easy", "minutes": 60, "title": "輕鬆跑", "tss": 40.0, "day": d(2)},
          {"id": "easy2", "kind": "easy", "minutes": 60, "title": "輕鬆跑", "tss": 40.0, "day": d(3)},
          {"id": "strength1", "kind": "strength", "minutes": 35, "title": "肌力（下肢單腳＋核心）", "tss": 20.0}]
    return info, ss


def total(ss):
    return sum(s["minutes"] for s in ss if s["kind"] != "strength")


def test_me_replaces_the_uphill_set_when_ads_is_within_10_percent():
    info, ss = me_week()                                  # 賽前第 7 週: 70 % of the 1500 m summit climb
    before = total(ss)
    SP.apply_me(ss, info, 0.08, 60.0)
    me = next(s for s in ss if s["id"] == "me")
    assert not any(SP._hill_set(s) for s in ss) and me["kind"] == "quality"
    assert me["climb_m"] == 750 and me["minutes"] <= SP.ME_MAX_MIN         # 70 % = 1050 m, cut to what fits 150′
    assert "背 9 kg（體重的 15%）" in me["detail"] and "做完 3 天只排輕鬆" in me["detail"] and "目標是 1050 m" in me["detail"]
    assert me["day"] == (date(2026, 11, 2) + dt.timedelta(days=1)).isoformat()      # the uphill set's day
    assert [s["minutes"] for s in ss if s["kind"] == "easy"] == [20, 20]           # the minutes come from the easy runs
    assert total(ss) - before == me["minutes"] - 60 - 80 and "Vertical Beast Mode" in me["source"]
    info10, ss10 = me_week(monday=date(2026, 10, 12))     # 賽前第 10 週: 50 % = 750 m
    SP.apply_me(ss10, info10, 0.05, None)
    me10 = next(s for s in ss10 if s["id"] == "me")
    assert me10["climb_m"] == 750 and "沒有體重紀錄" in me10["detail"] and "目標是" not in me10["detail"]


def test_without_ads_the_week_keeps_general_strength():
    info, ss = me_week()
    notes = []
    before = total(ss)
    SP.apply_me(ss, info, 0.15, 60.0, notes=notes)
    assert not any(s["id"] == "me" or SP._hill_set(s) for s in ss)
    assert [s["id"] for s in ss if s["kind"] == "strength"] == ["strength1", "strength2"]
    assert total(ss) == before - 0 and "ADS（LTHR ÷ AeT − 1）是 15%" in notes[0]["text"]
    info, ss = me_week()
    notes = []
    SP.apply_me(ss, info, None, 60.0, notes=notes)
    assert "算不出 ADS" in notes[0]["text"]


def test_no_me_in_a_recovery_week_a_blocked_week_or_a_trail_race():
    info, ss = me_week(mode="recovery_week")
    assert not SP.apply_me(ss, info, 0.05, 60.0) is None and not any(s["id"] == "me" for s in ss)
    info, ss = me_week()
    SP.apply_me(ss, info, 0.05, 60.0, allow=False)
    assert not any(s["id"] == "me" for s in ss)
    r = SP.race_day(P.Plan(events=[P.Event("r", "合歡山越野", "2026-12-19", kind="race", distance_km=30, climbing_m=2000,
                                           est_hours=6.0)]), TODAY)
    info = SP.week_context(kind="specific", mode="specific", monday=date(2026, 11, 2), race=r)
    _i, ss = me_week()
    SP.apply_me(ss, info, 0.05, 60.0)
    assert any(SP._hill_set(s) for s in ss)                # a trail race keeps its uphill set


def test_week_plan_uses_me_when_the_gate_has_ads(monkeypatch):
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
    next(i for i in st.indicators if i.id == "gate").extra["gap"] = 0.07
    wp = O.week_plan(ds, st, T0)
    me = next(s for s in wp["sessions"] if s["id"] == "me")
    assert me["day"] and me["title"].startswith("ME 負重爬坡") and wp["specific"]["me"]["climb_m"] == me["climb_m"]
    from backend.engine import projection as PJ
    from backend.tests.test_b2b import _phases
    weeks = PJ.project_weeks(wp, _phases(plan, T0), date(2026, 11, 8))
    spec = [w for w in weeks if (w.get("specific") or {}).get("me_week")]
    assert spec and all(any(x["id"] == "me" and x["day"] for x in w["sessions"]) for w in spec)


def test_sp112_checks_a_multi_day_baiyue_keeps_and_drops():
    """SP-112 × SP-114 (integration): the weekly volume, its climb sub-check and Koop's hours go (the
    攻頂日模擬 replaces them); 跨級, the climb rate and the climb power stay (they judge the summit day,
    not the volume), with a route suggestion instead of the trail race's 「低一級的比賽」."""
    assert set(BM.DROP_FEAS) == {"weekly", "climb", "hours"}
    big = [{"km": 30.0, "gain_m": 2500, "loss_m": 600}] + DXB[1:]           # summit day EP 55: class S
    e = trip(day_plan=big, distance_km=75, climbing_m=3800)
    ln = line(e)
    rates = F.climb_rates([{"vam": 300.0, "hr": 150.0, "lthr": 170.0, "z": 1000.0, "g": 0.2} for _ in range(30)])
    r = F.assess(e, ln, TODAY, hist(km=40.0, climb=300.0)[-4:], best={"ep": 10.0, "date": date(2026, 5, 1)},
                 climb={"rates": rates}, power={"cp": 70.0, "kg": 70.0})
    assert {"climb", "step", "vam", "power"} <= set(ids(r["checks"]))       # before the hook
    BM.apply(r, e, ln, TODAY, None, [row(10, 1200.0)])
    got = ids(r["checks"])
    assert not {"weekly", "climb", "hours"} & set(got)
    assert {"step", "vam", "power", "summit_sim"} <= set(got)
    assert next(c for c in r["checks"] if c["id"] == "step")["level"] == "over" and r["level"] == "over"
    assert any("低一級的路線" in s for s in r["suggestions"]) and not any("比賽" in s for s in r["suggestions"])
    # readiness: SP-112 adds no check there; the long day / week / hours go, B2B is kept
    assert set(BM.DROP_READY) == {"long", "weekly", "hours"}


def test_summit_simulation_and_me_are_walking_sessions():
    """SP-115 × SP-114 (integration): the 攻頂日模擬 and 「ME 負重爬坡」 climb at the walking cap (75 %
    HRmax or RPE ≤ 13, target_policy.is_walk → session type walk), the stored ME too (no id); the
    技術地形 session and a plain interval stay as they were."""
    from backend.engine import hr_profile as HP
    from backend.engine import target_policy as TP
    info, ss = me_week()
    SP.apply_me(ss, info, 0.08, 60.0)
    me = next(s for s in ss if s["id"] == "me")
    sim = [{"id": "long", "kind": "long", "minutes": 138, "title": "LSD（山路）",
            "detail": "有山路就走山路，陡坡用走的；全程心率壓在輕鬆跑上限以下，爬坡可以走"}]
    SP.decorate(sim, SP.week_context(kind="specific", mode="specific", monday=date(2026, 11, 2), race=race_of(trip(), TODAY)))
    assert sim[0]["title"].startswith("攻頂日模擬")
    for s in (me, sim[0], {"kind": "quality", "title": me["title"]}):            # the last: a stored row, no id
        assert TP.is_walk(s) and TP.session_type(s) == "walk"
    assert TP.target_policy(sim[0])["basis"] == "hr" and TP.target_policy(me)["basis"] == "hr"
    assert not TP.is_walk({"kind": "quality", "title": "VO2max 間歇 5×4 分上坡"})
    assert not TP.is_walk({"kind": "hike", "title": "技術地形 40′（RPE 3–4）"})
    w = HP.walk_cap(195.0, aet=145.0)
    SP.walk_targets(ss + sim, w, 145.0)
    for s in (me, sim[0]):
        assert s["target"].startswith("心率 ≤ 爬坡上限 146 bpm") and "RPE ≤ 13" in s["target"]
        assert "下坡看腿的感覺" in s["detail"] and "全程心率壓在輕鬆跑上限以下" not in s["detail"]
    assert not any(s.get("target", "").startswith("心率 ≤ 爬坡上限") for s in ss if s["id"] not in ("me",))


def test_strength_drops_the_step_down_once_sp114s_me_is_in(monkeypatch):
    """SP-119 × SP-114 (integration): SP-119 detects the ME by id 「me…」 / 「ME」 in the title — SP-114's
    session is id "me", 「ME 負重爬坡（…）」, so it matches; but the ME is added after the strength
    template (specific_phase.apply_me), so strength_plan.refresh re-renders the 維持 session: no
    離心下階 in week_plan and in the projected ME weeks."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as PJ
    from backend.engine import strength_plan as STP
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY as T0
    assert STP.has_me([SP.me_session({"race": race_of(trip(), TODAY), "weeks_out": 6}, 60.0)])
    ds = _history(T0)
    plan = _plan_with("2026-12-05", 1, T0)
    plan.events = [trip(start="2026-11-07", est_hours=21.0)]
    ds.plan = plan
    st = Status(ds, plan, T0, prefs=PP.Prefs()).compute()
    next(i for i in st.indicators if i.id == "gate").extra["gap"] = 0.07
    wp = O.week_plan(ds, st, T0)
    assert any(s["id"] == "me" for s in wp["sessions"])
    strength = [s for s in wp["sessions"] if s["kind"] == "strength"]
    assert strength and all("離心下階" not in s["title"] and "ME 負重爬坡" in s["detail"] for s in strength)
    weeks = PJ.project_weeks(wp, _phases(plan, T0), date(2026, 11, 8))
    for w in weeks:
        if any(x["id"] == "me" for x in w["sessions"]):
            ss = [x for x in w["sessions"] if x["kind"] == "strength"]
            assert ss and all("離心下階" not in x["title"] for x in ss)
