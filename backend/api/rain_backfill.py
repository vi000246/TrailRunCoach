"""
Runs the one-time rain backfill (engine/rain_backfill.py, SP-299) on the server: the
scheduler loop (sync/scheduler.py) calls `tick` every minute once STARTUP_DELAY_S have passed
since the start (the warm-up and the first routes build have the CPU first).

One writer at a time: the job runs in the routes Builder's own slot (`Builder.start_task`, a
daemon thread), so while it runs no routes build starts — the pages' `_ensure_fresh` and
「重建」 find the builder busy and the build starts after it — and nothing else writes the
weather cache or activity_weather.json. It starts only when no build runs.

`tick`: records a finished run (the next tick after its thread ends), else starts one when it
is not done yet (the setting `weather.rain_backfill`) and the last attempt is RETRY_S old.
complete → done; a failed / cut-short run → tried again RETRY_S later, given up (done,
gave_up) after MAX_ATTEMPTS; no activity_weather.json yet → waiting, not counted. STOP (set by
the app's lifespan when it quits) ends a run before its next call and starts no new one; a run
cut by it, or by a crash, is not an attempt — the next start resumes it, asking only for what
the cache still lacks.

Never in tests (conftest sets WKO5COACH_NO_RAIN_BACKFILL), the demo, or with the archive
client off (WKO5COACH_ROUTES_WEATHER=0).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.settings.repository import SettingsRepository

log = logging.getLogger(__name__)

SETTING_KEY = "weather.rain_backfill"
ENV_OFF = "WKO5COACH_NO_RAIN_BACKFILL"
STARTUP_DELAY_S = 900            # after the app starts
RETRY_S = 6 * 3600               # between attempts after a failed / cut-short run
MAX_ATTEMPTS = 5
STOP = threading.Event()         # the app is quitting (main.lifespan)

_JOB: Optional[dict] = None      # the running / finished run: {thread, prev, now, res}


def enabled() -> bool:
    if os.getenv(ENV_OFF):
        return False
    from backend import tenancy
    if tenancy.demo_mode():
        return False
    from backend.api import routes as RA
    return RA.BUILDER.weather_get is not None


def _run(today: dt.date) -> dict:
    """The job itself (the builder's thread): the routes store, its tracks and archive client."""
    from backend.api import routes as RA
    from backend.engine import rain_backfill as RB
    return RB.run(RA.STORE.root, RA.BUILDER.track, RA.BUILDER.weather_get, today, stop=STOP)


def next_state(prev: dict, res: dict, now: datetime) -> dict:
    if res.get("stopped"):
        return {**prev, "stopped": True}            # not an attempt: the next start resumes
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
               run: Optional[Callable[[dt.date], dict]] = None) -> Optional[dict]:
    """→ {"state": the state written} when a finished run was recorded, {"started": its thread}
    when a run started, else None."""
    global _JOB
    if _JOB is not None:
        if _JOB["thread"].is_alive():
            return None
        job, _JOB = _JOB, None
        state = next_state(job["prev"], job["res"] or {"complete": False, "error": "no result"}, job["now"])
        async with session_factory() as db:
            await SettingsRepository(db, athlete_id).set(SETTING_KEY, state)
            await db.commit()
        log.warning("rain backfill: %s", {k: state.get(k) for k in (
            "done", "attempts", "calls", "filled", "failed", "skipped", "waiting", "gave_up", "stopped", "error")})
        return {"state": state}
    if STOP.is_set() or not enabled():
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
    job = {"prev": prev, "now": now, "res": None}
    fn = run or _run

    def work():
        try:
            job["res"] = fn(now.date())
        except Exception as e:           # noqa: BLE001 — counted as a failed attempt
            job["res"] = {"complete": False, "error": f"{type(e).__name__}: {e}"[:200]}

    t = RA.BUILDER.start_task(work, "rain-backfill")
    if t is None:                        # a build started meanwhile
        return None
    job["thread"] = t
    _JOB = job
    return {"started": t}
