"""
TrainingPeaks sync client.

Login:
  1. OAuth password grant as WKO5 sends it, only when client credentials are
     configured at runtime (TP_CLIENT_ID / TP_CLIENT_SECRET or
     ~/.wko5coach/tp_client.json) and not disabled by the
     sync.trainingpeaks.use_wko5_client setting. Nothing secret is in code.
  2. Otherwise / on any rejection: the website form login -> Production_tpAuth
     cookie -> tpapi users/v3/token.
  Refresh: refresh_token grant with the same client credentials, then the
  cookie, then TP_AUTH_REQUIRED.

Endpoints in use (verified live 2026-09-30):
    users/v3/user
    fitness/v6/athletes/{aid}/workouts/{start}/{end}          first sync / since=
    fitness/v2/athletes/{aid}/workouts/changed?date=...       incremental
    fitness/v6/athletes/{aid}/workouts/{wid}/details          file list
    fitness/v6/athletes/{aid}/workouts/{wid}/rawfiledata/{id} gzip FIT
The WKO5-era detaildata / filedata calls no longer list or serve files;
filedata is only a fallback for a file without an id. The premium flag is
informational; basic accounts download.

Tokens, refresh token and cookie are stored sealed in SQLite (sync_state).
Credentials are never stored or logged.
"""
import base64
import gzip
import json
import os
import zlib
import re
import logging
import time
import httpx
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Optional, AsyncIterator
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import SyncState, Athlete, WorkoutFile
from backend.i18n import _
from backend.sync import http, session_check, storage
from backend.sync.http import as_utc
from backend.settings.secrets import SecretError, SecretKeyMissing, seal, unseal

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

# OAuth client credentials are NOT in the code. They are loaded at runtime
# from TP_CLIENT_ID / TP_CLIENT_SECRET, else ~/.wko5coach/tp_client.json
# ({"client_id", "client_secret"}). Without them the OAuth path is skipped and
# the website login is used. Using another app's OAuth client may break
# TrainingPeaks' terms; bring your own registered client. See .env.example.
TP_CLIENT_FILE = Path.home() / ".wko5coach" / "tp_client.json"

# Space-separated, i.e. '+' once form-urlencoded (as WKO5 sends it).
TP_SCOPE = "fitness baseactivity users metrics software groundcontrol"


CREDS_SOURCES = ("env", "file", "none")


def _creds_pair(d) -> Optional[tuple[str, str]]:
    if isinstance(d, dict) and d.get("client_id") and d.get("client_secret"):
        return str(d["client_id"]), str(d["client_secret"])
    return None


def lookup_client_creds() -> tuple[Optional[tuple[str, str]], str]:
    """(creds, source label). Order: env → ~/.wko5coach/tp_client.json.
    Nothing is shipped in the repo. Values are never logged."""
    cid, sec = os.getenv("TP_CLIENT_ID"), os.getenv("TP_CLIENT_SECRET")
    if cid and sec:
        return (cid, sec), "env"
    try:
        creds = _creds_pair(json.loads(TP_CLIENT_FILE.read_text("utf-8")))
    except (OSError, ValueError):
        creds = None
    if creds:
        return creds, "file"
    return None, "none"


def load_client_creds() -> Optional[tuple[str, str]]:
    """(client_id, client_secret) or None. Never logged."""
    return lookup_client_creds()[0]


async def oauth_enabled(db: AsyncSession, athlete_id: int) -> Optional[tuple[str, str]]:
    """Client creds when the WKO5-client OAuth path may be used: the setting
    sync.trainingpeaks.use_wko5_client is true, or unset (auto) and creds exist."""
    creds = load_client_creds()
    if creds is None:
        return None
    from backend.settings.repository import SettingsRepository
    flag = await SettingsRepository(db, athlete_id).get("sync.trainingpeaks.use_wko5_client")
    return creds if flag is None or flag else None


def _form(fields: dict) -> str:
    return urlencode(fields)          # quote_plus: spaces -> '+'


async def _token_post(fields: dict) -> httpx.Response:
    async with http.client(timeout=15) as client:
        return await client.post(
            TP_OAUTH_URL, content=_form(fields),
            headers={**TP_HEADERS, "Content-Type": "application/x-www-form-urlencoded"})


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


