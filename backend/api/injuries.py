"""
傷病紀錄 API (engine/injuries.py, engine/injury_exposure.py;
docs/plans/injury-tracking.plan.md §3.6). Everything answers 404 in the demo
mode (WKO5COACH_MODE=demo). Local only: nothing here is synced or shared.

  GET    /api/v1/wko5/injuries                    every event (+ auto days off, linked activities)
  GET    /api/v1/wko5/injuries/meta               areas (fixed + the user's), severities, settings
  POST   /api/v1/wko5/injuries                    a manual event
  PATCH  /api/v1/wko5/injuries/{id}               change fields
  POST   /api/v1/wko5/injuries/{id}/resolve       「好了」 {date?}
  DELETE /api/v1/wko5/injuries/{id}               delete (activities keep their pain mark)
  POST   /api/v1/wko5/injuries/areas              {label}: add a body area
  DELETE /api/v1/wko5/injuries/areas/{label}      remove one from the picker
  PUT    /api/v1/wko5/injuries/settings           {pattern_alerts?, reentry_step_up?}
  GET    /api/v1/wko5/injuries/analysis           the case-crossover (tier decides the fields)
  GET    /api/v1/wko5/injuries/timeline           weekly load, CTL, injury bands, pain ticks
  GET    /api/v1/wko5/injuries/{id}/days          the 21 days before the onset
  GET    /api/v1/wko5/injuries/page               the page
"""
from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Optional

from backend.engine.localtime import today_local
from backend.i18n.pages import render_page
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.current import current_athlete_id
from backend.db.database import get_db
from backend.db.models import ActivityTag, InjuryEvent
from backend.engine import injuries as INJ


def _not_demo():
    if INJ.demo_mode():
        raise HTTPException(404, "Not Found")


router = APIRouter(prefix="/api/v1/wko5/injuries", tags=["injuries"], dependencies=[Depends(_not_demo)])

EDITABLE = ("area", "side", "kind", "severity", "pain_max", "onset_date", "onset_key", "onset_file", "status",
            "resolved_date", "days_missed", "pause_quality", "note")


def _row_dict(e: InjuryEvent) -> dict:
    return {c: getattr(e, c) for c in INJ.EVENT_COLS}


async def _linked(db: AsyncSession) -> dict:
    return dict((await db.execute(select(ActivityTag.injury_id, func.count()).where(
        ActivityTag.injury_id.is_not(None)).group_by(ActivityTag.injury_id))).all())


async def _get(db: AsyncSession, eid: int) -> InjuryEvent:
    e = (await db.execute(select(InjuryEvent).where(InjuryEvent.id == eid))).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, "INJURY_NOT_FOUND")
    return e


async def custom_areas(db: AsyncSession) -> list[str]:
    from backend.settings.repository import SettingsRepository
    v = await SettingsRepository(db).get(INJ.SETTING_AREAS)
    return [a for a in v if INJ.clean_custom(a) == a] if isinstance(v, list) else []


async def remember_area(db: AsyncSession, label: Optional[str]) -> None:
    """A user-added area joins the picker (user_settings injury.custom_areas)."""
    from backend.settings.repository import SettingsRepository
    if not INJ.is_custom(label):
        return
    have = await custom_areas(db)
    if label in have or len(have) >= INJ.CUSTOM_MAX:
        return
    await SettingsRepository(db).set(INJ.SETTING_AREAS, have + [label])


def _runs() -> tuple[list[tuple], Optional[dt.date]]:
    """(date, minutes, pain) of every run of the dataset, and the dataset's today."""
    from backend.api.wko5views import _dataset
    from backend.engine import activity_tags as AT
    from backend.engine.overview import day_to_date, moving_s
    ds = _dataset()
    tags = AT.load()
    out = []
    for w in ds.workouts:
        if w.sport != "run":
            continue
        u = AT.find(tags, w.entry.start, w.entry.file) if tags else None
        out.append((day_to_date(w.day), moving_s(w) / 60.0, (u or {}).get("pain")))
    return out, day_to_date(ds.today)


