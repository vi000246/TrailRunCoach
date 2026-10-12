"""
COROS with many users (SP-385): every account logs in and syncs from this one server's IP
through COROS Training Hub's unofficial API (teamapi.coros.com). If COROS takes that for abuse
it can throttle or block the IP — and every user's sync stops at once. So, for the whole server:

1. a rate limit: one token bucket (per second and per minute) every COROS request waits on
   (sync/http.py wraps the transport: requests to *.coros.com go through `before_request`), and
   at most `max_concurrent` accounts sync at the same time (`sync_slot`, sync/runner.py)
2. no retry loop when blocked: a 429 / 403, or a login refused that way, raises CorosBlocked;
   that account is paused until its next scheduled sync (`paused`). When the server-wide share
   of 429 / 403 answers passes ERROR_RATE over the last ERROR_WINDOW_S, every COROS call pauses
   for COOLDOWN_S (`cooldown_until`)
3. a server-wide switch (site_settings `coros.enabled`): off = the scheduler skips COROS, the
   settings page says 暫停中, the imported activities stay. The owner flips it
   (POST /api/v1/admin/coros, owner only, until SP-351's admin pages)
4. a breaker: ≥ BREAKER_ACCOUNTS different accounts refused at login within BREAKER_WINDOW_S
   turn the switch off by themselves (`coros.tripped` = when and why); only the owner turns it
   back on
5. the disclaimer before connecting (api/auth.py): DISCLAIMER_VERSION and the time are kept per
   tenant; the owner's existing connection needs none
6. an event log (logs/sync_events.jsonl, sync/events.py): logins, syncs, 429 / 403, pauses —
   tenants by id only, never an e-mail or a token

The rate limit and the concurrency cap apply in multi-user mode (accounts.enabled()) or when
`coros.limits` is set to "on"; the owner alone keeps syncing at the old speed. The switch, the
breaker and the pauses apply always. Numbers are site settings (coros.rate_per_s,
coros.rate_per_min, coros.max_concurrent); when an official API (SP-348, a Partner API) comes,
they are to be recomputed from its quota.
"""
from __future__ import annotations

import asyncio
import collections
import threading
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from backend import site_settings as SS

clock: Callable[[], float] = time.monotonic      # tests replace
wall: Callable[[], float] = time.time

RATE_PER_S = 2.0
RATE_PER_MIN = 60.0
MAX_CONCURRENT = 1
ERROR_WINDOW_S = 600.0
ERROR_MIN_CALLS = 20
ERROR_RATE = 0.3
COOLDOWN_S = 1800.0
BREAKER_ACCOUNTS = 3
BREAKER_WINDOW_S = 24 * 3600.0
HOST_SUFFIX = "coros.com"

DISCLAIMER_VERSION = "coros-unofficial-v1-2026-10-12"
# todo-multi-user-sharing.plan.md §7.5, worded for a hosted service and the 記住密碼 option, plus
# SP-385's lock-out line. To be replaced by SP-60's third-party clause (a new version then)
DISCLAIMER = {
    "zh-TW": ("本服務透過 COROS Training Hub 網頁使用的非公開介面下載你自己的活動，並非 COROS 官方提供或支援。"
              "COROS 可能隨時變更或封鎖這個介面、暫時鎖住登入，也可能依其條款處置自動化存取的帳號，風險由你自行承擔。"
              "你的 COROS 密碼只用來登入；勾選「記住密碼」才會加密保存，登入憑證加密存放在本服務的伺服器上。"),
    "en": ("This service downloads your own activities through the non-public interface the COROS Training "
           "Hub website uses; it is not provided or supported by COROS. COROS may change or block it at any "
           "time, lock logins for a while, or act on accounts under its terms for automated access — at your "
           "own risk. Your COROS password is used to log in only; it is stored, encrypted, only when you tick "
           "Remember password. The login token is stored encrypted on this service's server."),
}


