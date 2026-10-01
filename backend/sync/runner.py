"""
One entry point for every sync run: the 「立即同步」 button (SSE), the daily
scheduler and the auto-sync-on-open trigger.

* a per-source busy flag: never two runs of the same source at once
  (a second caller gets SYNC_BUSY / HTTP 409)
* the outcome of each run (downloaded / checked / errors, when) is stored in
  the settings store as sync.<source>.last_result
* background runs use their own DB session
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import SyncState
from backend.settings.repository import SettingsRepository
from backend.sync.http import as_utc

log = logging.getLogger(__name__)

SOURCES = ("coros", "tp")
SETTING_NAME = {"coros": "coros", "tp": "trainingpeaks"}      # sync.<name>.* keys
_BUSY: set[str] = set()
_TASKS: set[asyncio.Task] = set()


class SyncBusy(RuntimeError):
    def __init__(self, source: str):
        super().__init__(f"SYNC_BUSY: a {source} sync (or file deletion) is already running")
        self.source = source


def is_busy(source: str) -> bool:
    return source in _BUSY


@contextlib.contextmanager
def hold(source: str):
    """Exclusive per-source section (sync or deletion). Single-threaded
    asyncio, so check-and-add is atomic."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    if source in _BUSY:
        raise SyncBusy(source)
    _BUSY.add(source)
    try:
        yield
    finally:
        _BUSY.discard(source)


def _client_stream(db: AsyncSession, source: str, athlete_id: int, since: Optional[str]):
    if source == "coros":
        from backend.sync import coros_client
        return coros_client.sync_workouts(db, athlete_id, since=since)
    from backend.sync import tp_client
    return tp_client.sync_workouts(db, athlete_id, since=since)


async def stream(db: AsyncSession, source: str, athlete_id: int = 1,
                 since: Optional[str] = None, trigger: str = "manual") -> AsyncIterator[dict]:
    """Run one sync, yielding the client's progress events. Yields a single
    SYNC_BUSY error event when the source is already running."""
    try:
        ctx = hold(source)
        ctx.__enter__()
    except SyncBusy as e:
        yield {"status": "error", "error": "SYNC_BUSY", "source": source, "detail": str(e)}
        return
    result = {"at": None, "trigger": trigger, "status": "running", "downloaded": 0,
              "checked": 0, "errors": 0, "error": None}
    try:
        async for ev in _client_stream(db, source, athlete_id, since):
            if ev.get("status") == "complete":
                result.update(downloaded=ev.get("total_downloaded", 0), checked=ev.get("total_checked", 0),
                              errors=len(ev.get("errors") or []), status="ok" if not ev.get("errors") else "partial")
            elif ev.get("error") and "workout_id" not in ev and "activity_id" not in ev:
                # run-level failure (auth, listing); per-workout errors carry an id
                result.update(status="failed", error=str(ev.get("error")))
            yield ev
    except Exception as e:  # keep the stream well-formed; record the failure
        log.warning("%s sync failed: %s", source, type(e).__name__)
        result.update(status="failed", error=type(e).__name__)
        yield {"status": "error", "error": "SYNC_FAILED", "detail": type(e).__name__}
    finally:
        ctx.__exit__(None, None, None)
        if result["status"] == "running":
            result["status"] = "aborted"
        result["at"] = datetime.now(timezone.utc).isoformat()
        try:
            await db.rollback()
            await SettingsRepository(db, athlete_id).set(f"sync.{SETTING_NAME[source]}.last_result", result)
            await db.commit()
        except Exception as e:
            log.warning("could not store %s sync result: %s", source, type(e).__name__)
        # 自動調整課表 (engine/plan_auto.py): ≥ 1 new activity -> reconcile, adapt and
        # push in a background task with its own DB session; it never raises here
        try:
            from backend.engine import plan_auto
            plan_auto.after_sync(source, result)
        except Exception as e:           # noqa: BLE001 — the sync result stands
            log.warning("auto plan trigger failed: %s", type(e).__name__)


async def run_once(source: str, athlete_id: int = 1, since: Optional[str] = None,
                   trigger: str = "schedule", session_factory: Optional[Callable] = None) -> dict:
    if session_factory is None:
        from backend.db.database import AsyncSessionLocal as session_factory
    last = {}
    async with session_factory() as db:
        async for ev in stream(db, source, athlete_id, since, trigger):
            last = ev
    return last


def start_background(source: str, athlete_id: int = 1, trigger: str = "auto",
                     session_factory: Optional[Callable] = None) -> Optional[asyncio.Task]:
    """Fire-and-forget run; None when the source is busy."""
    if is_busy(source):
        return None
    t = asyncio.create_task(run_once(source, athlete_id, trigger=trigger, session_factory=session_factory))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


# ---------------------------------------------------------------------------
# readiness / freshness
# ---------------------------------------------------------------------------

async def logged_in(db: AsyncSession, source: str, athlete_id: int = 1) -> bool:
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    if st is None:
        return False
    return bool(st.coros_access_token if source == "coros" else st.tp_access_token)


async def last_sync_at(db: AsyncSession, source: str, athlete_id: int = 1) -> Optional[datetime]:
    """Latest of the clean-sync cursor time and the last recorded run."""
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    cands = []
    if st is not None:
        cands.append(as_utc(st.coros_last_sync_at if source == "coros" else st.last_sync_at))
    res = await SettingsRepository(db, athlete_id).get(f"sync.{SETTING_NAME[source]}.last_result")
    if isinstance(res, dict) and res.get("at"):
        try:
            cands.append(as_utc(datetime.fromisoformat(res["at"])))
        except ValueError:
            pass
    cands = [c for c in cands if c]
    return max(cands) if cands else None


async def ready_sources(db: AsyncSession, athlete_id: int = 1) -> dict[str, str]:
    """source -> "ready" or the reason it is skipped."""
    repo = SettingsRepository(db, athlete_id)
    out = {}
    for s in SOURCES:
        if not await repo.get(f"sync.{SETTING_NAME[s]}.enabled"):
            out[s] = "disabled"
        elif not await logged_in(db, s, athlete_id):
            out[s] = "not_logged_in"
        elif is_busy(s):
            out[s] = "busy"
        else:
            out[s] = "ready"
    return out
