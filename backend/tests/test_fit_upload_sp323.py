"""SP-323: 手動匯入 FIT — the files a person picks in the browser (or a whole folder) become
activities of the current tenant: stored as hash-named objects (SP-308) in the 資料來源's folder
(the charts read only that folder), the same file twice is one activity (sha256), the same run
already synced from COROS — or synced later — is counted once (sync/dedup.py, SP-344), a bad file
never fails the batch, sizes and counts are capped, and only a tenant with `upload.fit` may do it.
Synthetic FITs only (fit_builder); every tenant in tmp."""
from __future__ import annotations

import asyncio
import gzip
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import tenancy
from backend.db.models import Athlete, AthleteSettings, Base, FitObject, WorkoutFile
from backend.files import fitstore
from backend.settings.repository import SettingsRepository
from backend.sync import coros_client, dedup, http, runner, storage
from backend.sync import upload as UP
from backend.demo.fitwrite import _wrap
from backend.tests.fit_builder import ENUM, UINT16, UINT32, _data, _definition, _ts, build_run
from backend.tests.test_sync_e2e import FakeCoros, collect, make_session, run

START = datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)     # 06:30 the next day in Taipei


def _run(day=0, **kw) -> bytes:
    return build_run(START + timedelta(days=day), seconds=600, hr=140, speed_m_s=3.0, **kw)


def _in(name, data) -> UP.Incoming:
    return UP.Incoming(name, data, len(data))


def _monitoring() -> bytes:
    """A FIT that is not an activity (file_id type monitoring_b = 32, no records, no session)."""
    fid = [(0, ENUM), (1, UINT16), (4, UINT32)]
    return _wrap(_definition(0, 0, fid) + _data(0, fid, [32, 255, _ts(START)]))


