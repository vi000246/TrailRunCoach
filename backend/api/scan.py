from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import Athlete
from backend.files.file_service import scan_and_import

router = APIRouter(prefix="/api/v1/scan", tags=["scan"])


@router.post("")
async def trigger_scan(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Athlete).where(Athlete.id == 1))
    athlete = result.scalar_one_or_none()
    if not athlete:
        return {"error": "No athlete configured. POST /api/v1/athletes/bootstrap first."}
    summary = await scan_and_import(db, athlete.id, athlete.data_dir)
    return summary
