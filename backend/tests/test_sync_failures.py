"""The failed list (sync/failures.py, SP-362): one activity's failed download no longer holds
the sync cursor; it is recorded and retried by id (at most 5 attempts or 7 days), an activity
without a FIT is no_file, a failure of the whole run still keeps the cursor. COROS / TP are
fake (httpx.MockTransport); the DB is in memory."""
import gzip
import json
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from backend.db.models import SyncFailure, SyncState, WorkoutFile
from backend.sync import coros_client, failures as FL, http, tp_client
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import START, FakeTP, _tp_state, collect, dev, make_session, run


class FakeCoros:
    """COROS: `listed` is what /activity/query returns (page 1); `files[labelId]` = FIT bytes
    (None = detail/download answers HTTP 500); `no_file` = answered without a fileUrl."""

    def __init__(self, listed, files, no_file=(), list_error=False):
        self.listed, self.files, self.no_file, self.list_error = listed, files, set(no_file), list_error
        self.downloads = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/account/login":
            return httpx.Response(200, json={"result": "0000", "data": {"accessToken": "ctok", "userId": 42}})
        if path == "/activity/query":
            q = dict(request.url.params)
            if self.list_error and q.get("size") != "1":
                return httpx.Response(502, text="bad gateway")
            items = self.listed if q.get("pageNumber") == "1" and q.get("size") != "1" else []
            return httpx.Response(200, json={"result": "0000", "data": {"dataList": items}})
        if path == "/activity/detail/download":
            lid = request.url.params["labelId"]
            self.downloads.append(lid)
            if lid in self.no_file:
                return httpx.Response(200, json={"result": "0000", "data": {}})
            if self.files.get(lid) is None:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json={"result": "0000", "data": {"fileUrl": f"https://files.example/fit/{lid}"}})
        if path.startswith("/fit/"):
            return httpx.Response(200, content=self.files[path.split("/")[-1]])
        return httpx.Response(404)


def act(label, day):
    return {"labelId": label, "date": int(day.strftime("%Y%m%d")), "sportType": 100}


async def _sync(s, fake):
    with http.use_transport(httpx.MockTransport(fake)):
        return await collect(coros_client.sync_workouts(s, 1))


async def _login(s, fake):
    with http.use_transport(httpx.MockTransport(fake)):
        await coros_client.login("me@example.com", "pw", s, 1)


async def _cursor(s):
    return (await s.execute(select(SyncState))).scalar_one().coros_last_sync_at


async def _rows(s):
    return (await s.execute(select(SyncFailure).order_by(SyncFailure.id))).scalars().all()


