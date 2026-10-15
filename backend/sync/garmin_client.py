"""
Garmin Connect sync for the SP-91 trial, through the unofficial `garminconnect` package
(python-garminconnect ≥ 0.3.17; docs/research/garmin.md). Structure as coros_client.py: login,
a sealed token, 「記住密碼」, sync_workouts' progress events, the shared import flow.

The package is optional: it is imported lazily (`lib()`); without it the app runs as before and
the Garmin option says it is not available (garmin_limits.available). Every Garmin request goes
through `_call` → garmin_limits.request (the switch, the account's pause and daily budget, the
server-wide 2-s gap) and a 429 / 403 is never retried (the package's own retries are off,
retry_attempts=0). Tests replace the package with a fake (`_LIB`); nothing here reaches the
network in tests.

Login (POST /api/v1/auth/garmin/login): email + password once. An account with MFA answers
"needs_mfa": the half-done login (the package's session object, which holds the MFA state) is kept
in memory for this tenant for MFA_TTL_S, and the code goes to POST /api/v1/auth/garmin/mfa; a wrong
code can be typed again (the package keeps the pending session after a failed code, 0.3.12+), at
most MFA_MAX_TRIES times. Nothing of it is written anywhere; a restart forgets it (log in again).
After the login only the library's token JSON (DI access + refresh token; the package refreshes
it by itself) is kept, sealed (settings/secrets.py) in sync_state.garmin_token_sealed. It never
appears in an API response or a log.

「記住密碼」 as COROS: stored sealed only when ticked, deleted on untick / disconnect, never
returned. Used only when Garmin refuses the token: ONE automatic re-login per run, which takes
one of the day's runs (garmin_limits.use_relogin) and is never made while the account is paused.
An MFA account cannot re-login by itself: it is asked to reconnect. Without a remembered password
a refused token is 登入已過期 —「請重新連接 Garmin」.

Sync: the activity list (newest first, PAGE_SIZE per request) down to the cursor minus
CURSOR_OVERLAP_DAYS (first sync: FIRST_SYNC_DAY, as COROS); each new one is downloaded with
download_activity(id, ORIGINAL) — a ZIP — and its .fit taken out and imported like a COROS file
(files/file_service._import_one_file, source "garmin", stored as the hashed object fit/garmin/objects/<aa>/<sha256>.fit.gz, SP-308);
dedup.py then makes it one activity with a COROS / TP row of the same start. A ZIP without a .fit
or a broken one is one error (the failed list, sync/failures.py); the rest of the list goes on.
When the account's request budget of the day is used (garmin_limits.REQUESTS_PER_DAY) the run
stops there with `more`; the cursor stays, so the next day lists again from the top: what is
imported already costs one DB lookup, the rest continues — a long history fills over a few days.

Heart rate: at the end of a sync the account's heart-rate zones (get_heart_rate_zones, one
request) give max HR, resting HR and LTHR as Garmin uses them; stored like COROS's
(athlete.garmin_profile; the LTHR also as a dated athlete_settings row). Field names follow
Garmin Connect's biometric-service/heartRateZones answer (maxHeartRateUsed,
lactateThresholdHeartRateUsed, restingHeartRateUsed) — read from the package's endpoint list;
not yet checked against a live account (SP-91 👆).

No workout push: sync/workout_targets/garmin.py stays disabled (owner 2026-10-07).
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import AsyncIterator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AthleteSettings, SyncState, WorkoutFile
from backend.settings.secrets import SecretError, seal, unseal
from backend.sync import events as EV
from backend.sync import failures as FL
from backend.sync import garmin_limits as GL
from backend.sync import session_check, storage
from backend.sync.http import as_utc

log = logging.getLogger(__name__)

SOURCE = "garmin"
FIRST_SYNC_DAY = date(2020, 1, 1)          # as coros_client.FIRST_SYNC_DAY
CURSOR_OVERLAP_DAYS = 14                   # as COROS (SP-362)
PAGE_SIZE = 50                             # activities per list request (the package allows 1000)
MFA_TTL_S = 600                            # a half-done MFA login is kept this long (memory only)
MFA_MAX_TRIES = 5
MAX_FIT_BYTES = 64 * 1024 * 1024           # a .fit bigger than this in the ZIP is refused (zip bomb)

_LIB = None                                # tests: a fake garminconnect module


class GarminUnavailable(RuntimeError):
    """The garminconnect package is not installed."""


class LoginBusy(RuntimeError):
    pass


class GarminAuthRequired(ValueError):
    """Not connected, or the token is refused and no re-login was possible."""


class GarminLoginFailed(ValueError):
    """Garmin refused the email / password (or the MFA code)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class GarminNoFit(ValueError):
    """The original download holds no .fit (a GPX / TCX upload, a manual entry)."""


