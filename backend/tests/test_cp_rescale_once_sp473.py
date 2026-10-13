"""
SP-473 (P0, production 2026-10-13): the cp_change run multiplied the user's own sessions twice.

plan.auto.state.cp was 175.43 (the last automatic run, 10/08) while the CP in effect was 191.0.
Before the run, the stored plan had already been written with 191 by paths that are not the
automatic run (the 課表 page's first-visit reconcile, the user's moves / adds copying those
texts): the easy runs said 「功率 124–153 W」 (65–80 % × 191). The cp_change run then rescaled
every upcoming session by 191 / 175 (plan_auto.rescale_sessions with state.cp as the old CP):
the auto sessions came out right only because reconcile regenerated them afterwards; the
custom / edited ones (10/15, 10/16) were left at 「功率 135–167 W」 and pushed to the watch.

A session written with the CP in effect must not be rescaled again; one written with the old
CP (before it moved) still follows the new CP (SP-468's rule for edited sessions, unchanged).

Fix (the user's option A): each stored row records the CP its watts were written with
(plan_sessions.cp_basis: reconcile's generated rows, an add, a variant / structure, an edit that
changes the watts take the CP in effect; a move or a title edit keeps it) and rescale_sessions
rescales each row from its own cp_basis (plan.auto.state.cp only for a row without one).

Synthetic data only — never the real app DB or ~/.wko5coach.
"""
import json
from datetime import date

from backend.tests.test_cp_hold_sp468 import _inputs

TODAY = "2026-09-30"


def _get(ss, uid):
    return next(s for s in ss if s["uid"] == uid)


def test_sessions_written_with_the_new_cp_are_not_rescaled_twice(monkeypatch):
    from backend.engine import plan_auto as PA
    from backend.engine import plan_store as PS
    from backend.sync import coros_workouts as CW
    from backend.tests.test_coros_workouts import make_db, run
    from backend.tests.test_plan_auto import Box
    from backend.tests.test_plan_auto_cp import ThPush
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    b = Box(monkeypatch, _inputs(175.4, "功率 114–140 W"), ThPush())

    async def go():
        db = await make_db()
        await PA.run(db)                                  # the last automatic run: state.cp 175.4
        assert (await PA.settings(db))["state"]["cp"] == 175.4
        ss = await PS.load(db)
        long_ = next(s for s in ss if s.get("gen_key") and "long" in s["gen_key"] and s["state"] == "active"
                     and s["week_start"] == "2026-09-28")
        # an edit made while 175.4 was in effect: its watts are 65–80 % × 175 → must follow 191
        await PS.edit(db, long_["uid"], {"target": "功率 114–140 W"}, TODAY)

        # the CP in effect moves to 191.0 (a refit) with no automatic run yet …
        b.inp = _inputs(191.0, "功率 124–153 W")
        # … and the 課表 page's first-visit reconcile writes the plan with it (api/plan_sessions._ensure)
        await PS.plan_reconcile(db, b.inp, apply=True)
        easy = next(s for s in await PS.load(db) if s.get("gen_key") and "easy1" in s["gen_key"]
                    and s["state"] == "active" and s["week_start"] == "2026-09-28")
        assert easy["target"] == "功率 124–153 W"
        # the user moves that easy run to next week (custom, edited — like 10/15 / 10/16 in production)
        moved = await PS.edit(db, easy["uid"], {"day": "2026-10-07"}, TODAY)
        assert moved["origin"] == "custom" and moved["edited"]
        # and adds one of their own with the target the page shows now
        own = await PS.add(db, {"kind": "easy", "title": "輕鬆跑", "day": "2026-10-03", "minutes": 40,
                                "target": "功率 124–153 W"}, TODAY, cp=191.0)
        assert (await PA.settings(db))["state"]["cp"] == 175.4        # the run has not seen 191 yet

        r = await PA.run(db, trigger="cp_change")             # what check_cp starts
        assert r["cp_change"]["old"] == 175.4 and r["cp_change"]["new"] == 191.0
        after = await PS.load(db)
        # written with 191 already: unchanged (was 「功率 135–167 W」 = × 191 / 175 a second time)
        assert _get(after, moved["uid"])["target"] == "功率 124–153 W"
        assert _get(after, own["uid"])["target"] == "功率 124–153 W"
        # written with 175.4: follows the new CP (SP-468: edited sessions are rescaled, × 191 / 175.4)
        assert _get(after, long_["uid"])["target"] == PA.rescale_watts("功率 114–140 W", 175.4, 191.0) \
            == "功率 124–152 W"
        # the change log's 「before」 shows what was stored, never a value × the ratio (production:
        # the auto 10/14 easy run's before was 「135–167 W」 though it said 「124–153 W」)
        rows = [x for x in await PA_log(db) if x.trigger == "cp_change"]
        befores = json.dumps([json.loads(x.before_json or "[]") for x in rows] +
                             [PA.entry_dict(x)["items"] for x in rows], ensure_ascii=False)
        assert "135–167" not in befores
    run(go())


