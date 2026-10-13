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

Status: reproduced, NOT fixed yet — strict xfail until the fix is chosen (the stored sessions do
not record the CP their watts were written with; see the SP-473 hand-off for the options). Drop
the xfail mark with the fix: strict, so a fix that makes it pass fails the suite until then.

Synthetic data only — never the real app DB or ~/.wko5coach.
"""
import json
from datetime import date

import pytest

from backend.tests.test_cp_hold_sp468 import _inputs

TODAY = "2026-09-30"


def _get(ss, uid):
    return next(s for s in ss if s["uid"] == uid)


@pytest.mark.xfail(strict=True, reason="SP-473 open: rescale_sessions takes state.cp as every row's old CP")
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
                                "target": "功率 124–153 W"}, TODAY)
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
