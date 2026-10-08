"""
SP-362 B3: the COROS push runs outside the plan writer lock (api/plan_sessions._wlock).

Before, an automatic run (engine/plan_auto.run) held the writer lock for the whole run including
the push (COROS 23–27 s on the NAS), and an edit's watch sync (SP-358 _sync_watch) pushed under
it too: the 課表 page's GET waited for COROS. Now the writer lock covers reconcile + save only;
the push follows under the push lock (plan_sessions._plock) and re-reads the stored plan first.
Owner rule: an edit made while a push runs wins — the push never overwrites it, and the edit's
own watch sync brings the watch in line (no lost change, no second push of the same version).

COROS is the scripted FakeHub (a gate holds its first program upload) or a stub; one event loop
and a file database, so the page request and the run really share the locks. No WKO5 data.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api import plan_sessions as API
from backend.db.models import Athlete, Base, CorosPlanPush, PlanChangeLog, SyncState
from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.settings.secrets import seal
from backend.sync import coros_workouts as CW, http
from backend.sync import workout_targets as WT
from backend.tests.test_coros_workouts import BASE, FakeHub, run
from backend.tests.test_plan_auto import Box, Push, base_inputs

CAL = ("2026-09-28", "2026-10-04")


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    # the calendar's dataset part (activities, phases) is hand-built
    monkeypatch.setattr(API, "_range_extras", lambda a, b: {"activities": [], "phases": []})


async def _dbs(tmp_path):
    """Two sessions on one file database (a run's and a page request's), with the app's
    connection setup (WAL: a reader never blocks a writer, as on the NAS)."""
    from sqlalchemy import event
    from backend.db.database import _on_connect
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plan.db'}")
    event.listen(engine.sync_engine, "connect", _on_connect)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    mk = async_sessionmaker(engine, expire_on_commit=False)
    async with mk() as s:
        s.add(Athlete(id=1, name="tester", data_dir="x"))
        s.add(SyncState(athlete_id=1, coros_access_token=seal("tok"), coros_base_url=BASE, coros_user_id="u1",
                        coros_token_expires=datetime.now(timezone.utc) + timedelta(hours=5)))
        await s.commit()
    return mk(), mk(), engine


async def _stored(engine):
    """The stored plan through a session of its own (the run's and the edit's are busy)."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        return await PS.load(s)


class SlowPush(Push):
    """The push stub, held at its first call until `gate` opens."""

    def __init__(self):
        super().__init__()
        self.started, self.gate = asyncio.Event(), asyncio.Event()

    async def push(self, db, sessions, *a, **kw):
        if not self.gate.is_set():
            self.started.set()
            await self.gate.wait()
        return await super().push(db, sessions, *a, **kw)


class GatedHub:
    """FakeHub behind a gate on the first program upload (COROS taking its time)."""

    def __init__(self):
        self.fake = FakeHub()
        self.started, self.gate = asyncio.Event(), asyncio.Event()

    async def __call__(self, req):
        if req.url.path == "/training/program/add" and not self.gate.is_set():
            self.started.set()
            await self.gate.wait()
        return self.fake(req)


def _easy1(ss):
    return next(s for s in ss if s.get("gen_key") == "easy1" and s["state"] == "active")


def test_calendar_does_not_wait_for_the_auto_runs_push(monkeypatch, tmp_path):
    push = SlowPush()
    Box(monkeypatch, base_inputs(), push)

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
        await asyncio.wait_for(push.started.wait(), 10)          # the run is at COROS now
        # the page's calendar answers while the push is still held (it used to wait on _wlock)
        body = await asyncio.wait_for(API.calendar(*CAL, db=db2), 5)
        assert not task.done()
        assert _easy1(body["sessions"])["minutes"] == 45                     # the run's saved plan
        push.gate.set()
        r = await asyncio.wait_for(task, 10)
        assert r["status"] == "applied" and r["push"]["status"] == "ok"
        log = (await db2.execute(select(PlanChangeLog))).scalars().all()
        assert len(log) == 1 and PA.entry_dict(log[0])["push"]["status"] == "ok"      # filled in after the push
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())


