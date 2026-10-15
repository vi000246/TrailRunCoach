"""
SP-102: no A race for more than MAINTAIN_AFTER_WEEKS (24, 推估) → the open-ended base turns into a
維持期 (維持＋輪替重點): CTL no longer pushed up every week, at least one session above VT2 a week,
easy running ≥ 80 %, no race-pace volume, a recovery week at least every 6 weeks (the 3:1 and the
6-week cap stay), a C race as the week's hard session (SP-95's rule, ≤ 1 a month), never a peak; a new
A race brings the 專項期 and the 減量期 back. Synthetic data only.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import load_guard as LG
from backend.engine import overview as O
from backend.engine import planning as P
from backend.engine import post_race as PR
from backend.engine import projection as PJ
from backend.engine import quality_gate as QG


def ev(name, date_, pri="A", **kw):
    return P.Event(id=name, name=name, date=date_, priority=pri, **kw)


RACE = ev("race", "2026-12-20", distance_km=50, climbing_m=3000)    # base again from 2027-01-25
BASE_AGAIN = date(2027, 1, 25)
M_START = BASE_AGAIN + dt.timedelta(weeks=P.MAINTAIN_AFTER_WEEKS)    # 2027-07-12


def _maint(ph):
    return [p for p in ph if p.maintain]


# ---- the phases ------------------------------------------------------------------------------------

def test_after_24_weeks_without_an_a_race_the_base_becomes_maintenance():
    assert P.MAINTAIN_AFTER_WEEKS == 24
    ph = P.auto_phases([RACE], date(2026, 6, 1), date(2027, 12, 31))
    last2 = [(p.kind, p.start, p.end, p.maintain) for p in ph[-2:]]
    assert last2 == [("base", "2027-01-25", "2027-07-11", False), ("base", "2027-07-12", "2027-12-31", True)]
    m = ph[-1]
    assert m.maintain_from == "2027-07-12" and P.phase_label(m) == "維持期"
    assert "24 週" in m.note and "推估" in m.note
    js = P.phase_json(m)
    assert js["maintain"] is True and js["label"] == "維持期" and js["maintain_from"] == "2027-07-12"
    # contiguous: nothing lost, nothing overlapping
    for a, b in zip(ph, ph[1:]):
        assert P._d(b.start) == P._d(a.end) + dt.timedelta(days=1)


def test_before_24_weeks_nothing_changes():
    ph = P.auto_phases([RACE], date(2026, 6, 1), date(2027, 7, 11))
    assert not _maint(ph) and ph[-1].kind == "base" and ph[-1].end == "2027-07-11"


def test_the_24_weeks_do_not_depend_on_the_window_asked_for():
    # the plan page asks from 120 days back, phase_on from 400 days back: the same 維持期 start
    for begin in (date(2026, 6, 1), date(2027, 3, 1), date(2027, 8, 1), date(2028, 2, 1)):
        ph = P.auto_phases([RACE], begin, date(2028, 3, 1))
        m = _maint(ph)
        assert m and m[0].maintain_from == M_START.isoformat()
        assert m[0].start == max(begin, M_START).isoformat()


def test_no_a_race_ever_counts_from_the_data_start():
    # pure engine without the app's data start: no 維持期 (the old open-ended base)
    assert not _maint(P.auto_phases([], date(2026, 1, 1), date(2027, 6, 1)))
    since = date(2026, 1, 5)
    ph = P.auto_phases([ev("c", "2026-05-01", "C")], date(2026, 3, 1), date(2027, 6, 1), since=since)
    assert [(p.start, p.maintain) for p in ph] == [("2026-03-01", False), ("2026-06-22", True)]
    assert "資料" in ph[-1].note


def test_the_data_start_hook_is_read_by_phases(monkeypatch):
    plan = P.Plan()
    monkeypatch.setattr(P, "SINCE_OF", lambda: date(2025, 1, 6))
    p = P.phase_on(plan, date(2026, 10, 15), transition_weeks=3)
    assert p is not None and p.kind == "base" and p.maintain
    monkeypatch.setattr(P, "SINCE_OF", lambda: date(2026, 9, 1))
    assert not P.phase_on(plan, date(2026, 10, 15), transition_weeks=3).maintain
    monkeypatch.setattr(P, "SINCE_OF", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert not P.phase_on(plan, date(2026, 10, 15), transition_weeks=3).maintain   # never breaks the plan


def test_a_new_a_race_brings_the_specific_phase_and_the_taper_back():
    new = ev("next", "2028-03-05", distance_km=42.2, kind="road")
    ph = P.auto_phases([RACE, new], date(2027, 9, 1), date(2028, 4, 1))
    kinds = [p.kind for p in ph]
    assert "specific" in kinds and "taper" in kinds and "event" in kinds
    assert not _maint(ph)                         # a base before an A race builds again
    # never 「一直待在巔峰」: the 維持期 holds no 專項期 / 減量期 / peak of its own
    alone = P.auto_phases([RACE], date(2027, 9, 1), date(2028, 4, 1))
    assert {p.kind for p in alone} == {"base"} and alone[-1].maintain


def test_rotation_in_four_week_blocks():
    ph = P.auto_phases([RACE], date(2027, 1, 1), date(2028, 6, 1))
    m = _maint(ph)[0]
    mon = M_START - dt.timedelta(days=M_START.weekday())
    got = [P.maintain_week(ph, mon + dt.timedelta(weeks=i))["focus"] for i in range(12)]
    assert got == ["speed"] * 4 + ["hill"] * 4 + ["speed"] * 4
    w = P.maintain_week(ph, mon + dt.timedelta(weeks=5))
    assert w["block"] == 2 and w["week_in_block"] == 2 and w["label"] == "爬坡"
    assert P.maintain_week(ph, mon - dt.timedelta(weeks=1)) is None
    assert P.maintain_week([P.phase_json(p) for p in ph], mon)["focus"] == "speed"     # dicts too
    assert m.maintain


# ---- the weekly volume: CTL held, not pushed ------------------------------------------------------

def test_ctl_goal_is_zero_in_maintenance():
    assert LG.ramp_goal("maintain", 60.0) == 0.0
    assert LG.ramp_goal("base", 60.0) > 0


def test_projected_maintenance_week_holds_the_volume():
    hist = [5.0] * 8
    build = [False] * 8
    h_base, mode_b, _w = PJ.week_hours("base", hist, build, 30.0, 50.0, 42.0, None)
    h_m, mode_m, why = PJ.week_hours("base", hist, build, 30.0, 50.0, 42.0, None, maintain=True)
    assert mode_m == "base" and h_m < h_base
    assert h_m <= O.volume_cap(5.0) and h_m >= 5.0 - 1e-9          # holds the 4-week level
    assert any("維持期" in w for w in why)
    # the 3:1 stays: three steady weeks → a recovery week
    h_r, mode_r, _w = PJ.week_hours("base", hist, [True] * 8, 30.0, 50.0, 42.0, None, maintain=True)
    assert mode_r == "recovery_week" and h_r == pytest.approx(O.RECOVERY_WEEK_SHARE * 5.0)


def test_six_week_cap_still_forces_a_recovery_week():
    assert O.recovery_reason("base", date(2027, 8, 2), None, O.RECOVERY_MAX_GAP) is not None


# ---- the weekly sessions --------------------------------------------------------------------------

BOTH = {"state": "unlocked", "guard": {}, "z3": {"open": True, "path": "weeks"},
        "z5": {"open": True, "state": "confirmed", "text": ""},
        "dose": {"done": 4, "step": 2, "z3": {"step": 2, "met": 3, "done": 3}, "z5": {"step": 1, "done": 1}},
        "ratio": {"z3": 2, "z5": 1, "why": "沒有 A 賽"}}


def test_every_maintenance_week_takes_zone5_when_it_is_open(monkeypatch):
    monkeypatch.setattr(QG, "z5_track", lambda gate, monday=None, steps=None: {"open": True, "z3_ok": True,
                                                                               "aet_ok": True, "text": ""})
    mon = date(2027, 7, 12)
    picks = [QG.week_decision(BOTH, "base", "base", mon + dt.timedelta(weeks=i), prefer="z5")["track"]
             for i in range(6)]
    assert picks == ["z5"] * 6
    # without the preference the base phase alternates 2:1
    plain = [QG.week_decision(BOTH, "base", "base", mon + dt.timedelta(weeks=i))["track"] for i in range(6)]
    assert "z3" in plain
    # Zone 5 not open: the Zone 3 rung stands in (and the week plan says so)
    monkeypatch.setattr(QG, "z5_track", lambda gate, monday=None, steps=None: {"open": False, "z3_ok": True,
                                                                               "aet_ok": False, "text": ""})
    assert QG.week_decision(BOTH, "base", "base", mon, prefer="z5")["track"] == "z3"


def test_hill_block_takes_the_uphill_version():
    gate = {"state": "unlocked", "guard": {}, "lthr": {"default": False}}
    dec = {"items": [{"track": "z5", "spec": QG.Z5[1], "advance": True}]}
    flat = O.quality_sessions(gate, dec, "base", {"cp": 250.0}, {}, 6.0)
    hill = O.quality_sessions(gate, dec, "base", {"cp": 250.0}, {}, 6.0, hill=True)
    assert not flat[0]["title"].endswith("上坡") and hill[0]["title"].endswith("上坡")


def _maint_week(gate_patch, prefs=None, sport=None, monkeypatch=None, events=()):
    """test_overview's synthetic week_plan with the status in a 維持期 (no A race ahead)."""
    from backend.engine import plan_prefs as PP
    from backend.engine.planning import Phase, Threshold
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY, _daily, _ds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    plan.thresholds.append(Threshold("2026-09-20", cp=250.0))
    plan.events = list(events)
    skip = {TODAY - dt.timedelta(days=d) for d in (4, 6)}
    ds = _ds([w for w in _daily() if w.start.date() not in skip], plan)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    gi = next(i for i in st.indicators if i.id == "gate")
    gi.extra = {**gi.extra, **gate_patch}
    st.phase = Phase("base", "2026-08-03", "2027-06-30", maintain_from="2026-08-03",
                     note="沒有 A 賽事已超過 24 週")
    st.kind = "base"
    return O.week_plan(ds, st, TODAY, prefs=prefs, sport=sport)


