"""
睡在高處的紀錄 (SP-259; engine/altitude.py NIGHTS_KEY): the 課表 calendar's context menu on a
day (today or before) records 「這一晚睡在 X m」 — the night from that evening to the next
morning — so the 高度適應提醒 counts a night the activities can't see (a drive up to 松雪樓, the
climb the next day). No symptoms (owner 2026-10-06).

  GET    /api/v1/overview/plan/high-nights                  {nights: [{day, m}]}
  PUT    /api/v1/overview/plan/high-nights/{day}  {m}       set that night → {nights}
  DELETE /api/v1/overview/plan/high-nights/{day}            remove it → {nights}
  GET    /api/v1/overview/plan/high-nights/{day}/prefill    {m, source} from a plan event's GPX night
                                                            (its camp), {m: null} without one
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.db.database import get_db
from backend.engine import altitude as AL
from backend.i18n import _

router = APIRouter(prefix="/api/v1/overview/plan", tags=["overview"])


def _day(day: str) -> str:
    try:
        d = dt.date.fromisoformat(str(day or "")[:10])
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    return d.isoformat()


def _today() -> str:
    from backend.engine.localtime import today_local
    return today_local().isoformat()


async def _nights(db: AsyncSession) -> list:
    from backend.settings.repository import SettingsRepository
    return list(await SettingsRepository(db).get(AL.NIGHTS_KEY) or [])


async def _save(db: AsyncSession, nights: list) -> dict:
    from backend.settings.repository import SettingsRepository
    try:
        await SettingsRepository(db).set(AL.NIGHTS_KEY, nights)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.commit()
    return {"nights": nights}


@router.get("/high-nights")
async def get_nights(db: AsyncSession = Depends(get_db)):
    return {"nights": await _nights(db)}


@router.put("/high-nights/{day}")
async def put_night(day: str, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    day = _day(day)
    if day > _today():
        raise HTTPException(400, _("還沒到的晚上不能記：到了那天或之後再記"))
    try:
        m = AL.clean_m((body or {}).get("m"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await _save(db, AL.set_night(await _nights(db), day, m))


@router.delete("/high-nights/{day}")
async def delete_night(day: str, db: AsyncSession = Depends(get_db)):
    day = _day(day)
    cur = await _nights(db)
    if not any(e.get("day") == day for e in cur):
        raise HTTPException(404, _("這天沒有睡在高處的紀錄"))
    return await _save(db, AL.set_night(cur, day, None))


def _prefill(day: str) -> dict:
    from backend.engine.planning import Plan
    try:
        got = AL.event_night(Plan.load().events, dt.date.fromisoformat(day),
                             lambda e: AL.event_altitude(e.id, e.days))
    except Exception:                       # noqa: BLE001 — only a convenience for the dialog
        got = None
    if not got:
        return {"m": None, "source": None}
    return {"m": got["m"], "source": _("「{name}」第 {n} 晚的營地（賽事 GPX）", name=got["name"], n=got["night"])}


@router.get("/high-nights/{day}/prefill")
async def prefill(day: str):
    return await run_in_threadpool(_prefill, _day(day))
