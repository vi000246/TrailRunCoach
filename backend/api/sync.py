import json
from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import SyncState
from backend.sync.tp_client import sync_workouts

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("/start")
async def start_sync(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    async def generate():
        async for event in sync_workouts(db, athlete_id):
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