class GarminBadFile(ValueError):
    """The original download is not a readable ZIP (or the .fit in it is too big / broken)."""


def lib():
    if _LIB is not None:
        return _LIB
    if not GL.available():
        raise GarminUnavailable("GARMIN_UNAVAILABLE: the garminconnect package is not installed")
    import garminconnect                     # lazy: optional dependency (requirements.txt)
    return garminconnect


def _new_api(email: Optional[str] = None, password: Optional[str] = None):
    # return_on_mfa: the MFA code comes with a second request (login_mfa); retry_attempts=0: no
    # retry loop inside the package (garmin_limits: a 429 / 403 is never retried)
    return lib().Garmin(email, password, return_on_mfa=True, retry_attempts=0)


# ---------------------------------------------------------------------------- errors -> codes

def _names(exc: BaseException) -> set:
    return {c.__name__ for c in type(exc).__mro__}


def _is_auth(exc: BaseException) -> bool:
    return "GarminConnectAuthenticationError" in _names(exc)


def _status(exc: BaseException) -> Optional[int]:
    """The HTTP status a library error stands for (429 for its rate-limit error)."""
    if "GarminConnectTooManyRequestsError" in _names(exc):
        return 429
    for obj in (exc, getattr(exc, "response", None)):
        sc = getattr(obj, "status_code", None)
        if isinstance(sc, int):
            return sc
    msg = str(exc)
    for code in (429, 403):                  # a login chain's message may list several answers
        if re.search(rf"\b{code}\b", msg):
            return code
    m = re.search(r"\b([45]\d\d)\b", msg)
    return int(m.group(1)) if m else None


async def _call(fn, *args, count: bool = True, login: bool = False):
    """One Garmin request in a worker thread (the package is synchronous), through the limits.
    429 / 403: GarminBlocked (the account is paused; never retried)."""
    async with GL.request(count=count):
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as e:
            st = _status(e)
            if st in (429, 403):
                raise GL.blocked(st, login=login) from None
            raise


def _tid() -> str:
    from backend import tenancy
    return tenancy.current().id


# ---------------------------------------------------------------------------- login

_LOGIN_LOCKS: dict = {}
_PENDING: dict = {}            # tenant id -> the half-done MFA login (memory only)


def _login_lock() -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), _tid())         # per tenant (SP-384)
    lk = _LOGIN_LOCKS.get(key)
    if lk is None:
        lk = _LOGIN_LOCKS[key] = asyncio.Lock()
    return lk


def mfa_pending() -> bool:
    p = _PENDING.get(_tid())
    if p is not None and p["until"] < time.monotonic():
        _PENDING.pop(_tid(), None)
        return False
    return p is not None


def forget_mfa() -> None:
    _PENDING.pop(_tid(), None)


