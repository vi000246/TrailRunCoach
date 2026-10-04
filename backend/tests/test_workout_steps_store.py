"""
Stored structures (plan_sessions.steps, engine/workout_steps.py): saving one is a
user edit that auto-replan keeps, the push uses it, a new library variant drops it,
a CP change rescales its absolute watts (engine/plan_auto.rescale_sessions).
Fake DB, mocked COROS.
"""
from datetime import date

import pytest

from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import make_db, run
from backend.tests.test_plan_auto import Box, base_inputs
from backend.tests.test_plan_auto_cp import ThPush, _by_title, with_cp
from backend.tests.test_plan_store import inputs, next_week


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


STEPS = {"items": [
    {"kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "auto", "intent": "easy"}},
    {"kind": "repeat", "times": 4, "items": [
        {"kind": "work", "dur": {"type": "time", "value": 240},
         "target": {"type": "power", "mode": "pct", "lo": 1.04, "hi": 1.08}},
        {"kind": "rest", "dur": {"type": "time", "value": 180}, "target": {"type": "none"}, "note": "慢跑"}]},
    {"kind": "work", "dur": {"type": "time", "value": 300}, "target": {"type": "power", "mode": "abs", "lo": 200, "hi": 210}},
    {"kind": "cool", "dur": {"type": "time", "value": 300}, "target": {"type": "auto", "intent": "easy"}}]}


def test_saved_steps_are_a_user_edit_kept_by_reconcile_and_pushed():
    db = run(make_db())
    inp = inputs(weeks=[next_week()])
    run(PS.plan_reconcile(db, inp, apply=True))
    ss = run(PS.load(db))
    easy = next(s for s in ss if s.get("gen_key") == "easy1" and s["week_start"] == "2026-09-28")
    assert easy["steps"] is None
    e = run(PS.edit(db, easy["uid"], {"steps": STEPS}, "2026-09-30"))
    assert e["edited"] and e["steps"]["origin"] == "user" and len(e["steps"]["items"]) == 4
    run(PS.plan_reconcile(db, inputs(weeks=[next_week()]), apply=True))       # auto-replan
    kept = next(s for s in run(PS.load(db)) if s["uid"] == easy["uid"])
    assert kept["steps"] == e["steps"]
    pd = PS.push_dict(kept)
    assert pd["steps"] == e["steps"]
    th = CW.Thresholds(cp=250, lthr=168, aet=150)
    got = CW.session_steps(pd, th)
    assert isinstance(got[1], CW.Repeat) and got[1].sets == 4
    assert got[1].steps[0].intensity == ("power", 260, 270)
    assert got[2].intensity == ("power", 200, 210)
    # relative steps follow the CP: the COROS fingerprint changes (「已過期」)
    a = CW.session_workout({**pd, "day": "2026-10-01"}, {"cp": 250, "lthr": 168, "aet": 150})
    b = CW.session_workout({**pd, "day": "2026-10-01"}, {"cp": 260, "lthr": 168, "aet": 150})
    assert a.fingerprint != b.fingerprint
    # clearing the structure goes back to the text
    c = run(PS.edit(db, easy["uid"], {"steps": None}, "2026-09-30"))
    assert c["steps"] is None
    with pytest.raises(PS.PlanError):
        run(PS.edit(db, easy["uid"], {"steps": {"items": []}}, "2026-09-30"))


def test_a_new_variant_drops_the_old_structure():
    db = run(make_db())
    run(PS.plan_reconcile(db, inputs(weeks=[next_week()]), apply=True))
    q = next(s for s in run(PS.load(db)) if s["kind"] == "quality")
    run(PS.edit(db, q["uid"], {"steps": STEPS}, "2026-09-30"))
    r = run(PS.edit(db, q["uid"], {"_variant": {"variant_key": "v1a", "rung_key": "z5a", "swap": "user"}}, "2026-09-30"))
    assert r["steps"] is None and r["variant_key"] == "v1a"


def test_cp_change_rescales_structured_watts_and_repushes(monkeypatch):
    b = Box(monkeypatch, base_inputs(), ThPush())

    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        q = _by_title(await PS.load(db), "有氧間歇（巡航）3×10 分")
        await PS.edit(db, q["uid"], {"steps": STEPS}, "2026-09-30")
        n0 = len(b.push.calls)
        b.inp = with_cp(base_inputs(), 330.0)
        r = await PA.run(db, trigger="cp_change")
        assert r["cp_change"]["n"] >= 1
        q2 = next(s for s in await PS.load(db) if s["uid"] == q["uid"])
        absolute = q2["steps"]["items"][2]["target"]
        assert (absolute["lo"], absolute["hi"]) == (220, 231)                  # 200–210 W × 330 / 300
        assert q2["steps"]["items"][1]["items"][0]["target"]["lo"] == 1.04     # % CP: unchanged, follows
        assert len(b.push.calls) == n0 + 1 and q["uid"] in {s["id"] for s in b.push.calls[-1]["sessions"]}
        sent = next(s for s in b.push.calls[-1]["sessions"] if s["id"] == q["uid"])
        steps = CW.session_steps(sent, CW.Thresholds(cp=330.0))
        assert steps[1].steps[0].intensity == ("power", round(1.04 * 330), round(1.08 * 330))
        assert steps[2].intensity == ("power", 220, 231)
    run(go())


def test_rescale_abs_power_untouched_without_absolute():
    d = WS.normalize({"items": [STEPS["items"][1]]})
    assert WS.rescale_abs_power(d, 300, 330) is d
