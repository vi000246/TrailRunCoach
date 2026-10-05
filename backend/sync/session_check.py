"""
Is the stored COROS / TrainingPeaks login still good? (2026-10-03)

A stored token is not proof of a login: a COROS token dies after ~24 h (or
as soon as the account logs in elsewhere), a TP session when its refresh
token / cookie is refused. The settings page used to say 已登入 for as long
as a token was stored. Now:

  * `check()` validates the token with one cheap authenticated call
    (COROS: activity/query size=1; TP: users/v3/user), at most once per
    CHECK_TTL_S per source; the answer is cached in memory.
  * Every COROS / TP call that gets the auth-required answer calls
    `mark_expired()`, so the status flips without waiting for the next check.
  * A dead token with a remembered password (「記住密碼」) is renewed by the
    clients' existing automatic re-login (one login, never a loop); only
    when that is not possible or fails is the login 登入已過期.
  * A network problem is "unknown": the page keeps 已登入 (a blip must not
    log anyone out); it is re-checked sooner (UNKNOWN_TTL_S). But a login
    COROS / TP already refused stays expired through an "unknown" re-check
    (SP-88: the 登入已過期 banner disappeared when the re-check after the
    CHECK_TTL_S cache could not reach COROS); only a login, an "ok" check or
    a logout clears it.

Nothing here logs or returns a token or a password.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import SyncState

log = logging.getLogger(__name__)

SOURCES = ("coros", "tp")
CHECK_TTL_S = 300            # a checked login is trusted this long (the page never hammers COROS / TP)
UNKNOWN_TTL_S = 60           # a check that could not reach the server is retried sooner

OK, EXPIRED, UNKNOWN, LOGGED_OUT = "ok", "expired", "unknown", "logged_out"

_CACHE: dict = {}            # (source, athlete_id) -> (result, time.monotonic())
_LOCKS: dict = {}


def _lock(source: str):
    loop = asyncio.get_running_loop()
    key = (id(loop), source)
    lk = _LOCKS.get(key)
    if lk is None:
        lk = _LOCKS[key] = asyncio.Lock()
    return lk


def mark_expired(source: str, athlete_id: int = 1) -> None:
    """A COROS / TP call answered auth-required (after any automatic re-login)."""
    if _CACHE.get((source, athlete_id), (None,))[0] != EXPIRED:
        log.warning("%s login expired", source.upper())
    _CACHE[(source, athlete_id)] = (EXPIRED, time.monotonic())


def mark_ok(source: str, athlete_id: int = 1) -> None:
    """A fresh login (manual or automatic) was stored."""
    _CACHE[(source, athlete_id)] = (OK, time.monotonic())


def forget(source: Optional[str] = None, athlete_id: Optional[int] = None) -> None:
    """Logout (or tests): drop the cached answer."""
    for k in list(_CACHE):
        if (source is None or k[0] == source) and (athlete_id is None or k[1] == athlete_id):
            _CACHE.pop(k, None)


def cached(source: str, athlete_id: int = 1) -> Optional[str]:
    """The cached answer while it is fresh, else None (no network)."""
    hit = _CACHE.get((source, athlete_id))
    if hit is None:
        return None
    result, at = hit
    ttl = UNKNOWN_TTL_S if result == UNKNOWN else CHECK_TTL_S
    return result if time.monotonic() - at < ttl else None


def is_expired(source: str, athlete_id: int = 1) -> bool:
    """Known to be expired (cache only, never a network call)."""
    return cached(source, athlete_id) == EXPIRED


async def _state(db: AsyncSession, athlete_id: int) -> Optional[SyncState]:
    return (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()


async def check(db: AsyncSession, source: str, athlete_id: int = 1) -> str:
    """ok | expired | unknown | logged_out. At most one live check per
    CHECK_TTL_S per source; concurrent callers wait for that one."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    st = await _state(db, athlete_id)
    if st is None or not (st.coros_access_token if source == "coros" else st.tp_access_token):
        return LOGGED_OUT
    hit = cached(source, athlete_id)
    if hit is not None:
        return hit
    async with _lock(source):
        hit = cached(source, athlete_id)
        if hit is not None:
            return hit
        prev = _CACHE.get((source, athlete_id), (None,))[0]      # stale answer, if any
        try:
            result = await (_check_coros(db, athlete_id) if source == "coros" else _check_tp(db, athlete_id))
        except Exception as e:                   # noqa: BLE001 — a check never breaks the page
            log.warning("%s login check failed: %s", source.upper(), type(e).__name__)
            result = UNKNOWN
        at = time.monotonic()
        if result == UNKNOWN and prev == EXPIRED:
            # refused before, unreachable now: still expired, re-checked as soon as an unknown
            result, at = EXPIRED, at - (CHECK_TTL_S - UNKNOWN_TTL_S)
        _CACHE[(source, athlete_id)] = (result, at)
        return result


# ---------------------------------------------------------------------------
# COROS
# ---------------------------------------------------------------------------

async def _check_coros(db: AsyncSession, athlete_id: int) -> str:
    from backend.sync import coros_client as C
    t0 = time.monotonic()
    try:
        # an expired token is renewed here first when a password is remembered
        token, base, user_id = await C._get_token_and_base(db, athlete_id)
    except ValueError:
        return EXPIRED
    verdict = await C.probe_token(base, token, user_id)
    if verdict == "ok":
        return OK
    if verdict != "invalid":
        return UNKNOWN
    # COROS refused the token before its 24 h: the stored expiry is wrong now
    st = await _state(db, athlete_id)
    if st is not None:
        st.coros_token_expires = datetime.now(timezone.utc)
        await db.commit()
    if st is not None and st.coros_password_sealed and await C.relogin(db, athlete_id, since=t0):
        return OK
    return EXPIRED


# ---------------------------------------------------------------------------
# TrainingPeaks
# ---------------------------------------------------------------------------

async def _check_tp(db: AsyncSession, athlete_id: int) -> str:
    from backend.sync import tp_client as T
    token = await T._get_valid_token(db, athlete_id)      # refresh / remembered-password login first
    if not token:
        return EXPIRED
    verdict = await T.probe_token(token)
    if verdict == "ok":
        return OK
    if verdict != "invalid":
        return UNKNOWN
    # refused before its expiry: force the refresh (and the remembered-password login)
    st = await _state(db, athlete_id)
    if st is not None:
        st.tp_token_expires = datetime.now(timezone.utc) - timedelta(minutes=10)
        await db.commit()
    token = await T._get_valid_token(db, athlete_id)
    if token and await T.probe_token(token) == "ok":
        return OK
    return EXPIRED