async def _list_json(db: AsyncSession, with_days: bool = True) -> list[dict]:
    from starlette.concurrency import run_in_threadpool
    evs = (await db.execute(select(InjuryEvent).where(InjuryEvent.athlete_id == current_athlete_id())
                            .order_by(InjuryEvent.onset_date.desc(), InjuryEvent.id.desc()))).scalars().all()
    linked = await _linked(db)
    runs = []
    if with_days and evs:
        try:
            runs, _ = await run_in_threadpool(_runs)
        except Exception:                   # noqa: BLE001 — the list still shows without the dataset
            runs = []
    today = today_local()
    out = []
    for e in evs:
        d = _row_dict(e)
        auto = INJ.days_off_auto(d, runs, today) if runs else None
        out.append(INJ.event_json(d, today, linked.get(e.id, 0), auto))
    return out


async def _settings(db: AsyncSession) -> dict:
    from backend.settings.repository import SettingsRepository
    r = SettingsRepository(db)
    return {"pattern_alerts": bool(await r.get(INJ.SETTING_PATTERN)),
            "reentry_step_up": bool(await r.get(INJ.SETTING_STEP_UP))}


@router.get("/meta")
async def meta(db: AsyncSession = Depends(get_db)):
    return {"areas": [{"key": k, "label": v, "custom": False} for k, v in INJ.AREAS.items()]
            + [{"key": a, "label": a, "custom": True} for a in await custom_areas(db)],
            "sides": INJ.SIDES, "no_side": list(INJ.NO_SIDE), "kinds": INJ.KINDS, "severities": INJ.SEVERITIES,
            "severity_help": INJ.SEVERITY_HELP, "statuses": INJ.STATUSES, "pain": INJ.PAIN,
            "settings": await _settings(db), "pattern_min_n": INJ.PATTERN_MIN_N,
            "disclaimer": INJ.DISCLAIMER, "monitor": INJ.SILBERNAGEL["text"]}


@router.get("")
async def list_injuries(db: AsyncSession = Depends(get_db)):
    return {"injuries": await _list_json(db)}


