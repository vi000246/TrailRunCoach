"""
背景同步 (SP-499): a new activity on the watch is synced — and what the pages read is computed —
before anyone opens a page, instead of on the page open (POST /sync/auto) or at the daily time.

When. Every minute the scheduler loop (sync/scheduler.loop) calls tick_all(). Each tenant whose
background sync is on is looked at once per *interval*, at a fixed *phase* inside it (minutes,
from a hash of its id): the tenants of one interval are spread evenly over it, never all at once,
and each keeps its own minute every day. The interval follows how recently someone used the
account (accounts/tenants.last_visits: a sign-in or a session in use — not a sync, so these runs
never keep an account "in use"):

    the owner                      every OWNER_EVERY_MIN
    used within  3 days            every 30 min      (TIERS, 推估 — see below)
    used within 14 days            every 2 h
    used within 30 days            every 6 h
    longer ago                     none: the daily sync only (sync.schedule.daily_time)

推估, not measured: a run ends, the watch hands it to the phone app within minutes, so a look every
30 min has a new activity processed ~15 min after it reached COROS on average — before the person
is back at a screen. A look with nothing new costs COROS ~2 requests (the activity list since the
cursor, the account's HR settings: sync/coros_client.sync_workouts) and ~1–5 s; the COROS
self-rating passes (up to ~10 detail reads of ~1.8 MB) run after a background look only when it
brought something new or once per FEEL_EVERY_S (feel_due). An account not used for a while is
looked at less often: whoever it is, the data waits until they come back anyway.

Never two at once, never on a busy server. At most STARTS_PER_TICK new background syncs a minute
and MAX_RUNNING running, for the whole server (so ≤ 60 looks an hour: 30 accounts at 30 min; with
more, the interval stretches evenly — the most overdue goes first). None while the server's job
queue (backend/jobs.py) has BACKLOG_MAX jobs waiting, nor in the first STARTUP_DELAY_S after a
start (the deploy warm-up has the CPU). A due tenant that waits for the server is not skipped: it
is tried again the next minute. Local QUIET hours (01:00–05:00, when the free tier's nightly
sync, the backups and the monthly archive run) are skipped.

The services' limits. Every run goes through sync/runner.start_background (trigger "background",
an automatic trigger: merged with a page-open run of the last 5 min), so COROS's server-wide
token bucket, its one sync place, the cooldown and the account pauses (sync/coros_limits.py)
apply as to any sync; a COROS account paused by a 429 / refused login is not looked at (the next
daily sync tries it, as before), a server-wide cooldown waits. Garmin is never looked at in the
background: its trial allows one automatic run a day (sync/garmin_limits.AUTO_PER_DAY), which
the daily sync uses. Neither is intervals.icu (SP-346): its webhook brings new activities, and the
daily sync is the poll that catches a missed one (its requests are counted per app per day). A run that ends blocked or failed backs off: the next look after 2^n
intervals (n failures in a row: the next look is always skipped), at most BACKOFF_MAX_MIN, until
a run works again.

After the run, nothing new here: a run that downloaded something calls runner.after_import as
every sync does — the chart Dataset, the overview status and the 課表's plan inputs (incl. the
session matching), then the automatic plan, the activity classification and (SP-499) the charts
drawn lately (api/prerender.py) — all through the job queue; the warm-up of a background run is
queued at `warm` (api/wko5views._priority), below every page someone waits for.

Switches: TRC_BG_SYNC = on (default) | owner (the owner's account only) | off — and each
account's own 「有新活動時在背景自動同步」 (sync.background.enabled, default on; the free tier
has none: it syncs once a night, membership `sync:auto_realtime`). A demo instance never runs it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime
from typing import Callable, Optional

from backend.sync import runner

log = logging.getLogger(__name__)

ENV = "WKO5COACH_BG_SYNC"          # set as TRC_BG_SYNC (backend/env_names.py)
TRIGGER = "background"

OWNER_EVERY_MIN = 30
TIERS = ((3, 30), (14, 120), (30, 360))     # (used within this many days, look every N minutes) 推估
QUIET = (1, 5)                     # local hours [from, to): no background look
MAX_RUNNING = 1                    # background syncs running at once, server-wide
STARTS_PER_TICK = 1                # background syncs started per minute, server-wide
BACKLOG_MAX = 4                    # jobs waiting in backend/jobs.py at which none is started
STARTUP_DELAY_S = 600              # none in the first 10 min after a start (the deploy warm-up)
BACKOFF_MAX_MIN = 24 * 60          # a blocked / failed run: the next look after 2, 4, 8 … intervals, at most a day
FEEL_EVERY_S = 6 * 3600            # COROS self-rating passes after a background look with nothing new

wall: Callable[[], float] = time.time          # tests replace
clock: Callable[[], float] = time.monotonic

_STATE: dict = {}       # tenant id -> {"every", "slot", "fails", "until"}
_RUNNING: dict = {}     # tenant id -> its running background sync (asyncio.Task)
_FEEL: dict = {}        # runner._k(source) -> wall time of the last background self-rating job
_START: dict = {"at": None}


def reset() -> None:
    """Tests: forget the slots, the back-offs and the start time."""
    _STATE.clear()
    _RUNNING.clear()
    _FEEL.clear()
    _START["at"] = None


def started() -> None:
    """The scheduler loop started (the start-up delay counts from here)."""
    _START["at"] = clock()


def mode() -> str:
    """on | owner | off (TRC_BG_SYNC; anything else reads as on)."""
    v = (os.environ.get(ENV) or "on").strip().lower()
    if v in ("0", "off", "false", "no"):
        return "off"
    return "owner" if v == "owner" else "on"


def allowed(tenant=None) -> bool:
    """The server lets this tenant (default: the current one) sync in the background: the switch,
    not a demo, and its plan has `sync:auto_realtime`. Its own setting is read by the tick."""
    from backend import membership as MS, tenancy
    t = tenant or tenancy.current()
    m = mode()
    if m == "off" or t.is_demo or tenancy.demo_mode() or (m == "owner" and t.id != "owner"):
        return False
    return MS.has("sync:auto_realtime", t)


def every_min(tenant_id: str, last_visit: Optional[float], now: float) -> Optional[int]:
    """Minutes between two looks at this tenant, or None (not used lately: the daily sync only)."""
    if tenant_id == "owner":
        return OWNER_EVERY_MIN
    if not last_visit:
        return None
    days = (now - last_visit) / 86400.0
    for within, every in TIERS:
        if days <= within:
            return every
    return None


def phase_min(tenant_id: str, every: int) -> int:
    """The tenant's fixed minute inside its interval (from its id: spread evenly, stable)."""
    return int(hashlib.sha256(("bg:" + tenant_id).encode("utf-8")).hexdigest(), 16) % every