async def _state(db: AsyncSession, athlete_id: int) -> Optional[SyncState]:
    return (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()


async def login(email: str, password: str, db: AsyncSession, athlete_id: int = 1,
                remember: bool = False) -> dict:
    """Email + password. {authenticated, email, password_saved} — or {mfa_required: true} when the
    account asks for a code (then login_mfa). GarminLoginFailed for a wrong password, GarminBlocked
    when refused as blocked, LoginBusy for a second login at once."""
    lk = _login_lock()
    if lk.locked():
        raise LoginBusy("GARMIN_LOGIN_BUSY: a Garmin login is already running")
    async with lk:
        forget_mfa()
        api = _new_api(email, password)
        res = await _login_call(api.login)
        mfa, state = res if isinstance(res, tuple) and len(res) == 2 else (None, None)
        if mfa == "needs_mfa":
            _PENDING[_tid()] = {"api": api, "state": state, "email": email, "remember": bool(remember),
                                "pw": seal(password) if remember else None, "tries": 0,
                                "until": time.monotonic() + MFA_TTL_S, "athlete_id": athlete_id}
            EV.log(SOURCE, "mfa_required", tenant=_tid(), lib=GL.lib_version())
            return {"authenticated": False, "mfa_required": True, "expires_in": MFA_TTL_S}
        return await _store_login(api, email, db, athlete_id, password if remember else None)


async def login_mfa(code: str, db: AsyncSession, athlete_id: int = 1) -> dict:
    """The MFA code of the login waiting for it. A wrong code: GarminLoginFailed GARMIN_MFA_WRONG,
    the login keeps waiting (type it again); expired / none: GARMIN_MFA_EXPIRED."""
    lk = _login_lock()
    if lk.locked():
        raise LoginBusy("GARMIN_LOGIN_BUSY: a Garmin login is already running")
    async with lk:
        if not mfa_pending():
            raise GarminLoginFailed("GARMIN_MFA_EXPIRED", "請重新輸入帳號密碼登入")
        p = _PENDING[_tid()]
        code = re.sub(r"\s+", "", str(code or ""))
        try:
            await _call(p["api"].resume_login, p["state"], code, count=False, login=True)
        except GL.GarminBlocked:
            forget_mfa()
            raise
        except Exception as e:                     # noqa: BLE001
            if _is_auth(e):
                p["tries"] += 1
                EV.log(SOURCE, "mfa_wrong", tenant=_tid(), tries=p["tries"])
                if p["tries"] >= MFA_MAX_TRIES:
                    forget_mfa()
                    raise GarminLoginFailed("GARMIN_MFA_EXPIRED", "驗證碼錯太多次，請重新登入")
                raise GarminLoginFailed("GARMIN_MFA_WRONG", "驗證碼不對，請再輸入一次")
            forget_mfa()
            EV.log(SOURCE, "login_failed", tenant=_tid(), kind="mfa_error", error=type(e).__name__,
                   lib=GL.lib_version())
            raise GarminLoginFailed("GARMIN_LOGIN_ERROR", type(e).__name__)
        forget_mfa()
        EV.log(SOURCE, "mfa_ok", tenant=_tid())
        pw = None
        if p["pw"]:
            try:
                pw = unseal(p["pw"])
            except SecretError:
                pw = None
        return await _store_login(p["api"], p["email"], db, athlete_id, pw)


async def _login_call(fn):
    """The library's login (a chain of requests of its own; one call to the limits, not charged to
    the day's request budget). A wrong password: GarminLoginFailed; logged without personal data."""
    try:
        return await _call(fn, count=False, login=True)
    except GL.GarminBlocked as e:
        EV.log(SOURCE, "login_blocked", tenant=_tid(), code=e.code, lib=GL.lib_version())
        raise
    except Exception as e:                         # noqa: BLE001
        kind = "auth" if _is_auth(e) else "error"
        EV.log(SOURCE, "login_failed", tenant=_tid(), kind=kind, error=type(e).__name__, lib=GL.lib_version())
        if kind == "auth":
            raise GarminLoginFailed("GARMIN_LOGIN_FAILED", "Garmin 帳號或密碼不對") from None
        raise GarminLoginFailed("GARMIN_LOGIN_ERROR", type(e).__name__) from None


async def _store_login(api, email: str, db: AsyncSession, athlete_id: int,
                       password: Optional[str]) -> dict:
    """Seal the token; the remembered password with it (None deletes one: 記住密碼 unticked)."""
    token = api.client.dumps()
    st = await _state(db, athlete_id)
    if st is None:
        st = SyncState(athlete_id=athlete_id)
        db.add(st)
    st.garmin_token_sealed = seal(token)          # SecretError (SECRET_KEY_MISSING) propagates
    st.garmin_token_expires = None
    st.garmin_email = email
    st.garmin_password_sealed = seal(password) if password else None
    await db.commit()
    session_check.mark_ok(SOURCE, athlete_id)
    GL.run_succeeded()
    EV.log(SOURCE, "login_ok", tenant=_tid(), lib=GL.lib_version())
    log.info("Garmin login OK")                   # nothing personal (SP-215)
    return {"authenticated": True, "email": email, "password_saved": bool(password)}


async def save_password(db: AsyncSession, athlete_id: int, password: Optional[str]) -> None:
    st = await _state(db, athlete_id)
    if st is None:
        return
    st.garmin_password_sealed = seal(password) if password else None
    await db.commit()


async def logout(db: AsyncSession, athlete_id: int = 1) -> None:
    """中斷連接: the token and the remembered password go; the imported activities stay."""
    forget_mfa()
    st = await _state(db, athlete_id)
    if st is not None:
        st.garmin_token_sealed = None
        st.garmin_token_expires = None
        st.garmin_password_sealed = None
        st.garmin_email = None
        await db.commit()
    session_check.forget(SOURCE, athlete_id)
    EV.log(SOURCE, "disconnected", tenant=_tid())


async def relogin(db: AsyncSession, athlete_id: int = 1) -> bool:
    """One automatic login with the remembered password (the token was refused). False without a
    remembered password, for an MFA account, or when Garmin refuses the password. Takes one of the
    day's runs; GarminBlocked when paused / none left / Garmin answers 429 / 403."""
    st = await _state(db, athlete_id)
    if st is None or not st.garmin_password_sealed or not st.garmin_email:
        return False
    try:
        password = unseal(st.garmin_password_sealed)
    except SecretError:
        return False
    GL.check_allowed(scheduled=False)             # never while paused (SP-91 A)
    GL.use_relogin()
    api = _new_api(st.garmin_email, password)
    try:
        res = await _login_call(api.login)
    except GarminLoginFailed:
        EV.log(SOURCE, "relogin_failed", tenant=_tid())
        return False
    mfa = res[0] if isinstance(res, tuple) and res else None
    if mfa == "needs_mfa":
        EV.log(SOURCE, "relogin_needs_mfa", tenant=_tid())
        return False
    await _store_login(api, st.garmin_email, db, athlete_id, password)
    EV.log(SOURCE, "relogin_ok", tenant=_tid())
    log.warning("Garmin token renewed with the remembered password")
    return True


async def _session(db: AsyncSession, athlete_id: int):
    """(api with the stored token loaded — no request —, state). GarminAuthRequired when not
    connected or the token is known refused (a remembered password logs in once first)."""
    st = await _state(db, athlete_id)
    if st is None or not st.garmin_token_sealed:
        raise GarminAuthRequired("not connected")
    if st.garmin_token_expires is not None:
        if not await relogin(db, athlete_id):
            session_check.mark_expired(SOURCE, athlete_id)
            raise GarminAuthRequired("token refused")
        await db.refresh(st)
    try:
        token = unseal(st.garmin_token_sealed)
    except SecretError as e:
        raise GarminAuthRequired(str(e))
    api = _new_api()
    api.client.loads(token)
    return api, st


async def _save_token(db: AsyncSession, st: SyncState, api) -> None:
    """The package refreshes the DI token by itself: keep the newest (sealed)."""
    try:
        tok = api.client.dumps()
        if not tok or not json.loads(tok).get("di_token"):
            return
        old = unseal(st.garmin_token_sealed) if st.garmin_token_sealed else None
        if tok != old:
            st.garmin_token_sealed = seal(tok)
            await db.commit()
    except Exception as e:                         # noqa: BLE001 — the sync result stands
        log.info("Garmin token not re-stored: %s", type(e).__name__)


async def _token_refused(db: AsyncSession, st: SyncState, athlete_id: int) -> None:
    st.garmin_token_expires = datetime.now(timezone.utc)
    await db.commit()
    session_check.mark_expired(SOURCE, athlete_id)
    EV.log(SOURCE, "token_refused", tenant=_tid(), password_saved=bool(st.garmin_password_sealed))


# ---------------------------------------------------------------------------- the original ZIP

def _is_fit(b: bytes) -> bool:
    return len(b) >= 12 and b[8:12] == b".FIT"


def extract_fit(blob: bytes) -> bytes:
    """The .fit of download_activity(ORIGINAL)'s answer: a ZIP with the original file (the
    package: "up to user to extract it"); a bare FIT is taken as it is. GarminNoFit when the ZIP
    has no .fit, GarminBadFile when it is not a readable ZIP."""
    if not blob:
        raise GarminNoFit("empty download")
    if _is_fit(blob):
        return blob
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, ValueError):
        raise GarminBadFile("not a ZIP") from None
    with zf:
        fits = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".fit")]
        if not fits:
            raise GarminNoFit("no .fit in the ZIP")
        info = max(fits, key=lambda i: i.file_size)
        if info.file_size > MAX_FIT_BYTES:
            raise GarminBadFile("the .fit in the ZIP is too big")
        try:
            return zf.read(info)
        except Exception:                          # noqa: BLE001 — CRC / zlib errors
            raise GarminBadFile("broken ZIP") from None