def test_an_edit_during_the_push_wins_and_the_watch_follows_it(monkeypatch, tmp_path):
    Box(monkeypatch, base_inputs())
    hub = GatedHub()

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        with http.use_transport(httpx.MockTransport(hub)):
            task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
            await asyncio.wait_for(hub.started.wait(), 10)       # the run's push is uploading
            uid = _easy1(await PS.load(db2))["uid"]
            edit = asyncio.create_task(API.edit_session(uid, {"minutes": 77}, db=db2))
            # the edit is saved while the push still runs (the writer lock is free) ...
            for _ in range(200):
                if _easy1(await _stored(engine))["minutes"] == 77:
                    break
                await asyncio.sleep(0.02)
            assert _easy1(await _stored(engine))["minutes"] == 77 and not task.done()
            assert not edit.done()                          # ... and its watch sync waits for the push
            hub.gate.set()
            r = await asyncio.wait_for(task, 10)
            out = await asyncio.wait_for(edit, 10)
        assert r["status"] == "applied"
        assert out["minutes"] == 77 and out["edited"] is True and out["coros"]["status"] == "ok"
        s = _easy1(await PS.load(db2))
        assert s["minutes"] == 77                                     # the push never overwrote the edit
        row = (await db2.execute(select(CorosPlanPush).where(CorosPlanPush.session_key == uid))).scalar_one()
        prov = await WT.active(db2)
        st = prov.status_of(PS.push_dict(s), base_inputs()["thresholds"], row, "2026-09-30")
        assert st["status"] == "pushed"                               # the watch has the edited version
        assert sum(1 for e in hub.fake.entities if e["happenDay"] == 20261002) == 1      # one copy that day
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())


def test_an_edit_saved_before_the_push_goes_out_once(monkeypatch, tmp_path):
    # the edit lands after the run saved its plan but before its push read the store: whichever
    # push goes first sends the edited version, the other finds it up to date (no second upload)
    Box(monkeypatch, base_inputs())
    fake = FakeHub()

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        with http.use_transport(httpx.MockTransport(fake)):
            async with API._plock():                        # COROS busy with an earlier push
                task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
                for _ in range(200):                        # the run saved its plan and released _wlock
                    async with async_sessionmaker(engine)() as s:
                        stamped = (await PA.settings(s))["state"].get("stamp")
                    if stamped and not API._wlock().locked():
                        break
                    await asyncio.sleep(0.02)
                uid = _easy1(await PS.load(db2))["uid"]
                edit = asyncio.create_task(API.edit_session(uid, {"minutes": 77}, db=db2))
                for _ in range(200):
                    if _easy1(await _stored(engine))["minutes"] == 77:
                        break
                    await asyncio.sleep(0.02)
                assert not task.done() and not edit.done()
            await asyncio.wait_for(task, 10)
            out = await asyncio.wait_for(edit, 10)
        assert out["coros"]["status"] in ("ok", "unchanged")
        assert len(fake.programs) == len(fake.live())       # no program uploaded twice (none replaced)
        assert sum(1 for e in fake.entities if e["happenDay"] == 20261002) == 1
        row = (await db2.execute(select(CorosPlanPush).where(CorosPlanPush.session_key == uid))).scalar_one()
        prov = await WT.active(db2)
        s = _easy1(await PS.load(db2))
        assert prov.status_of(PS.push_dict(s), base_inputs()["thresholds"], row, "2026-09-30")["status"] == "pushed"
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())


def test_a_push_failure_outside_the_lock_is_still_logged(monkeypatch, tmp_path):
    Box(monkeypatch, base_inputs(), Push(fail=True))

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        r = await PA.run(db1, trigger="sync:coros")
        assert r["status"] == "applied" and r["push"]["status"] == "failed"
        e = PA.entry_dict((await db2.execute(select(PlanChangeLog))).scalars().one())
        assert e["status"] == "applied" and e["push"]["status"] == "failed" and "COROS down" in e["push"]["error"]
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())


def test_the_edits_watch_sync_does_not_hold_the_writer_lock(monkeypatch, tmp_path):
    # SP-358's sync after an edit pushes under the push lock only: a calendar load meanwhile answers
    push = SlowPush()
    Box(monkeypatch, base_inputs(), push)

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        push.gate.set()
        await PA.run(db1, trigger="sync:coros")                  # the plan is stored and pushed
        push.gate.clear()
        uid = _easy1(await PS.load(db1))["uid"]
        edit = asyncio.create_task(API.edit_session(uid, {"minutes": 50}, db=db1))
        await asyncio.wait_for(push.started.wait(), 10)
        body = await asyncio.wait_for(API.calendar(*CAL, db=db2), 5)
        assert _easy1(body["sessions"])["minutes"] == 50 and not edit.done()
        push.gate.set()
        assert (await asyncio.wait_for(edit, 10))["coros"]["status"] == "ok"
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())
