import json
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.db.database import get_db
from backend.db.models import SyncState, WorkoutFile
from backend.settings.repository import SettingsRepository
from backend.sync.tp_client import sync_workouts, fetch_tp_settings
from backend.sync import coros_client, dedup, purge, runner, storage


async def _disabled(db: AsyncSession, athlete_id: int, source: str) -> bool:
    return not await SettingsRepository(db, athlete_id).get(f"sync.{source}.enabled")

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.get("/inventory")
async def sync_inventory(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Loaded-data inventory: counts by source/sport, date range, per-source last sync."""
    base = WorkoutFile.athlete_id == athlete_id
    total = (await db.execute(
        select(func.count(WorkoutFile.id)).where(base))).scalar() or 0
    by_source = {
        (k or "unknown"): v for k, v in (await db.execute(
            select(WorkoutFile.source, func.count(WorkoutFile.id))
            .where(base).group_by(WorkoutFile.source))).all()
    }
    by_sport = {
        (k or "unknown"): v for k, v in (await db.execute(
            select(WorkoutFile.sport, func.count(WorkoutFile.id))
            .where(base).group_by(WorkoutFile.sport))).all()
    }
    dr = (await db.execute(
        select(func.min(WorkoutFile.workout_date), func.max(WorkoutFile.workout_date))
        .where(base))).first()
    st = (await db.execute(
        select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    duplicates = (await db.execute(
        select(func.count(WorkoutFile.id)).where(base, WorkoutFile.duplicate_of.isnot(None)))).scalar() or 0
    return {
        "total": total,
        "duplicates": duplicates,     # same activity from a non-primary source (not in totals)
        "by_source": by_source,
        "by_sport": by_sport,
        "date_min": dr[0].isoformat() if dr and dr[0] else None,
        "date_max": dr[1].isoformat() if dr and dr[1] else None,
        "last_sync": {
            "coros": st.coros_last_sync_at.isoformat() if st and st.coros_last_sync_at else None,
            "tp": st.last_sync_at.isoformat() if st and st.last_sync_at else None,
        },
    }


@router.post("/start")
async def start_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger TrainingPeaks sync. Streams progress events via Server-Sent Events.

    Query params:
      - since: ISO date "YYYY-MM-DD" (default: last_sync_cursor or 2010-01-01)
      - page_size: TP pagination size (1..100, default 20 — matches WKO5)
    """
    return await _sse_sync(db, "tp", athlete_id, since)


async def _sse_sync(db: AsyncSession, source: str, athlete_id: int, since: Optional[str]):
    """SSE stream of one run through the shared runner (per-source lock).
    409 SYNC_BUSY when that source is already running."""
    if runner.is_busy(source):
        raise HTTPException(409, "SYNC_BUSY")
    disabled = await _disabled(db, athlete_id, runner.SETTING_NAME[source])

    async def generate():
        if disabled:
            yield {"event": "sync_progress", "data": json.dumps(
                {"status": "error", "error": "SYNC_DISABLED", "source": source})}
            return
        async for event in runner.stream(db, source, athlete_id, since=since, trigger="manual"):
            yield {"event": "sync_progress", "data": json.dumps(event)}
    return EventSourceResponse(generate())


@router.post("/coros/start")
async def start_coros_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger Coros sync. Streams progress events via Server-Sent Events."""
    return await _sse_sync(db, "coros", athlete_id, since)


@router.get("/sources")
async def sync_sources(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Everything the settings page shows per source: login state, enabled,
    busy, last sync / result, files on disk, DB rows, date span."""
    repo = SettingsRepository(db, athlete_id)
    out = {}
    for src in runner.SOURCES:
        name = runner.SETTING_NAME[src]
        last = await runner.last_sync_at(db, src, athlete_id)
        out[src] = {
            "logged_in": await runner.logged_in(db, src, athlete_id),
            "enabled": await repo.get(f"sync.{name}.enabled"),
            "busy": runner.is_busy(src),
            "last_sync_at": last.isoformat() if last else None,
            "last_result": await repo.get(f"sync.{name}.last_result"),
            "stats": await purge.source_stats(db, src, athlete_id),
        }
    return out


@router.post("/auto")
async def auto_sync(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Page-open trigger: starts a background sync for every enabled,
    logged-in, idle source whose last sync is older than
    sync.auto_on_open.hours. A no-op when fresh, busy or switched off.
    Returns immediately."""
    repo = SettingsRepository(db, athlete_id)
    if not await repo.get("sync.auto_on_open.enabled"):
        return {"started": [], "skipped": {s: "auto_off" for s in runner.SOURCES}}
    hours = float(await repo.get("sync.auto_on_open.hours"))
    now = datetime.now(timezone.utc)
    started, skipped = [], {}
    for src, state in (await runner.ready_sources(db, athlete_id)).items():
        if state != "ready":
            skipped[src] = state
            continue
        last = await runner.last_sync_at(db, src, athlete_id)
        if last and (now - last).total_seconds() < hours * 3600:
            skipped[src] = "fresh"
            continue
        if runner.start_background(src, athlete_id, trigger="open") is None:
            skipped[src] = "busy"
        else:
            started.append(src)
    return {"started": started, "skipped": skipped, "threshold_hours": hours}


@router.get("/compare")
async def compare_sources_api(a: str = "wko5", b: str = "coros", since: Optional[date] = None):
    """Same activities from two chart data sources (wko5 | coros | tp),
    paired by start time ±2 min: per-activity duration / distance / climbing /
    NP / TSS and relative differences (flagged beyond tolerance)."""
    import asyncio
    from backend.api.wko5views import ATHLETE_DIR
    from backend.engine.wko5expr.sourcecompare import compare
    if a not in ("wko5", "coros", "tp") or b not in ("wko5", "coros", "tp") or a == b:
        raise HTTPException(400, "a and b must be two different sources of wko5, coros, tp")
    return await asyncio.to_thread(compare, a, b, ATHLETE_DIR, since)


@router.delete("/{source}/files")
async def delete_source_files(source: str, athlete_id: int = 1,
                              date_from: Optional[date] = None, date_to: Optional[date] = None,
                              db: AsyncSession = Depends(get_db)):
    """Delete one source's synced FIT files + DB rows (confined to
    ~/.wko5coach/fit/<source>/), rebuild de-dup, reset the cursor.
    source ∈ {coros, tp}; 409 SYNC_BUSY while that source syncs."""
    if source not in storage.SOURCES:
        raise HTTPException(400, "source must be coros or tp")
    try:
        return await purge.delete_source_files(db, source, athlete_id, date_from, date_to)
    except runner.SyncBusy:
        raise HTTPException(409, "SYNC_BUSY")


@router.get("/status")
async def sync_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = result.scalar_one_or_none()
    if not state:
        return {"authenticated": False}
    return {
        "authenticated": bool(state.tp_access_token),
        "last_sync": state.last_sync_at.isoformat() if state.last_sync_at else None,
        "cursor": state.last_sync_cursor,
    }


class SyncSettingsBody(BaseModel):
    primary_source: Optional[str] = None
    timezone: Optional[str] = None
    coros_enabled: Optional[bool] = None
    trainingpeaks_enabled: Optional[bool] = None
    tp_use_wko5_client: Optional[bool] = None     # null = auto (on when creds configured)
    daily_sync_time: Optional[str] = None         # "HH:MM" or null (off)
    auto_on_open: Optional[bool] = None
    auto_on_open_hours: Optional[float] = None
    chart_data_source: Optional[str] = None       # wko5 | coros | tp
    map_basemap: Optional[str] = None             # workout map default basemap id
    map_overlays: Optional[list[str]] = None      # workout map default overlay ids
    exclude_bad_activities: Optional[bool] = None  # engine/bad_activity.py (設定 → 資料校正)


_SETTING_KEYS = {"exclude_bad_activities": "activities.exclude_bad","primary_source": "sync.primary_source", "timezone": "athlete.timezone",
                 "coros_enabled": "sync.coros.enabled",
                 "trainingpeaks_enabled": "sync.trainingpeaks.enabled",
                 "tp_use_wko5_client": "sync.trainingpeaks.use_wko5_client",
                 "daily_sync_time": "sync.schedule.daily_time",
                 "auto_on_open": "sync.auto_on_open.enabled",
                 "auto_on_open_hours": "sync.auto_on_open.hours",
                 "chart_data_source": "charts.data_source",
                 "map_basemap": "charts.map.basemap", "map_overlays": "charts.map.overlays"}


async def _sync_settings(repo: SettingsRepository) -> dict:
    from backend.sync.tp_client import lookup_client_creds
    out = {k: await repo.get(v) for k, v in _SETTING_KEYS.items()}
    creds, source = lookup_client_creds()
    out["tp_client_credentials_configured"] = creds is not None      # never the values
    out["tp_client_credentials_source"] = source                     # env|file|sealed|wko5_exe|none
    from backend.sync.tp_client import TP_CLIENT_FILE
    out["tp_client_file_exists"] = TP_CLIENT_FILE.exists()           # existence only, never read here
    from backend.settings.secrets import KEY_DOC, key_status
    out["secret_key_status"] = key_status()      # env|file|missing|none
    if out["secret_key_status"] == "missing":
        out["secret_key_hint"] = f"SECRET_KEY_MISSING: deploy the key (chezmoi apply) or set WKO5COACH_SECRET_KEY — see {KEY_DOC}"
    return out


@router.get("/settings")
async def get_sync_settings(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Primary source, per-source enable flags and the athlete time zone."""
    return await _sync_settings(SettingsRepository(db, athlete_id))


@router.put("/settings")
async def put_sync_settings(body: SyncSettingsBody, athlete_id: int = 1,
                            db: AsyncSession = Depends(get_db)):
    """Only the fields sent are changed. Changing the primary source re-runs
    the cross-source de-dup so totals switch to that source immediately.
    (`null` primary_source = first imported wins.)"""
    repo = SettingsRepository(db, athlete_id)
    sent = body.model_dump(exclude_unset=True)
    try:
        for k, v in sent.items():
            await repo.set(_SETTING_KEYS[k], v)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rebuilt = await dedup.rebuild(db, athlete_id) if "primary_source" in sent else None
    await db.commit()
    return {**await _sync_settings(repo), "dedup": rebuilt}


@router.post("/dedup/rebuild")
async def rebuild_dedup(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    r = await dedup.rebuild(db, athlete_id)
    await db.commit()
    return r


@router.get("/tp/settings")
async def get_tp_settings(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Fetch TP athlete settings (FTP, weight, LTHR, etc)."""
    data = await fetch_tp_settings(db, athlete_id)
    if data is None:
        return {"error": "TP_AUTH_REQUIRED_OR_NO_ATHLETE"}
    return data
