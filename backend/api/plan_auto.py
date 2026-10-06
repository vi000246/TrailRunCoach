"""
自動調整課表 (engine/plan_auto.py): settings, the change log, the held
proposal and 復原.

  GET  /api/v1/overview/plan/auto                    settings + pending proposal + last N log entries
  PUT  /api/v1/overview/plan/auto/settings           {enabled, push, push_days, confirm_big, notify, rpe_rule}
  POST /api/v1/overview/plan/auto/run                run now (ignores the data stamp; big changes still held)
  POST /api/v1/overview/plan/auto/proposal/{id}/approve
  POST /api/v1/overview/plan/auto/proposal/{id}/reject
  POST /api/v1/overview/plan/auto/log/{id}/undo      restore the sessions before that run, re-push
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.engine import plan_auto as PA

router = APIRouter(prefix="/api/v1/overview/plan/auto", tags=["overview"])

THRESHOLDS = {"big_tss_up": PA.BIG_TSS_UP, "race_guard_days": PA.RACE_GUARD_DAYS, "max_changed": PA.MAX_CHANGED,
              "texts": PA.BIG_TEXT}


@router.get("")
async def get_auto(limit: int = 10, db: AsyncSession = Depends(get_db)):
    cfg = await PA.settings(db)
    cfg.pop("state", None)
    p = await PA.pending(db)
    return {"settings": cfg, "pending": PA.entry_dict(p) if p is not None else None,
            "log": await PA.entries(db, max(1, min(50, limit))), "thresholds": THRESHOLDS}


@router.put("/settings")
async def put_settings(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.settings.repository import AUTO_KEYS, SettingsRepository
    repo = SettingsRepository(db)
    try:
        for k in AUTO_KEYS:
            short = k.split(".")[-1]
            if short in body:
                await repo.set(k, body[short])
    except (ValueError, TypeError) as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.commit()
    cfg = await PA.settings(db)
    cfg.pop("state", None)
    return {"settings": cfg}


@router.post("/run")
async def run_now(db: AsyncSession = Depends(get_db)):
    # stamp ignored, but a big change is still held for approval: force=False + no stamp
    cfg = await PA.settings(db)
    st = dict(cfg["state"] or {})
    st.pop("stamp", None)
    await PA._set_state(db, st)
    res = await PA.run(db, trigger="manual")
    res.pop("items", None)
    return res


def _bad(e: Exception):
    return HTTPException(400, str(e))


@router.post("/proposal/{entry_id}/approve")
async def approve(entry_id: int, db: AsyncSession = Depends(get_db)):
    try:
        res = await PA.approve(db, entry_id)
    except ValueError as e:
        raise _bad(e)
    res.pop("items", None)
    return res


@router.post("/proposal/{entry_id}/reject")
async def reject(entry_id: int, db: AsyncSession = Depends(get_db)):
    try:
        return await PA.reject(db, entry_id)
    except ValueError as e:
        raise _bad(e)


@router.post("/log/{entry_id}/undo")
async def undo(entry_id: int, db: AsyncSession = Depends(get_db)):
    try:
        return await PA.undo(db, entry_id)
    except ValueError as e:
        raise _bad(e)
