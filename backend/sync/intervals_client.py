"""
intervals.icu sync (SP-346; docs/research/sync-platforms.md §3.1, §4.1, §4.8, §4.10): activities and
their ORIGINAL files only — no workouts are pushed (sync/workout_targets has no intervals.icu
provider). intervals.icu is the go-between for every watch it connects to (Garmin, COROS, Suunto,
Polar, Wahoo, Amazfit, Huawei; Apple Watch through a phone app).

Connect (OAuth, intervals.icu forum topic 2759): the settings page asks POST
/api/v1/auth/intervals/connect for the authorize URL (`authorize_url`: client_id, redirect_uri,
scope ACTIVITY:READ, a one-time `state` bound to this tenant for STATE_TTL_S, memory only); the person
approves on intervals.icu, which sends the browser to CALLBACK_PATH with a code (or
error=access_denied); `finish_connect` exchanges it "within 2 minutes" (POST TOKEN_URL, client id and
secret) for an access token and the athlete's id. There is no refresh token and the token does not
expire; it is sealed with the server key (settings/secrets.py) in sync_state.intervals_token_sealed,
never returned, never logged. The athlete id goes into the webhook map (intervals_limits.register).
中斷連接 (`disconnect`) calls DELETE /api/v1/disconnect-app ("will stop webhook delivery for the
athlete") and deletes the token whatever it answers; the imported activities stay. A 401 / 403 later
means the person removed the app on intervals.icu: intervals_token_refused, 「請重新連接」.

The app's client id / secret / webhook secret come from the environment only
(TRC_INTERVALS_CLIENT_ID, TRC_INTERVALS_CLIENT_SECRET, TRC_INTERVALS_WEBHOOK_SECRET, or their _FILE;
settings/env_secrets.py). Without the id and secret the option says 「還沒開放」 (`configured`).

Sync (`sync_workouts`, through sync/runner.py like the other sources):
  1. the athlete (GET /athlete/0): its icu_last_seen. A free account unseen on intervals.icu for 90
     days goes DORMANT and its new files are not processed (topic 112826); the page reminds on day
     REMIND_DAYS (`dormancy`). An API call is not known to count as being seen (topic 130858: a coach
     pulling through the API daily still saw his athletes go dormant), so nothing here tries to
     "keep the account awake"; OAuth never gives us the password, and we never log in for anyone
  2. the recent activities: the list from the cursor minus CURSOR_OVERLAP_DAYS (first sync:
     RECENT_DAYS) to today, one WINDOW_DAYS window per request (`oldest` / `newest`, the author's
     advice); each new one is downloaded (GET /activity/{id}/file — "the original activity file (fit,
     gpx or tcx) gzip compressed"), gunzipped, stored as the hashed FIT object fit/intervals/objects/…
     (SP-308) and imported; dedup.py makes it one activity with a COROS / Garmin / TP row of the same
     run (or the same bytes). This is also the daily poll that catches what a webhook missed
     (intervals.icu "will retry 8 times with exponential backoff", then gives up)
  3. what the webhook queued (`pending`) and the failed list's due rows (sync/failures.py)
  4. the first back-fill (setting sync.intervals.backfill): older windows, newest first, down to
     FIRST_SYNC_DAY, within the back-fill budget (intervals_limits: a share of the app's day and
     BACKFILL_PER_RUN a run); stopped there with `more`, it goes on at the next sync
Per activity: Strava's are skipped without an error (intervals.icu only has a stub: "activity
webhooks are not delivered for Strava activities" and the API returns no data for them); a GPX / TCX
original is no_file (only FIT is imported); Huawei's file is not the watch's original ("The Huawei
API does not support fit file export.") — stored but marked (workout_files.upstream_source HUAWEI,
garmin_attribution.mark), and ranked below an original FIT by dedup; a Garmin device's activity
is attributed to Garmin (garmin_attribution).

Webhook (api/intervals.py): ACTIVITY_UPLOADED / ACTIVITY_ANALYZED events, the payload's secret
checked; the activity is queued (`add_pending`) in the tenant the athlete id maps to, and a run
drains it (`drain_pending`). Every request goes through sync/http.client (tests: MockTransport,
never the network) and intervals_limits.request (the app-wide day, 15 minutes, 10 / s).
"""
from __future__ import annotations

import asyncio
import io
import logging
import secrets as _secrets
import time
import zipfile
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import SyncState, WorkoutFile
from backend.i18n import N_, _
from backend.settings.secrets import SecretError, seal, unseal
from backend.sync import events as EV
from backend.sync import failures as FL
from backend.sync import intervals_limits as IL
from backend.sync import session_check
from backend.sync.http import as_utc

log = logging.getLogger(__name__)

