from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import date
from typing import Optional

from backend.db.database import get_db
from backend.db.models import WorkoutFile, WorkoutMetric, MmpCache, AthleteSettings
from backend.engine.algorithms.mmp import compute_mmp
from backend.files.fit_reader import parse_fit

router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])

POWER_ZONES_DEF = [
    (1, "Recovery",      0.00, 0.55),
    (2, "Endurance",     0.55, 0.75),
    (3, "Tempo",         0.75, 0.90),
    (4, "Threshold",     0.90, 1.05),
    (5, "VO2max",        1.05, 1.20),
    (6, "Anaerobic",     1.20, 1.50),
    (7, "Neuromuscular", 1.50, 99.0),
]

HR_ZONES_DEF = [
    (1, "Recovery",  0.00, 0.85),
    (2, "Aerobic",   0.85, 0.90),
    (3, "Tempo",     0.90, 0.95),
    (4, "Threshold", 0.95, 1.00),
    (5, "VO2max",    1.00, 99.0),
]


async def _get_workout_or_404(workout_id: int, db: AsyncSession) -> WorkoutFile:
    result = await db.execute(
        select(WorkoutFile).where(WorkoutFile.id == workout_id).options(selectinload(WorkoutFile.metrics))
    )
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    return w


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


@router.get("/{workout_id}/timeseries")
async def get_workout_timeseries(workout_id: int, db: AsyncSession = Depends(get_db)):
    w = await _get_workout_or_404(workout_id, db)
    if w.file_format != "fit":
        raise HTTPException(422, "NO_FIT_FILE")
    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"PARSE_ERROR: {e}")

    time_s = raw.time_s if raw.time_s is not None and len(raw.time_s) > 0 else []
    power_w = raw.power_w if raw.power_w is not None else []
    hr_bpm = raw.heart_rate_bpm if raw.heart_rate_bpm is not None else []
    cadence = raw.cadence_rpm if raw.cadence_rpm is not None else []

    n = len(time_s)
    if n == 0:
        return {"workout_id": workout_id, "duration_s": 0, "sample_rate_s": 1, "series": []}

    step = max(1, n // 1800)
    series = []
    for i in range(0, n, step):
        point: dict = {"t": int(time_s[i])}
        if i < len(power_w) and power_w[i] is not None:
            point["power"] = round(float(power_w[i]))
        if i < len(hr_bpm) and hr_bpm[i] is not None:
            point["hr"] = round(float(hr_bpm[i]))
        if i < len(cadence) and cadence[i] is not None:
            point["cadence"] = round(float(cadence[i]))
        series.append(point)

    return {
        "workout_id": workout_id,
        "duration_s": int(time_s[-1]) if len(time_s) > 0 else 0,
        "sample_rate_s": step,
        "series": series,
    }


@router.get("/{workout_id}/zones")
async def get_workout_zones(workout_id: int, db: AsyncSession = Depends(get_db)):
    w = await _get_workout_or_404(workout_id, db)

    settings_result = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == w.athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = settings_result.scalars().first()
    ftp = float(settings.ftp_w) if settings and settings.ftp_w else 200.0
    lthr = int(settings.lthr) if settings and settings.lthr else 165

    if w.file_format != "fit":
        return {"workout_id": workout_id, "ftp": ftp, "lthr": lthr, "power_zones": [], "hr_zones": []}

    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"PARSE_ERROR: {e}")

    power_w = list(raw.power_w) if raw.power_w is not None else []
    hr_bpm = list(raw.heart_rate_bpm) if raw.heart_rate_bpm is not None else []

    def count_zones(values: list, zones: list, threshold: float) -> dict:
        counts: dict[int, int] = {z[0]: 0 for z in zones}
        for v in values:
            if v is None or (isinstance(v, float) and v != v):  # skip None/NaN
                continue
            ratio = float(v) / threshold
            for z_id, _, lo, hi in zones:
                if lo <= ratio < hi:
                    counts[z_id] += 1
                    break
        return counts

    pw_counts = count_zones(power_w, POWER_ZONES_DEF, ftp)
    hr_counts = count_zones(hr_bpm, HR_ZONES_DEF, float(lthr))

    return {
        "workout_id": workout_id,
        "ftp": ftp,
        "lthr": lthr,
        "power_zones": [
            {
                "zone": z, "name": n,
                "min_w": round(lo * ftp),
                "max_w": round(hi * ftp) if hi < 10 else None,
                "time_s": pw_counts[z],
            }
            for z, n, lo, hi in POWER_ZONES_DEF
        ],
        "hr_zones": [
            {
                "zone": z, "name": n,
                "min_bpm": round(lo * lthr),
                "max_bpm": round(hi * lthr) if hi < 10 else None,
                "time_s": hr_counts[z],
            }
            for z, n, lo, hi in HR_ZONES_DEF
        ],
    }


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