async def _password_grant(username: str, password: str, creds: tuple[str, str]) -> dict:
    """
    Password grant as WKO5 sends it: form-urlencoded grant_type=password,
    username, password, scope (space-separated), client_id, client_secret,
    User-Agent WKO5/PC/5.0.587. Without the real client secret TP answers
    invalid_client / invalid_grant — then the website login is used.
    """
    resp = await _token_post({
        "grant_type": "password", "username": username, "password": password,
        "scope": TP_SCOPE, "client_id": creds[0], "client_secret": creds[1]})
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
    username: str, password: str, db: AsyncSession, athlete_id: int,
    prefer: Optional[str] = None,
) -> dict:
    """OAuth password grant first; on rejection, the website login + cookie
    token exchange. Credentials are never stored or logged; the access token,
    refresh token and session cookie are stored sealed (secrets.py).

    prefer="web": website login only. prefer="oauth": the WKO5-client grant
    only (no fallback; needs configured credentials)."""
    cookie = None
    token = None
    if prefer not in (None, "auto", "web", "oauth"):
        raise TpLoginError("TP_LOGIN_ERROR: method must be auto, web or oauth")
    if prefer == "oauth":
        creds = load_client_creds()
        if creds is None:
            raise TpLoginError("TP_LOGIN_ERROR[oauth]: no TP client credentials configured")
        try:
            token = await _password_grant(username, password, creds)
        except TpGrantRejected as e:
            raise TpLoginError(f"TP_LOGIN_FAILED[oauth]: HTTP {e.status}")
        method = "oauth"
        creds = None
    else:
        creds = None if prefer == "web" else await oauth_enabled(db, athlete_id)
    if token is not None:
        pass
    elif creds is not None:
        try:
            token = await _password_grant(username, password, creds)
            method = "oauth"
        except TpGrantRejected as e:
            err = ""
            try:
                err = str(json.loads(e.body or "null").get("error", ""))
            except (ValueError, AttributeError):
                pass
            log.warning("TP login step=oauth status=%d error=%s; using the website login",
                        e.status, err or "-")
    else:
        log.warning("TP login step=oauth skipped (no client credentials / disabled)")
    if token is None:
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
        tp_athlete_id, athletes_list, user_type, premium = None, [], "", False
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
    session_check.mark_ok("tp", athlete_id)

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

    aid = root.get("athleteId") or root.get("userId")
    premium = root_premium or str(root.get("athleteType", "")).lower() == "premium"
    user_type = "coach" if is_coach else str(root.get("athleteType", ""))
    if aid:
        return aid, [{"id": aid, "self": True}], user_type, premium
    return None, [], user_type, premium


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
    creds = load_client_creds()
    if refresh and creds:
        resp = await _token_post({"grant_type": "refresh_token", "refresh_token": refresh,
                                  "client_id": creds[0], "client_secret": creds[1]})
        if resp.status_code == 200:
            token = resp.json()
            state.tp_access_token = seal(token["access_token"])
            state.tp_refresh_token = seal(token.get("refresh_token")) or state.tp_refresh_token
            state.tp_token_expires = _expiry(token)
            await db.commit()
            return True
        log.warning("TP login step=refresh status=%d; trying the session cookie", resp.status_code)
    if cookie:
        # website-login session: a fresh access token from the cookie
        tok = await _token_from_cookie(cookie)
        if tok is None:
            return False          # cookie rejected -> TP_AUTH_REQUIRED
        state.tp_access_token = seal(tok["access_token"])
        state.tp_token_expires = _expiry(tok)
        await db.commit()
        return True
    return False


# 「記住密碼」 (opt-in, user-approved 2026-10-01): username + password sealed
# (secrets.seal) in sync_state; used only when the token can no longer be
# refreshed (refresh token / session cookie rejected): ONE automatic login,
# serialised by a lock, never retried in a loop. Deleted on untick / logout.
_RELOGIN_LOCKS: dict = {}


def _relogin_lock():
    import asyncio
    loop = asyncio.get_running_loop()
    lk = _RELOGIN_LOCKS.get(id(loop))
    if lk is None:
        lk = _RELOGIN_LOCKS[id(loop)] = asyncio.Lock()
    return lk


