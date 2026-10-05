"""
SP-98: the 恢復期 by event size + the 回量期 (reverse taper), the first-week day rules
(engine/post_race.py), the pre-race volume base; SP-111 下坡升級 (planning.downhill /
engine/downhill_recovery.py); SP-95's two A races < 12 / < 8 weeks apart. Synthetic plans; the
downhill hooks are set per test (conftest leaves them unset).
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import downhill_recovery as DR
from backend.engine import overview as O
from backend.engine import planning as P
from backend.engine import post_race as PR

B, E = date(2026, 6, 1), date(2027, 12, 31)


def ev(name="r", date_="2027-03-06", **kw):
    return P.Event(id=name, name=name, date=date_, priority=kw.pop("priority", "A"), **kw)


MARA = dict(kind="road", distance_km=42.2, est_hours=4.5)
ULTRA = dict(distance_km=70, climbing_m=4000, est_hours=12.0)
HUNDRED = dict(distance_km=160, climbing_m=9000, est_hours=30.0)


def of(ph, kind, eid="r"):
    return [p for p in ph if p.kind == kind and p.event_id == eid]


def days(p):
    return (P._d(p.end) - P._d(p.start)).days + 1


def hooks(monkeypatch, race, mine):
    monkeypatch.setattr(P, "RACE_DOWNHILL_OF", lambda e: race)
    monkeypatch.setattr(P, "ATHLETE_DOWNHILL_OF", lambda d: mine)


# ---- length by size ---------------------------------------------------------------------------

@pytest.mark.parametrize("kw, rec, rb", [
    (dict(distance_km=10, climbing_m=0, est_hours=1.0), 7, 0),
    (dict(distance_km=21, climbing_m=0, est_hours=2.5), 7, 0),
    (MARA, 7, 1), (ULTRA, 14, 0), (HUNDRED, 14, 2),
    (dict(distance_km=30, climbing_m=2000), 7, 1),           # EP 50, no time: 馬拉松級 (not horizontal km)
    (dict(distance_km=42), 7, 1),                            # km only: 馬拉松級
])
def test_recovery_and_rebuild_by_size(kw, rec, rb):
    rp = P.recovery_plan(ev(**kw))
    assert (rp["days"], rp["rebuild"]) == (rec, rb) and rp["downhill"] is None


def test_b_race_recovery_by_size():
    assert P.recovery_plan(ev(distance_km=10, climbing_m=0, est_hours=1.0), b=True)["days"] == 3
    assert P.recovery_plan(ev(distance_km=21, climbing_m=0, est_hours=2.5), b=True)["days"] == 5
    assert (P.recovery_plan(ev(**MARA), b=True)["days"], P.recovery_plan(ev(**MARA), b=True)["rebuild"]) == (7, 0)
    assert P.recovery_plan(ev(**ULTRA), b=True)["days"] == 14


def test_phases_recovery_transition_rebuild_base():
    r = ev(**HUNDRED)                                     # race 3/6: recovery 3/7–3/20
    ph = P.auto_phases([r], B, E, 3)
    rec, tr, rb = of(ph, "recovery")[0], of(ph, "transition")[0], of(ph, "rebuild")[0]
    assert (rec.start, rec.end) == ("2027-03-07", "2027-03-20")
    assert (tr.start, days(tr)) == ("2027-03-21", 21) and (rb.start, days(rb)) == ("2027-04-11", 14)
    nxt = ph[ph.index(rb) + 1]
    assert nxt.kind == "base" and nxt.start == "2027-04-25"
    assert "100 英里級（預估時間 30.0 h）：恢復 14 天＋回量 2 週" in rec.note
    # 轉換期 off: the 回量期 follows the 恢復期
    ph = P.auto_phases([r], B, E, 0)
    assert of(ph, "rebuild")[0].start == "2027-03-21" and not of(ph, "transition")
    # post-race days include the 回量期 (not a running break, not a volume baseline)
    assert date(2027, 4, 15) in P.post_race_days(P.Plan(events=[r]), date(2027, 4, 1), date(2027, 4, 30), 3)


def test_rebuild_shares():
    ph = P.auto_phases([ev(**HUNDRED)], B, E, 0)
    rb = of(ph, "rebuild")[0]                              # 3/21 (Sun) – 4/3
    assert P.rebuild_share(rb, date(2027, 3, 22)) == (0.5, 0, 2)
    assert P.rebuild_share(rb, date(2027, 3, 29)) == (0.75, 1, 2)
    one = of(P.auto_phases([ev(**MARA)], B, E, 0), "rebuild")[0]
    assert days(one) == 7 and P.rebuild_share(one, P._d(one.start))[0] == 0.5


# ---- 下坡升級 (SP-111) -------------------------------------------------------------------------

def test_downhill_ratio_two_bumps_a_marathon_to_14_days(monkeypatch):
    hooks(monkeypatch, 20.0, 10.0)
    rp = P.recovery_plan(ev(**MARA))
    assert (rp["days"], rp["rebuild"], rp["bumped"]) == (14, 0, "size")
    assert "這場的下坡是你近 6 週最大一次的 2.0 倍" in rp["text"] and "恢復多給 7 天" in rp["text"]
    assert "特別注意" in rp["text"] and "72 小時內不排硬課" in rp["text"]
    rec = of(P.auto_phases([ev(**MARA)], B, E, 0), "recovery")[0]
    assert days(rec) == 14 and not of(P.auto_phases([ev(**MARA)], B, E, 0), "rebuild")


def test_close_downhill_training_keeps_7_days(monkeypatch):
    hooks(monkeypatch, 12.0, 10.0)                          # 1.2: no bump, still a big downhill
    rp = P.recovery_plan(ev(**MARA))
    assert (rp["days"], rp["rebuild"], rp["bumped"]) == (7, 1, None)
    assert "1.2 倍" in rp["text"] and "特別注意" not in rp["text"] and "72 小時" in rp["text"]
    hooks(monkeypatch, 8.0, 10.0)                           # less than you did: nothing extra
    rp = P.recovery_plan(ev(**MARA))
    assert rp["bumped"] is None and "72 小時" not in rp["text"]


def test_ten_weeks_ago_does_not_count():
    day = date(2027, 3, 6)
    assert DR.athlete_max([(day - dt.timedelta(days=70), 30.0)], day) == 0.0      # 10 weeks: weight 0
    assert DR.athlete_max([(day - dt.timedelta(days=30), 10.0)], day) == 10.0
    assert DR.athlete_max([(day - dt.timedelta(days=52), 10.0)], day) == pytest.approx(10.0 * 11 / 21)
    assert DR.athlete_max([], day) == 0.0


def test_nothing_downhill_counts_as_over(monkeypatch):
    hooks(monkeypatch, 5.0, 0.0)
    rp = P.recovery_plan(ev(**MARA))
    assert rp["days"] == 14 and "近 9 週沒有下坡紀錄" in rp["text"]


def test_ultra_bump_adds_a_rebuild_week(monkeypatch):
    hooks(monkeypatch, 30.0, 10.0)
    rp = P.recovery_plan(ev(**ULTRA))
    assert (rp["days"], rp["rebuild"], rp["bumped"]) == (14, 1, "rebuild") and "回量期多 1 週" in rp["text"]
    rp = P.recovery_plan(ev(**HUNDRED))
    assert (rp["days"], rp["rebuild"]) == (14, 3)
    assert [round(P.rebuild_share(of(P.auto_phases([ev(**HUNDRED)], B, E, 0), "rebuild")[0],
                                  date(2027, 3, 22) + dt.timedelta(weeks=k))[0], 3) for k in range(3)] \
        == [0.5, 0.625, 0.75]


def test_no_gpx_no_bump_and_says_so(monkeypatch):
    hooks(monkeypatch, None, 10.0)
    rp = P.recovery_plan(ev(**MARA))
    assert (rp["days"], rp["rebuild"]) == (7, 1) and "沒有賽事 GPX" in rp["text"]
    monkeypatch.setattr(P, "RACE_DOWNHILL_OF", lambda e: 1 / 0)       # a broken hook: no verdict
    assert P.downhill(ev(**MARA)) is None


def test_b2b_and_ultra_checks_ignore_the_downhill(monkeypatch):
    hooks(monkeypatch, 50.0, 1.0)
    e = ev(**MARA)
    assert not e.is_long and P.event_size(e) == P.MARATHON


def test_race_load_uses_the_segment_speed():
    from backend.engine.algorithms import chart_metrics as CM
    xs = [i * 10.0 for i in range(101)]                       # 1 km at −10 %
    zs = [1000.0 - x * 0.1 for x in xs]
    seg = [{"start_km": 0.0, "end_km": 1.0, "dist_m": 1000.0, "t": 360.0}]     # 10 km/h
    assert DR.race_load(xs, zs, seg) == pytest.approx(CM.downhill_weight(-0.1, 10.0))
    assert DR.race_load(xs, zs, None, 10.0) == pytest.approx(CM.downhill_weight(-0.1, 10.0))
    assert DR.race_load(xs, [1000.0] * len(xs), seg) == 0.0                   # flat: nothing


# ---- the first week's day rules ---------------------------------------------------------------

def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


def test_no_run_two_days_then_40_minutes(monkeypatch):
    r = ev(**MARA)                                             # Sat 3/6 → no run 3/7–3/8, ≤ 40 min to 3/13
    ph = P.auto_phases([r], B, E, 0)
    w = PR.a_windows(ph, [r], date(2027, 3, 8))
    assert w and w[0]["no_run"] == date(2027, 3, 8) and w[0]["flat"] is None
    ss = [s("easy1", "easy", "2027-03-08"), s("strength1", "strength", "2027-03-08", 35),
          s("easy2", "easy", "2027-03-10", 60), s("quality", "quality", "2027-03-11", 60, variant_key="z3_x"),
          s("easy3", "easy", "2027-03-14", 60)]
    notes = []
    out = PR.apply(ss, w, date(2027, 3, 8), notes)
    assert all(x["day"] != "2027-03-08" for x in out)
    for x in out:
        if x["kind"] != "strength" and x["day"] <= "2027-03-13":
            assert x["kind"] == "easy" and x["minutes"] <= 40
    assert next(x for x in out if x["id"] == "easy3")["minutes"] == 60          # day 8: no cap
    assert notes and "前 2 天不排跑步" in notes[0]["text"] and notes[0]["src"] == "recovery"
    # a big downhill: the first 72 h on the flat
    hooks(monkeypatch, 12.0, 10.0)
    w = PR.a_windows(ph, [r], date(2027, 3, 8))
    out = PR.apply([s("easy1", "easy", "2027-03-09")], w, date(2027, 3, 8), [])
    assert out[0]["terrain"] == "road" and "不跑下坡" in out[0]["detail"]


def test_sessions_with_no_free_day_are_dropped():
    r = ev(**MARA)
    ph = P.auto_phases([r], B, E, 0)
    w = PR.a_windows(ph, [r], date(2027, 3, 1))                 # the race week: Sunday 3/7 is day 1
    notes = []
    out = PR.apply([s("easy1", "easy", "2027-03-07"), s("easy2", "easy", "2027-03-02")], w, date(2027, 3, 1), notes)
    assert [x["id"] for x in out] == ["easy2"] and "拿掉了" in notes[0]["text"]


# ---- SP-95: two A races ---------------------------------------------------------------------

def test_close_trail_races_only_recovery_rebuild_taper():
    r1 = ev("r1", "2027-03-06", distance_km=30, climbing_m=2000, est_hours=5.0)     # trail, 馬拉松級
    r2 = ev("r2", "2027-04-17", distance_km=30, climbing_m=2000, est_hours=5.0)     # 6 weeks later
    ph = P.auto_phases([r1, r2], B, E)
    assert not of(ph, "specific", "r2") and not of(ph, "transition", "r1")
    rb = of(ph, "rebuild", "r1")[0]
    assert P._d(rb.end) + dt.timedelta(days=1) == P._d(of(ph, "taper", "r2")[0].start)
    assert "中間不排訓練" in rb.note


def test_close_road_races_no_transition_and_an_intermediate_cap():
    r1 = ev("r1", "2027-03-06", **MARA)
    r2 = ev("r2", "2027-05-01", **MARA)                         # 8 weeks: recovery → rebuild → 專項期 → taper
    ph = P.auto_phases([r1, r2], B, E, 3)
    assert not of(ph, "transition", "r1") and of(ph, "rebuild", "r1") and of(ph, "specific", "r2")
    sp = of(ph, "specific", "r2")[0]
    it = P.intermediate(ph, P._d(sp.start) + dt.timedelta(days=7 - P._d(sp.start).weekday()))
    assert it and it["share"] == pytest.approx(0.9) and it["gap"] == 56
    assert P.inter_peak(28) == pytest.approx(0.65) and P.inter_peak(35) == pytest.approx(0.775)
    why = []
    mon = P._d(sp.start) + dt.timedelta(days=7 - P._d(sp.start).weekday())
    assert O.inter_cap(ph, mon, 10.0, why, lambda m: 8.0) == pytest.approx(0.9 * 8.0) and "中間訓練" in why[0]
    far = P.auto_phases([r1, ev("r2", "2027-07-24", **MARA)], B, E)
    assert of(far, "transition", "r1") and P.intermediate(far, date(2027, 6, 7)) is None


# ---- week_plan + projection -------------------------------------------------------------------

def test_week_plan_and_projection_recovery_transition_rebuild():
    """A road marathon on Sat 9/26 (馬拉松級: recovery 9/27–10/3, 轉換期 10/4–10/24, 回量期 10/25–31).
    TODAY Wed 9/30: a 恢復期 week at 40 % of the 4 weeks before the taper, every run ≤ 40 min; the
    projection's 回量期 week at 50 %, then the base phase."""
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as PJ
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = [P.Event("m", "城市馬", "2026-09-26", kind="road", priority="A", distance_km=42.2, est_hours=4.5)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    assert st.kind == "recovery"
    wp = O.week_plan(ds, st, TODAY)
    ref = wp["transition_ref"]["hours"]
    assert ref and wp["target"]["hours"] == pytest.approx(P.REC_SHARE * ref)
    assert any("賽前 4 週平均" in w and "40%" in w for w in wp["why"])
    runs = [x for x in wp["sessions"] if x["kind"] in O.RUN_KINDS and not x["done"]]
    assert runs and all(x["kind"] == "easy" and x["minutes"] <= P.REC_SHORT_MIN for x in runs)
    assert all(x["day"] > "2026-09-28" for x in wp["sessions"] if x["day"] and not x["done"])
    assert any("馬拉松級（預估時間 4.5 h）：恢復 7 天＋回量 1 週" in n["text"] for n in wp["notes"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8), events=plan.events)
    rb = next(w for w in weeks if w["start"] == "2026-10-26")
    assert rb["phase"] == "rebuild" and rb["mode_label"] == "回量期"
    assert rb["hours"] == pytest.approx(0.5 * ref)
    assert all(x["kind"] in ("easy", "strength") and (x["kind"] == "strength" or x["minutes"] <= 60)
               for x in rb["sessions"])
    assert any("回量期" in n["text"] for n in rb.get("notes") or [])
    assert next(w for w in weeks if w["start"] == "2026-11-02")["phase"] == "base"


def test_race_value_from_the_stored_gpx(tmp_path, monkeypatch):
    """downhill_recovery._race_value: the event's GPX + the calculator's segment speeds; the race's mean
    speed when the calculator can't predict; None without a GPX."""
    from backend.engine import event_gpx as EG
    from backend.engine.racepower import calc as CALC
    from backend.tests.test_event_gpx import synth_gpx
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    EG._memo.clear()
    e = ev("g", distance_km=10, climbing_m=500, est_hours=1.25)          # 8 km/h on average
    assert DR._race_value(e) is None
    EG.save("g", synth_gpx([(5, 500), (5, -500)]), "r.gpx")              # 5 km at −10 % down
    monkeypatch.setattr(CALC, "make_plan", lambda ctx, body: {"segments": [
        {"start_km": 0.0, "end_km": 5.0, "dist_m": 5000.0, "t": 2400.0},
        {"start_km": 5.0, "end_km": 10.0, "dist_m": 5000.0, "t": 1500.0}]})          # 12 km/h down
    from backend.engine.algorithms import chart_metrics as CM
    fast = DR._race_value(e)
    assert fast == pytest.approx(5 * CM.downhill_weight(-0.1, 12.0), rel=0.15)

    def broken(ctx, body):
        raise RuntimeError("no CP")
    monkeypatch.setattr(CALC, "make_plan", broken)
    slow = DR._race_value(e)
    assert slow == pytest.approx(5 * CM.downhill_weight(-0.1, 8.0), rel=0.15) and slow < fast
