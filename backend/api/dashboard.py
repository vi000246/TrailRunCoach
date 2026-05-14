import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from backend.db.database import get_db
from backend.db.models import DashboardConfig

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

DEFAULT_LAYOUT = {
    "version": 1,
    "name": "Default",
    "widgets": [
        {
            "id": "w1", "type": "pmc_chart",
            "position": {"col": 0, "row": 0, "w": 12, "h": 4},
            "config": {"show_ctl": True, "show_atl": True, "show_tsb": True, "date_range": "ytd"},
        },
        {
            "id": "w2", "type": "workout_list",
            "position": {"col": 0, "row": 4, "w": 12, "h": 3},
            "config": {"last_n": 10},
        },
    ],
}


@router.get("/default/{athlete_id}")
async def get_default_dashboard(athlete_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DashboardConfig).where(
            DashboardConfig.athlete_id == athlete_id,
            DashboardConfig.is_default == True,
        )
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        return {"id": None, "name": "Default", "layout": DEFAULT_LAYOUT}
    return {"id": cfg.id, "name": cfg.name, "layout": json.loads(cfg.layout_json)}


@router.get("/{config_id}")
async def get_dashboard(config_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DashboardConfig).where(DashboardConfig.id == config_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(404, "DASHBOARD_NOT_FOUND")
    return {"id": cfg.id, "name": cfg.name, "layout": json.loads(cfg.layout_json)}


class DashboardSave(BaseModel):
    name: str
    layout: dict
    is_default: bool = False


@router.put("/{config_id}")
async def save_dashboard(config_id: int, body: DashboardSave, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DashboardConfig).where(DashboardConfig.id == config_id))
    cfg = result.scalar_one_or_none()
    if cfg:
        cfg.name = body.name
        cfg.layout_json = json.dumps(body.layout)
        cfg.is_default = body.is_default
    else:
        cfg = DashboardConfig(
            id=config_id, athlete_id=1, name=body.name,
            layout_json=json.dumps(body.layout), is_default=body.is_default,
        )
        db.add(cfg)
    await db.commit()
    return {"id": config_id, "saved": True}
