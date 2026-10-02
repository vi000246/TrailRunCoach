from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from datetime import date

from backend.db.database import get_db
from backend.db.models import Athlete, AthleteSettings, WorkoutFile, WorkoutMetric

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


@router.get("/{athlete_id}/settings")
async def get_settings(athlete_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    s = result.scalars().first()
    if s is None:
        raise HTTPException(status_code=404, detail="NO_SETTINGS")

    ftp = s.ftp_w
    lthr = s.lthr

    power_zones = []
    if ftp:
        # Palladino's running power zones (% CP; engine/zones.py), the app's only power set
        from backend.engine.zones import palladino_rows
        for zid, name, lo, hi in palladino_rows():
            power_zones.append({"zone": zid, "label": name, "min_w": int(ftp * lo),
                                "max_w": int(ftp * hi) - 1 if hi < 10 else None})

    hr_zones = []
    if lthr:
        # Friel 7-zone collapsed to 5: breakpoints at 85/90/95/100 % LTHR
        breakpoints = [int(lthr * p) for p in (0.85, 0.90, 0.95, 1.00)]
        labels = ["Recovery", "Aerobic", "Tempo", "Threshold", "Anaerobic"]
        for i in range(5):
            hr_zones.append({
                "zone": i + 1,
                "label": labels[i],
                "min_bpm": breakpoints[i - 1] if i > 0 else 0,
                "max_bpm": breakpoints[i] - 1 if i < 4 else None,
            })

    return {
        "athlete_id": athlete_id,
        "effective_date": s.effective_date.isoformat(),
        "ftp_w": ftp,
        "run_ftp_w": s.run_ftp_w,
        "lthr": lthr,
        "weight_kg": s.weight_kg,
        "threshold_pace_s_per_km": s.threshold_pace_s_per_km,
        "initial_ctl_run": s.initial_ctl_run,
        "initial_atl_run": s.initial_atl_run,
        "ai_provider": s.ai_provider,
        "ai_model": s.ai_model,
        "power_zones": power_zones,
        "hr_zones": hr_zones,
    }


class SettingsUpdate(BaseModel):
    ftp_w: Optional[float] = None
    run_ftp_w: Optional[float] = None
    lthr: Optional[int] = None
    weight_kg: Optional[float] = None
    effective_date: Optional[date] = None
    threshold_pace_s_per_km: Optional[float] = None
    initial_ctl_run: Optional[float] = None
    initial_atl_run: Optional[float] = None
    ai_provider: Optional[str] = None
    ai_api_key:  Optional[str] = None
    ai_model:    Optional[str] = None


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
        if body.run_ftp_w is not None:
            s.run_ftp_w = body.run_ftp_w
        if body.lthr is not None:
            s.lthr = body.lthr
        if body.weight_kg is not None:
            s.weight_kg = body.weight_kg
        if body.threshold_pace_s_per_km is not None:
            s.threshold_pace_s_per_km = body.threshold_pace_s_per_km
        if body.initial_ctl_run is not None:
            s.initial_ctl_run = body.initial_ctl_run
        if body.initial_atl_run is not None:
            s.initial_atl_run = body.initial_atl_run
        if body.ai_provider is not None:
            s.ai_provider = body.ai_provider
        if body.ai_api_key is not None:
            s.ai_api_key = body.ai_api_key
        if body.ai_model is not None:
            s.ai_model = body.ai_model
    else:
        s = AthleteSettings(
            athlete_id=athlete_id, effective_date=eff_date,
            ftp_w=body.ftp_w, run_ftp_w=body.run_ftp_w,
            lthr=body.lthr, weight_kg=body.weight_kg,
            threshold_pace_s_per_km=body.threshold_pace_s_per_km,
            initial_ctl_run=body.initial_ctl_run,
            initial_atl_run=body.initial_atl_run,
            ai_provider=body.ai_provider,
            ai_model=body.ai_model,
            ai_api_key=body.ai_api_key,
        )
        db.add(s)
    await db.commit()
    return {"saved": True}


@router.post("/{athlete_id}/recalculate-running-metrics")
async def recalculate_running_metrics(athlete_id: int, db: AsyncSession = Depends(get_db)):
    """
    Recalculate TSS and intensity metrics for ALL running FIT workouts using runFTP.

    This overwrites previously computed values that used the wrong (cycling) FTP.
    WKO5 uses runFTP = ftp(meanmax(runpower)) over a 90-day rolling window.
    """
    from backend.engine.algorithms.metrics import compute_all_metrics
    from backend.files.fit_reader import parse_fit
    from backend.files.file_service import get_run_ftp
    import numpy as np

    q = await db.execute(
        select(WorkoutFile)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.sport == "running",
            WorkoutFile.file_format == "fit",
            WorkoutFile.workout_date.isnot(None),
        )
        .order_by(WorkoutFile.workout_date)
    )
    workouts = q.scalars().all()

    updated = 0
    skipped_no_power = 0
    skipped_no_ftp = 0
    errors = 0

    for wf in workouts:
        run_ftp = await get_run_ftp(db, athlete_id, as_of_date=wf.workout_date)
        if not run_ftp or run_ftp <= 0:
            skipped_no_ftp += 1
            continue

        try:
            raw = parse_fit(wf.file_path)
        except Exception:
            errors += 1
            continue

        if not raw.has_power:
            skipped_no_power += 1
            continue

        metrics = compute_all_metrics(
            raw.power_w, ftp_w=run_ftp, duration_s=raw.duration_s,
            hr=raw.heart_rate_bpm if raw.has_hr else None,
            cadence=raw.cadence_rpm if raw.has_cadence else None,
        )

        intensity_keys = {"high_intensity_95pct_s", "high_intensity_103pct_s"}
        power_arr = raw.power_w
        hi_95 = float(np.sum(power_arr >= 0.95 * run_ftp))
        hi_103 = float(np.sum(power_arr >= 1.03 * run_ftp))
        if hi_95 > 0:
            metrics["high_intensity_95pct_s"] = hi_95
        if hi_103 > 0:
            metrics["high_intensity_103pct_s"] = hi_103

        recalc_keys = {"tss", "normalized_power_w", "avg_power_w", "variability_index"} | intensity_keys
        existing_q = await db.execute(
            select(WorkoutMetric).where(
                WorkoutMetric.workout_id == wf.id,
                WorkoutMetric.metric_key.in_(recalc_keys),
            )
        )
        existing = {m.metric_key: m for m in existing_q.scalars().all()}

        for key in recalc_keys:
            val = metrics.get(key)
            if key in intensity_keys:
                val = metrics.get(key)
            if val is None or not isinstance(val, (float, int)):
                continue
            if key in existing:
                existing[key].value = float(val)
            else:
                db.add(WorkoutMetric(workout_id=wf.id, metric_key=key, value=float(val)))

        updated += 1

    await db.commit()
    return {
        "updated": updated,
        "skipped_no_power": skipped_no_power,
        "skipped_no_run_ftp": skipped_no_ftp,
        "errors": errors,
    }
