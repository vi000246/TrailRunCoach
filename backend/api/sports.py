from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.db.models import WorkoutFile

router = APIRouter(prefix="/api/v1/sports", tags=["sports"])

SPORT_LABELS = {
    "running": "跑步",
    "cycling": "單車",
    "swimming": "游泳",
    "walking": "健走",
}


@router.get("/facets")
async def facets(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Return the sports this athlete actually has, with activity counts.

    Drives the overview page's dynamic multi-select filter.
    """
    q = (
        select(WorkoutFile.sport, func.count(WorkoutFile.id))
        .where(WorkoutFile.athlete_id == athlete_id)
        .group_by(WorkoutFile.sport)
        .order_by(func.count(WorkoutFile.id).desc())
    )
    rows = (await db.execute(q)).all()
    return {
        "sports": [
            {
                "key": sport or "unknown",
                "count": count,
                "label": SPORT_LABELS.get(sport, sport or "其他"),
            }
            for sport, count in rows
        ]
    }
