"""
Runs the one-time rain backfill (engine/rain_backfill.py, SP-299) on the server: the
scheduler loop (sync/scheduler.py) calls `tick` every minute once STARTUP_DELAY_S have passed
since the start (the warm-up and the first routes build have the CPU first).

`tick` starts the job in a worker thread when it is not done yet (the setting
`weather.rain_backfill`), the last attempt is RETRY_S old, and no routes build is running (it
writes the same activity_weather.json). The state is written when a run ends: complete → done;
a failed / cut-short run → tried again RETRY_S later, given up (done, gave_up) after
MAX_ATTEMPTS; no activity_weather.json yet → waiting, not counted. A run cut by a restart
leaves the state as it was, so the next start runs again — and asks only for what the cache
still lacks.

Never in tests (conftest sets WKO5COACH_NO_RAIN_BACKFILL), the demo, or with the archive
client off (WKO5COACH_ROUTES_WEATHER=0).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.settings.repository import SettingsRepository

log = logging.getLogger(__name__)

SETTING_KEY = "weather.rain_backfill"
ENV_OFF = "WKO5COACH_NO_RAIN_BACKFILL"
STARTUP_DELAY_S = 900            # after the app starts
RETRY_S = 6 * 3600               # between attempts after a failed / cut-short run
MAX_ATTEMPTS = 5

_TASK: Optional[asyncio.Task] = None


def enabled() -> bool:
    if os.getenv(ENV_OFF):
        return False
    from backend import tenancy
    if tenancy.demo_mode():
        return False
    from backend.api import routes as RA
    return RA.BUILDER.weather_get is not None


def _run(today: dt.date) -> dict:
    """The job itself (a worker thread): the routes store, its tracks and archive client."""
    from backend.api import routes as RA
    from backend.engine import rain_backfill as RB
    return RB.run(RA.STORE.root, RA.BUILDER.track, RA.BUILDER.weather_get, today)


def next_state(prev: dict, res: dict, now: datetime) -> dict:
    at = now.isoformat(timespec="seconds")
    if res.get("no_doc"):
        return {**prev, "done": False, "at": at, "waiting": True}
    attempts = int(prev.get("attempts") or 0) + 1
    complete = bool(res.get("complete"))
    out = {"done": complete or attempts >= MAX_ATTEMPTS, "at": at, "attempts": attempts,
           "calls_total": int(prev.get("calls_total") or 0) + int(res.get("calls") or 0),
           **{k: res.get(k) for k in ("activities", "calls", "filled", "failed", "skipped", "no_track")}}
    if out["done"] and not complete:
        out["gave_up"] = True
    if res.get("error"):
        out["error"] = res["error"]
    return out


async def tick(session_factory: Callable, athlete_id: int = 1, now: Optional[datetime] = None,
               run: Optional[Callable[[dt.date], dict]] = None) -> Optional[asyncio.Task]:
    """Start the backfill if it is due. → the job's task (its result: the new state), else None."""
    global _TASK
    if not enabled() or (_TASK is not None and not _TASK.done()):
        return None
    from backend.api import routes as RA
    if RA.BUILDER.running():
        return None
    now = now or datetime.now(timezone.utc)
    async with session_factory() as db:
        prev = await SettingsRepository(db, athlete_id).get(SETTING_KEY) or {}
    if prev.get("done"):
        return None
    try:
        last = datetime.fromisoformat(prev["at"]) if prev.get("at") else None
    except ValueError:
        last = None
    if last is not None and (now - last).total_seconds() < RETRY_S:
        return None
    _TASK = asyncio.create_task(_job(session_factory, athlete_id, prev, now, run or _run))
    return _TASK


async def _job(session_factory: Callable, athlete_id: int, prev: dict, now: datetime,
               run: Callable[[dt.date], dict]) -> dict:
    try:
        res = await asyncio.to_thread(run, now.date())
    except Exception as e:               # noqa: BLE001 — counted as a failed attempt
        res = {"complete": False, "error": f"{type(e).__name__}: {e}"[:200]}
    state = next_state(prev, res, now)
    async with session_factory() as db:
        await SettingsRepository(db, athlete_id).set(SETTING_KEY, state)
        await db.commit()
    log.warning("rain backfill: %s", {k: state.get(k) for k in ("done", "attempts", "calls", "filled", "failed",
                                                                 "skipped", "waiting", "gave_up", "error")})
    return state
