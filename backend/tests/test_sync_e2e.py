"""
End-to-end sync tests with fake HTTP (httpx.MockTransport) — never touches a
real account or the network.

login → list → download → import → de-dup, for both TrainingPeaks and COROS,
plus the hardening fixes: naive token expiry, TP 401/403/404 not advancing the
cursor, rollback on import failure, corrupt-FIT stubs, COROS incremental
cursor, athlete-local workout dates, hrTSS without power, date-effective FTP.
"""
import asyncio
import re
import base64
import gzip
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Athlete, AthleteSettings, Base, SyncState, WorkoutFile
from backend.settings.repository import SettingsRepository
from backend.sync import coros_client, dedup, http, tp_client
from backend.tests.fit_builder import build_run

START = datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)   # 06:30 next day in Taipei


def run(coro):
    return asyncio.run(coro)


async def make_session(tmp_path, tz="Asia/Taipei", **settings):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = async_sessionmaker(engine, expire_on_commit=False)()
    s.add(Athlete(id=1, name="tester", data_dir=str(tmp_path / "tp"), tp_athlete_id=77))
    s.add(AthleteSettings(athlete_id=1, effective_date=date(2020, 1, 1), lthr=170,
                          run_ftp_w=250.0, threshold_pace_s_per_km=300.0))
    await s.flush()
    repo = SettingsRepository(s, 1)
    await repo.set("athlete.timezone", tz)
    for k, v in settings.items():
        await repo.set(k.replace("__", "."), v)
    await s.commit()
    return s


async def collect(gen):
    return [e async for e in gen]


def dev(name="480567774272323786.fit.gz", fid=111):
    """details response with one device file, as the live API returns it."""
    return {"workoutDeviceFileInfos": [{"fileId": fid, "fileSystemId": "x", "fileName": name,
                                       "dateUploaded": "2026-09-02T00:00:00"}],
            "attachmentFileInfos": []}


def fit_payload(fit: bytes) -> dict:
    return {"data": base64.b64encode(gzip.compress(fit)).decode()}


# ---------------------------------------------------------------------------
# TrainingPeaks
# ---------------------------------------------------------------------------

class FakeTP:
    """Scripted TP API. `detail` maps workout id -> (status, json)."""

    def __init__(self, workouts, detail, files):
        self.workouts, self.detail, self.files = workouts, detail, files
        self.calls = []
        self.ranges = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(str(request.url))
        if path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2",
                                             "expires_in": 3600})
        if "/workouts/changed" in path:
            page = int(parse_qs(urlparse(str(request.url)).query)["page"][0])
            items = self.workouts if page == 1 else []
            return httpx.Response(200, json={"modified": items, "deleted": []})
        m = re.search(r"/workouts/(\d{4}-\d\d-\d\d)/(\d{4}-\d\d-\d\d)$", path)
        if m:
            self.ranges.append((m.group(1), m.group(2)))
            return httpx.Response(200, json=[w for w in self.workouts
                                             if m.group(1) <= w["workoutDay"] <= m.group(2)])
        if path.endswith("/details"):
            wid = int(path.split("/")[-2])
            status, body = self.detail[wid]
            return httpx.Response(status, json=body)
        if "/rawfiledata/" in path:
            wid = int(path.split("/")[-3])
            status, body = self.files[wid]
            return httpx.Response(status, content=body, headers={"content-type": "application/gzip"})
        if "/filedata/" in path:
            wid = int(path.split("/")[-3])
            status, body = self.files[wid]
            if isinstance(body, bytes):
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={})


async def _tp_state(s, expires):
    s.add(SyncState(athlete_id=1, tp_access_token="tok", tp_refresh_token="r",
                    tp_token_expires=expires))
    await s.commit()


def test_tp_naive_future_expiry_is_valid_without_network(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))  # naive, as SQLite returns
        fake = FakeTP([], {}, {})
        with http.use_transport(httpx.MockTransport(fake)):
            assert await tp_client._get_valid_token(s, 1) == "tok"
        assert fake.calls == []
    run(go())


def test_tp_naive_past_expiry_refreshes(tmp_path, tp_creds):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() - timedelta(hours=2))
        with http.use_transport(httpx.MockTransport(FakeTP([], {}, {}))):
            assert await tp_client._get_valid_token(s, 1) == "new"
    run(go())


