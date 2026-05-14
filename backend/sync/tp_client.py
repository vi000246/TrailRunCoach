"""
TrainingPeaks sync client.

Uses WKO5's own OAuth2 ROPC flow — reverse engineered from PowerKitOSX.framework:

  Password grant (no client_id — extracted byte-for-byte from binary):
    POST https://oauth.trainingpeaks.com/oauth/token
    grant_type=password&username={u}&password={p}
    &scope=fitness+baseactivity+users+metrics+software+groundcontrol

  Refresh grant:
    grant_type=refresh_token&refresh_token={t}&client_id=WKO5&client_secret=

Tokens are stored in SQLite (sync_state table). Credentials are never persisted.
"""
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import SyncState, Athlete, WorkoutFile

TP_OAUTH_URL = "https://oauth.trainingpeaks.com/oauth/token"
TP_API_BASE = "https://tpapi.trainingpeaks.com/"

# From refresh token template in binary
TP_CLIENT_ID = "WKO5"
TP_CLIENT_SECRET = ""

# Scope string literal from binary (uses + as separator, not spaces)
TP_SCOPE = "fitness+baseactivity+users+metrics+software+groundcontrol"


async def login_password(username: str, password: str, db: AsyncSession, athlete_id: int) -> dict:
    """
    Authenticate with TrainingPeaks using username/password.
    Password grant body extracted byte-for-byte from PowerKitOSX.framework:
      grant_type=password&username=...&password=...&scope=fitness+baseactivity+...
    No client_id in password grant — matches WKO5's exact format.
    Credentials are NOT stored; only the resulting tokens are saved.
    """
    # Build body with proper URL encoding. client_id=WKO5 is required.
    # Scope uses space separator (urllib encodes to %20 or +).
    async with httpx.AsyncClient() as client:
        resp = await client.post(TP_OAUTH_URL, data={
            "grant_type": "password",
            "username": username,
            "password": password,
            "client_id": TP_CLIENT_ID,
            "scope": "fitness baseactivity users metrics software groundcontrol",
        })
        if resp.status_code != 200:
            raise ValueError(f"TP login failed ({resp.status_code}): {resp.text[:300]}")
        token = resp.json()

    # Fetch athlete profile to get tp_athlete_id
    tp_athlete_id = await _fetch_athlete_id(token["access_token"])

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=token.get("expires_in", 3600))

    # Persist tokens (not credentials)
    state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_result.scalar_one_or_none()
    if not state:
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.tp_access_token = token["access_token"]
    state.tp_refresh_token = token.get("refresh_token")
    state.tp_token_expires = expires_at
    await db.commit()

    # Store tp_athlete_id on Athlete record
    if tp_athlete_id:
        athlete_result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
        athlete = athlete_result.scalar_one_or_none()
        if athlete:
            athlete.tp_athlete_id = tp_athlete_id
            await db.commit()

    return {"authenticated": True, "tp_athlete_id": tp_athlete_id}


async def _fetch_athlete_id(access_token: str) -> Optional[int]:
    """Get TP athlete ID from users/v3/user endpoint."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(base_url=TP_API_BASE, headers=headers, timeout=10) as client:
        resp = await client.get("users/v3/user")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("athlete", {}).get("id") or data.get("id")
    return None


async def get_auth_url() -> str:
    """OAuth redirect URL — not needed for ROPC, but kept for compatibility."""
    return (
        "https://oauth.trainingpeaks.com/OAuth/Authorize"
        f"?response_type=code&client_id={TP_CLIENT_ID}"
        f"&redirect_uri=http://localhost:8000/api/v1/auth/tp/callback"
        f"&scope=fitness+baseactivity+users+metrics+software+groundcontrol"
    )


async def exchange_code(code: str, db: AsyncSession, athlete_id: int) -> dict:
    """OAuth authorization code exchange (kept for future use if needed)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(TP_OAUTH_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": TP_CLIENT_ID,
            "client_secret": TP_CLIENT_SECRET,
            "redirect_uri": "http://localhost:8000/api/v1/auth/tp/callback",
        })
        resp.raise_for_status()
        token = resp.json()

    tp_athlete_id = await _fetch_athlete_id(token["access_token"])
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=token.get("expires_in", 3600))
    state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_result.scalar_one_or_none()
    if not state:
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.tp_access_token = token["access_token"]
    state.tp_refresh_token = token.get("refresh_token")
    state.tp_token_expires = expires_at
    await db.commit()
    if tp_athlete_id:
        athlete_result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
        athlete = athlete_result.scalar_one_or_none()
        if athlete:
            athlete.tp_athlete_id = tp_athlete_id
            await db.commit()
    return token


