"""
Orchestrate FIT → metrics → MMP → JSON pipeline.

This is the entry point for processing a single .fit file.
"""

from datetime import datetime, timezone
from typing import Optional

import numpy as np

from fit_parser import parse_fit, RawWorkout
from mmp import compute_mmp, MMP_DURATIONS
from metrics import compute_all_metrics
from storage import save_workout, load_athlete, workout_exists


def import_fit(path: str, force: bool = False) -> dict:
    """
    Full pipeline: parse .fit → compute metrics + MMP → save JSON.

    Args:
        path: path to .fit file
        force: re-import even if already exists

    Returns:
        dict with keys: status, workout_id, path, metrics, warnings
    """
    warnings: list[str] = []

    # Parse FIT
    raw = parse_fit(path)

    # Determine workout ID from start time
    if raw.start_time:
        if hasattr(raw.start_time, "astimezone"):
            workout_id = raw.start_time.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H-%M-%S"
            )
        else:
            workout_id = str(raw.start_time)
    else:
        import os
        mtime = os.path.getmtime(path)
        workout_id = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H-%M-%S"
        )
        warnings.append("No start_time in FIT file; using file mtime")

    # Check for duplicate
    if not force and workout_exists(workout_id):
        return {
            "status": "skipped",
            "workout_id": workout_id,
            "reason": "already imported",
        }

    # Load FTP from athlete profile
    athlete = load_athlete()
    ftp_w: Optional[float] = athlete.get("ftp_w")
    if not ftp_w:
        warnings.append("FTP not set in athlete.json — TSS/IF skipped")

    # Metrics
    if not raw.has_power:
        warnings.append("No power data — MMP/NP/TSS skipped")
        metrics = compute_all_metrics(
            power=np.zeros(1),
            ftp_w=ftp_w,
            duration_s=raw.duration_s,
            hr=raw.heart_rate_bpm if raw.has_hr else None,
            cadence=raw.cadence_rpm if raw.has_cadence else None,
        )
    else:
        metrics = compute_all_metrics(
            power=raw.power_w,
            ftp_w=ftp_w,
            duration_s=raw.duration_s,
            hr=raw.heart_rate_bpm if raw.has_hr else None,
            cadence=raw.cadence_rpm if raw.has_cadence else None,
        )

    # MMP curve
    mmp_curve: dict = {}
    if raw.has_power:
        mmp_raw = compute_mmp(raw.power_w, raw.time_s, ignore_gaps=False)
        # Only include durations reachable in this workout
        max_duration = int(raw.duration_s)
        mmp_curve = {
            str(d): v
            for d, v in mmp_raw.items()
            if d <= max_duration and v > 0
        }

    # Elevation gain
    elevation_gain_m = 0.0
    if len(raw.altitude_m) > 1:
        diff = np.diff(raw.altitude_m)
        elevation_gain_m = round(float(np.sum(diff[diff > 0])), 0)

    # Build workout document
    date_str = ""
    if raw.start_time and hasattr(raw.start_time, "strftime"):
        date_str = raw.start_time.strftime("%Y-%m-%d")

    workout = {
        "id": workout_id,
        "date": date_str,
        "source_file": path,
        "sport": raw.sport,
        "device": raw.device,
        "metrics": {
            **metrics,
            "total_distance_m": round(raw.total_distance_m, 0),
            "elevation_gain_m": elevation_gain_m,
        },
        "mmp_curve": mmp_curve,
        "raw_channels": {
            "sample_rate_s": 1,
            "has_power": raw.has_power,
            "has_hr": raw.has_hr,
            "has_cadence": raw.has_cadence,
            "has_gps": len(raw.distance_m) > 0,
            "sample_count": len(raw.time_s),
        },
    }

    saved_path = save_workout(workout)

    return {
        "status": "imported",
        "workout_id": workout_id,
        "path": str(saved_path),
        "metrics": metrics,
        "mmp_5min": mmp_curve.get("300"),
        "mmp_20min": mmp_curve.get("1200"),
        "mmp_60min": mmp_curve.get("3600"),
        "warnings": warnings,
    }
