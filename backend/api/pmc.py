from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, timedelta
from typing import Optional

from backend.db.database import get_db
from backend.db.models import WorkoutMetric, WorkoutFile
from backend.engine.algorithms.metrics import compute_pmc

router = APIRouter(prefix="/api/v1/pmc", tags=["pmc"])


@router.get("")
async def get_pmc(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    q = (
        select(WorkoutFile.workout_date, WorkoutMetric.value)
        .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutMetric.metric_key == "tss",
            WorkoutFile.workout_date.isnot(None),
        )
    )
    result = await db.execute(q)
    tss_rows = result.all()

    tss_by_date: dict[date, float] = {}
    for d, v in tss_rows:
        if v and d:
            tss_by_date[d] = tss_by_date.get(d, 0.0) + v

    tss_series = sorted(tss_by_date.items())
    pmc_data = compute_pmc(tss_series)

    filtered = [p for p in pmc_data if date_from.isoformat() <= p["date"] <= date_to.isoformat()]
    return {"series": filtered, "athlete_id": athlete_id}
