"""
課表訂閱 — the stored plan as a calendar feed (engine/calendar_feed.py).

    GET    /api/v1/plan/calendar             {enabled, path, url}: the feed's address (設定 page)
    POST   /api/v1/plan/calendar/token       {origin?} → a new secret address (重設網址: the old one stops working)
    DELETE /api/v1/plan/calendar/token       turn the feed off
    GET    /share/calendar/<token>.ics       (public) the iCalendar feed; 404 for any other token

The feed lives under /share/ because the Cloudflare tunnel's Basic-auth proxy lets
only that prefix through without the site password, and Google Calendar / the
iPhone fetch it without logging in: the long random token is the only key. A wrong
or old token is a 404 (never 401: nothing tells a guesser the path exists). The
demo instance mounts neither router (api/main.py owner_only): a visitor never gets
a feed of the demo athlete, let alone the owner's plan.

Links in the feed (「在課表打開這堂課」) use, in order: WKO5COACH_PUBLIC_URL, the
origin the settings page was opened at when the address was made (what the user
reaches the app by), else the request's own address (X-Forwarded-Proto / -Host only
from a trusted proxy, security/ratelimit.trusted_networks).
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.current import current_athlete_id
from backend.db.database import get_db
from backend.engine import calendar_feed as CF
from backend.settings.repository import SettingsRepository

KEY = "plan.calendar"
ENV_PUBLIC_URL = "WKO5COACH_PUBLIC_URL"

router = APIRouter(prefix="/api/v1/plan/calendar", tags=["plan"])
feed_router = APIRouter(prefix="/share/calendar", tags=["share"], include_in_schema=False)
FEED_HEADERS = {"Cache-Control": "private, max-age=300", "X-Robots-Tag": "noindex, nofollow",
                "Referrer-Policy": "no-referrer",
                "Content-Disposition": 'inline; filename="trailruncoach-plan.ics"'}


def _trusted_peer(request: Request) -> bool:
    from backend.security.ratelimit import trusted_networks
    try:
        addr = ipaddress.ip_address((request.client.host if request.client else "") or "")
    except ValueError:
        return False
    return any(addr in n for n in trusted_networks())


def request_origin(request: Request) -> str:
    """scheme://host the client used: X-Forwarded-Proto / -Host only from a trusted proxy."""
    scheme, host = request.url.scheme, request.headers.get("host") or request.url.netloc
    if _trusted_peer(request):
        fp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        fh = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        if fp in ("http", "https"):
            scheme = fp
        if fh:
            host = fh
    return CF.clean_origin(f"{scheme}://{host}") or str(request.base_url).rstrip("/")


def public_base(request: Request, stored) -> str:
    env = CF.clean_origin(os.environ.get(ENV_PUBLIC_URL))
    if env:
        return env
    if isinstance(stored, dict) and stored.get("origin"):
        return stored["origin"]
    return request_origin(request)


def _body(request: Request, stored) -> dict:
    if not isinstance(stored, dict) or not stored.get("token"):
        return {"enabled": False, "path": None, "url": None}
    path = CF.feed_path(stored["token"])
    return {"enabled": True, "path": path, "url": public_base(request, stored) + path,
            "window": {"past_days": CF.WINDOW_PAST, "future_days": CF.WINDOW_FUTURE}}


@router.get("")
async def get_calendar(request: Request, db: AsyncSession = Depends(get_db)):
    return _body(request, await SettingsRepository(db).get(KEY))


@router.post("/token")
async def new_token(request: Request, body: Optional[dict] = Body(None), db: AsyncSession = Depends(get_db)):
    """A new secret address; the previous one (if any) is a 404 from now on."""
    origin = CF.clean_origin((body or {}).get("origin"))
    repo = SettingsRepository(db)
    value = {"token": CF.new_token(), "origin": origin}
    await repo.set(KEY, value)
    await db.commit()
    return _body(request, value)


@router.delete("/token")
async def delete_token(request: Request, db: AsyncSession = Depends(get_db)):
    await SettingsRepository(db).set(KEY, None)
    await db.commit()
    return _body(request, None)


async def _rows(db: AsyncSession, today: dt.date) -> list[tuple[dict, Optional[dt.datetime]]]:
    from backend.db.models import PlanSession
    from backend.engine import plan_store as PS
    lo = (today - dt.timedelta(days=CF.WINDOW_PAST)).isoformat()
    hi = (today + dt.timedelta(days=CF.WINDOW_FUTURE)).isoformat()
    res = await db.execute(select(PlanSession).where(
        PlanSession.athlete_id == current_athlete_id(), PlanSession.day >= lo, PlanSession.day <= hi,
        PlanSession.state.in_(CF.STATES)))
    return [(PS.to_dict(r), r.updated_at) for r in res.scalars().all()]


@feed_router.api_route("/{token}.ics", methods=["GET", "HEAD"])
async def feed(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    stored = await SettingsRepository(db).get(KEY)
    if not CF.token_ok(token, stored):
        raise HTTPException(404, "Not Found")
    from backend.engine.localtime import today_local
    today = today_local()
    text = CF.build(await _rows(db, today), today, public_base(request, stored))
    return Response(content=text.encode("utf-8"), media_type="text/calendar; charset=utf-8", headers=FEED_HEADERS)
