"""
SP-668: the switch from the 14-day to the 10-day 減量期 reaches the stored plan and the watch only through
the existing rules (engine/plan_auto.py, reconcile) — nothing is re-pushed by force:

  * a stored week whose phase was 減量期 and is now 專項期 (today is 11–14 days before the A race) is a
    phase change: held as a proposal, the stored sessions and what is on the watch stay until approved;
  * the sessions coming back (the long run, the full quality session) raise the week's load: held too;
  * the race guard (A 賽前 14 天 removing a long / quality / test) is unchanged — it never was the taper;
  * a change under every threshold (same phase, a run 10 min longer) applies on its own, as before.

The DB is in memory; COROS is never called (Push records). Synthetic.
"""
from datetime import date

import pytest

from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import make_db, run
from backend.tests.test_plan_auto import Box, Push, _active, later
from backend.tests.test_plan_store import ACT_929, cur_plan, g, inputs, next_week

@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


RACE_DAYS = 11          # today Wed 9/30, the A race Sun 10/11: 14-day taper from 9/27, 10-day from Thu 10/1


def taper14_week():
    """This week as the 14-day taper planned it: the full taper quality, no long run 7 days out… the
    taper's shorter runs."""
    return [g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
            g("quality", "quality", "上坡節奏跑 3×10 分（緩坡）", 85, "2026-10-01"),
            g("easy1", "easy", "輕鬆跑", 40, "2026-10-02"),
            g("long", "long", "長跑（減量期，≤ 90 分）", 90, "2026-10-04")]


def taper10_week():
    """The same week after SP-668: Mon–Wed 專項期, Thu–Sun the taper's first days (straddle_hours) — the
    quality on Thu (10 out) is the full template, the runs a bit longer, the long run still ≤ 90."""
    return [g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
            g("quality", "quality", "上坡節奏跑 3×10 分（緩坡）", 85, "2026-10-01"),
            g("easy1", "easy", "輕鬆跑", 50, "2026-10-02"),
            g("long", "long", "長跑（減量期，≤ 90 分）", 90, "2026-10-04")]


def _inp(sessions, phase="taper", hours=4.0, weeks=None, new=0):
    inp = inputs(cur=cur_plan(sessions=sessions, hours=hours), weeks=weeks if weeks is not None else [next_week()])
    inp["phase"] = {"kind": phase, "label": phase, "start": "2026-09-27" if phase == "taper" else "2026-08-02",
                    "end": "2026-10-10" if phase == "taper" else "2026-09-30"}
    inp["days_to_next_a"] = RACE_DAYS
    return later(inp, new) if new else inp


def test_taper_to_specific_is_a_phase_change_held_and_not_pushed(monkeypatch):
    b = Box(monkeypatch, _inp(taper14_week()), Push())

    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        n0 = len(b.push.calls)
        sent = [s["id"] for s in b.push.calls[-1]["sessions"]]
        # after the deploy: today is in the 專項期 (the 10-day taper starts tomorrow)
        b.inp = _inp(taper10_week(), phase="specific", new=1)
        r = await PA.run(db)
        assert r["status"] == "pending"
        assert "phase" in [x["rule"] for x in PA.entry_dict(await PA.pending(db))["big"]]
        # the stored plan waits; the watch only gets the 「課表待確認」 notice, no session is re-sent
        assert _active(await PS.load(db))["easy1"]["minutes"] == 40
        new_pushes = b.push.calls[n0:]
        assert all(s["kind"] == "notice" for c in new_pushes for s in c["sessions"])
        assert sent
    run(go())


def test_the_long_run_coming_back_is_a_load_increase_held(monkeypatch):
    """Same phase on both sides (a manual 專項期 → still 專項期): what the 10-day taper gives back this week
    (a long run where the 14-day taper had none) is a > +20 % week: held, like any increase."""
    week14 = [s for s in taper14_week() if s["id"] != "long"]
    week10 = taper14_week()[:3] + [g("long", "long", "LSD（山路）", 150, "2026-10-04")]
    b = Box(monkeypatch, _inp(week14, phase="specific"), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        n0 = len(b.push.calls)
        b.inp = _inp(week10, phase="specific", new=1)
        r = await PA.run(db)
        assert r["status"] == "pending"
        assert [x["rule"] for x in PA.entry_dict(await PA.pending(db))["big"]] == ["tss"]
        assert "long" not in _active(await PS.load(db))
        assert all(s["kind"] == "notice" for c in b.push.calls[n0:] for s in c["sessions"])
    run(go())


def test_race_guard_is_still_14_days():
    """RACE_GUARD_DAYS is plan_auto's own window (removing a long / quality / test before an A race), not
    the taper length: SP-668 leaves it at 14."""
    assert PA.RACE_GUARD_DAYS == 14
    st = [{"uid": "q", "day": "2026-09-29", "kind": "quality", "state": "active", "tss": 60.0, "minutes": 60,
           "title": "有氧間歇"}]
    items = PA.diff(st, [], [], [])
    big = PA.classify(st, [], items, "2026-09-28", "2026-10-04", "specific", "specific", 13)   # 12 days out
    assert [b["rule"] for b in big] == ["race"]


def test_a_small_change_applies_on_its_own(monkeypatch):
    """A change under every threshold (one easy run 10 min longer, same phase) applies on its own and goes
    out by the usual push window — no proposal, no notice."""
    b = Box(monkeypatch, _inp(taper14_week(), phase="taper"), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = _inp(taper10_week(), phase="taper", new=1)
        r = await PA.run(db)
        assert r["status"] == "applied"
        assert _active(await PS.load(db))["easy1"]["minutes"] == 50
        assert not [x for x in await PS.load(db) if x["kind"] == "notice"]          # nothing to confirm
    run(go())