def test_week_plan_in_maintenance(monkeypatch):
    monkeypatch.setattr(QG, "z5_track", lambda gate, monday=None, steps=None: {"open": True, "z3_ok": True,
                                                                               "aet_ok": True, "text": ""})
    wp = _maint_week(BOTH)
    m = wp["maintain"]
    assert m and m["focus"] in ("speed", "hill") and wp["phase"] == "base"
    assert any(n.get("src") == "maintain" and "維持期" in n["text"] for n in wp["notes"])
    assert any("維持期" in w for w in wp["why"])
    q = [s for s in wp["sessions"] if s["kind"] == "quality" and s["id"] != "tech"]
    if wp["mode"] != "recovery_week":
        assert q and q[0]["rung_key"].startswith(("v", "z5"))                  # > VT2: Zone 5
    else:
        assert q and q[0]["title"].startswith("恢復週 fartlek")                 # 98–101 % CP fartlek
    # easy running ≥ 80 %: intervals' time in zone ≤ 20 % of the planned running time
    run_min = sum(s["minutes"] for s in wp["sessions"] if s["kind"] in O.RUN_KINDS)
    assert sum(O.session_tiz_min(s) for s in q) <= 0.20 * run_min + 1e-6
    # no race-pace volume: no marathon-pace segment, no B2B weekend
    assert not any("馬拉松配速" in (s.get("detail") or "") or "比賽配速" in (s.get("title") or "")
                   for s in wp["sessions"])
    assert not (wp.get("b2b") or {}).get("due")