def slot_of(now: float, tenant_id: str, every: int) -> int:
    """The number of the tenant's current interval: a new number = a look is due."""
    return int((now / 60.0 - phase_min(tenant_id, every)) // every)


def slot_start(slot: int, tenant_id: str, every: int) -> float:
    return (slot * every + phase_min(tenant_id, every)) * 60.0


def backoff_s(fails: int, every: int) -> float:
    """After `fails` blocked / failed runs in a row: 2^fails intervals (the next look is skipped
    after the first), at most BACKOFF_MAX_MIN."""
    return min(every * 2 ** max(1, fails), BACKOFF_MAX_MIN) * 60.0


def feel_due(trigger: str, result: dict) -> bool:
    """sync/runner.stream: start COROS's self-rating job after this run? Always, except after a
    background look that brought nothing new: then once per FEEL_EVERY_S (a rating added later in
    the COROS app still arrives, without ~10 detail reads every 30 min)."""
    if trigger != TRIGGER or int((result or {}).get("downloaded") or 0) > 0:
        return True
    k, now = runner._k("coros"), wall()
    if now - _FEEL.get(k, -1e18) < FEEL_EVERY_S:
        return False
    _FEEL[k] = now
    return True


def _running() -> int:
    return sum(1 for t in _RUNNING.values() if not (isinstance(t, asyncio.Task) and t.done()))


def server_busy() -> Optional[str]:
    """Why no background sync starts now (None: one may)."""
    from backend import jobs
    if _START["at"] is not None and clock() - _START["at"] < STARTUP_DELAY_S:
        return "startup"
    if _running() >= MAX_RUNNING:
        return "running"
    if len(jobs.snapshot()["queued"]) >= BACKLOG_MAX:
        return "queue_busy"
    return None


def _ended(tid: str, task) -> None:
    """A background run ended: blocked / failed -> back off; worked -> the back-off is over."""
    if _RUNNING.get(tid) is task:
        _RUNNING.pop(tid, None)
    st = _STATE.get(tid)
    if st is None or task.cancelled():
        return
    exc = task.exception()
    last = None if exc is not None else task.result()
    code = last.get("error") if isinstance(last, dict) else None
    if exc is not None or (code and code != "SYNC_BUSY"):
        st["fails"] = int(st.get("fails") or 0) + 1
        wait = backoff_s(st["fails"], int(st["every"]))
        st["until"] = float(st.get("started_at") or wall()) + wait
        log.info("background sync (%s) ended with %s: next look in %d min", "owner" if tid == "owner" else "a user",
                 code or type(exc).__name__, int(wait // 60))
    else:
        st["fails"], st["until"] = 0, 0.0


async def _look(t, every: int, slot: int, now: float, sf: Callable, start: Callable) -> str:
    """One due look at the current tenant: started, or why not. A reason that leaves the slot
    open (the server or COROS is busy) is tried again the next minute."""
    from backend.settings.repository import SettingsRepository
    st = _STATE[t.id]

    def done(why: str) -> str:
        st["slot"] = slot
        return why
    if now < float(st.get("until") or 0.0):
        return done("backoff")
    if not allowed(t):
        return done("not_allowed")
    async with sf() as db:
        repo = SettingsRepository(db, 1)
        if not await repo.get("sync.background.enabled"):
            return done("off")
        tz = await repo.timezone()
        if QUIET[0] <= datetime.fromtimestamp(now, tz).hour < QUIET[1]:
            return done("quiet")
        todo, skipped = await runner.auto_plan(db, 1)
        if "garmin" in todo:
            todo.remove("garmin")              # one automatic Garmin run a day: the daily sync's
            skipped["garmin"] = "garmin_daily"
        if "intervals" in todo:
            # intervals.icu (SP-346) announces new activities by webhook; a look every 30 min would
            # spend ~2 of the account's ~100 requests a day for nothing: its daily sync is the poll
            todo.remove("intervals")
            skipped["intervals"] = "intervals_webhook"
        if not todo:
            return done(next(iter(v for v in skipped.values() if v != "not_in_use"), "no_source"))
        src = todo[0]
        if src == "coros":
            from backend.sync import coros_limits as CL
            if CL.paused():
                return done("paused")          # a 429 / refused login: the next daily sync tries it
            cs = CL.status()
            if cs["cooldown_s"] > 0:
                return "coros_cooldown"        # server-wide: wait, not this tenant's fault
            if cs["limits"] and CL.active_syncs() >= cs["max_concurrent"]:
                return "coros_busy"
        last = await runner.last_sync_at(db, src, 1)
        if last is not None and now - last.timestamp() < every * 60:
            return done("fresh")               # a page open / the button synced it meanwhile
    task = start(src, 1, TRIGGER, sf)
    if task is None:
        return done("busy")
    st["slot"], st["started_at"] = slot, now
    if isinstance(task, asyncio.Task):
        _RUNNING[t.id] = task
        task.add_done_callback(lambda done_task, tid=t.id: _ended(tid, done_task))
    return "started"


async def tick_all(session_factory: Optional[Callable] = None, now: Optional[float] = None,
                   start: Optional[Callable] = None) -> dict:
    """One minute's background-sync check of every tenant: {tenant id: what happened} for the
    tenants due or newly seen ("started", or why not). `now`: epoch seconds."""
    from backend import tenancy
    from backend.accounts import tenants as TN
    out: dict = {}
    if mode() == "off" or tenancy.demo_mode():
        return out
    now = wall() if now is None else now
    start = start or runner.start_background
    visits = TN.last_visits()
    due = []
    for t in TN.sync_tenants():
        if mode() == "owner" and t.id != "owner":
            continue
        every = every_min(t.id, visits.get(t.id), now)
        if every is None:
            _STATE.pop(t.id, None)
            continue
        slot = slot_of(now, t.id, every)
        st = _STATE.get(t.id)
        if st is None or st["every"] != every:
            # first seen (a start, a new tier): its first look is at its own next minute, so a
            # restart never makes every tenant due at once
            _STATE[t.id] = {"every": every, "slot": slot, "fails": (st or {}).get("fails", 0),
                            "until": (st or {}).get("until", 0.0)}
            out[t.id] = "scheduled"
            continue
        if st["slot"] == slot:
            continue
        due.append((slot_start(slot, t.id, every), t, every, slot))
    n = 0
    for _at, t, every, slot in sorted(due, key=lambda x: x[0]):      # the longest waiting first
        busy = server_busy()
        if busy is None and n >= STARTS_PER_TICK:
            busy = "next_minute"
        if busy is not None:
            out[t.id] = busy
            continue
        with tenancy.use(t):
            try:
                await TN.ensure_ready(t)
                sf = session_factory
                if sf is None:
                    from backend.db.database import AsyncSessionLocal as sf
                out[t.id] = await _look(t, every, slot, now, sf, start)
            except asyncio.CancelledError:
                raise
            except Exception as e:             # noqa: BLE001 — never kill the loop for one tenant
                _STATE[t.id]["slot"] = slot
                out[t.id] = "error"
                log.warning("background sync check failed (%s): %s", "owner" if t.id == "owner" else "a user",
                            type(e).__name__)
        if out[t.id] == "started":
            n += 1
    return out


def describe(tenant=None) -> dict:
    """For the settings page: {available, why, every_min, quiet} of the current tenant. why: None |
    off (the server's switch) | plan (the free tier: nightly) | demo. every_min: its interval while
    someone uses it (the person looking at the page)."""
    from backend import membership as MS, tenancy
    t = tenant or tenancy.current()
    if allowed(t):
        why = None
    elif t.is_demo or tenancy.demo_mode():
        why = "demo"
    elif mode() == "off" or (mode() == "owner" and t.id != "owner"):
        why = "off"
    else:
        why = "plan" if not MS.has("sync:auto_realtime", t) else "off"
    every = (OWNER_EVERY_MIN if t.id == "owner" else TIERS[0][1]) if why is None else None
    return {"available": why is None, "why": why, "every_min": every, "quiet": list(QUIET)}
