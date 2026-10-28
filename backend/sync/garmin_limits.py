"""
The Garmin trial's limits (SP-91): Garmin Connect is reached through the unofficial
`garminconnect` package, from this one server's IP, for every user who connects. Garmin's terms
forbid automated access and its known answer is a login lock of 48–72 h (docs/research/garmin.md
§2.4, §2.5), so the trial keeps the load small and stops at the first sign of push-back:

1. per account per day (the server's local date): AUTO_PER_DAY automatic sync (the daily schedule
   or the page-open trigger) + MANUAL_PER_DAY 「立即同步」 (the button, a failed-list 重試); an
   automatic re-login with the remembered password uses one of the day's
   AUTO_PER_DAY + MANUAL_PER_DAY runs too. At most REQUESTS_PER_DAY Garmin requests per account
   per day: a first sync of a long history stops there and goes on the next day
2. ≥ GAP_S seconds between two Garmin requests, server-wide (every call goes through
   `request()`), and one account's sync at a time on the whole server (`sync_slot`: the next one
   waits) — the NAS has one IP
3. no retry on 429 / 403: the account is paused (`paused`) until its next scheduled sync, which
   tries once (and may re-login once); the settings page says PAUSED_TEXT. No automatic re-login
   while paused. The package's own retries are off (retry_attempts=0, sync/garmin_client.py)
4. a server-wide switch (site_settings `garmin.enabled`, default on, no invitations): off = the
   connect option is hidden, the scheduler skips Garmin, the data stays. The owner flips it with
   PUT /api/v1/auth/garmin/site or `python -m backend.scripts.garmin_trial_report --switch off`
   (until SP-351's admin pages); membership.set_source("sync:garmin_trial", …) flips it too
5. a breaker: ≥ BREAKER_ACCOUNTS different accounts answered 429 within BREAKER_WINDOW_S turn the
   switch off by themselves (`garmin.tripped` = when and why); only the owner turns it back on.
   The 429s of the window are kept in site.json, so a restart does not forget them
6. the disclaimer before connecting (api/auth.py): DISCLAIMER_VERSION and the time per tenant
7. every login, sync, 429 / 403, MFA step, pause, recovery (how long the account was locked) and
   breaker goes to the sync event log (sync/events.py, source "garmin") with the package version —
   tenants by id only; backend/scripts/garmin_trial_report.py reads it

These limits apply always, the owner included: they are the trial's rules, not a multi-user
add-on. Numbers can be changed per server (site settings garmin.auto_per_day, garmin.manual_per_day,
garmin.requests_per_day, garmin.gap_s).
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import importlib.util
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from backend import site_settings as SS
from backend.i18n import N_, _

clock: Callable[[], float] = time.monotonic       # tests replace
wall: Callable[[], float] = time.time
sleep = asyncio.sleep                             # tests replace (the 2-s gap)

AUTO_PER_DAY = 1
MANUAL_PER_DAY = 3
REQUESTS_PER_DAY = 200          # ~7 min of requests at GAP_S; a long first sync spreads over days
GAP_S = 2.0
BREAKER_ACCOUNTS = 3
BREAKER_WINDOW_S = 24 * 3600.0
MANUAL_TRIGGERS = ("manual", "retry")             # a person pressed a button

PAUSED_TEXT = N_("Garmin 暫時鎖住登入，通常 2–3 天會恢復，這段時間會自動暫停同步")
LIMIT_TEXT = N_("今天的 Garmin 同步次數已用完（每天自動 1 次＋手動「立即同步」3 次，自動重新登入也算一次），明天會恢復")
REQUESTS_TEXT = N_("今天向 Garmin 下載的量已到上限，剩下的活動明天會自動接著同步")

DISCLAIMER_VERSION = "garmin-unofficial-v1-2026-10-12"
# todo-multi-user-sharing.plan.md §7.5 (COROS) worded for Garmin, the trial and 記住密碼
DISCLAIMER = {
    "zh-TW": N_("Garmin 同步是試運行功能：本服務透過 Garmin Connect 網站與 App 使用的非公開介面（非官方套件 "
              "garminconnect）下載你自己的活動，並非 Garmin 官方提供或支援。Garmin 的使用條款禁止用網站沒提供的方式"
              "自動存取：Garmin 可能隨時變更或封鎖這個介面、暫時鎖住登入（通常 2–3 天），也可能依其條款處置帳號，"
              "風險由你自行承擔。你的 Garmin 密碼只用來登入；勾選「記住密碼」才會加密保存，登入憑證加密存放在本服務的"
              "伺服器上。為了降低被鎖的機率，每天最多自動同步 1 次、手動同步 3 次。"),
    "en": ("Garmin sync is a trial: this service downloads your own activities through the non-public "
           "interface the Garmin Connect website and app use (the unofficial garminconnect package); it is not "
           "provided or supported by Garmin. Garmin's terms forbid automated access by means the site does not "
           "provide: Garmin may change or block it at any time, lock logins for a while (usually 2–3 days), or "
           "act on accounts under its terms — at your own risk. Your Garmin password is used to log in only; it "
           "is stored, encrypted, only when you tick Remember password. The login token is stored encrypted on "
           "this service's server. To keep the risk low, at most 1 automatic and 3 manual syncs a day."),
}


class GarminBlocked(RuntimeError):
    """A Garmin call was not made (or was refused): `code` says why.
    GARMIN_RATE_LIMITED   Garmin answered 429 / 403 now (this account pauses until its next schedule)
    GARMIN_PAUSED         this account is paused (an earlier 429 / 403)
    GARMIN_DAILY_LIMIT    the day's syncs (or requests: detail "requests") of this account are used
    GARMIN_DISABLED       server-wide switch off (by the owner, or the breaker)
    GARMIN_UNAVAILABLE    the garminconnect package is not installed on this server"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------- the package