async def _rows(s) -> list[WorkoutFile]:
    return list((await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars())


def _status(r) -> tuple:
    return r["added"], r["duplicate"], r["failed"], r["skipped"]


def _coros_act(label, raw, day=0):
    return {"labelId": label, "date": int((START + timedelta(days=day)).strftime("%Y%m%d")), "sportType": 100,
            "fitUrl": f"https://files.example/fit/{label}", "_bytes": raw}


async def _coros_sync(s, acts):
    with http.use_transport(httpx.MockTransport(FakeCoros(acts))):
        await coros_client.login("me@example.com", "pw", s, 1)
        return await collect(coros_client.sync_workouts(s, 1))


# ---------------------------------------------------------------------------- the import itself

def test_new_files_become_upload_activities_in_the_data_source_folder(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        r = await UP.import_files(s, 1, [_in("a.fit", _run(0)), _in("Morning Run.FIT", _run(1))])
        assert _status(r) == (2, 0, 0, 0)
        rows = await _rows(s)
        assert [x.source for x in rows] == [UP.SOURCE, UP.SOURCE] == ["upload", "upload"]
        assert all(x.duplicate_of is None and x.file_format == "fit" for x in rows)
        # the 資料來源's folder (COROS until another is chosen): the chart Dataset reads only that one
        assert all(x.file_path.startswith("coros/objects/") and fitstore.is_object_key(x.file_path) for x in rows)
        assert rows[0].workout_date == date(2026, 9, 2) and rows[0].sport == "running"
        objs = (await s.execute(select(FitObject))).scalars().all()
        assert len(objs) == 2 and {o.source for o in objs} == {"coros"}     # the folder's: later stores find it
        assert len([p for p in _fit_root_in_tmp.rglob("*.fit.gz")]) == 2
        it = r["items"][0]
        assert it["name"] == "a.fit" and it["status"] == "added" and it["date"] == "2026-09-02"
        assert it["reason"] is None and it["sport"] == "running"
    run(go())


def test_a_gzipped_fit_is_taken_too(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        r = await UP.import_files(s, 1, [_in("export.fit.gz", gzip.compress(_run(0)))])
        assert _status(r) == (1, 0, 0, 0)
        (row,) = await _rows(s)
        assert fitstore.read_bytes(row.file_path) == _run(0)             # stored as the FIT, named by its hash
    run(go())


def test_the_same_file_twice_is_one_activity(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        a = _run(0)
        r1 = await UP.import_files(s, 1, [_in("a.fit", a), _in("copy of a.fit", a)])
        r2 = await UP.import_files(s, 1, [_in("a.fit", a)])                   # the same folder again later
        assert _status(r1) == (1, 1, 0, 0) and _status(r2) == (0, 1, 0, 0)
        dup = r1["items"][1]
        assert dup["status"] == "duplicate" and dup["reason"] == "same_file" and dup["date"] == "2026-09-02"
        assert len(await _rows(s)) == 1
        assert len(list(_fit_root_in_tmp.rglob("*.fit.gz"))) == 1
    run(go())


def test_a_run_coros_already_synced_is_counted_once(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _coros_sync(s, [_coros_act("111", _run(0, power=240))])        # the watch's own file
        # the same run exported from another platform: other bytes, the same start and distance
        r = await UP.import_files(s, 1, [_in("garmin_export.fit", _run(0)), _in("other_day.fit", _run(3))])
        assert _status(r) == (1, 1, 0, 0)
        it = r["items"][0]
        assert it["status"] == "duplicate" and it["reason"] == "same_activity" and it["of"] == "coros"
        coros, up, other = await _rows(s)
        assert coros.source == "coros" and coros.duplicate_of is None
        assert up.source == "upload" and up.duplicate_of == coros.id                # kept as a copy, counted once
        assert other.duplicate_of is None
        counted = (await s.execute(select(WorkoutFile.id).where(await dedup.in_use(s, 1)))).scalars().all()
        assert sorted(counted) == [coros.id, other.id]
    run(go())


def test_the_imported_count_leaves_out_the_copies_kept_of_another_sources_run(tmp_path):
    """SP-456: 「已匯入 n 筆」 beside the buttons is the activities the upload added — a file kept
    only as a copy of a COROS run (counted once, under COROS) is not one of them, so the count
    matches the 「新增」 of the result."""
    async def go():
        s = await make_session(tmp_path)
        await _coros_sync(s, [_coros_act("111", _run(0, power=240))])
        r = await UP.import_files(s, 1, [_in("same_as_coros.fit", _run(0)), _in("b.fit", _run(3)),
                                         _in("c.fit", _run(5))])
        assert _status(r) == (2, 1, 0, 0)
        st = await UP.stats(s, 1)
        assert st["rows"] == r["added"] == 2
        assert (st["date_from"], st["date_to"]) == ("2026-09-05", "2026-09-07")   # the copy's day is COROS's
        # a COROS sync that later takes over an uploaded run takes it out of the count too
        await _coros_sync(s, [_coros_act("111", _run(0, power=240)), _coros_act("333", _run(3, power=240), 3)])
        assert (await UP.stats(s, 1))["rows"] == 1
    run(go())


def test_a_later_coros_sync_of_the_same_run_is_not_a_second_activity(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        a, b = _run(0), _run(1)
        r = await UP.import_files(s, 1, [_in("a.fit", a), _in("b.fit", b)])
        assert _status(r) == (2, 0, 0, 0)
        # COROS syncs the same two runs: one with its own bytes, one byte for byte the uploaded file
        ev = await _coros_sync(s, [_coros_act("111", _run(0, power=240)), _coros_act("222", b, 1)])
        assert [e["activity_id"] for e in ev if e.get("reason") == "same_content"] == ["222"]
        rows = await _rows(s)
        assert len(rows) == 3
        up_a, up_b, coros_a = rows
        assert coros_a.source == "coros" and coros_a.duplicate_of is None              # the 資料來源's copy counts
        assert up_a.duplicate_of == coros_a.id and up_b.duplicate_of is None           # b: stored and recorded once
        counted = (await s.execute(select(WorkoutFile.id).where(await dedup.in_use(s, 1)))).scalars().all()
        assert sorted(counted) == [up_b.id, coros_a.id]
    run(go())


def test_bad_files_are_listed_and_never_fail_the_batch(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        good = _run(0)
        files = [_in("notes.txt", b"hello"), _in("photo.jpg", b"\xff\xd8\xff"),            # not FIT: skipped
                 _in("broken.fit", b"this is not a fit file at all"),                    # .fit, not a FIT
                 _in("cut.fit", good[:300]),                                             # truncated
                 _in("steps.fit", _monitoring()),                                        # a FIT, not an activity
                 _in("empty.fit", b""),
                 _in("bad.fit.gz", b"\x1f\x8b\x08garbage"),
                 _in("good.fit", good)]
        r = await UP.import_files(s, 1, files)
        assert _status(r) == (1, 0, 5, 2)
        why = {i["name"]: (i["status"], i["reason"]) for i in r["items"]}
        assert why == {"notes.txt": ("skipped", "not_fit"), "photo.jpg": ("skipped", "not_fit"),
                       "broken.fit": ("failed", "not_fit"), "cut.fit": ("failed", "unreadable"),
                       "steps.fit": ("failed", "not_activity"), "empty.fit": ("failed", "empty"),
                       "bad.fit.gz": ("failed", "not_fit"), "good.fit": ("added", None)}
        assert len(await _rows(s)) == 1
        assert len(list(_fit_root_in_tmp.rglob("*.fit.gz"))) == 1                       # nothing stored for the bad ones
    run(go())


def test_a_database_error_on_one_file_leaves_the_others_and_no_stray_object(tmp_path, monkeypatch,
                                                                            _fit_root_in_tmp):
    from backend.files import file_service as FS
    real = FS._import_one_file
    calls = []

    async def flaky(db, athlete_id, path, **kw):
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        return await real(db, athlete_id, path, **kw)
    monkeypatch.setattr(FS, "_import_one_file", flaky)

    async def go():
        s = await make_session(tmp_path)
        r = await UP.import_files(s, 1, [_in("a.fit", _run(0)), _in("b.fit", _run(1))])
        assert _status(r) == (1, 0, 1, 0) and r["items"][0]["reason"] == "error"
        (row,) = await _rows(s)
        assert row.start_time_utc.date() == date(2026, 9, 2)
        # the first file's object was written before the error: removed again (the charts would
        # read a file without a row as an activity)
        assert [p.name for p in _fit_root_in_tmp.rglob("*.fit.gz")] == [Path(row.file_path).name]
    run(go())


def test_size_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(UP, "MAX_FILE_BYTES", 1000)

    async def go():
        s = await make_session(tmp_path)
        big = _run(0)
        assert len(big) > 1000
        r = await UP.import_files(s, 1, [UP.Incoming("big.fit", None, len(big)),          # not read: over the cap
                                         _in("bomb.fit.gz", gzip.compress(b"\0" * 5000))])
        assert _status(r) == (0, 0, 2, 0)
        assert [i["reason"] for i in r["items"]] == ["too_large", "too_large"]
        assert await _rows(s) == []
    run(go())
    lim = UP.limits()
    assert lim["max_files"] == UP.MAX_FILES and lim["max_request_bytes"] == UP.MAX_REQUEST_BYTES
    assert lim["max_select"] == UP.MAX_SELECT and lim["exts"][:2] == [".fit", ".fit.gz"]
    assert lim["exts"][2:] == [".tcx", ".tcx.gz", ".gpx", ".gpx.gz",              # SP-349: turned into FIT
                               ".pwx", ".pwx.gz"]                                 # SP-833: TrainingPeaks / WKO


def test_defaults_are_sane():
    """FIT files are usually well under 5 MB (the owner's median 93 KB, SP-319 §1); a multi-day trip
    recorded every second is the big case. A batch request stays well under the proxies' limits."""
    assert 10 * 2**20 <= UP.MAX_FILE_BYTES <= 50 * 2**20
    assert UP.MAX_FILE_BYTES <= UP.MAX_REQUEST_BYTES <= 200 * 2**20
    assert 10 <= UP.MAX_FILES <= 200 and UP.MAX_SELECT >= 1000
    # one request's parsing (~2 s per MB here, ~8.5× on the NAS) stays well inside ~100 s
    assert 2**20 <= UP.BATCH_BYTES <= 4 * 2**20 < UP.MAX_REQUEST_BYTES


def test_the_chart_dataset_shows_the_upload_once_and_says_where_it_came_from(tmp_path):
    from backend.engine.wko5expr import fitdataset as FD
    from backend.engine.wko5expr.config import EngineConfig
    dbf = tmp_path / "app.db"

    async def go():
        eng = create_async_engine(f"sqlite+aiosqlite:///{dbf}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        s = async_sessionmaker(eng, expire_on_commit=False)()
        s.add(Athlete(id=1, name="tester", data_dir=""))
        s.add(AthleteSettings(athlete_id=1, effective_date=date(2020, 1, 1), lthr=170))
        await SettingsRepository(s, 1).set("athlete.timezone", "Asia/Taipei")
        await s.commit()
        await _coros_sync(s, [_coros_act("111", _run(0, power=240))])
        await UP.import_files(s, 1, [_in("same_run.fit", _run(0)), _in("old_run.fit", _run(-400))])
        await s.close()
        await eng.dispose()
    run(go())
    from zoneinfo import ZoneInfo
    ds = FD.FitFolderDataset(storage.source_dir("coros"), config=EngineConfig(parity=False), today=date(2026, 9, 30),
                             source="coros", tz=ZoneInfo("Asia/Taipei"), classifications=FD.load_classifications(dbf),
                             athlete_settings=[{"effective_date": "2020-01-01", "weight_kg": 60}],
                             estimate_thresholds=False)
    starts = sorted(w.entry.start.date() for w in ds.workouts)
    assert starts == [date(2025, 7, 29), date(2026, 9, 2)]                  # the COROS run once, the old upload
    origin = {w.entry.start.date(): ds.file_origin(w) for w in ds.workouts}
    assert origin == {date(2025, 7, 29): "upload", date(2026, 9, 2): "coros"}


# ---------------------------------------------------------------------------- 資料來源 switch

def test_switching_the_data_source_moves_the_imported_files_along(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        await UP.import_files(s, 1, [_in("a.fit", _run(0)), _in("b.fit", _run(1))])
        before = [r.file_path for r in await _rows(s)]
        await SettingsRepository(s, 1).set("sync.primary_source", "garmin")
        moved = await UP.rehome(s, 1)
        await s.commit()
        assert moved == {"moved": 2, "folder": "garmin"}
        rows = await _rows(s)
        assert all(r.file_path.startswith("garmin/objects/") for r in rows)
        assert [Path(r.file_path).name for r in rows] == [Path(p).name for p in before]
        assert all(fitstore.read_bytes(r.file_path) for r in rows)
        assert not list((_fit_root_in_tmp / "coros").rglob("*.fit.gz"))           # nothing left behind
        assert {o.source for o in (await s.execute(select(FitObject))).scalars()} == {"garmin"}
        assert await UP.rehome(s, 1) == {"moved": 0, "folder": "garmin"}            # idempotent
        # a later import of the same bytes still finds them (no second row)
        r = await UP.import_files(s, 1, [_in("a.fit", _run(0))])
        assert _status(r) == (0, 1, 0, 0)
    run(go())


def test_the_settings_endpoint_moves_them_when_the_source_changes(tmp_path, monkeypatch):
    from backend.api import sync as SYNC

    async def light(repo):                       # the page's other fields (power source, Dataset …)
        return {}
    monkeypatch.setattr(SYNC, "_sync_settings", light)

    async def go():
        s = await make_session(tmp_path)
        await UP.import_files(s, 1, [_in("a.fit", _run(0))])
        out = await SYNC.put_sync_settings(SYNC.SyncSettingsBody(timezone="Asia/Taipei"), 1, s)
        assert out["upload"] is None and (await _rows(s))[0].file_path.startswith("coros/")
        out = await SYNC.put_sync_settings(SYNC.SyncSettingsBody(primary_source="trainingpeaks"), 1, s)
        assert out["upload"] == {"moved": 1, "folder": "tp"}
        (row,) = await _rows(s)
        assert row.file_path.startswith("tp/objects/") and row.duplicate_of is None
    run(go())


# ---------------------------------------------------------------------------- after the import

def test_after_import_runs_the_post_sync_steps_in_order(tmp_path, monkeypatch):
    from backend.api import wko5views
    from backend.engine import calibrate, localtime, plan_auto
    calls = []

    async def tz(db, athlete_id=1):
        calls.append(("tz", athlete_id))
    monkeypatch.setattr(localtime, "refresh_from_fits", tz)
    monkeypatch.setattr(wko5views, "warm_up", lambda reason: calls.append(("warm", reason)))
    monkeypatch.setattr(plan_auto, "after_sync", lambda src, res: calls.append(("plan", src, res["downloaded"])))
    monkeypatch.setattr(calibrate, "after_sync", lambda src, res, aid: calls.append(("calib", src, aid)))

    async def go():
        s = await make_session(tmp_path)
        await runner.after_import(s, "upload", {"status": "ok", "downloaded": 3, "trigger": "upload"}, 1)
        assert calls == [("tz", 1), ("warm", "sync-upload"), ("plan", "upload", 3), ("calib", "upload", 1)]
        calls.clear()
        await runner.after_import(s, "upload", {"status": "ok", "downloaded": 0, "trigger": "upload"}, 1)
        assert calls == [("plan", "upload", 0), ("calib", "upload", 1)]          # nothing new: no rebuild
    run(go())


def test_a_sync_run_goes_through_the_same_after_import(tmp_path, monkeypatch):
    got = []

    async def fake(db, source, result, athlete_id=1):
        got.append((source, result["downloaded"], result["status"]))
    monkeypatch.setattr(runner, "after_import", fake)

    async def client(db):
        yield {"status": "downloaded", "workout_id": 1}
        yield {"status": "complete", "total_downloaded": 1, "total_checked": 1, "errors": []}

    async def go():
        s = await make_session(tmp_path)
        await collect(runner.stream(s, "tp", 1, client=client))
    run(go())
    assert got == [("tp", 1, "ok")]


# ---------------------------------------------------------------------------- the API

class _Bind:
    """What the sign-in middleware will do (SP-58): the request's tenant, here from a test header."""

    def __init__(self, app, tenants: dict):
        self.app, self.tenants = app, tenants

    async def __call__(self, scope, receive, send):
        name = dict(scope.get("headers") or []).get(b"x-test-tenant", b"").decode() if scope["type"] == "http" else ""
        t = self.tenants.get(name)
        if t is None:
            return await self.app(scope, receive, send)
        from backend.accounts import tenants as TN
        await TN.ensure_ready(t)
        tok = tenancy.set_current(t)
        try:
            await self.app(scope, receive, send)
        finally:
            tenancy.reset(tok)


def _user(tmp_path, tid, caps=tenancy.USER_CAPS) -> tenancy.Tenant:
    d = tmp_path / tid
    return tenancy.Tenant(id=tid, kind=tenancy.USER, root=d, shared=d, caps=caps)


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.accounts import tenants as TN
    from backend.main import build_app
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setattr(storage, "FIT_ROOT", None)               # each tenant's own fit/ folder
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    after = []

    async def fake_after(db, source, result, athlete_id=1):
        after.append((tenancy.current().id, source, dict(result)))
    monkeypatch.setattr(runner, "after_import", fake_after)
    tenants = {"A": _user(tmp_path, "uA"), "B": _user(tmp_path, "uB"),
               "nocap": _user(tmp_path, "uN", tenancy.USER_CAPS - {"upload.fit"})}
    with TestClient(_Bind(build_app(demo=False), tenants), raise_server_exceptions=False) as c:
        c.tenants, c.after = tenants, after
        yield c


URL = "/api/v1/sync/upload/fit"


def _as(who):
    return {"X-Test-Tenant": who}


def _files(*pairs):
    return [("files", (name, data, "application/octet-stream")) for name, data in pairs]


def _db_rows(t: tenancy.Tenant) -> list[tuple]:
    p = t.root / "wko5coach.db"
    if not p.exists():
        return []
    con = sqlite3.connect(p)
    try:
        return con.execute("SELECT source, file_path, duplicate_of FROM workout_files ORDER BY id").fetchall()
    finally:
        con.close()


def test_upload_needs_the_upload_fit_capability(api):
    r = api.post(URL, files=_files(("a.fit", _run(0))), headers=_as("nocap"))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "UPLOAD_FIT_FORBIDDEN"
    assert _db_rows(api.tenants["nocap"]) == [] and not (api.tenants["nocap"].shared / "fit").exists()
    assert api.get(URL, headers=_as("nocap")).status_code == 403
    assert api.after == []


def test_upload_writes_only_into_the_current_tenant(api):
    r = api.post(URL, files=_files(("a.fit", _run(0)), ("b.fit", _run(1)), ("readme.md", b"# hi")),
                 data={"last": "1"}, headers=_as("A"))
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["added"], j["duplicate"], j["failed"], j["skipped"]) == (2, 0, 0, 1)
    a, b = api.tenants["A"], api.tenants["B"]
    assert [row[0] for row in _db_rows(a)] == ["upload", "upload"]
    assert len(list((a.shared / "fit").rglob("*.fit.gz"))) == 2
    assert _db_rows(b) == [] and not (b.shared / "fit").exists()
    assert not (tenancy.home_root() / "fit").exists()                         # not the owner's either
    assert api.get(URL, headers=_as("A")).json()["rows"] == 2
    assert api.get(URL, headers=_as("B")).json()["rows"] == 0
    # the post-import steps (Dataset warm-up, overview, plan inputs, auto plan) in A's tenant
    assert api.after == [("uA", "upload", {"status": "ok", "downloaded": 2, "trigger": "upload"})]


def test_the_same_file_again_through_the_api_is_a_duplicate(api):
    api.post(URL, files=_files(("a.fit", _run(0))), headers=_as("A"))
    j = api.post(URL, files=_files(("again.fit", _run(0))), headers=_as("A")).json()
    assert (j["added"], j["duplicate"]) == (0, 1) and j["items"][0]["reason"] == "same_file"
    assert len(_db_rows(api.tenants["A"])) == 1
    assert [x[2]["downloaded"] for x in api.after] == [1]                     # nothing new the second time


def test_a_folder_in_batches_runs_the_post_import_steps_once_at_the_end(api):
    j1 = api.post(URL, files=_files(("2026/a.fit", _run(0))), data={"last": "0"}, headers=_as("A")).json()
    assert j1["added"] == 1 and api.after == []                                # more batches to come
    j2 = api.post(URL, files=_files(("2026/a.fit", _run(0))), data={"last": "1", "earlier_added": "1"},
                  headers=_as("A")).json()
    assert j2["added"] == 0 and j2["duplicate"] == 1
    assert api.after == [("uA", "upload", {"status": "ok", "downloaded": 1, "trigger": "upload"})]


def test_too_many_files_or_too_big_a_request_is_refused(api, monkeypatch):
    monkeypatch.setattr(UP, "MAX_FILES", 2)
    r = api.post(URL, files=_files(("a.fit", _run(0)), ("b.fit", _run(1)), ("c.fit", _run(2))), headers=_as("A"))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "UPLOAD_TOO_MANY"
    monkeypatch.setattr(UP, "MAX_REQUEST_BYTES", 1000)
    r = api.post(URL, files=_files(("a.fit", _run(0))), headers=_as("A"))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"
    assert _db_rows(api.tenants["A"]) == []
    r = api.post(URL, data={"last": "1"}, headers=_as("A"))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "UPLOAD_NO_FILES"


def test_a_file_over_the_size_cap_fails_alone(api, monkeypatch):
    monkeypatch.setattr(UP, "MAX_FILE_BYTES", 2000)
    small = build_run(START, seconds=20)
    assert len(small) < 2000 < len(_run(1))
    j = api.post(URL, files=_files(("big.fit", _run(1)), ("small.fit", small)), headers=_as("A")).json()
    assert (j["added"], j["failed"]) == (1, 1) and j["items"][0]["reason"] == "too_large"


def test_two_uploads_at_once_in_one_tenant_are_refused(api):
    with UP.claim("uA"):
        r = api.post(URL, files=_files(("a.fit", _run(0))), headers=_as("A"))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "UPLOAD_BUSY"
        assert api.post(URL, files=_files(("a.fit", _run(0))), headers=_as("B")).status_code == 200
    assert api.post(URL, files=_files(("a.fit", _run(0))), headers=_as("A")).status_code == 200


def _paths(app) -> set:
    stack, out = list(app.routes), set()
    while stack:
        r = stack.pop(0)
        sub = getattr(r, "original_router", None)
        if sub is not None:
            stack.extend(sub.routes)
        else:
            out.add(getattr(r, "path", ""))
    return out


def test_the_demo_never_takes_an_upload():
    from backend import tenancy_mw as MW
    from backend.main import build_app
    assert "upload.fit" not in tenancy.DEMO_CAPS and "upload.fit" in tenancy.USER_CAPS
    assert not MW.allowed_write("POST", URL)                                  # the demo middleware: 403
    assert URL not in _paths(build_app(demo=True))                           # nor mounted there at all
    assert URL in _paths(build_app(demo=False))