async def save_password(db: AsyncSession, athlete_id: int, username: Optional[str],
                        password: Optional[str]) -> None:
    """Store (sealed) or delete (password None) the remembered TP login; commits."""
    state = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    if state is None:
        if not password:
            return
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.tp_username = username if password else None
    state.tp_password_sealed = seal(password) if password else None
    await db.commit()


async def _relogin(db: AsyncSession, athlete_id: int, state: SyncState) -> bool:
    async with _relogin_lock():
        await db.refresh(state)
        expires = as_utc(state.tp_token_expires)
        if expires and datetime.now(timezone.utc) < expires - timedelta(minutes=5):
            return True                       # another caller renewed it while we waited
        if not (state.tp_password_sealed and state.tp_username):
            return False
        try:
            password = unseal(state.tp_password_sealed)
        except SecretError:
            return False
        try:
            await login_password(state.tp_username, password, db, athlete_id)
        except SecretKeyMissing:
            raise
        except Exception as e:                # noqa: BLE001 — reported as TP_AUTH_REQUIRED, not retried
            log.warning("TP automatic re-login failed: %s", type(e).__name__)
            return False
        log.warning("TP token renewed with the remembered password")
        await db.refresh(state)
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
        if not ok and state.tp_password_sealed:
            ok = await _relogin(db, athlete_id, state)
        if not ok:
            session_check.mark_expired("tp", athlete_id)
            return None
    try:
        return unseal(state.tp_access_token)
    except SecretError as e:
        log.warning("TP token unreadable: %s", e)
        session_check.mark_expired("tp", athlete_id)
        return None


async def probe_token(access_token: str) -> str:
    """The login check (sync/session_check.py): GET users/v3/user.
    "ok" | "invalid" (401 / 403) | "unknown" (network / other)."""
    headers = {**TP_HEADERS, "Authorization": f"Bearer {access_token}"}
    try:
        async with http.client(base_url=TP_API_BASE, headers=headers, timeout=10) as client:
            resp = await client.get("users/v3/user")
    except Exception:                         # noqa: BLE001 — unknown, never raises
        return "unknown"
    if resp.status_code in (401, 403):
        return "invalid"
    return "ok" if resp.status_code == 200 else "unknown"


# ---------------------------------------------------------------------------
# Workout sync
# ---------------------------------------------------------------------------

DEFAULT_FIRST_SYNC_DAY = "2010-01-01"
RANGE_CHUNK_DAYS = 90


def date_chunks(start: str, end: str, days: int = RANGE_CHUNK_DAYS) -> list[tuple[str, str]]:
    """Inclusive [start, end] split into ranges of at most `days` days."""
    a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    out = []
    while a <= b:
        e = min(b, a + timedelta(days=days - 1))
        out.append((a.isoformat(), e.isoformat()))
        a = e + timedelta(days=1)
    return out


async def _iter_date_range(client: httpx.AsyncClient, aid: int, start: str, end: str):
    """GET fitness/v6/athletes/{aid}/workouts/{start}/{end} per chunk (verified
    live 2026-09-30: a JSON list of workout objects with workoutId,
    workoutDay, startTime). Yields (items, info-event)."""
    first = True
    for a, b in date_chunks(start, end):
        try:
            resp = await client.get(f"fitness/v6/athletes/{aid}/workouts/{a}/{b}")
        except httpx.HTTPError as e:
            yield [], {"error": "TP_API_ERROR", "detail": str(e)}
            return
        if resp.status_code != 200:
            yield [], {"error": "TP_API_ERROR", "status": resp.status_code, "body": resp.text[:200]}
            return
        body = resp.json()
        items = body if isinstance(body, list) else (body.get("workouts") or [] if isinstance(body, dict) else [])
        yield items, {"status": "range_list", "from": a, "to": b, "count": len(items),
                      **({"mode": "date_range"} if first else {})}
        first = False


