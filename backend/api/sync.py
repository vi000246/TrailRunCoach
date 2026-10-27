import json
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import SyncState
from backend.i18n import N_, UserError, _
from backend.settings.repository import SettingsRepository
from backend.sync.tp_client import sync_workouts, fetch_tp_settings
from backend.sync import coros_client, dedup, purge, runner, storage, upload


async def _disabled(db: AsyncSession, athlete_id: int, source: str) -> bool:
    return not await SettingsRepository(db, athlete_id).get(f"sync.{source}.enabled")

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("/start")
async def start_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger TrainingPeaks sync. Streams progress events via Server-Sent Events.

    Query params:
      - since: ISO date "YYYY-MM-DD" (default: last_sync_cursor or 2010-01-01)
      - page_size: TP pagination size (1..100, default 20 — matches WKO5)
    """
    return await _sse_sync(db, "tp", athlete_id, since)


async def _sse_sync(db: AsyncSession, source: str, athlete_id: int, since: Optional[str]):
    """SSE stream of one run through the shared runner (per-source lock).
    409 SYNC_BUSY when that source is already running."""
    if runner.is_busy(source):
        raise HTTPException(409, "SYNC_BUSY")
    disabled = await _disabled(db, athlete_id, runner.SETTING_NAME[source])
    if not disabled:
        from backend import cooldown
        cooldown.take(f"sync:{source}")          # SP-714: a cool-down per person and source (429 COOLDOWN)

    async def generate():
        if disabled:
            yield {"event": "sync_progress", "data": json.dumps(
                {"status": "error", "error": "SYNC_DISABLED", "source": source})}
            return
        # COROS's self-rating passes run after the stream ends (SP-362 A5): the button finishes earlier
        async for event in runner.stream(db, source, athlete_id, since=since, trigger="manual",
                                         feel_in_background=True):
            yield {"event": "sync_progress", "data": json.dumps(event)}
    return EventSourceResponse(generate())


@router.post("/coros/start")
async def start_coros_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger Coros sync. Streams progress events via Server-Sent Events."""
    return await _sse_sync(db, "coros", athlete_id, since)


@router.post("/garmin/start")
async def start_garmin_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """「立即同步」 Garmin (SP-91 trial): one of today's 3 manual runs (sync/garmin_limits.py).
    Streams progress events via Server-Sent Events, as COROS."""
    return await _sse_sync(db, "garmin", athlete_id, since)


@router.post("/intervals/start")
async def start_intervals_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """「立即同步」 intervals.icu (SP-346): the recent activities, the webhook queue, the failed list and
    the back-fill's next part (sync/intervals_client.py). Server-Sent Events, as COROS."""
    return await _sse_sync(db, "intervals", athlete_id, since)


