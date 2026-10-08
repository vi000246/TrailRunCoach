"""Per-source lock, run results, scheduler, auto-on-open, per-source folders,
file deletion and the folder migration (fake HTTP, temp folders only)."""
import datetime as dt
import gzip
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.db.models import SyncState, WorkoutFile
from backend.settings.repository import SettingsRepository
from backend.sync import coros_client, http, purge, runner, scheduler, storage
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import START, FakeCoros, _coros_act, collect, make_session, run


def _factory(s):
    """session_factory that hands out the test session (context manager)."""
    class _Ctx:
        async def __aenter__(self):
            return s

        async def __aexit__(self, *a):
            return False
    return lambda: _Ctx()


async def _coros_synced(s, acts):
    with http.use_transport(httpx.MockTransport(FakeCoros(acts))):
        await coros_client.login("me@example.com", "pw", s, 1)
        return await collect(runner.stream(s, "coros", 1))


# ---- lock + results ----------------------------------------------------------

def test_hold_is_exclusive_per_source():
    with runner.hold("coros"):
        assert runner.is_busy("coros") and not runner.is_busy("tp")
        with pytest.raises(runner.SyncBusy):
            with runner.hold("coros"):
                pass
        with runner.hold("tp"):
            pass
    assert not runner.is_busy("coros")


def test_stream_busy_event_and_last_result(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        ev = await _coros_synced(s, [_coros_act("A1", START)])
        res = await SettingsRepository(s, 1).get("sync.coros.last_result")
        assert res["status"] == "ok" and res["downloaded"] == 1 and res["trigger"] == "manual"
        with runner.hold("coros"):
            busy = await collect(runner.stream(s, "coros", 1))
        assert busy == [busy[0]] and busy[0]["error"] == "SYNC_BUSY"
    run(go())


def test_sse_endpoint_returns_409_when_busy(tmp_path):
    from backend.api.sync import start_coros_sync

    async def go():
        s = await make_session(tmp_path)
        with runner.hold("coros"):
            with pytest.raises(HTTPException) as ei:
                await start_coros_sync(athlete_id=1, since=None, db=s)
        assert ei.value.status_code == 409 and ei.value.detail == "SYNC_BUSY"
    run(go())


def test_synced_files_land_in_per_source_folders(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        await _coros_synced(s, [_coros_act("A1", START)])
        wf = (await s.execute(select(WorkoutFile))).scalar_one()
        p = Path(wf.file_path)
        assert p.parent == _fit_root_in_tmp / "coros" / "2026" and p.exists()
    run(go())


# ---- scheduler ----------------------------------------------------------------

def test_scheduler_runs_once_per_day_after_the_time(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, coros_access_token="t", tp_access_token="t"))
        await s.commit()
        repo = SettingsRepository(s, 1)
        await repo.set("sync.schedule.daily_time", "06:30")
        await repo.set("sync.trainingpeaks.enabled", False)
        await s.commit()
        calls = []
        start = lambda src, aid, trig, f: calls.append((src, trig)) or object()
        tpe = timezone(timedelta(hours=8))
        early = datetime(2026, 9, 30, 6, 0, tzinfo=tpe)
        late = datetime(2026, 9, 30, 6, 31, tzinfo=tpe)
        assert await scheduler.tick(_factory(s), early, start=start) == []
        assert await scheduler.tick(_factory(s), late, start=start) == ["coros"]
        assert await scheduler.tick(_factory(s), late + timedelta(hours=3), start=start) == []
        assert calls == [("coros", "schedule")]
        nxt = datetime(2026, 10, 1, 7, 0, tzinfo=tpe)
        assert await scheduler.tick(_factory(s), nxt, start=start) == ["coros"]
        await repo.set("sync.schedule.daily_time", None)
        assert await scheduler.tick(_factory(s), nxt + timedelta(days=1), start=start) == []
    run(go())


def test_scheduler_skips_busy_and_logged_out(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, coros_access_token="t"))      # TP not logged in
        await s.commit()
        await SettingsRepository(s, 1).set("sync.schedule.daily_time", "00:00")
        await s.commit()
        start = lambda *a: object()
        with runner.hold("coros"):
            assert await scheduler.tick(_factory(s), start=start) == []
    run(go())


