"""
engine/plan_auto.py: the automatic run after a sync — idempotence, the
big-change hold (watch notice, approve / reject), safe reductions applied on
their own, 復原, a push failure not breaking anything, the lock, and the
notice kind kept out of load / compliance. COROS is never called: either
push_sessions is replaced, or the scripted FakeHub answers over a mock
transport. The DB is in memory.
"""
import asyncio
import copy
from datetime import date

import httpx
import pytest
from sqlalchemy import select

from backend.api import plan_sessions as API
from backend.db.models import CorosPlanPush, PlanChangeLog
from backend.engine import compliance as C
from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.engine import reconcile as R
from backend.sync import coros_workouts as CW, http
from backend.tests.test_coros_workouts import FakeHub, make_db, run
from backend.tests.test_plan_store import ACT_929, cur_plan, g, inputs, next_week


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


class Push:
    """Stand-in for CW.push_sessions / remove_keys: records, never networks."""

    def __init__(self, fail=False):
        self.calls, self.removed, self.fail = [], [], fail

    async def push(self, db, sessions, thresholds, today, *, stale_keys=(), missed_keys=(), **kw):
        if self.fail:
            raise RuntimeError("COROS down")
        self.calls.append({"sessions": [dict(s) for s in sessions], "stale": list(stale_keys),
                           "missed": list(missed_keys), "today": today})
        return {"sessions": [{"id": s["id"], "status": "pushed", "changed": True} for s in sessions], "removed": []}

    async def remove(self, db, keys, **kw):
        self.removed.append(list(keys))
        return []


def base_inputs(new=0, easy_minutes=45, quality=True, days_to_race=None):
    ss = [g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
          g("easy1", "easy", "輕鬆跑", easy_minutes, "2026-10-02"),
          g("long", "long", "LSD（山路）", 120, "2026-10-04")]
    if quality:
        ss.insert(1, g("quality", "quality", "閾值 3×10 分", 60, "2026-10-01", detail="休 2–3 分鐘；暖身 15 分、緩和 10 分"))
    inp = inputs(cur=cur_plan(sessions=ss), weeks=[next_week()], horizon="2026-10-11")
    inp["days_to_next_a"] = days_to_race
    return later(inp, new) if new else inp


class Box:
    def __init__(self, monkeypatch, inp, push: Push = None):
        self.inp = inp
        monkeypatch.setattr(API, "_compute_inputs", lambda *a: self.inp)
        self.push = push
        if push is not None:
            monkeypatch.setattr(CW, "push_sessions", push.push)
            monkeypatch.setattr(CW, "remove_keys", push.remove)


async def _log(db):
    return (await db.execute(select(PlanChangeLog).order_by(PlanChangeLog.id))).scalars().all()


def _active(ss):
    """This week's active sessions by gen_key."""
    return {s.get("gen_key") or s["uid"]: s for s in ss if s["state"] == "active" and s["week_start"] == "2026-09-28"}


NEW_ACT = {"index": 12, "date": "2026-09-30", "category": "strength", "moving_s": 1800, "tss": 10}


def later(inp, n=1):
    """The same inputs after a sync that brought `n` more activities."""
    inp["activities"] = inp["activities"] + [{**NEW_ACT, "index": 12 + k} for k in range(n)]
    return inp