@router.post("/areas")
async def add_area(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    lab = INJ.clean_custom((body or {}).get("label"))
    if lab is None:
        raise HTTPException(400, "INVALID_AREA")
    if len(await custom_areas(db)) >= INJ.CUSTOM_MAX and lab not in await custom_areas(db):
        raise HTTPException(400, "TOO_MANY_AREAS")
    await remember_area(db, lab)
    await db.commit()
    return {"key": lab, "label": lab, "custom": True}


@router.delete("/areas/{label}")
async def remove_area(label: str, db: AsyncSession = Depends(get_db)):
    """Only removes it from the picker: stored events keep their area."""
    from backend.settings.repository import SettingsRepository
    have = await custom_areas(db)
    await SettingsRepository(db).set(INJ.SETTING_AREAS, [a for a in have if a != label])
    await db.commit()
    return {"removed": label in have}


@router.put("/settings")
async def put_settings(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.settings.repository import SettingsRepository
    from starlette.concurrency import run_in_threadpool
    r = SettingsRepository(db)
    if "pattern_alerts" in body:
        v = body["pattern_alerts"]
        if not isinstance(v, bool):
            raise HTTPException(400, "pattern_alerts must be true/false")
        if v:
            an = await run_in_threadpool(_analysis_cached, None, False, None)
            if an["n"] < INJ.PATTERN_MIN_N:
                raise HTTPException(400, f"要有 {INJ.PATTERN_MIN_N} 次以上進分析的受傷紀錄才能打開（目前 {an['n']} 次）")
        await r.set(INJ.SETTING_PATTERN, v)
    if "reentry_step_up" in body:
        if not isinstance(body["reentry_step_up"], bool):
            raise HTTPException(400, "reentry_step_up must be true/false")
        await r.set(INJ.SETTING_STEP_UP, body["reentry_step_up"])
    await db.commit()
    _plan_changed()
    return await _settings(db)


def _plan_changed() -> None:
    """The planner reads the events (quality_gate pause, reentry): drop its caches."""
    INJ._memo.clear()
    _an_memo.clear()
    try:
        from backend.api import overview as OV
        with OV._lock:
            OV._status_cache.clear()
    except Exception:                       # noqa: BLE001
        pass


def _clean_body(body: dict, partial: bool) -> dict:
    f = {k: body[k] for k in EDITABLE if k in body}
    if "area" in f and f["area"] is not None:
        f["area"] = INJ.norm_area(f["area"]) or f["area"]
    if "note" in f and isinstance(f["note"], str):
        f["note"] = f["note"].strip() or None
    err = INJ.validate_event(f)
    if err:
        raise HTTPException(400, err)
    if not partial and "onset_date" not in f:
        raise HTTPException(400, "INVALID_DATE")
    if f.get("side") and f.get("area") in INJ.NO_SIDE:
        f["side"] = None
    return f


async def _onset_activity(f: dict, e: Optional[InjuryEvent]) -> None:
    """§1.3 rule 4: a moved onset date points at that day's activity (if any)."""
    from starlette.concurrency import run_in_threadpool
    if "onset_date" not in f or (e is not None and f["onset_date"] == e.onset_date):
        return
    if "onset_key" in f:
        return

    def find():
        from backend.api.wko5views import _dataset
        from backend.engine import activity_tags as AT
        try:
            ds = _dataset()
        except Exception:                   # noqa: BLE001
            return None
        day = [w for w in ds.workouts if w.entry.start.date().isoformat() == f["onset_date"] and w.sport == "run"]
        if not day:
            day = [w for w in ds.workouts if w.entry.start.date().isoformat() == f["onset_date"]]
        if not day:
            return None
        w = max(day, key=lambda x: x.metrics.get("duration") or 0)
        return AT.key_of(w.entry.start), w.entry.file
    hit = await run_in_threadpool(find)
    f["onset_key"], f["onset_file"] = hit if hit else (None, None)


@router.post("")
async def create_injury(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    f = _clean_body(body or {}, partial=False)
    err = INJ.check_dates(f.get("onset_date"), f.get("resolved_date"))
    if err:
        raise HTTPException(400, err)
    await _onset_activity(f, None)
    f.setdefault("area", INJ.UNKNOWN)
    f.setdefault("kind", "overuse")
    f.setdefault("severity", "mild")
    f.setdefault("status", "resolved" if f.get("resolved_date") else "active")
    if f["status"] == "resolved" and not f.get("resolved_date"):
        f["resolved_date"] = today_local().isoformat()
    evs = [_row_dict(e) for e in (await db.execute(select(InjuryEvent))).scalars().all()]
    rec = INJ.recurrence(f, evs)
    if rec:
        f["recurrence_of"] = rec["recurrence_of"]
        f["note"] = "；".join(x for x in (f.get("note"), rec["note"]) if x)
    now = dt.datetime.utcnow()
    e = InjuryEvent(athlete_id=current_athlete_id(), created_at=now, updated_at=now, pause_quality=bool(f.pop("pause_quality", False)), **f)
    db.add(e)
    await remember_area(db, f.get("area"))
    await db.commit()
    _plan_changed()
    return INJ.event_json(_row_dict(e), today_local(), 0)


@router.patch("/{eid}")
async def patch_injury(eid: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    e = await _get(db, eid)
    f = _clean_body(body or {}, partial=True)
    onset = f.get("onset_date", e.onset_date)
    if f.get("status") == "resolved" and not f.get("resolved_date", e.resolved_date):
        f["resolved_date"] = today_local().isoformat()
    if f.get("status") in ("active", "draft") and "resolved_date" not in f:
        f["resolved_date"] = None
    if f.get("resolved_date") and "status" not in f:
        f["status"] = "resolved"
    err = INJ.check_dates(onset, f.get("resolved_date", e.resolved_date))
    if err:
        raise HTTPException(400, err)
    await _onset_activity(f, e)
    # saving the details of a draft makes it a real event (分析才會納入)
    if e.status == "draft" and "status" not in f and body.get("confirm", True):
        f["status"] = "resolved" if f.get("resolved_date", e.resolved_date) else "active"
    for k, v in f.items():
        setattr(e, k, v)
    e.updated_at = dt.datetime.utcnow()
    await remember_area(db, e.area)
    await db.commit()
    _plan_changed()
    linked = await _linked(db)
    return INJ.event_json(_row_dict(e), today_local(), linked.get(e.id, 0))


@router.post("/{eid}/resolve")
async def resolve_injury(eid: int, body: Optional[dict] = Body(None), db: AsyncSession = Depends(get_db)):
    e = await _get(db, eid)
    day = (body or {}).get("date") or today_local().isoformat()
    err = INJ.validate_event({"resolved_date": day}) or INJ.check_dates(e.onset_date, day)
    if err:
        raise HTTPException(400, err)
    e.status, e.resolved_date, e.updated_at = "resolved", day, dt.datetime.utcnow()
    await db.commit()
    _plan_changed()
    return INJ.event_json(_row_dict(e), today_local(), (await _linked(db)).get(e.id, 0))


@router.delete("/{eid}")
async def delete_injury(eid: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import update
    from backend.engine import activity_tags as AT
    e = await _get(db, eid)
    await db.execute(update(ActivityTag).where(ActivityTag.injury_id == eid).values(injury_id=None))
    await db.delete(e)
    await db.commit()
    AT._memo.clear()
    _plan_changed()
    return {"deleted": eid}


# ---- analysis ------------------------------------------------------------

_an_lock = threading.Lock()
_an_memo: dict = {}


def _stamp() -> tuple:
    from backend.engine import activity_tags as AT
    p = AT._db_path()
    try:
        st = p.stat() if p is not None else None
        return (str(p), st.st_mtime_ns, st.st_size) if st else ()
    except OSError:
        return ()


def _analysis_cached(severities: Optional[tuple], season: bool, area: Optional[str]) -> dict:
    from backend.api.wko5views import _dataset
    from backend.engine import injury_exposure as IE
    from backend.engine.overview import day_to_date
    ds = _dataset()
    key = (id(ds), _stamp(), severities, season, area)
    with _an_lock:
        hit = _an_memo.get(key)
    if hit is not None and not hit.get("pending"):
        return hit
    out = IE.analysis(ds, INJ.load_events(), day_to_date(ds.today), severities=severities, season=season, area=area)
    with _an_lock:
        _an_memo.clear() if len(_an_memo) > 16 else None
        _an_memo[key] = out
    return out


@router.get("/analysis")
async def analysis(severities: Optional[str] = None, season: int = 0, area: Optional[str] = None):
    from starlette.concurrency import run_in_threadpool
    sev = None
    if severities:
        sev = tuple(sorted(s for s in severities.split(",") if s in INJ.SEVERITIES))
        if not sev:
            raise HTTPException(400, "INVALID_SEVERITY")
    return await run_in_threadpool(_analysis_cached, sev, bool(season), area or None)


@router.get("/timeline")
async def timeline(begin: Optional[str] = None, end: Optional[str] = None):
    from starlette.concurrency import run_in_threadpool

    def work():
        from backend.api.wko5views import _dataset
        from backend.engine import activity_tags as AT
        from backend.engine import injury_exposure as IE
        from backend.engine.overview import day_to_date
        ds = _dataset()
        try:
            b = dt.date.fromisoformat(begin) if begin else None
            e = dt.date.fromisoformat(end) if end else None
        except ValueError:
            raise HTTPException(400, "INVALID_DATE")
        return IE.timeline(ds, INJ.load_events(), INJ.pain_marks(AT.load()), day_to_date(ds.today), b, e)
    return await run_in_threadpool(work)


@router.get("/page", include_in_schema=False)
def page():
    return render_page("injuries")


@router.get("/{eid}/days")
async def event_days(eid: int, db: AsyncSession = Depends(get_db)):
    from starlette.concurrency import run_in_threadpool
    e = _row_dict(await _get(db, eid))

    def work():
        from backend.api.wko5views import _dataset
        from backend.engine import injury_exposure as IE
        from backend.engine.overview import day_to_date
        ds = _dataset()
        return IE.event_days(ds, e, day_to_date(ds.today))
    return {"id": eid, "days": await run_in_threadpool(work)}