def test_schedule_time_validation(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        with pytest.raises(ValueError):
            await SettingsRepository(s, 1).set("sync.schedule.daily_time", "25:00")
    run(go())


# ---- auto on open --------------------------------------------------------------

def test_auto_sync_starts_stale_sources_only(tmp_path, monkeypatch):
    from backend.api.sync import auto_sync
    started = []
    monkeypatch.setattr(runner, "start_background", lambda src, aid=1, trigger="auto", **k:
                        started.append((src, trigger)) or object())

    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, coros_access_token="t", tp_access_token="t",
                        coros_last_sync_at=datetime.utcnow() - timedelta(hours=1),
                        last_sync_at=datetime.utcnow() - timedelta(hours=30)))
        await s.commit()
        # COROS (the 資料來源, the default) is fresh; TP is stale but not in use
        r = await auto_sync(1, s)
        assert r["started"] == [] and r["skipped"] == {"coros": "fresh", "tp": "not_in_use"}
        await SettingsRepository(s, 1).set("sync.primary_source", "trainingpeaks")
        r = await auto_sync(1, s)
        assert r["started"] == ["tp"] and r["skipped"] == {"coros": "not_in_use"}
        assert started == [("tp", "open")]
        await SettingsRepository(s, 1).set("sync.auto_on_open.enabled", False)
        assert (await auto_sync(1, s))["started"] == []
    run(go())


# ---- delete a source's files ---------------------------------------------------

async def _two_sources(s, root):
    """A COROS and a TP row for the same run (dedup group) + a WKO5-like file."""
    files = {}
    for src, sub, dbsrc, t in (("coros", "coros", "coros", START),
                               ("tp", "tp", "trainingpeaks", START + timedelta(seconds=20))):
        d = root / sub / "2026"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{src}.fit"
        f.write_bytes(build_run(t, seconds=60))
        files[src] = f
        wf = WorkoutFile(athlete_id=1, file_path=str(f), file_format="fit", source=dbsrc,
                         start_time_utc=t.replace(tzinfo=None), workout_date=dt.date(2026, 9, 2))
        s.add(wf)
        await s.flush()
    wko5 = root.parent / "WKO5" / "Me" / "2026"
    wko5.mkdir(parents=True, exist_ok=True)
    (wko5 / "x.wko4").write_bytes(b"wko4")
    s.add(SyncState(athlete_id=1, coros_last_sync_at=datetime.utcnow(), last_sync_cursor="2026-09-01",
                    last_sync_at=datetime.utcnow()))
    await s.commit()
    from backend.sync import dedup
    await dedup.rebuild(s, 1)
    await s.commit()
    return files, wko5 / "x.wko4"


@pytest.mark.parametrize("has_wko5", [True, False])
def test_delete_tp_files_keeps_coros_and_wko5_and_rebuilds_dedup(tmp_path, _fit_root_in_tmp, monkeypatch, has_wko5):
    if has_wko5:
        (tmp_path / "wko5athlete").mkdir()
        (tmp_path / "wko5athlete" / "A.wko5athlete").write_bytes(b"")
        monkeypatch.setenv("WKO5_ATHLETE_DIR", str(tmp_path / "wko5athlete"))
    else:
        monkeypatch.delenv("WKO5_ATHLETE_DIR", raising=False)

    async def go():
        s = await make_session(tmp_path, sync__primary_source="trainingpeaks")
        files, wko5_file = await _two_sources(s, _fit_root_in_tmp)
        coros_row = (await s.execute(select(WorkoutFile).where(WorkoutFile.source == "coros"))).scalar_one()
        assert coros_row.duplicate_of is not None               # TP was canonical
        r = await purge.delete_source_files(s, "tp", 1)
        assert r["rows_deleted"] == 1 and r["files_deleted"] == 1 and r["files_refused"] == 0
        assert r["chart_source_switched_to_wko5"] is has_wko5
        assert not files["tp"].exists() and files["coros"].exists() and wko5_file.exists()
        rows = (await s.execute(select(WorkoutFile))).scalars().all()
        assert [x.source for x in rows] == ["coros"] and rows[0].duplicate_of is None   # now canonical
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.last_sync_cursor is None and st.last_sync_at is None and st.coros_last_sync_at is not None
        # without a WKO5 folder the charts stay on the (now empty) 資料來源; never the other source
        assert await SettingsRepository(s, 1).get("charts.data_source") == ("wko5" if has_wko5 else "source")
    run(go())


