"""
Daily automatic sync. Started from the app lifespan; wakes every minute and,
once per local day at/after `sync.schedule.daily_time` (HH:MM in the athlete
time zone), runs every enabled, logged-in source through the runner (so it
shares the per-source lock with the button and auto-on-open).

Missed times (app not running) are caught up the first minute the app is up
after the time on that day; there is no catch-up for earlier days.

The same loop also runs the daily automatic backup (api/backup.auto_tick).
"""
from __future__ import annotations

import asyncio
import logging
import os
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
        await repo.set("sync.schedule.last_run", today)
        await db.commit()
        # only the 資料來源 in use (runner.auto_plan)
        todo, skipped = await runner.auto_plan(db, athlete_id)
    started = []
    for src in todo:
        if start(src, athlete_id, "schedule", session_factory) is not None:
            started.append(src)
        else:
            skipped[src] = "busy"
    log.warning("scheduled sync %s: started=%s skipped=%s", today, started, skipped)
    return started


async def loop(session_factory: Optional[Callable] = None, interval: float = INTERVAL_S) -> None:
    if session_factory is None:
        from backend.db.database import AsyncSessionLocal as session_factory
    while True:
        try:
            await tick(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:           # never kill the loop
            log.warning("sync scheduler tick failed: %s", type(e).__name__)
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