_AVAILABLE: dict = {}


def available() -> bool:
    """The garminconnect package can be imported here (it is optional: without it the app runs
    and the Garmin option says it is not available). A test's fake package counts as installed."""
    from backend.sync import garmin_client
    if garmin_client._LIB is not None:
        return True
    if "v" not in _AVAILABLE:
        try:
            _AVAILABLE["v"] = importlib.util.find_spec("garminconnect") is not None
        except (ImportError, ValueError):
            _AVAILABLE["v"] = False
    return _AVAILABLE["v"]


def lib_version() -> Optional[str]:
    try:
        from importlib.metadata import version
        return version("garminconnect")
    except Exception:                            # noqa: BLE001 — not installed / no metadata
        return None


# ---------------------------------------------------------------------------- settings

def _num(key: str, default: float) -> float:
    try:
        v = float(SS.get(key, default))
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def limits() -> dict:
    return {"auto_per_day": int(_num("garmin.auto_per_day", AUTO_PER_DAY)),
            "manual_per_day": int(_num("garmin.manual_per_day", MANUAL_PER_DAY)),
            "requests_per_day": int(_num("garmin.requests_per_day", REQUESTS_PER_DAY)),
            "gap_s": _num("garmin.gap_s", GAP_S)}


def enabled() -> bool:
    return SS.get("garmin.enabled", True) is not False


def set_enabled(on: bool, by: str = "owner", reason: Optional[str] = None) -> dict:
    """The server-wide switch. Turning it on clears a tripped breaker and its window."""
    from backend.sync import events
    if on:
        SS.update({"garmin.enabled": True, "garmin.tripped": None, "garmin.blocks": None})
        events.log("garmin", "enabled", by=by)
    else:
        SS.update({"garmin.enabled": False, "garmin.disabled": {"at": wall(), "by": by, "reason": reason}})
        events.log("garmin", "disabled", by=by, reason=reason)
    return status()


def _tenant_id() -> str:
    from backend import tenancy
    return tenancy.current().id


def today() -> str:
    """The day the daily counts belong to: the server's local date."""
    return dt.datetime.fromtimestamp(wall()).date().isoformat()


# ---------------------------------------------------------------------------- pauses

_SCHEDULED: contextvars.ContextVar[bool] = contextvars.ContextVar("garmin_scheduled", default=False)
_PREV_PAUSE: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("garmin_prev_pause", default=None)