def _tp_fixture(detail2=(403, {"message": "premium only"})):
    fit = build_run(START, seconds=600, power=250)
    return FakeTP(
        workouts=[{"workoutId": 1, "workoutDay": "2026-09-02"},
                  {"workoutId": 2, "workoutDay": "2026-09-03"},
                  {"workoutId": 3, "workoutDay": "2026-09-04"}],
        detail={1: (200, dev(fid=1)),
                2: detail2 if detail2[0] != 200 else (200, dev(fid=2)),
                3: (200, {"workoutDeviceFileInfos": [], "attachmentFileInfos": []})},
        files={1: (200, gzip.compress(fit)),
               2: (200, gzip.compress(build_run(START + timedelta(days=2), seconds=300)))},
    )


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_tp_http_error_is_an_error_and_keeps_the_cursor(tmp_path, status):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        with http.use_transport(httpx.MockTransport(_tp_fixture((status, {})))):
            ev = await collect(tp_client.sync_workouts(s, 1))
        by = {e.get("workout_id"): e["status"] for e in ev if "workout_id" in e and e["status"] != "checking"}
        assert by == {1: "downloaded", 2: "error", 3: "no_file"}
        done = ev[-1]
        assert done["status"] == "complete" and len(done["errors"]) == 1
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.last_sync_cursor is None          # not advanced past workout 2
    run(go())


def test_tp_clean_run_advances_cursor_and_stores_local_date(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        with http.use_transport(httpx.MockTransport(_tp_fixture((200, None)))):
            ev = await collect(tp_client.sync_workouts(s, 1))
        assert ev[-1]["errors"] == []
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.last_sync_cursor == datetime.now(timezone.utc).date().isoformat()
        wf = (await s.execute(select(WorkoutFile).where(WorkoutFile.tp_workout_id == 1))).scalar_one()
        assert wf.source == "trainingpeaks"
        assert wf.start_time_utc == datetime(2026, 9, 1, 22, 30)
        assert wf.workout_date == date(2026, 9, 2)   # Taipei, not the UTC day
    run(go())


def test_import_stores_no_per_activity_metrics(tmp_path):
    """The import writes the workout_files row only: workout_metrics / mmp_cache (written at import,
    never read) are no longer part of the schema, so a new DB does not even have them."""
    from sqlalchemy import text
    from backend.files import file_service

    async def go():
        s = await make_session(tmp_path)
        f = tmp_path / "1_2026-09-01_run.fit"
        f.write_bytes(build_run(START, power=240))
        wf = await file_service._import_one_file(s, 1, f, source="coros")
        await s.commit()
        assert wf is not None and wf.sport == "running" and wf.workout_date == date(2026, 9, 2)
        names = {r[0] for r in (await s.execute(text("SELECT name FROM sqlite_master WHERE type='table'")))}
        assert "workout_files" in names and not names & {"workout_metrics", "mmp_cache"}
        await s.close()
    run(go())


def test_coros_login_stores_lthr_and_weight_not_ftp(tmp_path):
    """The account's LTHR (the cold-start prior, fitdataset._coros_lthr_prior) and weight are kept as
    a dated athlete_settings row; zoneData.ftp is not stored or returned (nothing read it)."""
    class Profile(FakeCoros):
        def __call__(self, request):
            if request.url.path == "/account/login":
                return httpx.Response(200, json={"result": "0000", "data": {
                    "accessToken": "ctok", "userId": 42, "weight": 61.5,
                    "zoneData": {"ftp": 287, "lthr": 171}}})
            return super().__call__(request)

    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(Profile([]))):
            info = await coros_client.login("me@example.com", "pw", s, 1)
        assert info["authenticated"] and "ftp_w" not in info and "lthr" not in info
        today = datetime.now(timezone.utc).date()
        row = (await s.execute(select(AthleteSettings).where(AthleteSettings.effective_date == today))).scalar_one()
        assert row.lthr == 171 and row.weight_kg == pytest.approx(61.5)
        assert not hasattr(row, "ftp_w")
        await s.close()
    run(go())


