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
import re
import logging
import httpx
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import SyncState, Athlete, WorkoutFile
from backend.sync import http
from backend.sync.http import as_utc
from backend.settings.secrets import SecretError, seal, unseal

log = logging.getLogger(__name__)


class TpDownloadError(Exception):
    """detaildata / filedata failed (auth, premium gate, not found, server).
    Unlike "the workout has no device file", this must not advance the cursor."""

TP_OAUTH_URL = "https://oauth.trainingpeaks.com/oauth/token"
TP_API_BASE = "https://tpapi.trainingpeaks.com/"

# Headers WKO5 sends on every request (PAEasyWeb in WKO5.exe 5.0.587):
#   User-Agent "WKO5/PC/%s", "Accept: */*", "Accept-Encoding: gzip".
TP_HEADERS = {
    "User-Agent": "WKO5/PC/5.0.587",
    "Accept": "*/*",
    "Accept-Encoding": "gzip",
}

# Refresh grant: client_id=WKO5 with empty client_secret (literal from binary).
TP_CLIENT_ID = "WKO5"
TP_CLIENT_SECRET = ""

# Scope string is concatenated literally in the binary with '+' separators
# (NOT URL-encoded spaces). The OAuth server treats '+' as the scope delimiter.
TP_SCOPE = "fitness+baseactivity+users+metrics+software+groundcontrol"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TpGrantRejected(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}")
        self.status, self.body = status, body


class TpLoginError(ValueError):
    """Message starts with a code the API maps to a status:
    TP_LOGIN_FAILED (bad credentials), TP_LOGIN_CAPTCHA, TP_LOGIN_MFA,
    TP_LOGIN_ERROR (anything else)."""


