"""What stays of the 熱適應課 plumbing after SP-569 (the plan schedules none any more,
test_heat_auto_sp569.py): rows stored before still push / move like before, and an event's heat
(hot / cool / auto) decides the race-day heat and the 熱適應 card's hot race."""
from __future__ import annotations

import datetime as dt

import pytest

from backend.engine.planning import Event

TODAY = dt.date(2026, 10, 5)                       # a Monday


def ev(days_to, heat="hot", prio="A", kind="race"):
    return Event(id="e1", name="熱天賽", date=(TODAY + dt.timedelta(days=days_to)).isoformat(), kind=kind,
                 priority=prio, heat=heat)


def test_coros_heat_run_steps_and_passive_never_pushed():
    from backend.sync import coros_workouts as CW
    th = CW.Thresholds.of({"aet": 142, "lthr": 160, "cp": 200})
    steps = CW.session_steps({"kind": "easy", "title": "熱適應輕鬆跑", "minutes": 60, "heat": True}, th)
    assert [s.kind for s in steps] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert [s.seconds for s in steps] == [600, 2700, 300] and steps[2].name == "走路降溫"
    with pytest.raises(CW.Unsupported, match="被動熱適應不推"):
        CW.session_steps({"kind": "heat_passive", "title": "跑後熱水浴", "minutes": 40}, th)


def test_move_to_keeps_heat_passive_off_the_main_days():
    from backend.engine import blackouts as BL
    wk = [{"kind": "easy", "day": "2026-10-06", "state": "active"},
          {"kind": "heat_passive", "day": "2026-10-07", "state": "active"}]
    s = {"kind": "easy", "day": "2026-10-08", "state": "active"}
    # the bath on 10/07 does not hold that day: the easy run may move there
    assert BL.move_to(s, wk + [s], {"2026-10-08": None}, "2026-10-05") == "2026-10-07"
    hp = wk[1]
    assert BL.move_to(hp, wk, {"2026-10-07": None}, "2026-10-05") in ("2026-10-06", "2026-10-08")


def test_event_heat_field_round_trips():
    from backend.engine import planning as P
    plan = P.Plan()
    e = plan.upsert_event({"name": "x", "date": "2026-11-01", "heat": "hot"})
    assert e.heat == "hot"
    assert plan.upsert_event({"name": "y", "date": "2026-11-01", "heat": "bogus"}).heat == "auto"
    assert P.event_json(e, TODAY)["heat"] == "hot"


def test_event_is_hot_auto_rules():
    from backend.engine import heat_data as HD
    past = [{"date": f"{y}-10-{d:02d}", "hadley": 155} for y in (2024, 2025) for d in (20, 22, 25)]
    assert HD.event_is_hot(ev(20, heat="auto"), past)["hot"]
    assert not HD.event_is_hot(ev(20, heat="auto", kind="baiyue"), past)["hot"]
    assert not HD.event_is_hot(ev(20, heat="auto"), [])["hot"]
