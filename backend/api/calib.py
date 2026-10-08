"""
Per-athlete calibration (engine/calibrate.py): the 設定 page's 進階設定 →
「自動估算的參數」 list, its 手動指定 / 改回自動, and a fit on demand.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.engine.localtime import today_local
from backend.db.current import current_athlete_id
from backend.db.database import get_db
from backend.engine import calibrate as CAL
from backend.i18n import _
from backend.settings.repository import SettingsRepository

router = APIRouter(prefix="/api/v1/calib", tags=["calibration"])


def _item(name: str) -> CAL.Item:
    item = CAL._registry().get(name)
    if item is None:
        raise HTTPException(404, "UNKNOWN_CALIBRATION")
    return item


def _replan(name: str) -> None:
    """A value the plan reads (the Zone 3 unlock rule, SP-295): the plan / status caches key on
    it (advanced_params.z3_rule_stamp); a background plan_auto run re-plans the stored sessions."""
    from backend.engine import advanced_params as AP
    if name in AP.Z3_RULE.values():
        from backend.engine import plan_auto as PA
        PA.after_settings()


async def _check_pair(repo: SettingsRepository, name: str, value) -> None:
    """Cross-item checks (SP-295: 重新上鎖天數 ≥ 最長幾天不跑) against the other item's value in effect:
    its stored manual value, else its default. `value` None = 改回自動 (the item's default)."""
    from backend.engine import advanced_params as AP
    if name not in AP.Z3_RULE.values():
        return
    reg = CAL._registry()
    cur = {}
    for n in AP.Z3_RULE.values():
        stored = await repo.get(CAL.key(n)) or {}
        cur[n] = stored.get("value") if stored.get("value") is not None else reg[n].default
    msg = AP.z3_pair_error(name, value, cur.__getitem__)
    if msg:
        raise HTTPException(400, msg)


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
    fmt = "{:.0f}" if item.integer else "{:g}"
    if item.bounds and not item.bounds[0] <= body.value <= item.bounds[1]:
        raise HTTPException(400, _("{label} 要在 {lo}–{hi} {unit} 之間", label=_(item.label), unit=_(item.unit),
                                   lo=fmt.format(item.bounds[0]), hi=fmt.format(item.bounds[1])))
    if item.integer and body.value != int(body.value):
        raise HTTPException(400, _("{label} 要是整數", label=_(item.label)))
    repo = SettingsRepository(db, current_athlete_id())
    await _check_pair(repo, name, body.value)
    prev = await repo.get(CAL.key(name)) or {}
    await repo.set(CAL.key(name), {"value": body.value, "se": None, "n": int(prev.get("n") or 0),
                                   "fitted_at": today_local().isoformat(), "source": "user"})
    await db.commit()
    CAL.forget_reads()
    _replan(name)
    return CAL.describe(name, await repo.get(CAL.key(name)))


@router.delete("/{name}")
async def clear_manual(name: str, db: AsyncSession = Depends(get_db)):
    """改回自動: the manual value goes; the next fit (or the default) applies."""
    _item(name)
    repo = SettingsRepository(db, current_athlete_id())
    await _check_pair(repo, name, None)
    await repo.set(CAL.key(name), None)
    await db.commit()
    CAL.forget_reads()
    _replan(name)
    return CAL.describe(name, None)


@router.post("/run")
async def run_now(db: AsyncSession = Depends(get_db)):
    """Fit every item now (otherwise it runs after each sync that imports an activity)."""
    res = await CAL.calibrate(db, current_athlete_id())
    repo = SettingsRepository(db, current_athlete_id())
    return {**res, "items": [CAL.describe(n, await repo.get(CAL.key(n))) for n in sorted(CAL._registry())]}
