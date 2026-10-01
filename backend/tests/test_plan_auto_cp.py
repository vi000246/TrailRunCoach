"""engine/plan_auto.py — a CP change re-zones and re-pushes the upcoming power
targets (COROS running workouts take absolute watts only): the stored
sessions' watt numbers follow % CP, the change log says 「CP 300 → 330 W：未來
N 堂課的功率目標已更新並重新推送」, and with plan.auto.push off nothing is sent
(「待推送」). COROS is never called: push_sessions is replaced, or the
scripted FakeHub answers over a mock transport. The DB is in memory."""
import copy
from datetime import date

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import PlanChangeLog
from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.settings.repository import SettingsRepository
from backend.sync import http
from backend.tests.test_coros_workouts import FakeHub, make_db, run
from backend.tests.test_plan_auto import Box, Push, base_inputs


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


class ThPush(Push):
    """Push that also records the thresholds each call used."""

    async def push(self, db, sessions, thresholds, today, **kw):
        out = await super().push(db, sessions, thresholds, today, **kw)
        self.calls[-1]["cp"] = (thresholds or {}).get("cp")
        return out


def with_cp(inp, cp):
    inp = copy.deepcopy(inp)
    inp["thresholds"]["cp"] = cp
    inp["cur"]["thresholds"]["cp"] = cp
    return inp


async def _log(db):
    return (await db.execute(select(PlanChangeLog).order_by(PlanChangeLog.id))).scalars().all()


def _by_title(ss, title):
    return next(s for s in ss if s["title"] == title and s["state"] == "active" and s["week_start"] == "2026-09-28")


def test_rescale_watts_only_touches_absolute_watts():
    assert PA.rescale_watts("功率 285–303 W · 心率 ≤ 138 bpm", 300, 330) == "功率 314–333 W · 心率 ≤ 138 bpm"
    assert PA.rescale_watts("固定功率 153 W，心率從 130", 204, 210) == "固定功率 158 W，心率從 130"
    assert PA.rescale_watts("< 163 W", 204, 210) == "< 168 W"
    for s in ("W′ 13100 J", "4.2 W/kg", "98–102% CP", "暖身 15 分"):
        assert PA.rescale_watts(s, 204, 210) == s
    assert PA.cp_summary(204, 210, 3, True) == "CP 204 → 210 W：未來 3 堂課的功率目標已更新並重新推送"
    assert PA.cp_summary(204, 210, 3, False) == "CP 204 → 210 W：未來 3 堂課的功率目標已更新，待推送"


def test_cp_change_rescales_logs_and_repushes(monkeypatch):
    b = Box(monkeypatch, base_inputs(), ThPush())

    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        assert (await PA.settings(db))["state"]["cp"] == 300.0
        q = _by_title(await PS.load(db), "閾值 3×10 分")
        # the user's own target text on the quality day (kept by reconcile: edited)
        await PS.edit(db, q["uid"], {"target": "功率 285–303 W"}, "2026-09-30")
        n0 = len(b.push.calls)
        # same activities, no new sync: nothing to do
        assert (await PA.run(db))["status"] == "noop" and len(b.push.calls) == n0
        # a CP test applied: 300 -> 330
        b.inp = with_cp(base_inputs(), 330.0)
        r = await PA.run(db, trigger="cp_change")
        c = r["cp_change"]
        assert (c["old"], c["new"]) == (300.0, 330.0) and c["n"] >= 1 and c["pushed"]
        q2 = _by_title(await PS.load(db), "閾值 3×10 分")
        assert q2["target"] == "功率 314–333 W"
        # re-pushed with the new CP, the quality session among them
        assert len(b.push.calls) == n0 + 1 and b.push.calls[-1]["cp"] == 330.0
        assert q["uid"] in {s["id"] for s in b.push.calls[-1]["sessions"]}
        e = PA.entry_dict(next(x for x in await _log(db) if x.trigger == "cp_change"))
        assert sum(1 for x in await _log(db) if x.trigger == "cp_change") == 1      # one row, not two
        assert e["summary"] == f"CP 300 → 330 W：未來 {c['n']} 堂課的功率目標已更新並重新推送"
        assert any(i["uid"] == q["uid"] and i["rule"] == "cp" and i["before"]["target"] == "功率 285–303 W"
                   for i in e["items"])
        assert (await PA.settings(db))["state"]["cp"] == 330.0
        # the same CP again: no second event
        assert "cp_change" not in await PA.run(db)
    run(go())


def test_cp_change_with_push_off_marks_pending_and_sends_nothing(monkeypatch):
    # plan.auto.push = false (the user's real setting): update the plan and the log, push nothing
    Box(monkeypatch, base_inputs())
    fake = FakeHub()

    async def go():
        db = await make_db()
        with http.use_transport(httpx.MockTransport(fake)):
            assert (await PA.run(db))["status"] == "applied"           # on the watch at CP 300
            await SettingsRepository(db).set("plan.auto.push", False)
            await db.commit()
            n = len(fake.calls)
            box_inp = with_cp(base_inputs(), 330.0)
            monkeypatch.setattr(PA, "cp_of", lambda inp: 330.0)
            from backend.api import plan_sessions as API
            monkeypatch.setattr(API, "_compute_inputs", lambda *a: box_inp)
            r = await PA.run(db, trigger="cp_change")
            assert len(fake.calls) == n                                 # COROS not touched
            c = r["cp_change"]
            assert not c["pushed"] and c["pending"] >= 1 and c["status"] == "待推送"
            e = PA.entry_dict(next(x for x in await _log(db) if x.trigger == "cp_change"))
            assert e["summary"].endswith("，待推送") and e["push"]["status"] == "off"
            assert any(i["push"] == "待推送" for i in e["items"])
    run(go())


def test_auto_off_does_nothing(monkeypatch):
    b = Box(monkeypatch, base_inputs(), ThPush())

    async def go():
        db = await make_db()
        await PA.run(db)
        await SettingsRepository(db).set("plan.auto.enabled", False)
        await db.commit()
        b.inp = with_cp(base_inputs(), 330.0)
        n = len(b.push.calls)
        assert (await PA.run(db))["status"] == "disabled" and len(b.push.calls) == n
    run(go())


def test_threshold_edit_starts_the_hook(monkeypatch):
    # api/plan._notify(True) -> plan_auto.after_thresholds (conftest stubs it; here it is recorded)
    from backend.api import plan as API
    seen = []
    monkeypatch.setattr("backend.api.wko5views.plan_changed", lambda t: None)
    monkeypatch.setattr(PA, "after_thresholds", lambda: seen.append(1))
    API._notify(False)
    assert not seen
    API._notify(True)
    assert seen == [1]