def paused(tenant_id: Optional[str] = None) -> Optional[dict]:
    """This account's pause ({at, reason, status}) or None."""
    return (SS.get("garmin.paused") or {}).get(tenant_id or _tenant_id())


def pause(reason: str, tenant_id: Optional[str] = None, status: Optional[int] = None) -> None:
    """Pause the account until its next scheduled sync. A scheduled run that is refused again keeps
    the first refusal's time, so the recovery time counts the whole lock."""
    from backend.sync import events
    tid = tenant_id or _tenant_id()
    cur = dict(SS.get("garmin.paused") or {})
    if tid in cur:
        return
    prev = _PREV_PAUSE.get()
    since = prev.get("at") if isinstance(prev, dict) and prev.get("at") else wall()
    cur[tid] = {"at": since, "reason": reason, "status": status, "last": wall()}
    SS.update({"garmin.paused": cur})
    events.log("garmin", "account_paused", tenant=tid, reason=reason, status=status,
               again=bool(prev))


def unpause(tenant_id: Optional[str] = None, recovered: bool = True) -> Optional[dict]:
    """Lift the pause; with `recovered` the event says how long the account was locked."""
    from backend.sync import events
    tid = tenant_id or _tenant_id()
    cur = dict(SS.get("garmin.paused") or {})
    was = cur.pop(tid, None)
    if was is not None:
        SS.update({"garmin.paused": cur or None})
        if recovered:
            events.log("garmin", "account_recovered", tenant=tid,
                       locked_s=int(max(0.0, wall() - float(was.get("at") or wall()))))
    return was


def run_succeeded(tenant_id: Optional[str] = None) -> None:
    """A sync / login of this account worked: a pause carried over from before (the scheduled run
    that was allowed past it) ends here, with the time the account was locked."""
    from backend.sync import events
    prev = _PREV_PAUSE.get()
    if prev is not None:
        tid = tenant_id or _tenant_id()
        events.log("garmin", "account_recovered", tenant=tid,
                   locked_s=int(max(0.0, wall() - float(prev.get("at") or wall()))))
        _PREV_PAUSE.set(None)
    unpause(tenant_id)


def check_allowed(scheduled: Optional[bool] = None) -> None:
    """Raise GarminBlocked when no Garmin call may be made for this tenant now. `scheduled`: the
    daily scheduled run, which may try again past an account pause (default: the current run's)."""
    if not available():
        raise GarminBlocked("GARMIN_UNAVAILABLE", "the garminconnect package is not installed")
    if not enabled():
        raise GarminBlocked("GARMIN_DISABLED", "breaker" if SS.get("garmin.tripped") else "switch off")
    if scheduled is None:
        scheduled = _SCHEDULED.get()
    p = paused()
    if p and not scheduled:
        raise GarminBlocked("GARMIN_PAUSED", PAUSED_TEXT)


# ---------------------------------------------------------------------------- the daily counts

def kind_of(trigger: str) -> str:
    return "manual" if trigger in MANUAL_TRIGGERS else "auto"


def usage(tenant_id: Optional[str] = None) -> dict:
    """Today's counts of this account: {day, auto, manual, relogin, requests}."""
    tid = tenant_id or _tenant_id()
    u = (SS.get("garmin.usage") or {}).get(tid)
    day = today()
    if not isinstance(u, dict) or u.get("day") != day:
        return {"day": day, "auto": 0, "manual": 0, "relogin": 0, "requests": 0}
    return {"day": day, **{k: int(u.get(k) or 0) for k in ("auto", "manual", "relogin", "requests")}}


def _save_usage(u: dict, tenant_id: Optional[str] = None) -> None:
    tid = tenant_id or _tenant_id()
    cur = {k: v for k, v in (SS.get("garmin.usage") or {}).items()
           if isinstance(v, dict) and v.get("day") == u["day"]}      # yesterday's counts go
    cur[tid] = u
    SS.update({"garmin.usage": cur})