def test_delete_refuses_paths_outside_the_source_folder(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        outside = tmp_path / "WKO5" / "keep.fit"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"x")
        other = _fit_root_in_tmp / "coros" / "2026" / "c.fit"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"x")
        traversal = _fit_root_in_tmp / "tp" / ".." / "coros" / "2026" / "c.fit"
        for p in (outside, traversal):
            s.add(WorkoutFile(athlete_id=1, file_path=str(p), file_format="fit", source="trainingpeaks"))
        await s.commit()
        r = await purge.delete_source_files(s, "tp", 1)
        assert r["files_refused"] == 2 and r["files_deleted"] == 0 and r["rows_deleted"] == 2
        assert outside.exists() and other.exists()
    run(go())


def test_confined_rejects_symlinks(tmp_path, _fit_root_in_tmp):
    target = tmp_path / "secret.txt"
    target.write_text("x")
    link = _fit_root_in_tmp / "tp" / "2026" / "link.fit"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    with pytest.raises(storage.PathNotConfined):
        storage.confined(link, "tp")
    with pytest.raises(storage.PathNotConfined):
        storage.confined(tmp_path / "secret.txt", "tp")
    with pytest.raises(ValueError):
        storage.source_dir("../x")


def test_delete_while_syncing_is_busy_and_endpoint_rejects_bad_source(tmp_path):
    from backend.api.sync import delete_source_files

    async def go():
        s = await make_session(tmp_path)
        with runner.hold("coros"):
            with pytest.raises(HTTPException) as ei:
                await delete_source_files("coros", 1, None, None, s)
        assert ei.value.status_code == 409
        with pytest.raises(HTTPException) as ei:
            await delete_source_files("wko5", 1, None, None, s)
        assert ei.value.status_code == 400
    run(go())