SOURCE = "intervals"
BASE = "https://intervals.icu"
API = BASE + "/api/v1"
AUTHORIZE_URL = BASE + "/oauth/authorize"
TOKEN_URL = BASE + "/api/oauth/token"
DISCONNECT_URL = API + "/disconnect-app"
SCOPE = "ACTIVITY:READ"
CALLBACK_PATH = "/oauth/intervals/callback"
ENV_CLIENT_ID = "TRC_INTERVALS_CLIENT_ID"
ENV_CLIENT_SECRET = "TRC_INTERVALS_CLIENT_SECRET"
ENV_WEBHOOK_SECRET = "TRC_INTERVALS_WEBHOOK_SECRET"
USER_AGENT = "TrailRunCoach (intervals.icu OAuth app)"
TIMEOUT_S = 30.0

FIRST_SYNC_DAY = date(2020, 1, 1)          # as COROS / Garmin
RECENT_DAYS = 30                           # the first sync's recent part (the rest is the back-fill)
WINDOW_DAYS = 31                           # one list request (the author: 1–3 months a request)
CURSOR_OVERLAP_DAYS = 14                   # as COROS / Garmin (SP-362)
STATE_TTL_S = 600
PENDING_MAX = 500
DORMANT_DAYS = 90
REMIND_DAYS = 83
MAX_FIT_BYTES = 64 * 1024 * 1024
WEBHOOK_TYPES = ("ACTIVITY_UPLOADED", "ACTIVITY_ANALYZED")

BACKFILL_MORE = N_("較早的活動排隊補抓中：今天的 intervals.icu 額度先留給新活動，下次同步會自動接著補")


class IntervalsNotConfigured(RuntimeError):
    """The server has no intervals.icu OAuth app (client id / secret) yet."""


class IntervalsAuthRequired(ValueError):
    """Not connected, or intervals.icu refused the token (the app was removed there)."""