async def _password_grant(username: str, password: str) -> dict:
    """
    Password grant — format VERIFIED against the live TP OAuth server 2026-05-15:
      grant_type=password&username={u}&password={p}
      &scope=fitness+baseactivity+...&client_id=WKO5

    As of 2026-09-30 TP answers a correct password for a normal account with
    400 invalid_grant ("Invalid resource owner password credential."), so the
    grant is effectively dead for us; it is still tried first because it is
    one request and gives a refresh token when it works.
    """
    body = (
        f"grant_type=password"
        f"&username={_urlquote(username)}"
        f"&password={_urlquote(password)}"
        f"&scope={TP_SCOPE}"
        f"&client_id={TP_CLIENT_ID}"
    )
    async with http.client(timeout=15) as client:
        resp = await client.post(
            TP_OAUTH_URL,
            content=body,
            headers={**TP_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise TpGrantRejected(resp.status_code, resp.text[:200])
    try:
        return resp.json()
    except Exception:
        raise TpGrantRejected(resp.status_code, "response is not JSON")


# ---- website login (cookie) ------------------------------------------------
#
# Verified end-to-end 2026-09-30 with a real account:
#   GET  home.trainingpeaks.com/login            -> form with __RequestVerificationToken
#   POST same URL (Username, Password, token)    -> redirects to app.trainingpeaks.com,
#                                                    sets cookie Production_tpAuth
#   GET  tpapi.trainingpeaks.com/users/v3/token  (cookie) -> {success, token{access_token}}
# The access token then works for every tpapi endpoint this client uses. It
# expires (about an hour); a new one is fetched with the same cookie, so the
# cookie plays the role of the refresh token and is stored sealed.

TP_WEB_LOGIN_URL = "https://home.trainingpeaks.com/login"
TP_WEB_TOKEN_URL = TP_API_BASE + "users/v3/token"
TP_AUTH_COOKIE = "Production_tpAuth"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

def _keys(resp: httpx.Response) -> str:
    """Top-level JSON keys only (never values) for the diagnostic trace."""
    try:
        body = resp.json()
    except Exception:
        return "-"
    if isinstance(body, dict):
        return ",".join(sorted(body.keys()))
    return type(body).__name__


def _trace(step: str, resp: httpx.Response) -> None:
    """Sanitised login trace: step, status, host+path, JSON keys. No query
    strings, bodies, cookies, tokens or credentials."""
    log.warning("TP login step=%s status=%s url=%s%s keys=%s", step, resp.status_code,
             resp.url.host, resp.url.path, _keys(resp))


_RE_CSRF = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_RE_ACTION = re.compile(r'<form[^>]*action="([^"]*)"', re.I)
_RE_ERROR = re.compile(r'(?:validation-summary-errors|field-validation-error)[^>]*>(.{0,400})', re.S)
_RE_CAPTCHA_ON = re.compile(r'name="CaptchaHidden"[^>]*value="(?:true|1|True)"'
                            r'|value="(?:true|1|True)"[^>]*name="CaptchaHidden"')


def _input_value(html: str, name: str) -> Optional[str]:
    m = re.search(rf'<input[^>]*name="{re.escape(name)}"[^>]*>', html)
    if not m:
        return None
    v = re.search(r'value="([^"]*)"', m.group(0))
    return v.group(1) if v else ""


def _cookie(jar: httpx.Cookies, name: str) -> Optional[str]:
    for c in jar.jar:
        if c.name == name and c.value:
            return c.value
    return None


def classify_login_page(url: str, html: str) -> TpLoginError:
    """Why a login POST didn't produce the auth cookie."""
    text = re.sub(r"<[^>]+>", " ", html)
    err = _RE_ERROR.search(html)
    msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", err.group(1))).strip()[:160] if err else ""
    low = (msg + " " + url).lower()
    if (re.search(r"mfa|two-?factor|verification code|authenticator", url, re.I)
            or re.search(r"(enter|send)[^.]{0,40}(verification|security) code|two-factor|multi-factor",
                         text, re.I)):
        return TpLoginError("TP_LOGIN_MFA: TrainingPeaks asks for a second-factor code. "
                            "Log in once at trainingpeaks.com in a browser, or turn MFA off, then retry.")
    if "captcha" in low or "robot" in low or _RE_CAPTCHA_ON.search(html):
        return TpLoginError("TP_LOGIN_CAPTCHA: TrainingPeaks wants a CAPTCHA (usually after several "
                            "failed attempts). Log in once at trainingpeaks.com in a browser, then retry.")
    return TpLoginError(f"TP_LOGIN_FAILED: {msg or 'website login did not set the session cookie'}")


async def _web_login(username: str, password: str) -> str:
    """Website form login; returns the Production_tpAuth cookie value."""
    async with http.client(timeout=30, follow_redirects=True,
                           headers={"User-Agent": BROWSER_UA}) as c:
        try:
            page = await c.get(TP_WEB_LOGIN_URL)
        except httpx.HTTPError as e:
            raise TpLoginError(f"TP_LOGIN_ERROR[login_page]: {type(e).__name__}")
        _trace("login_page", page)
        if page.status_code != 200:
            raise TpLoginError(f"TP_LOGIN_ERROR[login_page]: HTTP {page.status_code}")
        form = {"Username": username, "Password": password}
        m = _RE_CSRF.search(page.text)
        if m:
            form["__RequestVerificationToken"] = m.group(1)
        for hidden in ("CaptchaHidden", "CaptchaToken", "Attempts", "SelectedMfaMethod"):
            v = _input_value(page.text, hidden)
            if v is not None:
                form[hidden] = v
        a = _RE_ACTION.search(page.text)
        url = page.url.join(a.group(1)) if a and a.group(1) else page.url
        try:
            resp = await c.post(url, data=form)
        except httpx.HTTPError as e:
            raise TpLoginError(f"TP_LOGIN_ERROR[login_post]: {type(e).__name__}")
        for h in resp.history:
            _trace("login_post_redirect", h)
        _trace("login_post", resp)
        cookie = _cookie(c.cookies, TP_AUTH_COOKIE)
        log.warning("TP login step=login_post auth_cookie=%s form_fields=%s", bool(cookie),
                 ",".join(sorted(k for k in form if k not in ("Username", "Password"))))
        if cookie:
            return cookie
        raise classify_login_page(str(resp.url), resp.text)


async def _token_from_cookie(cookie: str) -> Optional[dict]:
    """users/v3/token with the session cookie -> token dict, or None if the
    cookie is no longer accepted."""
    async with http.client(timeout=15, headers={"User-Agent": BROWSER_UA,
                                                "Cookie": f"{TP_AUTH_COOKIE}={cookie}"}) as c:
        resp = await c.get(TP_WEB_TOKEN_URL)
    _trace("token", resp)
    if resp.status_code != 200:
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    tok = body.get("token") if isinstance(body.get("token"), dict) else body
    log.warning("TP login step=token token_keys=%s", ",".join(sorted(tok.keys())))
    if body.get("success") is False or not tok.get("access_token"):
        return None
    return tok


def _expiry(tok: dict) -> datetime:
    now = datetime.now(timezone.utc)
    if tok.get("expires_in"):
        return now + timedelta(seconds=float(tok["expires_in"]))
    for k in ("expires", "expiresAt", "expires_at"):
        v = tok.get(k)
        if isinstance(v, str):
            try:
                t = datetime.fromisoformat(v.replace("Z", "+00:00"))
                return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return now + timedelta(hours=1)


async def login_password(
    username: str, password: str, db: AsyncSession, athlete_id: int
) -> dict:
    """OAuth password grant first; on rejection, the website login + cookie
    token exchange. Credentials are never stored or logged; the access token,
    refresh token and session cookie are stored sealed (secrets.py)."""
    cookie = None
    try:
        token = await _password_grant(username, password)
        method = "oauth"
    except TpGrantRejected as e:
        log.info("TP password grant rejected (HTTP %d); trying the website login", e.status)
        cookie = await _web_login(username, password)
        token = await _token_from_cookie(cookie)
        if token is None:
            raise TpLoginError("TP_LOGIN_ERROR[token]: logged in, but users/v3/token refused the session")
        method = "web"

    # users/v3/user returns the authenticated user and accessible athletes.
    try:
        user_info = await _fetch_user(token["access_token"])
        tp_athlete_id, athletes_list, user_type, premium = _extract_athlete_id(user_info)
    except Exception as e:
        log.warning("TP login step=user failed (non-fatal): %s", type(e).__name__)
        user_info, tp_athlete_id, athletes_list, user_type, premium = {}, None, [], "", False
    if not tp_athlete_id:
        tp_athlete_id = await _athlete_id_fallback(token["access_token"], user_info)
        if tp_athlete_id:
            athletes_list = athletes_list or [{"id": tp_athlete_id, "self": True}]
    log.warning("TP login step=done method=%s athlete_id_found=%s premium=%s",
             method, bool(tp_athlete_id), premium)

    state_result = await db.execute(
        select(SyncState).where(SyncState.athlete_id == athlete_id)
    )
    state = state_result.scalar_one_or_none()
    if not state:
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.tp_access_token = seal(token["access_token"])
    state.tp_refresh_token = seal(token.get("refresh_token"))
    state.tp_web_cookie = seal(cookie)
    state.tp_token_expires = _expiry(token)

    if tp_athlete_id:
        await _ensure_athlete(db, athlete_id, tp_athlete_id)

    await db.commit()

    return {
        "authenticated": True,
        "tp_athlete_id": tp_athlete_id,
        "athletes": athletes_list,
        "user_type": user_type,
        "premium": premium,
        "can_download": _can_download(user_type, premium),
        "method": method,          # "oauth" (password grant) or "web" (website login)
    }


async def _fetch_user(access_token: str) -> dict:
    """GET users/v3/user — returns user profile + accessible athletes."""
    headers = {**TP_HEADERS, "Authorization": f"Bearer {access_token}"}
    async with http.client(
        base_url=TP_API_BASE, headers=headers, timeout=10
    ) as client:
        resp = await client.get("users/v3/user")
        _trace("user", resp)
        if resp.status_code == 200:
            body = resp.json()
            root = body.get("user") if isinstance(body, dict) and isinstance(body.get("user"), dict) else None
            if root is not None:
                log.warning("TP login step=user user_keys=%s", ",".join(sorted(root.keys())))
        if resp.status_code != 200:
            raise ValueError(
                f"users/v3/user failed ({resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()


def _extract_athlete_id(user_info: dict) -> tuple[Optional[int], list[dict], str, bool]:
    """
    Parse users/v3/user response. Field names from the WKO5.exe (Windows 5.0.587)
    string table, grouped together in PKTrainingPeaks.cpp:
      { "user": { "userId", "userName", "isCoach",
                  "athletes": [ { "athleteId", "athleteType": "basic"|"premium", ... } ] } }
    The premium gate is per-athlete `athleteType` ("basic" / "premium"), or the
    user being a coach — NOT a top-level `premium` flag.
    For a self-coached athlete, userId IS the athleteId.
    """
    root = user_info.get("user") if isinstance(user_info.get("user"), dict) else user_info
    # The live API (2026-09-30) returns numeric userType / athleteType / coachType
    # and explicit booleans isPremium / isCoached — not the "basic"/"premium"
    # strings the WKO5 string table suggested. Accept both.
    is_coach = (bool(root.get("isCoach")) or (root.get("coachType") or 0) > 0
                or "coach" in str(root.get("userType", "")).lower())
    athletes = root.get("athletes") or user_info.get("athletes") or []

    clean_athletes = []
    for a in athletes:
        aid = a.get("athleteId") or a.get("id") or a.get("userId")
        if aid:
            clean_athletes.append({
                "id": aid,
                "name": a.get("userName") or a.get("name") or "",
                "athlete_type": str(a.get("athleteType", "")),
                "premium": bool(a.get("isPremium")),
            })

    root_premium = bool(root.get("isPremium")) or bool(root.get("premium"))
    if clean_athletes:
        primary = clean_athletes[0]
        premium = root_premium or primary["premium"] or primary["athlete_type"].lower() == "premium"
        user_type = "coach" if is_coach else primary["athlete_type"]
        return primary["id"], clean_athletes, user_type, premium

    aid = root.get("athleteId") or root.get("userId") or root.get("personId") or root.get("id")
    premium = root_premium or str(root.get("athleteType", "")).lower() == "premium"
    user_type = "coach" if is_coach else str(root.get("athleteType", ""))
    if aid:
        return aid, [{"id": aid, "self": True}], user_type, premium
    return None, [], user_type, premium


def _first_id(obj) -> Optional[int]:
    """athleteId / userId / personId / id from a dict, or the first element
    of a list of such dicts."""
    if isinstance(obj, list):
        obj = obj[0] if obj else None
    if not isinstance(obj, dict):
        return None
    for k in ("athleteId", "userId", "personId", "id"):
        v = obj.get(k)
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            return int(v)
    return None


# Tried in order when users/v3/user didn't yield an athlete id. Each answers
# JSON for the logged-in user; only ids are read from it.
ATHLETE_ID_FALLBACKS = ("users/v3/user/athletes", "fitness/v1/athletes")


async def _athlete_id_fallback(access_token: str, user_info: dict) -> Optional[int]:
    for key in ("accountStatus", "athlete", "person"):
        aid = _first_id(user_info.get(key)) if isinstance(user_info, dict) else None
        if aid:
            log.warning("TP login step=athlete_id source=user.%s", key)
            return aid
    headers = {**TP_HEADERS, "Authorization": f"Bearer {access_token}"}
    async with http.client(base_url=TP_API_BASE, headers=headers, timeout=10) as client:
        for path in ATHLETE_ID_FALLBACKS:
            try:
                resp = await client.get(path)
            except httpx.HTTPError:
                continue
            _trace("athlete_id", resp)
            if resp.status_code != 200:
                continue
            try:
                body = resp.json()
            except Exception:
                continue
            aid = _first_id(body.get("athletes") if isinstance(body, dict) and "athletes" in body else body)
            if aid:
                return aid
    return None


def _can_download(user_type: str, premium: bool) -> bool:
    """Binary: 'Download is allowed only from premium and coach accounts.'
    This is a WKO5 client-side check; the server may or may not enforce it."""
    if premium:
        return True
    return "coach" in str(user_type or "").lower()


def _parse_changed(body) -> tuple[list[dict], list]:
    """workouts/changed returns {"modified": [...], "deleted": [...]}
    (binary log: "Received list of %u deleted workouts, and %u changed or new
    workouts."). Also tolerate a bare list / legacy "workouts" key."""
    if isinstance(body, list):
        return body, []
    if not isinstance(body, dict):
        return [], []
    modified = body.get("modified")
    if modified is None:
        modified = body.get("workouts") or []
    return modified or [], body.get("deleted") or []


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

async def _refresh_token(state: SyncState, db: AsyncSession) -> bool:
    """
    Refresh flow — body matches binary literally:
      grant_type=refresh_token&refresh_token={t}&client_id=WKO5&client_secret=
    """
    try:
        refresh = unseal(state.tp_refresh_token)
        cookie = unseal(state.tp_web_cookie)
    except SecretError:
        return False
    if not refresh and cookie:
        # website-login session: a fresh access token from the cookie
        tok = await _token_from_cookie(cookie)
        if tok is None:
            return False          # cookie rejected -> TP_AUTH_REQUIRED
        state.tp_access_token = seal(tok["access_token"])
        state.tp_token_expires = _expiry(tok)
        await db.commit()
        return True
    if not refresh:
        return False
    body = (
        f"grant_type=refresh_token"
        f"&refresh_token={_urlquote(refresh)}"
        f"&client_id={TP_CLIENT_ID}"
        f"&client_secret={TP_CLIENT_SECRET}"
    )
    async with http.client(timeout=15) as client:
        resp = await client.post(
            TP_OAUTH_URL,
            content=body,
            headers={**TP_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        log.warning("TP refresh failed (%d): %s", resp.status_code, resp.text[:200])
        return False
    token = resp.json()
    state.tp_access_token = seal(token["access_token"])
    state.tp_refresh_token = seal(token.get("refresh_token")) or state.tp_refresh_token
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
    # Refresh if within 5 minutes of expiry. SQLite returns the stored expiry
    # naive (it was written as UTC), so normalise before comparing.
    expires = as_utc(state.tp_token_expires)
    if expires and datetime.now(timezone.utc) >= expires - timedelta(minutes=5):
        ok = await _refresh_token(state, db)
        if not ok:
            return None
    try:
        return unseal(state.tp_access_token)
    except SecretError as e:
        log.warning("TP token unreadable: %s", e)
        return None


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

    headers = {**TP_HEADERS, "Authorization": f"Bearer {token}"}
    sync_started = datetime.now(timezone.utc)
    page = 1
    total_downloaded = 0
    total_checked = 0
    errors: list[str] = []

    async with http.client(
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
            page_items, deleted = _parse_changed(resp.json())
            if page == 1:
                yield {
                    "status": "changed_list",
                    "modified": len(page_items),
                    "deleted": len(deleted),
                }
            if not page_items and not deleted:
                break

            for wo in page_items:
                wo_id = wo.get("workoutId") or wo.get("id")
                wo_day = (wo.get("workoutDay") or wo.get("startTime") or "")[:10]
                if not wo_id:
                    continue
                if wo_day and wo_day > date.today().isoformat():
                    # planned (future) workout: nothing recorded yet
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

                # Register + parse via the shared import pipeline. A failed
                # import is rolled back (no half-written rows) and reported;
                # the cursor then stays put so the next sync retries it.
                from backend.files.file_service import _import_one_file, record_corrupt
                try:
                    record = await _import_one_file(
                        db, athlete_id, fit_path, source="trainingpeaks",
                        tp_workout_id=wo_id,
                    )
                    if record is None:
                        # unreadable FIT: remember it so it isn't re-downloaded
                        await db.rollback()
                        await record_corrupt(db, athlete_id, fit_path, source="trainingpeaks",
                                             workout_date=_iso_date(wo_day), tp_workout_id=wo_id)
                        await db.commit()
                        yield {"status": "error", "workout_id": wo_id,
                               "workout_date": wo_day, "detail": "corrupt_fit"}
                        continue
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    msg = f"workout {wo_id}: import_failed: {e}"
                    log.warning(msg)
                    errors.append(msg)
                    yield {"status": "error", "workout_id": wo_id,
                           "workout_date": wo_day, "detail": f"import_failed: {e}"}
                    continue
                total_downloaded += 1
                yield {
                    "status": "downloaded",
                    "workout_id": wo_id,
                    "workout_date": wo_day,
                    "file": str(fit_path),
                    "total_downloaded": total_downloaded,
                }

            page += 1
            if len(page_items) < page_size and len(deleted) < page_size:
                break

    # Only advance the cursor when this run actually got through cleanly —
    # otherwise a failed/empty run would silently skip history on the next sync.
    if state and not errors:
        state.last_sync_at = datetime.now(timezone.utc)
        state.last_sync_cursor = sync_started.date().isoformat()
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
        # 401 token / 403 premium gate / 404 / 5xx: an error, not "no file" —
        # returning None here used to advance the cursor past the workout.
        raise TpDownloadError(
            f"detaildata HTTP {resp.status_code}: {resp.text[:120]!r}")

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

    # WKO5 skips TP's auto-merged PWX ("Skipping auto merged PWX file %s").
    files = [f for f in files if "auto_merged" not in str(f.get("fileName", ""))] or files
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
        raise TpDownloadError(
            f"filedata {file_name} HTTP {fresp.status_code}: {fresp.text[:120]!r}")

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
    async with http.client(
        base_url=TP_API_BASE,
        headers={**TP_HEADERS, "Authorization": f"Bearer {token}"},
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
    async with http.client(timeout=15) as client:
        resp = await client.post(
            TP_OAUTH_URL,
            content=body,
            headers={**TP_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
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
    state.tp_access_token = seal(token["access_token"])
    state.tp_refresh_token = seal(token.get("refresh_token"))
    state.tp_token_expires = datetime.now(timezone.utc) + timedelta(
        seconds=token.get("expires_in", 3600)
    )
    if tp_athlete_id:
        await _ensure_athlete(db, athlete_id, tp_athlete_id)
    await db.commit()
    return token


async def _ensure_athlete(db: AsyncSession, athlete_id: int, tp_athlete_id: int) -> None:
    """Record the TP athlete id, creating the local athlete row on a fresh DB
    (sync needs it: it stores tp_athlete_id and the FIT download folder)."""
    athlete = (await db.execute(select(Athlete).where(Athlete.id == athlete_id))).scalar_one_or_none()
    if athlete is None:
        folder = Path.home() / ".wko5coach" / "fit" / f"athlete_{athlete_id}"
        folder.mkdir(parents=True, exist_ok=True)
        athlete = Athlete(id=athlete_id, name=f"athlete_{athlete_id}", data_dir=str(folder))
        db.add(athlete)
    athlete.tp_athlete_id = tp_athlete_id


def _iso_date(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _urlquote(s: str) -> str:
    """URL-encode a string for use inside an x-www-form-urlencoded body.

    Spaces become %20 (NOT '+') so the literal '+' in our pre-built scope
    string keeps its meaning as the OAuth scope delimiter.
    """
    from urllib.parse import quote
    return quote(s, safe="")
