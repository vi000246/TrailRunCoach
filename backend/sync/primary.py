"""
資料來源 (2026-10-02, owner decision): ONE synced source is used at a time.

Setting `sync.primary_source`: "coros" | "trainingpeaks". The chosen source is
the only one read anywhere — the chart Dataset reads only its folder
(wko5expr/datasource.py), automatic syncs only sync it (sync/runner.py
auto_plan), the CP-test / race-power scan reads only its files
(racepower/cptest.py), the DB totals count only its rows (sync/dedup.py
in_use_clause). Nothing is merged or back-filled from the other source; its
files, rows and saved login stay where they are, unused, until the user
switches back.

Migration: an old value ("auto", None, "local") is replaced on first read
by the source the old 自動 would have picked — the one with the newest
activity, COROS on a tie or without data (`choose_initial`). Activity keys
are start times (engine/activity_key.py), so tags / RPE / pain / pack /
plan matches survive a switch.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

SETTING_KEY = "sync.primary_source"
DB_SOURCES = ("coros", "trainingpeaks")          # workout_files.source / settings names
FOLDER = {"coros": "coros", "trainingpeaks": "tp"}  # -> sync/storage.py folder names
DB_OF = {v: k for k, v in FOLDER.items()}
LABELS = {"coros": "COROS", "trainingpeaks": "TrainingPeaks"}
DEFAULT = "coros"


def to_db(name: Optional[str]) -> Optional[str]:
    """A folder ("tp") or DB ("trainingpeaks") name -> the DB name."""
    if name is None:
        return None
    return DB_OF.get(name, name)


def normalize(value) -> Optional[str]:
    """The stored setting -> "coros" | "trainingpeaks", None for an old /
    unknown value that still has to be migrated."""
    v = to_db(value) if isinstance(value, str) else None
    return v if v in DB_SOURCES else None


def effective(value) -> str:
    """The source in effect for a stored value (DEFAULT until migrated)."""
    return normalize(value) or DEFAULT


def folder(value) -> str:
    """The folder ("coros" / "tp") of the source in effect."""
    return FOLDER[effective(value)]


def other_folder(value) -> str:
    """The folder of the source NOT in use."""
    f = folder(value)
    return "tp" if f == "coros" else "coros"


def choose_initial(latest: dict) -> str:
    """Migration pick. latest = {db source: newest activity date | None}:
    the source with the newest activity, COROS on a tie / without data."""
    cands = [(d, 1 if s == "coros" else 0, s) for s, d in latest.items() if d is not None and s in DB_SOURCES]
    return max(cands)[2] if cands else DEFAULT


# ---------------------------------------------------------------------------
# the app DB (async)
# ---------------------------------------------------------------------------

async def _latest(db, athlete_id: int) -> dict:
    from sqlalchemy import func, select
    from backend.db.models import WorkoutFile
    out = {}
    for s in DB_SOURCES:
        d = (await db.execute(select(func.max(WorkoutFile.workout_date)).where(
            WorkoutFile.athlete_id == athlete_id, WorkoutFile.source == s,
            WorkoutFile.file_format != "corrupt"))).scalar()
        if isinstance(d, str):
            d = dt.date.fromisoformat(d[:10])
        out[s] = d
    return out


async def current(db, athlete_id: int = 1) -> str:
    """The source in use; migrates an old value (auto / unset) on the way,
    writing the pick so the synchronous readers see the same source."""
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    stored = await repo.get(SETTING_KEY)
    v = normalize(stored)
    if v is None:
        v = choose_initial(await _latest(db, athlete_id))
        await repo.set(SETTING_KEY, v)
        await db.flush()
    return v


async def migrate(session_factory=None, athlete_id: int = 1) -> Optional[str]:
    """Startup: settle an old setting before any synchronous reader runs."""
    if session_factory is None:
        from backend.db.database import AsyncSessionLocal as session_factory
    from backend.settings.repository import SettingsRepository
    async with session_factory() as db:
        was = normalize(await SettingsRepository(db, athlete_id).get(SETTING_KEY))
        v = await current(db, athlete_id)
        if was is None:
            from backend.sync import dedup
            await dedup.rebuild(db, athlete_id)      # canonical rows = the migrated source's
        await db.commit()
        return v