def _act_date(act: dict) -> Optional[date]:
    """The activity's local date (startTimeLocal "YYYY-MM-DD HH:MM:SS")."""
    for k in ("startTimeLocal", "startTimeGMT"):
        v = act.get(k) if isinstance(act, dict) else None
        if isinstance(v, str) and len(v) >= 10:
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                continue
    return None


def _type_token(act: dict) -> str:
    t = (act.get("activityType") or {}).get("typeKey") if isinstance(act.get("activityType"), dict) else None
    return re.sub(r"[^a-z0-9_]+", "", str(t or "other").lower())[:30] or "other"


# ---------------------------------------------------------------------------- one activity

async def _remember(db: AsyncSession, run: dict, coro) -> None:
    try:
        await coro
    except Exception as e:                         # noqa: BLE001
        log.warning("Garmin failed list not written: %s", type(e).__name__)
        run["hold"] = True
        try:
            await db.rollback()
        except Exception:                          # noqa: BLE001
            pass


async def _fetch_one(db: AsyncSession, athlete_id: int, api, act: dict, act_date: Optional[date],
                     run: dict) -> AsyncIterator[dict]:
    """Download one activity's original, take its .fit out and import it. A failure goes to the
    failed list and the batch goes on; a block / the day's budget stops the run (run["stop"])."""
    from backend.files.file_service import _import_one_file, record_corrupt
    from backend.sync.coros_sport import fit_session_sport, sport_token
    aid = str(act.get("activityId"))
    t_dl = time.monotonic()
    try:
        blob = await _call(api.download_activity, aid, lib().Garmin.ActivityDownloadFormat.ORIGINAL)
    except GL.GarminBlocked as e:
        run["stop"] = e
        return
    except Exception as e:                         # noqa: BLE001
        if _status(e) == 404:                      # no original file at all (a manual entry)
            kind = {}

            async def note():
                kind["k"] = (await FL.record_no_file_answer(db, athlete_id, SOURCE, aid, None, act_date)).kind
            await _remember(db, run, note())
            if kind.get("k") == FL.NO_FILE:
                yield {"status": "no_file", "activity_id": aid, "date": act_date.isoformat() if act_date else None}
                return
        log.warning("Garmin download failed %s: %s", aid, type(e).__name__)
        run["errors"].append(f"{aid}: {type(e).__name__}")
        await _remember(db, run, FL.record_failure(db, athlete_id, SOURCE, aid, type(e).__name__, None, act_date))
        yield {"status": "error", "activity_id": aid, "error": "GARMIN_DOWNLOAD_FAILED"}
        return
    dl_s = time.monotonic() - t_dl
    try:
        fit_bytes = extract_fit(blob)
    except (GarminNoFit, GarminBadFile) as e:
        # one error; the rest of the batch goes on (SP-91 B). No .fit: a GPX / TCX upload or a
        # manual entry — the second such answer makes it no_file (never retried again)
        code = "GARMIN_NO_FIT" if isinstance(e, GarminNoFit) else "GARMIN_BAD_ZIP"
        run["errors"].append(f"{aid}: {code}")
        if isinstance(e, GarminNoFit):
            await _remember(db, run, FL.record_no_file_answer(db, athlete_id, SOURCE, aid, None, act_date))
        else:
            await _remember(db, run, FL.record_failure(db, athlete_id, SOURCE, aid, f"{code}: {e}", None, act_date))
        yield {"status": "error", "activity_id": aid, "error": code, "detail": str(e)}
        return

    date_str = act_date.isoformat() if act_date else "unknown"
    # stored once, gzip, named by its sha256 (SP-308, files/fitstore.py):
    # <tenant>/fit/garmin/objects/<aa>/<sha256>.fit.gz; workout_files.file_path = that key
    from backend.files import fitstore
    stored = await fitstore.store_fit(db, fit_bytes, SOURCE)
    dest = stored.key
    fit_sport, fit_sub = await asyncio.to_thread(fit_session_sport, dest)
    sport = sport_token(fit_sport, fit_sub, None) if fit_sport else _type_token(act)
    filename = f"{aid}_{date_str}_{sport}.fit"
    if await fitstore.live_row(db, dest) is not None:
        # the same bytes are already an activity: stored once, recorded once
        await db.commit()
        await _remember(db, run, FL.resolve(db, athlete_id, SOURCE, aid))
        yield {"status": "skipped", "activity_id": aid, "reason": "same_content", "key": dest}
        return
    try:
        wf = await _import_one_file(db, athlete_id, dest, source=SOURCE)
        if wf is None:
            await db.rollback()
            await fitstore.store_fit(db, fit_bytes, SOURCE)       # the stub points at the object
            stub = await record_corrupt(db, athlete_id, dest, source=SOURCE, workout_date=act_date)
            stub.garmin_activity_id = aid
            await db.commit()
            await _remember(db, run, FL.resolve(db, athlete_id, SOURCE, aid))
            run["errors"].append(f"{aid}: corrupt_fit")
            yield {"status": "error", "activity_id": aid, "error": "corrupt_fit"}
            return
        wf.garmin_activity_id = aid
        await db.commit()
        run["downloaded"] += 1
        await _remember(db, run, FL.resolve(db, athlete_id, SOURCE, aid))
        yield {"status": "downloaded", "activity_id": aid, "file": filename, "key": dest, "sport": sport,
               "date": date_str, "secs": {"download": round(dl_s, 3)}}
    except Exception as e:                         # noqa: BLE001
        await db.rollback()
        log.warning("Garmin import failed for %s: %s", filename, type(e).__name__)
        run["errors"].append(f"{aid}: import_failed")
        await _remember(db, run, FL.record_failure(db, athlete_id, SOURCE, aid, f"import_failed: {e}", None, act_date))
        yield {"status": "error", "activity_id": aid, "error": "import_failed"}


