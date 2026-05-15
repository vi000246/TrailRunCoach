"""
TrainingPeaks sync client — reverse-engineered byte-for-byte from
WKO5 PowerKitOSX.framework (Build 590, arm64+x86_64).

Strings extracted from the binary:

  Password grant body — VERIFIED 2026-05-15 against live TP OAuth server:
    grant_type=password&username=<u>&password=<p>
    &scope=fitness+baseactivity+users+metrics+software+groundcontrol
    &client_id=WKO5
  (Binary string constants didn't show client_id adjacent to grant_type=password,
  but live API test confirmed: without client_id → empty 400; with client_id=WKO5
  → proper "invalid_grant" error for wrong creds = format is accepted.)

  Refresh grant body (client_secret has empty value):
    grant_type=refresh_token&refresh_token=<t>&client_id=WKO5&client_secret=

  Endpoints (format strings — %s = base, %d = athlete id, %lld = workout id):
    https://oauth.trainingpeaks.com/oauth/token
    https://tpapi.trainingpeaks.com/
    users/v3/user
    fitness/v1/athletes/%d/settings
    fitness/v2/athletes/%d/workouts/changed?date=%s&searchDirection=After&pageSize=%u&page=%u
    fitness/v6/athletes/%d/workouts/%lld/detaildata
    fitness/v6/athletes/%d/workouts/%lld/filedata/%s
    metrics/v2/athletes/%d/timedmetrics/%04u-%02u-%02u/%04u-%02u-%02u

  JSON field names (from binary):
    user.userId, user.userName, user.userType, user.athletes[*]
    workoutId, workoutDay, startTime, athleteId, workoutDeviceFileInfos[*].fileName
    Response of filedata endpoint: {"data": "<base64-of-gzip-compressed-FIT>"}
    (PowerKit decodes via b64decode → zlib inflate, logging
     "Error inflating device file %s" on failure)

  Premium gate (binary string): "Download is allowed only from premium
  and coach accounts." — TP returns 403 otherwise.

Tokens are persisted in SQLite (sync_state). Credentials are NEVER stored.
"""
import base64
import gzip
import zlib
import logging
import httpx
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import SyncState, Athlete, WorkoutFile

log = logging.getLogger(__name__)

TP_OAUTH_URL = "https://oauth.trainingpeaks.com/oauth/token"
TP_API_BASE = "https://tpapi.trainingpeaks.com/"

# Refresh grant: client_id=WKO5 with empty client_secret (literal from binary).
TP_CLIENT_ID = "WKO5"
TP_CLIENT_SECRET = ""

# Scope string is concatenated literally in the binary with '+' separators
# (NOT URL-encoded spaces). The OAuth server treats '+' as the scope delimiter.
TP_SCOPE = "fitness+baseactivity+users+metrics+software+groundcontrol"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def login_password(
    username: str, password: str, db: AsyncSession, athlete_id: int
) -> dict:
    """
    Password grant — VERIFIED format against live TP OAuth server 2026-05-15:
      grant_type=password&username={u}&password={p}
      &scope=fitness+baseactivity+...&client_id=WKO5

    client_id=WKO5 IS required even in the password grant (binary string
    constants showed it only in refresh, but live test: without it → empty 400,
    with it → proper invalid_grant = server accepts the format).
    """
    body = (
        f"grant_type=password"
        f"&username={_urlquote(username)}"
        f"&password={_urlquote(password)}"
        f"&scope={TP_SCOPE}"
        f"&client_id={TP_CLIENT_ID}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TP_OAUTH_URL,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        body = resp.text[:400]
        raise ValueError(f"TP login failed (HTTP {resp.status_code}): {body!r}")
    try:
        token = resp.json()
    except Exception:
        raise ValueError(f"TP login: response is not JSON: {resp.text[:200]!r}")

    # users/v3/user returns the authenticated user and accessible athletes.
    try:
        user_info = await _fetch_user(token["access_token"])
        tp_athlete_id, athletes_list, user_type, premium = _extract_athlete_id(user_info)
        log.info("TP user info raw: %s", user_info)
    except Exception as e:
        log.warning("users/v3/user failed (non-fatal): %s", e)
        tp_athlete_id, athletes_list, user_type, premium = None, [], "", False

    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=token.get("expires_in", 3600)
    )

    state_result = await db.execute(
        select(SyncState).where(SyncState.athlete_id == athlete_id)
    )
    state = state_result.scalar_one_or_none()
    if not state:
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.tp_access_token = token["access_token"]
    state.tp_refresh_token = token.get("refresh_token")
    state.tp_token_expires = expires_at

    if tp_athlete_id:
        athlete_result = await db.execute(
            select(Athlete).where(Athlete.id == athlete_id)
        )
        athlete = athlete_result.scalar_one_or_none()
        if athlete:
            athlete.tp_athlete_id = tp_athlete_id

    await db.commit()

    return {
        "authenticated": True,
        "tp_athlete_id": tp_athlete_id,
        "athletes": athletes_list,
        "user_type": user_type,
        "premium": premium,
        "can_download": _can_download(user_type, premium),
    }