def test_week_plan_volume_holds_in_maintenance(monkeypatch):
    monkeypatch.setattr(QG, "z5_track", lambda gate, monday=None, steps=None: {"open": True, "z3_ok": True,
                                                                               "aet_ok": True, "text": ""})
    m = _maint_week(BOTH)
    if m["mode"] == "recovery_week":
        pytest.skip("synthetic history made this a 3:1 recovery week")
    from backend.tests.test_overview import _two_track_week
    b = _two_track_week(BOTH)
    if b["mode"] == "recovery_week":
        pytest.skip("synthetic history made this a 3:1 recovery week")
    assert m["target"]["hours"] <= b["target"]["hours"] + 1e-9


# ---- C races --------------------------------------------------------------------------------------

def test_c_race_is_the_weeks_hard_session_and_one_a_month():
    mon = date(2027, 8, 2)
    c = ev("c1", "2027-08-07", "C", kind="road", distance_km=10, est_hours=0.8)
    ss = [{"id": "quality", "kind": "quality", "day": "2027-08-04", "minutes": 60, "title": "VO2max", "tss": 60.0,
           "done": False, "detail": "", "source": ""},
          {"id": "long", "kind": "long", "day": "2027-08-08", "minutes": 120, "title": "LSD", "tss": 90.0,
           "done": False, "detail": "", "source": ""}]
    out = PR.bc_apply([dict(s) for s in ss], [c], mon, [])
    assert not any(s["kind"] == "quality" for s in out)                  # SP-95: the race is the hard session
    assert not PR.c_hints([c], mon, maintain=True)
    c2 = ev("c2", "2027-08-21", "C", kind="road", distance_km=10, est_hours=0.8)
    txt = [n["text"] for n in PR.c_hints([c, c2], mon, maintain=True)]
    assert len(txt) == 1 and "一個月最多 1 場" in txt[0] and "c2" in txt[0]
    assert not PR.c_hints([c, c2], mon, maintain=False)                 # only the 維持期 counts them