# ---------------------------------------------------------------------------- the sync

def _blocked_event(e: GL.GarminBlocked) -> dict:
    return {"status": "error", "error": e.code, "detail": e.detail or str(e)}


async def sync_workouts(db: AsyncSession, athlete_id: int = 1, since: Optional[str] = None) -> AsyncIterator[dict]:
    """List Garmin activities → skip known → download the original ZIP → .fit → import → events.
    A run-level failure (login, listing, a block) keeps the cursor; one activity's failure goes to
    the failed list. The day's request budget used up: `more` on the complete event, cursor kept."""
    try:
        api, st = await _session(db, athlete_id)
    except GarminAuthRequired as e:
        yield {"status": "error", "error": "GARMIN_AUTH_REQUIRED", "detail": str(e), "hint": "請重新連接 Garmin"}
        return
    except GL.GarminBlocked as e:
        yield _blocked_event(e)
        return

    last = as_utc(st.garmin_last_sync_at)
    if since:
        since_day = date.fromisoformat(since[:10])
    elif last:
        since_day = last.date() - timedelta(days=CURSOR_OVERLAP_DAYS)
    else:
        from backend import site_config as SC
        since_day = SC.first_sync_day(FIRST_SYNC_DAY)          # the admin's history limit (SP-351)
    sync_started = datetime.now(timezone.utc)
    yield {"status": "started", "since": since_day.isoformat(), "until": sync_started.date().isoformat()}

    run = {"downloaded": 0, "errors": [], "hold": False, "stop": None}
    total_checked, start, relogged, more = 0, 0, False, False
    handled: set = set()
    done_listing = False
    while not done_listing and run["stop"] is None:
        try:
            acts = await _call(api.get_activities, start, PAGE_SIZE)
        except GL.GarminBlocked as e:
            run["stop"] = e
            break
        except Exception as e:                     # noqa: BLE001
            if _is_auth(e) or _status(e) == 401:
                if not relogged:
                    relogged = True
                    try:
                        ok = await relogin(db, athlete_id)
                    except GL.GarminBlocked as b:
                        yield _blocked_event(b)
                        return
                    if ok:
                        api, st = await _session(db, athlete_id)
                        yield {"status": "relogin", "detail": "Garmin token renewed (remembered password)"}
                        continue
                await _token_refused(db, st, athlete_id)
                yield {"status": "error", "error": "GARMIN_AUTH_REQUIRED", "detail": "token refused",
                       "hint": "請重新連接 Garmin"}
                return
            EV.log(SOURCE, "list_failed", tenant=_tid(), error=type(e).__name__, status=_status(e),
                   lib=GL.lib_version())
            yield {"status": "error", "error": "GARMIN_API_ERROR", "detail": type(e).__name__}
            return
        if isinstance(acts, dict):
            acts = acts.get("activityList") or acts.get("activities") or []
        if not acts:
            break
        for act in acts:
            if not isinstance(act, dict) or act.get("activityId") is None:
                continue
            aid = str(act["activityId"])
            act_date = _act_date(act)
            if act_date is not None and act_date < since_day:
                done_listing = True
                break
            total_checked += 1
            yield {"status": "checking", "activity_id": aid, "date": act_date.isoformat() if act_date else None}
            known = (await db.execute(select(WorkoutFile.id).where(
                WorkoutFile.athlete_id == athlete_id, WorkoutFile.garmin_activity_id == aid))).scalars().first()
            if known is not None:
                yield {"status": "skipped", "activity_id": aid, "reason": "already_imported"}
                continue
            prev = await FL.get(db, athlete_id, SOURCE, aid)
            if prev is not None and not FL.auto_retry(prev):
                yield {"status": "skipped", "activity_id": aid,
                       "reason": "no_file" if prev.kind == FL.NO_FILE else "retry_stopped"}
                continue
            handled.add(aid)
            async for ev in _fetch_one(db, athlete_id, api, act, act_date, run):
                yield ev
            if run["stop"] is not None:
                break
        if len(acts) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    # the failed list's due rows the listing did not meet: retried by id (SP-362)
    if run["stop"] is None:
        try:
            retry = [(r.provider_id, r.workout_date) for r in await FL.due(db, athlete_id, SOURCE)
                     if r.provider_id not in handled]
        except Exception as e:                     # noqa: BLE001
            log.warning("Garmin failed list not read: %s", type(e).__name__)
            retry, run["hold"] = [], True
        for aid, act_date in retry:
            known = (await db.execute(select(WorkoutFile.id).where(
                WorkoutFile.athlete_id == athlete_id, WorkoutFile.garmin_activity_id == aid))).scalars().first()
            if known is not None:
                await FL.resolve(db, athlete_id, SOURCE, aid)
                continue
            total_checked += 1
            yield {"status": "checking", "activity_id": aid, "date": act_date.isoformat() if act_date else None,
                   "retry": True}
            async for ev in _fetch_one(db, athlete_id, api, {"activityId": aid}, act_date, run):
                yield ev
            if run["stop"] is not None:
                break

    stop = run["stop"]
    if stop is not None and not (stop.code == "GARMIN_DAILY_LIMIT" and stop.detail == "requests"):
        # blocked (429 / 403), paused, switched off: the run ends here, cursor kept, no retry
        await _save_token(db, st, api)
        yield _blocked_event(stop)
        return
    if stop is not None:
        more, run["hold"] = True, True           # the day's request budget: tomorrow goes on
        EV.log(SOURCE, "budget_used", tenant=_tid(), downloaded=run["downloaded"])
    st = await _state(db, athlete_id) or st
    if not run["hold"]:
        st.garmin_last_sync_at = sync_started
        await db.commit()
    await _save_token(db, st, api)
    GL.run_succeeded()
    if not more:
        await refresh_hr_profile(db, athlete_id, api)
    yield {"status": "complete", "total_downloaded": run["downloaded"], "total_checked": total_checked,
           "errors": run["errors"], "more": more, **({"detail": GL.REQUESTS_TEXT} if more else {})}


