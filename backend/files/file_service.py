import asyncio
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import Athlete, WorkoutFile, WorkoutMetric, MmpCache
from backend.files.wko4_reader import parse_wko4_metadata
from backend.files.fit_reader import parse_fit
from backend.engine.algorithms.mmp import compute_mmp
from backend.engine.algorithms.metrics import (
    compute_all_metrics, compute_run_ftp_from_mmp, compute_load_metrics,
)
from backend.engine.algorithms.classify import classify_trail

WKO5_ROOT = Path.home() / "WKO5"


def _apply_classification(wf: WorkoutFile) -> None:
    """Set trail/road classification from sport + distance + elevation.

    Skips activities the user has manually overridden so re-imports never
    clobber a deliberate choice.
    """
    if getattr(wf, "classification_overridden", False):
        return
    wf.trail_classification = classify_trail(
        wf.sport, wf.total_distance_m, wf.elevation_gain_m
    )


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
            # savepoint per file: a failure leaves no half-written rows
            async with db.begin_nested():
                wf = await _import_one_file(db, athlete_id, f)
            if wf:
                new_count += 1
        except Exception:
            error_count += 1
        await asyncio.sleep(0)

    await db.commit()
    return {"new": new_count, "skipped": skip_count, "errors": error_count, "total": len(files)}


async def get_run_ftp(db: AsyncSession, athlete_id: int, as_of_date: Optional[date] = None) -> Optional[float]:
    """
    Compute runFTP for an athlete as of a given date.

    WKO5 formula: athleterange(date-89, date, ftp(meanmax(runpower)))
    Uses a 2-parameter Critical Power model fit to running MMP data from the
    preceding 90-day window. Falls back to manually set run_ftp_w if no MMP data.
    """
    from backend.db.models import AthleteSettings
    if as_of_date is None:
        as_of_date = date.today()

    # Check for a manually set run_ftp_w first
    settings_q = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = settings_q.scalars().first()
    if settings and settings.run_ftp_w and settings.run_ftp_w > 0:
        return settings.run_ftp_w

    # Compute from 90-day running MMP window
    window_start = as_of_date - timedelta(days=89)
    mmp_q = await db.execute(
        select(MmpCache.duration_s, MmpCache.value)
        .join(WorkoutFile, MmpCache.workout_id == WorkoutFile.id)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.sport == "running",
            WorkoutFile.workout_date >= window_start,
            WorkoutFile.workout_date <= as_of_date,
            MmpCache.channel == "power",
            MmpCache.value > 0,
        )
    )
    rows = mmp_q.fetchall()
    if not rows:
        return None

    # Aggregate: for each duration, take the max across all workouts in window
    by_duration: dict[int, float] = {}
    for dur, val in rows:
        if val and (dur not in by_duration or val > by_duration[dur]):
            by_duration[dur] = val

    return compute_run_ftp_from_mmp(by_duration)


def utc_and_local(start: Optional[datetime], tz: tzinfo, is_local: bool) -> tuple[Optional[datetime], Optional[date]]:
    """(naive UTC start, athlete-local date). FIT timestamps are UTC; .wko4
    start times are the local wall clock WKO5 shows."""
    if start is None:
        return None, None
    if start.tzinfo is not None:
        aware = start
    elif is_local:
        aware = start.replace(tzinfo=tz)
    else:
        aware = start.replace(tzinfo=timezone.utc)
    utc = aware.astimezone(timezone.utc)
    return utc.replace(tzinfo=None), aware.astimezone(tz).date()


async def settings_on(db: AsyncSession, athlete_id: int, day: date):
    """The AthleteSettings row in effect on `day` (latest effective_date <= day),
    falling back to the earliest row when the workout predates all of them."""
    from backend.db.models import AthleteSettings
    q = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id, AthleteSettings.effective_date <= day)
        .order_by(AthleteSettings.effective_date.desc())
    )
    row = q.scalars().first()
    if row is None:
        q = await db.execute(
            select(AthleteSettings).where(AthleteSettings.athlete_id == athlete_id)
            .order_by(AthleteSettings.effective_date.asc()))
        row = q.scalars().first()
    return row


def elevation_gain(raw) -> Optional[float]:
    """Device total_ascent when present (what COROS / TP / WKO5 show); raw
    positive altitude diffs only as a fallback — they count GPS / baro jitter
    as climbing and run well above the device figure."""
    ta = raw.session.get("total_ascent") if raw.session else None
    if ta is not None:
        try:
            return float(ta)
        except (TypeError, ValueError):
            pass
    if len(raw.altitude_m) > 1:
        diffs = np.diff(raw.altitude_m)
        return round(float(np.sum(diffs[diffs > 0])), 1)
    return None


