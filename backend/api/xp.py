"""
TrailRunCoach × 疊石 Cairn (research SP-571, docs/research/sp571-cross-promotion.md):

  GET  /api/v1/app/xp/cairn            the Cairn card (SP-763): show it or not, and why not —
                                       {show, why: off | lean | linked | null, off, linked, url}
  POST /api/v1/app/xp/cairn/dismiss    the card's 「不用了」: off for both places (ui.xp_cairn_off, the
                                       tenant's settings — every device), counted xp-card-dismiss
  PUT  /api/v1/app/xp/cairn            {off}: 設定 › 提示多寡's 「重新顯示」 (or off again); no count
  POST /api/v1/app/xp/event            {event: xp-card-shown | xp-card-click}: one count (SP-765)
  POST /api/v1/app/xp/cairn/route      {url}: a Cairn share link brought here (SP-764: Cairn's
                                       「用 TrailRunCoach 算這條」 opens the calculator with ?cairn=<link>) —
                                       the trip's name, start date and days to fill 賽事管理's 新增賽事 with,
                                       or the race that already holds this route ({event_id})

The card is an introduction, not a tip that changes what to do (SP-571 §3.2): hidden for 提示多寡
「精簡」 (SP-482), once 「不用了」 was pressed, and for an account linked with Cairn (SP-329: there only
the links stay). Never in the demo (not mounted: no account to remember 「不用了」 on). The events are
counted for a signed-in user only (accounts/xp.py); owner mode shows the card and counts nothing.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.accounts import xp as XP
from backend.db.current import current_athlete_id
from backend.db.database import get_db

router = APIRouter(prefix="/api/v1/app/xp", tags=["xp"])

OFF_KEY = "ui.xp_cairn_off"
LEVEL_KEY = "ui.hint_level"


def _user():
    from backend.accounts import login as LG
    return LG.current_user()


def _linked(u) -> bool:
    if u is None:
        return False
    from backend.accounts import links as LK
    try:
        return LK.is_linked(u, LK.CAIRN)
    except Exception:                      # noqa: BLE001 — accounts.db unreadable: treat as not linked
        return False


def cairn_url() -> str:
    """Cairn's front page, marked as coming from here (Cairn's SP-768 / 769 read from=trc)."""
    from backend.engine import cairn as CA
    return CA.origin() + "/?from=trc"


def card_state(off: bool, level: Optional[str], linked: bool) -> dict:
    why = "linked" if linked else "off" if off else "lean" if level == "lean" else None
    return {"show": why is None, "why": why, "off": bool(off), "linked": bool(linked), "url": cairn_url()}


@router.get("/cairn")
async def get_card():
    from backend.engine.wko5expr.datasource import read_setting
    u = _user()
    linked = await run_in_threadpool(_linked, u)
    return card_state(bool(read_setting(OFF_KEY, False)), read_setting(LEVEL_KEY, None), linked)


async def _set_off(db: AsyncSession, off: bool) -> dict:
    from backend.engine.wko5expr.datasource import read_setting
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, current_athlete_id())
    await repo.set(OFF_KEY, bool(off))
    await db.commit()
    u = _user()
    return card_state(off, read_setting(LEVEL_KEY, None), await run_in_threadpool(_linked, u))


@router.post("/cairn/dismiss")
async def dismiss(db: AsyncSession = Depends(get_db)):
    out = await _set_off(db, True)
    await run_in_threadpool(XP.record, _user(), XP.DISMISS)
    return out


class OffIn(BaseModel):
    off: StrictBool


@router.put("/cairn")
async def put_card(body: OffIn, db: AsyncSession = Depends(get_db)):
    return await _set_off(db, body.off)


class EventIn(BaseModel):
    event: str = Field("", max_length=40)


@router.post("/event")
async def post_event(body: EventIn):
    # 「不用了」 counts through /cairn/dismiss, xp-linked when the link is made (api/link.py)
    if body.event not in (XP.SHOWN, XP.CLICK):
        raise HTTPException(400, f"unknown event {body.event!r}")
    return {"recorded": await run_in_threadpool(XP.record, _user(), body.event)}


class RouteIn(BaseModel):
    url: str = Field("", max_length=2000)


_STATUS = {"link": 400, "gone": 410, "unreachable": 502, "bad": 502}


def _event_with(gpx_id: str) -> Optional[str]:
    """The season plan's race whose GPX was imported from this Cairn route (SP-537), if any."""
    from backend.engine import event_gpx as EG
    from backend.engine import planning as P
    ids = {e.id for e in P.Plan.load().events}
    for eid, row in EG.all_rows().items():
        src = (row or {}).get("source") or {}
        if eid in ids and src.get("kind") == "cairn" and src.get("gpx_id") == gpx_id:
            return eid
    return None


@router.post("/cairn/route")
async def cairn_route(body: RouteIn):
    """What 賽事管理 fills 新增賽事 with for a Cairn link brought here (nothing saved, the token not kept)."""
    from backend.engine import cairn as CA
    try:
        imp = await run_in_threadpool(CA.import_link, body.url)
    except CA.CairnError as x:
        raise HTTPException(_STATUS.get(x.kind, 400), str(x))
    eid = await run_in_threadpool(_event_with, imp["gpx_id"])
    return {"name": imp["name"], "start_date": imp["start_date"], "days": imp["days"],
            "nights": len(imp["nights"]), "event_id": eid, "has_records": await run_in_threadpool(_has_records)}


def _has_records() -> Optional[bool]:
    """Any activity yet (the calculator then says the first estimate is from the typed-in fitness);
    None when the data can't be read (nothing is said then)."""
    try:
        from backend.api.wko5views import _dataset
        from backend.engine import experience as EX
        from backend.engine.localtime import today_local
        return any(True for _ in EX.foot_days(_dataset(), today_local()))      # a run or a hike recorded
    except Exception:                      # noqa: BLE001 — no data folder yet, a core-less build
        return None
