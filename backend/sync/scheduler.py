"""
Daily automatic sync. Started from the app lifespan; wakes every minute and,
once per local day at/after `sync.schedule.daily_time` (HH:MM in the athlete
time zone), runs every enabled, logged-in source through the runner (so it
shares the per-source lock with the button and auto-on-open).

Missed times (app not running) are caught up the first minute the app is up
after the time on that day; there is no catch-up for earlier days.

The same loop also runs the daily automatic backup (api/backup.auto_tick), the weekly
check of the last 60 days (sync/check.weekly_tick, SP-362 A4), the daily cleanup of deleted
FIT objects (sync/purge.cleanup_tick, SP-308), the monthly archive of the FITs past the
retention (engine/fit_archive.tick, SP-317: the 1st, 03:00–05:00 local, tenants staggered),
the nightly apply of the received body weights (api/weight.tick, SP-472: 03:00–06:00 local, not
while a sync or an automatic plan run of the tenant is going), the background sync's looks for
new activities (sync/background.tick_all, SP-499) and, once, the rain backfill
(api/rain_backfill.tick, SP-299).

Every tenant (SP-384, SP-374 第 5 條): each minute the loop walks accounts.tenants.sync_tenants()
— the owner, and in multi-user mode every enabled user — and runs the daily sync and the weekly
check inside that tenant (its own DB, its own `sync.schedule.daily_time`). A user's time is
shifted by a fixed 0–STAGGER_MIN-minute offset from its id (SP-338 第 11 條: not everyone at
07:00 sharp); the owner's is not. The backup and the rain backfill stay the owner's (a user has
no `backup` cap; the backfill keeps one job slot for the server).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time as _time               # `time` below is datetime.time (the daily HH:MM)
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional

from backend.settings.repository import SettingsRepository
from backend.sync import runner

log = logging.getLogger(__name__)
INTERVAL_S = 60
STAGGER_MIN = 30          # a user's daily sync starts 0–29 min after its daily_time (SP-338 第 11 條)


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


NIGHT_START = "01:00"     # the free tier's daily sync: somewhere in 01:00–05:00 (SP-330 / SP-338 第 5、11 條)
NIGHT_SPAN_MIN = 240


def night_slot(tenant_id: str) -> tuple[str, int]:
    """(HH:MM, offset) of a free-tier tenant's nightly sync: spread over NIGHT_SPAN_MIN by its id."""
    return NIGHT_START, int(hashlib.sha256(("night:" + tenant_id).encode("utf-8")).hexdigest(), 16) % NIGHT_SPAN_MIN


def offset_minutes(tenant_id: str) -> int:
    """The fixed shift of a tenant's daily sync: 0 for the owner, else 0..STAGGER_MIN-1 from a
    hash of its id (the same every day, so a user's sync time does not wander)."""
    if tenant_id == "owner":
        return 0
    return int(hashlib.sha256(tenant_id.encode("utf-8")).hexdigest(), 16) % STAGGER_MIN


def _due(local: datetime, at: str, offset_min: int) -> bool:
    start = datetime.combine(local.date(), _parse(at), tzinfo=local.tzinfo) + timedelta(minutes=offset_min)
    return local >= start


async def tick(session_factory: Callable, now: Optional[datetime] = None, athlete_id: int = 1,
               start: Callable = runner.start_background, offset_min: int = 0,
               night: Optional[tuple] = None) -> list[str]:
    """One scheduler check of the current tenant. Returns the sources started. `night`
    (HH:MM, offset): the free tier's nightly slot replaces the tenant's own daily_time."""
    async with session_factory() as db:
        repo = SettingsRepository(db, athlete_id)
        at = await repo.get("sync.schedule.daily_time")
        if not at:
            return []
        if night is not None:
            at, offset_min = night
        tz = await repo.timezone()
        local = (now or datetime.now(timezone.utc)).astimezone(tz)
        today = local.date().isoformat()
        if await repo.get("sync.schedule.last_run") == today or not _due(local, at, offset_min):
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