async def _get_valid_token(db: AsyncSession, athlete_id: int) -> Optional[str]:
    """Return valid access token, refreshing if expired."""
    result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = result.scalar_one_or_none()
    if not state or not state.tp_access_token:
        return None
    if state.tp_token_expires and datetime.now(timezone.utc) >= state.tp_token_expires - timedelta(minutes=5):
        async with httpx.AsyncClient() as client:
            # Refresh token format from binary: grant_type=refresh_token&refresh_token=...&client_id=WKO5&client_secret=
            refresh_body = (
                f"grant_type=refresh_token"
                f"&refresh_token={state.tp_refresh_token}"
                f"&client_id={TP_CLIENT_ID}"
                f"&client_secret={TP_CLIENT_SECRET}"
            )
            resp = await client.post(
                TP_OAUTH_URL,
                content=refresh_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 200:
                token = resp.json()
                state.tp_access_token = token["access_token"]
                state.tp_refresh_token = token.get("refresh_token", state.tp_refresh_token)
                state.tp_token_expires = (
                    datetime.now(timezone.utc) + timedelta(seconds=token.get("expires_in", 3600))
                )
                await db.commit()
            else:
                return None
    return state.tp_access_token


async def sync_workouts(db: AsyncSession, athlete_id: int) -> AsyncIterator[dict]:
    """Yield progress dicts as workouts are downloaded from TP."""
    token = await _get_valid_token(db, athlete_id)
    if not token:
        yield {"error": "TP_AUTH_REQUIRED", "hint": "POST /api/v1/auth/tp/login with {username, password}"}
        return

    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if not athlete or not athlete.tp_athlete_id:
        yield {"error": "No TP athlete ID. Run POST /api/v1/auth/tp/login first."}
        return

    sync_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = sync_result.scalar_one_or_none()
    since_date = state.last_sync_cursor if state and state.last_sync_cursor else "2015-01-01"

    headers = {"Authorization": f"Bearer {token}"}
    page = 1
    total_downloaded = 0
    total_checked = 0

    async with httpx.AsyncClient(base_url=TP_API_BASE, headers=headers, timeout=30) as client:
        while True:
            resp = await client.get(
                f"fitness/v2/athletes/{athlete.tp_athlete_id}/workouts/changed"
                f"?date={since_date}&searchDirection=After&pageSize=20&page={page}"
            )
            if resp.status_code != 200:
                yield {"error": f"TP_API_ERROR: {resp.status_code}", "body": resp.text[:200]}
                return

            data = resp.json()
            workouts_page = data if isinstance(data, list) else data.get("workouts", [])
            if not workouts_page:
                break

            for wo in workouts_page:
                wo_id = wo.get("workoutId") or wo.get("id")
                wo_date = wo.get("workoutDay") or (wo.get("startTime") or "")[:10]
                total_checked += 1
                yield {"status": "checking", "workout_date": wo_date, "workout_id": wo_id, "checked": total_checked}

                fit_path = await _download_fit(client, athlete, wo_id, wo_date)
                if fit_path:
                    existing = await db.execute(
                        select(WorkoutFile).where(WorkoutFile.file_path == str(fit_path))
                    )
                    if not existing.scalar_one_or_none():
                        from backend.files.file_service import _import_one_file
                        await _import_one_file(db, athlete_id, fit_path)
                        await db.commit()
                        total_downloaded += 1
                        yield {"status": "downloaded", "workout_date": wo_date, "file": str(fit_path), "total_downloaded": total_downloaded}

            page += 1
            if len(workouts_page) < 20:
                break

    if state:
        from datetime import date
        state.last_sync_at = datetime.now(timezone.utc)
        state.last_sync_cursor = date.today().isoformat()
        await db.commit()

    yield {"status": "complete", "total_downloaded": total_downloaded, "total_checked": total_checked}


async def _download_fit(client: httpx.AsyncClient, athlete, workout_id, workout_date: str) -> Optional[Path]:
    """Download FIT file for a workout to ~/WKO5/{athlete}/{year}/"""
    try:
        detail_resp = await client.get(
            f"fitness/v6/athletes/{athlete.tp_athlete_id}/workouts/{workout_id}/detaildata"
        )
        if detail_resp.status_code != 200:
            return None
        detail = detail_resp.json()
        files = detail.get("files", []) or []
        fit_files = [f for f in files if str(f.get("name", "")).lower().endswith(".fit")]
        if not fit_files:
            return None
        fname = fit_files[0]["name"]

        fit_resp = await client.get(
            f"fitness/v6/athletes/{athlete.tp_athlete_id}/workouts/{workout_id}/filedata/{fname}"
        )
        if fit_resp.status_code != 200:
            return None

        year = workout_date[:4] if workout_date else "2000"
        save_dir = Path(athlete.data_dir) / year
        save_dir.mkdir(exist_ok=True)
        dt_str = workout_date.replace("-", "_") if workout_date else "unknown"
        safe_name = f"{Path(athlete.data_dir).name}_{dt_str}_{workout_id}.fit"
        save_path = save_dir / safe_name
        save_path.write_bytes(fit_resp.content)
        return save_path
    except Exception:
        return None


async def fetch_tp_settings(db: AsyncSession, athlete_id: int) -> Optional[dict]:
    """Fetch athlete settings from TP (FTP, weight, LTHR)."""
    token = await _get_valid_token(db, athlete_id)
    if not token:
        return None
    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if not athlete or not athlete.tp_athlete_id:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=TP_API_BASE, headers=headers, timeout=10) as client:
        resp = await client.get(f"fitness/v1/athletes/{athlete.tp_athlete_id}/settings")
        if resp.status_code == 200:
            return resp.json()
    return None
