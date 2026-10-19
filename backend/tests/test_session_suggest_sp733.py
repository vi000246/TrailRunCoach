"""SP-733: 新增課表's 有氧間歇 is the session the automatic plan would schedule that day — the generator's
re-picks included: the week's Zone 3 cap → 巡航版, the day's cap → 巡航版, a CTL ramp week (load guard at
the watch line) → threshold only, the 越野 專項期 → the rung's uphill version. Both sides call
overview.ladder_session; these tests build the generator's week (quality_gate.week_decision →
overview.quality_sessions) and the dialog's suggestion (session_suggest.aerobic) from the same gate and
compare them. Synthetic only."""
from __future__ import annotations

import copy
import datetime as dt

import pytest

from backend.engine import interval_library as IL
from backend.engine import overview as O
from backend.engine import quality_gate as QG
from backend.engine import session_suggest as SS

TH = {"cp": 260.0, "lthr": 165.0, "aet": 140.0}
MON = dt.date(2026, 9, 28)
DAY = "2026-10-01"
# Zone 3 open at A2 (step 1), Zone 5 closed: a week of one Zone 3 session
GATE = {"state": "unlocked", "prefix": "", "monday": MON.isoformat(),
        "dose": {"z3": {"step": 1, "done": 1, "met": 1}, "z5": {"step": 0, "done": 0, "met": 0}},
        "z5": {"open": False, "reason": "還沒有實測 AeT 通過"}, "z3": {"open": True}}
SAME = ("variant_key", "title", "rung_key", "equiv", "progress", "variant_reps", "variant_blocks", "variant_adj",
        "minutes", "target", "detail", "terrain", "swap_reason")


@pytest.fixture(autouse=True)
def _no_history(monkeypatch):
    monkeypatch.setattr(O, "variant_history", lambda before, gate=None: [])


def _inp(hours: float, phase: str = "base", sport: str = "road") -> dict:
    return {"cur": {"week": {"start": MON.isoformat()}, "phase": phase, "mode": phase, "target": {"hours": hours},
                    "sessions": [], "primary_sport": sport, "maintain": None}, "weeks": []}


def _generator(gate: dict, hours: float, phase: str = "base", road: bool = True, cap=None) -> dict:
    """The week's Zone 3 session as week_plan builds it (week_decision → quality_sessions)."""
    dec = QG.week_decision(gate, phase, phase, MON, n=1)
    qs = O.quality_sessions(gate, dec, phase, TH, {}, hours, None, [], False if phase == "base" else not road,
                            road, cap)
    z3 = [q for q, it in zip(qs, dec["items"]) if it.get("track") == "z3"]
    assert z3, dec
    return z3[0]


def _suggest(gate: dict, hours: float, phase: str = "base", sport: str = "road", cap=None) -> dict:
    r = SS.aerobic(_inp(hours, phase, sport), DAY, gate, TH, cap=cap)
    assert r is not None and r["locked"] is None
    return r["session"]


def _same(a: dict, b: dict) -> None:
    assert {k: a.get(k) for k in SAME} == {k: b.get(k) for k in SAME}


def test_a_roomy_week_is_the_rung_itself():
    g = _generator(GATE, 8.0)
    s = _suggest(GATE, 8.0)
    _same(s, g)
    assert IL.get(s["variant_key"]).rung == "a2" and s["swap"] == "user"


def test_the_weeks_zone3_cap_gives_the_cruise_version():
    # 4.5 h: Zone 3 ≤ 10 % = 27 min < A2's 36 min in zone → 巡航版
    g = _generator(GATE, 4.5)
    s = _suggest(GATE, 4.5)
    _same(s, g)
    assert IL.get(s["variant_key"]).rung in {r[0] for r in QG.CRUISE} and "巡航版" in s["swap_reason"]


def test_the_days_cap_gives_the_cruise_version():
    # a 45-min weekday: A2 (3×12′ with warm-up / cool-down) doesn't fit → the 巡航版 that does
    g = _generator(GATE, 8.0, cap=45)
    s = _suggest(GATE, 8.0, cap=45)
    _same(s, g)
    assert "平日上限" in s["swap_reason"] and s["minutes"] <= 45


def test_a_ramp_week_is_threshold_only():
    gate = copy.deepcopy(GATE)
    gate["guard"] = {"sub": True, "block": False, "verdict": "長期負荷週增量 +5.5：本週只排閾值下"}
    g = _generator(gate, 8.0)
    s = _suggest(gate, 8.0)
    _same(s, g)
    assert s["title"] == QG.SUB[1] and s["equiv"] is False


def test_the_trail_specific_phase_takes_the_uphill_version():
    g = _generator(GATE, 8.0, phase="specific", road=False)
    s = _suggest(GATE, 8.0, phase="specific", sport="trail")
    _same(s, g)
    assert IL.get(s["variant_key"]).terrain == "hill" and s["terrain"] == "trail"
    # a road runner's 專項期: on the flat
    r = _suggest(GATE, 8.0, phase="specific", sport="road")
    assert IL.get(r["variant_key"]).terrain != "hill"


def test_both_sides_call_the_same_function(monkeypatch):
    """The re-picks live in overview.ladder_session: the generator and the dialog both go through it."""
    calls = []
    real = O.ladder_session

    def spy(*a, **k):
        calls.append(a[2])                  # kind
        return real(*a, **k)
    monkeypatch.setattr(O, "ladder_session", spy)
    _generator(GATE, 4.5)
    _suggest(GATE, 4.5)
    assert calls == ["base", "base"]


def test_a_projected_week_uses_its_own_hours_and_step():
    inp = _inp(8.0)
    nxt = (MON + dt.timedelta(days=7)).isoformat()
    inp["weeks"] = [{"start": nxt, "phase": "base", "mode": "base", "hours": 4.0,
                     "sessions": [{"kind": "quality", "rung_key": "a3", "variant_key": "a3a"}]}]
    wk = SS.week_of(inp, "2026-10-07")
    assert wk["first"] is False and wk["hours"] == 4.0
    it = SS.z3_item(GATE, wk)
    assert it["spec"][0] == "a3"                        # the step the projection reached that week
    s = SS.aerobic(inp, "2026-10-07", GATE, TH)["session"]
    assert IL.get(s["variant_key"]).rung in {r[0] for r in QG.CRUISE}     # 4 h: under A3's time in zone


def test_no_gate_state_keeps_the_ladder():
    assert SS.aerobic(_inp(8.0), DAY, {}, TH) is None
