import json
from typing import Optional

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.db.database import get_db
from backend.db.models import SyncState, WorkoutFile
from backend.sync.tp_client import sync_workouts, fetch_tp_settings
from backend.sync import coros_client

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
    return {
        "total": total,
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
    async def generate():
        async for event in sync_workouts(db, athlete_id, since=since, page_size=page_size):
            yield {"event": "sync_progress", "data": json.dumps(event)}
    return EventSourceResponse(generate())


@router.post("/coros/start")
async def start_coros_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger Coros sync. Streams progress events via Server-Sent Events."""
    async def generate():
        async for event in coros_client.sync_workouts(db, athlete_id, since=since):
            yield {"event": "sync_progress", "data": json.dumps(event)}
    return EventSourceResponse(generate())


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


@router.get("/tp/settings")
async def get_tp_settings(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Fetch TP athlete settings (FTP, weight, LTHR, etc)."""
    data = await fetch_tp_settings(db, athlete_id)
    if data is None:
        return {"error": "TP_AUTH_REQUIRED_OR_NO_ATHLETE"}
    return data
