"""
Week plan -> COROS: push this week's sessions to COROS Training Hub as
structured workouts on their days (backend/sync/coros_workouts.py).

  GET    /api/v1/overview/weekplan/coros              status per session (no network)
  POST   /api/v1/overview/weekplan/push-coros         whole week; ?session=<id> for one
  DELETE /api/v1/overview/weekplan/push-coros         remove what was pushed; ?session=<id>
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.db.database import get_db
from backend.sync import coros_workouts as CW

router = APIRouter(prefix="/api/v1/overview/weekplan", tags=["overview"])


async def _plan() -> dict:
    from backend.api.overview import weekplan
    return await run_in_threadpool(weekplan)


def _auth(e: CW.CorosAuthError):
    return HTTPException(401, {"error": "COROS_AUTH_REQUIRED", "detail": str(e),
                               "hint": "到設定頁重新登入 COROS"})


@router.get("/coros")
async def coros_status(db: AsyncSession = Depends(get_db)):
    return await CW.week_status(db, await _plan())


@router.post("/push-coros")
async def push_coros(session: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    plan = await _plan()
    try:
        return await CW.push_week(db, plan, only=session)
    except KeyError:
        raise HTTPException(404, f"本週沒有 session {session!r}")
    except CW.CorosAuthError as e:
        raise _auth(e)


@router.delete("/push-coros")
async def unpush_coros(session: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    plan = await _plan()
    try:
        return await CW.remove_week(db, plan["week"]["start"], only=session)
    except CW.CorosAuthError as e:
        raise _auth(e)
