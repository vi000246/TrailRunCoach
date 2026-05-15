"""
Coros unofficial API client.
Auth: POST /account/login with MD5-hashed password, field "pwd", accountType=2.
Success: result=="0000". Token in data.accessToken (TTL 24h).
Region detection: tries EU → US → CN in order.
Reference: cygnusb/coros-mcp, xballoy/coros-api
"""
import hashlib
import json
import logging
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Optional, AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import SyncState, Athlete, WorkoutFile, AthleteSettings
from backend.files.file_service import _import_one_file

log = logging.getLogger(__name__)

COROS_BASES = {
    "eu": "https://teameuapi.coros.com",
    "us": "https://teamapi.coros.com",
    "cn": "https://teamcnapi.coros.com",
}
COROS_FITS_ROOT = Path.home() / ".wko5coach" / "fits"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

SPORT_NAMES: dict[int, str] = {
    100: "cycling",
    200: "run",
    300: "swim",
    400: "triathlon",
    19: "indoor_cycling",
    20: "treadmill",
}


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _parse_coros_date(t: int) -> Optional[date]:
    """Parse YYYYMMDD int → date (the `date` field is 8-digit YYYYMMDD)."""
    try:
        s = str(t)
        if len(s) == 8:
            return datetime.strptime(s, "%Y%m%d").date()
        return None
    except Exception:
        return None


def _headers(token: Optional[str] = None, user_id: Optional[str] = None) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if token:
        h["accessToken"] = token
    if user_id:
        import json as _json
        h["yfheader"] = _json.dumps({"userId": user_id})
    return h


async def _detect_data_base(token: str, user_id: str) -> str:
    """After login, find which base URL accepts the token for activity queries."""
    params = {"size": 1, "pageNumber": 1, "startDay": "20250101", "endDay": "20260101"}
    for base in COROS_BASES.values():
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{base}/activity/query",
                    headers=_headers(token, user_id),
                    params=params,
                )
            if resp.status_code == 200 and resp.json().get("result") == "0000":
                log.info("Coros data server detected: %s", base)
                return base
        except Exception:
            continue
    log.warning("Could not detect data server, defaulting to US")
    return COROS_BASES["us"]


async def login(email: str, password: str, db: AsyncSession, athlete_id: int = 1) -> dict:
    """MD5-hash password, try all regions, detect data server, persist to sync_state."""
    payload = {"account": email, "accountType": 2, "pwd": _md5(password)}

    last_error = None
    for region, base in COROS_BASES.items():
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base}/account/login",
                    json=payload,
                    headers=_headers(),
                )
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code} from {region}"
                continue
            data = resp.json()
            if data.get("result") != "0000":
                last_error = data.get("message", f"result={data.get('result')}")
                continue

            result = data["data"]
            token = result["accessToken"]
            user_id = str(result.get("userId", ""))
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

            # Detect which server actually accepts this token for data calls
            data_base = await _detect_data_base(token, user_id)

            state_res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
            state = state_res.scalar_one_or_none()
            if not state:
                state = SyncState(athlete_id=athlete_id)
                db.add(state)
            state.coros_access_token = token
            state.coros_token_expires = expires_at
            state.coros_email = email
            state.coros_base_url = data_base
            state.coros_user_id = user_id

            # Persist FTP/LTHR from Coros profile as AthleteSettings
            zone_data = result.get("zoneData") or {}
            ftp = zone_data.get("ftp")
            lthr = zone_data.get("lthr")
            weight = result.get("weight")
            if ftp or lthr or weight:
                today = datetime.now(timezone.utc).date()
                settings_res = await db.execute(
                    select(AthleteSettings).where(
                        AthleteSettings.athlete_id == athlete_id,
                        AthleteSettings.effective_date == today,
                    )
                )
                settings = settings_res.scalar_one_or_none()
                if not settings:
                    settings = AthleteSettings(athlete_id=athlete_id, effective_date=today)
                    db.add(settings)
                if ftp:
                    settings.ftp_w = float(ftp)
                if lthr:
                    settings.lthr = int(lthr)
                if weight:
                    settings.weight_kg = float(weight)
                log.info("Coros profile: FTP=%s LTHR=%s weight=%s", ftp, lthr, weight)

            await db.commit()

            log.info("Coros login OK region=%s data_base=%s user_id=%s", region, data_base, user_id)
            return {
                "authenticated": True,
                "coros_user_id": user_id,
                "email": email,
                "region": region,
                "ftp_w": ftp,
                "lthr": lthr,
                "token_expires": expires_at.isoformat(),
            }
        except Exception as e:
            last_error = str(e)
            continue

    raise ValueError(f"Coros login failed on all regions: {last_error}")


async def _get_token_and_base(db: AsyncSession, athlete_id: int = 1) -> tuple[str, str, str]:
    """Return (token, base_url, user_id) from DB, raise if missing or expired."""
    res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = res.scalar_one_or_none()
    if not state or not state.coros_access_token:
        raise ValueError("COROS_AUTH_REQUIRED: not logged in")
    if state.coros_token_expires:
        exp = state.coros_token_expires
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= exp:
            raise ValueError("COROS_AUTH_REQUIRED: token expired, please login again")
    base = state.coros_base_url or COROS_BASES["us"]
    user_id = state.coros_user_id or ""
    return state.coros_access_token, base, user_id


