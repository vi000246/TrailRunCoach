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
    """A sync, a check or a deletion of the source is running. A yielding holder (the
    self-rating job, SP-362 A5) does not count: a sync asks it to stop and waits."""
    return source in _BUSY and source not in _YIELD


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


# A background job that gives way (SP-362 A5): it holds the busy flag (nothing runs beside
# it) but a sync / check of the same source sets its stop request and waits for it to end
# between two reads. A deletion (hold) is refused meanwhile, as during a sync.
_YIELD: dict[str, asyncio.Event] = {}
YIELD_WAIT_S = 30.0               # at most this long (one detail read is ≤ 20 s, DETAIL_TIMEOUT_S)


@contextlib.contextmanager
def hold_yielding(source: str):
    """hold(source) for a job that stops when asked; yields the stop request (an Event)."""
    with hold(source):
        stop = asyncio.Event()
        _YIELD[source] = stop
        try:
            yield stop
        finally:
            _YIELD.pop(source, None)


async def _make_way(source: str, wait: float = YIELD_WAIT_S) -> None:
    """Ask a yielding holder of `source` to stop and wait (at most `wait` s) until it has."""
    stop = _YIELD.get(source)
    if stop is None:
        return
    stop.set()
    end = time.monotonic() + wait
    while source in _BUSY and source in _YIELD and time.monotonic() < end:
        await asyncio.sleep(0.05)


def _client_stream(db: AsyncSession, source: str, athlete_id: int, since: Optional[str], **kw):
    if source == "coros":
        from backend.sync import coros_client
        return coros_client.sync_workouts(db, athlete_id, since=since, **kw)
    from backend.sync import tp_client
    return tp_client.sync_workouts(db, athlete_id, since=since)


async def stream(db: AsyncSession, source: str, athlete_id: int = 1,
                 since: Optional[str] = None, trigger: str = "manual",
                 feel_in_background: bool = False, client: Optional[Callable] = None,
                 remember: bool = True) -> AsyncIterator[dict]:
    """Run one sync, yielding the client's progress events. Yields a single
    SYNC_BUSY error event when the source is already running.

    `feel_in_background` (the button's SSE and run_once; SP-362 A5): COROS's self-rating
    passes over already-imported activities run as a job after the result is stored
    (start_feel_job) instead of inside the run.
    `client(db)` replaces the source's sync stream (the 完整檢查 / weekly check and its 補下載,
    sync/check.py): same busy flag, same post-run steps on what it downloaded; with
    `remember` False the run is not stored as the source's last sync (last_result / last_ok)."""
    await _make_way(source)
    try:
        ctx = hold(source)
        ctx.__enter__()
    except SyncBusy as e:
        yield {"status": "error", "error": "SYNC_BUSY", "source": source, "detail": str(e)}
        return
    result = {"at": None, "trigger": trigger, "status": "running", "downloaded": 0,
              "checked": 0, "errors": 0, "error": None}
    clock = SyncClock()
    bg_feel = feel_in_background and source == "coros" and client is None
    feel_read = None                  # the complete event's `feel_read`: the job is due
    try:
        events = client(db) if client is not None else \
            _client_stream(db, source, athlete_id, since, **({"feel_passes": False} if bg_feel else {}))
        async for ev in events:
            clock.event(ev)
            if ev.get("status") == "complete":
                if "feel_read" in ev:
                    ev = dict(ev)
                    feel_read = frozenset(ev.pop("feel_read") or ())
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
            if remember:
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
        # ④ COROS's self-rating passes (SP-231) as a background job (SP-362 A5): the run
        # above has ended and released the busy flag; the job holds it as a yielding holder
        if feel_read is not None and result["status"] in OK_STATUSES:
            try:
                start_feel_job(db, athlete_id, feel_read)
            except Exception as e:       # noqa: BLE001
                log.warning("self-rating job not started: %s", type(e).__name__)
        clock.secs["after"] += time.monotonic() - t_after
        try:
            _log_run(source, trigger, result, clock)
        except Exception:                # noqa: BLE001 — logging never breaks a sync
            pass


def start_feel_job(db: AsyncSession, athlete_id: int, read_now=frozenset()) -> asyncio.Task:
    """COROS's self-rating passes after a sync, in the background (SP-362 A5), on their own
    session of the sync's DB (the same tenant). Holds the COROS busy flag as a yielding holder:
    never beside a sync / check of COROS — one that starts asks it to stop between two reads;
    the rows left are read by the job after that run. ≥ 1 rating stored -> the automatic plan
    and the calibration, as the sync's `rpe_filled` did (SP-231)."""
    t = asyncio.get_running_loop().create_task(_feel_job(db.bind, athlete_id, frozenset(read_now)))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


async def _feel_job(bind, athlete_id: int, read_now: frozenset) -> int:
    from backend.sync import coros_client
    t0 = time.monotonic()
    try:
        with hold_yielding("coros") as stop:
            async with AsyncSession(bind, expire_on_commit=False) as db:
                filled = await coros_client.feel_job(db, athlete_id, read_now, should_stop=stop.is_set)
            stopped = stop.is_set()
    except SyncBusy:
        return 0                      # a check / deletion / sync took the flag first: the next job
    except Exception as e:            # noqa: BLE001 — never raises
        log.warning("COROS self-rating job failed: %s", type(e).__name__)
        return 0
    log.info("COROS self-rating job: %s stored in %.1f s%s", filled, time.monotonic() - t0,
             " (gave way to a sync)" if stopped else "")
    if filled >= 1:
        await _after_feel(filled, athlete_id)
    return filled


async def _after_feel(filled: int, athlete_id: int) -> None:
    """The hooks a sync with `rpe_filled` ≥ 1 calls (plan rule D / the RPE factor, SP-231),
    after the sync's own automatic plan run (if any) has ended."""
    res = {"status": "ok", "trigger": "rpe", "downloaded": 0, "rpe_filled": int(filled)}
    try:
        from backend.engine import plan_auto
        await plan_auto.wait_idle(600.0)
        plan_auto.after_sync("coros", res)
    except Exception as e:            # noqa: BLE001
        log.warning("auto plan trigger after the self-rating job failed: %s", type(e).__name__)
    try:
        from backend.engine import calibrate
        calibrate.after_sync("coros", res, athlete_id)
    except Exception as e:            # noqa: BLE001
        log.warning("calibration trigger after the self-rating job failed: %s", type(e).__name__)


async def run_once(source: str, athlete_id: int = 1, since: Optional[str] = None,
                   trigger: str = "schedule", session_factory: Optional[Callable] = None) -> dict:
    if session_factory is None:
        from backend.db.database import AsyncSessionLocal as session_factory
    last = {}
    async with session_factory() as db:
        async for ev in stream(db, source, athlete_id, since, trigger, feel_in_background=True):
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