# ---------------------------------------------------------------------------- heart rate

def parse_hr_zones(zones) -> Optional[dict]:
    """The HR part of get_heart_rate_zones(): one entry per sport profile (DEFAULT, RUNNING,
    CYCLING …); the running one, else the default. {max_hr, rest_hr, lthr, ratios: {},
    hr_zone_type, source: "garmin"} or None when it holds none of them."""
    rows = zones if isinstance(zones, list) else [zones] if isinstance(zones, dict) else []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None

    def pick(name):
        return next((r for r in rows if str(r.get("sport") or "").upper() == name), None)
    row = pick("RUNNING") or pick("DEFAULT") or rows[0]

    def num(*keys):
        for k in keys:
            v = row.get(k)
            try:
                if v is not None and 25 <= float(v) <= 250:
                    return int(round(float(v)))
            except (TypeError, ValueError):
                continue
        return None
    out = {"max_hr": num("maxHeartRateUsed", "maxHeartRate"),
           "rest_hr": num("restingHeartRateUsed", "restingHeartRate"),
           "lthr": num("lactateThresholdHeartRateUsed", "lactateThresholdHeartRate"),
           "ratios": {}, "hr_zone_type": row.get("trainingMethod"), "source": SOURCE}
    if not (out["max_hr"] or out["rest_hr"] or out["lthr"]):
        return None
    return out