async def PA_log(db):
    from sqlalchemy import select
    from backend.db.models import PlanChangeLog
    return (await db.execute(select(PlanChangeLog).order_by(PlanChangeLog.id))).scalars().all()


def test_auto_off_for_a_while_then_on_rescales_each_row_once(monkeypatch):
    """plan.auto.enabled off: no run, state.cp stays 175.4 while the CP in effect goes 191 then 200
    and the page writes the plan with each. Back on, each row goes from its own CP to 200 once —
    not × 200 / 175.4 on top of what it already had."""
    from backend.engine import plan_auto as PA
    from backend.engine import plan_store as PS
    from backend.settings.repository import SettingsRepository
    from backend.sync import coros_workouts as CW
    from backend.tests.test_coros_workouts import make_db, run
    from backend.tests.test_plan_auto import Box
    from backend.tests.test_plan_auto_cp import ThPush
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    b = Box(monkeypatch, _inputs(175.4, "功率 114–140 W"), ThPush())

    async def go():
        db = await make_db()
        await PA.run(db)
        long_ = next(s for s in await PS.load(db) if s.get("gen_key") and "long" in s["gen_key"]
                     and s["state"] == "active" and s["week_start"] == "2026-09-28")
        await PS.edit(db, long_["uid"], {"target": "功率 114–140 W"}, TODAY, cp=175.4)
        await SettingsRepository(db).set("plan.auto.enabled", False)
        await db.commit()

        b.inp = _inputs(191.0, "功率 124–153 W")
        assert (await PA.run(db, trigger="cp_change"))["status"] == "disabled"
        assert not await PA.check_cp(db, b.inp)
        await PS.plan_reconcile(db, b.inp, apply=True)               # the page, with 191
        own = await PS.add(db, {"kind": "easy", "title": "輕鬆跑", "day": "2026-10-03", "minutes": 40,
                                "target": "功率 124–153 W"}, TODAY, cp=191.0)
        b.inp = _inputs(200.0, "功率 130–160 W")
        await PS.plan_reconcile(db, b.inp, apply=True)               # the page, with 200
        assert (await PA.settings(db))["state"]["cp"] == 175.4

        await SettingsRepository(db).set("plan.auto.enabled", True)
        await db.commit()
        r = await PA.run(db, trigger="cp_change")
        assert r["cp_change"]["old"] == 175.4 and r["cp_change"]["new"] == 200.0
        after = {s["uid"]: s for s in await PS.load(db)}
        assert after[own["uid"]]["target"] == PA.rescale_watts("功率 124–153 W", 191.0, 200.0) == "功率 130–160 W"
        assert after[long_["uid"]]["target"] == PA.rescale_watts("功率 114–140 W", 175.4, 200.0) == "功率 130–160 W"
        easy = next(s for s in after.values() if s.get("gen_key") and "easy1" in s["gen_key"]
                    and s["state"] == "active" and s["week_start"] == "2026-09-28")
        assert easy["target"] == "功率 130–160 W"
        assert {after[u]["cp_basis"] for u in (own["uid"], long_["uid"], easy["uid"])} == {200.0}
    run(go())


