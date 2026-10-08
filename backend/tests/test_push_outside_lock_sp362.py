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
import json
import sqlite3
import threading
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import tenancy
from backend.api import plan_sessions as API
from backend.db.models import Athlete, Base, CorosPlanPush, PlanChangeLog, SyncState
from backend.engine import blackouts as BL
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


# ---------------------------------------------------------------------------
# review follow-ups
# ---------------------------------------------------------------------------

def _queued(lock) -> int:
    return len(getattr(lock, "_waiters", None) or ())


async def _until(cond, s=10.0):
    for _ in range(int(s / 0.02)):
        got = cond()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return True
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


class KeyedInputs:
    """The real _compute_inputs over a scripted key (H1): the key holds the dataset generation
    and the stored 不排課日期 (read from the file DB, as BL.load reads the setting); the
    computation (`inputs_key` included, as _build_inputs does) drops the sessions of this week
    on a blocked day, like week_plan. `gates[gen]` holds that generation's computation."""

    def __init__(self, monkeypatch, dbfile):
        self.dbfile, self.gen, self.minutes, self.gates = dbfile, 1, {}, {}
        self.started: set = set()
        for name in ("_cache", "_refreshing", "_failed"):
            monkeypatch.setattr(API, name, {})
        monkeypatch.setattr(API, "_last", OrderedDict())
        monkeypatch.setattr(API, "_inputs_key", self)

    def stored_bl(self):
        con = sqlite3.connect(self.dbfile)
        try:
            row = con.execute("select value_json from user_settings where key = 'plan.blackouts'").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else []

    def __call__(self, blackouts=None):
        bl = self.stored_bl() if blackouts is None else blackouts
        gen = self.gen
        key = (tenancy.current().id, gen, date(2026, 9, 30), (), json.dumps(bl, sort_keys=True))

        def build():
            self.started.add(gen)
            if gen in self.gates:
                assert self.gates[gen].wait(10)
            inp = base_inputs(new=gen - 1, easy_minutes=self.minutes.get(gen, 45))
            days = set(BL.blocked(BL.from_list(bl)))
            inp["cur"]["sessions"] = [s for s in inp["cur"]["sessions"] if s["day"] not in days or s.get("done")]
            inp["blackouts"], inp["inputs_key"] = bl, key
            API._cache[key] = inp
            return inp
        return key, build


def test_a_blackout_saved_while_a_run_waits_is_not_reverted(monkeypatch, tmp_path):
    # H1: the run computed its inputs before the 不排課日期 was saved; inside the lock it sees the
    # inputs' key changed, lets go, recomputes and applies the plan with the blocked day
    push = Push()
    monkeypatch.setattr(CW, "push_sessions", push.push)
    monkeypatch.setattr(CW, "remove_keys", push.remove)

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        kb = KeyedInputs(monkeypatch, str(tmp_path / "plan.db"))
        assert (await PA.run(db1))["status"] == "applied"
        assert any(s["day"] == "2026-10-02" for s in push.calls[-1]["sessions"])
        n0 = len(push.calls)
        kb.gen = 2                                                   # a sync brought an activity
        async with API._wlock():
            task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
            await _until(lambda: _queued(API._wlock()) == 1)        # inputs computed, waiting
            put = asyncio.create_task(API.put_blackouts(
                {"blackouts": [{"start": "2026-10-02", "end": "2026-10-02", "label": "出遊"}]}, db=db2))
            await _until(lambda: kb.stored_bl() and _queued(API._wlock()) == 2)
        r = await asyncio.wait_for(task, 10)
        await asyncio.wait_for(put, 10)
        assert r["status"] in ("applied", "noop")
        ss = await _stored(engine)
        assert not [s for s in ss if s.get("day") == "2026-10-02" and s["state"] == "active" and s["origin"] == "auto"]
        assert not [s for c in push.calls[n0:] for s in c["sessions"] if s.get("day") == "2026-10-02"]
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())


def test_an_older_generation_is_never_applied_after_a_newer_one(monkeypatch, tmp_path):
    # H1: run A computes generation 2 slowly; run B starts after generation 3 arrived and applies
    # it; A then finds its inputs out of date inside the lock and applies 3 (a no-op), not 2
    push = Push()
    monkeypatch.setattr(CW, "push_sessions", push.push)
    monkeypatch.setattr(CW, "remove_keys", push.remove)

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        kb = KeyedInputs(monkeypatch, str(tmp_path / "plan.db"))
        await PA.run(db1)
        kb.gen, kb.gates[2], kb.minutes[3] = 2, threading.Event(), 30
        a = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
        await _until(lambda: 2 in kb.started)
        kb.gen = 3
        rb = await asyncio.wait_for(PA.run(db2, trigger="sync:tp"), 10)
        assert rb["status"] == "applied" and _easy1(await _stored(engine))["minutes"] == 30
        kb.gates[2].set()
        ra = await asyncio.wait_for(a, 10)
        assert ra["status"] == "noop"
        assert _easy1(await _stored(engine))["minutes"] == 30           # the newer plan stays
        async with async_sessionmaker(engine)() as s:
            st = (await PA.settings(s))["state"]
        assert st["stamp"] == PA.stamp(base_inputs(new=2, easy_minutes=30))
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())