async def record_corrupt(db: AsyncSession, athlete_id: int, path: Path, source: str,
                         workout_date: Optional[date] = None, tp_workout_id: Optional[int] = None,
                         coros_activity_id: Optional[str] = None) -> WorkoutFile:
    """Stub row for an unreadable file, so the sync doesn't re-download it."""
    res = await db.execute(select(WorkoutFile).where(WorkoutFile.file_path == str(path)))
    stub = res.scalar_one_or_none()
    if stub is None:
        stub = WorkoutFile(athlete_id=athlete_id, file_path=str(path))
        db.add(stub)
    stub.file_format = "corrupt"
    stub.source = source
    stub.workout_date = workout_date
    stub.tp_workout_id = tp_workout_id
    stub.coros_activity_id = coros_activity_id
    await db.flush()
    return stub


async def _import_one_file(
    db: AsyncSession,
    athlete_id: int,
    path: Path,
    source: str = "local",
    tp_workout_id: Optional[int] = None,
    coros_activity_id: Optional[str] = None,
) -> Optional[WorkoutFile]:
    """Parse one file, compute metrics, persist to DB (flush only — the
    caller commits, or rolls back on error). Returns None for unreadable
    files."""
    from backend.settings.repository import SettingsRepository
    from backend.sync import dedup

    fmt = path.suffix.lower().lstrip(".")

    if fmt == "wko4":
        # wko4 is WKO5's proprietary binary format. We can read metadata (sport,
        # date) from the header but cannot decode the exercise channel data.
        # These workouts represent pre-Coros historical data; metrics cannot be
        # computed until the format is reverse-engineered further.
        meta = parse_wko4_metadata(str(path))
        raw = None
        start_time = meta.start_time
        sport = meta.sport
        duration_s = meta.duration_s
        total_distance_m = meta.total_distance_m
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

    tz = await SettingsRepository(db, athlete_id).timezone()
    start_utc, local_day = utc_and_local(start_time, tz, is_local=(fmt == "wko4"))

    wf = WorkoutFile(
        athlete_id=athlete_id,
        file_path=str(path),
        file_format=fmt,
        workout_date=local_day,
        start_time_utc=start_utc,
        sport=sport,
        duration_s=duration_s,
        total_distance_m=total_distance_m,
        source=source,
        tp_workout_id=tp_workout_id,
        coros_activity_id=coros_activity_id,
    )
    db.add(wf)
    await db.flush()
    await dedup.resolve(db, wf)

    if raw is None:
        return wf

    wf.elevation_gain_m = elevation_gain(raw)
    _apply_classification(wf)

    is_running = (raw.sport == "running")
    workout_date = local_day or date.today()
    day_settings = await settings_on(db, athlete_id, workout_date)

    # Trail/run load metrics (hrTSS primary, rTSS alongside) — running only.
    # Computed before the power check: most runs have no power and still need
    # a load.
    if is_running:
        load_metrics = compute_load_metrics(
            hr=raw.heart_rate_bpm if raw.has_hr else None,
            duration_s=raw.duration_s,
            distance_m=raw.total_distance_m,
            lthr=day_settings.lthr if day_settings else None,
            threshold_pace_s_per_km=day_settings.threshold_pace_s_per_km if day_settings else None,
        )
        for key, val in load_metrics.items():
            db.add(WorkoutMetric(workout_id=wf.id, metric_key=key, value=float(val)))

    if not raw.has_power:
        return wf

    # Determine the correct FTP for this sport:
    # - running → runFTP (computed from 90-day running MMP, or manually set run_ftp_w)
    # - all others → ftp_w in effect on the workout date
    if is_running:
        ftp_w = await get_run_ftp(db, athlete_id, as_of_date=workout_date)
    else:
        ftp_w = day_settings.ftp_w if day_settings else None

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

    # Intensity metrics for running: seconds at or above runFTP thresholds
    # WKO5: sum(if(runpower >= runFTP * 0.95, deltatime, 0))
    #        sum(if(runpower >= runFTP * 1.03, deltatime, 0))
    if is_running and ftp_w and ftp_w > 0:
        power_arr = raw.power_w
        hi_95 = float(np.sum(power_arr >= 0.95 * ftp_w))
        hi_103 = float(np.sum(power_arr >= 1.03 * ftp_w))
        if hi_95 > 0:
            db.add(WorkoutMetric(workout_id=wf.id, metric_key="high_intensity_95pct_s", value=hi_95))
        if hi_103 > 0:
            db.add(WorkoutMetric(workout_id=wf.id, metric_key="high_intensity_103pct_s", value=hi_103))

    return wf