def test_cp_basis_kept_by_a_move_and_a_title_edit_taken_by_new_watts():
    """restamp_cp: a move (another week too) or a title / minutes edit keeps the row's cp_basis;
    an edit that writes other watts, a library variant or a saved structure takes the CP in effect;
    a new row takes it; the page's reconcile stamps the rows it regenerates, never an edited one."""
    from backend.engine import plan_store as PS
    from backend.tests.test_coros_workouts import make_db, run

    async def go():
        db = await make_db()
        await PS.plan_reconcile(db, _inputs(175.4, "功率 114–140 W"), apply=True)
        ss = await PS.load(db)
        easy = next(s for s in ss if s.get("gen_key") == "easy1" and s["state"] == "active")
        assert easy["cp_basis"] == 175.4 and easy["target"] == "功率 114–140 W"
        uid = easy["uid"]

        async def basis():
            return next(s for s in await PS.load(db) if s["uid"] == uid)["cp_basis"]
        await PS.edit(db, uid, {"day": "2026-10-03"}, TODAY, cp=191.0)                 # drag, same week
        assert await basis() == 175.4
        await PS.edit(db, uid, {"title": "輕鬆跑（河濱）", "minutes": 50}, TODAY, cp=191.0)
        assert await basis() == 175.4
        # the dialog saves every field as it was: same watts → kept
        await PS.edit(db, uid, {"title": "輕鬆跑（河濱）", "target": "功率 114–140 W"}, TODAY, cp=191.0)
        assert await basis() == 175.4
        moved = await PS.edit(db, uid, {"day": "2026-10-07"}, TODAY, cp=191.0)         # to another week
        assert moved["origin"] == "custom" and await basis() == 175.4
        # the reconcile with 191 leaves the edited row alone
        await PS.plan_reconcile(db, _inputs(191.0, "功率 124–153 W"), apply=True)
        assert await basis() == 175.4
        # the watts typed anew: the CP in effect
        await PS.edit(db, uid, {"target": "功率 124–153 W"}, TODAY, cp=191.0)
        assert await basis() == 191.0
        # a saved structure (its summary is written with the CP in effect) / a variant: the CP in effect
        steps = {"items": [{"kind": "work", "dur": {"type": "time", "value": 1800},
                            "target": {"type": "power", "mode": "pct", "lo": 0.65, "hi": 0.80}}]}
        await PS.edit(db, uid, {"steps": steps}, TODAY, cp=200.0)
        assert await basis() == 200.0
        # a new row: the CP in effect; unknown → None (rescaled from state.cp, as before SP-473)
        a = await PS.add(db, {"kind": "easy", "day": "2026-10-03", "target": "功率 124–153 W"}, TODAY, cp=191.0)
        b = await PS.add(db, {"kind": "easy", "day": "2026-10-03", "target": "功率 124–153 W"}, TODAY)
        got = {s["uid"]: s["cp_basis"] for s in await PS.load(db)}
        assert got[a["uid"]] == 191.0 and got[b["uid"]] is None
        # regenerated auto rows carry the inputs' CP
        auto = [s for s in await PS.load(db) if s["origin"] == "auto" and not s["edited"]
                and s["state"] == "active" and s["day"] >= TODAY]
        assert auto and {s["cp_basis"] for s in auto} == {191.0}
    run(go())


def test_the_page_edit_and_add_record_the_cp_in_effect(monkeypatch):
    """api/plan_sessions: POST /sessions and PATCH /sessions/{uid} pass the CP in effect."""
    from backend.engine import plan_store as PS
    from backend.sync import coros_workouts as CW
    from backend.tests.test_coros_workouts import run
    from backend.tests.test_plan_store import API, Env
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    with Env(monkeypatch) as e:
        cp0 = e.inp["thresholds"]["cp"]
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        easy = next(s for s in ss if s["kind"] == "easy" and s["state"] == "active" and s["day"] >= TODAY)
        e.inp = {**e.inp, "thresholds": {**e.inp["thresholds"], "cp": cp0 + 10}}
        r = e.c.post(f"{API}/sessions", json={"kind": "easy", "day": "2026-10-03", "title": "輕鬆跑",
                                              "target": "功率 200–240 W"})
        assert r.status_code == 200, r.text
        new_uid = r.json()["uid"]
        assert e.c.patch(f"{API}/sessions/{easy['uid']}", json={"day": "2026-10-03"}).status_code == 200
        got = {s["uid"]: s for s in run(PS.load(e.db))}
        assert got[new_uid]["cp_basis"] == cp0 + 10
        assert got[easy["uid"]]["cp_basis"] == cp0                     # a drag keeps it
        r = e.c.patch(f"{API}/sessions/{easy['uid']}", json={"target": "功率 201–241 W"})
        assert r.status_code == 200, r.text
        assert next(s for s in run(PS.load(e.db)) if s["uid"] == easy["uid"])["cp_basis"] == cp0 + 10
