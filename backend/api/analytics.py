from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date, timedelta
from typing import Optional

from backend.db.database import get_db
from backend.db.models import WorkoutFile, WorkoutMetric
from backend.engine.algorithms.metrics import compute_run_pmc, compute_intensity_load_series

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


@router.get("/run-load")
async def run_load(
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
            WorkoutFile.sport == "running",
            WorkoutFile.workout_date.isnot(None),
        )
    )
    result = await db.execute(q)
    rows = result.all()
    tss_by_date: dict[date, float] = {}
    for d, v in rows:
        if v and d:
            tss_by_date[d] = tss_by_date.get(d, 0.0) + v

    run_series = sorted(tss_by_date.items())
    pmc_data = compute_run_pmc(run_series)
    filtered = [p for p in pmc_data if date_from.isoformat() <= p["date"] <= date_to.isoformat()]
    return {"series": filtered, "athlete_id": athlete_id}


@router.get("/intensity-load")
async def intensity_load(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    async def _get_series(metric_key: str) -> list[tuple[date, float]]:
        q = (
            select(WorkoutFile.workout_date, WorkoutMetric.value)
            .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
            .where(
                WorkoutFile.athlete_id == athlete_id,
                WorkoutMetric.metric_key == metric_key,
                WorkoutFile.sport == "running",
                WorkoutFile.workout_date.isnot(None),
            )
        )
        rows = (await db.execute(q)).all()
        by_date: dict[date, float] = {}
        for d, v in rows:
            if v and d:
                by_date[d] = by_date.get(d, 0.0) + v
        return sorted(by_date.items())

    s95 = await _get_series("high_intensity_95pct_s")
    s103 = await _get_series("high_intensity_103pct_s")

    chronic_95 = {r["date"]: r["value"] for r in compute_intensity_load_series(s95, tau=42.0)}
    acute_95 = {r["date"]: r["value"] for r in compute_intensity_load_series(s95, tau=7.0)}
    chronic_103 = {r["date"]: r["value"] for r in compute_intensity_load_series(s103, tau=42.0)}
    acute_103 = {r["date"]: r["value"] for r in compute_intensity_load_series(s103, tau=7.0)}

    all_dates = sorted(set(list(chronic_95) + list(acute_95) + list(chronic_103) + list(acute_103)))
    filtered = [
        {
            "date": d,
            "chronic_95pct_min": chronic_95.get(d, 0.0),
            "acute_95pct_min": acute_95.get(d, 0.0),
            "chronic_103pct_min": chronic_103.get(d, 0.0),
            "acute_103pct_min": acute_103.get(d, 0.0),
        }
        for d in all_dates
        if date_from.isoformat() <= d <= date_to.isoformat()
    ]
    return {"series": filtered, "athlete_id": athlete_id}


@router.get("/run-volume")
async def run_volume(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    tss_subq = (
        select(func.coalesce(func.sum(WorkoutMetric.value), 0))
        .where(WorkoutMetric.workout_id == WorkoutFile.id)
        .where(WorkoutMetric.metric_key == "tss")
        .correlate(WorkoutFile)
        .scalar_subquery()
    )

    wq = (
        select(
            func.strftime("%Y-%W", WorkoutFile.workout_date).label("week_key"),
            func.min(WorkoutFile.workout_date).label("week_start"),
            func.sum(WorkoutFile.total_distance_m).label("distance_m"),
            (func.sum(WorkoutFile.duration_s) / 3600.0).label("hours"),
            func.sum(WorkoutFile.elevation_gain_m).label("elevation_m"),
            func.sum(tss_subq).label("tss"),
            func.count(WorkoutFile.id).label("count"),
        )
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.sport == "running",
            WorkoutFile.workout_date >= date_from,
            WorkoutFile.workout_date <= date_to,
        )
        .group_by("week_key")
        .order_by("week_key")
    )
    weeks = (await db.execute(wq)).all()

    mq = (
        select(
            func.strftime("%Y-%m", WorkoutFile.workout_date).label("month_key"),
            func.sum(WorkoutFile.total_distance_m).label("distance_m"),
            (func.sum(WorkoutFile.duration_s) / 3600.0).label("hours"),
            func.sum(WorkoutFile.elevation_gain_m).label("elevation_m"),
            func.count(WorkoutFile.id).label("count"),
        )
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.sport == "running",
            WorkoutFile.workout_date >= date_from,
            WorkoutFile.workout_date <= date_to,
        )
        .group_by("month_key")
        .order_by("month_key")
    )
    months = (await db.execute(mq)).all()

    return {
        "weeks": [
            {
                "week_start": r.week_start.isoformat() if r.week_start else None,
                "distance_km": round(float(r.distance_m or 0) / 1000, 2),
                "hours": round(float(r.hours or 0), 1),
                "elevation_m": round(float(r.elevation_m or 0), 0),
                "tss": round(float(r.tss or 0)),
                "count": r.count,
            }
            for r in weeks
        ],
        "months": [
            {
                "month": r.month_key,
                "distance_km": round(float(r.distance_m or 0) / 1000, 2),
                "hours": round(float(r.hours or 0), 1),
                "elevation_m": round(float(r.elevation_m or 0), 0),
                "count": r.count,
            }
            for r in months
        ],
        "athlete_id": athlete_id,
    }