async def _iter_changed(client: httpx.AsyncClient, aid: int, cursor: str, page_size: int):
    """Incremental: workouts/changed (modification date after the cursor)."""
    page = 1
    while True:
        # format string from the WKO5 binary:
        #   fitness/v2/athletes/%d/workouts/changed?date=%s&searchDirection=After&pageSize=%u&page=%u
        url = (f"fitness/v2/athletes/{aid}/workouts/changed"
               f"?date={cursor}&searchDirection=After&pageSize={page_size}&page={page}")
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            yield [], {"error": "TP_API_ERROR", "detail": str(e)}
            return
        if resp.status_code != 200:
            yield [], {"error": "TP_API_ERROR", "status": resp.status_code, "body": resp.text[:200]}
            return
        items, deleted = _parse_changed(resp.json())
        yield items, ({"status": "changed_list", "mode": "changed", "modified": len(items),
                       "deleted": len(deleted)} if page == 1 else {})
        if not items and not deleted:
            return
        if len(items) < page_size and len(deleted) < page_size:
            return
        page += 1


async def sync_workouts(
    db: AsyncSession,
    athlete_id: int,
    since: Optional[str] = None,
    page_size: int = 20,
) -> AsyncIterator[dict]:
    """
    Yield SSE-friendly progress dicts as workouts are downloaded.
      1. list: date range (first sync / since=) or workouts/changed (incremental)
      2. per workout: details → workoutDeviceFileInfos / attachmentFileInfos
      3. rawfiledata/{fileId} → gzip → FIT → import
    The premium flag is informational only; basic accounts download too.
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
    total_downloaded = 0
    total_checked = 0
    errors: list[str] = []
    # First sync, or an explicit since=: list by workout date (date range).
    # Later syncs: workouts/changed, which filters on *modification* date.
    incremental = since is None and bool(state and state.last_sync_cursor)
    list_from = since or DEFAULT_FIRST_SYNC_DAY

    async with http.client(
        base_url=TP_API_BASE, headers=headers, timeout=60
    ) as client:
        if incremental:
            pages = _iter_changed(client, athlete.tp_athlete_id, cursor, page_size)
        else:
            pages = _iter_date_range(client, athlete.tp_athlete_id, list_from, date.today().isoformat())
        async for page_items, info in pages:
            if "error" in info:
                if info.get("status") in (401, 403):
                    # TP refused the token: the login is gone (sync/session_check.py)
                    session_check.mark_expired("tp", athlete_id)
                    info = {**info, "error": "TP_AUTH_REQUIRED", "hint": _("請到設定頁重新登入 TrainingPeaks")}
                yield info
                return
            if info:
                yield info
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

                t_dl = time.monotonic()        # download vs import seconds (sync/runner.SyncClock)
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

                dl_s = time.monotonic() - t_dl
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
                    "secs": {"download": round(dl_s, 3)},
                }


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


FIT_NAME = re.compile(r"\.fit(\.gz)?$", re.I)
GZIP_MAGIC = bytes([0x1F, 0x8B])


def pick_fit_file(details: dict) -> Optional[dict]:
    """The device FIT (.fit / .fit.gz) from a details response, else a FIT
    attachment. TP's auto-merged files are skipped (as WKO5 does)."""
    for key in ("workoutDeviceFileInfos", "attachmentFileInfos"):
        infos = details.get(key) or []
        fits = [f for f in infos if isinstance(f, dict)
                and FIT_NAME.search(str(f.get("fileName", "")))
                and "auto_merged" not in str(f.get("fileName", ""))]
        if fits:
            return {**fits[0], "_kind": "device" if key.startswith("workout") else "attachment"}
    return None


def fit_bytes(blob: bytes) -> bytes:
    """Gunzip when the gzip magic is present (names are not trusted)."""
    if blob[:2] == GZIP_MAGIC:
        try:
            return gzip.decompress(blob)
        except OSError as e:
            log.warning("TP file gunzip failed: %s", e)
            return blob
    return blob


def is_fit(raw: bytes) -> bool:
    # header: size(1) protocol(1) profile(2) data_size(4) ".FIT" crc(2)
    return len(raw) >= 14 and raw[8:12] == b".FIT"