async def _list_page(
    token: str, base: str, user_id: str, start_day: str, end_day: str, page: int, size: int = 20
) -> list:
    params = {"size": size, "pageNumber": page, "startDay": start_day, "endDay": end_day}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base}/activity/query", headers=_headers(token, user_id), params=params
        )
    if resp.status_code != 200:
        raise ValueError(f"Coros list_activities HTTP {resp.status_code}: {resp.text[:200]!r}")
    body = resp.json()
    if body.get("result") != "0000":
        raise ValueError(f"Coros list_activities error: {body.get('message')!r}")
    inner = body.get("data") or {}
    return inner.get("dataList") or inner.get("list") or []


async def _download_fit(token: str, base: str, user_id: str, activity: dict) -> bytes:
    """Download FIT bytes: try fitUrl first, then POST /activity/detail/download."""
    fit_url = activity.get("fitUrl")
    if fit_url:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(fit_url)
            if resp.status_code == 200 and resp.content:
                return resp.content

    label_id = str(activity["labelId"])
    sport_type = activity.get("sportType", 0)
    # fileType=4 is FIT format per xballoy/coros-api
    params = {"labelId": label_id, "sportType": str(sport_type), "fileType": "4"}
    async with httpx.AsyncClient(timeout=30) as client:
        url_resp = await client.post(
            f"{base}/activity/detail/download",
            params=params,
            headers=_headers(token, user_id),
        )
    if url_resp.status_code != 200:
        raise ValueError(f"detail/download HTTP {url_resp.status_code}: {url_resp.text[:200]!r}")
    url_data = url_resp.json()
    if url_data.get("result") != "0000":
        raise ValueError(f"detail/download error: {url_data.get('message')!r}")
    download_url = (url_data.get("data") or {}).get("fileUrl", "")
    if not download_url:
        raise ValueError("detail/download: no fileUrl in response")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(download_url)
    if resp.status_code != 200:
        raise ValueError(f"FIT download HTTP {resp.status_code}")
    return resp.content


async def sync_workouts(
    db: AsyncSession,
    athlete_id: int = 1,
    since: Optional[str] = None,
) -> AsyncIterator[dict]:
    """List Coros activities → skip known → download .fit → import → yield SSE events."""
    token, base, user_id = await _get_token_and_base(db, athlete_id)

    ath_res = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = ath_res.scalar_one_or_none()
    athlete_name = athlete.name if athlete else "default"

    since_day = (since or "20200101").replace("-", "")
    end_day = datetime.now().strftime("%Y%m%d")

    yield {"status": "started", "since": since_day, "until": end_day}

    page = 1
    total_checked = 0
    total_downloaded = 0

    while True:
        activities = await _list_page(token, base, user_id, since_day, end_day, page)
        if not activities:
            break

        for act in activities:
            label_id = str(act.get("labelId", ""))
            act_date = _parse_coros_date(act.get("date") or act.get("startTime", 0))
            sport_type = act.get("sportType", 0)
            sport_name = SPORT_NAMES.get(sport_type, "other")
            total_checked += 1

            yield {
                "status": "checking",
                "activity_id": label_id,
                "date": act_date.isoformat() if act_date else None,
            }

            dup = await db.execute(
                select(WorkoutFile).where(WorkoutFile.coros_activity_id == label_id)
            )
            if dup.scalar_one_or_none():
                yield {"status": "skipped", "activity_id": label_id, "reason": "already_imported"}
                continue

            try:
                fit_bytes = await _download_fit(token, base, user_id, act)
            except Exception as e:
                log.warning("Coros FIT download failed %s: %s", label_id, e)
                yield {"status": "error", "activity_id": label_id, "error": str(e)}
                continue

            year = act_date.year if act_date else "unknown"
            dest_dir = COROS_FITS_ROOT / athlete_name / str(year)
            dest_dir.mkdir(parents=True, exist_ok=True)
            date_str = act_date.isoformat() if act_date else "unknown"
            filename = f"{label_id}_{date_str}_{sport_name}.fit"
            dest = dest_dir / filename
            dest.write_bytes(fit_bytes)

            try:
                await _import_one_file(
                    db, athlete_id, dest,
                    source="coros",
                    coros_activity_id=label_id,
                )
                await db.commit()
                total_downloaded += 1
                yield {
                    "status": "downloaded",
                    "activity_id": label_id,
                    "file": filename,
                    "sport": sport_name,
                }
            except Exception as e:
                log.warning("Import failed for %s: %s", filename, e)
                yield {"status": "error", "activity_id": label_id, "error": f"import_failed: {e}"}

        if len(activities) < 20:
            break
        page += 1

    state_res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_res.scalar_one_or_none()
    if state:
        state.coros_last_sync_at = datetime.now(timezone.utc)
        await db.commit()

    yield {"status": "complete", "total_downloaded": total_downloaded, "total_checked": total_checked}