class CorosBlocked(RuntimeError):
    """A COROS call was not made (or was refused): `code` says why.
    COROS_RATE_LIMITED   COROS answered 429 / 403 (this account pauses until its next schedule)
    COROS_PAUSED         this account is paused (an earlier 429 / refused login)
    COROS_COOLDOWN       server-wide: too many 429 / 403 lately
    COROS_DISABLED       server-wide switch off (by the owner, or the breaker)"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# ---------------------------------------------------------------------------- settings

def limits_on() -> bool:
    v = SS.get("coros.limits")
    if v in ("on", True):
        return True
    if v in ("off", False):
        return False
    from backend import accounts
    return accounts.enabled()


def enabled() -> bool:
    return SS.get("coros.enabled", True) is not False


def _num(key: str, default: float) -> float:
    try:
        v = float(SS.get(key, default))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def set_enabled(on: bool, by: str = "owner", reason: Optional[str] = None) -> dict:
    """The server-wide switch. Turning it on clears a tripped breaker and the cooldown."""
    from backend.sync import events
    if on:
        SS.update({"coros.enabled": True, "coros.tripped": None})
        _STATE["cooldown_until"] = 0.0
        _STATE["login_blocks"].clear()
        events.log("coros", "enabled", by=by)
    else:
        SS.update({"coros.enabled": False, "coros.disabled": {"at": wall(), "by": by, "reason": reason}})
        events.log("coros", "disabled", by=by, reason=reason)
    return status()


# ---------------------------------------------------------------------------- in-process state

_LOCK = threading.Lock()
_STATE: dict = {
    "tokens_s": None, "tokens_m": None, "last": None,
    "calls": collections.deque(),          # (t, blocked: bool) of the last ERROR_WINDOW_S
    "cooldown_until": 0.0,
    "login_blocks": collections.deque(),   # (wall t, tenant id) of logins refused as blocked
}
_SLOTS: dict = {"active": 0, "cond": None}


def reset() -> None:
    """Tests: forget the buckets, the counts and the pauses."""
    with _LOCK:
        _STATE.update(tokens_s=None, tokens_m=None, last=None, cooldown_until=0.0)
        _STATE["calls"].clear()
        _STATE["login_blocks"].clear()
    _SLOTS.update(active=0, cond=None)


def _tenant_id() -> str:
    from backend import tenancy
    return tenancy.current().id


def _refill(now: float) -> tuple[float, float]:
    ps, pm = _num("coros.rate_per_s", RATE_PER_S), _num("coros.rate_per_min", RATE_PER_MIN)
    burst_s, burst_m = max(1.0, ps), max(1.0, pm)
    if _STATE["last"] is None:
        _STATE.update(tokens_s=burst_s, tokens_m=burst_m, last=now)
    dt = max(0.0, now - _STATE["last"])
    _STATE["tokens_s"] = min(burst_s, _STATE["tokens_s"] + dt * ps)
    _STATE["tokens_m"] = min(burst_m, _STATE["tokens_m"] + dt * pm / 60.0)
    _STATE["last"] = now
    return ps, pm


def _take_or_wait() -> float:
    """Take a token (0) or the seconds until one is there."""
    with _LOCK:
        ps, pm = _refill(clock())
        if _STATE["tokens_s"] >= 1.0 and _STATE["tokens_m"] >= 1.0:
            _STATE["tokens_s"] -= 1.0
            _STATE["tokens_m"] -= 1.0
            return 0.0
        need_s = 0.0 if _STATE["tokens_s"] >= 1.0 else (1.0 - _STATE["tokens_s"]) / ps
        need_m = 0.0 if _STATE["tokens_m"] >= 1.0 else (1.0 - _STATE["tokens_m"]) * 60.0 / pm
        return max(need_s, need_m, 0.001)


def paused(tenant_id: Optional[str] = None) -> Optional[dict]:
    """This account's pause ({at, reason}) or None: set by a 429 / blocked login, cleared by its
    next scheduled sync (runner) or a successful login."""
    tid = tenant_id or _tenant_id()
    return (SS.get("coros.paused") or {}).get(tid)


def pause(reason: str, tenant_id: Optional[str] = None) -> None:
    from backend.sync import events
    tid = tenant_id or _tenant_id()
    cur = dict(SS.get("coros.paused") or {})
    if tid not in cur:
        cur[tid] = {"at": wall(), "reason": reason}
        SS.update({"coros.paused": cur})
        events.log("coros", "account_paused", tenant=tid, reason=reason)


def unpause(tenant_id: Optional[str] = None) -> None:
    from backend.sync import events
    tid = tenant_id or _tenant_id()
    cur = dict(SS.get("coros.paused") or {})
    if cur.pop(tid, None) is not None:
        SS.update({"coros.paused": cur or None})
        events.log("coros", "account_resumed", tenant=tid)


def check_allowed(scheduled: bool = False) -> None:
    """Raise CorosBlocked when no COROS call may be made for this tenant now. `scheduled`: the
    daily scheduled run, which may try again after an account pause."""
    if not enabled():
        tr = SS.get("coros.tripped")
        raise CorosBlocked("COROS_DISABLED", "breaker" if tr else "switch off")
    if clock() < _STATE["cooldown_until"]:
        raise CorosBlocked("COROS_COOLDOWN", f"{int(_STATE['cooldown_until'] - clock())} s left")
    if not scheduled and paused():
        raise CorosBlocked("COROS_PAUSED", (paused() or {}).get("reason", ""))


async def before_request(url_host: str) -> None:
    """Every request to *.coros.com (sync/http.py): the switch / cooldown / pause, then a token
    of the server-wide bucket (waits; never refuses for the rate alone)."""
    check_allowed()                     # a scheduled run's pause was cleared by sync_slot already
    if not limits_on():
        return
    while True:
        wait = _take_or_wait()
        if wait <= 0:
            return
        await asyncio.sleep(wait)


def after_response(status: int, login: bool = False) -> None:
    """Count the answer; on a 429 (or a login answered 403 / 429) pause this account (and maybe
    the server, or trip the breaker) and raise CorosBlocked: no retry loop. A 403 elsewhere is
    COROS's expired-token answer (coros_client): logged, the re-login handles it."""
    from backend.sync import events
    blocked = status == 429 or (login and status == 403)
    if status == 403 and not login:
        events.log("coros", "http_403", tenant=_tenant_id())
    now = clock()
    tid = _tenant_id()
    with _LOCK:
        calls = _STATE["calls"]
        calls.append((now, blocked))
        while calls and now - calls[0][0] > ERROR_WINDOW_S:
            calls.popleft()
        n, bad = len(calls), sum(1 for _t, b in calls if b)
        cool = n >= ERROR_MIN_CALLS and bad / n >= ERROR_RATE and now >= _STATE["cooldown_until"]
        if cool:
            _STATE["cooldown_until"] = now + COOLDOWN_S
    if cool:
        events.log("coros", "server_cooldown", calls=n, blocked=bad, seconds=int(COOLDOWN_S))
    if not blocked:
        return
    events.log("coros", "http_%d" % status, tenant=tid, login=login)
    pause(f"HTTP {status}" + (" (login)" if login else ""), tid)
    if login:
        login_blocked(tid)
    raise CorosBlocked("COROS_RATE_LIMITED", f"HTTP {status}")


