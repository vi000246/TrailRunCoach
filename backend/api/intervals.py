"""
intervals.icu's two calls into the app (SP-346, sync/intervals_client.py):

  GET  /oauth/intervals/callback   back from intervals.icu's authorize page: the one-time state
                                   (made by POST /api/v1/auth/intervals/connect, bound to the tenant
                                   that asked) names the tenant; the code is exchanged there and the
                                   browser goes back to 設定 › 資料同步 with ?intervals=connected |
                                   denied | failed. A public path in sign-in mode (/oauth/*/callback,
                                   accounts/login.py): the state, not the cookie, says whose it is
  POST /api/v1/intervals/webhook   intervals.icu's webhooks (configured on the app's "Manage App"
                                   page). The payload {secret, events: [{athlete_id, type, timestamp,
                                   activity}]} is trusted only when `secret` equals
                                   TRC_INTERVALS_WEBHOOK_SECRET (compared in constant time); 401
                                   otherwise, 404 while no secret is configured. ACTIVITY_UPLOADED /
                                   ACTIVITY_ANALYZED events are queued in the tenant their athlete id
                                   maps to (intervals_limits.tenant_for) — when intervals.icu is that
                                   tenant's 資料來源 and its sync is on — and a background run downloads
                                   them; a plan without real-time sync (sync:auto_realtime) keeps them
                                   for its nightly sync. Answers 200 at once (intervals.icu retries a
                                   non-2xx). No session, no CSRF header (tenancy_mw lets this one path
                                   through with no tenant); nothing personal is logged
Neither is mounted in the demo (main.py: owner_only).
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["intervals"])

WEBHOOK_PATH = "/api/v1/intervals/webhook"
MAX_BODY = 2 * 1024 * 1024
SETTINGS = "/api/v1/app/settings"


def tenant_by_id(tid: str):
    """The Tenant of an id the webhook map / a state holds: "owner", or "u<n>" of an enabled user."""
    from backend import tenancy
    if tid == "owner":
        return tenancy.owner()
    if not (isinstance(tid, str) and tid.startswith("u") and tid[1:].isdigit()):
        return None
    from backend import accounts
    if not accounts.enabled():
        return None
    from backend.accounts import store, tenants as TN
    u = store.get_user(int(tid[1:]))
    if u is None or u.disabled:
        return None
    return TN.tenant_of(u)


def _back(result: str) -> RedirectResponse:
    return RedirectResponse(f"{SETTINGS}?intervals={result}#sync", status_code=303)


@router.get("/oauth/intervals/callback", include_in_schema=False)
async def intervals_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    from backend import tenancy
    from backend.accounts import tenants as TN
    from backend.sync import intervals_client as IC
    row = IC.pop_state(state)
    if row is None:
        return _back("expired")
    if error or not code:
        return _back("denied")
    t = tenant_by_id(row["tenant"])
    if t is None:
        return _back("failed")
    with tenancy.use(t):
        await TN.ensure_ready(t)
        from backend.db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            try:
                await IC.finish_connect(db, row["athlete_id"], code, row["redirect_uri"])
            except IC.IntervalsConnectFailed as e:
                log.warning("intervals.icu connect failed: %s", e.code)
                return _back("failed")
            except IC.IntervalsNotConfigured:
                return _back("failed")
    return _back("connected")


def _secret_ok(got) -> bool:
    from backend.sync import intervals_client as IC
    want = IC.webhook_secret()
    return bool(want) and isinstance(got, str) and hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))


@router.post(WEBHOOK_PATH, include_in_schema=False)
async def intervals_webhook(request: Request):
    from backend.sync import intervals_client as IC
    if not IC.webhook_secret():
        raise HTTPException(404, "not enabled")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "too large")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON")
    if not isinstance(payload, dict) or not _secret_ok(payload.get("secret")):
        raise HTTPException(401, "bad secret")
    by_tenant: dict = {}
    for ev in payload.get("events") or []:
        if not isinstance(ev, dict) or ev.get("type") not in IC.WEBHOOK_TYPES:
            continue
        act = ev.get("activity")
        icu = ev.get("athlete_id")
        if not isinstance(act, dict) or act.get("id") is None or not icu:
            continue
        tid = IC.IL.tenant_for(str(icu))
        if tid is not None:
            by_tenant.setdefault(tid, []).append(act)
    queued = 0
    for tid, acts in by_tenant.items():
        try:
            queued += await _queue(tid, acts)
        except Exception as e:                      # noqa: BLE001 — the other tenants still get theirs
            log.warning("intervals.icu webhook not queued for a tenant: %s", type(e).__name__)
    return JSONResponse({"ok": True, "queued": queued})


async def _queue(tid: str, acts: list) -> int:
    """Queue in that tenant (when intervals.icu is its 資料來源, connected, sync on) and start the run
    (real-time plans only)."""
    from backend import membership as MS, tenancy
    from backend.accounts import tenants as TN
    from backend.settings.repository import SettingsRepository
    from backend.sync import intervals_client as IC, primary as P
    t = tenant_by_id(tid)
    if t is None:
        return 0
    with tenancy.use(t):
        await TN.ensure_ready(t)
        from backend.db.current import current_athlete_id
        from backend.db.database import AsyncSessionLocal
        athlete_id = current_athlete_id()
        async with AsyncSessionLocal() as db:
            repo = SettingsRepository(db, athlete_id)
            if await P.current(db, athlete_id) != "intervals" or not await repo.get("sync.intervals.enabled"):
                return 0
            if not await IC.connected(db, athlete_id):
                return 0
            n = await IC.add_pending(db, athlete_id, acts)
        if MS.has("sync:auto_realtime", t) or t.id == "owner":
            IC.start_webhook_run(athlete_id)
        from backend.sync import events as EV
        EV.log(IC.SOURCE, "webhook", tenant=t.id, queued=n)
        return n
