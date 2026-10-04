"""
SP-73: the 轉換期 after an A race's 恢復期 (engine/planning.auto_phases) — its length setting
(課表偏好 transition_weeks), the next A race's 專項期 winning, manual phases winning, and the
volume (overview.transition_hours: 50 % of the pre-race level, each run ≤ 60 min) agreeing
between week_plan and the projection. Synthetic plans only.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import planning as P


def ev(name, date_, pri="A", **kw):
    return P.Event(id=name, name=name, date=date_, priority=pri, **kw)


def kinds(ph):
    return [(p.kind, p.start, p.end) for p in ph]


RACE = ev("r1", "2026-12-20", distance_km=50, climbing_m=3000)      # long: recovery 12/21 – 1/3


def test_transition_follows_the_recovery_and_its_length_is_a_setting():
    b, e = date(2026, 6, 1), date(2027, 4, 1)
    three = kinds(P.auto_phases([RACE], b, e))
    assert three[4:7] == [("recovery", "2026-12-21", "2027-01-03"), ("transition", "2027-01-04", "2027-01-24"),
                          ("base", "2027-01-25", "2027-04-01")]
    four = kinds(P.auto_phases([RACE], b, e, transition_weeks=4))
    assert four[5] == ("transition", "2027-01-04", "2027-01-31")
    # 0 = off: exactly the phases before SP-73
    off = kinds(P.auto_phases([RACE], b, e, transition_weeks=0))
    assert off[4:] == [("recovery", "2026-12-21", "2027-01-03"), ("base", "2027-01-04", "2027-04-01")]
    assert not any(p.kind == "transition" for p in P.auto_phases([RACE], b, e, 0))


def test_next_a_race_specific_phase_wins_transition_shortened_or_skipped():
    b, e = date(2026, 6, 1), date(2027, 6, 1)
    # race 2's 專項期 starts 1/14 (race − 14 d − 8 weeks): 10 days of 轉換期 are left
    r2 = ev("r2", "2027-03-25", est_hours=4)
    ph = P.auto_phases([RACE, r2], b, e)
    tr = [p for p in ph if p.kind == "transition" and p.event_id == "r1"][0]
    assert (tr.start, tr.end) == ("2027-01-04", "2027-01-13")
    assert "縮短為 10 天" in tr.note and "r2" in tr.note
    sp = [p for p in ph if p.kind == "specific" and p.event_id == "r2"][0]
    assert sp.start == "2027-01-14"                                  # unchanged by the 轉換期
    assert kinds(P.auto_phases([RACE, r2], b, e, 0)) != kinds(ph)
    assert [p for p in P.auto_phases([RACE, r2], b, e, 0) if p.kind == "specific" and p.event_id == "r2"][0].start \
        == "2027-01-14"
    # race 2's 專項期 starts 1/8: < 7 days → no 轉換期, the recovery phase says why
    r3 = ev("r3", "2027-03-19", est_hours=4)
    ph = P.auto_phases([RACE, r3], b, e)
    assert not any(p.kind == "transition" and p.event_id == "r1" for p in ph)
    rec = [p for p in ph if p.kind == "recovery" and p.event_id == "r1"][0]
    assert rec.note.startswith("沒有轉換期") and "r3" in rec.note
    assert [p for p in ph if p.kind == "specific" and p.event_id == "r3"][0].start == "2027-01-08"
    for p, q in zip(ph, ph[1:]):
        assert P._d(q.start) == P._d(p.end) + dt.timedelta(days=1)   # still contiguous
    # B races never shorten it
    ph = P.auto_phases([RACE, ev("b", "2027-01-10", "B")], b, e)
    assert [(p.start, p.end) for p in ph if p.kind == "transition"] == [("2027-01-04", "2027-01-24")]


def test_manual_phases_win_and_the_setting_is_read(monkeypatch):
    plan = P.Plan(events=[RACE])
    monkeypatch.setattr(P, "transition_weeks_setting", lambda user_id=1: 0)
    assert not any(p.kind == "transition" for p in P.phases(plan, date(2026, 6, 1), date(2027, 4, 1)))
    monkeypatch.setattr(P, "transition_weeks_setting", lambda user_id=1: 4)
    assert P.phase_on(plan, date(2027, 1, 30)).kind == "transition"
    assert P.phase_on(plan, date(2027, 1, 30), transition_weeks=3).kind == "base"
    plan.phases = [P.Phase("base", "2026-06-01", "2027-04-01", auto=False)]
    assert P.phase_on(plan, date(2027, 1, 10)).kind == "base"        # the user's phases: no 轉換期 added


def test_setting_default_and_out_of_range(monkeypatch):
    from backend.engine.wko5expr import datasource as DS
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, u=1: d)
    assert P.transition_weeks_setting() == 3
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, u=1: 9)
    assert P.transition_weeks_setting() == 4
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, u=1: "x")
    assert P.transition_weeks_setting() == 3


def test_prefs_field_is_not_shaping_and_validated():
    from backend.settings.repository import DEFAULTS, validate
    assert DEFAULTS["plan.prefs.transition_weeks"] == 3
    assert not PP.Prefs(transition_weeks=4).active and not PP.Prefs(transition_weeks=0).active
    assert PP.from_settings({"plan.prefs.transition_weeks": 0}).transition_weeks == 0
    assert PP.Prefs(transition_weeks=2).settings()["plan.prefs.transition_weeks"] == 2
    PP.check(PP.Prefs(transition_weeks=4))
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(transition_weeks=5))
    validate("plan.prefs.transition_weeks", 0)
    with pytest.raises(ValueError):
        validate("plan.prefs.transition_weeks", 6)


def test_pre_race_mondays_and_transition_hours():
    ph = P.auto_phases([RACE], date(2026, 6, 1), date(2027, 4, 1))
    # taper 12/6 (Sun) → its week starts 11/30; the 4 complete weeks before it
    assert P.pre_race_mondays(ph, date(2027, 1, 10)) == [date(2026, 11, 2), date(2026, 11, 9),
                                                         date(2026, 11, 16), date(2026, 11, 23)]
    assert P.pre_race_mondays(ph, date(2026, 12, 1)) == []           # no race before it
    h, why = O.transition_hours(8.0, 3.0)
    assert h == pytest.approx(4.0) and "賽前 4 週平均 8.0 h × 50%" in why
    assert O.transition_hours(None, 3.0)[0] == pytest.approx(0.65 * 3.0)      # no race known: the old rule
    assert O.easy_count(300, "transition") == 5 and O.easy_count(301, "transition") == 6
    ss = [{"kind": "easy", "minutes": 80, "tss": 80.0, "done": False}, {"kind": "strength", "minutes": 35, "tss": 20.0}]
    notes = []
    assert O.cap_transition_runs(ss, notes) == 20 and ss[0]["minutes"] == 60 and ss[0]["tss"] == 60.0
    assert "Canova" in notes[0]["text"]


def test_week_plan_and_projection_agree_on_the_transition_weeks():
    """A short A race on 9/20 (recovery 9/21–27): TODAY 9/30 is 轉換期 week 1 of 3. Easy runs only
    (≤ 60 min) + strength, 50 % of the 4 build weeks before the taper; the projected 轉換期 weeks
    get the same hours, then the base phase comes back."""
    from backend.engine import projection as PJ
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = [P.Event("r", "路跑", "2026-09-20", kind="road", priority="A", est_hours=3.0)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    assert st.kind == "transition"
    wp = O.week_plan(ds, st, TODAY)
    ref = wp["transition_ref"]
    assert ref["mondays"] == ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24"]
    assert ref["hours"] == pytest.approx(370 / 60.0)                 # the build weeks: 50+60+50+150+60 min
    assert wp["target"]["hours"] == pytest.approx(0.5 * 370 / 60.0)
    assert wp["mode"] == "transition" and wp["mode_label"] == "轉換期"
    run = [s for s in wp["sessions"] if s["kind"] not in ("strength",)]
    assert run and all(s["kind"] == "easy" and s["minutes"] <= O.TRANSITION_RUN_MAX for s in run)
    assert not any("衝刺" in s["title"] or "加速" in s["title"] for s in run)
    assert sum(1 for s in wp["sessions"] if s["kind"] == "strength") == 2
    assert any(n.get("src") == "transition" and "交叉訓練" in n["text"] for n in wp["notes"])
    assert not wp["test_suggestions"] or all(t["kind"] != "cp" for t in wp["test_suggestions"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 25))
    tr = [w for w in weeks if w["phase"] == "transition"]
    assert [w["start"] for w in tr] == ["2026-10-05", "2026-10-12"]
    assert all(w["hours"] == pytest.approx(wp["target"]["hours"]) for w in tr)
    assert all(all(s["kind"] in ("easy", "strength") and (s["kind"] == "strength" or s["minutes"] <= 60)
                   for s in w["sessions"]) for w in tr)
    assert all(any(n.get("src") == "transition" for n in w.get("notes") or []) for w in tr)
    nxt = [w for w in weeks if w["start"] == "2026-10-19"][0]
    assert nxt["phase"] == "base" and any(s["kind"] == "long" for s in nxt["sessions"])
    # 課表偏好 with 3 runs a week: the Canova cap still holds after the shaping
    prefs = PP.Prefs(runs=3)
    wp3 = O.week_plan(ds, st, TODAY, prefs=prefs)
    assert all(s["minutes"] <= 60 for s in wp3["sessions"] if s["kind"] == "easy")
    for w in PJ.project_weeks(wp3, _phases(plan, TODAY), date(2026, 10, 18), prefs=prefs):
        assert all(s["minutes"] <= 60 for s in w["sessions"] if s["kind"] == "easy")
