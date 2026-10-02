"""Sync settings API, source enable flags, start-time backfill, settings repo."""
import json
from datetime import date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.db.models import WorkoutFile
from backend.settings.repository import SettingsRepository, UnknownSetting, resolve_tz
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import START, make_session, run


def test_repository_defaults_validation_and_unknown_keys(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        repo = SettingsRepository(s, 1)
        assert await repo.get("sync.primary_source") is None          # migrated on first read (sync/primary.py)
        assert await repo.get("sync.coros.enabled") is True
        with pytest.raises(ValueError):
            await repo.set("sync.primary_source", "garmin")
        with pytest.raises(ValueError):
            await repo.set("athlete.timezone", "Mars/Olympus")
        with pytest.raises(UnknownSetting):
            await repo.get("nope")
        # per user: user 2 doesn't see user 1's values
        assert await SettingsRepository(s, 2).get("athlete.timezone") is None
        assert (await repo.all())["athlete.timezone"] == "Asia/Taipei"
    run(go())


def test_resolve_tz_env_fallback(monkeypatch):
    monkeypatch.setenv("WKO5COACH_TZ", "Europe/Paris")
    assert str(resolve_tz(None)) == "Europe/Paris"
    assert str(resolve_tz("Asia/Tokyo")) == "Asia/Tokyo"


def test_put_settings_switches_primary_and_rebuilds(tmp_path):
    from backend.api.sync import SyncSettingsBody, get_sync_settings, put_sync_settings

    async def go():
        s = await make_session(tmp_path)
        a = WorkoutFile(athlete_id=1, file_path="a", file_format="fit", source="coros",
                        start_time_utc=datetime(2026, 9, 1, 8, 0))
        b = WorkoutFile(athlete_id=1, file_path="b", file_format="fit", source="trainingpeaks",
                        start_time_utc=datetime(2026, 9, 1, 8, 1))
        s.add_all([a, b])
        await s.commit()
        r = await put_sync_settings(SyncSettingsBody(primary_source="trainingpeaks"), 1, s)
        assert r["primary_source"] == "trainingpeaks" and r["dedup"]["duplicates"] == 1
        assert a.duplicate_of == b.id
        r = await put_sync_settings(SyncSettingsBody(coros_enabled=False), 1, s)
        assert r["coros_enabled"] is False and r["dedup"] is None
        assert (await get_sync_settings(1, s))["primary_source"] == "trainingpeaks"
        with pytest.raises(HTTPException):
            await put_sync_settings(SyncSettingsBody(timezone="Nowhere/Land"), 1, s)
    run(go())


def test_disabled_source_does_not_sync(tmp_path):
    from backend.api.sync import start_coros_sync

    async def go():
        s = await make_session(tmp_path, sync__coros__enabled=False)
        resp = await start_coros_sync(athlete_id=1, since=None, db=s)
        events = [e async for e in resp.body_iterator]
        assert json.loads(events[0]["data"])["error"] == "SYNC_DISABLED"
    run(go())


def test_backfill_sets_utc_and_local_date(tmp_path):
    from backend.scripts.backfill_start_time import backfill

    async def go():
        s = await make_session(tmp_path)
        p = tmp_path / "old.fit"
        p.write_bytes(build_run(START, seconds=120))
        s.add(WorkoutFile(athlete_id=1, file_path=str(p), file_format="fit", source="coros",
                          workout_date=date(2026, 9, 1)))    # old import: the UTC date
        await s.commit()
        dry = await backfill(s, 1, apply=False)
        assert dry["fixed"] == 1 and dry["date_changed"] == 1
        wf = (await s.execute(select(WorkoutFile))).scalar_one()
        assert wf.start_time_utc is None                    # dry run changed nothing
        done = await backfill(s, 1, apply=True)
        assert done["fixed"] == 1
        wf = (await s.execute(select(WorkoutFile))).scalar_one()
        assert wf.start_time_utc == datetime(2026, 9, 1, 22, 30)
        assert wf.workout_date == date(2026, 9, 2)
    run(go())