async def _download_workout_fit(
    client: httpx.AsyncClient, athlete: Athlete, workout_id: int, workout_day: str
) -> Optional[Path]:
    """
    Verified live 2026-09-30 (the WKO5-era detaildata / filedata calls no
    longer list or serve files):
      1. GET fitness/v6/athletes/{aid}/workouts/{wid}/details
         -> workoutDeviceFileInfos / attachmentFileInfos: [{fileId, fileName: "….fit.gz", …}]
      2. GET fitness/v6/athletes/{aid}/workouts/{wid}/rawfiledata/{fileId}
         -> 200 application/gzip, raw gzip bytes of the FIT
    Fallback when a file has no fileId: the old filedata/{fileName} (JSON
    base64). Returns None only when the workout has no FIT at all; HTTP
    failures raise TpDownloadError (cursor kept). A blob that isn't a FIT is
    still written, so the import marks it corrupt (stub) instead of retrying
    forever.
    """
    base = f"fitness/v6/athletes/{athlete.tp_athlete_id}/workouts/{workout_id}"
    resp = await client.get(f"{base}/details")
    if resp.status_code != 200:
        raise TpDownloadError(f"details HTTP {resp.status_code}: {resp.text[:120]!r}")
    details = resp.json()
    if not isinstance(details, dict):
        raise TpDownloadError("details: unexpected response shape")
    info = pick_fit_file(details)
    if info is None:
        return None

    file_id = info.get("fileId")
    if file_id:
        fresp = await client.get(f"{base}/rawfiledata/{file_id}")
        if fresp.status_code != 200:
            raise TpDownloadError(f"rawfiledata HTTP {fresp.status_code}: {fresp.text[:120]!r}")
        ctype = fresp.headers.get("content-type", "").lower()
        raw = _decode_filedata_response(fresp) if "json" in ctype else fit_bytes(fresp.content)
    else:
        fresp = await client.get(f"{base}/filedata/{info.get('fileName')}")
        if fresp.status_code != 200:
            raise TpDownloadError(f"filedata HTTP {fresp.status_code}: {fresp.text[:120]!r}")
        raw = _decode_filedata_response(fresp)
    raw = fit_bytes(raw or b"")
    if not is_fit(raw):
        log.warning("TP workout %s: downloaded %s file is not a FIT (%d bytes)",
                    workout_id, info["_kind"], len(raw))

    year = (workout_day or "2000-01-01")[:4]
    save_dir = storage.year_dir("tp", year)          # ~/.wko5coach/fit/tp/<year>/

    safe_day = (workout_day or "unknown").replace("-", "_")
    save_path = save_dir / f"tp_{safe_day}_{workout_id}.fit"
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


def _require_creds() -> tuple[str, str]:
    creds = load_client_creds()
    if creds is None:
        raise ValueError("TP OAuth client credentials not configured "
                         "(TP_CLIENT_ID / TP_CLIENT_SECRET or ~/.wko5coach/tp_client.json)")
    return creds


async def get_auth_url() -> str:
    """OAuth authorization-code redirect URL (kept for completeness)."""
    return ("https://oauth.trainingpeaks.com/OAuth/Authorize?" + urlencode({
        "response_type": "code", "client_id": _require_creds()[0],
        "redirect_uri": "http://localhost:8000/api/v1/auth/tp/callback", "scope": TP_SCOPE}))


async def exchange_code(code: str, db: AsyncSession, athlete_id: int) -> dict:
    """Authorization-code exchange (kept for future use if needed)."""
    cid, sec = _require_creds()
    resp = await _token_post({
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "client_secret": sec, "redirect_uri": "http://localhost:8000/api/v1/auth/tp/callback"})
    if resp.status_code != 200:
        raise ValueError(f"TP code exchange failed (HTTP {resp.status_code})")
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
    session_check.mark_ok("tp", athlete_id)
    return token


async def _ensure_athlete(db: AsyncSession, athlete_id: int, tp_athlete_id: int) -> None:
    """Record the TP athlete id, creating the local athlete row on a fresh DB
    (sync needs it: it stores tp_athlete_id and the FIT download folder)."""
    athlete = (await db.execute(select(Athlete).where(Athlete.id == athlete_id))).scalar_one_or_none()
    if athlete is None:
        folder = storage.fit_root()          # synced FITs live in per-source folders below it
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
