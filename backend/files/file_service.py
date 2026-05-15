import asyncio
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import Athlete, WorkoutFile, WorkoutMetric, MmpCache
from backend.files.wko4_reader import parse_wko4_metadata
from backend.files.fit_reader import parse_fit
from backend.engine.algorithms.mmp import compute_mmp
from backend.engine.algorithms.metrics import compute_all_metrics

WKO5_ROOT = Path.home() / "WKO5"


def discover_workout_files(athlete_dir: Path) -> list[Path]:
    """Scan athlete directory for .wko4 and .fit files, sorted by date."""
    files = []
    if not athlete_dir.exists():
        return files
    for year_dir in sorted(athlete_dir.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for f in sorted(year_dir.iterdir()):
            if f.suffix.lower() in (".wko4", ".fit"):
                files.append(f)
    return files


async def scan_and_import(db: AsyncSession, athlete_id: int, athlete_dir: str) -> dict:
    """Scan directory and import all new workout files. Returns summary."""
    files = discover_workout_files(Path(athlete_dir))
    new_count = 0
    skip_count = 0
    error_count = 0

    for f in files:
        path_str = str(f)
        existing = await db.execute(select(WorkoutFile).where(WorkoutFile.file_path == path_str))
        if existing.scalar_one_or_none():
            skip_count += 1
            continue
        try:
            wf = await _import_one_file(db, athlete_id, f)
            if wf:
                new_count += 1
        except Exception:
            error_count += 1
        await asyncio.sleep(0)

    await db.commit()
    return {"new": new_count, "skipped": skip_count, "errors": error_count, "total": len(files)}


async def _import_one_file(
    db: AsyncSession,
    athlete_id: int,
    path: Path,
    source: str = "local",
    tp_workout_id: Optional[int] = None,
    coros_activity_id: Optional[str] = None,
) -> Optional[WorkoutFile]:
    """Parse one file, compute metrics, persist to DB."""
    fmt = path.suffix.lower().lstrip(".")

    if fmt == "wko4":
        meta = parse_wko4_metadata(str(path))
        raw = None
        start_time = meta.start_time
        sport = meta.sport
        duration_s = None
        total_distance_m = None
    elif fmt == "fit":
        try:
            raw = parse_fit(str(path))
        except Exception:
            return None
        start_time = raw.start_time
        sport = raw.sport
        duration_s = raw.duration_s
        total_distance_m = raw.total_distance_m
    else:
        return None

    wf = WorkoutFile(
        athlete_id=athlete_id,
        file_path=str(path),
        file_format=fmt,
        workout_date=start_time.date() if start_time else None,
        sport=sport,
        duration_s=duration_s,
        total_distance_m=total_distance_m,
        source=source,
        tp_workout_id=tp_workout_id,
        coros_activity_id=coros_activity_id,
    )
    db.add(wf)
    await db.flush()

    if raw is not None:
        # Elevation gain from altitude channel (sum of positive ascents)
        if len(raw.altitude_m) > 1:
            diffs = np.diff(raw.altitude_m)
            elevation_gain = float(np.sum(diffs[diffs > 0]))
            wf.elevation_gain_m = round(elevation_gain, 1)
        elif "total_ascent" in raw.session:
            wf.elevation_gain_m = float(raw.session["total_ascent"])

    if raw is not None and raw.has_power:
        # Get FTP from athlete settings if available
        from sqlalchemy import select as sel
        from backend.db.models import AthleteSettings
        from datetime import date
        settings_q = await db.execute(
            sel(AthleteSettings)
            .where(AthleteSettings.athlete_id == athlete_id)
            .order_by(AthleteSettings.effective_date.desc())
        )
        latest_settings = settings_q.scalars().first()
        ftp_w = latest_settings.ftp_w if latest_settings else None

        metrics_dict = compute_all_metrics(
            raw.power_w, ftp_w=ftp_w, duration_s=raw.duration_s,
            hr=raw.heart_rate_bpm if raw.has_hr else None,
            cadence=raw.cadence_rpm if raw.has_cadence else None,
        )
        for key, val in metrics_dict.items():
            if isinstance(val, (float, int)) and val is not None:
                db.add(WorkoutMetric(workout_id=wf.id, metric_key=key, value=float(val)))

        mmp = compute_mmp(raw.power_w, raw.time_s)
        for dur, val in mmp.items():
            if val > 0:
                db.add(MmpCache(workout_id=wf.id, channel="power", duration_s=dur, value=val))

        # Intensity metrics for running workouts (seconds at or above FTP thresholds)
        if raw.sport == "running" and ftp_w and ftp_w > 0:
            power_arr = raw.power_w
            hi_95 = float(np.sum(power_arr >= 0.95 * ftp_w))
            hi_103 = float(np.sum(power_arr >= 1.03 * ftp_w))
            if hi_95 > 0:
                db.add(WorkoutMetric(workout_id=wf.id, metric_key="high_intensity_95pct_s", value=hi_95))
            if hi_103 > 0:
                db.add(WorkoutMetric(workout_id=wf.id, metric_key="high_intensity_103pct_s", value=hi_103))

    return wf
