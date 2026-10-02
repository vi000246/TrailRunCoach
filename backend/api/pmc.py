from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import date, timedelta
from typing import Optional

from backend.engine.localtime import today_local
from backend.db.database import get_db
from backend.db.models import WorkoutMetric, WorkoutFile, AthleteSettings
from backend.engine.algorithms.metrics import compute_pmc
from backend.api.analytics import _sport_clause
from backend.sync.dedup import in_use

router = APIRouter(prefix="/api/v1/pmc", tags=["pmc"])


@router.get("")
async def get_pmc(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    sports: Optional[list[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if date_to is None:
        date_to = today_local()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    q = (
        select(WorkoutFile.workout_date, WorkoutMetric.value)
        .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutMetric.metric_key == "tss",
            _sport_clause(sports, await in_use(db, athlete_id)),
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


@router.post("/recompute")
async def recompute_tss(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Recompute TSS for all workouts using current FTP (for workouts that have NP)."""
    # Get current FTP
    settings_q = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = settings_q.scalars().first()
    ftp = settings.ftp_w if settings else None
    if not ftp:
        return {"error": "No FTP set", "updated": 0}

    # Get all workouts for this athlete with NP but no TSS
    wf_q = await db.execute(
        select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id)
    )
    workouts = wf_q.scalars().all()

    updated = 0
    for wf in workouts:
        # Load existing metrics for this workout
        metrics_q = await db.execute(
            select(WorkoutMetric).where(WorkoutMetric.workout_id == wf.id)
        )
        metrics = {m.metric_key: m.value for m in metrics_q.scalars().all()}

        if "tss" in metrics:
            continue
        np = metrics.get("normalized_power_w") or metrics.get("avg_power_w")
        dur = metrics.get("duration_s") or wf.duration_s
        if not np or not dur:
            continue

        tss = (np / ftp) ** 2 * (dur / 3600) * 100
        db.add(WorkoutMetric(workout_id=wf.id, metric_key="tss", value=round(tss, 1)))
        updated += 1

    await db.commit()
    return {"updated": updated, "ftp_w": ftp}
