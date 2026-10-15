"""
intervals.icu's limits on this app (SP-346; docs/research/sync-platforms.md §3.1, §4.1), kept for
the whole server:

1. the app's daily request total: "The default daily rate limit is 100/user per day up to 500 users
   (max 50000 requests), with a minimum of 5000." (forum topic 609) — `daily_budget()` =
   min(50 000, max(5 000, 100 × connected accounts)); the accounts are the webhook map's entries
   (`register`). "The requests per 15 minute limit is 1/8 of the daily limit with a minimum of
   2500." — `quarter_budget()`. We stop at SAFETY of each, so a request of ours never meets a 429
2. "an additional limit of 10 calls per second per IP address": ≥ GAP_S between two requests,
   server-wide (`request()`)
3. the first back-fill of a new account (≈ 225 requests for a year, §4.1) is what can use the day up
   when many people join at once: back-fill requests may take at most BACKFILL_SHARE of the day's
   total (the webhooks and daily polls keep the rest) and one run at most BACKFILL_PER_RUN; the run
   stops there with `more` and the next day's sync goes on from where it stopped — the accounts
   queue up over days (sync/intervals_client.py, setting sync.intervals.backfill)

The counts live in memory and in site.json (`intervals.usage`, written every SAVE_EVERY requests and
at the end of a run: a restart forgets at most that many). The webhook map
(`intervals.athletes` = {sha256(intervals.icu athlete id): tenant id}) is in site.json too: the
webhook names the athlete, never our tenant; the id itself is not stored there (tenants by id only).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from backend import site_settings as SS

clock: Callable[[], float] = time.monotonic       # tests replace
sleep = asyncio.sleep

MIN_DAILY = 5000
PER_USER_DAILY = 100
MAX_DAILY = 50000              # 500 users; beyond it the app must write to support@intervals.icu
MIN_QUARTER = 2500
SAFETY = 0.9                   # stop at 90 % of either limit
BACKFILL_SHARE = 0.5           # back-fill takes at most half of the day's requests
BACKFILL_PER_RUN = 300         # one account's run at most this many back-fill requests (≈ 1.5 years listed)
GAP_S = 0.12                   # ≤ 10 calls per second per IP (we have one IP)
SAVE_EVERY = 20

USAGE_KEY = "intervals.usage"
MAP_KEY = "intervals.athletes"


class IntervalsBudget(RuntimeError):
    """A request was not made: `code` INTERVALS_DAILY_LIMIT (the app's day), INTERVALS_QUARTER_LIMIT
    (its 15 minutes) or INTERVALS_BACKFILL_LIMIT (the back-fill's share of the day / of this run)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_STATE: dict = {}
_GAP_LOCK: dict = {}
_LAST = {"t": -1e9}


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _usage() -> dict:
    """{day, requests, backfill, quarter_start (epoch s), quarter} for today (UTC day)."""
    u = _STATE.get("u")
    if u is None:
        stored = SS.get(USAGE_KEY) or {}
        u = _STATE["u"] = dict(stored) if isinstance(stored, dict) else {}
    if u.get("day") != _today():
        u.clear()
        u.update(day=_today(), requests=0, backfill=0, quarter_start=0.0, quarter=0)
    return u


def save() -> None:
    """The counts to site.json (end of a run; every SAVE_EVERY requests)."""
    u = _STATE.get("u")
    if u is not None:
        SS.update({USAGE_KEY: dict(u)})


def reset() -> None:
    """Tests: forget the counts (memory only)."""
    _STATE.clear()
    _GAP_LOCK.clear()
    _LAST["t"] = -1e9


# ---------------------------------------------------------------------------- the webhook map

def athlete_key(icu_id: str) -> str:
    return hashlib.sha256(str(icu_id).strip().encode("utf-8")).hexdigest()


def register(icu_id: str, tenant_id: str) -> None:
    """This intervals.icu athlete's webhooks go to `tenant_id` (a connect). One tenant per athlete:
    connecting the same intervals.icu account from another tenant moves it there."""
    m = dict(SS.get(MAP_KEY) or {})
    m = {k: v for k, v in m.items() if v != tenant_id}           # one account per tenant
    m[athlete_key(icu_id)] = tenant_id
    SS.update({MAP_KEY: m})


def unregister(tenant_id: str) -> None:
    m = dict(SS.get(MAP_KEY) or {})
    left = {k: v for k, v in m.items() if v != tenant_id}
    if left != m:
        SS.update({MAP_KEY: left or None})


def tenant_for(icu_id: str) -> Optional[str]:
    return (SS.get(MAP_KEY) or {}).get(athlete_key(icu_id))


def connected_accounts() -> int:
    return len(SS.get(MAP_KEY) or {})


# ---------------------------------------------------------------------------- budgets

def daily_budget(accounts: Optional[int] = None) -> int:
    n = connected_accounts() if accounts is None else accounts
    return min(MAX_DAILY, max(MIN_DAILY, PER_USER_DAILY * n))


def quarter_budget(daily: Optional[int] = None) -> int:
    return max(MIN_QUARTER, (daily_budget() if daily is None else daily) // 8)


def backfill_room() -> int:
    """Back-fill requests the server may still make today."""
    u = _usage()
    return max(0, int(daily_budget() * BACKFILL_SHARE) - int(u["backfill"]))


def status() -> dict:
    u = _usage()
    d = daily_budget()
    return {"day": u["day"], "requests": u["requests"], "backfill": u["backfill"], "daily_budget": d,
            "backfill_budget": int(d * BACKFILL_SHARE), "accounts": connected_accounts()}


def use(backfill: bool = False) -> None:
    """Count one request (raises IntervalsBudget instead when it would go past a limit)."""
    u = _usage()
    d = daily_budget()
    if u["requests"] + 1 > int(d * SAFETY):
        raise IntervalsBudget("INTERVALS_DAILY_LIMIT")
    now = time.time()
    if now - float(u.get("quarter_start") or 0) >= 900:
        u["quarter_start"], u["quarter"] = now, 0
    if u["quarter"] + 1 > int(quarter_budget(d) * SAFETY):
        raise IntervalsBudget("INTERVALS_QUARTER_LIMIT")
    if backfill and u["backfill"] + 1 > int(d * BACKFILL_SHARE):
        raise IntervalsBudget("INTERVALS_BACKFILL_LIMIT")
    u["requests"] += 1
    u["quarter"] += 1
    if backfill:
        u["backfill"] += 1
    if u["requests"] % SAVE_EVERY == 0:
        try:
            save()
        except OSError:
            pass


@asynccontextmanager
async def request(backfill: bool = False):
    """One intervals.icu request: counted (IntervalsBudget past a limit), and ≥ GAP_S after the
    server's previous one."""
    use(backfill=backfill)
    loop = id(asyncio.get_running_loop())
    lk = _GAP_LOCK.get(loop)
    if lk is None:
        lk = _GAP_LOCK[loop] = asyncio.Lock()
    async with lk:
        wait = GAP_S - (clock() - _LAST["t"])
        if wait > 0:
            await sleep(wait)
        _LAST["t"] = clock()
    yield