class IntervalsConnectFailed(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------- the app's settings

def _env(name: str) -> Optional[str]:
    from backend.settings.env_secrets import secret_env
    return secret_env(name)


def client_id() -> Optional[str]:
    return _env(ENV_CLIENT_ID)


def configured() -> bool:
    """The OAuth app's id and secret are set (the app is approved and its keys deployed)."""
    return bool(_env(ENV_CLIENT_ID) and _env(ENV_CLIENT_SECRET))


def webhook_secret() -> Optional[str]:
    return _env(ENV_WEBHOOK_SECRET)


def redirect_uri(request_origin: str) -> str:
    """<public URL>/oauth/intervals/callback: TRC_PUBLIC_URL (the address registered with the app)
    when set, else the address the page was opened at."""
    import os
    from backend.accounts.google import ENV_PUBLIC_URL
    origin = (os.environ.get(ENV_PUBLIC_URL) or "").strip().rstrip("/") or request_origin.rstrip("/")
    return origin + CALLBACK_PATH


def _tid() -> str:
    from backend import tenancy
    return tenancy.current().id


# ---------------------------------------------------------------------------- OAuth

_STATES: dict = {}                 # state -> {tenant, athlete_id, redirect_uri, until}


def authorize_url(athlete_id: int, request_origin: str) -> str:
    """Where the browser goes to approve; the state is bound to this tenant, used once."""
    if not configured():
        raise IntervalsNotConfigured("INTERVALS_NOT_CONFIGURED")
    now = time.monotonic()
    for k in [k for k, v in _STATES.items() if v["until"] < now]:
        _STATES.pop(k, None)
    state = _secrets.token_urlsafe(24)
    uri = redirect_uri(request_origin)
    _STATES[state] = {"tenant": _tid(), "athlete_id": athlete_id, "redirect_uri": uri, "until": now + STATE_TTL_S}
    return AUTHORIZE_URL + "?" + urlencode({"client_id": client_id(), "redirect_uri": uri, "scope": SCOPE,
                                            "state": state, "response_type": "code"})


def pop_state(state: Optional[str]) -> Optional[dict]:
    """The connect this state started (once; None when unknown or expired)."""
    if not state:
        return None
    row = _STATES.pop(state, None)
    if row is None or row["until"] < time.monotonic():
        return None
    return row


async def _req(method: str, url: str, token: Optional[str] = None, backfill: bool = False, **kw):
    """One intervals.icu request through the app-wide limits. 401 / 403 -> IntervalsAuthRequired;
    429 -> IntervalsBudget (we stop; no retry loop)."""
    from backend.sync import http
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **kw.pop("headers", {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with IL.request(backfill=backfill):
        async with http.client(timeout=TIMEOUT_S) as c:
            r = await c.request(method, url, headers=headers, **kw)
    if r.status_code in (401, 403):
        raise IntervalsAuthRequired(f"HTTP {r.status_code}")
    if r.status_code == 429:
        EV.log(SOURCE, "rate_limited", tenant=_tid())
        raise IL.IntervalsBudget("INTERVALS_RATE_LIMITED")
    return r


async def _state(db: AsyncSession, athlete_id: int) -> Optional[SyncState]:
    return (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()


async def finish_connect(db: AsyncSession, athlete_id: int, code: str, uri: str) -> dict:
    """The code from the callback -> the token (sealed) and the athlete id. IntervalsConnectFailed
    with a short code when intervals.icu refuses it; nothing personal is logged."""
    if not configured():
        raise IntervalsNotConfigured("INTERVALS_NOT_CONFIGURED")
    try:
        r = await _req("POST", TOKEN_URL, data={"client_id": client_id(), "client_secret": _env(ENV_CLIENT_SECRET),
                                                "code": code, "redirect_uri": uri})
    except IntervalsAuthRequired:
        raise IntervalsConnectFailed("INTERVALS_CODE_REFUSED") from None
    except IL.IntervalsBudget as e:
        raise IntervalsConnectFailed(e.code) from None
    except Exception as e:                          # noqa: BLE001 — network
        log.warning("intervals.icu token exchange failed: %s", type(e).__name__)
        raise IntervalsConnectFailed("INTERVALS_UNREACHABLE") from None
    if r.status_code >= 400:
        raise IntervalsConnectFailed("INTERVALS_CODE_REFUSED")
    try:
        body = r.json()
    except ValueError:
        body = {}
    token = body.get("access_token") if isinstance(body, dict) else None
    icu_id = str(((body.get("athlete") or {}) if isinstance(body, dict) else {}).get("id") or "").strip()
    if not token or not icu_id:
        raise IntervalsConnectFailed("INTERVALS_BAD_ANSWER")
    st = await _state(db, athlete_id)
    if st is None:
        st = SyncState(athlete_id=athlete_id)
        db.add(st)
    same = st.intervals_athlete_id == icu_id
    st.intervals_token_sealed = seal(token)          # SecretError (SECRET_KEY_MISSING) propagates
    st.intervals_token_refused = None
    st.intervals_athlete_id = icu_id
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    if not same or not await repo.get("sync.intervals.backfill"):
        # a new account: its back-fill starts over (newest month first)
        await repo.set("sync.intervals.backfill", new_backfill(_today()))
    await db.commit()
    IL.register(icu_id, _tid())
    session_check.mark_ok(SOURCE, athlete_id)
    EV.log(SOURCE, "connected", tenant=_tid(), scope=str(body.get("scope") or "")[:80])
    log.info("intervals.icu connected")
    return {"authenticated": True, "scope": body.get("scope")}


async def _revoke_token(token: str) -> bool:
    """DELETE /api/v1/disconnect-app (stops the webhooks for the athlete). True on a 2xx."""
    try:
        r = await _req("DELETE", DISCONNECT_URL, token=token)
    except IntervalsAuthRequired:
        return True                                  # already removed on intervals.icu
    except Exception as e:                           # noqa: BLE001
        log.warning("intervals.icu disconnect-app failed: %s", type(e).__name__)
        return False
    return 200 <= r.status_code < 300


async def disconnect(db: AsyncSession, athlete_id: int = 1) -> dict:
    """中斷連接: disconnect-app at intervals.icu, then the token, the athlete id, the queued webhook
    activities and the webhook map entry go; the imported activities stay. {remote: ok | failed |
    none}: the local deletion happens whatever intervals.icu answered."""
    st = await _state(db, athlete_id)
    remote = "none"
    if st is not None and st.intervals_token_sealed:
        try:
            token = unseal(st.intervals_token_sealed)
        except SecretError:
            token = None
        if token:
            remote = "ok" if await _revoke_token(token) else "failed"
    if st is not None:
        st.intervals_token_sealed = None
        st.intervals_token_refused = None
        st.intervals_athlete_id = None
        from backend.settings.repository import SettingsRepository
        await SettingsRepository(db, athlete_id).set("sync.intervals.pending", None)
        await db.commit()
    IL.unregister(_tid())
    session_check.forget(SOURCE, athlete_id)
    EV.log(SOURCE, "disconnected", tenant=_tid(), remote=remote)
    return {"logged_out": True, "remote": remote}


async def revoke(source: str, creds: dict) -> None:
    """刪除帳號 (accounts/deletion.REVOKE): disconnect-app with the stored token."""
    sealed = creds.get("intervals_token_sealed")
    token = unseal(sealed) if sealed else None
    if token and not await _revoke_token(token):
        raise RuntimeError("intervals.icu disconnect-app failed")
    IL.unregister(_tid())


async def connected(db: AsyncSession, athlete_id: int = 1) -> bool:
    """A token is stored and was not refused (no request)."""
    st = await _state(db, athlete_id)
    return bool(st and st.intervals_token_sealed and st.intervals_token_refused is None)


async def _session(db: AsyncSession, athlete_id: int) -> tuple[str, SyncState]:
    if not configured():
        raise IntervalsNotConfigured("INTERVALS_NOT_CONFIGURED")
    st = await _state(db, athlete_id)
    if st is None or not st.intervals_token_sealed:
        raise IntervalsAuthRequired("not connected")
    if st.intervals_token_refused is not None:
        raise IntervalsAuthRequired("token refused")
    try:
        return unseal(st.intervals_token_sealed), st
    except SecretError as e:
        raise IntervalsAuthRequired(str(e))


async def _token_refused(db: AsyncSession, st: SyncState, athlete_id: int) -> None:
    st.intervals_token_refused = datetime.now(timezone.utc)
    await db.commit()
    session_check.mark_expired(SOURCE, athlete_id)
    EV.log(SOURCE, "token_refused", tenant=_tid())


# ---------------------------------------------------------------------------- dormancy

def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_dt(v) -> Optional[datetime]:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def dormancy(seen: Optional[dict], now: Optional[datetime] = None) -> Optional[dict]:
    """From sync.intervals.seen: {last_seen, days (since it), remind (≥ REMIND_DAYS), dormant
    (≥ DORMANT_DAYS), days_left}; None before the first read."""
    last = _parse_dt((seen or {}).get("last_seen"))
    if last is None:
        return None
    now = now or datetime.now(timezone.utc)
    days = max(0, (now - last).days)
    return {"last_seen": last.isoformat(), "checked_at": (seen or {}).get("at"), "days": days,
            "remind": days >= REMIND_DAYS, "dormant": days >= DORMANT_DAYS,
            "days_left": max(0, DORMANT_DAYS - days)}


async def _refresh_seen(db: AsyncSession, athlete_id: int, token: str) -> Optional[dict]:
    """GET /athlete/0 -> icu_last_seen into sync.intervals.seen (one request per sync run)."""
    r = await _req("GET", API + "/athlete/0", token=token)
    if r.status_code >= 400:
        return None
    try:
        a = r.json()
    except ValueError:
        return None
    a = a.get("athlete", a) if isinstance(a, dict) else {}
    last = a.get("icu_last_seen") if isinstance(a, dict) else None
    if not isinstance(last, str):
        return None
    from backend.settings.repository import SettingsRepository
    value = {"last_seen": last, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    await SettingsRepository(db, athlete_id).set("sync.intervals.seen", value)
    await db.commit()
    d = dormancy(value)
    if d and d["remind"]:
        EV.log(SOURCE, "dormancy_reminder", tenant=_tid(), days=d["days"])
    return value


# ---------------------------------------------------------------------------- the original file

class NotFit(ValueError):
    """The original file is a GPX / TCX (or nothing we can read): only FIT is imported."""


def _inflate(data: bytes, wbits: int) -> bytes:
    d = zlib.decompressobj(wbits)
    out = d.decompress(data, MAX_FIT_BYTES + 1)
    if len(out) > MAX_FIT_BYTES or d.unconsumed_tail:
        raise NotFit("too big")
    return out


def decode_file(data: bytes) -> bytes:
    """GET /activity/{id}/file's answer -> the FIT bytes: gzip (the documented form), a ZIP or a bare
    FIT; NotFit for anything else (GPX / TCX) or a file over MAX_FIT_BYTES."""
    if not data:
        raise NotFit("empty")
    try:
        if data[:2] == b"\x1f\x8b":
            data = _inflate(data, 16 + zlib.MAX_WBITS)
    except zlib.error:
        raise NotFit("broken gzip") from None
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                fits = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".fit")]
                if not fits or fits[0].file_size > MAX_FIT_BYTES:
                    raise NotFit("no .fit in the ZIP")
                data = zf.read(fits[0])
        except (zipfile.BadZipFile, ValueError, zlib.error):
            raise NotFit("broken ZIP") from None
    if len(data) >= 12 and data[8:12] == b".FIT":
        return data
    raise NotFit("not FIT")


# ---------------------------------------------------------------------------- one activity

def _act_date(act: dict) -> Optional[date]:
    for k in ("start_date_local", "start_date"):
        v = act.get(k)
        if isinstance(v, str) and len(v) >= 10:
            try:
                return date.fromisoformat(v[:10])
            except ValueError:
                continue
    return None


def _short(v, n: int) -> Optional[str]:
    s = " ".join(str(v or "").split())[:n]
    return s or None


def slim(act: dict) -> dict:
    """The fields of an activity kept for later (the webhook queue): no names, no notes."""
    return {"id": str(act.get("id")), "source": _short(act.get("source"), 20),
            "device_name": _short(act.get("device_name"), 100), "start_date_local": _short(act.get("start_date_local"), 25),
            "type": _short(act.get("type"), 30), "file_type": _short(act.get("file_type"), 10)}


async def _known(db: AsyncSession, athlete_id: int, aid: str) -> bool:
    return (await db.execute(select(WorkoutFile.id).where(
        WorkoutFile.athlete_id == athlete_id, WorkoutFile.intervals_activity_id == aid))).scalars().first() is not None


async def _remember(db: AsyncSession, run: dict, coro) -> None:
    try:
        await coro
    except Exception as e:                           # noqa: BLE001
        log.warning("intervals.icu failed list not written: %s", type(e).__name__)
        run["hold"] = True
        try:
            await db.rollback()
        except Exception:                            # noqa: BLE001
            pass


async def _process(db: AsyncSession, athlete_id: int, token: str, act: dict, run: dict,
                   backfill: bool = False) -> AsyncIterator[dict]:
    """One listed / announced activity: skip it (known, Strava), or download its original and import
    it. A failure goes to the failed list; a limit stops the run (run["stop"])."""
    aid = str(act.get("id") or "").strip()
    if not aid:
        return
    act_date = _act_date(act)
    day = act_date.isoformat() if act_date else None
    run["checked"] += 1
    yield {"status": "checking", "activity_id": aid, "date": day}
    if await _known(db, athlete_id, aid):
        yield {"status": "skipped", "activity_id": aid, "reason": "already_imported"}
        return
    src = str(act.get("source") or "").upper()
    if src == "STRAVA":
        # intervals.icu holds only a stub of a Strava activity: nothing to download, not an error
        yield {"status": "skipped", "activity_id": aid, "reason": "strava", "date": day}
        return
    prev = await FL.get(db, athlete_id, SOURCE, aid)
    if prev is not None and not FL.auto_retry(prev):
        yield {"status": "skipped", "activity_id": aid,
               "reason": "no_file" if prev.kind == FL.NO_FILE else "retry_stopped"}
        return
    ftype = str(act.get("file_type") or "").lower()
    if ftype and ftype not in ("fit", "fit.gz"):
        await _remember(db, run, FL.record_no_file(db, athlete_id, SOURCE, aid, None, act_date))
        yield {"status": "no_file", "activity_id": aid, "date": day, "reason": "not_fit"}
        return
    t_dl = time.monotonic()
    try:
        r = await _req("GET", f"{API}/activity/{aid}/file", token=token, backfill=backfill)
    except (IntervalsAuthRequired, IL.IntervalsBudget) as e:
        run["stop"] = e
        return
    except Exception as e:                           # noqa: BLE001 — network
        run["errors"].append(f"{aid}: {type(e).__name__}")
        await _remember(db, run, FL.record_failure(db, athlete_id, SOURCE, aid, type(e).__name__, None, act_date))
        yield {"status": "error", "activity_id": aid, "error": "INTERVALS_DOWNLOAD_FAILED"}
        return
    dl_s = time.monotonic() - t_dl
    if r.status_code == 404:
        kind = {}

        async def note():
            kind["k"] = (await FL.record_no_file_answer(db, athlete_id, SOURCE, aid, None, act_date)).kind
        await _remember(db, run, note())
        if kind.get("k") == FL.NO_FILE:
            yield {"status": "no_file", "activity_id": aid, "date": day}
        else:
            run["errors"].append(f"{aid}: no file")
            yield {"status": "error", "activity_id": aid, "error": "INTERVALS_NO_FILE"}
        return
    if r.status_code >= 400:
        run["errors"].append(f"{aid}: HTTP {r.status_code}")
        await _remember(db, run, FL.record_failure(db, athlete_id, SOURCE, aid, f"HTTP {r.status_code}", None, act_date))
        yield {"status": "error", "activity_id": aid, "error": "INTERVALS_DOWNLOAD_FAILED"}
        return
    try:
        fit = decode_file(r.content)
    except NotFit as e:
        await _remember(db, run, FL.record_no_file(db, athlete_id, SOURCE, aid, None, act_date))
        yield {"status": "no_file", "activity_id": aid, "date": day, "reason": "not_fit", "detail": str(e)}
        return
    async for ev in _import(db, athlete_id, aid, act, act_date, fit, run, dl_s):
        yield ev


async def _import(db: AsyncSession, athlete_id: int, aid: str, act: dict, act_date: Optional[date], fit: bytes,
                  run: dict, dl_s: float) -> AsyncIterator[dict]:
    from backend.files import fitstore
    from backend.files.file_service import _import_one_file, record_corrupt
    from backend.sync import dedup
    day = act_date.isoformat() if act_date else None
    upstream, device = _short(act.get("source"), 20), _short(act.get("device_name"), 100)
    stored = await fitstore.store_fit(db, fit, SOURCE)
    key = stored.key
    live = await fitstore.live_row(db, key)
    if live is not None:
        # the same bytes are already an activity (another source, or this one): stored once,
        # recorded once; the id is noted so the next listing does not download it again
        if live.intervals_activity_id is None:
            live.intervals_activity_id = aid
        await db.commit()
        await _remember(db, run, FL.resolve(db, athlete_id, SOURCE, aid))
        yield {"status": "skipped", "activity_id": aid, "reason": "same_content", "key": key}
        return
    try:
        wf = await _import_one_file(db, athlete_id, key, source=SOURCE)
        if wf is None:
            await db.rollback()
            await fitstore.store_fit(db, fit, SOURCE)
            stub = await record_corrupt(db, athlete_id, key, source=SOURCE, workout_date=act_date)
            stub.intervals_activity_id = aid
            await db.commit()
            await _remember(db, run, FL.resolve(db, athlete_id, SOURCE, aid))
            run["errors"].append(f"{aid}: corrupt_fit")
            yield {"status": "error", "activity_id": aid, "error": "corrupt_fit"}
            return
        wf.intervals_activity_id = aid
        wf.upstream_source = upstream
        wf.device_name = device
        if (upstream or "").upper() == "HUAWEI":
            await dedup.resolve(db, wf)              # not an original: an original FIT of it counts first
        await db.commit()
        run["downloaded"] += 1
        await _remember(db, run, FL.resolve(db, athlete_id, SOURCE, aid))
        yield {"status": "downloaded", "activity_id": aid, "key": key, "date": day,
               "not_original": (upstream or "").upper() == "HUAWEI", "secs": {"download": round(dl_s, 3)}}
    except Exception as e:                           # noqa: BLE001
        await db.rollback()
        log.warning("intervals.icu import failed for %s: %s", aid, type(e).__name__)
        run["errors"].append(f"{aid}: import_failed")
        await _remember(db, run, FL.record_failure(db, athlete_id, SOURCE, aid, f"import_failed: {type(e).__name__}",
                                                   None, act_date))
        yield {"status": "error", "activity_id": aid, "error": "import_failed"}


# ---------------------------------------------------------------------------- listing

def windows(oldest: date, newest: date, size: int = WINDOW_DAYS) -> list[tuple[date, date]]:
    """[oldest, newest] (inclusive) in windows of `size` days, newest first."""
    out, hi = [], newest
    while hi >= oldest:
        lo = max(oldest, hi - timedelta(days=size - 1))
        out.append((lo, hi))
        hi = lo - timedelta(days=1)
    return out


async def _list(token: str, lo: date, hi: date, backfill: bool = False) -> list:
    r = await _req("GET", API + "/athlete/0/activities", token=token, backfill=backfill,
                   params={"oldest": lo.isoformat(), "newest": hi.isoformat()})
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    try:
        acts = r.json()
    except ValueError:
        raise RuntimeError("not JSON") from None
    acts = [a for a in (acts if isinstance(acts, list) else []) if isinstance(a, dict) and a.get("id") is not None]
    acts.sort(key=lambda a: str(a.get("start_date_local") or ""), reverse=True)
    return acts


def new_backfill(today: date) -> dict:
    return {"next_newest": (today - timedelta(days=RECENT_DAYS + 1)).isoformat(),
            "oldest": FIRST_SYNC_DAY.isoformat(), "done": False, "windows": 0}


# ---------------------------------------------------------------------------- the webhook queue

async def add_pending(db: AsyncSession, athlete_id: int, acts: list[dict]) -> int:
    """Queue announced activities (deduplicated by id, at most PENDING_MAX, oldest dropped)."""
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    cur = [x for x in (await repo.get("sync.intervals.pending") or []) if isinstance(x, dict)]
    have = {x.get("id") for x in cur}
    added = 0
    for a in acts:
        s = slim(a)
        if s["id"] and s["id"] != "None" and s["id"] not in have:
            cur.append(s)
            have.add(s["id"])
            added += 1
    await repo.set("sync.intervals.pending", cur[-PENDING_MAX:] or None)
    await db.commit()
    return added


async def _take_pending(db: AsyncSession, athlete_id: int) -> list[dict]:
    from backend.settings.repository import SettingsRepository
    return [x for x in (await SettingsRepository(db, athlete_id).get("sync.intervals.pending") or [])
            if isinstance(x, dict) and x.get("id")]


async def _drop_pending(db: AsyncSession, athlete_id: int, ids: set) -> None:
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    left = [x for x in (await repo.get("sync.intervals.pending") or []) if isinstance(x, dict) and x.get("id") not in ids]
    await repo.set("sync.intervals.pending", left or None)
    await db.commit()


async def _drain(db: AsyncSession, athlete_id: int, token: str, run: dict) -> AsyncIterator[dict]:
    done = set()
    for act in await _take_pending(db, athlete_id):
        async for ev in _process(db, athlete_id, token, act, run):
            yield ev
        if run["stop"] is not None:
            break
        done.add(act["id"])
    if done:
        await _drop_pending(db, athlete_id, done)


# ---------------------------------------------------------------------------- the sync

def _stop_event(stop) -> dict:
    if isinstance(stop, IntervalsAuthRequired):
        return {"status": "error", "error": "INTERVALS_AUTH_REQUIRED", "detail": str(stop),
                "hint": _("請重新連接 intervals.icu")}
    return {"status": "error", "error": getattr(stop, "code", "INTERVALS_API_ERROR"), "detail": str(stop)}


async def _begin(db: AsyncSession, athlete_id: int):
    """(token, state) or the run-level error event."""
    try:
        token, st = await _session(db, athlete_id)
    except IntervalsNotConfigured:
        return None, {"status": "error", "error": "INTERVALS_NOT_CONFIGURED", "detail": _("intervals.icu 同步還沒開放")}
    except IntervalsAuthRequired as e:
        return None, {"status": "error", "error": "INTERVALS_AUTH_REQUIRED", "detail": str(e),
                      "hint": _("請重新連接 intervals.icu")}
    return (token, st), None


async def sync_workouts(db: AsyncSession, athlete_id: int = 1, since: Optional[str] = None) -> AsyncIterator[dict]:
    """The athlete's icu_last_seen, the recent activities, the webhook queue, the failed list, then
    the back-fill — progress events as the other sources'. A run-level failure keeps the cursor."""
    got, err = await _begin(db, athlete_id)
    if err:
        yield err
        return
    token, st = got
    today = _today()
    newest = today + timedelta(days=1)               # a run east of UTC already in tomorrow
    last = as_utc(st.intervals_last_sync_at)
    if since:
        recent_from = date.fromisoformat(since[:10])
    elif last:
        recent_from = last.date() - timedelta(days=CURSOR_OVERLAP_DAYS)
    else:
        recent_from = today - timedelta(days=RECENT_DAYS)
    sync_started = datetime.now(timezone.utc)
    yield {"status": "started", "since": recent_from.isoformat(), "until": today.isoformat()}
    run = {"downloaded": 0, "checked": 0, "errors": [], "hold": False, "stop": None, "more": False}
    handled: set = set()
    try:
        # 1. icu_last_seen (the dormancy reminder): best effort
        try:
            await _refresh_seen(db, athlete_id, token)
        except (IntervalsAuthRequired, IL.IntervalsBudget) as e:
            run["stop"] = e
        except Exception as e:                       # noqa: BLE001
            log.info("intervals.icu athlete not read: %s", type(e).__name__)

        # 2. the recent activities (older windows of a far `since` count as back-fill)
        bf_cut = today - timedelta(days=RECENT_DAYS)
        for lo, hi in (windows(recent_from, newest) if run["stop"] is None else []):
            bf = hi < bf_cut
            try:
                acts = await _list(token, lo, hi, backfill=bf)
            except (IntervalsAuthRequired, IL.IntervalsBudget) as e:
                run["stop"] = e
                break
            except Exception as e:                   # noqa: BLE001
                EV.log(SOURCE, "list_failed", tenant=_tid(), error=type(e).__name__)
                yield {"status": "error", "error": "INTERVALS_API_ERROR", "detail": type(e).__name__}
                return
            for act in acts:
                handled.add(str(act.get("id")))
                async for ev in _process(db, athlete_id, token, act, run, backfill=bf):
                    yield ev
                if run["stop"] is not None:
                    break
            if run["stop"] is not None:
                break

        # 3. the webhook queue and the failed list's due rows (by id)
        if run["stop"] is None:
            async for ev in _drain(db, athlete_id, token, run):
                yield ev
        if run["stop"] is None:
            try:
                retry = [r for r in await FL.due(db, athlete_id, SOURCE) if r.provider_id not in handled]
            except Exception as e:                   # noqa: BLE001
                log.warning("intervals.icu failed list not read: %s", type(e).__name__)
                retry, run["hold"] = [], True
            for row in retry:
                if await _known(db, athlete_id, row.provider_id):
                    await FL.resolve(db, athlete_id, SOURCE, row.provider_id)
                    continue
                act = {"id": row.provider_id,
                       "start_date_local": row.workout_date.isoformat() if row.workout_date else None}
                async for ev in _process(db, athlete_id, token, act, run):
                    yield ev
                if run["stop"] is not None:
                    break

        # 4. the back-fill, within its budget
        if run["stop"] is None and not since:
            async for ev in _backfill(db, athlete_id, token, run):
                yield ev
    finally:
        try:
            IL.save()
        except OSError:
            pass

    stop = run["stop"]
    if isinstance(stop, IntervalsAuthRequired):
        await _token_refused(db, st, athlete_id)
        yield _stop_event(stop)
        return
    if isinstance(stop, IL.IntervalsBudget) and stop.code != "INTERVALS_BACKFILL_LIMIT":
        # the app's day / 15 minutes (or a 429): the cursor stays, the next run goes on
        EV.log(SOURCE, "budget_used", tenant=_tid(), code=stop.code, downloaded=run["downloaded"])
        yield _stop_event(stop)
        return
    st = await _state(db, athlete_id) or st
    if not run["hold"]:
        st.intervals_last_sync_at = sync_started
        await db.commit()
    more = run["more"]
    yield {"status": "complete", "total_downloaded": run["downloaded"], "total_checked": run["checked"],
           "errors": run["errors"], "more": more, **({"detail": _(BACKFILL_MORE)} if more else {})}


async def _backfill(db: AsyncSession, athlete_id: int, token: str, run: dict) -> AsyncIterator[dict]:
    """Older windows, newest first, until FIRST_SYNC_DAY, the run's back-fill requests
    (IL.BACKFILL_PER_RUN) or the server's back-fill share of the day; the position is saved after
    every window, so a stop loses nothing."""
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    bf = await repo.get("sync.intervals.backfill")
    if not isinstance(bf, dict) or bf.get("done"):
        return
    start_used = IL.status()["backfill"]
    try:
        nn, oldest = date.fromisoformat(bf["next_newest"]), date.fromisoformat(bf["oldest"])
    except (KeyError, TypeError, ValueError):
        return
    while nn >= oldest:
        if IL.status()["backfill"] - start_used >= IL.BACKFILL_PER_RUN or IL.backfill_room() <= 0:
            run["more"] = True
            break
        lo = max(oldest, nn - timedelta(days=WINDOW_DAYS - 1))
        try:
            acts = await _list(token, lo, nn, backfill=True)
        except IL.IntervalsBudget as e:
            if e.code == "INTERVALS_BACKFILL_LIMIT":
                run["more"] = True
            else:
                run["stop"] = e
            break
        except IntervalsAuthRequired as e:
            run["stop"] = e
            break
        except Exception as e:                       # noqa: BLE001 — try this window again next time
            log.info("intervals.icu back-fill list failed: %s", type(e).__name__)
            run["more"] = True
            break
        complete = True
        for act in acts:
            async for ev in _process(db, athlete_id, token, act, run, backfill=True):
                yield ev
            if run["stop"] is not None:
                if getattr(run["stop"], "code", None) == "INTERVALS_BACKFILL_LIMIT":
                    run["stop"], run["more"] = None, True
                complete = False
                break
        if not complete:
            break
        nn = lo - timedelta(days=1)
        bf = {**bf, "next_newest": nn.isoformat(), "windows": int(bf.get("windows") or 0) + 1,
              "done": nn < oldest}
        await repo.set("sync.intervals.backfill", bf)
        await db.commit()
        yield {"status": "backfill", "until": lo.isoformat(), "done": bf["done"]}


async def drain_pending(db: AsyncSession, athlete_id: int = 1) -> AsyncIterator[dict]:
    """A webhook's run (api/intervals.py through runner.stream): only the queued activities."""
    got, err = await _begin(db, athlete_id)
    if err:
        yield err
        return
    token, st = got
    yield {"status": "started", "webhook": True}
    run = {"downloaded": 0, "checked": 0, "errors": [], "hold": False, "stop": None, "more": False}
    try:
        async for ev in _drain(db, athlete_id, token, run):
            yield ev
    finally:
        try:
            IL.save()
        except OSError:
            pass
    stop = run["stop"]
    if isinstance(stop, IntervalsAuthRequired):
        await _token_refused(db, st, athlete_id)
    if stop is not None:
        yield _stop_event(stop)
        return
    yield {"status": "complete", "total_downloaded": run["downloaded"], "total_checked": run["checked"],
           "errors": run["errors"]}


# ---------------------------------------------------------------------------- status (the page)

async def status(db: AsyncSession, athlete_id: int = 1) -> dict:
    """What the settings page shows; never the token, never the athlete id."""
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    st = await _state(db, athlete_id)
    connected = bool(st and st.intervals_token_sealed)
    refused = bool(connected and st.intervals_token_refused is not None)
    bf = await repo.get("sync.intervals.backfill")
    pending = await repo.get("sync.intervals.pending") or []
    return {"configured": configured(), "webhook": bool(webhook_secret()),
            "authenticated": connected and not refused, "expired": refused,
            "status": "expired" if refused else "logged_in" if connected else "logged_out",
            "last_sync": st.intervals_last_sync_at.isoformat() if st and st.intervals_last_sync_at else None,
            "dormancy": dormancy(await repo.get("sync.intervals.seen")) if connected else None,
            "backfill": ({k: bf.get(k) for k in ("next_newest", "oldest", "done", "windows")}
                         if isinstance(bf, dict) and connected else None),
            "pending": len(pending) if isinstance(pending, list) else 0,
            "remind_days": REMIND_DAYS, "dormant_days": DORMANT_DAYS}


_TASKS: set = set()


def start_webhook_run(athlete_id: int = 1, tries: int = 10, wait_s: float = 20.0) -> asyncio.Task:
    """Drain this tenant's queue in the background (the tenant is the caller's context): through the
    runner (busy flag, the after-import steps); a busy source is tried again `tries` times, then the
    next sync drains it."""
    async def go():
        from backend.db.database import AsyncSessionLocal
        from backend.sync import runner
        for _i in range(tries):
            last = {}
            async with AsyncSessionLocal() as db:
                async for ev in runner.stream(db, SOURCE, athlete_id, trigger="webhook", remember=False,
                                              client=lambda d: drain_pending(d, athlete_id)):
                    last = ev
            if last.get("error") != "SYNC_BUSY":
                return last
            await asyncio.sleep(wait_s)
        return {}
    t = asyncio.get_running_loop().create_task(go())
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t
