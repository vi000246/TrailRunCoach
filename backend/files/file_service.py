import asyncio
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import Athlete, WorkoutFile
from backend.files.wko4_reader import parse_wko4_metadata
from backend.files.fit_reader import parse_fit
from backend.engine.algorithms.classify import classify_trail

WKO5_ROOT = Path.home() / "WKO5"
# The import used to also write per-activity metrics (workout_metrics: hrTSS / rTSS / NP / TSS /
# time above runFTP) and a power mean-max (mmp_cache, read only to get the runFTP for those
# metrics). Nothing read either: the charts, PMC and API take every value from the FIT dataset
# (engine/wko5expr/fitdataset.py). Both tables stay as they are in older DBs, unread and unwritten
# (data_registry.LEGACY_TABLES).


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


def _sync_ids(source: str, path: Path) -> dict:
    """Provider ids encoded in the sync file names (coros_client / tp_client):
    COROS "<labelId>_<YYYY-MM-DD>_<sport>.fit", TP "tp_<YYYY_MM_DD>_<workoutId>.fit".
    `source` is the DB name ("coros" / "trainingpeaks") or the folder / API name
    ("tp"), storage.SOURCES."""
    from backend.sync import storage
    source = storage.SOURCES.get(source, source)
    stem = path.name.split(".")[0]
    if source == "coros":
        head = stem.split("_", 1)[0]
        return {"coros_activity_id": head} if head.isdigit() else {}
    if source == "trainingpeaks":
        tail = stem.rsplit("_", 1)[-1]
        return {"tp_workout_id": int(tail)} if tail.isdigit() else {}
    return {}


def discover_tagged_files(athlete_dir: Path) -> list[tuple[Path, str]]:
    """(file, source) pairs. A folder laid out like the sync storage root
    (<dir>/coros/<year>/, <dir>/tp/<year>/) is walked per source and each file
    tagged with that source; any other folder is the classic
    <dir>/<year>/*.wko4|.fit layout, tagged "local"."""
    from backend.sync import storage
    out: list[tuple[Path, str]] = []
    per_source = [(athlete_dir / folder, db_src) for folder, db_src in storage.SOURCES.items()
                  if (athlete_dir / folder).is_dir()]
    for d, db_src in per_source:
        out += [(f, db_src) for f in discover_workout_files(d) if not f.is_symlink()]
    out += [(f, "local") for f in discover_workout_files(athlete_dir)]
    return out


async def _already_imported(db: AsyncSession, athlete_id: int, path: Path, ids: dict) -> bool:
    """Same file (by path, however it was spelled) or the same provider activity."""
    cands = {str(path)}
    try:
        cands.add(str(path.resolve()))
    except OSError:
        pass
    q = select(WorkoutFile.id).where(WorkoutFile.file_path.in_(cands))
    if (await db.execute(q)).first():
        return True
    if ids.get("coros_activity_id"):
        q = select(WorkoutFile.id).where(WorkoutFile.athlete_id == athlete_id,
                                         WorkoutFile.coros_activity_id == ids["coros_activity_id"])
        if (await db.execute(q)).first():
            return True
    if ids.get("tp_workout_id"):
        q = select(WorkoutFile.id).where(WorkoutFile.athlete_id == athlete_id,
                                         WorkoutFile.tp_workout_id == ids["tp_workout_id"])
        if (await db.execute(q)).first():
            return True
    return False


async def scan_and_import(db: AsyncSession, athlete_id: int, athlete_dir: str) -> dict:
    """Scan a folder and import every workout file not in the DB yet. Works on
    the sync storage root (~/.wko5coach/fit with coros/ and tp/ below it —
    files are tagged with their source and provider id, so a file the sync
    already recorded is never imported twice) and on a classic year-folder
    layout. De-dup / canonical rules apply through _import_one_file."""
    files = discover_tagged_files(Path(athlete_dir))
    new_count = 0
    skip_count = 0
    error_count = 0
    by_source: dict[str, int] = {}

    for f, source in files:
        ids = _sync_ids(source, f)
        if await _already_imported(db, athlete_id, f, ids):
            skip_count += 1
            continue
        try:
            # savepoint per file: a failure leaves no half-written rows
            async with db.begin_nested():
                wf = await _import_one_file(db, athlete_id, f, source=source, **ids)
            if wf:
                new_count += 1
                by_source[source] = by_source.get(source, 0) + 1
        except Exception:
            error_count += 1
        await asyncio.sleep(0)

    await db.commit()
    return {"new": new_count, "skipped": skip_count, "errors": error_count, "total": len(files),
            "new_by_source": by_source}


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
    """Parse one file and persist its workout_files row: date, sport, distance, elevation,
    trail / road, the watch's RPE (flush only — the caller commits, or rolls back on error).
    Returns None for unreadable files. Per-activity metrics are not stored: the FIT dataset
    computes them (engine/wko5expr/fitdataset.py)."""
    from backend.settings.repository import SettingsRepository
    from backend.sync import dedup

    fmt = path.suffix.lower().lstrip(".")
    # a new file in a synced folder: the chart Dataset's files stamp rescans (SP-362)
    from backend.engine.wko5expr.datasource import files_changed
    files_changed()

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
            import asyncio
            raw = await asyncio.to_thread(parse_fit, str(path))     # FIT parsing: off the event loop
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
    # the watch's post-workout RPE / feel, when the FIT has it (activity_tags: it outranks the HR effort rule)
    from backend.engine.activity_tags import recorded_from_session
    wf.rpe, wf.feel = recorded_from_session(raw.session)

    return wf