PROFILE_KEY = "athlete.garmin_profile"


async def store_hr_profile(db: AsyncSession, athlete_id: int, prof: dict) -> None:
    """athlete.garmin_profile (when changed) and, as COROS's login does, the LTHR as a dated
    athlete_settings row (today). No values logged (personal data)."""
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    prof = {**prof, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    old = await repo.get(PROFILE_KEY) or {}
    if {k: v for k, v in old.items() if k != "at"} != {k: v for k, v in prof.items() if k != "at"}:
        await repo.set(PROFILE_KEY, prof)
    if prof.get("lthr"):
        today = datetime.now(timezone.utc).date()
        row = (await db.execute(select(AthleteSettings).where(
            AthleteSettings.athlete_id == athlete_id, AthleteSettings.effective_date == today))).scalar_one_or_none()
        if row is None:
            row = AthleteSettings(athlete_id=athlete_id, effective_date=today)
            db.add(row)
        row.lthr = int(prof["lthr"])
    await db.commit()
    log.info("Garmin HR profile stored: max %s, LTHR %s", *("yes" if prof.get(k) else "no" for k in ("max_hr", "lthr")))


async def refresh_hr_profile(db: AsyncSession, athlete_id: int, api) -> Optional[dict]:
    """Best effort (one request): a failure never fails the sync."""
    try:
        zones = await _call(api.get_heart_rate_zones)
    except GL.GarminBlocked:
        return None
    except Exception as e:                         # noqa: BLE001
        log.info("Garmin HR zones not read: %s", type(e).__name__)
        return None
    prof = parse_hr_zones(zones)
    if prof is None:
        return None
    try:
        await store_hr_profile(db, athlete_id, prof)
    except Exception as e:                         # noqa: BLE001
        await db.rollback()
        log.info("Garmin HR profile not stored: %s", type(e).__name__)
        return None
    return prof