def login_blocked(tenant_id: Optional[str] = None) -> bool:
    """A login refused as blocked (not a wrong password). ≥ BREAKER_ACCOUNTS different accounts in
    BREAKER_WINDOW_S: the switch goes off by itself. True when that just happened."""
    from backend.sync import events
    tid = tenant_id or _tenant_id()
    now = wall()
    with _LOCK:
        lb = _STATE["login_blocks"]
        lb.append((now, tid))
        while lb and now - lb[0][0] > BREAKER_WINDOW_S:
            lb.popleft()
        accounts_hit = {t for _w, t in lb}
    if len(accounts_hit) >= BREAKER_ACCOUNTS and enabled():
        SS.update({"coros.enabled": False,
                   "coros.tripped": {"at": now, "accounts": len(accounts_hit),
                                     "reason": f"{len(accounts_hit)} accounts refused at login within 24 h"}})
        events.log("coros", "breaker_tripped", accounts=len(accounts_hit))
        return True
    return False


# ---------------------------------------------------------------------------- accounts syncing at once

import contextvars                                  # noqa: E402

_SCHEDULED: contextvars.ContextVar[bool] = contextvars.ContextVar("coros_scheduled", default=False)


@asynccontextmanager
async def sync_slot(scheduled: bool = False):
    """One of the server's `max_concurrent` COROS sync places (waits for one); the scheduled run
    may go on past an account pause (it clears it). No cap when the limits are off."""
    check_allowed(scheduled=scheduled)
    if scheduled and paused():
        unpause()
    tok = _SCHEDULED.set(scheduled)
    if not limits_on():
        try:
            yield
        finally:
            _SCHEDULED.reset(tok)
        return
    if _SLOTS["cond"] is None:
        _SLOTS["cond"] = asyncio.Condition()
    cond: asyncio.Condition = _SLOTS["cond"]
    async with cond:
        while _SLOTS["active"] >= int(_num("coros.max_concurrent", MAX_CONCURRENT)):
            await cond.wait()
        _SLOTS["active"] += 1
    try:
        yield
    finally:
        _SCHEDULED.reset(tok)
        async with cond:
            _SLOTS["active"] -= 1
            cond.notify_all()


def active_syncs() -> int:
    return _SLOTS["active"]


# ---------------------------------------------------------------------------- status

def status(tenant_id: Optional[str] = None) -> dict:
    """For the settings page and the owner: plain words, nothing personal."""
    tr = SS.get("coros.tripped")
    p = paused(tenant_id)
    cool = max(0, int(_STATE["cooldown_until"] - clock()))
    if not enabled():
        state = "disabled"
    elif cool:
        state = "cooldown"
    elif p:
        state = "paused"
    else:
        state = "ok"
    return {"state": state, "enabled": enabled(), "tripped": tr, "cooldown_s": cool,
            "paused": p, "limits": limits_on(),
            "rate_per_s": _num("coros.rate_per_s", RATE_PER_S),
            "rate_per_min": _num("coros.rate_per_min", RATE_PER_MIN),
            "max_concurrent": int(_num("coros.max_concurrent", MAX_CONCURRENT)),
            "disclaimer_version": DISCLAIMER_VERSION}