def left(tenant_id: Optional[str] = None) -> dict:
    """What is left today: {auto, manual, requests}."""
    u, lim = usage(tenant_id), limits()
    total_left = lim["auto_per_day"] + lim["manual_per_day"] - u["auto"] - u["manual"] - u["relogin"]
    return {"auto": max(0, min(lim["auto_per_day"] - u["auto"], total_left)),
            "manual": max(0, min(lim["manual_per_day"] - u["manual"], total_left)),
            "requests": max(0, lim["requests_per_day"] - u["requests"])}


def can_start(trigger: str, tenant_id: Optional[str] = None) -> bool:
    return left(tenant_id)[kind_of(trigger)] > 0


def start_run(trigger: str, tenant_id: Optional[str] = None) -> dict:
    """Count one sync of today (raises GARMIN_DAILY_LIMIT when none is left)."""
    if not can_start(trigger, tenant_id):
        raise GarminBlocked("GARMIN_DAILY_LIMIT", LIMIT_TEXT)
    u = usage(tenant_id)
    u[kind_of(trigger)] += 1
    _save_usage(u, tenant_id)
    return u


def use_relogin(tenant_id: Optional[str] = None) -> None:
    """An automatic re-login takes one of the day's runs (raises when none is left)."""
    lim, u = limits(), usage(tenant_id)
    if u["auto"] + u["manual"] + u["relogin"] >= lim["auto_per_day"] + lim["manual_per_day"]:
        raise GarminBlocked("GARMIN_DAILY_LIMIT", LIMIT_TEXT)
    u["relogin"] += 1
    _save_usage(u, tenant_id)


# ---------------------------------------------------------------------------- requests, server-wide

_REQ: dict = {"last": None, "locks": {}}
_SLOTS: dict = {"locks": {}, "active": 0}


def reset() -> None:
    """Tests: forget the gap clock, the locks and the cached package check."""
    _REQ.update(last=None, locks={})
    _SLOTS.update(locks={}, active=0)
    _AVAILABLE.clear()


def _loop_lock(store: dict) -> asyncio.Lock:
    key = id(asyncio.get_running_loop())
    lk = store.get(key)
    if lk is None:
        lk = store[key] = asyncio.Lock()
    return lk


@asynccontextmanager
async def request(count: bool = True):
    """One Garmin request (a library call): the switch / pause / the day's request budget, then the
    server-wide gap — every Garmin request of the server waits on one lock, at least GAP_S after the
    previous one ended. `count` False: not charged to the account's budget (a login). A pause
    always stops it: the scheduled run lifted an old one at its start (sync_slot), so a pause seen
    here is a refusal of this very run — and no automatic re-login is made while paused."""
    check_allowed(scheduled=False)
    if count:
        u = usage()
        if u["requests"] >= limits()["requests_per_day"]:
            raise GarminBlocked("GARMIN_DAILY_LIMIT", "requests")
    async with _loop_lock(_REQ["locks"]):
        gap = limits()["gap_s"]
        if _REQ["last"] is not None:
            wait = _REQ["last"] + gap - clock()
            if wait > 0:
                await sleep(wait)
        if count:
            u = usage()
            u["requests"] += 1
            _save_usage(u)
        try:
            yield
        finally:
            _REQ["last"] = clock()


def blocked(status: int, login: bool = False, tenant_id: Optional[str] = None) -> GarminBlocked:
    """Garmin answered 429 / 403: no retry. The account pauses until its next schedule, a 429
    counts for the breaker. Returns the GarminBlocked to raise."""
    from backend.sync import events
    tid = tenant_id or _tenant_id()
    events.log("garmin", "http_%d" % status, tenant=tid, login=login, lib=lib_version())
    pause(f"HTTP {status}" + (" (login)" if login else ""), tid, status=status)
    if status == 429:
        rate_limited(tid)
    return GarminBlocked("GARMIN_RATE_LIMITED", PAUSED_TEXT)