@router.get("/sources")
async def sync_sources(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Everything the settings page shows per source: login state, enabled,
    busy, last sync / result, files on disk, DB rows, date span."""
    repo = SettingsRepository(db, athlete_id)
    out = {}
    for src in runner.SOURCES:
        name = runner.SETTING_NAME[src]
        last = await runner.last_sync_at(db, src, athlete_id)
        out[src] = {
            "logged_in": await runner.logged_in(db, src, athlete_id),
            "enabled": await repo.get(f"sync.{name}.enabled"),
            "busy": runner.is_busy(src),
            "last_sync_at": last.isoformat() if last else None,
            "last_result": await repo.get(f"sync.{name}.last_result"),
            "stats": await purge.source_stats(db, src, athlete_id),
        }
    return out


@router.get("/primary")
async def sync_primary(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The 資料來源 in use (sync/primary.py) and whether it can sync now — the
    課表 page's 抓活動 button. Cheap: no disk stats, no chart Dataset.
    logged_in follows the login check (sync/session_check.py, cached like the
    status endpoints): it used to read only the cache and said true for a token
    COROS had refused once the cached answer was 5 min old (SP-88)."""
    from backend.sync import primary as P, session_check
    use = await P.current(db, athlete_id)
    src = P.FOLDER[use]
    login = await session_check.check(db, src, athlete_id)
    return {
        "source": src,                                   # coros | garmin | tp (the /sync/<src> start URL)
        "label": P.LABELS[use],
        "logged_in": login not in (session_check.EXPIRED, session_check.LOGGED_OUT),
        "login": login,                                  # ok | expired | unknown | logged_out
        "enabled": bool(await SettingsRepository(db, athlete_id).get(f"sync.{use}.enabled")),
        "busy": runner.is_busy(src),
    }


@router.post("/auto")
async def auto_sync(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Page-open trigger: starts a background sync for every enabled,
    logged-in, idle source whose last sync is older than
    sync.auto_on_open.hours. A no-op when fresh, busy or switched off.
    Returns immediately."""
    repo = SettingsRepository(db, athlete_id)
    if not await repo.get("sync.auto_on_open.enabled"):
        return {"started": [], "skipped": {s: "auto_off" for s in runner.SOURCES}}
    from backend import membership as MS
    if not MS.has("sync:auto_realtime"):
        # the free tier syncs once a night (SP-330 / SP-321); opening a page starts nothing
        return {"started": [], "skipped": {s: "plan_nightly" for s in runner.SOURCES}}
    hours = float(await repo.get("sync.auto_on_open.hours"))
    now = datetime.now(timezone.utc)
    # only the 資料來源 in use (sync/primary.py)
    todo, skipped = await runner.auto_plan(db, athlete_id)
    started = []
    for src in todo:
        if src == "garmin":
            # SP-91: the day's one automatic Garmin run (or the account is paused until its
            # scheduled sync): opening a page starts nothing then
            from backend.sync import garmin_limits as GL
            if GL.paused():
                skipped[src] = "paused"
                continue
            if not GL.can_start("open"):
                skipped[src] = "daily_limit"
                continue
        last = await runner.last_sync_at(db, src, athlete_id)
        if last and (now - last).total_seconds() < hours * 3600:
            skipped[src] = "fresh"
            continue
        if runner.start_background(src, athlete_id, trigger="open") is None:
            skipped[src] = "busy"
        else:
            started.append(src)
    return {"started": started, "skipped": skipped, "threshold_hours": hours}


@router.get("/compare")
async def compare_sources_api(a: str = "wko5", b: str = "coros", since: Optional[date] = None):
    """Same activities from two chart data sources (wko5 | coros | tp),
    paired by start time ±2 min: per-activity duration / distance / climbing /
    NP / TSS and relative differences (flagged beyond tolerance)."""
    import asyncio
    from backend.api.wko5views import ATHLETE_DIR
    from backend.engine.wko5expr.sourcecompare import compare
    if a not in ("wko5", "coros", "tp") or b not in ("wko5", "coros", "tp") or a == b:
        raise HTTPException(400, "a and b must be two different sources of wko5, coros, tp")
    from backend.engine.wko5expr.datasource import wko5_available
    from backend.settings.paths import for_tenant
    wdir = for_tenant(ATHLETE_DIR)                  # the owner's folder only (SP-384)
    if "wko5" in (a, b) and not wko5_available(wdir):
        raise HTTPException(404, "NO_COMPARISON_FOLDER")
    return await asyncio.to_thread(compare, a, b, wdir, since)


@router.delete("/{source}/files")
async def delete_source_files(source: str, athlete_id: int = 1,
                              date_from: Optional[date] = None, date_to: Optional[date] = None,
                              db: AsyncSession = Depends(get_db)):
    """Delete one source's synced FIT files + DB rows (confined to
    ~/.wko5coach/fit/<source>/), rebuild de-dup, reset the cursor.
    source ∈ {coros, garmin, tp, intervals}; 409 SYNC_BUSY while that source syncs."""
    if source not in storage.SOURCES:
        raise HTTPException(400, "source must be coros, garmin, tp or intervals")
    try:
        return await purge.delete_source_files(db, source, athlete_id, date_from, date_to)
    except runner.SyncBusy:
        raise HTTPException(409, "SYNC_BUSY")


@router.get("/failed")
async def failed_list(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The failed list (sync/failures.py, SP-362): activities a sync could not download /
    import (state retrying | stopped) and the ones without a FIT (no_file); 設定 › 進階設定."""
    from backend.sync import failures as FL
    return {"items": await FL.listing(db, athlete_id), "max_attempts": FL.MAX_ATTEMPTS,
            "max_age_days": FL.MAX_AGE_DAYS}


@router.post("/failed/{row_id}/retry")
async def failed_retry(row_id: int, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """「重試」: the row is due again (attempts from 0), and its source syncs now in the
    background when it can (logged in, enabled, idle) — `started`; otherwise the next sync
    retries it (`queued`)."""
    from backend.sync import failures as FL
    row = await FL.reset(db, athlete_id, row_id)
    if row is None:
        raise HTTPException(404, "no such row")
    source = row.source
    ready = (await runner.ready_sources(db, athlete_id)).get(source)
    started = ready == "ready" and runner.start_background(source, athlete_id, trigger="retry") is not None
    return {"id": row_id, "source": source, "status": "started" if started else "queued"}


class CheckBody(BaseModel):
    source: Optional[str] = None                  # coros | tp; None = every source that can run now
    ids: Optional[list[str]] = None               # 補下載: only these of the last check's missing ones


@router.get("/check")
async def check_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """完整檢查 (sync/check.py, SP-362 A3 / A4), per source: running + progress, the last full
    result (missing / local_only / failed groups, 補下載 outcome), the last weekly check."""
    from backend.sync import check
    return await check.status(db, athlete_id)


@router.post("/check")
async def check_start(body: Optional[CheckBody] = None, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """「完整檢查」: a background full listing of every connected source (or `source`),
    compared with the DB by provider id. Never deletes, never moves the cursor. `started` /
    `skipped` {source: disabled | not_logged_in | busy}; 409 SYNC_BUSY when the one source
    asked for is busy."""
    from backend import cooldown
    from backend.sync import check
    want = (body.source if body else None)
    if want is not None and want not in check.REMOTES:
        raise HTTPException(400, "source must be coros or tp")
    cooldown.check("check")                       # SP-714: counted below only when a check started
    ready = await runner.ready_sources(db, athlete_id)
    started, skipped = [], {}
    # Garmin (SP-91 trial) has no full check: a listing of the whole history would use the
    # account's daily budget (check.REMOTES are COROS and TP)
    for src in ([want] if want else [s for s in runner.SOURCES if s in check.REMOTES]):
        if ready[src] != "ready":
            skipped[src] = ready[src]
        elif check.start(src, athlete_id, check.FULL) is None:
            skipped[src] = "busy"
        else:
            started.append(src)
    if want and skipped.get(want) == "busy":
        raise HTTPException(409, "SYNC_BUSY")
    if started:
        cooldown.mark("check")
    return {"started": started, "skipped": skipped}


@router.post("/check/{source}/fill")
async def check_fill(source: str, body: Optional[CheckBody] = None, athlete_id: int = 1,
                     db: AsyncSession = Depends(get_db)):
    """「補下載」: download the last 完整檢查's missing activities of `source` (or `ids` of them)
    in the background, through the sync's own fetch (a failure goes to the failed list).
    409 SYNC_BUSY / NOT_READY, 404 when there is nothing to download."""
    from backend import cooldown
    from backend.sync import check
    if source not in check.REMOTES:
        raise HTTPException(400, "source must be coros or tp")
    cd_key = f"check:fill-{source}"               # SP-714: the 完整檢查's wait, counted on its own
    cooldown.check(cd_key)
    last = await SettingsRepository(db, athlete_id).get(check.key(source, check.FULL)) or {}
    if not last.get("missing"):
        raise HTTPException(404, "NOTHING_MISSING")
    ready = (await runner.ready_sources(db, athlete_id))[source]
    if ready == "busy":
        raise HTTPException(409, "SYNC_BUSY")
    if ready != "ready":
        raise HTTPException(409, f"NOT_READY: {ready}")
    if check.start(source, athlete_id, check.FILL, ids=(body.ids if body else None)) is None:
        raise HTTPException(409, "SYNC_BUSY")
    cooldown.mark(cd_key)
    return {"source": source, "status": "started"}


@router.get("/status")
async def sync_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Legacy, TrainingPeaks only: authenticated = a TP token is stored. Not the
    資料來源's login (GET /sync/primary, /auth/coros/status): with COROS in use
    it says false (SP-88)."""
    result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = result.scalar_one_or_none()
    if not state:
        return {"source": "tp", "authenticated": False}
    return {
        "source": "tp",
        "authenticated": bool(state.tp_access_token),
        "last_sync": state.last_sync_at.isoformat() if state.last_sync_at else None,
        "cursor": state.last_sync_cursor,
    }


class SyncSettingsBody(BaseModel):
    primary_source: Optional[str] = None          # 資料來源 (the only one used): coros | garmin | trainingpeaks | intervals (sync/primary.py)
    timezone: Optional[str] = None
    coros_enabled: Optional[bool] = None
    garmin_enabled: Optional[bool] = None
    intervals_enabled: Optional[bool] = None
    trainingpeaks_enabled: Optional[bool] = None
    tp_use_wko5_client: Optional[bool] = None     # null = auto (on when creds configured)
    daily_sync_time: Optional[str] = None         # "HH:MM" or null (off)
    auto_on_open: Optional[bool] = None
    auto_on_open_hours: Optional[float] = None
    background_sync: Optional[bool] = None        # 有新活動時在背景自動同步 (sync/background.py, SP-499)
    chart_data_source: Optional[str] = None       # source (the 資料來源) | wko5
    map_basemap: Optional[str] = None             # workout map default basemap id
    map_overlays: Optional[list[str]] = None      # workout map default overlay ids
    exclude_bad_activities: Optional[bool] = None  # engine/bad_activity.py (設定 → 資料校正)
    use_power: Optional[bool] = None              # 使用功率 (wko5expr/power_use.py); off = HR only
    accept_watch_power: Optional[bool] = None     # 進階: watch-estimated power feeds the power models (engine/power_source.py)
    push_provider: Optional[str] = None           # 進階: where the plan is pushed (sync/workout_targets)
    region_override: Optional[str] = None         # 進階: tw | intl; null = auto (engine/region.py)
    primary_sport: Optional[str] = None           # 主要訓練項目: auto | trail | road (engine/primary_sport.py)


_SETTING_KEYS = {"exclude_bad_activities": "activities.exclude_bad", "primary_source": "sync.primary_source",
                 "timezone": "athlete.timezone",
                 "coros_enabled": "sync.coros.enabled", "garmin_enabled": "sync.garmin.enabled",
                 "intervals_enabled": "sync.intervals.enabled",
                 "trainingpeaks_enabled": "sync.trainingpeaks.enabled",
                 "tp_use_wko5_client": "sync.trainingpeaks.use_wko5_client",
                 "daily_sync_time": "sync.schedule.daily_time",
                 "auto_on_open": "sync.auto_on_open.enabled",
                 "auto_on_open_hours": "sync.auto_on_open.hours",
                 "background_sync": "sync.background.enabled",
                 "chart_data_source": "charts.data_source",
                 "map_basemap": "charts.map.basemap", "map_overlays": "charts.map.overlays",
                 "use_power": "charts.power.enabled", "accept_watch_power": "power.accept_watch_power",
                 "push_provider": "plan.push.provider", "region_override": "athlete.region",
                 "primary_sport": "athlete.primary_sport"}


async def _power_source() -> tuple[str, str]:
    """(stryd | watch | none, how): the 一般設定 value, else detected on the
    chart Dataset (the one every page reads anyway); ("stryd", "預設") when
    detection fails, the long-standing behaviour."""
    import asyncio
    from backend.engine import athlete_profile as AP
    from backend.engine.planning import Plan
    prof = Plan.load().profile
    if AP.profile_power_source(prof) is not None:
        return AP.effective_power_source(prof)
    try:
        from backend.api.wko5views import _dataset
        ds = await asyncio.to_thread(_dataset)
        return AP.effective_power_source(prof, ds)
    except Exception:                       # noqa: BLE001
        return "stryd", N_("預設")


async def _sync_settings(repo: SettingsRepository) -> dict:
    from backend.sync import primary as P
    from backend.sync.tp_client import lookup_client_creds
    out = {k: await repo.get(v) for k, v in _SETTING_KEYS.items()}
    # 使用功率 None = auto: from the power source (一般設定, else detected; engine/athlete_profile.py)
    out["use_power_stored"] = out["use_power"]
    out["power_source"], how = await _power_source()
    out["power_source_how"] = _(how)          # SP-748: the settings page's 「自動：功率來源 Stryd（設定）」
    from backend.engine import athlete_profile as AP
    from backend.engine import power_state as PST
    from backend.engine.planning import Plan
    # SP-950: watch power follows the 功率來源 when it is set (手錶功率 = accepted); the stored 進階 value
    # stays as it is (accept_watch_power) and only decides while no source is set
    out["accept_watch_power_effective"] = PST.accept_watch(Plan.load().profile, bool(out["accept_watch_power"]))
    if out["use_power"] is None:
        out["use_power"] = AP.use_power(out["power_source"], out["accept_watch_power_effective"])
    if out["power_source"] == "none":
        # ③ 沒有功率計: a stored 使用功率 on is ignored (kept, not deleted — choosing a meter again restores it)
        out["use_power"] = False
    out["power_meter"] = PST.has_meter(out["power_source"])
    # 資料來源: the one source in use (an old 自動 / unset value is migrated here)
    out["primary_source"] = await P.current(repo.db, repo.user_id)
    out["primary_label"] = P.LABELS[out["primary_source"]]
    out["primary_folder"] = P.FOLDER[out["primary_source"]]
    # 背景同步 (SP-499): whether this account can have it (the server's switch, the plan) and how often
    from backend.sync import background
    out["background"] = background.describe()
    # 地區 (engine/region.py): the map default follows it while none is chosen
    from backend.engine import region as RG
    out["region"], out["region_how"] = RG.region(repo.user_id)
    out["map_basemap_stored"] = out["map_basemap"]
    if out["map_basemap"] is None:
        out["map_basemap"] = RG.default_basemap(out["region"])
    # 時區 in effect (engine/localtime.py): the setting, else detected from the FITs / browser
    import datetime as _dt
    tz = await repo.timezone()
    off = _dt.datetime.now(tz).utcoffset()
    out["timezone_effective"] = getattr(tz, "key", None) or str(tz)
    out["timezone_offset_min"] = None if off is None else int(off.total_seconds() // 60)
    out["timezone_auto"] = await repo.get("athlete.timezone.auto")
    # the chart source: the 資料來源, or the WKO5 folder (cross-check); old values read as source
    out["chart_data_source_stored"] = out["chart_data_source"]
    out["chart_data_source"] = "wko5" if out["chart_data_source"] == "wko5" else "source"
    from backend.engine.wko5expr.datasource import wko5_available
    out["wko5_available"] = wko5_available()
    from backend.sync import workout_targets as WT
    out["push_provider"] = WT.resolve(out["push_provider"]).id
    out["push_providers"] = WT.available()
    # 「負荷」 steps: the TSS → COROS TL conversion in effect (engine/coros_tl.py, 推估; refit after syncs)
    from backend.engine import coros_tl as TL
    out["coros_tl"] = TL.describe(await repo.get(TL.KEY), await repo.get(TL.LOAD_KEY))
    # 「負荷」 by RPE: each level's TSS per hour in effect (engine/rpe_load.py, 推估; refit per level after syncs)
    from backend.engine import rpe_load as RL
    out["rpe_load"] = RL.describe(await repo.get(RL.KEY))
    creds, source = lookup_client_creds()
    out["tp_client_credentials_configured"] = creds is not None      # never the values
    out["tp_client_credentials_source"] = source                     # env|file|none
    from backend.sync.tp_client import TP_CLIENT_FILE
    out["tp_client_file_exists"] = TP_CLIENT_FILE.exists()           # existence only, never read here
    from backend.settings.secrets import KEY_DOC, RECOVER_WITHOUT_KEY, key_status
    out["secret_key_status"] = key_status()      # env|file|missing|none
    if out["secret_key_status"] == "missing":
        out["secret_key_hint"] = (f"SECRET_KEY_MISSING: deploy the key (chezmoi apply) or set TRC_SECRET_KEY — see "
                                  f"{KEY_DOC}; or {RECOVER_WITHOUT_KEY}")
    return out


@router.get("/settings")
async def get_sync_settings(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Primary source, per-source enable flags and the athlete time zone."""
    return await _sync_settings(SettingsRepository(db, athlete_id))


@router.put("/settings")
async def put_sync_settings(body: SyncSettingsBody, athlete_id: int = 1,
                            db: AsyncSession = Depends(get_db)):
    """Only the fields sent are changed. Switching the 資料來源 re-runs the
    de-dup so totals switch to that source immediately; the chart Dataset
    follows through datasource.current_source (that source's folder only), and
    the imported FITs (SP-323, sync/upload.py) move into that folder with it."""
    repo = SettingsRepository(db, athlete_id)
    sent = body.model_dump(exclude_unset=True)
    try:
        for k, v in sent.items():
            await repo.set(_SETTING_KEYS[k], v)
    except ValueError as e:
        raise HTTPException(400, str(e))
    moved = await upload.rehome(db, athlete_id) if "primary_source" in sent else None
    rebuilt = await dedup.rebuild(db, athlete_id) if "primary_source" in sent else None
    await db.commit()
    return {**await _sync_settings(repo), "dedup": rebuilt, "upload": moved}


class BrowserZone(BaseModel):
    zone: str


@router.post("/timezone/browser")
async def browser_timezone(body: BrowserZone, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The browser's Intl zone (shell.js sends it once): one input of the
    automatic time zone (engine/localtime.py)."""
    from backend.engine import localtime
    try:
        return await localtime.set_browser_zone(db, body.zone, athlete_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/dedup/rebuild")
async def rebuild_dedup(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    from backend import cooldown
    cooldown.take("dedup")                        # SP-714: a cool-down per person (429 COOLDOWN)
    r = await dedup.rebuild(db, athlete_id)
    await db.commit()
    return r


class DedupIds(BaseModel):
    ids: list[int]


@router.get("/dedup/group/{row_id}")
async def dedup_group(row_id: int, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The copies of one activity from every source (SP-344): which one counts, which are duplicates."""
    return {"rows": await dedup.group_of(db, athlete_id, row_id)}


@router.get("/dedup/near/{row_id}")
async def dedup_near(row_id: int, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """The activity editor's 「其他來源」 box: the copies, and nearby rows to merge by hand."""
    return await dedup.near(db, athlete_id, row_id)


@router.post("/dedup/merge")
async def dedup_merge(body: DedupIds, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """「這幾筆是同一次運動」: merged by hand whatever their times (SP-344); counted once."""
    try:
        r = await dedup.merge(db, athlete_id, body.ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return r


@router.post("/dedup/split/{row_id}")
async def dedup_split(row_id: int, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """「這筆是另一次運動」: split from its group by hand (SP-344); counted on its own."""
    try:
        r = await dedup.split(db, athlete_id, row_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    await db.commit()
    return r


@router.delete("/dedup/links/{row_id}")
async def dedup_unlink(row_id: int, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Forget the user's merges / splits of this activity: the automatic rule decides again."""
    r = await dedup.unlink(db, athlete_id, row_id)
    await db.commit()
    return r


# ---------------------------------------------------------------------------- 手動匯入 FIT (SP-323)

def _may_upload() -> None:
    """The `upload.fit` capability (tenancy.py: never in the demo) and the plan's `import:fit`
    scope (membership.py)."""
    from backend import membership as MS, tenancy
    if not tenancy.can("upload.fit"):
        raise UserError(N_("這個帳號不能匯入 FIT 檔"), code="UPLOAD_FIT_FORBIDDEN", status=403)
    MS.require("import:fit")


def _mb(n: int) -> int:
    return max(1, n // (1024 * 1024))


@router.get("/upload/fit")
async def upload_fit_status(db: AsyncSession = Depends(get_db)):
    """The imported activities (rows, date span) and the limits the page batches by."""
    from backend.db.current import current_athlete_id
    _may_upload()
    return await upload.stats(db, current_athlete_id())


@router.post("/upload/fit")
async def upload_fit(request: Request, db: AsyncSession = Depends(get_db)):
    """「匯入 FIT 檔」: multipart `files` (≤ upload.MAX_FILES, the body ≤ MAX_REQUEST_BYTES; the
    page sends a big folder in batches) into the current tenant only (sync/upload.py). `last` = 0
    on every batch but the last one, `earlier_added` = what the earlier batches added: the steps
    after a sync (Dataset warm-up, overview, plan inputs, automatic plan) run once, after the last.
    {added, duplicate, failed, skipped, folder, items: [{name, status, reason, date, sport, of}]}."""
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from backend import tenancy
    from backend.db.current import current_athlete_id
    _may_upload()
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    if size > upload.MAX_REQUEST_BYTES:
        raise UserError(N_("一次上傳最多 {mb} MB"), code="UPLOAD_TOO_LARGE", status=413,
                        mb=_mb(upload.MAX_REQUEST_BYTES))
    athlete_id = current_athlete_id()
    try:
        with upload.claim(tenancy.current().id):
            try:
                form = await request.form(max_files=upload.MAX_FILES + 1, max_fields=20)
            except StarletteHTTPException as e:
                if "too many files" in str(e.detail).lower():
                    raise UserError(N_("一次最多 {n} 個檔"), code="UPLOAD_TOO_MANY", status=413, n=upload.MAX_FILES)
                raise UserError(N_("讀不懂上傳的內容"), code="UPLOAD_BAD_FORM", status=400)
            try:                                  # the spooled temporary files go in every case
                files = [v for k, v in form.multi_items() if k == "files" and not isinstance(v, str)]
                if not files:
                    raise UserError(N_("沒有收到檔案"), code="UPLOAD_NO_FILES", status=400)
                if len(files) > upload.MAX_FILES:
                    raise UserError(N_("一次最多 {n} 個檔"), code="UPLOAD_TOO_MANY", status=413, n=upload.MAX_FILES)
                res = await upload.import_files(db, athlete_id, files)
                last = str(form.get("last") or "1") != "0"
                try:
                    earlier = max(0, int(form.get("earlier_added") or 0))
                except ValueError:
                    earlier = 0
            finally:
                await form.close()
    except upload.UploadBusy:
        raise UserError(N_("另一個匯入還在進行，請稍後再試"), code="UPLOAD_BUSY", status=409)
    added = res["added"] + earlier
    if last and added > 0:
        await runner.after_import(db, upload.SOURCE, {"status": "partial" if res["failed"] else "ok",
                                                      "downloaded": added, "trigger": "upload"}, athlete_id)
    return res


# ---------------------------------------------------------------------------- 匯入匯出檔 (SP-349)

class ArchiveStart(BaseModel):
    name: str
    size: int


def _archive_error(e) -> UserError:
    from backend.sync import archive as AR
    r = e.reason
    if r == "busy":
        return UserError(N_("另一個匯出檔還在處理，請等它完成"), code="ARCHIVE_BUSY", status=409)
    if r == "not_found":
        return UserError(N_("找不到這次匯入，請重新選擇檔案"), code="ARCHIVE_NOT_FOUND", status=404)
    if r == "offset":
        return UserError(N_("上傳的位置對不上，請重新選擇同一個檔案接著傳"), code="ARCHIVE_OFFSET", status=409,
                         received=int(e.params.get("received") or 0))
    if r == "too_large":
        return UserError(N_("匯出檔最大 {gb} GB"), code="ARCHIVE_TOO_LARGE", status=413,
                         gb=AR.MAX_ARCHIVE_BYTES // 1024 ** 3)
    if r == "empty":
        return UserError(N_("檔案是空的"), code="ARCHIVE_EMPTY", status=400)
    if r == "nothing":
        return UserError(N_("沒有可以重試的檔案"), code="ARCHIVE_NOTHING_TO_RETRY", status=409)
    return UserError(N_("只能匯入 ZIP 檔"), code="ARCHIVE_NOT_ZIP", status=400)


@router.get("/upload/archive")
async def archive_status():
    """The tenant's latest export import (state, progress, results), or null; and the limits."""
    from backend.sync import archive as AR
    _may_upload()
    return {"import": AR.latest(), "limits": AR.limits()}


@router.post("/upload/archive")
async def archive_start(body: ArchiveStart):
    """「匯入匯出檔」: start (or resume) the upload of one ZIP (sync/archive.py); the page then PUTs
    it in pieces of `limits.chunk_bytes` at `received`."""
    import asyncio
    from backend.db.current import current_athlete_id
    from backend.sync import archive as AR
    _may_upload()
    try:
        return await asyncio.to_thread(AR.create, body.name, int(body.size), current_athlete_id())
    except AR.ArchiveError as e:
        raise _archive_error(e)


@router.get("/upload/archive/{imp_id}")
async def archive_one(imp_id: str):
    from backend.sync import archive as AR
    _may_upload()
    try:
        s = AR.status(imp_id)
    except AR.ArchiveError as e:
        raise _archive_error(e)
    if s is None:
        raise _archive_error(AR.ArchiveError("not_found"))
    return s


@router.put("/upload/archive/{imp_id}")
async def archive_piece(imp_id: str, request: Request, offset: int = 0):
    """One piece of the ZIP (raw body, ≤ chunk_bytes) at `offset`; the last one queues the work
    (background, in parts: GET the status for the progress)."""
    import asyncio
    from backend.sync import archive as AR
    _may_upload()
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    if size > AR.CHUNK_BYTES:
        raise _archive_error(AR.ArchiveError("too_large"))
    buf = bytearray()
    async for part in request.stream():
        buf += part
        if len(buf) > AR.CHUNK_BYTES:
            raise _archive_error(AR.ArchiveError("too_large"))
    try:
        s = await asyncio.to_thread(AR.append, imp_id, int(offset), bytes(buf))
    except AR.ArchiveError as e:
        raise _archive_error(e)
    if s.pop("start", False):
        AR.start(imp_id, asyncio.get_running_loop())
        s = AR.status(imp_id)
    return s


@router.post("/upload/archive/{imp_id}/retry")
async def archive_retry(imp_id: str):
    """「重試」: go on where the work stopped, and try the files that failed for a passing reason again."""
    import asyncio
    from backend.sync import archive as AR
    _may_upload()
    try:
        return AR.retry(imp_id, asyncio.get_running_loop())
    except AR.ArchiveError as e:
        raise _archive_error(e)


@router.delete("/upload/archive/{imp_id}")
async def archive_delete(imp_id: str):
    """「清除」: forget the import (its archive and results; a running one stops after its current
    file). The imported activities stay."""
    from backend.sync import archive as AR
    _may_upload()
    try:
        return {"deleted": AR.delete(imp_id)}
    except AR.ArchiveError as e:
        raise _archive_error(e)


@router.get("/tp/settings")
async def get_tp_settings(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    """Fetch TP athlete settings (FTP, weight, LTHR, etc)."""
    data = await fetch_tp_settings(db, athlete_id)
    if data is None:
        return {"error": "TP_AUTH_REQUIRED_OR_NO_ATHLETE"}
    return data
