import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import overview as O


def W(sport="run", sport_type="running", tags=()):
    return SimpleNamespace(sport=sport, sport_type=sport_type, tags=list(tags))


@pytest.mark.parametrize("w, cat", [
    (W(), "road"),
    (W(sport_type="treadmill running", tags=["runningtreadmill"]), "road"),
    (W(sport_type="trail running", tags=["runningtrail"]), "trail"),
    (W("walk", "hiking", ["hiking"]), "hike"),
    (W("other", "mountaineering", ["mountaineering"]), "hike"),
    (W("road bike", "cycling", ["cycling"]), "bike"),
    (W("strength", "strength"), "strength"),
    (W("other", "strength"), "strength"),
    (W("walk", "walking", ["walking"]), "walk"),
    (W("other", "table tennis"), "other"),
])
def test_category(w, cat):
    assert O.category(w) == cat


def test_periods():
    d = dt.date(2026, 9, 30)                     # a Wednesday
    assert O.period_start(d, "week") == dt.date(2026, 9, 28)
    assert O.period_start(d, "month") == dt.date(2026, 9, 1)
    assert O.period_start(d, "year") == dt.date(2026, 1, 1)
    assert O.period_next(dt.date(2026, 1, 1), "month") == dt.date(2026, 2, 1)
    assert O.period_next(dt.date(2026, 12, 1), "month") == dt.date(2027, 1, 1)
    assert O.period_prev(dt.date(2026, 3, 1), "month") == dt.date(2026, 2, 1)
    assert O.period_prev(dt.date(2026, 1, 1), "year") == dt.date(2025, 1, 1)
    assert O.period_label(dt.date(2026, 9, 28), "week") == "9/28–10/4"
    assert O.period_label(dt.date(2026, 9, 1), "month") == "2026/09"


def test_projection_matches_tl_recurrence():
    rows = O.project(20.0, 10.0, [70.0, 0.0], 42.0, 7.0)
    c1 = 20 + (70 - 20) / 42
    a1 = 10 + (70 - 10) / 7
    assert rows[0]["ctl"] == pytest.approx(c1)
    assert rows[0]["atl"] == pytest.approx(a1)
    assert rows[0]["tsb"] == pytest.approx(10.0)          # yesterday's CTL − ATL
    assert rows[1]["tsb"] == pytest.approx(c1 - a1)
    assert rows[1]["ctl"] == pytest.approx(c1 + (0 - c1) / 42)


def test_ramp_tss_for_three_ctl_points():
    # weekly TSS that raises CTL by exactly 3 in 7 days
    cc, ctl0 = 42.0, 20.0
    f7 = 1 - (1 - 1 / cc) ** 7
    daily = ctl0 + 3 / f7
    rows = O.project(ctl0, 0.0, [daily] * 7, cc, 7.0)
    assert rows[-1]["ctl"] - ctl0 == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# week_plan with the two interval tracks (SP-31): Zone 3 (有氧間歇) and Zone 5
# ---------------------------------------------------------------------------

def _two_track_week(gate_patch, prefs=None, phase_kind=None, sport=None, monkeypatch=None):
    """week_plan on the synthetic 8 weeks of daily runs (test_quality_gate), the 間歇門檻's result
    replaced by `gate_patch` over the real one (so the tracks' state is what the test says)."""
    from backend.engine import plan_prefs as PP
    from backend.engine.planning import Threshold
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY, _daily, _ds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    plan.thresholds.append(Threshold("2026-09-20", cp=250.0))
    # last week 2 runs short: not 3 build weeks in a row, so this isn't a 3:1 recovery week
    skip = {TODAY - dt.timedelta(days=d) for d in (4, 6)}
    ds = _ds([w for w in _daily() if w.start.date() not in skip], plan)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    gi = next(i for i in st.indicators if i.id == "gate")
    gi.extra = {**gi.extra, **gate_patch}
    if phase_kind:
        st.kind = phase_kind
    return O.week_plan(ds, st, TODAY, prefs=prefs, sport=sport)


Z3_OPEN = {"open": True, "path": "weeks", "path_label": "連續 4 週護欄都過", "weeks": 4, "weeks_need": 4,
           "tests": [], "reason": "", "text": "Zone 3：已解鎖"}
BOTH = {"state": "unlocked", "guard": {}, "z3": Z3_OPEN, "z5": {"open": True, "state": "confirmed", "text": ""},
        "dose": {"done": 4, "step": 2, "z3": {"step": 2, "met": 3, "done": 3}, "z5": {"step": 1, "done": 1}},
        "ratio": {"z3": 2, "z5": 1, "why": "A 賽越野"}}


def _q(wp):
    return [s for s in wp["sessions"] if s["kind"] == "quality"]


