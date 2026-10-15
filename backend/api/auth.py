from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.db.database import get_db
from backend.i18n import _
from backend.sync.tp_client import login_password, fetch_tp_settings
from backend.sync import coros_client, session_check
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
    the server key; remember=false deletes a stored one. Never returned.
    Returns: { authenticated, tp_athlete_id, athletes, user_type, premium, can_download, password_saved }
    Note: download requires premium or coach account.
    """
    from backend.sync.tp_client import save_password
    from backend import membership as MS
    MS.require("sync:tp_web")              # TP's website login: the owner's only (SP-330, owner 2026-10-12)
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
    if state and state.tp_access_token:
        # validated like COROS (refresh / remembered-password login first)
        session = _session_fields(await session_check.check(db, "tp", athlete_id))
        await db.refresh(state)
    else:
        session = {"authenticated": False, "expired": False, "status": "logged_out"}
    return {
        **session,
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
    session_check.forget("tp", athlete_id)
    return {"logged_out": True}


class CorosLoginRequest(BaseModel):
    email: str
    password: str
    athlete_id: int = 1
    remember: bool = False            # 「記住密碼」
    consent_version: Optional[str] = None   # the 非官方介面 disclaimer ticked (SP-385): its version


CONSENT_KEY = "sync.coros.consent"


async def _coros_consent(db: AsyncSession, athlete_id: int, version: Optional[str]) -> None:
    """SP-385: connecting COROS needs the disclaimer ticked (its version and the time are kept in
    the tenant's settings). Not the owner: the owner runs the server (the self-hosted app behaves
    as before; the owner's existing connection needed no new tick anyway). 400
    COROS_CONSENT_REQUIRED with the text to show otherwise."""
    from backend import tenancy
    from backend.settings.repository import SettingsRepository
    from backend.sync import coros_limits as CL
    repo = SettingsRepository(db, athlete_id)
    have = await repo.get(CONSENT_KEY)
    if version == CL.DISCLAIMER_VERSION:
        if not (isinstance(have, dict) and have.get("version") == version):
            import datetime as _dt
            await repo.set(CONSENT_KEY, {"version": version,
                                         "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")})
            await db.commit()
        return
    if isinstance(have, dict) and have.get("version") == CL.DISCLAIMER_VERSION:
        return
    if tenancy.is_owner():
        return                                # the server's owner: no tick asked
    from backend.i18n import current_locale
    raise HTTPException(400, {"code": "COROS_CONSENT_REQUIRED", "version": CL.DISCLAIMER_VERSION,
                              "message": CL.DISCLAIMER.get(current_locale(), CL.DISCLAIMER["en"])})


@router.get("/coros/disclaimer")
async def coros_disclaimer(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The 非官方介面 disclaimer shown before connecting COROS (SP-385) and whether this tenant
    has ticked the current version."""
    from backend.i18n import current_locale
    from backend.settings.repository import SettingsRepository
    from backend.sync import coros_limits as CL
    from backend import tenancy
    have = await SettingsRepository(db, athlete_id).get(CONSENT_KEY)
    return {"version": CL.DISCLAIMER_VERSION, "text": CL.DISCLAIMER.get(current_locale(), CL.DISCLAIMER["en"]),
            "required": not tenancy.is_owner(),
            "consented": isinstance(have, dict) and have.get("version") == CL.DISCLAIMER_VERSION,
            "consented_at": have.get("at") if isinstance(have, dict) else None}


class CorosSiteIn(BaseModel):
    enabled: Optional[bool] = None
    rate_per_s: Optional[float] = None
    rate_per_min: Optional[float] = None
    max_concurrent: Optional[int] = None


@router.get("/coros/site")
def coros_site():
    """The server-wide COROS state (SP-385): switch, breaker, cooldown, this account's pause,
    the limits. Plain, nothing personal; the event log is the owner's."""
    from backend import tenancy
    from backend.sync import coros_limits as CL, events as EV
    out = CL.status()
    if tenancy.is_owner():
        out["events"] = EV.read("coros", 50)
    return out


@router.put("/coros/site")
def put_coros_site(body: CorosSiteIn):
    """The owner turns the server-wide COROS switch off / back on (a tripped breaker too) and sets
    the limits — until SP-351's admin pages. 403 for anyone else."""
    from backend import tenancy, site_settings as SS
    from backend.sync import coros_limits as CL
    if not tenancy.is_owner():
        raise HTTPException(403, "只有站主能切換 COROS 全站開關")
    vals = {}
    for k in ("rate_per_s", "rate_per_min", "max_concurrent"):
        v = getattr(body, k)
        if v is not None:
            if v <= 0:
                raise HTTPException(400, f"{k} must be > 0")
            vals[f"coros.{k}"] = v
    if vals:
        SS.update(vals)
    if body.enabled is not None:
        CL.set_enabled(bool(body.enabled), by="owner")
    return CL.status()


@router.post("/coros/login")
async def coros_login(body: CorosLoginRequest, db: AsyncSession = Depends(get_db)):
    """Log in (one login at a time: a second concurrent one gets 409
    COROS_LOGIN_BUSY — COROS keeps only the latest token). remember=true
    stores the password sealed for the automatic re-login; false deletes it."""
    from backend.sync import coros_limits as CL, events as EV
    from backend import membership as MS, tenancy
    if not MS.role_allows("sync:coros_credential"):
        raise MS.scope_error("sync:coros_credential")
    await _coros_consent(db, body.athlete_id, body.consent_version)
    try:
        CL.check_allowed()
    except CL.CorosBlocked as e:
        raise HTTPException(503, {"code": e.code, "site": CL.status()})
    try:
        result = await coros_client.login(body.email, body.password, db, body.athlete_id)
        await coros_client.save_password(db, body.athlete_id, body.password if body.remember else None)
        EV.log("coros", "login_ok", tenant=tenancy.current().id)
        return {**result, "password_saved": bool(body.remember)}
    except CL.CorosBlocked as e:
        raise HTTPException(503, {"code": e.code, "site": CL.status()})
    except coros_client.LoginBusy as e:
        raise HTTPException(409, str(e))
    except SecretKeyMissing as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        detail = str(e)
        EV.log("coros", "login_failed", tenant=tenancy.current().id)
        if "401" in detail or "login failed" in detail.lower():
            raise HTTPException(401, f"COROS_LOGIN_FAILED: {detail}")
        raise HTTPException(502, f"COROS_LOGIN_ERROR: {detail}")


# ---------------------------------------------------------------------------
# Garmin Connect (SP-91 trial): unofficial `garminconnect`, limits in sync/garmin_limits.py
# ---------------------------------------------------------------------------

class GarminLoginRequest(BaseModel):
    email: str
    password: str
    athlete_id: int = 1
    remember: bool = False            # 「記住密碼」
    consent_version: Optional[str] = None   # the 非官方介面 disclaimer ticked: its version


class GarminMfaRequest(BaseModel):
    code: str
    athlete_id: int = 1


GARMIN_CONSENT_KEY = "sync.garmin.consent"


def _garmin_text() -> str:
    from backend.i18n import current_locale
    from backend.sync import garmin_limits as GL
    return GL.DISCLAIMER.get(current_locale(), GL.DISCLAIMER["en"])


async def _garmin_consent(db: AsyncSession, athlete_id: int, version: Optional[str]) -> None:
    """Connecting Garmin needs the disclaimer ticked — everyone, the owner too (no Garmin connection
    existed before the trial). Its version and the time are kept in the tenant's settings. 400
    GARMIN_CONSENT_REQUIRED with the text to show otherwise."""
    import datetime as _dt
    from backend.settings.repository import SettingsRepository
    from backend.sync import garmin_limits as GL
    repo = SettingsRepository(db, athlete_id)
    have = await repo.get(GARMIN_CONSENT_KEY)
    if version == GL.DISCLAIMER_VERSION:
        if not (isinstance(have, dict) and have.get("version") == version):
            await repo.set(GARMIN_CONSENT_KEY, {"version": version,
                                                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")})
            await db.commit()
        return
    if isinstance(have, dict) and have.get("version") == GL.DISCLAIMER_VERSION:
        return
    raise HTTPException(400, {"code": "GARMIN_CONSENT_REQUIRED", "version": GL.DISCLAIMER_VERSION,
                              "message": _garmin_text()})


@router.get("/garmin/disclaimer")
async def garmin_disclaimer(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The 非官方介面 disclaimer shown before connecting Garmin and whether this tenant has ticked
    the current version."""
    from backend.settings.repository import SettingsRepository
    from backend.sync import garmin_limits as GL
    have = await SettingsRepository(db, athlete_id).get(GARMIN_CONSENT_KEY)
    return {"version": GL.DISCLAIMER_VERSION, "text": _garmin_text(),
            "consented": isinstance(have, dict) and have.get("version") == GL.DISCLAIMER_VERSION,
            "consented_at": have.get("at") if isinstance(have, dict) else None}


class GarminSiteIn(BaseModel):
    enabled: Optional[bool] = None
    auto_per_day: Optional[int] = None
    manual_per_day: Optional[int] = None
    requests_per_day: Optional[int] = None
    gap_s: Optional[float] = None


@router.get("/garmin/site")
def garmin_site():
    """The server-wide Garmin state (switch, breaker, this account's pause and what is left of
    today's syncs, the limits) in plain words; the event log is the owner's."""
    from backend import tenancy
    from backend.sync import events as EV, garmin_limits as GL
    out = GL.status()
    if tenancy.is_owner():
        out["events"] = EV.read("garmin", 50)
    return out


@router.put("/garmin/site")
def put_garmin_site(body: GarminSiteIn):
    """The owner turns the server-wide Garmin switch off / back on (a tripped breaker too) and sets
    the limits — until SP-351's admin pages. 403 for anyone else."""
    from backend import tenancy, site_settings as SS
    from backend.sync import garmin_limits as GL
    if not tenancy.is_owner():
        raise HTTPException(403, "只有站主能切換 Garmin 全站開關")
    vals = {}
    for k in ("auto_per_day", "manual_per_day", "requests_per_day", "gap_s"):
        v = getattr(body, k)
        if v is not None:
            if v < 0 or (k in ("requests_per_day", "gap_s") and v <= 0):
                raise HTTPException(400, f"{k} must be > 0")
            vals[f"garmin.{k}"] = v
    if vals:
        SS.update(vals)
    if body.enabled is not None:
        GL.set_enabled(bool(body.enabled), by="owner")
    return GL.status()


def _garmin_error(e: Exception) -> HTTPException:
    from backend.sync import garmin_client as GC, garmin_limits as GL
    if isinstance(e, GL.GarminBlocked):
        return HTTPException(503, {"code": e.code, "message": e.detail or str(e), "site": GL.status()})
    if isinstance(e, GC.GarminUnavailable):
        return HTTPException(503, {"code": "GARMIN_UNAVAILABLE", "site": GL.status()})
    if isinstance(e, GC.LoginBusy):
        return HTTPException(409, str(e))
    if isinstance(e, SecretKeyMissing):
        return HTTPException(503, str(e))
    if isinstance(e, GC.GarminLoginFailed):
        status = 502 if e.code == "GARMIN_LOGIN_ERROR" else 401
        return HTTPException(status, {"code": e.code, "message": str(e).split(": ", 1)[-1]})
    return HTTPException(502, {"code": "GARMIN_LOGIN_ERROR", "message": type(e).__name__})


@router.post("/garmin/login")
async def garmin_login(body: GarminLoginRequest, db: AsyncSession = Depends(get_db)):
    """Connect Garmin: email + password once. {mfa_required: true} when the account asks for a
    code (then POST /garmin/mfa). remember=true stores the password sealed for one automatic
    re-login; false deletes a stored one. Never returns the token / password."""
    from backend import membership as MS
    from backend.sync import garmin_client as GC, garmin_limits as GL
    if not MS.role_allows("sync:garmin_trial"):
        raise MS.scope_error("sync:garmin_trial")
    await _garmin_consent(db, body.athlete_id, body.consent_version)
    try:
        GL.check_allowed(scheduled=False)
        return await GC.login(body.email, body.password, db, body.athlete_id, remember=body.remember)
    except Exception as e:                       # noqa: BLE001 — mapped to plain codes
        raise _garmin_error(e)


@router.post("/garmin/mfa")
async def garmin_mfa(body: GarminMfaRequest, db: AsyncSession = Depends(get_db)):
    """The MFA code of the login waiting for it (step 2). 401 GARMIN_MFA_WRONG: type it again;
    GARMIN_MFA_EXPIRED: log in again."""
    from backend.sync import garmin_client as GC
    try:
        return await GC.login_mfa(body.code, db, body.athlete_id)
    except Exception as e:                       # noqa: BLE001
        raise _garmin_error(e)


@router.get("/garmin/status")
async def garmin_auth_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Connected / 登入已過期 / not connected, the remembered password (yes / no only), a login
    waiting for its MFA code, and the server's Garmin state. No Garmin request is made."""
    from sqlalchemy import select
    from backend.db.models import SyncState
    from backend.sync import garmin_client as GC, garmin_limits as GL
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
    saved = bool(st and st.garmin_password_sealed)               # never the value
    out = {"password_saved": saved, "mfa_pending": GC.mfa_pending(), "site": GL.status()}
    if not st or not st.garmin_token_sealed:
        return {**out, "authenticated": False, "expired": False, "status": "logged_out", "email": None}
    verdict = await session_check.check(db, "garmin", athlete_id)
    return {**out, **_session_fields(verdict), "email": st.garmin_email,
            "last_sync": st.garmin_last_sync_at.isoformat() if st.garmin_last_sync_at else None,
            "auto_relogin": saved}


@router.post("/garmin/logout")
async def garmin_logout(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """中斷連接: deletes the token and the remembered password; the imported activities stay."""
    from backend.sync import garmin_client as GC
    await GC.logout(db, athlete_id)
    return {"logged_out": True}


# ---------------------------------------------------------------------------- intervals.icu (SP-346)

@router.get("/intervals/status")
async def intervals_auth_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Not open yet (no OAuth app on this server) / not connected / connected / 請重新連接, the last
    sync, the dormancy reminder (icu_last_seen ≥ 83 days), the back-fill's progress and the queued
    webhook activities. No intervals.icu request; never the token or the account's id."""
    from backend import membership as MS
    from backend.sync import intervals_client as IC
    out = await IC.status(db, athlete_id)
    out["allowed"] = MS.has(MS.SYNC_SOURCE_SCOPE["intervals"])
    out["site_off"] = MS.SYNC_SOURCE_SCOPE["intervals"] in MS.sources_off()
    return out


@router.post("/intervals/connect")
async def intervals_connect(request: Request, athlete_id: int = 1):
    """The intervals.icu authorize URL the page sends the browser to (scope ACTIVITY:READ; a one-time
    state bound to this account, 10 minutes). 409 INTERVALS_NOT_CONFIGURED before the server has its
    OAuth app; 403 when this plan / the server has the source off."""
    from backend import membership as MS
    from backend.sync import intervals_client as IC
    if not IC.configured():
        raise HTTPException(409, {"code": "INTERVALS_NOT_CONFIGURED", "message": _("intervals.icu 同步還沒開放")})
    if not MS.has(MS.SYNC_SOURCE_SCOPE["intervals"]):
        raise HTTPException(403, {"code": "INTERVALS_NOT_ALLOWED", "message": _("這個帳號目前不能用 intervals.icu 同步")})
    origin = f"{request.url.scheme}://{request.url.netloc}"
    return {"url": IC.authorize_url(athlete_id, origin)}


@router.post("/intervals/logout")
async def intervals_logout(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """中斷連接: DELETE /api/v1/disconnect-app at intervals.icu (stops its webhooks), then the token is
    deleted whatever it answered; the imported activities stay. {remote: ok | failed | none}."""
    from backend.sync import intervals_client as IC, runner
    if runner.is_busy("intervals"):
        raise HTTPException(409, "SYNC_BUSY")
    return await IC.disconnect(db, athlete_id)


def _session_fields(verdict: str) -> dict:
    """status: logged_in | expired (登入已過期) | logged_out. check: the
    login check's answer (sync/session_check.py); "unknown" = COROS / TP not
    reachable, the login is still shown as 已登入."""
    expired = verdict == session_check.EXPIRED
    return {"authenticated": not expired, "expired": expired,
            "status": "expired" if expired else "logged_in", "check": verdict}


@router.get("/coros/status")
async def coros_auth_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The stored token is validated (one cheap COROS call, cached
    session_check.CHECK_TTL_S); an expired one is renewed with a remembered
    password, else status = expired. Never returns the token / password."""
    from sqlalchemy import select
    from backend.db.models import SyncState
    state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_result.scalar_one_or_none()
    saved = bool(state and state.coros_password_sealed)          # never the value
    if not state or not state.coros_access_token:
        return {"authenticated": False, "expired": False, "status": "logged_out", "email": None,
                "password_saved": saved}
    verdict = await session_check.check(db, "coros", athlete_id)
    await db.refresh(state)                     # an automatic re-login may have renewed it
    return {
        **_session_fields(verdict),
        "email": state.coros_email,
        "token_expires": state.coros_token_expires.isoformat() if state.coros_token_expires else None,
        "last_sync": state.coros_last_sync_at.isoformat() if state.coros_last_sync_at else None,
        "password_saved": saved,
        # an expired token is renewed on the next sync / push when a password is remembered
        "auto_relogin": saved,
    }


@router.get("/session-alerts")
async def session_alerts(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The overview / schedule banner (static/session_banner.js).

    expired: the logins in use (資料來源, 課表推送) that have expired. Uses
    the same cached check as the status endpoints.

    sync: can the 資料來源 sync (SP-88)? problem = expired (登入已過期) |
    logged_out (no login any more, after it synced before) | failed (the last
    run failed and nothing has fixed it since) | None, with the last
    successful sync time and the last run. A sync switched off is no problem."""
    from backend.settings.repository import SettingsRepository
    from backend.sync import primary as P, runner
    db_name = await P.current(db, athlete_id)
    use = P.FOLDER[db_name]
    repo = SettingsRepository(db, athlete_id)
    push = await repo.get("plan.push.provider")
    names = {"coros": "COROS", "garmin": "Garmin", "tp": "TrainingPeaks", "intervals": "intervals.icu"}
    out, verdicts = [], {}
    for src in session_check.SOURCES:
        needs = ([_("同步")] if src == use else []) + ([_("推送")] if src == "coros" and push == "coros" else [])
        if not needs:
            continue
        verdicts[src] = await session_check.check(db, src, athlete_id)
        if verdicts[src] == session_check.EXPIRED:
            out.append({"source": src, "name": names[src],
                        "needs": ["sync" if n == _("同步") else "push" for n in needs],
                        "message": _("{name} 登入已過期，重新登入後才能{what}", name=names[src], what='／'.join(needs))})

    login = verdicts[use]
    enabled = bool(await repo.get(f"sync.{db_name}.enabled"))
    last_ok = await runner.last_sync_at(db, use, athlete_id)
    run = await repo.get(f"sync.{db_name}.last_result")
    run = run if isinstance(run, dict) else None
    problem = None
    if enabled:
        if login == session_check.EXPIRED:
            problem = "expired"
        elif login == session_check.LOGGED_OUT:
            # only when it synced before: an install that never logged in is not 「同步失敗」
            problem = "logged_out" if (last_ok or run) else None
        elif run and run.get("status") == "failed" and not (
                str(run.get("error") or "").endswith("AUTH_REQUIRED") and login == session_check.OK):
            # an auth failure is over once the login checks ok (logged in again since)
            problem = "failed"
    # intervals.icu (SP-346): a free account unseen there for 90 days stops receiving activities;
    # from day 83 the banner asks the person to open intervals.icu once
    dormant = None
    if use == "intervals" and enabled and login == session_check.OK:
        from backend.sync import intervals_client as IC
        d = IC.dormancy(await repo.get("sync.intervals.seen"))
        if d and d["remind"]:
            dormant = {"days": d["days"], "days_left": d["days_left"], "dormant": d["dormant"], "url": IC.BASE}
    return {"expired": out, "settings_url": "/api/v1/app/settings#sync", "intervals_dormancy": dormant,
            "sync": {"source": use, "name": names[use], "enabled": enabled, "login": login,
                     "problem": problem,
                     "last_ok_at": last_ok.isoformat() if last_ok else None,
                     "last_run": {k: run.get(k) for k in ("at", "trigger", "status", "error")} if run else None}}


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
    session_check.forget("coros", athlete_id)
    return {"logged_out": True}


class RememberBody(BaseModel):
    remember: bool
    athlete_id: int = 1


@router.put("/{source}/remember")
async def set_remember(source: str, body: RememberBody, db: AsyncSession = Depends(get_db)):
    """Unticking 「記住密碼」 deletes the stored password immediately. Ticking
    it alone stores nothing (the password is only sent with a login)."""
    if source not in ("coros", "garmin", "tp"):
        raise HTTPException(400, "source must be coros, garmin or tp")
    if not body.remember:
        if source == "coros":
            await coros_client.save_password(db, body.athlete_id, None)
        elif source == "garmin":
            from backend.sync import garmin_client
            await garmin_client.save_password(db, body.athlete_id, None)
        else:
            from backend.sync.tp_client import save_password
            await save_password(db, body.athlete_id, None, None)
    from sqlalchemy import select
    from backend.db.models import SyncState
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == body.athlete_id))).scalar_one_or_none()
    saved = bool(st and {"coros": st.coros_password_sealed, "garmin": st.garmin_password_sealed}.get(
        source, st.tp_password_sealed if st else None))
    return {"password_saved": saved}