# ---- safety: the switch never overwrites the user's own sessions ---------------------------------

def test_switch_to_maintenance_keeps_the_users_edits():
    from backend.engine import reconcile as R
    from backend.engine import plan_store as PS
    from backend.tests.test_plan_store import active, cur_plan, g, inputs
    new, _ = R.reconcile([], PS.gen_weeks(inputs()), [], "2026-09-30", "2026-10-11")
    a = active(new)
    a[("2026-09-28", "quality")].update(title="我的 6×1 公里", minutes=75, edited=True)   # the user's edit
    custom = {**a[("2026-09-28", "easy1")], "uid": "mine", "gen_key": None, "origin": "custom", "edited": True,
              "title": "跟朋友跑山", "day": "2026-10-03"}
    new.append(custom)
    # the 維持期 starts: the generator now plans a Zone 5 hill session and less volume
    cur = cur_plan(sessions=[g("quality", "quality", "VO2max 間歇 4×3 分 上坡", 55, "2026-10-01"),
                             g("strength1", "strength", "肌力（下肢單腳＋核心）", 35, "2026-10-02"),
                             g("easy1", "easy", "輕鬆跑", 40, "2026-10-02"),
                             g("long", "long", "LSD", 100, "2026-10-04")], hours=4.5)
    cur["maintain"] = {"focus": "hill"}
    new2, ch = R.reconcile(new, PS.gen_weeks(inputs(cur=cur)), [], "2026-09-30", "2026-10-11")
    b = active(new2)
    q = b[("2026-09-28", "quality")]
    assert q["title"] == "我的 6×1 公里" and q["minutes"] == 75 and q["edited"]
    assert any(s["uid"] == "mine" and s["state"] == "active" and s["title"] == "跟朋友跑山" for s in new2)
    assert b[("2026-09-28", "long")]["minutes"] == 100                  # the unedited ones follow the 維持期


def test_switch_to_maintenance_is_a_phase_change_for_the_auto_run():
    from backend.engine import plan_auto as PA
    assert PA.phase_token({"kind": "base", "maintain": True}) == "maintain"
    assert PA.phase_token({"kind": "base"}) == "base" and PA.phase_token(None) is None
    big = PA.classify([], [], [], "2026-09-30", "2026-10-06", "maintain", "base", None)
    assert any(b["rule"] == "phase" for b in big)


# ---- the page texts --------------------------------------------------------------------------------

def test_status_says_maintenance_in_plain_words():
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY, _daily, _ds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    ds = _ds(_daily(), plan)
    st = Status(ds, plan, TODAY)
    st.phase = P.Phase("base", "2026-08-03", "2027-06-30", maintain_from="2026-08-03",
                       note="沒有 A 賽事已超過 24 週（從 2026-02-16 起算）：維持＋輪替重點（推估）")
    st.kind = "base"
    ind = st.i_phase()
    assert ind.text == "維持期" and ind.extra["maintain"] is True
    assert "24 週" in ind.why and "加一場 A 賽事" in ind.why
    d = st.to_dict()
    assert d["phase"]["label"] == "維持期" and d["phase"]["maintain"] is True and d["phase"]["why"]