async def tick_all(session_factory: Optional[Callable] = None, now: Optional[datetime] = None,
                   start: Callable = runner.start_background) -> dict:
    """The daily-sync check of every tenant (SP-384): {tenant id: sources started}. A failing
    tenant is logged and skipped; the others still run. `session_factory` None = each tenant's
    own DB (database.AsyncSessionLocal reads the current tenant)."""
    from backend import tenancy
    from backend.accounts import tenants as TN
    out: dict = {}
    for t in TN.sync_tenants():
        with tenancy.use(t):
            try:
                await TN.ensure_ready(t)
                sf = session_factory
                if sf is None:
                    from backend.db.database import AsyncSessionLocal as sf
                from backend import membership as MS
                night = None if t.id == "owner" or MS.has("sync:auto_realtime", t) else night_slot(t.id)
                out[t.id] = await tick(sf, now=now, start=start, offset_min=offset_minutes(t.id), night=night)
            except asyncio.CancelledError:
                raise
            except Exception as e:           # noqa: BLE001 — never kill the loop for one tenant
                log.warning("sync scheduler tick failed (%s): %s", "owner" if t.id == "owner" else "a user",
                            type(e).__name__)
    return out


async def _each_tenant(fn: Callable, label: str) -> None:
    from backend import tenancy
    from backend.accounts import tenants as TN
    for t in TN.sync_tenants():
        with tenancy.use(t):
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("%s tick failed: %s", label, type(e).__name__)


async def loop(session_factory: Optional[Callable] = None, interval: float = INTERVAL_S) -> None:
    """Every minute: each tenant's daily sync and weekly check; the owner's rain backfill and
    backup. `session_factory` None = each tenant's own DB."""
    from backend import tenancy
    from backend.sync import check

    def sf():
        if session_factory is not None:
            return session_factory
        from backend.db.database import AsyncSessionLocal
        return AsyncSessionLocal
    from backend.sync import background
    t_start = _time.monotonic()
    background.started()
    while True:
        await tick_all(session_factory)
        # 背景同步 (sync/background.py, SP-499): each tenant's look for new activities, at an
        # interval by how recently it was used, staggered, one at a time for the server
        try:
            await background.tick_all(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as e:               # noqa: BLE001
            log.warning("background sync tick failed: %s", type(e).__name__)
        # the weekly check of the last 60 days (sync/check.py, SP-362 A4); not in the first
        # minutes after a start, when the warm-up has the CPU
        if _time.monotonic() - t_start >= check.STARTUP_DELAY_S:
            await _each_tenant(lambda: check.weekly_tick(sf()), "weekly sync check")
            # once a day per tenant: the files of deleted / orphan FIT objects (sync/purge.cleanup_objects, SP-308)
            from backend.sync import purge
            await _each_tenant(lambda: purge.cleanup_tick(sf()), "FIT objects cleanup")
            # the monthly archive of FITs past the retention (engine/fit_archive.py, SP-317): on the
            # 1st, 03:00–05:00 local, each tenant at its own slot; queued at `archive` priority
            from backend.engine import fit_archive
            await _each_tenant(fit_archive.tick_async, "monthly archive")
            # 體重同步 (SP-472, api/weight.tick): the measurements received during the day go into the
            # weight entries once a night (03:00 local + a per-tenant slot), never while a sync runs
            from backend.api import weight as weight_api
            await _each_tenant(lambda: weight_api.tick(sf()), "weight apply")
        with tenancy.use(tenancy.owner()):
            # the one-time rain backfill of the last 12 months (api/rain_backfill.py, SP-299)
            try:
                from backend.api import rain_backfill
                if _time.monotonic() - t_start >= rain_backfill.STARTUP_DELAY_S:
                    await rain_backfill.tick(sf())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("rain backfill tick failed: %s", type(e).__name__)
            # daily automatic backup (api/backup.py): the first pass runs at app
            # start; afterwards only when > 24 h since the last good backup
            try:
                from backend.api import backup as backup_api
                if not os.getenv("WKO5COACH_NO_AUTO_BACKUP"):
                    await backup_api.auto_tick(sf())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("backup tick failed: %s", type(e).__name__)
            # multi-user mode: the whole server's daily backup — accounts.db and every user's
            # tenant (accounts/server_backup.py, SP-387 第 7 條)
            try:
                from backend.accounts import server_backup
                if not os.getenv("WKO5COACH_NO_AUTO_BACKUP"):
                    await server_backup.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("server backup tick failed: %s", type(e).__name__)
        await asyncio.sleep(interval)
