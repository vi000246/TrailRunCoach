"""
User row -> tenant (SP-384; auth-and-demo plan §5).

  owner (user 1)   tenancy.owner(): the same tenant as the single-user app — id "owner", its
                   folder ~/.wko5coach in place, the WKO5 folder, every cap. Nothing it reads
                   changes, so the owner's pages answer exactly as before the migration.
  user             Tenant("u<id>", USER, root = shared = <server-root>/users/<id>/), the caps of
                   tenancy.USER_CAPS (everything but the owner's own resources), no WKO5 folder.

ensure_ready(t): a user tenant's folder, its DB (init_db: create + migrate, as the owner's at
start-up) and its athlete row, once per process. sync_tenants(): the tenants the daily
scheduler walks (sync/scheduler.py).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from backend import accounts, tenancy
from backend.accounts import store

log = logging.getLogger(__name__)


def tenant_of(user: store.User) -> tenancy.Tenant:
    if user.is_owner:
        return tenancy.owner()
    d = user.folder()
    return tenancy.Tenant(id=f"u{user.id}", kind=tenancy.USER, root=d, shared=d, caps=tenancy.USER_CAPS)


_READY: set = set()
_READY_LOCK: Optional[asyncio.Lock] = None


async def ensure_ready(t: tenancy.Tenant) -> None:
    """A user tenant's folder, DB schema and athlete row (idempotent; once per process per
    folder). The owner's is made at start-up (main.lifespan)."""
    global _READY_LOCK
    if t.kind != tenancy.USER:
        return
    key = str(t.root)
    if key in _READY and (t.root / "wko5coach.db").exists():
        return
    if _READY_LOCK is None:
        _READY_LOCK = asyncio.Lock()
    async with _READY_LOCK:
        if key in _READY and (t.root / "wko5coach.db").exists():
            return
        t.root.mkdir(parents=True, exist_ok=True)
        from backend.db import database
        from backend.db.current import ensure_athlete
        with tenancy.use(t):
            await database.init_db()
            async with database.AsyncSessionLocal() as db:
                if await ensure_athlete(db, ""):
                    await db.commit()
        _READY.add(key)


def active_tenants(days: int = 7) -> list[tenancy.Tenant]:
    """The users active within `days` — signed in (a sign-in, or a session in use: its
    last_seen_at slides with the visits) or synced (a COROS / Garmin sync that worked,
    sync/events.py) — the most recently active first. SP-338 第 3 條, SP-394: after a deploy only
    these are warmed up ahead, one at a time behind the owner; the others are brought up to date
    on their first visit, old values marked 更新中 meanwhile. Not the owner (always warmed), not a
    disabled user."""
    import datetime as dt
    if not accounts.enabled():
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

    def epoch(iso) -> float:
        try:
            t = dt.datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return 0.0
        return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()
    out = []
    try:
        seen = store.last_seen()
        from backend.sync import events
        synced = events.last_syncs(since=cutoff.timestamp())
        for u in store.users():
            if u.is_owner:
                continue
            t = tenant_of(u)
            last = max(epoch(u.last_login_at), epoch(seen.get(u.id)), synced.get(t.id, 0.0))
            if last >= cutoff.timestamp():
                out.append((last, t))
    except Exception as e:                       # noqa: BLE001
        log.warning("accounts.db unreadable, no deploy warm-up for users: %s", type(e).__name__)
    return [t for _last, t in sorted(out, key=lambda x: -x[0])]


def last_visits() -> dict[str, float]:
    """tenant id -> when (epoch s) someone last used it: its newest sign-in or session in use
    (store.last_seen slides at most once an hour). Syncs do not count — the background sync
    (sync/background.py, SP-499) picks its interval from this, and its own runs must not keep an
    account "in use". Users only (the owner is always in use); {} in single-user mode or when
    accounts.db is unreadable."""
    import datetime as dt
    if not accounts.enabled():
        return {}

    def epoch(iso) -> float:
        try:
            t = dt.datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return 0.0
        return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()
    out: dict[str, float] = {}
    try:
        seen = store.last_seen()
        for u in store.users():
            if not u.is_owner:
                out[tenant_of(u).id] = max(epoch(u.last_login_at), epoch(seen.get(u.id)))
    except Exception as e:                       # noqa: BLE001
        log.warning("accounts.db unreadable, no visit times: %s", type(e).__name__)
        return {}
    return out


def sync_tenants() -> list[tenancy.Tenant]:
    """Every tenant whose daily sync the scheduler checks: the owner, plus in multi-user mode
    every enabled user (each reads its own sync.schedule.daily_time; a user without one is
    skipped by the tick itself)."""
    out = [tenancy.owner()]
    if not accounts.enabled():
        return out
    try:
        for u in store.users():
            if not u.is_owner:
                out.append(tenant_of(u))
    except Exception as e:                       # noqa: BLE001 — the owner's sync still runs
        log.warning("accounts.db unreadable, scheduling the owner only: %s", type(e).__name__)
    return out