# ---- B races when no A race is ahead (owner 2026-10-15: the light preparation, MAINTAIN_B_POLICY) --

HALF_B = dict(kind="road", distance_km=21.1, est_hours=1.8)         # 短 → SP-95 recovery 3 days


def _rows(ph):
    return [(p.kind, p.start, p.end, p.maintain, p.light) for p in ph]


def test_b_race_without_a_race_gets_a_light_block_then_maintenance_again():
    assert P.MAINTAIN_B_POLICY == "b_light" and P.B_PREP_WEEKS == 4 and P.B_PREP_TAPER_DAYS == 7
    assert P.maintain_entry(BASE_AGAIN) == M_START and P.maintain_entry(None) is None
    b = ev("b", "2027-09-11", "B", **HALF_B)
    c = ev("c", "2027-10-02", "C", kind="road", distance_km=10, est_hours=0.8)
    ph = P.auto_phases([RACE, b, c], date(2027, 7, 1), date(2027, 12, 31))
    assert _rows(ph) == [
        ("base", "2027-07-01", "2027-07-11", False, False),
        ("base", "2027-07-12", "2027-08-06", True, False),          # 維持期
        ("specific", "2027-08-07", "2027-09-03", False, True),      # 4-week 專項塊
        ("taper", "2027-09-04", "2027-09-10", False, True),         # 1-week 減量 (not 14 days)
        ("base", "2027-09-11", "2027-12-31", True, False),          # back to the 維持期; the C race: nothing
    ]
    spec, tap = ph[2], ph[3]
    assert spec.event_id == "b" and tap.event_id == "b"
    assert P.phase_label(spec) == "B 賽輕量準備" and P.phase_label(tap) == "B 賽減量"
    assert P.phase_json(spec)["label"] == "B 賽輕量準備" and P.phase_json(spec)["light"] is True
    assert "要不要改成 A" in spec.note and "推估" in spec.note
    assert ph[1].maintain_from == ph[4].maintain_from == M_START.isoformat()     # the rotation goes on
    # the C race alone: never a phase
    assert not any(p.light for p in P.auto_phases([RACE, c], date(2027, 7, 1), date(2027, 12, 31)))


def test_b_race_in_a_normal_open_base_also_gets_the_light_block():
    b = ev("b", "2027-04-10", "B", **HALF_B)
    ph = P.auto_phases([RACE, b], date(2026, 6, 1), date(2027, 6, 1))
    assert [(p.kind, p.light) for p in ph if p.start >= "2027-01-25"] == [
        ("base", False), ("specific", True), ("taper", True), ("base", False)]


def test_b_race_made_an_a_race_gets_the_full_cycle():
    a = ev("b", "2027-09-11", "A", **HALF_B)
    ph = P.auto_phases([RACE, a], date(2027, 6, 1), date(2027, 12, 31))
    sp = next(p for p in ph if p.kind == "specific")
    tp = next(p for p in ph if p.kind == "taper")
    assert not sp.light and not tp.light
    assert (P._d(tp.end) - P._d(tp.start)).days + 1 == P.TAPER_DAYS                # 14 days
    assert (P._d(sp.end) - P._d(sp.start)).days + 1 == 7 * P.SPECIFIC_WEEKS        # 8 weeks


def test_b_race_before_an_a_race_keeps_sp95_only():
    b = ev("b", "2027-04-10", "B", **HALF_B)
    a = ev("a", "2027-10-10", "A", **HALF_B)
    ph = P.auto_phases([RACE, b, a], date(2027, 2, 1), date(2027, 11, 1))
    assert not any(p.light for p in ph)                       # an A race ahead: SP-95's week rules only
    f, why = PR.b_week_factor([b], date(2027, 4, 5), "base")
    assert f == 0.75 and why


