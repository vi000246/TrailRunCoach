from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, true
from datetime import date, timedelta
from typing import Optional

from backend.db.database import get_db
from backend.db.models import WorkoutFile, WorkoutMetric, AthleteSettings, PmcCache
from backend.engine.algorithms.metrics import compute_run_pmc, compute_intensity_load_series

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _sport_clause(sports: Optional[list[str]]):
    """Filter predicate for an optional list of sports.

    ``None`` (or empty) means "all sports" — returns a tautology so callers can
    always ``.where(_sport_clause(sports))`` without branching.
    """
    if not sports:
        return true()
    return WorkoutFile.sport.in_(sports)


@router.get("/dashboard-summary")
async def dashboard_summary(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    today = date.today()
    week_start = today - timedelta(days=7)

    # Latest PMC row
    pmc_result = await db.execute(
        select(PmcCache)
        .where(PmcCache.athlete_id == athlete_id)
        .order_by(PmcCache.date.desc())
        .limit(2)
    )
    pmc_rows = pmc_result.scalars().all()
    latest_pmc = pmc_rows[0] if pmc_rows else None
    prev_pmc = pmc_rows[1] if len(pmc_rows) > 1 else None

    tsb_state = None
    ctl_trend = None
    if latest_pmc:
        tsb = latest_pmc.tsb or 0.0
        if tsb > 5:
            tsb_state = "fresh"
        elif tsb > -10:
            tsb_state = "optimal"
        elif tsb > -25:
            tsb_state = "tired"
        else:
            tsb_state = "overreached"

        if prev_pmc and prev_pmc.ctl is not None and latest_pmc.ctl is not None:
            ctl_trend = round(latest_pmc.ctl - prev_pmc.ctl, 1)

    # Weekly TSS + hours
    tss_subq = (
        select(func.coalesce(func.sum(WorkoutMetric.value), 0))
        .where(WorkoutMetric.workout_id == WorkoutFile.id)
        .where(WorkoutMetric.metric_key == "tss")
        .correlate(WorkoutFile)
        .scalar_subquery()
    )
    week_q = await db.execute(
        select(
            func.sum(tss_subq).label("tss"),
            (func.sum(WorkoutFile.duration_s) / 3600.0).label("hours"),
            func.count(WorkoutFile.id).label("count"),
        )
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.workout_date >= week_start,
        )
    )
    week_row = week_q.first()

    # Last workout
    last_q = await db.execute(
        select(WorkoutFile)
        .where(WorkoutFile.athlete_id == athlete_id)
        .order_by(WorkoutFile.workout_date.desc())
        .limit(1)
    )
    last_wo = last_q.scalars().first()

    return {
        "tsb": round(latest_pmc.tsb, 1) if latest_pmc and latest_pmc.tsb is not None else None,
        "tsb_state": tsb_state,
        "ctl": round(latest_pmc.ctl, 1) if latest_pmc and latest_pmc.ctl is not None else None,
        "ctl_trend": ctl_trend,
        "weekly_tss": round(float(week_row.tss or 0)) if week_row else 0,
        "weekly_hours": round(float(week_row.hours or 0), 1) if week_row else 0.0,
        "weekly_count": week_row.count if week_row else 0,
        "last_workout": {
            "id": last_wo.id,
            "date": last_wo.workout_date.isoformat() if last_wo.workout_date else None,
            "sport": last_wo.sport,
            "duration_s": last_wo.duration_s,
        } if last_wo else None,
    }


