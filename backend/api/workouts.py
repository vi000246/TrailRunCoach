from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import date
from typing import Optional

from backend.db.database import get_db
from backend.db.models import WorkoutFile, WorkoutMetric, MmpCache
from backend.engine.algorithms.mmp import compute_mmp
from backend.files.fit_reader import parse_fit

router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])


@router.get("")
async def list_workouts(
    athlete_id: int = 1,
    page: int = 1,
    per_page: int = 20,
    sport: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id)
    if sport:
        q = q.where(WorkoutFile.sport == sport)
    if date_from:
        q = q.where(WorkoutFile.workout_date >= date_from)
    if date_to:
        q = q.where(WorkoutFile.workout_date <= date_to)

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar()

    q = q.order_by(WorkoutFile.workout_date.desc()).offset((page - 1) * per_page).limit(per_page)
    q = q.options(selectinload(WorkoutFile.metrics))
    result = await db.execute(q)
    workouts = result.scalars().all()

    return {
        "total": total, "page": page, "per_page": per_page,
        "items": [_workout_summary(w) for w in workouts],
    }


@router.get("/{workout_id}")
async def get_workout(workout_id: int, db: AsyncSession = Depends(get_db)):
    q = select(WorkoutFile).where(WorkoutFile.id == workout_id).options(selectinload(WorkoutFile.metrics))
    result = await db.execute(q)
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    return _workout_detail(w)


@router.get("/{workout_id}/mmp")
async def get_workout_mmp(workout_id: int, channel: str = "power", db: AsyncSession = Depends(get_db)):
    q = select(MmpCache).where(MmpCache.workout_id == workout_id, MmpCache.channel == channel)
    result = await db.execute(q)
    cached = result.scalars().all()
    if cached:
        curve = {str(c.duration_s): c.value for c in cached}
        return {"workout_id": workout_id, "channel": channel, "curve": curve, "cached": True}

    w_result = await db.execute(select(WorkoutFile).where(WorkoutFile.id == workout_id))
    w = w_result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    if w.file_format != "fit":
        raise HTTPException(422, "NO_POWER_DATA")

    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"FILE_PARSE_ERROR: {e}")

    if not raw.has_power:
        raise HTTPException(422, "NO_POWER_DATA")

    mmp = compute_mmp(raw.power_w, raw.time_s)
    for dur, val in mmp.items():
        if val > 0:
            db.add(MmpCache(workout_id=workout_id, channel=channel, duration_s=dur, value=val))
    await db.commit()

    curve = {str(d): v for d, v in mmp.items() if v > 0}
    return {"workout_id": workout_id, "channel": channel, "curve": curve, "cached": False}


def _workout_summary(w: WorkoutFile) -> dict:
    metrics = {m.metric_key: m.value for m in w.metrics}
    return {
        "id": w.id,
        "date": w.workout_date.isoformat() if w.workout_date else None,
        "sport": w.sport,
        "duration_s": w.duration_s,
        "file_format": w.file_format,
        "source": w.source,
        "metrics": metrics,
    }


def _workout_detail(w: WorkoutFile) -> dict:
    return {**_workout_summary(w), "file_path": w.file_path}