def test_coros_a_failed_download_is_listed_and_the_cursor_moves(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        d2 = START + timedelta(days=1)
        fake = FakeCoros([act("OK", START), act("BAD", d2)], {"OK": build_run(START), "BAD": None})
        await _login(s, fake)
        ev = await _sync(s, fake)
        assert [e["activity_id"] for e in ev if e.get("status") == "downloaded"] == ["OK"]
        assert len(ev[-1]["errors"]) == 1                    # still reported (runner: partial)
        assert await _cursor(s) is not None                  # SP-362: the cursor moved
        (row,) = await _rows(s)
        assert (row.source, row.provider_id, row.sport_type, row.kind, row.attempts) == ("coros", "BAD", 100,
                                                                                         "failed", 1)
        assert row.workout_date == d2.date() and "500" in row.last_error
        # the next sync: listing has nothing new (the cursor is past it); BAD is retried BY ID
        fake2 = FakeCoros([], {"BAD": build_run(d2)})
        ev2 = await _sync(s, fake2)
        assert fake2.downloads == ["BAD"]
        assert any(e.get("retry") for e in ev2 if e.get("status") == "checking")
        assert ev2[-1]["total_downloaded"] == 1 and ev2[-1]["errors"] == []
        assert await _rows(s) == []                          # imported: off the list
        ids = (await s.execute(select(WorkoutFile.coros_activity_id))).scalars().all()
        assert sorted(ids) == ["BAD", "OK"]
    run(go())


def test_coros_retries_stop_after_five_attempts_and_reset_makes_it_due(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([act("BAD", START)], {"BAD": None})
        await _login(s, fake)
        for _ in range(FL.MAX_ATTEMPTS + 2):
            await _sync(s, fake)
        assert fake.downloads == ["BAD"] * FL.MAX_ATTEMPTS     # never a 6th automatic attempt
        (row,) = await _rows(s)
        assert row.attempts == FL.MAX_ATTEMPTS and FL.state(row) == "stopped"
        ev = await _sync(s, fake)                            # listed again (overlap): skipped
        assert {"status": "skipped", "activity_id": "BAD", "reason": "retry_stopped"} in ev
        # 「重試」 (設定 › 進階設定): due again, and this time the file is there
        assert (await FL.reset(s, 1, row.id)).attempts == 0
        fake.files["BAD"] = build_run(START)
        ev = await _sync(s, fake)
        assert ev[-1]["total_downloaded"] == 1 and await _rows(s) == []
    run(go())


def _ago(days):
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days, minutes=1)


def test_coros_retries_stop_after_seven_days_of_trying(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([], {"OLD": None})
        await _login(s, fake)
        row = await FL.record_failure(s, 1, "coros", "OLD", "boom", 100, date(2026, 9, 1))
        await FL.record_failure(s, 1, "coros", "OLD", "boom")          # a 2nd attempt …
        row.first_at = _ago(FL.MAX_AGE_DAYS)                           # … 7 days after the 1st
        await s.commit()
        await _sync(s, fake)
        assert fake.downloads == []
        assert (await FL.listing(s, 1))[0]["state"] == "stopped"
    run(go())


def test_one_attempt_then_a_week_without_syncs_is_still_retried(tmp_path):
    """Review SP-362 #2: a failure, then 8 days away (a 百岳 trip, no sync) — 7 days means 7
    days of trying, so the next sync still tries it."""
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([], {"AWAY": build_run(START)})
        await _login(s, fake)
        row = await FL.record_failure(s, 1, "coros", "AWAY", "boom", 100, START.date())
        row.first_at = row.last_at = _ago(8)
        await s.commit()
        assert FL.state(row) == "retrying"
        ev = await _sync(s, fake)
        assert fake.downloads == ["AWAY"] and ev[-1]["total_downloaded"] == 1 and await _rows(s) == []
    run(go())


def test_coros_no_file_needs_two_answers_and_is_then_not_asked_again(tmp_path):
    """Review SP-362 #3: one answer without a fileUrl is an ordinary (retryable) failure; the
    second makes it no_file, which the listing then skips."""
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([act("MAN", START)], {}, no_file={"MAN"})
        await _login(s, fake)
        ev = await _sync(s, fake)
        assert any(e.get("status") == "error" and e.get("activity_id") == "MAN" for e in ev)
        (row,) = await _rows(s)
        assert row.kind == "failed" and row.attempts == 1 and FL.state(row) == "retrying"
        ev = await _sync(s, fake)
        assert {"status": "no_file", "activity_id": "MAN", "date": "2026-09-01"} in ev
        assert ev[-1]["errors"] == [] and await _cursor(s) is not None
        (row,) = await _rows(s)
        assert row.kind == "no_file" and FL.state(row) == "no_file"
        ev = await _sync(s, fake)                            # listed again: no third request
        assert fake.downloads == ["MAN", "MAN"]
        assert any(e.get("reason") == "no_file" for e in ev)
        # 「重試」 on a no_file row: asked again by id (the file may exist now)
        await FL.reset(s, 1, row.id)
        fake.no_file.clear()
        fake.files["MAN"] = build_run(START)
        fake.listed = []
        ev = await _sync(s, fake)
        assert ev[-1]["total_downloaded"] == 1 and await _rows(s) == []
    run(go())


def test_coros_an_import_failure_is_retried_by_id(tmp_path, monkeypatch):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([act("IMP", START)], {"IMP": build_run(START)})
        await _login(s, fake)
        real = coros_client._import_one_file

        async def boom(*a, **k):
            raise RuntimeError("disk full")
        monkeypatch.setattr(coros_client, "_import_one_file", boom)
        ev = await _sync(s, fake)
        assert any("import_failed" in (e.get("error") or "") for e in ev) and await _cursor(s) is not None
        (row,) = await _rows(s)
        assert row.kind == "failed" and "import_failed" in row.last_error
        monkeypatch.setattr(coros_client, "_import_one_file", real)
        fake.listed = []                                     # not listed again: retried by id
        ev = await _sync(s, fake)
        assert ev[-1]["total_downloaded"] == 1 and await _rows(s) == []
        assert fake.downloads == ["IMP", "IMP"]
    run(go())


def test_coros_retry_by_id_stores_no_training_load_and_the_listing_fills_it(tmp_path):
    """Review SP-362 #6: the retry-by-id path has no list item (no trainingLoad): it never
    writes one; the overlap listing fills it as for any imported activity."""
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([act("TL", START)], {"TL": None})
        await _login(s, fake)
        await _sync(s, fake)
        fake.files["TL"], fake.listed = build_run(START), []
        await _sync(s, fake)
        wf = (await s.execute(select(WorkoutFile).where(WorkoutFile.coros_activity_id == "TL"))).scalar_one()
        assert wf.coros_training_load is None
        fake.listed = [{**act("TL", START), "trainingLoad": 55}]
        ev = await _sync(s, fake)
        await s.refresh(wf)
        assert wf.coros_training_load == 55 and ev[-1]["tl_filled"] == 1
    run(go())


def test_coros_a_listing_failure_keeps_the_cursor(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([act("A", START)], {"A": build_run(START)})
        await _login(s, fake)
        await _sync(s, fake)
        before = await _cursor(s)
        await FL.record_failure(s, 1, "coros", "B", "boom")
        broken = FakeCoros([], {"B": build_run(START)}, list_error=True)
        ev = await _sync(s, broken)
        assert ev[-1]["error"] == "COROS_API_ERROR"
        assert await _cursor(s) == before and broken.downloads == []     # no retries either
    run(go())


def test_coros_cursor_stays_when_the_failed_list_cannot_be_written(tmp_path, monkeypatch):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([act("BAD", START)], {"BAD": None})
        await _login(s, fake)

        async def broken(*a, **k):
            raise RuntimeError("db locked")
        monkeypatch.setattr(FL, "record_failure", broken)
        await _sync(s, fake)
        assert await _cursor(s) is None                     # the old behaviour: nothing lost
    run(go())


def test_coros_overlap_is_fourteen_days(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([], {})
        await _login(s, fake)
        await _sync(s, fake)
        ev = await _sync(s, fake)
        want = datetime.now(timezone.utc).date() - timedelta(days=14)
        assert coros_client.CURSOR_OVERLAP_DAYS == 14 and ev[0]["since"] == want.strftime("%Y%m%d")
        assert coros_client.RETRY_DAYS == 4                 # the self-rating retry window did not grow
    run(go())


# ---------------------------------------------------------------------------- TrainingPeaks

def _tp(detail, files, workouts):
    return FakeTP(workouts, detail, files)


def test_tp_a_failed_download_is_listed_the_cursor_moves_and_it_is_retried_by_id(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        fit2 = gzip.compress(build_run(START + timedelta(days=1), seconds=300))
        wos = [{"workoutId": 1, "workoutDay": "2026-09-02"}, {"workoutId": 2, "workoutDay": "2026-09-03"},
               {"workoutId": 3, "workoutDay": "2026-09-04"}]
        fake = _tp({1: (200, dev(fid=1)), 2: (500, {}), 3: (200, {"workoutDeviceFileInfos": []})},
                   {1: (200, gzip.compress(build_run(START)))}, wos)
        with http.use_transport(httpx.MockTransport(fake)):
            ev = await collect(tp_client.sync_workouts(s, 1))
        assert len(ev[-1]["errors"]) == 1
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.last_sync_cursor == datetime.now(timezone.utc).date().isoformat()
        rows = {r.provider_id: r for r in await _rows(s)}
        assert rows["2"].kind == "failed" and rows["2"].workout_date == date(2026, 9, 3)
        assert rows["3"].kind == "no_file"                 # remembered, not a failure
        # incremental: workouts/changed lists nothing; workout 2 is retried by id and imported
        fake2 = _tp({2: (200, dev(fid=2))}, {2: (200, fit2)}, [])
        with http.use_transport(httpx.MockTransport(fake2)):
            ev2 = await collect(tp_client.sync_workouts(s, 1))
        assert ev2[-1]["total_downloaded"] == 1 and ev2[-1]["errors"] == []
        assert [r.provider_id for r in await _rows(s)] == ["3"]
        assert not any("/workouts/3/" in c for c in fake2.calls)    # no_file: never asked again
    run(go())


def test_tp_no_file_is_checked_again_when_the_listing_returns_it(tmp_path):
    """Review SP-362 #1: a TP workout without a device file yet (planned, not uploaded) that
    gets its file later comes back in workouts/changed — it is downloaded then."""
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        wo = [{"workoutId": 5, "workoutDay": "2026-09-02"}]
        with http.use_transport(httpx.MockTransport(_tp({5: (200, {"workoutDeviceFileInfos": []})}, {}, wo))):
            await collect(tp_client.sync_workouts(s, 1))
        assert [r.kind for r in await _rows(s)] == ["no_file"]
        fake = _tp({5: (200, dev(fid=5))}, {5: (200, gzip.compress(build_run(START)))}, wo)   # changed: file now
        with http.use_transport(httpx.MockTransport(fake)):
            ev = await collect(tp_client.sync_workouts(s, 1))
        assert any("/workouts/changed" in c for c in fake.calls)
        assert ev[-1]["total_downloaded"] == 1 and await _rows(s) == []
    run(go())


def test_tp_listing_skips_a_stopped_row(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        for _ in range(FL.MAX_ATTEMPTS):
            await FL.record_failure(s, 1, "tp", 6, "HTTP 500")
        fake = _tp({6: (200, dev(fid=6))}, {6: (200, gzip.compress(build_run(START)))},
                   [{"workoutId": 6, "workoutDay": "2026-09-02"}])
        with http.use_transport(httpx.MockTransport(fake)):
            ev = await collect(tp_client.sync_workouts(s, 1))
        assert {"status": "skipped", "workout_id": 6, "workout_date": "2026-09-02", "reason": "retry_stopped"} in ev
        assert not any("/workouts/6/" in c for c in fake.calls)
    run(go())


# ---------------------------------------------------------------------------- list, API, purge

def test_listing_redacts_and_api_retry_queues_without_a_login(tmp_path, monkeypatch):
    from backend.api import sync as API

    async def go():
        s = await make_session(tmp_path)
        await FL.record_failure(s, 1, "coros", "X1", "GET https://files.example/f.fit?sig=abc123SECRET failed",
                                100, date(2026, 9, 5))
        await FL.record_no_file(s, 1, "tp", 77, workout_date=date(2026, 9, 6))
        out = await API.failed_list(1, s)
        assert [i["state"] for i in out["items"]] == ["no_file", "retrying"]
        assert "SECRET" not in out["items"][1]["last_error"] and out["max_attempts"] == 5
        started = []
        monkeypatch.setattr(API.runner, "start_background", lambda *a, **k: started.append(a) or object())
        r = await API.failed_retry(out["items"][1]["id"], 1, s)
        assert r == {"id": out["items"][1]["id"], "source": "coros", "status": "queued"} and started == []
    run(go())


def test_purge_clears_the_sources_failed_list(tmp_path):
    from backend.sync import purge

    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1))
        await s.commit()
        await FL.record_failure(s, 1, "coros", "C1", "x")
        await FL.record_failure(s, 1, "tp", "9", "x")
        await purge.delete_source_files(s, "coros", 1)
        assert [(r.source, r.provider_id) for r in await _rows(s)] == [("tp", "9")]
    run(go())