@router.get("/weekly")
async def weekly_load(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    sports: Optional[list[str]] = Query(None),
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
        .where(WorkoutFile.athlete_id == athlete_id, _sport_clause(sports))
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
    sports: Optional[list[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    # Backward-compatible default: callers that don't pass sports get running only.
    sports = sports or ["running"]

    # Fetch seed values from latest settings
    settings_q = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = settings_q.scalars().first()
    initial_ctl = (settings.initial_ctl_run or 0.0) if settings else 0.0
    initial_atl = (settings.initial_atl_run or 0.0) if settings else 0.0

    q = (
        select(WorkoutFile.workout_date, WorkoutMetric.value)
        .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutMetric.metric_key == "tss",
            _sport_clause(sports),
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
    pmc_data = compute_run_pmc(run_series, initial_ctl=initial_ctl, initial_atl=initial_atl)
    filtered = [p for p in pmc_data if date_from.isoformat() <= p["date"] <= date_to.isoformat()]
    return {
        "series": filtered,
        "athlete_id": athlete_id,
        "seeded": initial_ctl > 0.0,
        "initial_ctl_used": initial_ctl if initial_ctl > 0.0 else None,
    }


@router.get("/trail-load")
async def trail_load(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trail-running PMC driven by hrTSS (primary load), with rTSS alongside.

    Only activities classified as ``trail`` are included — pace-based rTSS is
    shown for reference because it under-credits technical terrain.
    """
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    async def _metric_by_date(metric_key: str) -> dict[date, float]:
        q = (
            select(WorkoutFile.workout_date, WorkoutMetric.value)
            .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
            .where(
                WorkoutFile.athlete_id == athlete_id,
                WorkoutMetric.metric_key == metric_key,
                WorkoutFile.trail_classification == "trail",
                WorkoutFile.workout_date.isnot(None),
            )
        )
        rows = (await db.execute(q)).all()
        by_date: dict[date, float] = {}
        for d, v in rows:
            if v and d:
                by_date[d] = by_date.get(d, 0.0) + v
        return by_date

    hr_by_date = await _metric_by_date("hr_tss")
    rtss_by_date = await _metric_by_date("r_tss")

    hr_series = sorted(hr_by_date.items())
    pmc_data = compute_run_pmc(hr_series)
    filtered = []
    for p in pmc_data:
        if not (date_from.isoformat() <= p["date"] <= date_to.isoformat()):
            continue
        d = date.fromisoformat(p["date"])
        filtered.append({
            "date": p["date"],
            "ctl": p["ctl"],
            "atl": p["atl"],
            "tsb": p["tsb"],
            "hr_tss": round(hr_by_date.get(d, 0.0), 1),
            "r_tss": round(rtss_by_date[d], 1) if d in rtss_by_date else None,
        })
    return {"series": filtered, "athlete_id": athlete_id, "primary_load": "hr_tss"}


@router.get("/chart-interpretation")
async def chart_interpretation(
    chart: str = "pmc",
    athlete_id: int = 1,
    db: AsyncSession = Depends(get_db),
):
    """Rule-based plain-language reading + status signal for a chart.

    Currently TSB-driven (status of latest PMC row); other charts fall back to
    the same signal until chart-specific rules are added.
    """
    from backend.engine.algorithms.interpret import interpret_tsb

    latest = (await db.execute(
        select(PmcCache)
        .where(PmcCache.athlete_id == athlete_id)
        .order_by(PmcCache.date.desc())
        .limit(1)
    )).scalars().first()
    tsb = latest.tsb if latest else None
    result = interpret_tsb(tsb)
    result["chart"] = chart
    return result


@router.get("/trail-summary")
async def trail_summary(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """Aggregate trail-running stats: total climb, activity count, recent climbs/VAM."""
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    q = (
        select(
            WorkoutFile.workout_date,
            WorkoutFile.elevation_gain_m,
            WorkoutFile.duration_s,
            WorkoutFile.total_distance_m,
        )
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.trail_classification == "trail",
            WorkoutFile.workout_date.isnot(None),
            WorkoutFile.workout_date >= date_from,
            WorkoutFile.workout_date <= date_to,
        )
        .order_by(WorkoutFile.workout_date.desc())
    )
    rows = (await db.execute(q)).all()

    total_gain = 0.0
    recent = []
    for d, gain, dur, dist in rows:
        gain = gain or 0.0
        total_gain += gain
        # VAM (vertical ascent metres/hour)
        vam = round(gain * 3600.0 / dur, 0) if dur and dur > 0 else None
        recent.append({
            "date": d.isoformat() if d else None,
            "gain_m": round(gain, 0),
            "distance_km": round((dist or 0.0) / 1000.0, 2),
            "vam": vam,
        })

    return {
        "athlete_id": athlete_id,
        "total_gain_m": round(total_gain, 0),
        "activity_count": len(rows),
        "recent": recent,
    }


@router.get("/intensity-load")
async def intensity_load(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    sports: Optional[list[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    sports = sports or ["running"]

    async def _get_series(metric_key: str) -> list[tuple[date, float]]:
        q = (
            select(WorkoutFile.workout_date, WorkoutMetric.value)
            .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
            .where(
                WorkoutFile.athlete_id == athlete_id,
                WorkoutMetric.metric_key == metric_key,
                _sport_clause(sports),
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
    sports: Optional[list[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=365)

    sports = sports or ["running"]

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
            _sport_clause(sports),
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
            _sport_clause(sports),
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