def test_two_close_b_races_share_the_room():
    b1 = ev("b1", "2027-09-11", "B", **HALF_B)
    b2 = ev("b2", "2027-09-25", "B", **HALF_B)                 # 2 weeks later: inside b1's block + recovery
    ph = P.auto_phases([RACE, b1, b2], date(2027, 7, 1), date(2027, 12, 31))
    b2s = [(p.kind, p.start, p.end) for p in ph if p.event_id == "b2"]
    # b1's recovery ends 9/14: only 3 days before b2's 減量 → no 專項塊 of its own, the 1-week 減量 stays
    assert b2s == [("taper", "2027-09-18", "2027-09-24")]
    b3 = ev("b3", "2027-10-16", "B", **HALF_B)                 # 5 weeks after b1: the block starts after its recovery
    ph = P.auto_phases([RACE, b1, b3], date(2027, 7, 1), date(2027, 12, 31))
    assert [(p.kind, p.start) for p in ph if p.event_id == "b3"] == [("specific", "2027-09-15"), ("taper", "2027-10-09")]
    for x, y in zip(ph, ph[1:]):
        assert P._d(y.start) == P._d(x.end) + dt.timedelta(days=1)


def test_status_and_week_say_b_light_prep(monkeypatch):
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY, _daily, _ds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    b = ev("b", (TODAY + dt.timedelta(days=20)).isoformat(), "B", distance_km=30, climbing_m=1500, est_hours=4.0)
    plan.events = [b]
    ds = _ds(_daily(), plan)
    st = Status(ds, plan, TODAY)
    st.phase = P.Phase("specific", (TODAY - dt.timedelta(days=7)).isoformat(),
                       (TODAY + dt.timedelta(days=12)).isoformat(), "b", light=True, note=P.b_prep_note(b))
    ind = st.i_phase()
    assert ind.text == "B 賽輕量準備" and "要不要改成 A" in ind.why and ind.extra["light"]
    assert st.to_dict()["phase"]["light"] is True
    s = {"detail": "全程輕鬆"}
    O.b_prep_long(s, b, road=False)
    assert "比賽配速" in s["detail"] and "補給演練" in s["detail"] and "長爬坡" in s["detail"]
    r = {"detail": ""}
    O.b_prep_long(r, ev("r", "2027-01-01", "B", **HALF_B), road=True)
    assert "比賽配速" in r["detail"] and "補給" not in r["detail"] and "5:07/km" in r["detail"]


def test_week_plan_in_a_b_light_block(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY, _daily, _ds, _plan
    from backend.tests.test_overview import BOTH as OV_BOTH
    plan = _plan(lthr=165, day="2026-09-01")
    plan.thresholds.append(P.Threshold("2026-09-20", cp=250.0))
    b = ev("b", (TODAY + dt.timedelta(days=17)).isoformat(), "B", distance_km=30, climbing_m=1500, est_hours=4.0)
    plan.events = [b]
    skip = {TODAY - dt.timedelta(days=d) for d in (4, 6)}
    ds = _ds([w for w in _daily() if w.start.date() not in skip], plan)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    gi = next(i for i in st.indicators if i.id == "gate")
    gi.extra = {**gi.extra, **OV_BOTH}
    st.phase = P.Phase("specific", (TODAY - dt.timedelta(days=14)).isoformat(),
                       (TODAY + dt.timedelta(days=9)).isoformat(), "b", light=True, note=P.b_prep_note(b))
    st.kind = "specific"
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert wp["b_prep"]["event_id"] == "b" and wp["mode_label"] in ("B 賽輕量準備", "恢復週")
    assert any(n.get("src") == "phase" and "輕量準備" in n["text"] for n in wp["notes"])
    if wp["mode"] != "recovery_week":
        lng = next(s for s in wp["sessions"] if s["kind"] == "long")
        assert "補給演練" in lng["detail"]
    # its 減量 week: SP-95's B share, not an A race's 40–50 %
    st.phase = P.Phase("taper", (TODAY - dt.timedelta(days=2)).isoformat(),
                       (TODAY + dt.timedelta(days=4)).isoformat(), "b", light=True)
    st.kind = "taper"
    wt = O.week_plan(ds, st, TODAY, sport="trail")
    assert wt["mode_label"] == "B 賽減量" and any("B 賽減量" in w and "75%" in w for w in wt["why"])