async def _fetch_user(access_token: str) -> dict:
    """GET users/v3/user — returns user profile + accessible athletes."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(
        base_url=TP_API_BASE, headers=headers, timeout=10
    ) as client:
        resp = await client.get("users/v3/user")
        if resp.status_code != 200:
            raise ValueError(
                f"users/v3/user failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()


def _extract_athlete_id(user_info: dict) -> tuple[Optional[int], list[dict], str, bool]:
    """
    Parse users/v3/user response. Possible shapes from the binary strings:
      { "user": { "userId":int, "userName":str, "userType":str,
                  "athletes": [{...}], "premium": bool } }
    OR the top-level might already be the user object.
    For a self-coached athlete, userId IS the athleteId (no separate athletes list).
    """
    log.debug("_extract_athlete_id raw: %s", user_info)
    # Unwrap "user" envelope if present
    root = user_info.get("user") if isinstance(user_info.get("user"), dict) else user_info
    user_type = root.get("userType", "")
    premium = bool(root.get("premium", False))
    athletes = root.get("athletes") or []

    # Build a clean athletes list for display
    clean_athletes = []
    if athletes:
        for a in athletes:
            aid = a.get("id") or a.get("athleteId") or a.get("userId")
            if aid:
                clean_athletes.append({"id": aid, "name": a.get("name", "")})

    # Find the primary athlete ID
    if clean_athletes:
        return clean_athletes[0]["id"], clean_athletes, user_type, premium

    # Fall back: athleteId or userId on the root object
    aid = root.get("athleteId") or root.get("userId")
    if aid:
        return aid, [{"id": aid, "self": True}], user_type, premium

    return None, [], user_type, premium

    return None, [], user_type, premium


def _can_download(user_type: str, premium: bool) -> bool:
    """Binary: 'Download is allowed only from premium and coach accounts.'"""
    if premium:
        return True
    ut = (user_type or "").lower()
    return "coach" in ut or "premium" in ut


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

async def _refresh_token(state: SyncState, db: AsyncSession) -> bool:
    """
    Refresh flow — body matches binary literally:
      grant_type=refresh_token&refresh_token={t}&client_id=WKO5&client_secret=
    """
    if not state.tp_refresh_token:
        return False
    body = (
        f"grant_type=refresh_token"
        f"&refresh_token={_urlquote(state.tp_refresh_token)}"
        f"&client_id={TP_CLIENT_ID}"
        f"&client_secret={TP_CLIENT_SECRET}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TP_OAUTH_URL,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        log.warning("TP refresh failed (%d): %s", resp.status_code, resp.text[:200])
        return False
    token = resp.json()
    state.tp_access_token = token["access_token"]
    state.tp_refresh_token = token.get("refresh_token", state.tp_refresh_token)
    state.tp_token_expires = datetime.now(timezone.utc) + timedelta(
        seconds=token.get("expires_in", 3600)
    )
    await db.commit()
    return True


async def _get_valid_token(db: AsyncSession, athlete_id: int) -> Optional[str]:
    result = await db.execute(
        select(SyncState).where(SyncState.athlete_id == athlete_id)
    )
    state = result.scalar_one_or_none()
    if not state or not state.tp_access_token:
        return None
    # Refresh if within 5 minutes of expiry.
    if state.tp_token_expires and datetime.now(timezone.utc) >= (
        state.tp_token_expires - timedelta(minutes=5)
    ):
        ok = await _refresh_token(state, db)
        if not ok:
            return None
    return state.tp_access_token


# ---------------------------------------------------------------------------
# Workout sync
# ---------------------------------------------------------------------------

async def sync_workouts(
    db: AsyncSession,
    athlete_id: int,
    since: Optional[str] = None,
    page_size: int = 20,
) -> AsyncIterator[dict]:
    """
    Yield SSE-friendly progress dicts as workouts are downloaded.
    Mirrors WKO5's PKTrainingPeaksDownload flow:
      1. workouts/changed (paged)
      2. For each new workout → detaildata → list of fileName
      3. For each .fit fileName → filedata/{name} → base64 → inflate → write
    """
    token = await _get_valid_token(db, athlete_id)
    if not token:
        yield {
            "error": "TP_AUTH_REQUIRED",
            "hint": "POST /api/v1/auth/tp/login with {username, password}",
        }
        return

    athlete_result = await db.execute(
        select(Athlete).where(Athlete.id == athlete_id)
    )
    athlete = athlete_result.scalar_one_or_none()
    if not athlete or not athlete.tp_athlete_id:
        yield {"error": "TP_ATHLETE_UNKNOWN", "hint": "Run /api/v1/auth/tp/login first"}
        return

    state_result = await db.execute(
        select(SyncState).where(SyncState.athlete_id == athlete_id)
    )
    state = state_result.scalar_one_or_none()
    cursor = since or (state.last_sync_cursor if state else None) or "2010-01-01"

    headers = {"Authorization": f"Bearer {token}"}
    page = 1
    total_downloaded = 0
    total_checked = 0
    errors: list[str] = []

    async with httpx.AsyncClient(
        base_url=TP_API_BASE, headers=headers, timeout=60
    ) as client:
        while True:
            # workouts/changed format string from binary:
            #   fitness/v2/athletes/%d/workouts/changed?date=%s&searchDirection=After&pageSize=%u&page=%u
            url = (
                f"fitness/v2/athletes/{athlete.tp_athlete_id}/workouts/changed"
                f"?date={cursor}&searchDirection=After"
                f"&pageSize={page_size}&page={page}"
            )
            try:
                resp = await client.get(url)
            except httpx.HTTPError as e:
                yield {"error": "TP_API_ERROR", "detail": str(e)}
                return
            if resp.status_code != 200:
                yield {
                    "error": "TP_API_ERROR",
                    "status": resp.status_code,
                    "body": resp.text[:200],
                }
                return
            body = resp.json()
            page_items = body if isinstance(body, list) else body.get("workouts", [])
            if not page_items:
                break

            for wo in page_items:
                wo_id = wo.get("workoutId") or wo.get("id")
                wo_day = wo.get("workoutDay") or (wo.get("startTime") or "")[:10]
                if not wo_id:
                    continue
                total_checked += 1
                yield {
                    "status": "checking",
                    "workout_id": wo_id,
                    "workout_date": wo_day,
                    "checked": total_checked,
                }

                # Skip if already imported via tp_workout_id.
                exists = await db.execute(
                    select(WorkoutFile).where(
                        WorkoutFile.tp_workout_id == wo_id,
                        WorkoutFile.athlete_id == athlete_id,
                    )
                )
                if exists.scalar_one_or_none():
                    yield {
                        "status": "skipped",
                        "workout_id": wo_id,
                        "workout_date": wo_day,
                        "reason": "already_imported",
                    }
                    continue

                try:
                    fit_path = await _download_workout_fit(
                        client, athlete, wo_id, wo_day
                    )
                except Exception as e:
                    msg = f"workout {wo_id}: {e}"
                    errors.append(msg)
                    yield {
                        "status": "error",
                        "workout_id": wo_id,
                        "workout_date": wo_day,
                        "detail": str(e),
                    }
                    continue

                if not fit_path:
                    yield {
                        "status": "no_file",
                        "workout_id": wo_id,
                        "workout_date": wo_day,
                    }
                    continue

                # Register + parse via existing import pipeline.
                from backend.files.file_service import _import_one_file
                try:
                    record = await _import_one_file(
                        db, athlete_id, fit_path, source="trainingpeaks",
                        tp_workout_id=wo_id,
                    )
                except TypeError:
                    # Backward-compat: older signature without source/tp_workout_id.
                    record = await _import_one_file(db, athlete_id, fit_path)
                    if record is not None:
                        record.source = "trainingpeaks"
                        record.tp_workout_id = wo_id
                await db.commit()
                total_downloaded += 1
                yield {
                    "status": "downloaded",
                    "workout_id": wo_id,
                    "workout_date": wo_day,
                    "file": str(fit_path),
                    "total_downloaded": total_downloaded,
                }

            page += 1
            if len(page_items) < page_size:
                break

    if state:
        state.last_sync_at = datetime.now(timezone.utc)
        state.last_sync_cursor = date.today().isoformat()
        await db.commit()

    yield {
        "status": "complete",
        "total_downloaded": total_downloaded,
        "total_checked": total_checked,
        "errors": errors,
    }


async def _download_workout_fit(
    client: httpx.AsyncClient, athlete: Athlete, workout_id: int, workout_day: str
) -> Optional[Path]:
    """
    Two-step download as in PKTrainingPeaksDownload:
      1. GET fitness/v6/athletes/{aid}/workouts/{wid}/detaildata
         → JSON with `workoutDeviceFileInfos: [ { fileName, ... } ]`
      2. GET fitness/v6/athletes/{aid}/workouts/{wid}/filedata/{fileName}
         → JSON with `data: <base64-gzip-of-fit-bytes>`
      3. base64 decode → zlib inflate → write to ~/WKO5/{name}/{year}/
    """
    detail_url = (
        f"fitness/v6/athletes/{athlete.tp_athlete_id}/workouts/{workout_id}/detaildata"
    )
    resp = await client.get(detail_url)
    if resp.status_code != 200:
        log.debug(
            "detaildata %s -> %d: %s", workout_id, resp.status_code, resp.text[:120]
        )
        return None

    detail = resp.json()

    # The detaildata JSON also confirms athleteId — binary asserts match.
    returned_aid = detail.get("athleteId")
    if returned_aid and returned_aid != athlete.tp_athlete_id:
        log.warning(
            "TP detaildata athleteId mismatch: expected %s got %s",
            athlete.tp_athlete_id, returned_aid,
        )

    files = detail.get("workoutDeviceFileInfos") or detail.get("files") or []
    if not files:
        return None

    # Prefer .fit, fall back to first listed file.
    fit_info = next(
        (f for f in files if str(f.get("fileName", "")).lower().endswith(".fit")),
        files[0],
    )
    file_name = fit_info.get("fileName")
    if not file_name:
        return None

    fdata_url = (
        f"fitness/v6/athletes/{athlete.tp_athlete_id}/workouts/{workout_id}"
        f"/filedata/{file_name}"
    )
    fresp = await client.get(fdata_url)
    if fresp.status_code != 200:
        log.debug(
            "filedata %s/%s -> %d", workout_id, file_name, fresp.status_code
        )
        return None

    # The body can be:
    #   (a) JSON with `{ "data": "<base64-of-gzip-fit>" }` (observed via binary
    #       symbols b64decode + 'Error inflating device file'), or
    #   (b) Raw FIT bytes if `Accept: application/octet-stream` is requested.
    # We handle both for robustness.
    raw = _decode_filedata_response(fresp)
    if not raw or len(raw) < 14:  # minimal FIT header is 14 bytes
        return None

    # FIT files start with: <header_size:1> <protocol_ver:1> <profile_ver:2>
    #                       <data_size:4> ".FIT" <crc:2>
    if raw[8:12] != b".FIT":
        log.warning(
            "downloaded blob for workout %s does not look like a FIT file (got %r)",
            workout_id, raw[:16],
        )

    year = (workout_day or "2000-01-01")[:4]
    save_dir = Path(athlete.data_dir) / year
    save_dir.mkdir(parents=True, exist_ok=True)

    safe_day = (workout_day or "unknown").replace("-", "_")
    save_path = save_dir / f"{Path(athlete.data_dir).name}_{safe_day}_{workout_id}.fit"
    save_path.write_bytes(raw)
    return save_path


def _decode_filedata_response(resp: httpx.Response) -> Optional[bytes]:
    """
    Convert TP filedata response into raw FIT bytes, matching PowerKit:
      b64decode("data") → zlib inflate → FIT.
    Some TP responses serve raw bytes directly; auto-detect.
    """
    ctype = resp.headers.get("content-type", "").lower()
    body = resp.content

    # Path A — JSON envelope.
    if "json" in ctype or body[:1] in (b"{", b"["):
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            b64 = payload.get("data") or payload.get("fileData") or payload.get("content")
            if isinstance(b64, str):
                try:
                    compressed = base64.b64decode(b64, validate=False)
                except Exception as e:
                    log.warning("base64 decode failed: %s", e)
                    return None
                return _inflate_any(compressed)

    # Path B — raw bytes. May already be a FIT file or may be base64 text,
    # or may be raw zlib/gzip data.
    if body[:4] == b"\x0c\x10" or (len(body) >= 12 and body[8:12] == b".FIT"):
        return body
    return _inflate_any(body) or body


def _inflate_any(data: bytes) -> Optional[bytes]:
    """Try gzip, then raw zlib, then deflate-raw. Mirrors libz behavior."""
    if not data:
        return None
    # gzip magic
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError as e:
            log.warning("gzip decompress failed: %s", e)
    # zlib header (0x78 = deflate / 32K window)
    try:
        return zlib.decompress(data)
    except zlib.error:
        pass
    # Raw deflate
    try:
        return zlib.decompress(data, -15)
    except zlib.error:
        pass
    return None


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

async def fetch_tp_settings(db: AsyncSession, athlete_id: int) -> Optional[dict]:
    """GET fitness/v1/athletes/{id}/settings — FTP, weight, LTHR, zones."""
    token = await _get_valid_token(db, athlete_id)
    if not token:
        return None
    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if not athlete or not athlete.tp_athlete_id:
        return None
    async with httpx.AsyncClient(
        base_url=TP_API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    ) as client:
        resp = await client.get(f"fitness/v1/athletes/{athlete.tp_athlete_id}/settings")
        if resp.status_code == 200:
            return resp.json()
        log.warning("tp settings %d: %s", resp.status_code, resp.text[:200])
    return None


async def get_auth_url() -> str:
    """OAuth authorization-code redirect URL (kept for completeness)."""
    return (
        "https://oauth.trainingpeaks.com/OAuth/Authorize"
        f"?response_type=code&client_id={TP_CLIENT_ID}"
        f"&redirect_uri=http://localhost:8000/api/v1/auth/tp/callback"
        f"&scope={TP_SCOPE}"
    )


async def exchange_code(code: str, db: AsyncSession, athlete_id: int) -> dict:
    """Authorization-code exchange (kept for future use if needed)."""
    body = (
        f"grant_type=authorization_code"
        f"&code={_urlquote(code)}"
        f"&client_id={TP_CLIENT_ID}"
        f"&client_secret={TP_CLIENT_SECRET}"
        f"&redirect_uri=http://localhost:8000/api/v1/auth/tp/callback"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TP_OAUTH_URL,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token = resp.json()

    user_info = await _fetch_user(token["access_token"])
    tp_athlete_id, _, _, _ = _extract_athlete_id(user_info)

    state_result = await db.execute(
        select(SyncState).where(SyncState.athlete_id == athlete_id)
    )
    state = state_result.scalar_one_or_none()
    if not state:
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.tp_access_token = token["access_token"]
    state.tp_refresh_token = token.get("refresh_token")
    state.tp_token_expires = datetime.now(timezone.utc) + timedelta(
        seconds=token.get("expires_in", 3600)
    )
    if tp_athlete_id:
        athlete_result = await db.execute(
            select(Athlete).where(Athlete.id == athlete_id)
        )
        athlete = athlete_result.scalar_one_or_none()
        if athlete:
            athlete.tp_athlete_id = tp_athlete_id
    await db.commit()
    return token


def _urlquote(s: str) -> str:
    """URL-encode a string for use inside an x-www-form-urlencoded body.

    Spaces become %20 (NOT '+') so the literal '+' in our pre-built scope
    string keeps its meaning as the OAuth scope delimiter.
    """
    from urllib.parse import quote
    return quote(s, safe="")
