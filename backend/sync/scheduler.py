"""
Daily automatic sync. Started from the app lifespan; wakes every minute and,
once per local day at/after `sync.schedule.daily_time` (HH:MM in the athlete
time zone), runs every enabled, logged-in source through the runner (so it
shares the per-source lock with the button and auto-on-open).

Missed times (app not running) are caught up the first minute the app is up
after the time on that day; there is no catch-up for earlier days.

The same loop also runs the daily automatic backup (api/backup.auto_tick), the weekly
check of the last 60 days (sync/check.weekly_tick, SP-362 A4) and, once, the rain backfill
(api/rain_backfill.tick, SP-299).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time as _time               # `time` below is datetime.time (the daily HH:MM)
from datetime import datetime, time, timezone
from typing import Callable, Optional

from backend.settings.repository import SettingsRepository
from backend.sync import runner

log = logging.getLogger(__name__)
INTERVAL_S = 60


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


async def tick(session_factory: Callable, now: Optional[datetime] = None, athlete_id: int = 1,
               start: Callable = runner.start_background) -> list[str]:
    """One scheduler check. Returns the sources started."""
    async with session_factory() as db:
        repo = SettingsRepository(db, athlete_id)
        at = await repo.get("sync.schedule.daily_time")
        if not at:
            return []
        tz = await repo.timezone()
        local = (now or datetime.now(timezone.utc)).astimezone(tz)
        today = local.date().isoformat()
        if await repo.get("sync.schedule.last_run") == today or local.time() < _parse(at):
            return []
        # only the 資料來源 in use (runner.auto_plan)
        todo, skipped = await runner.auto_plan(db, athlete_id)
        # the day is done only when its source started: busy (a check / 補下載 / sync at
        # daily_time) -> the next minute tries again (SP-362 review #4)
        if "busy" in skipped.values():
            log.info("scheduled sync %s: source busy, retried next minute", today)
            return []
        await repo.set("sync.schedule.last_run", today)
        await db.commit()
        started = []
        for src in todo:
            t = start(src, athlete_id, "schedule", session_factory)
            if t is None:
                skipped[src] = "busy"
                continue
            started.append(src)
            if isinstance(t, asyncio.Task):      # ended SYNC_BUSY after all: retried next minute
                t.add_done_callback(lambda done: _busy_retry(done, session_factory, athlete_id, today))
        if "busy" in skipped.values():
            await repo.set("sync.schedule.last_run", None)
            await db.commit()
            log.info("scheduled sync %s: source busy, retried next minute", today)
            return started
    log.warning("scheduled sync %s: started=%s skipped=%s", today, started, skipped)
    return started


_PENDING: set = set()


def _busy_retry(task: asyncio.Task, session_factory: Callable, athlete_id: int, today: str) -> None:
    if task.cancelled() or task.exception() is not None:
        return
    last = task.result()
    if isinstance(last, dict) and last.get("error") == "SYNC_BUSY":
        t = asyncio.get_running_loop().create_task(_unmark(session_factory, athlete_id, today))
        _PENDING.add(t)
        t.add_done_callback(_PENDING.discard)


async def _unmark(session_factory: Callable, athlete_id: int, today: str) -> None:
    """The scheduled run got SYNC_BUSY (e.g. the self-rating job did not give way in time):
    forget the day's mark so the next minute starts it again."""
    try:
        async with session_factory() as db:
            repo = SettingsRepository(db, athlete_id)
            if await repo.get("sync.schedule.last_run") == today:
                await repo.set("sync.schedule.last_run", None)
                await db.commit()
                log.info("scheduled sync %s got SYNC_BUSY: retried next minute", today)
    except Exception as e:                       # noqa: BLE001
        log.warning("scheduled sync mark not reset: %s", type(e).__name__)


async def loop(session_factory: Optional[Callable] = None, interval: float = INTERVAL_S) -> None:
    if session_factory is None:
        from backend.db.database import AsyncSessionLocal as session_factory
    from backend.sync import check
    t_start = _time.monotonic()
    while True:
        try:
            await tick(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:           # never kill the loop
            log.warning("sync scheduler tick failed: %s", type(e).__name__)
        # the weekly check of the last 60 days (sync/check.py, SP-362 A4); not in the first
        # minutes after a start, when the warm-up has the CPU
        try:
            if _time.monotonic() - t_start >= check.STARTUP_DELAY_S:
                await check.weekly_tick(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("weekly sync check tick failed: %s", type(e).__name__)
        # the one-time rain backfill of the last 12 months (api/rain_backfill.py, SP-299)
        try:
            from backend.api import rain_backfill
            if _time.monotonic() - t_start >= rain_backfill.STARTUP_DELAY_S:
                await rain_backfill.tick(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("rain backfill tick failed: %s", type(e).__name__)
        # daily automatic backup (api/backup.py): the first pass runs at app
        # start; afterwards only when > 24 h since the last good backup
        try:
            from backend.api import backup as backup_api
            if not os.getenv("WKO5COACH_NO_AUTO_BACKUP"):
                await backup_api.auto_tick(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("backup tick failed: %s", type(e).__name__)
        await asyncio.sleep(interval)
