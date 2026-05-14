from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.db.database import get_db
from backend.sync.tp_client import get_auth_url, exchange_code, login_password, fetch_tp_settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str
    athlete_id: int = 1


@router.post("/tp/login")
async def tp_login_password(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticate with TrainingPeaks using username/password.
    Uses WKO5's OAuth2 ROPC flow (client_id=WKO5, grant_type=password).
    Credentials are not stored — only the resulting access/refresh tokens.
    """
    try:
        result = await login_password(body.username, body.password, db, body.athlete_id)
        return result
    except Exception as e:
        detail = str(e)
        if "401" in detail or "Unauthorized" in detail:
            raise HTTPException(401, "TP_LOGIN_FAILED: Invalid username or password")
        raise HTTPException(502, f"TP_LOGIN_ERROR: {detail}")


@router.get("/tp/status")
async def tp_auth_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from backend.db.models import SyncState, Athlete
    state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_result.scalar_one_or_none()
    athlete_result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = athlete_result.scalar_one_or_none()
    return {
        "authenticated": bool(state and state.tp_access_token),
        "tp_athlete_id": athlete.tp_athlete_id if athlete else None,
    }


@router.post("/tp/logout")
async def tp_logout(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from backend.db.models import SyncState
    state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_result.scalar_one_or_none()
    if state:
        state.tp_access_token = None
        state.tp_refresh_token = None
        state.tp_token_expires = None
        await db.commit()
    return {"logged_out": True}


@router.get("/tp/oauth")
async def tp_oauth_redirect():
    """OAuth redirect flow (alternative to password login)."""
    url = await get_auth_url()
    return RedirectResponse(url)


@router.get("/tp/callback")
async def tp_callback(code: str, db: AsyncSession = Depends(get_db)):
    await exchange_code(code, db, athlete_id=1)
    return RedirectResponse("/?sync=authenticated")
