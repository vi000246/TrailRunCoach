from pathlib import Path
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from datetime import date

from backend.db.database import get_db
from backend.db.models import Athlete, AthleteSettings

router = APIRouter(prefix="/api/v1/athletes", tags=["athletes"])

WKO5_ROOT = Path.home() / "WKO5"


@router.get("")
async def list_athletes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Athlete))
    athletes = result.scalars().all()
    return [{"id": a.id, "name": a.name, "data_dir": a.data_dir} for a in athletes]


@router.post("/bootstrap")
async def bootstrap_athletes(db: AsyncSession = Depends(get_db)):
    """Auto-detect athlete directories in ~/WKO5/ and create DB records."""
    created = []
    if not WKO5_ROOT.exists():
        return {"created": [], "error": f"~/WKO5 not found at {WKO5_ROOT}"}
    for item in WKO5_ROOT.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        if item.name in ("Views", "Chart History", "Smart Segments"):
            continue
        year_dirs = [d for d in item.iterdir() if d.is_dir() and d.name.isdigit()]
        if not year_dirs:
            continue
        existing = await db.execute(select(Athlete).where(Athlete.name == item.name))
        if existing.scalar_one_or_none():
            continue
        athlete = Athlete(name=item.name, data_dir=str(item))
        db.add(athlete)
        created.append(item.name)
    await db.commit()
    return {"created": created}


class SettingsUpdate(BaseModel):
    ftp_w: Optional[float] = None
    weight_kg: Optional[float] = None
    effective_date: Optional[date] = None


@router.put("/{athlete_id}/settings")
async def update_settings(athlete_id: int, body: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    eff_date = body.effective_date or date.today()
    result = await db.execute(
        select(AthleteSettings).where(
            AthleteSettings.athlete_id == athlete_id,
            AthleteSettings.effective_date == eff_date,
        )
    )
    s = result.scalar_one_or_none()
    if s:
        if body.ftp_w is not None:
            s.ftp_w = body.ftp_w
        if body.weight_kg is not None:
            s.weight_kg = body.weight_kg
    else:
        s = AthleteSettings(
            athlete_id=athlete_id, effective_date=eff_date,
            ftp_w=body.ftp_w, weight_kg=body.weight_kg,
        )
        db.add(s)
    await db.commit()
    return {"saved": True}
