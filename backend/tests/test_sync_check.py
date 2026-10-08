"""SP-362 A3 / A4: 完整檢查 (full listing compared with the DB by provider id), 「補下載」 of
the missing ones, and the weekly check of the last 60 days (sync/check.py). COROS / TP are
fake (httpx.MockTransport, paged like the real list API); the DB is in memory. Nothing is
ever deleted, the sync cursor never moves."""
import asyncio
import gzip
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from backend.db.models import SyncFailure, SyncState, WorkoutFile
from backend.engine import calibrate, plan_auto
from backend.settings.repository import SettingsRepository
from backend.sync import check, coros_client, failures as FL, http, runner, scheduler
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import FakeTP, _tp_state, dev, make_session, run

TODAY = date.today()


def _factory(s):
    class _Ctx:
        async def __aenter__(self):
            return s

        async def __aexit__(self, *a):
            return False
    return lambda: _Ctx()


class PagedCoros:
    """COROS /activity/query as the sync uses it: pages of `size`, filtered by startDay /
    endDay; detail/download -> a FIT (or 500 when `files[labelId]` is None)."""

    def __init__(self, acts, files=None, list_error=False):
        self.acts, self.files, self.list_error = acts, files or {}, list_error
        self.list_calls, self.downloads = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/account/login":
            return httpx.Response(200, json={"result": "0000", "data": {"accessToken": "ctok", "userId": 42}})
        if path == "/activity/query":
            q = dict(request.url.params)
            if q.get("size") == "1":                      # the data-server probe
                return httpx.Response(200, json={"result": "0000", "data": {"dataList": []}})
            self.list_calls.append(q)
            if self.list_error:
                return httpx.Response(502, text="bad gateway")
            sel = [a for a in self.acts if q["startDay"] <= str(a["date"]) <= q["endDay"]]
            n, p = int(q["size"]), int(q["pageNumber"])
            return httpx.Response(200, json={"result": "0000", "data": {"dataList": sel[(p - 1) * n:p * n]}})
        if path == "/activity/detail/download":
            lid = request.url.params["labelId"]
            self.downloads.append(lid)
            if self.files.get(lid) is None:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json={"result": "0000", "data": {"fileUrl": f"https://files.example/fit/{lid}"}})
        if path.startswith("/fit/"):
            return httpx.Response(200, content=self.files[path.split("/")[-1]])
        if path == "/activity/detail/query":
            return httpx.Response(500)
        return httpx.Response(404)


def act(label, day, **kw):
    return {"labelId": label, "date": int(day.strftime("%Y%m%d")), "sportType": 100, **kw}


def _dt(d):
    return datetime(d.year, d.month, d.day, 6, 0, tzinfo=timezone.utc)


async def _local(s, label, day, source="coros"):
    s.add(WorkoutFile(athlete_id=1, file_path=f"/x/{label}.fit", file_format="fit", source=source,
                      coros_activity_id=label if source == "coros" else None,
                      tp_workout_id=int(label) if source == "trainingpeaks" else None, workout_date=day))
    await s.commit()


@pytest.fixture
def hooks(monkeypatch):
    seen = []
    monkeypatch.setattr(plan_auto, "after_sync", lambda src, res: seen.append(("plan", dict(res))))
    monkeypatch.setattr(calibrate, "after_sync", lambda src, res, aid: seen.append(("calib", dict(res))))
    from backend.api import wko5views
    monkeypatch.setattr(wko5views, "warm_up", lambda reason: seen.append(("warm", reason)))
    from backend.engine import localtime

    async def tz(db, aid):
        return None
    monkeypatch.setattr(localtime, "refresh_from_fits", tz)
    return seen


async def _coros_ready(s, fake):
    with http.use_transport(httpx.MockTransport(fake)):
        await coros_client.login("me@example.com", "pw", s, 1)
    st = (await s.execute(select(SyncState))).scalar_one()
    st.coros_last_sync_at = datetime(2026, 10, 1, tzinfo=timezone.utc)     # a cursor that must not move
    await s.commit()
    return st.coros_last_sync_at


async def _go(s, fake, source="coros", mode=check.FULL, ids=None):
    with http.use_transport(httpx.MockTransport(fake)):
        t = check.start(source, 1, mode, ids=ids, session_factory=_factory(s))
        assert t is not None
        return await t


