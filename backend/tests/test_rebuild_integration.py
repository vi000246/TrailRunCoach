"""
SP-98's 回量期 (phase kind "rebuild") × the branches merged with it (integration 2026-10-05): every
phase-kind rule added next to it handles it — SP-119 strength (AA, like the 轉換期), SP-103 strides
(kept every week), SP-116 rebase (after 恢復期 + 轉換期 + 回量期), SP-97 (a past week touching it is
recovery-like). Synthetic plans and athletes.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import base_check as BC
from backend.engine import load_guard as LG
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import planning as P
from backend.engine import projection as PJ
from backend.engine import strength_plan as STP
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30

HUNDRED = dict(distance_km=160, climbing_m=9000, est_hours=30.0)     # 14-day 恢復期 + 2-week 回量期


@pytest.fixture(autouse=True)
def _three_week_transition(monkeypatch):
    monkeypatch.setattr(P, "transition_weeks_setting", lambda user_id=1: 3)


def _post(race="2027-03-06", **kw):
    ev = P.Event(id="r", name="百英里", date=race, priority="A", **{**HUNDRED, **kw})
    ph = P.auto_phases([ev], date(2026, 6, 1), date(2027, 12, 31), 3)
    return ev, ph


def test_strength_aa_in_the_rebuild_and_the_base_counts_from_the_transition():
    ev, ph = _post()
    rb = next(p for p in ph if p.kind == "rebuild")
    tr = next(p for p in ph if p.kind == "transition")
    monday = P._d(rb.start) - dt.timedelta(days=P._d(rb.start).weekday()) + dt.timedelta(weeks=1)
    assert STP.stage("rebuild", ph, monday) == "aa"
    # the 基礎期 right after the 回量期 counts its AA weeks from the 轉換期: past them already → 最大肌力
    base = next(p for p in ph if p.kind == "base" and p.start > rb.end)
    assert STP.block_start(ph, P._d(base.start)) == P._d(tr.start)
    assert STP.stage("base", ph, P._d(base.start)) == "max"
    nxt = [P.Event(id="n", name="越野", date="2027-12-01", kind="race", priority="A", distance_km=50, climbing_m=3000)]
    ctx = STP.week_context(nxt, ph, monday, "rebuild")
    assert ctx["active"] and ctx["stage"] == "aa" and "基礎循環" in STP.session(ctx)["title"]


def test_strides_kept_in_the_rebuild():
    assert O.strides_for("rebuild", "rebuild", 0, False, None) == O.TRANSITION_STRIDES
    assert O.strides_for("rebuild", "rebuild", 1, False, None) is None
    assert O.strides_for("rebuild", "reentry", 0, False, None) is None
    assert O.strides_for("recovery", "recovery", 0, False, None) is None
    n = O.transition_strides_note("rebuild")
    assert n["src"] == "transition" and "回量期" in n["text"] and "15 秒加速" in n["text"]


def test_projected_rebuild_week_has_one_set_of_strides():
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = [P.Event("r", "全馬", "2026-09-06", kind="road", priority="A", distance_km=42.2, est_hours=4.5)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 1))
    rb = [w for w in weeks if w["phase"] == "rebuild"]
    assert rb, [w["phase"] for w in weeks]
    for w in rb:
        st_ = [s for s in w["sessions"] if O.TRANSITION_STRIDES[0] in s["title"]]
        assert len(st_) == 1 and st_[0]["minutes"] <= O.TRANSITION_RUN_MAX and "Jay Johnson" in st_[0]["source"]
        assert any(n.get("src") == "transition" and "回量期照轉換期" in n["text"] for n in w["notes"])


def test_a_race_rebase_starts_after_the_rebuild():
    ev, ph = _post(race="2026-07-04")
    plan = P.Plan(events=[ev])
    rb = next(p for p in ph if p.kind == "rebuild")
    r = BC.a_race_rebase(plan, date(2026, 9, 30))
    assert r is not None and r["from"] == (P._d(rb.end) + dt.timedelta(days=1)).isoformat()


def test_a_week_touching_the_rebuild_counts_as_recovery_like():
    ev, ph = _post(race="2026-07-04")
    plan = P.Plan(events=[ev])
    rb = next(p for p in ph if p.kind == "rebuild")
    mon = P._d(rb.end) - dt.timedelta(days=P._d(rb.end).weekday())          # the rebuild's last week
    after = [mon + dt.timedelta(weeks=k) for k in range(1, 4)]                # base weeks after it
    skip = LG.skip_mondays(plan, [mon] + after)
    assert mon in skip and not (set(after) & skip)
    hours = [5.0, 6.0, 6.5, 7.0]                                              # no drop: only the phase marks it
    assert O.weeks_since_recovery(hours, [m in skip for m in [mon] + after]) == 3


def test_rebuild_after_the_transition_keeps_the_volume_monotonic():
    """Owner 2026-10-05 (kept): the 回量期 follows the 轉換期 — the post-race volume only goes up:
    恢復期 → 轉換期 → 回量期 (50 → 75 %) → 基礎期."""
    ev, ph = _post()
    kinds = [p.kind for p in ph if p.event_id == "r" or p.kind == "base"]
    i = kinds.index("recovery")
    assert kinds[i:i + 3] == ["recovery", "transition", "rebuild"]
    shares = [P.REC_SHARE, O.TRANSITION_SHARE, *P.REBUILD_SHARES]
    assert shares == sorted(shares)
