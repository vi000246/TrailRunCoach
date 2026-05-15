from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date
from typing import Optional

from backend.db.database import get_db
from backend.db.models import WorkoutFile, WorkoutMetric

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/weekly")
async def weekly_load(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    tss_subq = (
        select(func.coalesce(func.sum(WorkoutMetric.value), 0))
        .where(WorkoutMetric.workout_id == WorkoutFile.id)
        .where(WorkoutMetric.metric_key == "tss")
        .correlate(WorkoutFile)
        .scalar_subquery()
    )

    q = (
        select(
            func.strftime("%Y-%W", WorkoutFile.workout_date).label("week_key"),
            func.min(WorkoutFile.workout_date).label("week_start"),
            func.sum(tss_subq).label("tss"),
            (func.sum(WorkoutFile.duration_s) / 3600.0).label("hours"),
            func.count(WorkoutFile.id).label("count"),
        )
        .where(WorkoutFile.athlete_id == athlete_id)
    )
    if date_from:
        q = q.where(WorkoutFile.workout_date >= date_from)
    if date_to:
        q = q.where(WorkoutFile.workout_date <= date_to)

    q = q.group_by("week_key").order_by("week_key")
    result = await db.execute(q)
    rows = result.all()

    return {
        "weeks": [
            {
                "week_start": row.week_start.isoformat() if row.week_start else None,
                "tss": round(float(row.tss or 0)),
                "hours": round(float(row.hours or 0), 1),
                "count": row.count,
            }
            for row in rows
        ]
    }