def _seed_remote():
    """45 COROS activities (3 pages of 20) over the last ~90 days, newest first like COROS."""
    days = [TODAY - timedelta(days=2 * i) for i in range(45)]
    return [act(f"A{i:02d}", d) for i, d in enumerate(days)]


# ---------------------------------------------------------------------------- FULL

def test_full_check_lists_every_page_and_reports_three_groups_without_touching_anything(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        acts = _seed_remote()
        fake = PagedCoros(acts)
        cursor = await _coros_ready(s, fake)
        for a in acts:
            if a["labelId"] not in ("A03", "A30", "A41"):
                await _local(s, a["labelId"], datetime.strptime(str(a["date"]), "%Y%m%d").date())
        await _local(s, "GONE", TODAY - timedelta(days=5))                   # deleted on the watch?
        await FL.record_failure(s, 1, "coros", "A41", "HTTP 500", 100, TODAY - timedelta(days=82))
        n_rows = (await s.execute(select(func.count(WorkoutFile.id)))).scalar()
        await _go(s, fake)
        assert len(fake.list_calls) == 3                                      # 20 + 20 + 5
        assert fake.list_calls[0]["startDay"] == coros_client.FIRST_SYNC_DAY
        assert fake.downloads == []                                           # a check downloads nothing
        res = await SettingsRepository(s, 1).get("sync.coros.check")
        assert res["status"] == "ok" and res["mode"] == "full" and res["remote"] == 45
        assert [m["id"] for m in res["missing"]] == ["A30", "A03"] and res["missing_n"] == 2   # oldest first
        assert res["missing"][0]["sport_type"] == 100 and res["missing"][0]["date"]
        assert [x["id"] for x in res["local_only"]] == ["GONE"] and res["local_only_n"] == 1
        assert [x["id"] for x in res["failed"]] == ["A41"] and res["failed"][0]["state"] == "retrying"
        # listed only: nothing deleted, the cursor and the last sync result untouched
        assert (await s.execute(select(func.count(WorkoutFile.id)))).scalar() == n_rows
        assert (await s.execute(select(SyncState))).scalar_one().coros_last_sync_at == cursor.replace(tzinfo=None)
        assert await SettingsRepository(s, 1).get("sync.coros.last_result") is None
        assert check.progress("coros") is None and not check.running("coros") and not runner.is_busy("coros")
        assert hooks == [("plan", hooks[0][1]), ("calib", hooks[1][1])] and hooks[0][1]["downloaded"] == 0
    run(go())


def test_fill_downloads_only_the_missing_ones_and_records_failures(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        acts = _seed_remote()[:5]
        fake = PagedCoros(acts)
        cursor = await _coros_ready(s, fake)
        for a in acts[2:]:
            await _local(s, a["labelId"], datetime.strptime(str(a["date"]), "%Y%m%d").date())
        await _go(s, fake)
        assert [m["id"] for m in (await SettingsRepository(s, 1).get("sync.coros.check"))["missing"]] == ["A01", "A00"]
        fake.files = {"A00": build_run(_dt(TODAY)), "A01": None}              # A01: HTTP 500
        await _go(s, fake, mode=check.FILL)
        assert sorted(fake.downloads) == ["A00", "A01"]
        ids = (await s.execute(select(WorkoutFile.coros_activity_id))).scalars().all()
        assert "A00" in ids and "A01" not in ids
        (row,) = (await s.execute(select(SyncFailure))).scalars().all()      # the sync's failed list
        assert row.provider_id == "A01" and row.attempts == 1
        res = await SettingsRepository(s, 1).get("sync.coros.check")
        assert res["missing"] == [] and res["missing_n"] == 0
        assert res["filled"]["downloaded"] == 1 and res["filled"]["errors"] == 1 and res["filled"]["status"] == "partial"
        assert (await s.execute(select(SyncState))).scalar_one().coros_last_sync_at == cursor.replace(tzinfo=None)
        assert ("warm", "sync-coros") in hooks                                 # the post-run steps ran
        assert any(h[0] == "plan" and h[1]["downloaded"] == 1 for h in hooks)
        assert await SettingsRepository(s, 1).get("sync.coros.last_result") is None
    run(go())


def test_fill_only_the_ids_asked_for(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        acts = _seed_remote()[:3]
        fake = PagedCoros(acts)
        await _coros_ready(s, fake)
        await _go(s, fake)
        fake.files = {a["labelId"]: build_run(_dt(TODAY)) for a in acts}
        await _go(s, fake, mode=check.FILL, ids=["A01"])
        assert fake.downloads == ["A01"]
        res = await SettingsRepository(s, 1).get("sync.coros.check")
        assert [m["id"] for m in res["missing"]] == ["A02", "A00"] and res["missing_n"] == 2
    run(go())


def test_a_listing_failure_is_stored_and_moves_nothing(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        fake = PagedCoros(_seed_remote(), list_error=True)
        cursor = await _coros_ready(s, fake)
        await _go(s, fake)
        res = await SettingsRepository(s, 1).get("sync.coros.check")
        assert res["status"] == "failed" and res["error"] == "COROS_API_ERROR" and "missing" not in res
        assert (await s.execute(select(SyncState))).scalar_one().coros_last_sync_at == cursor.replace(tzinfo=None)
        assert hooks and all(h[0] != "warm" and h[1]["status"] == "failed" for h in hooks)   # gates: nothing starts
    run(go())


def test_a_check_and_a_sync_never_run_together(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        with runner.hold("coros"):                                            # a sync is running
            assert check.start("coros", 1, check.FULL, session_factory=_factory(s)) is None
            assert not check.running("coros")
        # a check is running: the sync gets SYNC_BUSY
        gate = asyncio.Event()

        async def slow(db):
            yield {"status": "started"}
            await gate.wait()
            yield {"status": "complete", "total_downloaded": 0, "total_checked": 0, "errors": []}
        t = asyncio.create_task(_drain(runner.stream(s, "coros", 1, client=slow, remember=False)))
        await asyncio.sleep(0.05)
        assert runner.is_busy("coros")
        busy = [e async for e in runner.stream(s, "coros", 1)]
        assert busy[0]["error"] == "SYNC_BUSY"
        gate.set()
        await t
    run(go())


async def _drain(gen):
    return [e async for e in gen]


# ---------------------------------------------------------------------------- TP

def test_tp_full_check_lists_90_day_windows_and_skips_planned_workouts(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        d = lambda n: (TODAY - timedelta(days=n)).isoformat()
        future = (TODAY + timedelta(days=3)).isoformat()
        wos = [{"workoutId": 1, "workoutDay": d(400)}, {"workoutId": 2, "workoutDay": d(10)},
               {"workoutId": 3, "workoutDay": d(2)}, {"workoutId": 4, "workoutDay": future}]
        fake = FakeTP(wos, {}, {})
        await _local(s, "1", TODAY - timedelta(days=400), source="trainingpeaks")
        await _local(s, "9", TODAY - timedelta(days=30), source="trainingpeaks")
        await FL.record_no_file(s, 1, "tp", 3, workout_date=TODAY - timedelta(days=2))
        await _go(s, fake, source="tp")
        assert fake.ranges[0][0] == "2010-01-01" and len(fake.ranges) > 60
        assert all((date.fromisoformat(b) - date.fromisoformat(a)).days < 90 for a, b in fake.ranges)
        res = await SettingsRepository(s, 1).get("sync.trainingpeaks.check")
        assert res["status"] == "ok" and res["remote"] == 3                   # the planned one skipped
        assert [m["id"] for m in res["missing"]] == ["2"]
        assert [x["id"] for x in res["local_only"]] == ["9"]
        assert [(x["id"], x["state"]) for x in res["failed"]] == [("3", "no_file")]
        # 補下載 through the TP fetch path
        fake.detail, fake.files = {2: (200, dev(fid=2))}, {2: (200, gzip.compress(build_run(_dt(TODAY))))}
        await _go(s, fake, source="tp", mode=check.FILL)
        tp_ids = (await s.execute(select(WorkoutFile.tp_workout_id))).scalars().all()
        assert 2 in tp_ids
        assert (await SettingsRepository(s, 1).get("sync.trainingpeaks.check"))["filled"]["downloaded"] == 1
    run(go())


# ---------------------------------------------------------------------------- WEEKLY (A4)

def test_weekly_check_lists_60_days_and_fetches_the_missing_ones(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        acts = _seed_remote()                                                 # 90 days
        fake = PagedCoros(acts, files={"A05": build_run(_dt(TODAY - timedelta(days=10)))})
        cursor = await _coros_ready(s, fake)
        for a in acts:
            if a["labelId"] not in ("A05", "A40"):                           # A40 is 80 days old
                await _local(s, a["labelId"], datetime.strptime(str(a["date"]), "%Y%m%d").date())
        await _local(s, "GONE", TODAY - timedelta(days=5))
        await _go(s, fake, mode=check.WEEKLY)
        assert fake.list_calls[0]["startDay"] == (TODAY - timedelta(days=60)).strftime("%Y%m%d")
        assert len(fake.list_calls) == 2                                      # 31 activities in 60 days
        assert fake.downloads == ["A05"]                                      # A40 is outside the window
        w = await SettingsRepository(s, 1).get("sync.coros.check_weekly")
        assert w["status"] == "ok" and w["fetched"] == 1 and w["missing_n"] == 1 and w["local_only_n"] == 1
        assert await SettingsRepository(s, 1).get("sync.coros.check") is None    # the full result is separate
        assert (await s.execute(select(func.count(WorkoutFile.id)).where(
            WorkoutFile.coros_activity_id == "GONE"))).scalar() == 1            # listed only, never deleted
        assert (await s.execute(select(SyncState))).scalar_one().coros_last_sync_at == cursor.replace(tzinfo=None)
        assert any(h[0] == "plan" and h[1]["downloaded"] == 1 for h in hooks)
    run(go())


def test_weekly_check_skips_failed_list_rows_and_caps_downloads(tmp_path, hooks, monkeypatch):
    monkeypatch.setattr(check, "WEEKLY_FETCH_MAX", 2)

    async def go():
        s = await make_session(tmp_path)
        acts = _seed_remote()[:6]
        fake = PagedCoros(acts, files={a["labelId"]: build_run(_dt(TODAY)) for a in acts})
        await _coros_ready(s, fake)
        await FL.record_failure(s, 1, "coros", "A00", "x", 100, TODAY)
        await _go(s, fake, mode=check.WEEKLY)
        assert len(fake.downloads) == 2 and "A00" not in fake.downloads      # the sync retries A00 itself
        w = await SettingsRepository(s, 1).get("sync.coros.check_weekly")
        assert w["missing_n"] == 5 and w["fetched"] == 2 and w["failed_n"] == 1
    run(go())


def test_compare_groups_and_window_edges():
    since, until = date(2026, 8, 1), date(2026, 9, 30)
    remote = [{"id": "R1", "date": "2026-09-01"}, {"id": "R2", "date": "2026-09-02"}, {"id": "F", "date": "2026-09-03"}]
    local = {"R1": date(2026, 9, 1), "EDGE0": since, "EDGE1": until, "MID": date(2026, 9, 10), "OLD": date(2026, 1, 1),
             "NODATE": None}
    g = check.compare(remote, local, {"F": {"id": "F", "date": "2026-09-03", "state": "stopped"}}, since, until, whole=False)
    assert [m["id"] for m in g["missing"]] == ["R2"]
    assert [x["id"] for x in g["local_only"]] == ["MID"]                     # edges, old and undated skipped
    assert g["failed_n"] == 1 and g["remote"] == 3
    g = check.compare(remote, local, {}, since, until, whole=True)
    assert [x["id"] for x in g["local_only"]] == ["NODATE", "MID"]


def test_weekly_due():
    now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    assert check.weekly_due(None, now)
    assert not check.weekly_due({"at": (now - timedelta(days=3)).isoformat(), "status": "ok"}, now)
    assert check.weekly_due({"at": (now - timedelta(days=7)).isoformat(), "status": "ok"}, now)
    assert not check.weekly_due({"at": (now - timedelta(hours=5)).isoformat(), "status": "failed"}, now)
    assert check.weekly_due({"at": (now - timedelta(days=1, minutes=1)).isoformat(), "status": "failed"}, now)


def test_weekly_tick_only_the_source_in_use_when_due(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, coros_access_token="t", tp_access_token="t"))
        await s.commit()
        repo = SettingsRepository(s, 1)
        await repo.set("sync.primary_source", "coros")
        await s.commit()
        calls = []
        start = lambda src, aid: calls.append(src) or object()
        now = datetime.now(timezone.utc)
        assert await check.weekly_tick(_factory(s), now, start_fn=start) == ["coros"]
        await repo.set("sync.coros.check_weekly", {"at": (now - timedelta(days=2)).isoformat(), "status": "ok"})
        await s.commit()
        assert await check.weekly_tick(_factory(s), now, start_fn=start) == []
        with runner.hold("coros"):                                            # busy: not ready
            await repo.set("sync.coros.check_weekly", None)
            await s.commit()
            assert await check.weekly_tick(_factory(s), now, start_fn=start) == []
        assert calls == ["coros"]
    run(go())


@pytest.mark.parametrize("uptime,expect", [(10.0, False), (check.STARTUP_DELAY_S + 1, True)])
def test_scheduler_loop_runs_the_weekly_tick_after_the_startup_delay(monkeypatch, uptime, expect):
    seen = []

    async def tick(factory, *a, **k):
        return []

    async def weekly(factory, *a, **k):
        seen.append("weekly")
        return []
    monkeypatch.setattr(scheduler, "tick", tick)
    monkeypatch.setattr(check, "weekly_tick", weekly)
    monkeypatch.setenv("WKO5COACH_NO_AUTO_BACKUP", "1")
    calls = {"n": 0}

    def mono():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else uptime          # the first call is the loop's start
    monkeypatch.setattr(scheduler, "_monotonic", mono)

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(scheduler.loop(object(), interval=0.01), timeout=0.1)
    run(go())
    assert bool(seen) is expect


# ---------------------------------------------------------------------------- API

def test_api_start_status_and_fill(tmp_path, hooks, monkeypatch):
    from backend.api import sync as API

    async def go():
        s = await make_session(tmp_path)
        out = await API.check_status(1, s)
        assert set(out) == {"coros", "tp"} and out["coros"]["last"] is None and out["coros"]["ready"] == "not_logged_in"
        r = await API.check_start(None, 1, s)
        assert r == {"started": [], "skipped": {"coros": "not_logged_in", "tp": "not_logged_in"}}
        s.add(SyncState(athlete_id=1, coros_access_token="t"))
        await s.commit()
        started = []
        monkeypatch.setattr(check, "start", lambda src, aid, mode, ids=None, **k: started.append((src, mode, ids)) or object())
        r = await API.check_start(API.CheckBody(source="coros"), 1, s)
        assert r["started"] == ["coros"] and started == [("coros", "full", None)]
        with pytest.raises(HTTPException) as ei:
            await API.check_fill("coros", None, 1, s)
        assert ei.value.status_code == 404                                    # no check yet
        await SettingsRepository(s, 1).set("sync.coros.check", {"status": "ok", "missing": [{"id": "X"}]})
        await s.commit()
        assert (await API.check_fill("coros", API.CheckBody(ids=["X"]), 1, s))["status"] == "started"
        assert started[-1] == ("coros", "fill", ["X"])
        with runner.hold("coros"):
            with pytest.raises(HTTPException) as ei:
                await API.check_fill("coros", None, 1, s)
            assert ei.value.status_code == 409
            with pytest.raises(HTTPException) as ei:
                await API.check_start(API.CheckBody(source="coros"), 1, s)
            assert ei.value.status_code == 409
        with pytest.raises(HTTPException):
            await API.check_start(API.CheckBody(source="garmin"), 1, s)
        # the stored result survives a reload (a new request reads it back)
        assert (await API.check_status(1, s))["coros"]["last"]["missing"] == [{"id": "X"}]
    run(go())


def test_progress_is_visible_while_listing(tmp_path, hooks):
    async def go():
        s = await make_session(tmp_path)
        acts = _seed_remote()
        fake = PagedCoros(acts)
        await _coros_ready(s, fake)
        seen = []
        real = check._prog

        def spy(source, **kw):
            real(source, **kw)
            seen.append(dict(check._PROGRESS[source]))
        check._prog = spy
        try:
            await _go(s, fake)
        finally:
            check._prog = real
        pages = [p["pages"] for p in seen if p.get("phase") == "list" and p.get("pages")]
        assert pages == [1, 2, 3] and seen[-1]["listed"] == 45
    run(go())
