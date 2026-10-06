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
from backend.files.file_service import _import_one_file, record_corrupt
from backend.sync import http, session_check, storage
from backend.sync.coros_sport import COROS_SPORT_TYPES, fit_session_sport, sport_token
from backend.sync.http import as_utc
from backend.settings.secrets import SecretError, seal, unseal

log = logging.getLogger(__name__)

FIRST_SYNC_DAY = "20200101"
# re-list a few days before the last sync: activities uploaded late (watch
# synced the next day) would otherwise fall behind the cursor
CURSOR_OVERLAP_DAYS = 3
PAGE_SIZE = 20

COROS_BASES = {
    "eu": "https://teameuapi.coros.com",
    "us": "https://teamapi.coros.com",
    "cn": "https://teamcnapi.coros.com",
}
# legacy location (before per-source folders); see scripts/migrate_fit_folders.py
LEGACY_COROS_FITS_ROOT = Path.home() / ".wko5coach" / "fits"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

# COROS sportType -> file-name word (fallback only: the FIT session sport
# names the file, sync/coros_sport.py). The old map had 100 = "cycling", which
# named every COROS run *_cycling.fit.
SPORT_NAMES: dict[int, str] = COROS_SPORT_TYPES


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


async def _probe(base: str, token: str, user_id: str, timeout: float = 10) -> tuple[bool, str]:
    """(token accepted for activity queries on `base`, why not)."""
    today = datetime.now(timezone.utc).date()
    params = {"size": 1, "pageNumber": 1,
              "startDay": (today - timedelta(days=30)).strftime("%Y%m%d"),
              "endDay": today.strftime("%Y%m%d")}
    try:
        async with http.client(timeout=timeout) as client:
            resp = await client.get(f"{base}/activity/query", headers=_headers(token, user_id), params=params)
    except Exception as e:                       # noqa: BLE001 — a probe never raises
        return False, type(e).__name__
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    try:
        body = resp.json()
    except ValueError:
        return False, "not JSON"
    if body.get("result") == "0000":
        return True, ""
    return False, f"result={body.get('result')} {body.get('message') or ''}".strip()


async def probe_token(base: str, token: str, user_id: str, timeout: float = 10) -> str:
    """The login check (sync/session_check.py): one size=1 activity query.
    "ok" | "invalid" (COROS refused the token) | "unknown" (network / other)."""
    today = datetime.now(timezone.utc).date()
    params = {"size": 1, "pageNumber": 1,
              "startDay": (today - timedelta(days=7)).strftime("%Y%m%d"),
              "endDay": today.strftime("%Y%m%d")}
    try:
        async with http.client(timeout=timeout) as client:
            resp = await client.get(f"{base}/activity/query", headers=_headers(token, user_id), params=params)
    except Exception:                            # noqa: BLE001 — unknown, never raises
        return "unknown"
    if resp.status_code in (401, 403):
        return "invalid"
    if resp.status_code != 200:
        return "unknown"
    try:
        body = resp.json()
    except ValueError:
        return "unknown"
    if body.get("result") == "0000":
        return "ok"
    return "invalid" if token_invalid(body) else "unknown"


async def _detect_data_base(token: str, user_id: str, login_base: Optional[str] = None,
                            known: Optional[str] = None) -> str:
    """After login, find which base URL accepts the token for activity
    queries. The login server is not always the data server (a Taiwan
    account logs in on EU, its data is on teamapi = US), so: the server
    detected before for this account (`known`), US, the login server, then
    the rest. When no probe answers (network trouble; on 2026-10-01 a
    blocked event loop timed every probe out) the known server is kept,
    else US; the reasons are logged."""
    order = []
    for b in (known, COROS_BASES["us"], login_base, *COROS_BASES.values()):
        if b and b not in order:
            order.append(b)
    why = []
    for base in order:
        ok, reason = await _probe(base, token, user_id)
        if ok:
            log.info("Coros data server detected: %s", base)
            return base
        why.append(f"{base.split('//')[-1]}: {reason}")
    fallback = known or COROS_BASES["us"]
    log.warning("Could not detect the COROS data server (%s); using %s", "; ".join(why), fallback)
    return fallback


