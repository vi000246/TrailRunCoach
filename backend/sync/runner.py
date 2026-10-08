"""
One entry point for every sync run: the 「立即同步」 button (SSE), the daily
scheduler and the auto-sync-on-open trigger.

* a per-source busy flag: never two runs of the same source at once
  (a second caller gets SYNC_BUSY / HTTP 409)
* the outcome of each run (downloaded / checked / errors, when) is stored in
  the settings store as sync.<source>.last_result; a run that worked (ok /
  partial) also as sync.<source>.last_ok. A failed run is not a sync: it
  never makes the source "fresh" for the auto-sync (SP-88)
* background runs use their own DB session
* each run's time per step (SP-215): listing, checking known activities,
  downloading, importing, finishing; stored as last_result.secs and logged
  in one line (backend/applog.py), WARNING over applog.SLOW_SYNC_S
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
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
OK_STATUSES = ("ok", "partial")                               # a run that synced
_BUSY: set[str] = set()
_TASKS: set[asyncio.Task] = set()


class SyncBusy(RuntimeError):
    def __init__(self, source: str):
        super().__init__(f"SYNC_BUSY: a {source} sync (or file deletion) is already running")
        self.source = source


class SyncClock:
    """Where a run's time went (SP-215), from the gaps between the client's
    events: the time up to an event is charged to what that event ends.

      started / checking / relogin -> list      (login, listing a page of activities)
      skipped                      -> check     (looking up an already-imported one)
      downloaded / no_file / error -> download + import (split by the event's
                                      `secs` {download, import} when the client sends it)
      complete                     -> finish    (cursor, the account's HR settings)
    `after` (the steps run when the stream ends) is added by the runner."""

    STEPS = ("list", "check", "download", "import", "finish", "after")

    def __init__(self):
        self.t0 = self.last = time.monotonic()
        self.secs = dict.fromkeys(self.STEPS, 0.0)
        self.slowest: Optional[tuple[float, str]] = None     # (seconds, activity id)

    def event(self, ev: dict) -> None:
        now = time.monotonic()
        gap, self.last = now - self.last, now
        st = ev.get("status")
        item = ev.get("workout_id") or ev.get("activity_id")
        if st == "skipped":
            self.secs["check"] += gap
        elif st == "complete":
            self.secs["finish"] += gap
        elif item is not None and st in ("downloaded", "no_file", "error"):
            split = ev.get("secs") if isinstance(ev.get("secs"), dict) else {}
            try:
                dl = min(gap, max(0.0, float(split.get("download", gap))))
            except (TypeError, ValueError):
                dl = gap
            self.secs["download"] += dl
            self.secs["import"] += gap - dl
            if self.slowest is None or gap > self.slowest[0]:
                self.slowest = (gap, str(item))
        else:
            self.secs["list"] += gap

    def total(self) -> float:
        return time.monotonic() - self.t0

    def summary(self) -> dict:
        """{total, list, check, download, import, finish, after} in seconds (0.1 s)."""
        out = {k: round(v, 1) for k, v in self.secs.items()}
        out["total"] = round(self.total(), 1)
        return out


def _log_run(source: str, trigger: str, result: dict, clock: SyncClock) -> None:
    """One line per run: outcome, counts, seconds per step, the slowest activity
    (its id only). Counts and durations only: nothing personal (backend/applog.py)."""
    from backend import applog
    s = clock.summary()
    steps = ", ".join(f"{k} {s[k]:.1f} s" for k in SyncClock.STEPS if s[k] >= 0.05)
    slow = f"; slowest activity {clock.slowest[0]:.1f} s ({clock.slowest[1]})" if clock.slowest else ""
    level = logging.WARNING if s["total"] >= applog.SLOW_SYNC_S or result["status"] == "failed" else logging.INFO
    log.log(level, "sync %s (%s) %s in %.1f s: checked %s, downloaded %s, errors %s%s%s",
            source, trigger, result["status"], s["total"], result.get("checked", 0),
            result.get("downloaded", 0), result.get("errors", 0),
            f" | {steps}" if steps else "", slow)


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
    clock = SyncClock()
    try:
        async for ev in _client_stream(db, source, athlete_id, since):
            clock.event(ev)
            if ev.get("status") == "complete":
                result.update(downloaded=ev.get("total_downloaded", 0), checked=ev.get("total_checked", 0),
                              errors=len(ev.get("errors") or []), status="ok" if not ev.get("errors") else "partial")
                if ev.get("tl_filled"):
                    # COROS TL stored on already-imported activities (engine/coros_tl.py refit)
                    result["tl_filled"] = int(ev["tl_filled"])
                if ev.get("rpe_filled"):
                    # COROS self-rating stored on already-imported activities (SP-231): the plan's
                    # rule D and the RPE factor need a run even without a new activity
                    result["rpe_filled"] = int(ev["rpe_filled"])
            elif ev.get("error") and "workout_id" not in ev and "activity_id" not in ev:
                # run-level failure (auth, listing); per-workout errors carry an id
                result.update(status="failed", error=str(ev.get("error")))
            yield ev
    except Exception as e:  # keep the stream well-formed; record the failure
        log.warning("%s sync failed: %s", source, type(e).__name__, exc_info=True)
        result.update(status="failed", error=type(e).__name__)
        yield {"status": "error", "error": "SYNC_FAILED", "detail": type(e).__name__}
    finally:
        ctx.__exit__(None, None, None)
        t_after = time.monotonic()                 # storing the result and the post-sync steps
        if result["status"] == "running":
            result["status"] = "aborted"
        result["at"] = datetime.now(timezone.utc).isoformat()
        result["secs"] = clock.summary()           # the settings page shows where the time went
        try:
            await db.rollback()
            repo = SettingsRepository(db, athlete_id)
            await repo.set(f"sync.{SETTING_NAME[source]}.last_result", result)
            if result["status"] in OK_STATUSES:
                await repo.set(f"sync.{SETTING_NAME[source]}.last_ok", {
                    "at": result["at"], "trigger": trigger, "downloaded": result["downloaded"]})
            await db.commit()
        except Exception as e:
            log.warning("could not store %s sync result: %s", source, type(e).__name__)
        # after the run, in priority order (SP-362): what the pages wait for first, then
        # the automatic plan, then the per-athlete calibration (lowest)
        # the athlete's time zone from the newest FIT (engine/localtime.py): before the
        # Dataset is rebuilt, which dates the activities with it
        if int(result.get("downloaded") or 0) > 0:
            try:
                from backend.engine import localtime
                await localtime.refresh_from_fits(db, athlete_id)
            except Exception as e:       # noqa: BLE001
                log.warning("time zone detection failed: %s", type(e).__name__)
        # ① new FIT files: rebuild the chart Dataset now (incremental: only the new files
        # are parsed), then the overview status and the plan inputs, not on the next page
        # load; its thread does the automatic classification last (api/wko5views.warm_up)
        if int(result.get("downloaded") or 0) > 0:
            try:
                from backend.api import wko5views
                wko5views.warm_up(f"sync-{source}")
            except Exception as e:       # noqa: BLE001
                log.warning("dataset warm-up after sync failed: %s", type(e).__name__)
        # ② 自動調整課表 (engine/plan_auto.py): ≥ 1 new activity -> reconcile, adapt and
        # push in a background task with its own DB session; it never raises here. Its
        # dataset / status / inputs join the warm-up's computations (single flight)
        try:
            from backend.engine import plan_auto
            plan_auto.after_sync(source, result)
        except Exception as e:           # noqa: BLE001 — the sync result stands
            log.warning("auto plan trigger failed: %s", type(e).__name__)
        # ③ 每人校正 (engine/calibrate.py): the same trigger re-fits the per-athlete
        # parameters in the background, after ② has ended
        try:
            from backend.engine import calibrate
            calibrate.after_sync(source, result, athlete_id)
        except Exception as e:           # noqa: BLE001
            log.warning("calibration trigger failed: %s", type(e).__name__)
        clock.secs["after"] += time.monotonic() - t_after
        try:
            _log_run(source, trigger, result, clock)
        except Exception:                # noqa: BLE001 — logging never breaks a sync
            pass


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
    if not (st.coros_access_token if source == "coros" else st.tp_access_token):
        return False
    # a login known to be expired (sync/session_check.py; cache only, no network)
    from backend.sync import session_check
    return not session_check.is_expired(source, athlete_id)


async def last_sync_at(db: AsyncSession, source: str, athlete_id: int = 1) -> Optional[datetime]:
    """When the source last synced: the latest of the clean-sync cursor time and
    the last run that worked (ok / partial). A failed or aborted run does not
    count (SP-88: a run that hit 登入已過期 used to make the source "fresh" for
    sync.auto_on_open.hours and showed as 上次同步)."""
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    cands = []
    if st is not None:
        cands.append(as_utc(st.coros_last_sync_at if source == "coros" else st.last_sync_at))
    repo = SettingsRepository(db, athlete_id)
    for key in ("last_result", "last_ok"):
        res = await repo.get(f"sync.{SETTING_NAME[source]}.{key}")
        if isinstance(res, dict) and res.get("at") and res.get("status", "ok") in OK_STATUSES:
            try:
                cands.append(as_utc(datetime.fromisoformat(res["at"])))
            except ValueError:
                pass
    cands = [c for c in cands if c]
    return max(cands) if cands else None


async def auto_plan(db: AsyncSession, athlete_id: int = 1) -> tuple[list, dict]:
    """What an automatic sync (page open, daily schedule) starts: only the
    資料來源 in use (sync/primary.py); the other source is never synced
    automatically. Returns (sources to start, skipped)."""
    from backend.sync import primary as P
    ready = await ready_sources(db, athlete_id)
    use = P.FOLDER[await P.current(db, athlete_id)]
    start = [s for s in ready if s == use and ready[s] == "ready"]
    skipped = {s: ("not_in_use" if s != use else ready[s]) for s in ready if s not in start}
    return start, skipped


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