def test_first_run_applies_and_pushes_only_the_window(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        r = await PA.run(db, trigger="sync:coros")
        assert r["status"] == "applied"
        days = sorted(s["day"] for s in b.push.calls[0]["sessions"])
        assert days and days[0] >= "2026-09-30" and days[-1] <= "2026-10-06"      # 7 days
        ss = await PS.load(db)
        assert any(s["day"] == "2026-10-11" for s in ss)                          # later: app only
        log = await _log(db)
        assert len(log) == 1 and log[0].status == "applied"
        # second run, same data: nothing at all
        r2 = await PA.run(db, trigger="sync:coros")
        assert r2["status"] == "noop" and len(b.push.calls) == 1 and len(await _log(db)) == 1
    run(go())


def test_idempotent_with_the_fake_hub_no_second_push(monkeypatch):
    Box(monkeypatch, base_inputs())
    fake = FakeHub()

    async def go():
        db = await make_db()
        with http.use_transport(httpx.MockTransport(fake)):
            assert (await PA.run(db))["status"] == "applied"
            n = len(fake.calls)
            assert len(fake.live()) >= 2
            # force past the stamp: same plan, everything already pushed -> no COROS call, no log
            st = (await PA.settings(db))["state"]
            st.pop("stamp")
            await PA._set_state(db, st)
            r = await PA.run(db)
            assert r["status"] == "noop" and r["push"]["status"] == "unchanged"
            assert len(fake.calls) == n
    run(go())


def test_big_increase_is_held_with_a_watch_notice_then_approved(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        n0 = len(b.push.calls)
        b.inp = base_inputs(new=1,easy_minutes=200)                      # +155 min easy: week TSS > +20 %
        r = await PA.run(db)
        assert r["status"] == "pending"
        ss = await PS.load(db)
        assert _active(ss)["easy1"]["minutes"] == 45                # the plan waits
        notice = [s for s in ss if s["kind"] == "notice"]
        assert len(notice) == 1 and notice[0]["title"].startswith("⚠")
        assert notice[0]["day"] == "2026-10-01"                     # something done today -> next session day
        pushed = b.push.calls[n0:]
        assert len(pushed) == 1 and [s["kind"] for s in pushed[0]["sessions"]] == ["notice"]
        p = await PA.pending(db)
        assert any(x["rule"] == "tss" for x in PA.entry_dict(p)["big"])
        # the same proposal again: no new entry, no new push
        st = (await PA.settings(db))["state"]
        st.pop("stamp")
        await PA._set_state(db, st)
        assert (await PA.run(db))["status"] == "pending"
        assert len(b.push.calls) == n0 + 1
        # approve: applied, notice gone from the store and the watch
        r = await PA.approve(db, p.id)
        assert r["status"] == "applied"
        ss = await PS.load(db)
        assert _active(ss)["easy1"]["minutes"] == 200 and not [s for s in ss if s["kind"] == "notice"]
        assert b.push.removed == [] or notice[0]["uid"] in sum(b.push.removed, [])
        assert (await db.get(PlanChangeLog, p.id)).status == "approved"
    run(go())


def test_reject_sticks(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = base_inputs(new=1,easy_minutes=200)
        await PA.run(db)
        p = await PA.pending(db)
        await PA.reject(db, p.id)
        st = (await PA.settings(db))["state"]
        st.pop("stamp")
        await PA._set_state(db, st)
        r = await PA.run(db)
        assert r["status"] == "rejected_before"
        assert _active(await PS.load(db))["easy1"]["minutes"] == 45
        assert not [s for s in await PS.load(db) if s["kind"] == "notice"]
    run(go())


def test_overview_only_notify_pushes_no_notice(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        from backend.settings.repository import SettingsRepository
        await SettingsRepository(db).set("plan.auto.notify", "overview")
        await db.commit()
        await PA.run(db)
        n0 = len(b.push.calls)
        b.inp = base_inputs(new=1,easy_minutes=200)
        assert (await PA.run(db))["status"] == "pending"
        assert len(b.push.calls) == n0 and not [s for s in await PS.load(db) if s["kind"] == "notice"]
    run(go())


def test_safe_reduction_applies_without_waiting(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = base_inputs(new=1,easy_minutes=20, quality=False)          # quality gone, easy shorter
        r = await PA.run(db)
        assert r["status"] == "applied"
        ss = _active(await PS.load(db))
        assert "quality" not in ss and ss["easy1"]["minutes"] == 20
    run(go())


def test_race_guard_holds_a_removed_quality(monkeypatch):
    b = Box(monkeypatch, base_inputs(days_to_race=10), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = base_inputs(new=1,quality=False, days_to_race=10)
        r = await PA.run(db)
        assert r["status"] == "pending"
        assert [x["rule"] for x in PA.entry_dict(await PA.pending(db))["big"]] == ["race"]
    run(go())


def test_phase_change_stays_held_on_the_next_sync(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = base_inputs(new=1, easy_minutes=50)
        b.inp["phase"] = {**b.inp["phase"], "kind": "specific"}
        r = await PA.run(db)
        assert r["status"] == "pending"
        b.inp = later(b.inp)                                   # another sync, same plan
        r2 = await PA.run(db)
        assert r2["status"] == "pending" and r2["id"] == r["id"]
        assert _active(await PS.load(db))["easy1"]["minutes"] == 45
        await PA.approve(db, r["id"])
        assert (await PA.settings(db))["state"]["phase"] == "specific"
    run(go())


def test_undo_restores_pins_and_repushes(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = base_inputs(new=1,easy_minutes=30)
        r = await PA.run(db)
        assert r["status"] == "applied" and _active(await PS.load(db))["easy1"]["minutes"] == 30
        n = len(b.push.calls)
        u = await PA.undo(db, r["id"])
        assert u["status"] == "undone"
        e1 = _active(await PS.load(db))["easy1"]
        assert e1["minutes"] == 45 and e1["edited"] is True
        assert len(b.push.calls) == n + 1
        assert (await db.get(PlanChangeLog, r["id"])).status == "undone"
        # the next run doesn't redo it
        st = (await PA.settings(db))["state"]
        st.pop("stamp")
        await PA._set_state(db, st)
        await PA.run(db)
        assert _active(await PS.load(db))["easy1"]["minutes"] == 45
        with pytest.raises(ValueError):
            await PA.undo(db, r["id"])
    run(go())


def test_push_failure_is_logged_not_raised(monkeypatch):
    Box(monkeypatch, base_inputs(), Push(fail=True))

    async def go():
        db = await make_db()
        r = await PA.run(db)
        assert r["status"] == "applied" and r["push"]["status"] == "failed"
        e = PA.entry_dict((await _log(db))[0])
        assert "COROS down" in e["push"]["error"]
        assert _active(await PS.load(db))                       # the plan itself was saved
    run(go())


def test_run_safe_never_raises_and_logs(monkeypatch):
    def boom(*a):
        raise RuntimeError("dataset broken")
    monkeypatch.setattr(API, "_compute_inputs", boom)

    async def go():
        db = await make_db()

        class F:
            def __call__(self):
                return self

            async def __aenter__(self):
                return db

            async def __aexit__(self, *a):
                return False
        monkeypatch.setattr(PA, "SESSION_FACTORY", F())
        r = await PA.run_safe("sync:coros")
        assert r["status"] == "failed"
        assert (await _log(db))[-1].status == "failed"
    run(go())


def test_sync_stream_triggers_after_sync_only_with_new_activities(monkeypatch):
    from backend.sync import runner
    seen = []
    monkeypatch.setattr(PA, "after_sync", lambda src, res: seen.append((src, dict(res))))

    def fake_stream(n):
        async def gen(db, source, athlete_id, since):
            yield {"status": "complete", "total_downloaded": n, "total_checked": 3, "errors": []}
        return gen

    async def go():
        db = await make_db()
        monkeypatch.setattr(runner, "_client_stream", fake_stream(1))
        evs = [e async for e in runner.stream(db, "coros")]
        assert evs[-1]["status"] == "complete" and seen[-1][1]["downloaded"] == 1
        monkeypatch.setattr(runner, "_client_stream", fake_stream(0))
        [e async for e in runner.stream(db, "coros")]
        assert seen[-1][1]["downloaded"] == 0
    run(go())


def test_after_sync_gate_starts_a_background_run(monkeypatch):
    calls = []

    async def fake_run_safe(trigger):
        calls.append(trigger)
        return {"status": "noop"}
    monkeypatch.setattr(PA, "run_safe", fake_run_safe)

    async def go():
        assert PA._after_sync("coros", {"status": "ok", "downloaded": 0}) is None
        assert PA._after_sync("coros", {"status": "failed", "downloaded": 3}) is None
        t = PA._after_sync("tp", {"status": "partial", "downloaded": 2})
        assert t is not None
        await t
        assert calls == ["sync:tp"]
    run(go())


def test_concurrent_runs_apply_once(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db1 = await make_db()
        r1, r2 = await asyncio.gather(PA.run(db1), PA.run(db1))
        assert sorted([r1["status"], r2["status"]]) == ["applied", "noop"]
        assert len(b.push.calls) == 1
        assert len([x for x in await _log(db1) if x.status == "applied"]) == 1
    run(go())


def test_notice_pushed_and_removed_on_the_fake_hub_and_never_load(monkeypatch):
    b = Box(monkeypatch, base_inputs())
    fake = FakeHub()

    async def go():
        db = await make_db()
        with http.use_transport(httpx.MockTransport(fake)):
            await PA.run(db)
            live0 = len(fake.live())
            b.inp = base_inputs(new=1,easy_minutes=200)
            assert (await PA.run(db))["status"] == "pending"
            assert len(fake.live()) == live0 + 1
            rows = (await db.execute(select(CorosPlanPush))).scalars().all()
            ss = await PS.load(db)
            notice = next(s for s in ss if s["kind"] == "notice")
            assert notice["uid"] in {r.session_key for r in rows}
            # never load / compliance / done-missed
            summ = PS.plan_summary(ss, "2026-09-28", "2026-09-30", 30, 30, 42, 7)
            assert summ["tss"] == pytest.approx(PS.plan_summary([s for s in ss if s["kind"] != "notice"],
                                                                "2026-09-28", "2026-09-30", 30, 30, 42, 7)["tss"])
            assert C.session_compliance({**notice, "state": "missed"}) is None
            later, _ = R.reconcile(ss, [], [], "2026-10-05")
            assert next(s for s in later if s["uid"] == notice["uid"])["state"] == "active"
            assert API.est_tss(notice, {"notice": 0.0}) == 0.0
            p = await PA.pending(db)
            await PA.reject(db, p.id)
            assert len(fake.live()) == live0
            rows = (await db.execute(select(CorosPlanPush))).scalars().all()
            assert notice["uid"] not in {r.session_key for r in rows}
    run(go())


def test_classify_rules():
    st = [{"uid": "a", "day": "2026-10-01", "kind": "easy", "state": "active", "tss": 40.0, "minutes": 50,
           "title": "輕鬆跑"}]
    up = [{**st[0], "tss": 60.0, "minutes": 75}]
    items = PA.diff(st, up, [], [])
    big = PA.classify(st, up, items, "2026-09-30", "2026-10-06", "base", "base", None)
    assert [b["rule"] for b in big] == ["tss"]
    down = [{**st[0], "tss": 20.0, "minutes": 25}]
    assert PA.classify(st, down, PA.diff(st, down, [], []), "2026-09-30", "2026-10-06", "base", "base", None) == []
    assert [b["rule"] for b in PA.classify(st, st, [], "2026-09-30", "2026-10-06", "specific", "base", None)] == ["phase"]
    many_s = [{**st[0], "uid": str(i), "day": f"2026-10-0{i}"} for i in range(1, 6)]
    many_n = [{**s, "title": "別的", "tss": 41.0} for s in many_s]
    it = PA.diff(many_s, many_n, [], [])
    assert "many" in [b["rule"] for b in PA.classify(many_s, many_n, it, "2026-09-30", "2026-10-06", "b", "b", None)]
    assert PA.is_reduction({"action": "changed", "tss": 30, "minutes": 40, "before": {"tss": 40, "minutes": 40}})