def test_two_a_week_is_one_zone3_and_one_zone5():
    from backend.engine import plan_prefs as PP
    wp = _two_track_week(BOTH, PP.Prefs(quality=2))
    if wp["mode"] == "recovery_week":
        pytest.skip("synthetic history made this a 3:1 recovery week")
    q = _q(wp)
    assert [s["id"] for s in q] == ["quality", "quality2"]
    assert [s["rung_key"] for s in q] == ["a3", "z5b"]                   # each track its own rung, not a copy
    assert q[0]["title"] != q[1]["title"] and q[1]["title"].startswith("VO2max")
    assert wp["quality_gate"]["this_week_tracks"] == ["z3", "z5"] and wp["quality_gate"]["quality_n"] == 2
    assert q[0]["day"] and q[1]["day"] and abs((dt.date.fromisoformat(q[0]["day"]) -
                                                dt.date.fromisoformat(q[1]["day"])).days) >= 2
    assert not [n for n in wp["notes"] if n.get("src") == "z3" and "沒排" in n["text"]]


def test_one_a_week_alternates_and_says_why_no_zone3():
    from backend.tests.test_quality_gate import TODAY
    wp = _two_track_week(BOTH)
    if wp["mode"] == "recovery_week":
        pytest.skip("synthetic history made this a 3:1 recovery week")
    q = _q(wp)
    assert len(q) == 1
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    z5_turn = ((mon.toordinal() - 1) // 7) % 3 >= 2
    if z5_turn:
        assert q[0]["rung_key"] == "z5b"
        assert any(n.get("src") == "z3" and "本週輪到 5 區" in n["text"] for n in wp["notes"])
    else:
        assert q[0]["rung_key"] == "a3"


def test_zone3_locked_means_no_interval_and_a_note():
    locked = {"state": "none", "guard": {}, "z5": {"open": False, "state": "unconfirmed"},
              "z3": {"open": False, "path": None, "weeks": 2, "weeks_need": 4, "tests": [],
                     "reason": "3 區還沒解鎖：連續 2/4 週護欄都過（推估）；或做一次 90 分鐘平路 1 區測試（飄移 < 10%）",
                     "text": "Zone 3：未解鎖"},
              "dose": {"done": 0, "step": 0, "z3": {"step": 0, "met": 0, "done": 0}, "z5": {"step": 0, "done": 0}}}
    wp = _two_track_week(locked)
    if wp["mode"] == "recovery_week":
        pytest.skip("synthetic history made this a 3:1 recovery week")
    assert _q(wp) == [] and not wp["quality_gate"]["allowed"]
    note = next(n for n in wp["notes"] if n.get("src") == "z3")
    assert note["text"].startswith("本週沒排 3 區（還沒解鎖）：連續 2/4 週")
    # a guardrail block says which rule
    blocked = {**BOTH, "guard": {"block": True, "rule": "volume", "blocks": ["volume"],
                                 "verdict": "上週量增 +30%（> 20%）：本週不排間歇"}}
    wp = _two_track_week(blocked)
    assert _q(wp) == [] and any(n.get("src") == "z3" and "上週量增 +30%" in n["text"] for n in wp["notes"])
    # the low-intensity share under 75 %: Zone 3 still planned with a warning, Zone 5 waits (SP-31)
    low = {**BOTH, "guard": {"block": True, "rule": "intensity", "blocks": ["intensity"],
                             "verdict": "低強度只有 60%（< 75%，底線）：本週 5 區先不排，3 區照排",
                             "warn": "輕鬆跑心率偏高：低強度只有 60%（底線 75%、基礎期目標 ≥ 90%）——只是提醒，3 區照排；5 區先不排"}}
    from backend.engine import plan_prefs as PP
    wp = _two_track_week(low, PP.Prefs(quality=2))
    q = _q(wp)
    # only Zone 3 available: 課表偏好 2 = the rung + a different Zone 3 session (巡航版), not a copy
    assert [s["rung_key"] for s in q][:1] == ["a3"] and len(q) == 2 and q[1]["rung_key"] in ("z3a", "z3b", "z3c")
    assert q[1]["title"] != q[0]["title"] and q[1]["progress"] is False
    assert any(n.get("src") == "intensity" and n["level"] == "watch" and "60%" in n["text"] for n in wp["notes"])


def test_specific_phase_runs_the_two_tracks_with_its_own_sessions():
    from backend.engine import plan_prefs as PP
    road = _two_track_week(BOTH, PP.Prefs(quality=2), phase_kind="specific", sport="road")
    trail = _two_track_week(BOTH, PP.Prefs(quality=2), phase_kind="specific", sport="trail")
    for wp in (road, trail):
        if wp["mode"] == "recovery_week":
            pytest.skip("synthetic history made this a 3:1 recovery week")
    # road: Zone 3 = the flat 2×15′ threshold run, Zone 5 = the Zone 5 ladder (flat)
    assert [s["title"] for s in _q(road)] == [O.ROAD_SPECIFIC_Q["title"], "VO2max 4×3 分"]
    # trail: Zone 3 = the ladder (uphill versions allowed), Zone 5 = the 5×4′ hill set
    tq = _q(trail)
    assert tq[0]["rung_key"] == "a3" and tq[1]["title"] == "爬坡間歇 5×4 分"


def test_the_weeks_interval_total_stays_under_20_percent():
    # SP-31 planning rule (推估, 80/20): Zone 3 + Zone 5 time in zone ≤ 20 % of the planned running
    # time — Zone 5 first, Zone 3 gets what is left: shortened (巡航版, fewer reps), never dropped
    from backend.engine import quality_gate as QG
    gate = {"state": "unlocked", "guard": {}, "lthr": {"default": False}}
    dec = {"items": [{"track": "z3", "spec": QG.Z3[0], "advance": True, "first": False},
                     {"track": "z5", "spec": QG.Z5[3], "advance": True}]}
    notes = []
    qs = O.quality_sessions(gate, dec, "base", {"cp": 250.0}, {}, 2.5, notes=notes)    # 30′ for both
    assert [s["id"] for s in qs] == ["quality", "quality2"]
    z3, z5 = qs
    assert z5["variant_key"] == "v4a" and O.session_tiz_min(z5) == 16
    assert z3["rung_key"] in ("a1", "z3a") and O.session_tiz_min(z3) <= 30 - 16
    assert O.session_tiz_min(z3) + O.session_tiz_min(z5) <= 0.20 * 2.5 * 60 + 1e-6
    assert any(n["src"] == "quality_share" and "20%" in n["text"] for n in notes)
    # plenty of volume: untouched
    notes = []
    qs = O.quality_sessions(gate, dec, "base", {"cp": 250.0}, {}, 8.0, notes=notes)
    assert [s["variant_key"] for s in qs] == ["a1a", "v4a"] and not notes


def test_two_a_week_with_zone5_closed_is_a_long_tempo_and_a_cruise_session():
    # owner 2026-10-04 (SP-31 follow-up): 課表偏好 2 a week, only the Zone 3 track open → the rung
    # (long tempo) + a different Zone 3 session (巡航版 of about the same time in zone), not a copy;
    # both within Zone 3 ≤ 10 % of the week and the week's interval total; notes say so
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import quality_gate as QG
    gate = {"state": "none", "guard": {}, "z3": {"open": True}, "z5": {"open": False},
            "dose": {"z3": {"step": 0, "met": 1, "done": 1}, "z5": {}}}
    d = QG.week_decision(gate, "base", "base", n=2)
    assert [(it["track"], bool(it.get("cruise")), it["advance"]) for it in d["items"]] == [
        ("z3", False, True), ("z3", True, False)]
    notes = []
    q = O.quality_sessions(gate, d, "base", {"cp": 250.0}, {}, 10.0, PP.Prefs(), [], notes=notes)
    assert [(s["id"], s["title"], s["rung_key"], s["progress"]) for s in q] == [
        ("quality", "閾值 2×15 分", "a1", True), ("quality2", "閾值 3×8 分", "z3b", False)]
    assert sum(O.session_tiz_min(s) for s in q) <= QG.z3_budget_min(10.0) + 1e-6
    assert any(n["src"] == "z3" and "第二堂排不同的 3 區課（巡航版）" in n["text"] for n in notes)
    # 5 h: 10 % = 30′ is all A1's 2×15′ → no room for a second one, a note says why
    notes = []
    q = O.quality_sessions(gate, d, "base", {"cp": 250.0}, {}, 5.0, PP.Prefs(), [], notes=notes)
    assert [s["title"] for s in q] == ["閾值 2×15 分"] and any("本週排 1 堂" in n["text"] for n in notes)
    # the first session itself a 巡航版 (the first Zone 3 week, 5 %): the second is a different structure
    g0 = {**gate, "dose": {"z3": {"step": 0, "met": 0, "done": 0}, "z5": {}}}
    q = O.quality_sessions(g0, QG.week_decision(g0, "base", "base", n=2), "base", {"cp": 250.0}, {}, 8.0,
                           PP.Prefs(), [])
    assert len(q) == 2 and q[0]["title"] != q[1]["title"] and q[0]["rung_key"] == "a1"
    # both tracks open: one Zone 3 + one Zone 5 as before (no 巡航版 item)
    both = {**gate, "z5": {"open": True}, "z3_recent": {"done": 2}}
    assert [it["track"] for it in QG.week_decision(both, "base", "base", n=2)["items"]] == ["z3", "z5"]