def test_delete_date_range_only(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        d = _fit_root_in_tmp / "coros" / "2026"
        d.mkdir(parents=True)
        for day in (1, 15):
            f = d / f"{day}.fit"
            f.write_bytes(b"12345")
            s.add(WorkoutFile(athlete_id=1, file_path=str(f), file_format="fit", source="coros",
                              workout_date=dt.date(2026, 9, day)))
        await s.commit()
        r = await purge.delete_source_files(s, "coros", 1, dt.date(2026, 9, 10), dt.date(2026, 9, 30))
        assert r["rows_deleted"] == 1 and r["bytes_deleted"] == 5
        assert (d / "1.fit").exists() and not (d / "15.fit").exists()
        stats = await purge.source_stats(s, "coros", 1)
        assert stats["files"] == 1 and stats["rows"] == 1 and stats["date_from"] == "2026-09-01"
    run(go())


# ---- settings / sources API -------------------------------------------------------

def test_sources_endpoint_and_new_settings(tmp_path, _fit_root_in_tmp):
    from backend.api.sync import SyncSettingsBody, put_sync_settings, sync_sources

    async def go():
        s = await make_session(tmp_path)
        await _coros_synced(s, [_coros_act("A1", START)])
        out = await sync_sources(1, s)
        c = out["coros"]
        assert c["logged_in"] and c["enabled"] and not c["busy"]
        assert c["stats"]["files"] == 1 and c["stats"]["rows"] == 1 and c["stats"]["date_from"] == "2026-09-02"
        assert c["last_result"]["downloaded"] == 1 and c["last_sync_at"]
        assert out["tp"]["logged_in"] is False and out["tp"]["stats"]["files"] == 0
        r = await put_sync_settings(SyncSettingsBody(daily_sync_time="05:45", auto_on_open=False,
                                                     auto_on_open_hours=12, chart_data_source="wko5"), 1, s)
        assert r["daily_sync_time"] == "05:45" and r["auto_on_open"] is False
        assert r["auto_on_open_hours"] == 12 and r["chart_data_source"] == "wko5"
        assert r["tp_client_file_exists"] is False
        with pytest.raises(HTTPException):
            await put_sync_settings(SyncSettingsBody(chart_data_source="garmin"), 1, s)
        # workout map defaults
        assert r["map_basemap"] == {"tw": "rudy", "intl": "osm"}[r["region"]] and r["map_overlays"] == []   # 地區 default
        r = await put_sync_settings(SyncSettingsBody(map_basemap="nlsc-emap", map_overlays=["contour"]), 1, s)
        assert r["map_basemap"] == "nlsc-emap" and r["map_overlays"] == ["contour"]
        with pytest.raises(HTTPException):
            await put_sync_settings(SyncSettingsBody(map_overlays=["bing"]), 1, s)
    run(go())


def test_tp_login_method_preference(tmp_path, tp_creds):
    from backend.sync import tp_client
    from backend.tests.test_tp_oauth import OAuthFake

    async def go():
        s = await make_session(tmp_path)
        fake = OAuthFake()
        with http.use_transport(httpx.MockTransport(fake)):
            r = await tp_client.login_password("u@example.com", "pw", s, 1, prefer="web")
            assert r["method"] == "web" and fake.token_bodies == []
            r = await tp_client.login_password("u@example.com", "pw", s, 1, prefer="oauth")
            assert r["method"] == "oauth" and fake.posted is not None
        fake2 = OAuthFake(grant=401)
        with http.use_transport(httpx.MockTransport(fake2)):
            with pytest.raises(tp_client.TpLoginError):
                await tp_client.login_password("u@example.com", "pw", s, 1, prefer="oauth")
            assert fake2.posted is None                      # no silent web fallback
    run(go())


# ---- migration ------------------------------------------------------------------

def test_migrate_fit_folders_dry_run_apply_idempotent(tmp_path, _fit_root_in_tmp):
    from backend.scripts.migrate_fit_folders import migrate

    async def go():
        s = await make_session(tmp_path)
        old_tp = _fit_root_in_tmp / "athlete_1" / "2026"
        old_co = tmp_path / "fits" / "athlete_1" / "2026"
        for d in (old_tp, old_co):
            d.mkdir(parents=True)
        (old_tp / "a.fit").write_bytes(b"tp")
        (old_co / "b.fit").write_bytes(b"co")
        (old_co / "stray.fit").write_bytes(b"?")
        s.add(WorkoutFile(athlete_id=1, file_path=str(old_tp / "a.fit"), file_format="fit",
                          source="trainingpeaks", workout_date=dt.date(2026, 9, 2)))
        s.add(WorkoutFile(athlete_id=1, file_path=str(old_co / "b.fit"), file_format="fit",
                          source="coros", workout_date=dt.date(2026, 9, 2)))
        await s.commit()
        legacy = {"coros": [tmp_path / "fits"], "tp": [_fit_root_in_tmp / "athlete_1"]}
        dry = await migrate(s, apply=False, legacy_dirs=legacy)
        assert dry["tp"]["moved"] == 1 and dry["coros"]["moved"] == 1 and dry["coros"]["untracked"] == 1
        assert (old_tp / "a.fit").exists()                     # dry run moved nothing
        done = await migrate(s, apply=True, legacy_dirs=legacy)
        assert done["tp"]["moved"] == 1 and done["coros"]["moved"] == 1
        assert (_fit_root_in_tmp / "tp" / "2026" / "a.fit").exists()
        assert (_fit_root_in_tmp / "coros" / "2026" / "b.fit").exists()
        assert not (_fit_root_in_tmp / "athlete_1").exists()   # emptied legacy folder removed
        assert (old_co / "stray.fit").exists()                 # untracked files stay
        paths = sorted(r.file_path for r in (await s.execute(select(WorkoutFile))).scalars())
        assert all(str(_fit_root_in_tmp) in p for p in paths)
        again = await migrate(s, apply=True, legacy_dirs=legacy)
        assert again["tp"]["already"] == 1 and again["coros"]["already"] == 1
        assert again["tp"]["moved"] == 0 and again["coros"]["moved"] == 0
    run(go())


def test_primary_endpoint_reports_the_source_in_use(tmp_path, _fit_root_in_tmp):
    """GET /sync/primary (課表 › 從 COROS 抓活動): source in use, login, enabled, busy."""
    from backend.api.sync import sync_primary

    async def go():
        s = await make_session(tmp_path)
        out = await sync_primary(1, s)
        assert out == {"source": "coros", "label": "COROS", "logged_in": False, "login": "logged_out",
                       "enabled": True, "busy": False}
        await _coros_synced(s, [_coros_act("A1", START)])
        with runner.hold("coros"):
            out = await sync_primary(1, s)
        assert out["logged_in"] and out["busy"]
        repo = SettingsRepository(s, 1)
        await repo.set("sync.primary_source", "trainingpeaks")
        await repo.set("sync.trainingpeaks.enabled", False)
        out = await sync_primary(1, s)
        assert out == {"source": "tp", "label": "TrainingPeaks", "logged_in": False, "login": "logged_out",
                       "enabled": False, "busy": False}
    run(go())
