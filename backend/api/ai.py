import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.db.database import get_db
from backend.db.models import AthleteSettings
from backend.engine.ai.client import get_ai_client, CLAUDE_MODELS, OPENAI_MODELS, GEMINI_MODELS
from backend.engine.ai.context import build_context, build_system_prompt
from backend.engine.ai.zones import compute_zones

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class ChatRequest(BaseModel):
    athlete_id: int
    message: str
    history: list[dict] = []


@router.get("/models")
async def list_models():
    return {"claude": CLAUDE_MODELS, "openai": OPENAI_MODELS, "gemini": GEMINI_MODELS}


@router.get("/status/{athlete_id}")
async def ai_status(athlete_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    s = result.scalars().first()
    if not s or not s.ai_provider or not s.ai_api_key:
        return {"configured": False}
    return {
        "configured": True,
        "provider": s.ai_provider,
        "model": s.ai_model,
    }


@router.get("/zones")
async def ai_zones(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Power/HR training zone boundaries computed from the athlete's settings."""
    s = (await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )).scalars().first()
    ftp = (s.run_ftp_w or s.ftp_w) if s else None
    lthr = s.lthr if s else None
    return {"athlete_id": athlete_id, "run_ftp_w": ftp, "lthr": lthr, **compute_zones(ftp, lthr)}


@router.post("/chat")
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == body.athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    s = result.scalars().first()
    if not s or not s.ai_provider or not s.ai_api_key:
        raise HTTPException(status_code=400, detail="AI_NOT_CONFIGURED")

    context_text = await build_context(db, body.athlete_id)
    system = await build_system_prompt(db, body.athlete_id)
    user_prompt = f"{context_text}\n\n=== 問題 ===\n{body.message}"

    ai_client = get_ai_client(
        provider=s.ai_provider,
        api_key=s.ai_api_key,
        model=s.ai_model or (CLAUDE_MODELS[1] if s.ai_provider == "claude" else OPENAI_MODELS[0]),
    )

    async def generate():
        try:
            async for chunk in ai_client.stream(system=system, user=user_prompt):
                yield {"data": json.dumps({"chunk": chunk})}
            yield {"data": json.dumps({"done": True})}
        except Exception as e:
            yield {"data": json.dumps({"error": str(e)})}

    return EventSourceResponse(generate())
