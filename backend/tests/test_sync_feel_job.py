"""SP-362 A5: the COROS post-run self-rating passes (retry of the recent unread rows + the
8-week backfill, SP-231) run as a background job AFTER the sync result is stored, so the
sync ends earlier. The job holds the COROS busy flag as a yielding holder: a sync or a check
of COROS asks it to stop between two reads and waits for it, never runs beside it. A job that
stores ≥ 1 rating starts the automatic plan (and the calibration) like `rpe_filled` did.
COROS is a MockTransport; the DB is in memory."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import WorkoutFile
from backend.engine import calibrate, plan_auto
from backend.settings.repository import SettingsRepository
from backend.sync import coros_client, http, runner
from backend.tests.test_coros_rpe import FakeDetail, _seed
from backend.tests.test_sync_e2e import _coros_act, collect, make_session, run

RECENT = datetime.now(timezone.utc).replace(hour=0, minute=30, second=0, microsecond=0) - timedelta(days=1)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(coros_client, "DETAIL_PAUSE_S", 0)


@pytest.fixture
def hooks(monkeypatch):
    seen = []
    monkeypatch.setattr(plan_auto, "after_sync", lambda src, res: seen.append(("plan", src, dict(res))))
    monkeypatch.setattr(calibrate, "after_sync", lambda src, res, aid: seen.append(("calib", src, dict(res))))
    return seen


async def _jobs():
    """Wait for the runner's background tasks (the self-rating job)."""
    for _ in range(50):
        pending = [t for t in list(runner._TASKS) if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def test_the_backfill_runs_after_the_sync_result_is_stored(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s)                                         # B0..B2 inside 8 weeks, unread
        fake = FakeDetail([], feels={"B0": 4, "B1": 0, "B2": 3})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = []
            async for e in runner.stream(s, "coros", 1, feel_in_background=True):
                ev.append(e)
            # the stream ended (result stored) before any backfill read
            res = await SettingsRepository(s, 1).get("sync.coros.last_result")
            assert res["status"] == "ok" and "rpe_filled" not in res
            assert ev[-1]["status"] == "complete" and "feel_read" not in ev[-1]
            assert fake.details() == []
            assert [h for h in hooks if h[2].get("rpe_filled")] == []
            await _jobs()
        assert sorted(q["labelId"] for _m, _p, q in fake.details()) == ["B0", "B1", "B2"]
        rows = {r.coros_activity_id: r for r in (await s.execute(select(WorkoutFile))).scalars()}
        assert rows["B0"].rpe == 7.0 and rows["B2"].rpe == 5.0
        assert (await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY))["done"] is True
        # ≥ 1 rating stored -> the plan and the calibration start (SP-231), like rpe_filled did
        late = [h for h in hooks if h[2].get("rpe_filled")]
        assert sorted(h[0] for h in late) == ["calib", "plan"]
        assert all(h[1] == "coros" and h[2]["rpe_filled"] == 2 and h[2]["status"] == "ok" for h in late)
        assert not runner.is_busy("coros") and "coros" not in runner._BUSY
    run(go())


def test_new_activity_rating_is_still_read_in_the_sync_and_not_twice(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeDetail([_coros_act("N1", RECENT)], feels={"N1": 4})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            await collect(runner.stream(s, "coros", 1, feel_in_background=True))
            assert [q["labelId"] for _m, _p, q in fake.details()] == ["N1"]      # in the sync
            await _jobs()
        assert [q["labelId"] for _m, _p, q in fake.details()] == ["N1"]          # the job skips it
        assert [h for h in hooks if h[2].get("rpe_filled")] == []                # nothing new by the job
    run(go())


def test_no_job_after_a_failed_sync(tmp_path, hooks, monkeypatch):
    started = []
    monkeypatch.setattr(runner, "start_feel_job", lambda *a, **k: started.append(a))

    async def fake_stream(db, source, athlete_id, since, **kw):
        yield {"status": "error", "error": "COROS_API_ERROR"}
    monkeypatch.setattr(runner, "_client_stream", fake_stream)

    async def go():
        s = await make_session(tmp_path)
        await collect(runner.stream(s, "coros", 1, feel_in_background=True))
    run(go())
    assert started == []


def test_without_background_the_passes_run_inline_as_before(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=1, n_old=0)
        fake = FakeDetail([], feels={"B0": 4})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(runner.stream(s, "coros", 1))
        assert ev[-1]["rpe_filled"] == 1 and len(fake.details()) == 1
        assert [t for t in runner._TASKS if not t.done()] == []
    run(go())


def test_fill_feel_stops_between_reads_when_asked_and_it_is_no_backfill_pass(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=3, n_old=0)
        fake = FakeDetail([], default=3)
        asked = {"n": 0}

        def stop():
            asked["n"] += 1
            return asked["n"] > 1                                # after the first read
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            n = await coros_client.feel_job(s, 1, should_stop=stop)
        assert len(fake.details()) == 1 and n == 1
        assert await SettingsRepository(s, 1).get(coros_client.BACKFILL_KEY) is None   # not a pass
    run(go())


def test_a_sync_preempts_the_job_and_waits_for_it(tmp_path, monkeypatch):
    order = []

    async def fake_stream(db, source, athlete_id, since, **kw):
        order.append("sync runs")
        yield {"status": "complete", "total_downloaded": 0, "total_checked": 0, "errors": []}
    monkeypatch.setattr(runner, "_client_stream", fake_stream)

    async def job():
        with runner.hold_yielding("coros") as stop:
            order.append("job holds")
            while not stop.is_set():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)                            # the read in flight ends
            order.append("job stops")

    async def go():
        s = await make_session(tmp_path)
        t = asyncio.create_task(job())
        await asyncio.sleep(0.02)
        assert runner._BUSY == {"coros"} and not runner.is_busy("coros")       # a sync may start
        ev = await collect(runner.stream(s, "coros", 1))
        await t
        assert ev[-1]["status"] == "complete"
    run(go())
    assert order == ["job holds", "job stops", "sync runs"]


def test_a_check_or_deletion_holding_the_flag_keeps_the_job_out(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        await _seed(s, n_recent=1, n_old=0)
        fake = FakeDetail([], feels={"B0": 4})
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            with runner.hold("coros"):
                t = runner.start_feel_job(s, 1, frozenset())
                assert await t == 0
        assert fake.details() == []                               # the next sync's job does it
    run(go())


def test_the_yielding_holder_gives_way_also_to_start_background(tmp_path):
    async def go():
        with runner.hold_yielding("coros"):
            assert runner.ready_sources is not None
            assert not runner.is_busy("coros")
            with pytest.raises(runner.SyncBusy):                  # a deletion (purge) is refused
                with runner.hold("coros"):
                    pass
        assert runner._BUSY == set()
    run(go())
