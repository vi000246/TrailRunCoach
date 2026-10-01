from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.db.database import get_db
from backend.sync.tp_client import get_auth_url, exchange_code, login_password, fetch_tp_settings
from backend.sync import coros_client
from backend.settings.secrets import SecretKeyMissing

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str
    athlete_id: int = 1
    method: Optional[str] = None      # TP only: auto (default) | web | oauth
    remember: bool = False            # 「記住密碼」: store the password sealed for the automatic re-login


@router.post("/tp/login")
async def tp_login_password(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticate with TrainingPeaks using WKO5's exact OAuth2 ROPC flow:
      grant_type=password (no client_id) → users/v3/user → persist tokens only.
    The password is stored only with remember=true (「記住密碼」), sealed with
    the local key; remember=false deletes a stored one. Never returned.
    Returns: { authenticated, tp_athlete_id, athletes, user_type, premium, can_download, password_saved }
    Note: download requires premium or coach account.
    """
    from backend.sync.tp_client import save_password
    try:
        result = await login_password(body.username, body.password, db, body.athlete_id,
                                      prefer=body.method)
        await save_password(db, body.athlete_id, body.username, body.password if body.remember else None)
        return {**result, "password_saved": bool(body.remember)}
    except SecretKeyMissing as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        detail = str(e)
        # codes from tp_client.TpLoginError (website login fallback)
        if detail.startswith(("TP_LOGIN_CAPTCHA", "TP_LOGIN_MFA")):
            raise HTTPException(403, detail)
        if detail.startswith("TP_LOGIN_FAILED"):
            raise HTTPException(401, detail)
        if detail.startswith("TP_LOGIN_ERROR"):
            raise HTTPException(502, detail)
        if "invalid_grant" in detail or "401" in detail or "Unauthorized" in detail:
            raise HTTPException(401, f"TP_LOGIN_FAILED: {detail}")
        if "400" in detail:
            raise HTTPException(400, f"TP_LOGIN_BAD_REQUEST: {detail}")
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
        # how the stored session was obtained (no values)
        "method": (None if not (state and state.tp_access_token)
                   else "web" if state.tp_web_cookie else "oauth" if state.tp_refresh_token else "token"),
        "password_saved": bool(state and state.tp_password_sealed),     # never the value
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
        state.tp_web_cookie = None
        state.tp_token_expires = None
        state.tp_username = None
        state.tp_password_sealed = None          # 記住密碼: logging out deletes it
        await db.commit()
    return {"logged_out": True}


class CorosLoginRequest(BaseModel):
    email: str
    password: str
    athlete_id: int = 1
    remember: bool = False            # 「記住密碼」


@router.post("/coros/login")
async def coros_login(body: CorosLoginRequest, db: AsyncSession = Depends(get_db)):
    """Log in (one login at a time: a second concurrent one gets 409
    COROS_LOGIN_BUSY — COROS keeps only the latest token). remember=true
    stores the password sealed for the automatic re-login; false deletes it."""
    try:
        result = await coros_client.login(body.email, body.password, db, body.athlete_id)
        await coros_client.save_password(db, body.athlete_id, body.password if body.remember else None)
        return {**result, "password_saved": bool(body.remember)}
    except coros_client.LoginBusy as e:
        raise HTTPException(409, str(e))
    except SecretKeyMissing as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        detail = str(e)
        if "401" in detail or "login failed" in detail.lower():
            raise HTTPException(401, f"COROS_LOGIN_FAILED: {detail}")
        raise HTTPException(502, f"COROS_LOGIN_ERROR: {detail}")


@router.get("/coros/status")
async def coros_auth_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from backend.db.models import SyncState
    from datetime import datetime, timezone
    state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_result.scalar_one_or_none()
    saved = bool(state and state.coros_password_sealed)          # never the value
    if not state or not state.coros_access_token:
        return {"authenticated": False, "email": None, "password_saved": saved}
    # SQLite returns naive datetimes; treat a naive expiry as UTC so the
    # comparison against an aware "now" doesn't raise TypeError.
    expiry = state.coros_token_expires
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    expired = expiry is not None and datetime.now(timezone.utc) >= expiry
    return {
        "authenticated": not expired,
        "email": state.coros_email,
        "token_expires": state.coros_token_expires.isoformat() if state.coros_token_expires else None,
        "last_sync": state.coros_last_sync_at.isoformat() if state.coros_last_sync_at else None,
        "password_saved": saved,
        # an expired token is renewed on the next sync / push when a password is remembered
        "auto_relogin": saved,
    }


@router.post("/coros/logout")
async def coros_logout(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from backend.db.models import SyncState
    state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_result.scalar_one_or_none()
    if state:
        state.coros_access_token = None
        state.coros_token_expires = None
        state.coros_email = None
        state.coros_password_sealed = None       # 記住密碼: logging out deletes it
        await db.commit()
    return {"logged_out": True}


class RememberBody(BaseModel):
    remember: bool
    athlete_id: int = 1


@router.put("/{source}/remember")
async def set_remember(source: str, body: RememberBody, db: AsyncSession = Depends(get_db)):
    """Unticking 「記住密碼」 deletes the stored password immediately. Ticking
    it alone stores nothing (the password is only sent with a login)."""
    if source not in ("coros", "tp"):
        raise HTTPException(400, "source must be coros or tp")
    if not body.remember:
        if source == "coros":
            await coros_client.save_password(db, body.athlete_id, None)
        else:
            from backend.sync.tp_client import save_password
            await save_password(db, body.athlete_id, None, None)
    from sqlalchemy import select
    from backend.db.models import SyncState
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == body.athlete_id))).scalar_one_or_none()
    saved = bool(st and (st.coros_password_sealed if source == "coros" else st.tp_password_sealed))
    return {"password_saved": saved}


@router.get("/tp/oauth")
async def tp_oauth_redirect():
    """OAuth redirect flow (alternative to password login)."""
    url = await get_auth_url()
    return RedirectResponse(url)


@router.get("/tp/callback")
async def tp_callback(code: str, db: AsyncSession = Depends(get_db)):
    await exchange_code(code, db, athlete_id=1)
    return RedirectResponse("/?sync=authenticated")