def rate_limited(tenant_id: Optional[str] = None) -> bool:
    """An account answered 429. ≥ BREAKER_ACCOUNTS different accounts within BREAKER_WINDOW_S: the
    switch goes off by itself. True when that just happened."""
    from backend.sync import events
    tid = tenant_id or _tenant_id()
    now = wall()
    blocks = [b for b in (SS.get("garmin.blocks") or [])
              if isinstance(b, list) and len(b) == 2 and now - float(b[0]) <= BREAKER_WINDOW_S]
    blocks.append([now, tid])
    SS.update({"garmin.blocks": blocks})
    accounts_hit = {t for _w, t in blocks}
    if len(accounts_hit) >= BREAKER_ACCOUNTS and enabled():
        SS.update({"garmin.enabled": False,
                   "garmin.tripped": {"at": now, "accounts": len(accounts_hit),
                                      "reason": f"{len(accounts_hit)} accounts answered 429 within 24 h"}})
        events.log("garmin", "breaker_tripped", accounts=len(accounts_hit), lib=lib_version())
        return True
    return False


# ---------------------------------------------------------------------------- one sync at a time

def slot_busy() -> bool:
    return _SLOTS["active"] > 0


def scheduled() -> bool:
    return _SCHEDULED.get()


@asynccontextmanager
async def sync_slot(trigger: str = "manual"):
    """The server's one Garmin sync place (waits for it), then one of today's runs of this account
    (GARMIN_DAILY_LIMIT when none is left). The scheduled run may go past an account pause: it lifts
    it for the run and keeps its time (a refusal again keeps counting the lock from then)."""
    sched = trigger == "schedule"
    check_allowed(scheduled=sched)
    if not can_start(trigger):
        raise GarminBlocked("GARMIN_DAILY_LIMIT", LIMIT_TEXT)
    if left()["requests"] <= 0:                  # a first sync used today's requests: tomorrow goes on
        raise GarminBlocked("GARMIN_DAILY_LIMIT", REQUESTS_TEXT)
    lk = _loop_lock(_SLOTS["locks"])
    async with lk:
        check_allowed(scheduled=sched)          # the switch may have gone off while waiting
        start_run(trigger)
        tok_s = _SCHEDULED.set(sched)
        prev = unpause(recovered=False) if sched and paused() else None
        tok_p = _PREV_PAUSE.set(prev)
        _SLOTS["active"] += 1
        try:
            yield
        finally:
            _SLOTS["active"] -= 1
            try:
                _PREV_PAUSE.reset(tok_p)
                _SCHEDULED.reset(tok_s)
            except ValueError:                   # closed from another context (a dropped SSE stream)
                _PREV_PAUSE.set(None)
                _SCHEDULED.set(False)


# ---------------------------------------------------------------------------- status

def status(tenant_id: Optional[str] = None) -> dict:
    """For the settings page and the owner: plain words, nothing personal."""
    p = paused(tenant_id)
    lf = left(tenant_id)
    if not available():
        state, msg = "unavailable", _("這台伺服器沒有安裝 Garmin 同步需要的套件（garminconnect），目前無法連接 Garmin。")
    elif not enabled():
        state = "disabled"
        msg = (_("Garmin 同步全站暫停中：短時間內有多個帳號被 Garmin 鎖住登入，為了不讓整台伺服器被封鎖，已自動暫停，"
                 "站主確認後才會恢復。已匯入的活動都還在。") if SS.get("garmin.tripped")
               else _("Garmin 同步全站暫停中（站主關閉）。已匯入的活動都還在。"))
    elif p:
        state, msg = "paused", _(PAUSED_TEXT) + _("（下一次每日自動同步會再試一次；在那之前請不要反覆登入或同步）。")
    elif lf["manual"] <= 0:
        state, msg = "limit", _(LIMIT_TEXT)
    elif lf["requests"] <= 0:
        state, msg = "limit", _(REQUESTS_TEXT)
    else:
        state, msg = "ok", ""
    return {"state": state, "message": msg, "enabled": enabled(), "available": available(),
            "tripped": SS.get("garmin.tripped"), "paused": p, "left": lf, "usage": usage(tenant_id),
            "limits": limits(), "busy": slot_busy(), "disclaimer_version": DISCLAIMER_VERSION,
            "lib_version": lib_version()}