def test_tp_import_failure_rolls_back(tmp_path, monkeypatch):
    from backend.files import file_service

    async def boom(db, athlete_id, path, **kw):
        db.add(WorkoutFile(athlete_id=athlete_id, file_path="half-written", file_format="fit"))
        await db.flush()
        raise RuntimeError("disk full")

    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        monkeypatch.setattr(file_service, "_import_one_file", boom)
        with http.use_transport(httpx.MockTransport(_tp_fixture((200, None)))):
            ev = await collect(tp_client.sync_workouts(s, 1))
        assert any("import_failed" in (e.get("detail") or "") for e in ev)
        rows = (await s.execute(select(WorkoutFile))).scalars().all()
        assert rows == []
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.last_sync_cursor is None
    run(go())


def test_tp_corrupt_fit_gets_a_stub_and_is_not_redownloaded(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        fake = FakeTP([{"workoutId": 9, "workoutDay": "2026-09-02"}],
                      {9: (200, dev(fid=9))},
                      {9: (200, gzip.compress(b"\x0e\x20not a fit file at all......"))})
        with http.use_transport(httpx.MockTransport(fake)):
            ev1 = await collect(tp_client.sync_workouts(s, 1))
            ev2 = await collect(tp_client.sync_workouts(s, 1, since="2026-01-01"))
        assert any(e.get("detail") == "corrupt_fit" for e in ev1)
        stub = (await s.execute(select(WorkoutFile))).scalar_one()
        assert stub.file_format == "corrupt" and stub.tp_workout_id == 9
        assert any(e.get("reason") == "already_imported" for e in ev2)
    run(go())


def test_tp_future_planned_workouts_are_ignored(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _tp_state(s, datetime.utcnow() + timedelta(hours=2))
        future = (date.today() + timedelta(days=3)).isoformat()
        fake = FakeTP([{"workoutId": 5, "workoutDay": future}], {}, {})
        with http.use_transport(httpx.MockTransport(fake)):
            ev = await collect(tp_client.sync_workouts(s, 1))
        assert not any(e.get("workout_id") == 5 for e in ev)
        assert ev[-1]["errors"] == []
    run(go())


# ---------------------------------------------------------------------------
# COROS
# ---------------------------------------------------------------------------

class FakeCoros:
    def __init__(self, activities):
        self.activities = activities
        self.list_params = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/account/login":
            body = json.loads(request.content)
            assert body["accountType"] == 2 and len(body["pwd"]) == 32   # md5, never plain
            return httpx.Response(200, json={"result": "0000", "data": {
                "accessToken": "ctok", "userId": 42}})
        if path == "/activity/query":
            q = dict(request.url.params)
            self.list_params.append(q)
            items = self.activities if q.get("pageNumber") == "1" and q.get("size") != "1" else []
            items = [{k: v for k, v in a.items() if k != "_bytes"} for a in items]
            return httpx.Response(200, json={"result": "0000", "data": {"dataList": items}})
        if path.startswith("/fit/"):
            label = path.split("/")[-1]
            act = next(a for a in self.activities if a["labelId"] == label)
            return httpx.Response(200, content=act["_bytes"])
        return httpx.Response(404)


def _coros_act(label, start, **kw):
    return {"labelId": label, "date": int(start.strftime("%Y%m%d")), "sportType": 200,
            "fitUrl": f"https://files.example/fit/{label}", "_bytes": build_run(start, **kw)}


def test_coros_login_list_download_import_and_incremental_cursor(tmp_path, monkeypatch):
    pass  # FIT folders are redirected to a temp dir by conftest

    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([_coros_act("A1", START, power=240), _coros_act("A2", START + timedelta(days=1))])
        with http.use_transport(httpx.MockTransport(fake)):
            info = await coros_client.login("me@example.com", "pw", s, 1)
            assert info["authenticated"]
            ev = await collect(coros_client.sync_workouts(s, 1))
            assert ev[0].get("since") == coros_client.FIRST_SYNC_DAY, ev
            assert sum(e.get("status") == "downloaded" for e in ev) == 2, ev
            assert ev[-1]["errors"] == []
            # second run: incremental from the last sync minus the overlap
            ev2 = await collect(coros_client.sync_workouts(s, 1))
        expect = (datetime.now(timezone.utc).date() - timedelta(days=coros_client.CURSOR_OVERLAP_DAYS))
        assert ev2[0]["since"] == expect.strftime("%Y%m%d")
        assert all(e["status"] != "downloaded" for e in ev2)
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()
        assert [r.coros_activity_id for r in rows] == ["A1", "A2"]
        assert rows[0].workout_date == date(2026, 9, 2)
    run(go())


def test_coros_download_error_keeps_cursor(tmp_path, monkeypatch):
    pass  # FIT folders are redirected to a temp dir by conftest

    async def go():
        s = await make_session(tmp_path)
        good = _coros_act("OK", START)
        bad = {"labelId": "BAD", "date": 20260903, "sportType": 200}   # no fitUrl -> detail/download 404
        with http.use_transport(httpx.MockTransport(FakeCoros([good, bad]))):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert len(ev[-1]["errors"]) == 1
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.coros_last_sync_at is None
    run(go())


def test_coros_expired_token_is_a_clear_event(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, coros_access_token="old",
                        coros_token_expires=datetime.utcnow() - timedelta(minutes=1)))
        await s.commit()
        ev = await collect(coros_client.sync_workouts(s, 1))
        assert len(ev) == 1 and ev[0]["error"] == "COROS_AUTH_REQUIRED" and ev[0]["status"] == "error"
    run(go())


# ---------------------------------------------------------------------------
# cross-source de-dup
# ---------------------------------------------------------------------------

def test_same_activity_from_both_sources_counts_once(tmp_path, monkeypatch):
    pass  # FIT folders are redirected to a temp dir by conftest

    async def go():
        s = await make_session(tmp_path)
        # COROS first
        with http.use_transport(httpx.MockTransport(FakeCoros([_coros_act("C1", START, power=250)]))):
            await coros_client.login("me@example.com", "pw", s, 1)
            await collect(coros_client.sync_workouts(s, 1))
        # the same run via TP, recorded 30 s later by the other device clock
        await _tp_state_update(s)
        fake = FakeTP([{"workoutId": 1, "workoutDay": "2026-09-02"}],
                      {1: (200, dev(fid=1))},
                      {1: (200, gzip.compress(build_run(START + timedelta(seconds=30), power=250)))})
        with http.use_transport(httpx.MockTransport(fake)):
            await collect(tp_client.sync_workouts(s, 1))
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()
        assert [r.source for r in rows] == ["coros", "trainingpeaks"]
        assert rows[0].duplicate_of is None and rows[1].duplicate_of == rows[0].id

        counted = (await s.execute(select(WorkoutFile.id).where(await dedup.in_use(s, 1)))).scalars().all()
        assert counted == [rows[0].id]                  # every total reads the COROS row only

        # user makes TP primary: the TP row becomes canonical
        await SettingsRepository(s, 1).set("sync.primary_source", "trainingpeaks")
        r = await dedup.rebuild(s, 1)
        assert r["duplicates"] == 1
        assert rows[0].duplicate_of == rows[1].id and rows[1].duplicate_of is None
    run(go())


async def _tp_state_update(s):
    st = (await s.execute(select(SyncState))).scalar_one()
    st.tp_access_token, st.tp_refresh_token = "tok", "r"
    st.tp_token_expires = datetime.utcnow() + timedelta(hours=2)
    await s.commit()


def test_choose_canonical_prefers_primary_then_oldest_and_skips_corrupt():
    from types import SimpleNamespace as N
    rows = [N(id=3, source="coros", file_format="fit"), N(id=2, source="trainingpeaks", file_format="fit"),
            N(id=1, source="local", file_format="corrupt")]
    assert dedup.choose_canonical(rows, None).id == 2
    assert dedup.choose_canonical(rows, "coros").id == 3
    assert dedup.choose_canonical(rows, "local").id == 2   # corrupt never wins over readable


def test_activities_far_apart_are_not_merged(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        a = WorkoutFile(athlete_id=1, file_path="a", file_format="fit", source="coros",
                        start_time_utc=datetime(2026, 9, 1, 8, 0))
        b = WorkoutFile(athlete_id=1, file_path="b", file_format="fit", source="trainingpeaks",
                        start_time_utc=datetime(2026, 9, 1, 8, 5))
        s.add_all([a, b])
        await s.flush()
        await dedup.resolve(s, b)
        assert a.duplicate_of is None and b.duplicate_of is None
    run(go())
