"""
Weight API tokens (SP-472): who may send body-weight measurements to POST /api/v1/weight/measurements.

  * a token of its own kind (`trcw_…`, 256 random bits) that can do ONE thing: send weight
    measurements (scope weight:write). It reads nothing and reaches no other endpoint; the web
    session (cookies) is never read by that endpoint, and the token is never accepted anywhere else.
  * made on the 設定頁 (資料同步 › 體重同步), shown once; only its SHA-256 is stored (weight_tokens,
    in the tenant's own DB — another tenant's token is unknown there). Revoke one or all; no expiry
    (a Shortcut that silently stops after a year would be worse), MAX_ACTIVE live at a time.
  * sent as `Authorization: Bearer trcw_…`, or in `X-TRC-Weight-Token` when `Authorization` has to
    carry a proxy's own credentials (the same choice as the debug API).
  * owner mode (no sign-in): the endpoint checks the token itself; nothing else changes. Sign-in mode
    (SP-58): the login gate (tenancy_mw) lets exactly this POST through on a Bearer token, without a
    session or the CSRF header — a request that carries no cookie cannot be forged by another site —
    after finding the token's user in accounts.db (api_tokens, scope weight:write, registered with the
    tenant's own row whenever multi-user mode is on); the endpoint then checks the tenant's row too.
  * limits (security/ratelimit.py): a valid token is checked FIRST, so the per-IP block never hits it
    (behind cloudflared every caller may share one IP). Valid: TOKEN_BUCKET per token, TENANT_BUCKET per
    tenant → 429. Failed (missing / unknown / revoked): FAIL_BUCKET per IP → that IP is refused (429)
    for FAIL_BLOCK_S. Nothing about a failure is written to the DB (a flood can't fill it).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets as _secrets
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import WeightToken
from backend.security import ratelimit as RL

log = logging.getLogger("backend.weight_auth")

PREFIX = "trcw_"
HEADER = "x-trc-weight-token"
SCOPE = "weight:write"
CLIENT = "weight"                    # accounts.db api_tokens.client
MAX_ACTIVE = 5
NAME_MAX = 40

TOKEN_BUCKET = RL.Buckets(rate=60, per=3600)        # requests per token per hour
TENANT_BUCKET = RL.Buckets(rate=120, per=3600)      # per tenant per hour (all its tokens)
FAIL_BUCKET = RL.Buckets(rate=10, per=600)          # failed authentications per IP (10 per 10 min)
FAIL_BLOCK_S = 600
_BLOCKED: dict = {}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return None if t is None else t.replace(microsecond=0).isoformat() + "Z"


def reset_limits() -> None:
    """Tests: forget the buckets and the blocked IPs."""
    TOKEN_BUCKET.reset()
    TENANT_BUCKET.reset()
    FAIL_BUCKET.reset()
    _BLOCKED.clear()


def token_from_headers(headers) -> Optional[str]:
    """X-TRC-Weight-Token first, else Authorization: Bearer; only a well-formed trcw_ token."""
    val = (headers.get(HEADER) or "").strip()
    if not val:
        kind, _, rest = (headers.get("authorization") or "").partition(" ")
        if kind.lower() != "bearer":
            return None
        val = rest.strip()
    return val if val.startswith(PREFIX) and 20 <= len(val) <= 200 else None


def blocked(ip: str) -> bool:
    until = _BLOCKED.get(ip)
    if until is None:
        return False
    if until > RL.clock():
        return True
    _BLOCKED.pop(ip, None)
    return False


def failed(ip: str) -> int:
    """Count a failed authentication from `ip`: the status to answer (401, or 429 once blocked)."""
    if blocked(ip):
        return 429
    if not FAIL_BUCKET.take(ip):
        _BLOCKED[ip] = RL.clock() + FAIL_BLOCK_S
        log.warning("weight API: an IP blocked for %d min after repeated failed tokens", FAIL_BLOCK_S // 60)
        return 429
    return 401


def within_limits(tenant_id: str, token_id: int) -> bool:
    return TOKEN_BUCKET.take(f"{tenant_id}:{token_id}") and TENANT_BUCKET.take(tenant_id)


# ---------------------------------------------------------------------------- the tenant's tokens

def view(r: WeightToken) -> dict:
    return {"id": r.id, "name": r.name, "prefix": r.prefix, "created_at": _iso(r.created_at),
            "revoked_at": _iso(r.revoked_at), "active": r.revoked_at is None, "last_used_at": _iso(r.last_used_at)}


async def find(db: AsyncSession, token: str) -> Optional[WeightToken]:
    """The live row of `token` in this tenant's DB, else None."""
    r = (await db.execute(select(WeightToken).where(WeightToken.token_hash == hash_token(token)))).scalar_one_or_none()
    return r if r is not None and r.revoked_at is None else None


async def create(db: AsyncSession, name: str = "") -> tuple[str, dict]:
    """(the token — shown once —, its row). ValueError past MAX_ACTIVE."""
    name = str(name or "").strip()[:NAME_MAX] or "iPhone"
    n = (await db.execute(select(func.count(WeightToken.id)).where(WeightToken.revoked_at.is_(None)))).scalar_one()
    if n >= MAX_ACTIVE:
        raise ValueError(f"at most {MAX_ACTIVE} active tokens: revoke one first")
    raw = _secrets.token_urlsafe(32)
    token = PREFIX + raw
    r = WeightToken(name=name, prefix=PREFIX + raw[:4], token_hash=hash_token(token), created_at=_now())
    db.add(r)
    await db.commit()
    _register(token)
    return token, view(r)


async def list_tokens(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(WeightToken).order_by(WeightToken.id.desc()))).scalars().all()
    return [view(r) for r in rows]


async def revoke(db: AsyncSession, token_id: Optional[int] = None) -> int:
    """Revoke one token or every live one; how many."""
    q = select(WeightToken).where(WeightToken.revoked_at.is_(None))
    if token_id is not None:
        q = q.where(WeightToken.id == token_id)
    rows = (await db.execute(q)).scalars().all()
    hashes = [r.token_hash for r in rows]
    if rows:
        await db.execute(update(WeightToken).where(WeightToken.id.in_([r.id for r in rows]))
                         .values(revoked_at=_now()))
        await db.commit()
    _unregister(hashes)
    return len(rows)


# ---------------------------------------------------------------------------- accounts.db (multi-user)

def _user_id() -> Optional[int]:
    """The accounts.db user of the current tenant when multi-user mode is on, else None."""
    from backend import accounts, tenancy
    if not accounts.enabled():
        return None
    from backend.accounts import login as LG
    u = LG.current_user()
    if u is not None:
        return int(u.id)
    return 1 if tenancy.current().id == "owner" else None


def _register(token: str) -> None:
    uid = _user_id()
    if uid is None:
        return
    from backend.accounts import store
    store.register_token_hash(uid, CLIENT, [SCOPE], hash_token(token))


def _unregister(hashes: list) -> None:
    uid = _user_id()
    if uid is None or not hashes:
        return
    from backend.accounts import store
    store.revoke_token_hashes(uid, hashes)