# one login at a time: COROS keeps only the latest token of an account, so a
# second login (a double click, a retry) silently invalidates the first one's
# token — and whichever request commits last decides which token is stored
_LOGIN_LOCKS: dict = {}


def _login_lock():
    import asyncio
    loop = asyncio.get_running_loop()
    lk = _LOGIN_LOCKS.get(id(loop))
    if lk is None:
        lk = _LOGIN_LOCKS[id(loop)] = asyncio.Lock()
    return lk


class LoginBusy(RuntimeError):
    pass


async def login(email: str, password: str, db: AsyncSession, athlete_id: int = 1) -> dict:
    """MD5-hash password, try the regions until one accepts the account,
    detect the data server, persist to sync_state. Exactly ONE successful
    login per call (COROS accepts only the latest token): after a region
    accepts, nothing logs in again, and a concurrent login is refused
    (LoginBusy -> HTTP 409)."""
    lk = _login_lock()
    if lk.locked():
        raise LoginBusy("COROS_LOGIN_BUSY: a COROS login is already running")
    async with lk:
        return await _login(email, password, db, athlete_id)


async def _login(email: str, password: str, db: AsyncSession, athlete_id: int) -> dict:
    payload = {"account": email, "accountType": 2, "pwd": _md5(password)}

    last_error = None
    for region, base in COROS_BASES.items():
        try:
            async with http.client(timeout=30) as client:
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
        except Exception as e:
            last_error = str(e) or type(e).__name__
            continue
        # this region issued a token: never log in again below (it would
        # invalidate this token); a failure from here on is an error
        return await _store_login(data["data"], email, region, base, db, athlete_id)

    raise ValueError(f"Coros login failed on all regions: {last_error}")


