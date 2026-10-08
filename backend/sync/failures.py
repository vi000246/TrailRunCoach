"""
Activities a sync listed but could not fetch (SP-362): the failed list.

Before, one failed download kept the sync cursor where it was, so every later sync listed
from far back again (production: 7 failures → 819 activities, 41 pages, 33–54 s each run).
Now a single activity's failure is written here and the cursor moves as normal; a failure of
the whole run (login, listing) still keeps the cursor (coros_client / tp_client).

  * kind "failed": the download or the import failed. The next syncs retry it BY ID (no
    re-listing), at most MAX_ATTEMPTS attempts in all or MAX_AGE_DAYS days after the first
    one (owner 2026-10-08); then automatic retries stop and the row stays for 設定 ›
    進階設定, whose 「重試」 (reset) makes it due again.
  * kind "no_file": the activity has no FIT file at all (a manual entry). Not a failure:
    never retried, never holds the cursor; kept so the listing overlap does not ask again.

A row is deleted when the activity is imported, and with the source's files (sync/purge.py).
Table sync_failures (db/models.SyncFailure; data_registry: IMPORTED bookkeeping).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import SyncFailure

MAX_ATTEMPTS = 5              # automatic attempts in all (owner 2026-10-08)
MAX_AGE_DAYS = 7              # … or this many days after the first failure
ERROR_CHARS = 200             # the stored error text (redacted: no token, no signed URL)
SOURCES = ("coros", "tp")
FAILED, NO_FILE = "failed", "no_file"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)     # naive UTC, as SQLite returns it


def _check(source: str) -> str:
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    return source


def _short(error) -> str:
    from backend import applog
    return applog.redact(str(error or ""))[:ERROR_CHARS]


def auto_retry(row: SyncFailure, now: Optional[datetime] = None) -> bool:
    """Whether the next sync retries this row by itself."""
    if row.kind != FAILED:
        return False
    now = now or _now()
    return int(row.attempts or 0) < MAX_ATTEMPTS and now - row.first_at < timedelta(days=MAX_AGE_DAYS)


def state(row: SyncFailure, now: Optional[datetime] = None) -> str:
    """retrying | stopped | no_file (what the settings list shows)."""
    if row.kind == NO_FILE:
        return NO_FILE
    return "retrying" if auto_retry(row, now) else "stopped"


async def get(db: AsyncSession, athlete_id: int, source: str, provider_id) -> Optional[SyncFailure]:
    return (await db.execute(select(SyncFailure).where(
        SyncFailure.athlete_id == athlete_id, SyncFailure.source == _check(source),
        SyncFailure.provider_id == str(provider_id)))).scalar_one_or_none()


async def _upsert(db: AsyncSession, athlete_id: int, source: str, provider_id, kind: str,
                  sport_type=None, workout_date: Optional[date] = None, error=None) -> SyncFailure:
    row = await get(db, athlete_id, source, provider_id)
    now = _now()
    if row is None:
        row = SyncFailure(athlete_id=athlete_id, source=source, provider_id=str(provider_id), kind=kind,
                          attempts=0, first_at=now, last_at=now)
        db.add(row)
    try:
        row.sport_type = int(sport_type) if sport_type is not None else row.sport_type
    except (TypeError, ValueError):
        pass
    row.workout_date = workout_date or row.workout_date
    row.kind = kind
    row.last_at = now
    if kind == FAILED:
        row.attempts = int(row.attempts or 0) + 1
        row.last_error = _short(error)
    else:
        row.last_error = None
    await db.commit()
    return row


async def record_failure(db: AsyncSession, athlete_id: int, source: str, provider_id, error,
                         sport_type=None, workout_date: Optional[date] = None) -> SyncFailure:
    """One more failed attempt (created on the first). Commits."""
    return await _upsert(db, athlete_id, source, provider_id, FAILED, sport_type, workout_date, error)


async def record_no_file(db: AsyncSession, athlete_id: int, source: str, provider_id,
                         sport_type=None, workout_date: Optional[date] = None) -> SyncFailure:
    """The activity has no FIT file (a manual entry): remembered, never retried. Commits."""
    return await _upsert(db, athlete_id, source, provider_id, NO_FILE, sport_type, workout_date)


async def resolve(db: AsyncSession, athlete_id: int, source: str, provider_id) -> bool:
    """The activity is imported now: forget its row. Commits; True when there was one."""
    row = await get(db, athlete_id, source, provider_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def due(db: AsyncSession, athlete_id: int, source: str, now: Optional[datetime] = None) -> list[SyncFailure]:
    """The rows the next sync retries by id, oldest activity first."""
    rows = (await db.execute(select(SyncFailure).where(
        SyncFailure.athlete_id == athlete_id, SyncFailure.source == _check(source),
        SyncFailure.kind == FAILED).order_by(SyncFailure.workout_date, SyncFailure.id))).scalars().all()
    now = now or _now()
    return [r for r in rows if auto_retry(r, now)]


async def listing(db: AsyncSession, athlete_id: int = 1) -> list[dict]:
    """Every row, newest activity first (GET /api/v1/sync/failed)."""
    rows = (await db.execute(select(SyncFailure).where(SyncFailure.athlete_id == athlete_id)
                             .order_by(SyncFailure.workout_date.desc(), SyncFailure.id.desc()))).scalars().all()
    now = _now()
    return [{"id": r.id, "source": r.source, "provider_id": r.provider_id, "sport_type": r.sport_type,
             "date": r.workout_date.isoformat() if r.workout_date else None, "kind": r.kind,
             "state": state(r, now), "attempts": int(r.attempts or 0), "last_error": r.last_error,
             "first_at": r.first_at.isoformat() + "Z", "last_at": r.last_at.isoformat() + "Z"}
            for r in rows]


async def reset(db: AsyncSession, athlete_id: int, row_id: int) -> Optional[SyncFailure]:
    """「重試」: due again (attempts 0, the 7 days start now). A no_file row is retried too
    (the user knows a file exists now). Commits; None when there is no such row."""
    row = (await db.execute(select(SyncFailure).where(SyncFailure.athlete_id == athlete_id,
                                                      SyncFailure.id == row_id))).scalar_one_or_none()
    if row is None:
        return None
    row.kind, row.attempts, row.first_at = FAILED, 0, _now()
    await db.commit()
    return row


async def clear(db: AsyncSession, athlete_id: int, source: str) -> None:
    """The source's rows (its files were deleted and its cursor reset: everything is listed
    again). Flushes; the caller commits."""
    await db.execute(delete(SyncFailure).where(SyncFailure.athlete_id == athlete_id,
                                               SyncFailure.source == _check(source)))
    await db.flush()
