"""
Per-athlete calibration (engine/calibrate.py): the 設定 page's 進階設定 →
「自動估算的參數」 list, its 手動指定 / 改回自動, and a fit on demand.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.current import current_athlete_id
from backend.db.database import get_db
from backend.engine import calibrate as CAL
from backend.settings.repository import SettingsRepository

router = APIRouter(prefix="/api/v1/calib", tags=["calibration"])


def _item(name: str) -> CAL.Item:
    item = CAL._registry().get(name)
    if item is None:
        raise HTTPException(404, "UNKNOWN_CALIBRATION")
    return item


@router.get("")
async def list_calibration(db: AsyncSession = Depends(get_db)):
    repo = SettingsRepository(db, current_athlete_id())
    return {"items": [CAL.describe(n, await repo.get(CAL.key(n))) for n in sorted(CAL._registry())]}


class Manual(BaseModel):
    value: float


@router.put("/{name}")
async def set_manual(name: str, body: Manual, db: AsyncSession = Depends(get_db)):
    """手動指定: stored with source = user, never overwritten by a fit."""
    item = _item(name)
    if item.bounds and not item.bounds[0] <= body.value <= item.bounds[1]:
        raise HTTPException(400, f"{item.label} 要在 {item.bounds[0]}–{item.bounds[1]} {item.unit} 之間")
    repo = SettingsRepository(db, current_athlete_id())
    prev = await repo.get(CAL.key(name)) or {}
    await repo.set(CAL.key(name), {"value": body.value, "se": None, "n": int(prev.get("n") or 0),
                                   "fitted_at": dt.date.today().isoformat(), "source": "user"})
    await db.commit()
    return CAL.describe(name, await repo.get(CAL.key(name)))


@router.delete("/{name}")
async def clear_manual(name: str, db: AsyncSession = Depends(get_db)):
    """改回自動: the manual value goes; the next fit (or the default) applies."""
    _item(name)
    repo = SettingsRepository(db, current_athlete_id())
    await repo.set(CAL.key(name), None)
    await db.commit()
    return CAL.describe(name, None)


@router.post("/run")
async def run_now(db: AsyncSession = Depends(get_db)):
    """Fit every item now (otherwise it runs after each sync that imports an activity)."""
    res = await CAL.calibrate(db, current_athlete_id())
    repo = SettingsRepository(db, current_athlete_id())
    return {**res, "items": [CAL.describe(n, await repo.get(CAL.key(n))) for n in sorted(CAL._registry())]}