async def _store_login(result: dict, email: str, region: str, base: str, db: AsyncSession,
                       athlete_id: int) -> dict:
    token = result["accessToken"]
    user_id = str(result.get("userId", ""))
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    state_res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_res.scalar_one_or_none()
    known = state.coros_base_url if state is not None and state.coros_user_id == user_id else None

    # Detect which server actually accepts this token for data calls
    data_base = await _detect_data_base(token, user_id, login_base=base, known=known)
    if not state:
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.coros_access_token = seal(token)      # SecretError (SECRET_KEY_MISSING) propagates
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
        log.info("Coros profile stored: FTP %s, LTHR %s, weight %s",      # no values: personal data
                 *("yes" if v else "no" for v in (ftp, lthr, weight)))
    try:
        await store_hr_profile(db, athlete_id, result)      # max / resting HR, zone tables (hr_profile.py)
    except Exception as e:                  # noqa: BLE001 — never fails the login
        log.info("Coros HR profile not stored: %s", type(e).__name__)

    await db.commit()
    session_check.mark_ok("coros", athlete_id)

    log.info("Coros login OK region=%s data_base=%s", region, data_base)
    return {
        "authenticated": True,
        "coros_user_id": user_id,
        "email": email,
        "region": region,
        "data_server": data_base,
        "ftp_w": ftp,
        "lthr": lthr,
        "token_expires": expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# The account's heart-rate settings (engine/hr_profile.py): max HR, resting HR and
# the three COROS zone tables (zoneData.lthrZone / rhrZone / maxHrZone). Read from
# the login response and refreshed on every sync with GET /account/query (one
# read-only call; checked 2026-10-03: it returns the same zoneData). Stored in
# user_settings 「athlete.coros_profile」, labelled 「來自手錶」 where used. Every change
# of those values is also kept in 「athlete.coros_profile_history」 (engine/coros_compare.py,
# SP-67: COROS moved this account's LTHR from 182 to 152 and nothing recorded when).
# ---------------------------------------------------------------------------

async def store_hr_profile(db: AsyncSession, athlete_id: int, data: Optional[dict]) -> Optional[dict]:
    """Parse and store the HR part of a COROS account response; no commit. None
    (nothing stored) when the response has no HR settings."""
    from backend.engine import coros_compare as CC
    from backend.engine import hr_profile as HP
    from backend.settings.repository import SettingsRepository
    prof = HP.parse_account(data)
    if prof is None:
        return None
    prof["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo = SettingsRepository(db, athlete_id)
    old = await repo.get(HP.ACCOUNT_KEY) or {}
    if {k: v for k, v in old.items() if k != "at"} != {k: v for k, v in prof.items() if k != "at"}:
        await repo.set(HP.ACCOUNT_KEY, prof)
        log.info("Coros HR profile updated")          # no values: personal data (SP-215)
    hist = CC.history_add(await repo.get(CC.HISTORY_KEY), prof, old)
    if hist is not None:
        await repo.set(CC.HISTORY_KEY, hist)
    return prof


async def refresh_hr_profile(db: AsyncSession, athlete_id: int, token: str, base: str, user_id: str) -> None:
    """Best effort: a failure never fails the sync."""
    try:
        async with http.client(timeout=15) as client:
            resp = await client.get(f"{base}/account/query", headers=_headers(token, user_id))
        body = resp.json() if resp.status_code == 200 else {}
        if body.get("result") == "0000" and await store_hr_profile(db, athlete_id, body.get("data")):
            await db.commit()
    except Exception as e:                  # noqa: BLE001
        log.info("Coros HR profile not refreshed: %s", type(e).__name__)


# ---------------------------------------------------------------------------
# 記住密碼 + automatic re-login (user-approved 2026-10-01)
#
# Only when the user ticked 「記住密碼」: the password is stored sealed
# (secrets.seal, Fernet with the local key) in sync_state.coros_password_sealed;
# never in plaintext, never logged, never returned by an API (the status says
# only password_saved). Unticking or logging out deletes it.
# When the token has expired (24 h) or COROS answers "Access token is invalid"
# (result 1019), ONE automatic login is made and the request retried ONCE.
# The login lock serialises it: concurrent callers wait for that one login
# and reuse its token (COROS honours only the latest login), and a failed
# re-login is reported, never retried in a loop.
# ---------------------------------------------------------------------------

TOKEN_INVALID_RESULTS = ("1019", "1030")
RELOGIN_REUSE_S = 120          # a re-login this recent is reused by the next caller (no second login)
_LAST_RELOGIN: dict = {}       # athlete_id -> monotonic time of the last automatic login


class CorosTokenInvalid(ValueError):
    """COROS rejected the stored token ("Access token is invalid")."""


def token_invalid(body: dict) -> bool:
    msg = str(body.get("message") or "").lower()
    return str(body.get("result")) in TOKEN_INVALID_RESULTS or ("token" in msg and "invalid" in msg)


async def save_password(db: AsyncSession, athlete_id: int, password: Optional[str]) -> None:
    """Store (sealed) or delete the remembered password; commits."""
    state = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    if state is None:
        if not password:
            return
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.coros_password_sealed = seal(password) if password else None
    await db.commit()


async def password_saved(db: AsyncSession, athlete_id: int = 1) -> bool:
    state = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    return bool(state is not None and state.coros_password_sealed)


async def relogin(db: AsyncSession, athlete_id: int = 1, since: Optional[float] = None) -> bool:
    """One automatic login with the remembered password. True when a fresh
    token is stored (by this call, or by another caller's login after
    `since`, a time.monotonic() value). False without a remembered password
    or when COROS refuses: the caller reports COROS_AUTH_REQUIRED."""
    import time
    async with _login_lock():
        last = _LAST_RELOGIN.get(athlete_id)
        if last is not None and since is not None and last >= since:
            return True                      # someone logged in while we waited: use that token
        if last is not None and time.monotonic() - last < RELOGIN_REUSE_S and since is None:
            return True
        state = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
        if state is None or not state.coros_password_sealed or not state.coros_email:
            return False
        try:
            password = unseal(state.coros_password_sealed)
        except SecretError:
            return False
        if not password:
            return False
        try:
            await _login(state.coros_email, password, db, athlete_id)
        except SecretError:
            raise
        except Exception as e:               # noqa: BLE001 — reported, not retried
            log.warning("COROS automatic re-login failed: %s", type(e).__name__)
            return False
        _LAST_RELOGIN[athlete_id] = time.monotonic()
        log.warning("COROS token renewed with the remembered password")
        return True


async def _get_token_and_base(db: AsyncSession, athlete_id: int = 1,
                              auto_relogin: bool = True) -> tuple[str, str, str]:
    """Return (token, base_url, user_id) from DB, raise if missing or expired.
    An expired token is renewed first when a password is remembered."""
    import time
    res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = res.scalar_one_or_none()
    if not state or not state.coros_access_token:
        raise ValueError("COROS_AUTH_REQUIRED: not logged in")
    exp = as_utc(state.coros_token_expires)
    if exp and datetime.now(timezone.utc) >= exp:
        # COROS tokens last 24 h and there is no refresh grant: log in again
        if auto_relogin and state.coros_password_sealed and await relogin(db, athlete_id, since=time.monotonic()):
            await db.refresh(state)
            return await _get_token_and_base(db, athlete_id, auto_relogin=False)
        session_check.mark_expired("coros", athlete_id)
        raise ValueError("COROS_AUTH_REQUIRED: token expired, please login again")
    base = state.coros_base_url or COROS_BASES["us"]
    user_id = state.coros_user_id or ""
    try:
        token = unseal(state.coros_access_token)
    except SecretError as e:
        session_check.mark_expired("coros", athlete_id)
        raise ValueError(f"COROS_AUTH_REQUIRED: {e}")
    return token, base, user_id


async def _list_page(
    token: str, base: str, user_id: str, start_day: str, end_day: str, page: int, size: int = 20
) -> list:
    params = {"size": size, "pageNumber": page, "startDay": start_day, "endDay": end_day}
    async with http.client(timeout=30) as client:
        resp = await client.get(
            f"{base}/activity/query", headers=_headers(token, user_id), params=params
        )
    if resp.status_code != 200:
        raise ValueError(f"Coros list_activities HTTP {resp.status_code}: {resp.text[:200]!r}")
    body = resp.json()
    if body.get("result") != "0000":
        if token_invalid(body):
            raise CorosTokenInvalid(f"Coros list_activities error: {body.get('message')!r}")
        raise ValueError(f"Coros list_activities error: {body.get('message')!r}")
    inner = body.get("data") or {}
    return inner.get("dataList") or inner.get("list") or []


async def _download_fit(token: str, base: str, user_id: str, activity: dict) -> bytes:
    """Download FIT bytes: try fitUrl first, then POST /activity/detail/download."""
    fit_url = activity.get("fitUrl")
    if fit_url:
        async with http.client(timeout=60, follow_redirects=True) as client:
            resp = await client.get(fit_url)
            if resp.status_code == 200 and resp.content:
                return resp.content

    label_id = str(activity["labelId"])
    sport_type = activity.get("sportType", 0)
    # fileType=4 is FIT format per xballoy/coros-api
    params = {"labelId": label_id, "sportType": str(sport_type), "fileType": "4"}
    async with http.client(timeout=30) as client:
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
    async with http.client(timeout=60, follow_redirects=True) as client:
        resp = await client.get(download_url)
    if resp.status_code != 200:
        raise ValueError(f"FIT download HTTP {resp.status_code}")
    return resp.content


# ---------------------------------------------------------------------------
# The post-run self-rating (SP-231, engine/coros_rpe.py): one read-only detail query per
# activity. Best effort: a failure or 0 (not filled) never fails the sync nor holds the
# cursor; a row whose query failed keeps coros_feel NULL and is tried again by the next
# sync's retry pass (the last RETRY_DAYS days). The last BACKFILL_DAYS of activities imported
# before this existed are filled once, by the next syncs (no login of its own, nothing written
# to COROS), rate-limited, marked done in the setting BACKFILL_KEY.
# ---------------------------------------------------------------------------

DETAIL_TIMEOUT_S = 20
# POST (verified on production 2026-10-06; a GET answers result=1001). The screen size is
# what scripts/probe_coros_tl.py sends with the same read (copied from COROS's web app)
DETAIL_SCREEN = {"screenW": 944, "screenH": 1414}
DETAIL_PAUSE_S = 0.4          # between the detail reads of a retry / backfill pass (tests set 0)
RETRY_DAYS = CURSOR_OVERLAP_DAYS + 1
RETRY_MAX = 10                # detail reads per sync for rows whose read failed
BACKFILL_DAYS = 56            # 8 weeks (research §4.1: the RPE factor weighs recent activities)
BACKFILL_MAX = 80             # detail reads per sync for the backfill
BACKFILL_PASSES = 3           # a backfill that keeps failing stops after this many syncs
BACKFILL_KEY = "sync.coros.rpe_backfill"
MAX_FAILS_IN_A_ROW = 5        # the endpoint looks down: stop this pass


async def _pause() -> None:
    """The gap between two detail reads of a pass (rate limit; tests replace it)."""
    import asyncio
    if DETAIL_PAUSE_S:
        await asyncio.sleep(DETAIL_PAUSE_S)


async def read_feel(token: str, base: str, user_id: str, label_id: str, sport_type) -> tuple[Optional[int], str]:
    """(feelType 0–5 or None, "ok" | "token" | "error") of one activity. Reads only
    sportFeelInfo.feelType (never sportNote / the voice note). Never raises."""
    from backend.engine import coros_rpe as CR
    params = {"labelId": str(label_id), "sportType": str(sport_type), **DETAIL_SCREEN}
    try:
        async with http.client(timeout=DETAIL_TIMEOUT_S) as client:
            resp = await client.post(f"{base}/activity/detail/query", params=params,
                                     headers=_headers(token, user_id))
        if resp.status_code in (401, 403):
            return None, "token"
        if resp.status_code != 200:
            return None, "error"
        body = resp.json()
    except Exception as e:                       # noqa: BLE001 — best effort, never fails the sync
        log.info("COROS detail read failed: %s", type(e).__name__)
        return None, "error"
    if isinstance(body, dict) and str(body.get("result")) != "0000" and token_invalid(body):
        return None, "token"
    feel = CR.parse_feel(body)
    return feel, "ok" if feel is not None else "error"


def _detail_sport(wf: WorkoutFile) -> Optional[int]:
    """The sportType a detail read needs: the stored COROS code; a run imported before it
    was stored = 100 (run, verified)."""
    if wf.coros_sport_type is not None:
        return wf.coros_sport_type
    return 100 if (wf.sport or "").lower() in ("running", "run") else None


async def fill_feel(db: AsyncSession, athlete_id: int, token: str, base: str, user_id: str,
                    since: date, limit: int, until: Optional[date] = None) -> dict:
    """Read the self-rating of the already-imported COROS activities in [since, until) that were
    never read (coros_feel NULL), newest first, at most `limit`. {checked, filled (RPE
    changed), failed, left (rows still unread beyond the limit), stopped (why a pass ended
    early or None)}. Commits per row; never raises."""
    from backend.engine import coros_rpe as CR
    out = {"checked": 0, "filled": 0, "failed": 0, "left": 0, "stopped": None}
    try:
        rows = (await db.execute(
            select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id, WorkoutFile.source == "coros",
                                      WorkoutFile.coros_activity_id.is_not(None),
                                      WorkoutFile.coros_feel.is_(None), WorkoutFile.workout_date >= since)
            .order_by(WorkoutFile.workout_date.desc(), WorkoutFile.id.desc()))).scalars().all()
    except Exception as e:                       # noqa: BLE001 — e.g. a DB before the migration
        log.info("COROS self-rating rows not read: %s", type(e).__name__)
        out["stopped"] = "db"
        return out
    rows = [r for r in rows if _detail_sport(r) is not None and (until is None or r.workout_date < until)]
    out["left"] = max(0, len(rows) - limit)
    fails = 0
    for i, wf in enumerate(rows[:limit]):
        if i:
            await _pause()
        feel, why = await read_feel(token, base, user_id, wf.coros_activity_id, _detail_sport(wf))
        out["checked"] += 1
        if feel is None:
            out["failed"] += 1
            fails += 1
            if why == "token" or fails >= MAX_FAILS_IN_A_ROW:
                out["stopped"] = why if why == "token" else "errors"
                out["left"] += len(rows[:limit]) - i - 1
                break
            continue
        fails = 0
        try:
            if CR.apply(wf, feel):
                out["filled"] += 1
            await db.commit()
        except Exception as e:                   # noqa: BLE001
            await db.rollback()
            log.info("COROS self-rating not stored: %s", type(e).__name__)
            out["failed"] += 1
    return out


async def backfill_feel(db: AsyncSession, athlete_id: int, token: str, base: str, user_id: str,
                        today: Optional[date] = None) -> Optional[dict]:
    """The one-time backfill of the last BACKFILL_DAYS (idempotent: rows already read are never
    read again; done once a pass leaves nothing unread, or after BACKFILL_PASSES passes).
    None when it is already done. The marker: {done, passes, checked, filled, at}."""
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    try:
        mark = await repo.get(BACKFILL_KEY) or {}
    except Exception:                            # noqa: BLE001
        return None
    if mark.get("done"):
        return None
    today = today or datetime.now(timezone.utc).date()
    # the last RETRY_DAYS are the retry pass's (_feel_passes): not read twice in one sync
    res = await fill_feel(db, athlete_id, token, base, user_id, today - timedelta(days=BACKFILL_DAYS), BACKFILL_MAX,
                          until=today - timedelta(days=RETRY_DAYS))
    if res["stopped"] in ("token", "db"):
        return res                               # not a pass: tried again by the next sync
    passes = int(mark.get("passes") or 0) + 1
    done = (res["left"] == 0 and res["failed"] == 0) or passes >= BACKFILL_PASSES
    try:
        await repo.set(BACKFILL_KEY, {"done": done, "passes": passes,
                                      "checked": int(mark.get("checked") or 0) + res["checked"],
                                      "filled": int(mark.get("filled") or 0) + res["filled"],
                                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        await db.commit()
    except Exception as e:                       # noqa: BLE001
        await db.rollback()
        log.info("COROS self-rating backfill marker not stored: %s", type(e).__name__)
    log.info("COROS self-rating backfill pass %s: checked %s, filled %s, failed %s, left %s%s",
             passes, res["checked"], res["filled"], res["failed"], res["left"], " (done)" if done else "")
    return {**res, "passes": passes, "done": done}


async def _read_new_feel(db: AsyncSession, wf, token: str, base: str, user_id: str, label_id: str,
                         sport_type) -> None:
    """The self-rating of an activity just imported (committed already: a failure here never
    touches the import). A failed read leaves coros_feel NULL for the next sync's retry."""
    from backend.engine import coros_rpe as CR
    try:
        feel, _why = await read_feel(token, base, user_id, label_id, sport_type)
        if feel is None:
            return
        CR.apply(wf, feel)
        await db.commit()
    except Exception as e:                       # noqa: BLE001
        log.info("COROS self-rating not stored: %s", type(e).__name__)
        try:
            await db.rollback()
        except Exception:                        # noqa: BLE001
            pass


async def _feel_passes(db: AsyncSession, athlete_id: int, token: str, base: str, user_id: str) -> int:
    """The end of a sync: retry the recent unread rows, then the backfill. The number of
    already-imported activities whose RPE changed. Never raises."""
    filled = 0
    try:
        today = datetime.now(timezone.utc).date()
        r = await fill_feel(db, athlete_id, token, base, user_id, today - timedelta(days=RETRY_DAYS), RETRY_MAX)
        if r["checked"]:
            await _pause()
        filled += r["filled"]
        if r["stopped"] != "token":
            b = await backfill_feel(db, athlete_id, token, base, user_id, today)
            filled += (b or {}).get("filled", 0)
    except Exception as e:                       # noqa: BLE001 — the sync result stands
        log.warning("COROS self-rating pass failed: %s", type(e).__name__)
        try:
            await db.rollback()
        except Exception:                        # noqa: BLE001
            pass
    return filled


def list_training_load(act: dict) -> Optional[float]:
    """COROS's Training Load of one activity-list item (`trainingLoad`, SP-37: present on
    most items); None when missing, zero or not a number."""
    v = act.get("trainingLoad") if isinstance(act, dict) else None
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 and f == f and f != float("inf") else None


async def sync_workouts(
    db: AsyncSession,
    athlete_id: int = 1,
    since: Optional[str] = None,
) -> AsyncIterator[dict]:
    """List Coros activities → skip known → download .fit → import → yield SSE events.

    Incremental: without `since`, lists from the last clean sync minus
    CURSOR_OVERLAP_DAYS (first sync: FIRST_SYNC_DAY). The cursor only moves
    when the run had no download / import errors, so failures are retried."""
    try:
        token, base, user_id = await _get_token_and_base(db, athlete_id)
    except ValueError as e:
        yield {"status": "error", "error": "COROS_AUTH_REQUIRED", "detail": str(e),
               "hint": "POST /api/v1/auth/coros/login with {email, password}"}
        return

    ath_res = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = ath_res.scalar_one_or_none()
    athlete_name = athlete.name if athlete else "default"

    state_res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_res.scalar_one_or_none()
    last = as_utc(state.coros_last_sync_at) if state else None
    if since:
        since_day = since.replace("-", "")
    elif last:
        since_day = (last.date() - timedelta(days=CURSOR_OVERLAP_DAYS)).strftime("%Y%m%d")
    else:
        since_day = FIRST_SYNC_DAY
    end_day = datetime.now().strftime("%Y%m%d")
    sync_started = datetime.now(timezone.utc)

    yield {"status": "started", "since": since_day, "until": end_day}

    page = 1
    total_checked = 0
    total_downloaded = 0
    tl_filled = 0                 # already-imported activities whose list trainingLoad was stored
    errors: list[str] = []

    relogged = False
    while True:
        import time as _time
        t0 = _time.monotonic()
        try:
            activities = await _list_page(token, base, user_id, since_day, end_day, page, PAGE_SIZE)
        except CorosTokenInvalid as e:
            # "Access token is invalid": one automatic login with the
            # remembered password, then this page once more; never a loop
            if not relogged and await relogin(db, athlete_id, since=t0):
                relogged = True
                token, base, user_id = await _get_token_and_base(db, athlete_id, auto_relogin=False)
                yield {"status": "relogin", "detail": "COROS token renewed (remembered password)"}
                continue
            session_check.mark_expired("coros", athlete_id)
            # stored too (SP-88): the refusal outlives the in-memory answer (its 5 min
            # cache, a restart), like the login check's own refusal (session_check._check_coros)
            if state is not None:
                state.coros_token_expires = datetime.now(timezone.utc)
                await db.commit()
            yield {"status": "error", "error": "COROS_AUTH_REQUIRED", "detail": str(e),
                   "hint": "請到設定頁重新登入 COROS"}
            return
        except Exception as e:
            yield {"status": "error", "error": "COROS_API_ERROR", "detail": str(e)}
            return
        if not activities:
            break

        for act in activities:
            label_id = str(act.get("labelId", ""))
            act_date = _parse_coros_date(act.get("date") or act.get("startTime", 0))
            sport_type = act.get("sportType", 0)
            total_checked += 1

            yield {
                "status": "checking",
                "activity_id": label_id,
                "date": act_date.isoformat() if act_date else None,
            }

            act_tl = list_training_load(act)
            dup = await db.execute(
                select(WorkoutFile).where(WorkoutFile.coros_activity_id == label_id)
            )
            known = dup.scalars().first()
            if known is not None:
                # the list's trainingLoad on rows imported before it was stored (or changed by
                # COROS since): no extra call, the list item is already here
                if act_tl is not None and known.coros_training_load != act_tl:
                    known.coros_training_load = act_tl
                    await db.commit()
                    tl_filled += 1
                yield {"status": "skipped", "activity_id": label_id, "reason": "already_imported"}
                continue

            t_dl = _time.monotonic()           # download vs import seconds (sync/runner.SyncClock)
            try:
                fit_bytes = await _download_fit(token, base, user_id, act)
            except Exception as e:
                log.warning("Coros FIT download failed %s: %s", label_id, e)
                errors.append(f"{label_id}: {e}")
                yield {"status": "error", "activity_id": label_id, "error": str(e)}
                continue

            dl_s = _time.monotonic() - t_dl
            year = act_date.year if act_date else "unknown"
            dest_dir = storage.year_dir("coros", year)      # ~/.wko5coach/fit/coros/<year>/
            date_str = act_date.isoformat() if act_date else "unknown"
            # the name's sport word comes from the FIT session (sport +
            # sub_sport); the COROS code only when the FIT can't say
            tmp = dest_dir / f".{label_id}.download"
            tmp.write_bytes(fit_bytes)
            import asyncio as _asyncio
            fit_sport, fit_sub = await _asyncio.to_thread(fit_session_sport, tmp)   # FIT parsing: off the loop
            sport_name = sport_token(fit_sport, fit_sub, sport_type)
            filename = f"{label_id}_{date_str}_{sport_name}.fit"
            dest = dest_dir / filename
            tmp.replace(dest)

            try:
                wf = await _import_one_file(
                    db, athlete_id, dest,
                    source="coros",
                    coros_activity_id=label_id,
                )
                if wf is not None:
                    try:
                        wf.coros_sport_type = int(sport_type)
                    except (TypeError, ValueError):
                        pass
                    wf.coros_training_load = act_tl
                if wf is None:
                    # FIT file is corrupt/unreadable — store a stub so we don't
                    # re-download it on the next sync (coros_activity_id dup check).
                    await db.rollback()
                    await record_corrupt(db, athlete_id, dest, source="coros",
                                         workout_date=act_date, coros_activity_id=label_id)
                    await db.commit()
                    log.warning("Corrupt FIT, stub recorded: %s", filename)
                    yield {"status": "error", "activity_id": label_id, "error": "corrupt_fit"}
                    continue
                await db.commit()
                total_downloaded += 1
                await _read_new_feel(db, wf, token, base, user_id, label_id, sport_type)
                yield {
                    "status": "downloaded",
                    "activity_id": label_id,
                    "file": filename,
                    "sport": sport_name,
                    "secs": {"download": round(dl_s, 3)},
                }
            except Exception as e:
                # nothing half-written survives; the cursor stays so it's retried
                await db.rollback()
                log.warning("Import failed for %s: %s", filename, e)
                errors.append(f"{label_id}: import_failed: {e}")
                yield {"status": "error", "activity_id": label_id, "error": f"import_failed: {e}"}

        if len(activities) < PAGE_SIZE:
            break
        page += 1

    state_res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_res.scalar_one_or_none()
    if state and not errors:
        state.coros_last_sync_at = sync_started
        await db.commit()
    await refresh_hr_profile(db, athlete_id, token, base, user_id)
    # the post-run self-rating of already-imported activities: unread recent ones, the
    # one-time 8-week backfill (SP-231); never fails the sync, never moves the cursor
    rpe_filled = await _feel_passes(db, athlete_id, token, base, user_id)

    yield {"status": "complete", "total_downloaded": total_downloaded,
           "total_checked": total_checked, "errors": errors, "tl_filled": tl_filled,
           "rpe_filled": rpe_filled}