def test_a_cancelled_push_marks_the_row_and_the_next_run_removes_the_notice(monkeypatch, tmp_path):
    # M2: cancelled while waiting for the push lock (shutdown, a cancelled task): the row says
    # interrupted, the 課表待確認 copy still to come off the watch is kept and removed next time
    b = Box(monkeypatch, base_inputs())
    fake = FakeHub()

    async def go():
        db1, db2, engine = await _dbs(tmp_path)
        with http.use_transport(httpx.MockTransport(fake)):
            await PA.run(db1)
            b.inp = base_inputs(new=1, easy_minutes=200)
            assert (await PA.run(db1))["status"] == "pending"
            notice = (await PA.pending(db2)).notice_uid
            on_watch = lambda: CW.rows_by_key(db2, 1, [notice])     # noqa: E731
            assert await on_watch()
            b.inp = base_inputs(new=2, easy_minutes=40)              # a reduction: applies, proposal superseded
            async with API._plock():
                task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
                await _until(lambda: _queued(API._plock()) == 1)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            async with async_sessionmaker(engine)() as s:
                rows = (await s.execute(select(PlanChangeLog).order_by(PlanChangeLog.id))).scalars().all()
                st = (await PA.settings(s))["state"]
            last = PA.entry_dict(rows[-1])
            assert last["status"] == "applied" and last["push"]["status"] == "interrupted"
            assert notice in st.get("remote", []) and await on_watch()
            r = await PA.run(db1)                                     # same data: no change, but the removal
            assert r["status"] == "noop"
            db2.expire_all()
            assert not await on_watch()
            async with async_sessionmaker(engine)() as s:
                assert not (await PA.settings(s))["state"].get("remote")
            # a row left 「pushing」 by a process that died: shown as interrupted
            e = await PA._add_entry(db2, trigger="x", status="applied", summary="s", items=[], push={"status": "pushing"})
            assert PA.entry_dict(e)["push"]["status"] == "interrupted"
        await db1.close(); await db2.close(); await engine.dispose()
    run(go())


def test_reject_during_the_notice_push_takes_it_off_the_watch(monkeypatch, tmp_path):
    async def flow():
        hub = GatedHub()
        with http.use_transport(httpx.MockTransport(hub)):
            db1, db2, engine = await _dbs(tmp_path)
            hub.gate.set()
            box = Box(monkeypatch, base_inputs())
            await PA.run(db1)
            live0 = len(hub.fake.live())
            hub.gate.clear()
            box.inp = base_inputs(new=1, easy_minutes=200)
            task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
            await asyncio.wait_for(hub.started.wait(), 10)
            p = await PA.pending(db2)
            rej = asyncio.create_task(PA.reject(db2, p.id))

            async def rejected():
                async with async_sessionmaker(engine)() as s:
                    return (await s.get(PlanChangeLog, p.id)).status == "rejected"
            await _until(rejected)
            assert not task.done()
            hub.gate.set()
            assert (await asyncio.wait_for(task, 10))["status"] == "pending"
            await asyncio.wait_for(rej, 10)
            assert len(hub.fake.live()) == live0                       # the notice came off again
            assert not [s for s in await _stored(engine) if s["kind"] == "notice"]
            await db1.close(); await db2.close(); await engine.dispose()
    run(flow())


def test_approve_during_the_notice_push_applies_and_takes_it_off(monkeypatch, tmp_path):
    async def flow():
        hub = GatedHub()
        with http.use_transport(httpx.MockTransport(hub)):
            db1, db2, engine = await _dbs(tmp_path)
            hub.gate.set()
            box = Box(monkeypatch, base_inputs())
            await PA.run(db1)
            hub.gate.clear()
            box.inp = base_inputs(new=1, easy_minutes=200)
            task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
            await asyncio.wait_for(hub.started.wait(), 10)
            p = await PA.pending(db2)
            ap = asyncio.create_task(PA.approve(db2, p.id))
            await _until(lambda: _queued(API._plock()) >= 1 or ap.done())
            hub.gate.set()
            assert (await asyncio.wait_for(task, 10))["status"] == "pending"
            assert (await asyncio.wait_for(ap, 10))["status"] == "applied"
            ss = await _stored(engine)
            assert _easy1(ss)["minutes"] == 200 and not [s for s in ss if s["kind"] == "notice"]
            assert not await CW.rows_by_key(db2, 1, [p.notice_uid])          # off the watch
            assert sum(1 for e in hub.fake.entities if e["happenDay"] == 20261001) == 1   # only the quality
            await db1.close(); await db2.close(); await engine.dispose()
    run(flow())


def test_undo_during_a_runs_push_wins(monkeypatch, tmp_path):
    async def flow():
        hub = GatedHub()
        with http.use_transport(httpx.MockTransport(hub)):
            db1, db2, engine = await _dbs(tmp_path)
            hub.gate.set()
            box = Box(monkeypatch, base_inputs())
            await PA.run(db1)
            hub.gate.clear()
            box.inp = base_inputs(new=1, easy_minutes=30)
            task = asyncio.create_task(PA.run(db1, trigger="sync:coros"))
            await asyncio.wait_for(hub.started.wait(), 10)
            async with async_sessionmaker(engine)() as s:
                rid = (await s.execute(select(PlanChangeLog.id).order_by(PlanChangeLog.id.desc()))).scalars().first()
            und = asyncio.create_task(PA.undo(db2, rid))
            for _ in range(200):
                if _easy1(await _stored(engine))["minutes"] == 45:
                    break
                await asyncio.sleep(0.02)
            assert not task.done()
            hub.gate.set()
            await asyncio.wait_for(task, 10)
            assert (await asyncio.wait_for(und, 10))["status"] == "undone"
            s = _easy1(await _stored(engine))
            assert s["minutes"] == 45 and s["edited"] is True
            row = (await db2.execute(select(CorosPlanPush).where(CorosPlanPush.session_key == s["uid"]))).scalar_one()
            prov = await WT.active(db2)
            assert prov.status_of(PS.push_dict(s), base_inputs()["thresholds"], row, "2026-09-30")["status"] == "pushed"
            assert sum(1 for e in hub.fake.entities if e["happenDay"] == 20261002) == 1
            await db1.close(); await db2.close(); await engine.dispose()
    run(flow())
