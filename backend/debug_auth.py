"""
Debug API authentication (SP-371, docs/debug-api.md): who may call /api/v1/debug/*.

  * off by default: `debug.api.enabled` (設定 › 進階) and a server PIN (TRC_DEBUG_PIN) are both
    needed; otherwise every /api/v1/debug/* path answers 404 (the endpoints don't show), and the
    demo instance never mounts them.
  * a dedicated Bearer token only — `Authorization: Bearer trcd_…`. The web session (cookies)
    is never read, so a page hit by XSS / CSRF cannot call these, and a leaked token reaches
    nothing else.
  * tokens are made on the settings page after the PIN; the token is shown once and only its
    SHA-256 is stored (debug_tokens; 256 random bits, so a fast hash is enough — nothing to
    brute-force). Scopes (SCOPES), an expiry (1 / 7 / 30 / 90 days), revoke one / all; bound to
    the tenant that made it (tenancy.current().id): another tenant's token is unknown here.
  * the PIN comes from the environment only — never the repo, never the DB. Compared in constant
    time (both sides hashed first); PIN_MAX_FAILS wrong in a row lock it for PIN_LOCK_S, for the
    whole server (one PIN per server). A PIN shorter than PIN_MIN_LEN counts as not set.
  * limits (backend/security/ratelimit.py, as tenancy_mw): TOKEN_BUCKET calls per token per
    minute → 429; FAIL_BUCKET failed authentications per source IP → that IP is blocked for
    FAIL_BLOCK_S (429).
  * audit: every call that got past the 404 gate writes a debug_audit row (time, token, path,
    query, IP, status, size, ms); the newest AUDIT_KEEP are kept, the settings page shows 100.
    A token used from an IP it never used before is flagged (new_ip) for the settings page.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets as _secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend import tenancy
from backend.db.database import get_db
from backend.db.models import DebugAudit, DebugToken
from backend.security import ratelimit as RL

PIN_ENV = "TRC_DEBUG_PIN"
PIN_MIN_LEN = 6
PIN_MAX_FAILS = 5
PIN_LOCK_S = 15 * 60
ENABLED_KEY = "debug.api.enabled"

TOKEN_PREFIX = "trcd_"
SCOPES = ("read:activity", "read:plan", "read:sync", "export:config")
DEFAULT_SCOPES = ("read:activity", "read:plan", "read:sync")      # export:config only when ticked
DAYS = (1, 7, 30, 90)
DEFAULT_DAYS = 30
MAX_ACTIVE = 20
NAME_MAX = 40
IPS_KEEP = 20

TOKEN_BUCKET = RL.Buckets(rate=60, per=60)        # calls per token per minute
FAIL_BUCKET = RL.Buckets(rate=10, per=600)        # failed authentications per IP (10 per 10 min)
FAIL_BLOCK_S = 600
AUDIT_KEEP = 1000
AUDIT_SHOW = 100

_NOT_FOUND = {"detail": "Not Found"}               # FastAPI's own 404 body: the endpoints don't show


# ---------------------------------------------------------------------------
# PIN
# ---------------------------------------------------------------------------

def _pin() -> Optional[str]:
    v = (os.environ.get(PIN_ENV) or "").strip()
    return v if len(v) >= PIN_MIN_LEN else None


def pin_configured() -> bool:
    return _pin() is not None


_PIN_STATE = {"fails": 0, "locked_until": 0.0}
_PIN_LOCK = threading.Lock()


class PinError(Exception):
    def __init__(self, code: str, retry_s: int = 0):
        super().__init__(code)
        self.code, self.retry_s = code, retry_s


def pin_locked_for() -> int:
    """Seconds the PIN stays locked (0 = not locked)."""
    with _PIN_LOCK:
        left = _PIN_STATE["locked_until"] - RL.clock()
    return max(0, int(left + 0.999)) if left > 0 else 0


def check_pin(pin: Optional[str]) -> None:
    """Raises PinError(no_pin | locked | wrong); returns when the PIN is right."""
    want = _pin()
    if want is None:
        raise PinError("no_pin")
    with _PIN_LOCK:
        now = RL.clock()
        if _PIN_STATE["locked_until"] > now:
            raise PinError("locked", int(_PIN_STATE["locked_until"] - now + 0.999))
        if _PIN_STATE["locked_until"]:                 # a lock that ran out: a fresh count
            _PIN_STATE.update(fails=0, locked_until=0.0)
        got = hashlib.sha256(str(pin or "").encode("utf-8")).digest()
        ok = hmac.compare_digest(got, hashlib.sha256(want.encode("utf-8")).digest())
        if ok:
            _PIN_STATE["fails"] = 0
            return
        _PIN_STATE["fails"] += 1
        if _PIN_STATE["fails"] >= PIN_MAX_FAILS:
            _PIN_STATE["locked_until"] = now + PIN_LOCK_S
            raise PinError("locked", PIN_LOCK_S)
        raise PinError("wrong")


def reset_limits() -> None:
    """Tests: forget the PIN failures and the rate-limit buckets."""
    with _PIN_LOCK:
        _PIN_STATE.update(fails=0, locked_until=0.0)
    TOKEN_BUCKET.reset()
    FAIL_BUCKET.reset()
    _BLOCKED.clear()


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _iso(t: Optional[dt.datetime]) -> Optional[str]:
    return None if t is None else t.replace(microsecond=0).isoformat() + "Z"


def clean_scopes(scopes) -> list[str]:
    if scopes is None:
        return list(DEFAULT_SCOPES)
    if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes):
        raise ValueError(f"scopes must be a non-empty list of {list(SCOPES)}")
    return [s for s in SCOPES if s in scopes]


def token_view(r: DebugToken, now: Optional[dt.datetime] = None) -> dict:
    now = now or _now()
    return {"id": r.id, "name": r.name, "prefix": r.prefix, "scopes": json.loads(r.scopes_json or "[]"),
            "created_at": _iso(r.created_at), "expires_at": _iso(r.expires_at),
            "expired": r.expires_at is not None and r.expires_at <= now, "revoked_at": _iso(r.revoked_at),
            "active": r.revoked_at is None and (r.expires_at is None or r.expires_at > now),
            "last_used_at": _iso(r.last_used_at), "last_ip": r.last_ip, "new_ip_at": _iso(r.new_ip_at)}


async def create_token(db: AsyncSession, name: str, scopes, days) -> tuple[str, dict]:
    """(the token — shown once —, its row view). The PIN is checked by the caller."""
    name = str(name or "").strip()[:NAME_MAX] or "agent"
    sc = clean_scopes(scopes)
    try:
        days = int(DEFAULT_DAYS if days in (None, "") else days)
    except (TypeError, ValueError):
        raise ValueError(f"days must be one of {list(DAYS)}")
    if days not in DAYS:
        raise ValueError(f"days must be one of {list(DAYS)}")
    t = tenancy.current().id
    now = _now()
    n = (await db.execute(select(func.count(DebugToken.id)).where(
        DebugToken.tenant_id == t, DebugToken.revoked_at.is_(None), DebugToken.expires_at > now))).scalar_one()
    if n >= MAX_ACTIVE:
        raise ValueError(f"at most {MAX_ACTIVE} active tokens: revoke one first")
    raw = _secrets.token_urlsafe(32)
    token = TOKEN_PREFIX + raw
    r = DebugToken(tenant_id=t, name=name, prefix=TOKEN_PREFIX + raw[:4], token_hash=hash_token(token),
                   scopes_json=json.dumps(sc), created_at=now, expires_at=now + dt.timedelta(days=days),
                   ips_json="[]")
    db.add(r)
    await db.commit()
    return token, token_view(r, now)


async def list_tokens(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(DebugToken).where(DebugToken.tenant_id == tenancy.current().id)
                             .order_by(DebugToken.id.desc()))).scalars().all()
    now = _now()
    return [token_view(r, now) for r in rows]


async def revoke(db: AsyncSession, token_id: Optional[int] = None) -> int:
    """Revoke one token (`token_id`) or every active one; how many were revoked."""
    q = update(DebugToken).where(DebugToken.tenant_id == tenancy.current().id, DebugToken.revoked_at.is_(None))
    if token_id is not None:
        q = q.where(DebugToken.id == token_id)
    res = await db.execute(q.values(revoked_at=_now()))
    await db.commit()
    return int(res.rowcount or 0)


async def enabled(db: AsyncSession) -> bool:
    from backend.settings.repository import SettingsRepository
    try:
        return bool(await SettingsRepository(db).get(ENABLED_KEY))
    except Exception:                       # noqa: BLE001 — no table in an old DB: off
        return False


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

async def audit(db: AsyncSession, ctx: "Ctx", status: int, size: int = 0) -> None:
    t = tenancy.current().id
    ms = int((time.monotonic() - ctx.started) * 1000)
    db.add(DebugAudit(tenant_id=t, at=_now(), token_id=ctx.token_id, token_name=ctx.name, method=ctx.method,
                      path=ctx.path[:200], query=(ctx.query or None) and ctx.query[:500], ip=ctx.ip[:64],
                      status=int(status), bytes=int(size), ms=ms, new_ip=bool(ctx.new_ip)))
    await db.flush()
    top = (await db.execute(select(func.max(DebugAudit.id)))).scalar_one() or 0
    if top > AUDIT_KEEP:
        await db.execute(delete(DebugAudit).where(DebugAudit.id <= top - AUDIT_KEEP))
    await db.commit()


async def audit_rows(db: AsyncSession, limit: int = AUDIT_SHOW) -> list[dict]:
    rows = (await db.execute(select(DebugAudit).where(DebugAudit.tenant_id == tenancy.current().id)
                             .order_by(DebugAudit.id.desc()).limit(limit))).scalars().all()
    return [{"at": _iso(r.at), "token": r.token_name, "token_id": r.token_id, "method": r.method,
             "path": r.path, "query": r.query, "ip": r.ip, "status": r.status, "bytes": r.bytes, "ms": r.ms,
             "new_ip": bool(r.new_ip)} for r in rows]


# ---------------------------------------------------------------------------
# the request gate (a FastAPI dependency of every /api/v1/debug/* route)
# ---------------------------------------------------------------------------

_BLOCKED: dict = {}            # ip -> monotonic time the block ends


@dataclass
class Ctx:
    path: str
    query: str
    ip: str
    method: str = "GET"
    token_id: Optional[int] = None
    name: Optional[str] = None
    scopes: list = field(default_factory=list)
    new_ip: bool = False
    gps: bool = False
    started: float = field(default_factory=time.monotonic)


def _blocked(ip: str) -> bool:
    until = _BLOCKED.get(ip)
    if until is None:
        return False
    if until > RL.clock():
        return True
    _BLOCKED.pop(ip, None)
    return False


def _bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or ""
    kind, _, val = h.partition(" ")
    if kind.lower() != "bearer":
        return None
    val = val.strip()
    return val if val.startswith(TOKEN_PREFIX) and 20 <= len(val) <= 200 else None


def _err(status: int, code: str, headers: Optional[dict] = None) -> HTTPException:
    return HTTPException(status, {"code": code}, headers=headers)


async def _fail(db: AsyncSession, ctx: Ctx, status: int, code: str) -> HTTPException:
    """A failed authentication: counted against the source IP, written to the audit log."""
    if not FAIL_BUCKET.take(ctx.ip):
        _BLOCKED[ctx.ip] = RL.clock() + FAIL_BLOCK_S
    await audit(db, ctx, status)
    return _err(status, code, {"WWW-Authenticate": "Bearer"} if status == 401 else None)


async def gate(request: Request, db: AsyncSession = Depends(get_db)) -> Ctx:
    """404 unless enabled (and never in the demo); then the Bearer token: 401 missing / unknown /
    revoked / expired / another tenant's, 429 over the limits. Returns the call's context."""
    if tenancy.demo_mode():
        raise HTTPException(404, _NOT_FOUND["detail"])
    t = tenancy.current()
    if t.is_demo or not pin_configured() or not await enabled(db):
        raise HTTPException(404, _NOT_FOUND["detail"])
    ctx = Ctx(path=request.url.path, query=request.url.query, ip=RL.client_ip(request.scope),
              method=request.method,
              gps=request.query_params.get("gps") in ("1", "true", "yes"))
    if _blocked(ctx.ip):
        await audit(db, ctx, 429)
        raise _err(429, "BLOCKED", {"Retry-After": str(FAIL_BLOCK_S)})
    tok = _bearer(request)
    if tok is None:
        raise await _fail(db, ctx, 401, "TOKEN_MISSING")
    r = (await db.execute(select(DebugToken).where(DebugToken.token_hash == hash_token(tok)))).scalar_one_or_none()
    now = _now()
    if r is None or r.tenant_id != t.id:
        raise await _fail(db, ctx, 401, "TOKEN_INVALID")
    ctx.token_id, ctx.name, ctx.scopes = r.id, r.name, json.loads(r.scopes_json or "[]")
    if r.revoked_at is not None:
        raise await _fail(db, ctx, 401, "TOKEN_REVOKED")
    if r.expires_at is None or r.expires_at <= now:
        raise await _fail(db, ctx, 401, "TOKEN_EXPIRED")
    if not TOKEN_BUCKET.take(f"{t.id}:{r.id}"):
        await audit(db, ctx, 429)
        raise _err(429, "RATE_LIMITED", {"Retry-After": "60"})
    ips = json.loads(r.ips_json or "[]")
    if ctx.ip not in ips:
        ctx.new_ip = True
        ips = (ips + [ctx.ip])[-IPS_KEEP:]
        r.ips_json = json.dumps(ips)
        r.new_ip_at = now
    r.last_used_at, r.last_ip = now, ctx.ip
    await db.commit()
    return ctx


def need(*scopes: str):
    """A dependency: the token must hold every one of `scopes` (403 otherwise)."""
    async def dep(ctx: Ctx = Depends(gate), db: AsyncSession = Depends(get_db)) -> Ctx:
        if not all(s in ctx.scopes for s in scopes):
            await audit(db, ctx, 403)
            raise _err(403, "SCOPE", None)
        return ctx
    return dep


def need_any(*scopes: str):
    """A dependency: the token must hold at least one of `scopes`."""
    async def dep(ctx: Ctx = Depends(gate), db: AsyncSession = Depends(get_db)) -> Ctx:
        if not any(s in ctx.scopes for s in scopes):
            await audit(db, ctx, 403)
            raise _err(403, "SCOPE", None)
        return ctx
    return dep
