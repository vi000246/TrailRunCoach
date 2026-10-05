"""
Season plan API — target events (races / 百岳), training phases and dated HR
thresholds. Data lives in ~/.wko5coach/plan.json (backend/engine/planning.py).
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Optional

from backend.engine.localtime import today_local
from backend.i18n import _
from backend.i18n.pages import render_page
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.current import current_athlete_id
from backend.db.database import get_db

from backend.engine import planning as P
from backend.engine.zones import SOURCE, aet_uncertainty, zones_json
from backend.files.wko5_athlete import pd_snapshot, read_athlete
from backend.settings.paths import athlete_dir

ATHLETE_DIR = athlete_dir()
STATIC = Path(__file__).resolve().parents[1] / "static"

router = APIRouter(prefix="/api/v1/plan", tags=["plan"])

# WKO5 writes 1980-01-01 on settings that were never entered
WKO5_DEFAULT_DATE = dt.date(1980, 1, 1)

# which periodization dashboard to open in each phase (views/periodization.json)
PHASE_DASHBOARD = {
    "recovery": "① 轉換期", "transition": "① 轉換期", "base": "② 基礎期",
    "specific": "③ 專項期", "taper": "④ 減量期", "event": "④ 減量期",
}


def _wko5_athlete():
    """The WKO5 athlete file, or None for a runner without a WKO5 folder."""
    try:
        f = next(Path(ATHLETE_DIR).glob("*.wko5athlete"), None)
        return None if f is None else read_athlete(f)
    except OSError:
        return None


@lru_cache(maxsize=1)
def _wko5_settings() -> dict:
    ath = _wko5_athlete()
    if ath is None:
        return {"runthr": None, "runmhr": None, "bikethr": None, "runtpace": None, "mftp": None}
    out = {}
    for name in ("runthr", "runmhr", "bikethr", "runtpace"):
        hist = ath.settings.get(name) or []
        last = hist[-1] if hist else None
        out[name] = None if last is None else {
            "value": last[1], "date": last[0].isoformat(),
            "is_default": all(d == WKO5_DEFAULT_DATE for d, _ in hist)}
    mftp = pd_snapshot(ath.root).get(("mftp", "Run"))
    out["mftp"] = None if mftp is None else round(mftp, 1)
    return out


def _kinds() -> dict:
    """Event kind labels; outside Taiwan (engine/region.py) 百岳 reads 多日登山
    (same stored kind, old data reads the same)."""
    from backend.engine import region as RG
    return P.KINDS if RG.is_tw() else {**P.KINDS, "baiyue": "多日登山"}


def _notify(thresholds: bool) -> None:
    from backend.api.wko5views import plan_changed
    plan_changed(thresholds)
    if thresholds:
        # a new CP re-zones the upcoming power targets and re-pushes them (engine/plan_auto.py)
        from backend.engine import plan_auto as PA
        PA.after_thresholds()


def _effective(plan: P.Plan, today: dt.date) -> dict:
    wk = _wko5_settings()
    lthr = plan.threshold_on("lthr", today)
    mhr = plan.threshold_on("mhr", today)
    aet = plan.threshold_on("aethr", today)
    lthr_v = lthr if lthr is not None else (wk["runthr"] or {}).get("value")
    cp = plan.threshold_on("cp", today)
    lr, ar = P.threshold_row(plan, "lthr", today), P.threshold_row(plan, "aethr", today)
    return {
        "cp": {"value": cp if cp is not None else wk.get("mftp"),
               "source": "plan" if cp is not None else "wko5_mftp"},
        "lthr": {"value": lthr_v, "source": "plan" if lthr is not None else "wko5",
                 "wko5_default": lthr is None and bool((wk["runthr"] or {}).get("is_default")),
                 "method": (lr or {}).get("method"), "label": (lr or {}).get("label")},
        "mhr": {"value": mhr if mhr is not None else (wk["runmhr"] or {}).get("value"),
                "source": "plan" if mhr is not None else "wko5"},
        "aethr": {"value": aet if aet is not None else (None if lthr_v is None else round(0.89 * lthr_v, 1)),
                  "source": "plan" if aet is not None else "friel_0.89",
                  "method": (ar or {}).get("method"),
                  "label": (ar or {}).get("label") or "0.89 × LTHR（Friel Z2 上限，推估）",
                  # the 「± N bpm」 badge on an estimated AeT (zones.aet_uncertainty); None when measured
                  "pm": None if aet is None and lthr_v is None else aet_uncertainty(
                      "measured" if ar and ar.get("measured") else "estimate" if aet is not None else "friel")},
    }


@router.get("")
def get_plan(begin: Optional[str] = None, end: Optional[str] = None):
    today = today_local()
    plan = P.Plan.load()
    from backend.engine import event_gpx as EG
    gpx = EG.all_rows()
    b = P._d(begin) or today - dt.timedelta(days=120)
    ev_last = max((e.end for e in plan.events), default=today)
    e = P._d(end) or max(today, ev_last) + dt.timedelta(days=42)
    phases = P.phases(plan, b, e)
    cur = next((p for p in phases if P._d(p.start) <= today <= P._d(p.end)), None)
    return {
        "today": today.isoformat(),
        "begin": b.isoformat(), "end": e.isoformat(),
        "events": [{**P.event_json(x, today), "gpx": EG.meta(gpx.get(x.id))}
                   for x in sorted(plan.events, key=lambda x: x.date)],
        "phases": [P.phase_json(p) for p in phases],
        "manual_phases": bool(plan.phases),
        "b_windows": P.b_event_windows(plan.events),
        "current_phase": None if cur is None else {
            **P.phase_json(cur), "dashboard": PHASE_DASHBOARD.get(cur.kind),
            "days_left": (P._d(cur.end) - today).days},
        "goals": P.goals(plan, today),
        "thresholds": [t.__dict__ for t in sorted(plan.thresholds, key=lambda t: t.date)],
        "effective_thresholds": (eff := _effective(plan, today)),
        "power_zones": {"source": SOURCE, "zones": zones_json(eff["cp"]["value"])},
        "wko5_settings": _wko5_settings(),
        "phase_labels": P.PHASES, "kinds": _kinds(),
        "rules": {"taper_days": P.TAPER_DAYS, "specific_weeks": P.SPECIFIC_WEEKS,
                  "mini_taper_days": P.MINI_TAPER_DAYS, "long_event_hours": P.LONG_EVENT_HOURS,
                  "long_event_ep": P.SIZE_EP[P.ULTRA - 1],
                  # SP-96: a 2–3 day 百岳's taper; 課表偏好 taper_days for a road marathon / an ultra
                  "baiyue_taper_days": P.BAIYUE_TAPER_DAYS, "taper_pref_days": P.taper_days_setting(),
                  # SP-114: a ≥ 4 day 百岳
                  "baiyue_long_taper_days": P.BAIYUE_LONG_TAPER_DAYS},
    }


class EventIn(BaseModel):
    id: Optional[str] = None
    name: str
    date: str
    kind: str = "race"
    priority: str = "A"
    days: int = 1
    distance_km: Optional[float] = None
    climbing_m: Optional[float] = None
    est_hours: Optional[float] = None
    note: str = ""
    heat: str = "auto"                      # auto | hot | cool (heat-acclimation.md §5.4)
    pack_kg: Optional[float] = None         # trip pack kg; None = 9 kg (loaded-carry-training.md §5.1)
    cutoff_hours: Optional[float] = None    # 關門／撤退時間 h (SP-105, engine/race_feasibility.py)
    summit_km: Optional[float] = None       # 百岳: km of the summit; None = the GPX's highest point
    # SP-114: [{km, gain_m, loss_m?}] per day, required for a multi-day trip (planning.clean_day_plan)
    day_plan: Optional[list[dict]] = None
    race_format: Optional[str] = None       # SP-114 賽制 stage | continuous (≥ 2 day 越野賽／其他)


def _gpx_day_plan(data: dict) -> Optional[list[dict]]:
    """The per-day numbers of the event's stored GPX when it has its own day ends (the calculator's
    split points or camp waypoints; SP-114: a GPX with day ends wins), None otherwise."""
    if not data.get("id") or int(data.get("days") or 1) < 2:
        return None
    from backend.engine import event_gpx as EG
    try:
        st = EG.day_stats(data["id"], int(data["days"]))
    except Exception:                       # noqa: BLE001 — a broken file: the user's numbers
        return None
    if not st or st.get("split_source") not in ("stored", "camp") or len(st["days"]) != int(data["days"]):
        return None
    return [{"km": d["km"], "gain_m": d["gain_m"], "loss_m": d["loss_m"]} for d in st["days"]]


@router.put("/events")
def put_event(body: EventIn):
    plan = P.Plan.load()
    data = body.model_dump()
    gd = _gpx_day_plan(data)
    if gd:
        data["day_plan"] = gd
    try:
        ev = plan.upsert_event(data)
    except P.EventError as e:                  # a bad field: the message is for the user
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, f"bad date: {e}")
    plan.save()
    _notify(False)
    return {"event": P.event_json(ev, today_local())}


@router.delete("/events/{eid}")
def delete_event(eid: str):
    plan = P.Plan.load()
    if not plan.delete_event(eid):
        raise HTTPException(404, "no such event")
    plan.save()
    from backend.engine import event_gpx as EG
    try:
        EG.delete(eid)                      # the event's stored GPX goes with it
    except EG.EventGpxError:
        pass
    _notify(False)
    return {"removed": eid}


# ---- the event's GPX (engine/event_gpx.py) --------------------------------

def _plan_event(eid: str) -> P.Event:
    for e in P.Plan.load().events:
        if e.id == eid:
            return e
    raise HTTPException(404, "no such event")


@router.get("/events/{eid}/gpx")
def get_event_gpx(eid: str):
    from backend.engine import event_gpx as EG
    e = _plan_event(eid)
    try:
        row = EG.get(eid)
    except EG.EventGpxError as x:
        raise HTTPException(400, str(x))
    if row is None:
        raise HTTPException(404, "這場賽事沒有 GPX")
    return {"gpx": EG.meta(row), "days": EG.day_stats(eid, e.days or 1)}


@router.post("/events/{eid}/gpx")
async def put_event_gpx(eid: str, file: UploadFile = File(...)):
    """Upload (or replace) the event's GPX / FIT course; stored gzipped."""
    from starlette.concurrency import run_in_threadpool
    from backend.engine import event_gpx as EG
    from backend.engine.racepower import gpx as GPX
    e = _plan_event(eid)
    data = await file.read(GPX.MAX_BYTES + 1)
    try:
        row = await run_in_threadpool(EG.save, eid, data, file.filename or "")
    except EG.EventGpxError as x:
        raise HTTPException(400, str(x))
    _notify(False)
    return {"gpx": EG.meta(row), "days": EG.day_stats(eid, e.days or 1)}


class SplitsIn(BaseModel):
    day_splits_km: list[float] = []


@router.put("/events/{eid}/gpx/splits")
def put_event_gpx_splits(eid: str, body: SplitsIn):
    """The day ends (km) of a multi-day trip, as clicked on the race calculator's profile."""
    from backend.engine import event_gpx as EG
    e = _plan_event(eid)
    try:
        row = EG.set_splits(eid, body.day_splits_km)
    except EG.EventGpxError as x:
        raise HTTPException(404 if "沒有 GPX" in str(x) else 400, str(x))
    _notify(False)
    return {"gpx": EG.meta(row), "days": EG.day_stats(eid, e.days or 1)}


@router.get("/events/{eid}/gpx/file")
def get_event_gpx_file(eid: str):
    """The stored file itself (un-gzipped), to download."""
    from urllib.parse import quote

    from fastapi.responses import Response

    from backend.engine import event_gpx as EG
    _plan_event(eid)
    row = EG.get(eid)
    data = EG.read_bytes(eid) if row else None
    if data is None:
        raise HTTPException(404, "這場賽事沒有 GPX")
    name = row.get("filename") or "course.gpx"
    mt = "application/gpx+xml" if not name.lower().endswith(".fit") else "application/octet-stream"
    return Response(content=data, media_type=mt,
                    headers={"Content-Disposition": f"attachment; filename=\"course\"; filename*=UTF-8''{quote(name)}"})


@router.delete("/events/{eid}/gpx")
def delete_event_gpx(eid: str):
    from backend.engine import event_gpx as EG
    _plan_event(eid)
    if not EG.delete(eid):
        raise HTTPException(404, "這場賽事沒有 GPX")
    _notify(False)
    return {"removed": eid}


class ThresholdIn(BaseModel):
    date: str
    lthr: Optional[float] = None
    aethr: Optional[float] = None
    mhr: Optional[float] = None
    rhr: Optional[float] = None          # resting HR (設定 → 心率, engine/hr_profile.py)
    cp: Optional[float] = None
    note: str = ""
    wprime: Optional[float] = None       # carried through so an edit keeps what apply-cp wrote
    cp_method: Optional[str] = None
    lthr_method: Optional[str] = None    # carried through likewise (planning.LTHR_METHODS)
    aethr_method: Optional[str] = None
    mhr_method: Optional[str] = None     # planning.MHR_METHODS (SP-64)


@router.get("/thresholds")
def get_thresholds():
    """設定 → 閾值測試紀錄 (settings.html, SP-46): the dated rows and what is in effect
    today, without the rest of the season plan. Same data as GET "" (plan.thresholds)."""
    today = today_local()
    plan = P.Plan.load()
    eff = _effective(plan, today)
    return {
        "today": today.isoformat(),
        "thresholds": [t.__dict__ for t in sorted(plan.thresholds, key=lambda t: t.date)],
        "effective_thresholds": eff,
        "power_zones": {"source": SOURCE, "zones": zones_json(eff["cp"]["value"])},
        "wko5_settings": _wko5_settings(),
    }


@router.put("/thresholds")
def put_thresholds(body: list[ThresholdIn]):
    plan = P.Plan.load()
    try:
        for t in body:
            P._d(t.date)
    except ValueError as e:
        raise HTTPException(400, f"bad date: {e}")
    for t in body:
        if t.lthr_method not in (None, *P.LTHR_METHODS) or t.aethr_method not in (None, *P.AETHR_METHODS) \
                or t.mhr_method not in (None, *P.MHR_METHODS):
            raise HTTPException(400, "unknown lthr_method / aethr_method / mhr_method")
    plan.thresholds = [P.Threshold(**t.model_dump()) for t in body
                       if any(v is not None for v in (t.lthr, t.aethr, t.mhr, t.rhr, t.cp))]
    plan.save()
    _notify(True)
    return {"thresholds": [t.__dict__ for t in plan.thresholds]}


class PhaseIn(BaseModel):
    kind: str
    start: str
    end: str
    event_id: Optional[str] = None


@router.put("/phases")
def put_phases(body: list[PhaseIn]):
    """Save manual phases (replaces the automatic schedule). [] = back to auto."""
    plan = P.Plan.load()
    out = []
    for p in body:
        if p.kind not in P.PHASES:
            raise HTTPException(400, f"unknown phase {p.kind}")
        s, e = P._d(p.start), P._d(p.end)
        if e < s:
            raise HTTPException(400, f"{p.kind}: end before start")
        out.append(P.Phase(p.kind, s.isoformat(), e.isoformat(), p.event_id, auto=False))
    plan.phases = sorted(out, key=lambda x: x.start)
    plan.save()
    _notify(False)
    return {"phases": [P.phase_json(p) for p in plan.phases]}


@lru_cache(maxsize=1)
def _wko5_profile() -> dict:
    """What WKO5 has on file, shown next to the editable profile."""
    ath = _wko5_athlete()
    if ath is None:
        return {"weights": [], "height_cm": None, "sex": None}
    prof = ath.root.get(3001)
    sex = prof.get(3017) if prof is not None else None
    height = (ath.settings.get("height") or [(None, None)])[-1][1]
    return {
        "weights": [{"date": d.isoformat(), "kg": v} for d, v in ath.settings.get("weight") or []
                    if d != WKO5_DEFAULT_DATE],
        "height_cm": None if height is None else round(height * 100),
        "sex": sex if sex in P.PROFILE_FIELDS["sex"] else None,
    }


@router.get("/profile")
def get_profile():
    plan = P.Plan.load()
    today = today_local()
    wk = _wko5_profile()
    eff_w = plan.weight_on(today)
    app_w = _app_weight()
    w_src = "設定頁" if eff_w is not None else None
    if eff_w is None and wk["weights"]:
        eff_w, w_src = wk["weights"][-1]["kg"], "WKO5"
    sex = plan.profile.get("sex") or wk["sex"]
    from backend.engine import athlete_profile as AP
    from backend.engine.wko5expr.datasource import read_setting
    return {
        "weights": [w.__dict__ for w in sorted(plan.weights, key=lambda w: w.date)],
        "profile": plan.profile,
        "wko5": wk,
        "effective": {
            "weight": eff_w, "weight_source": w_src,
            "height_cm": plan.profile.get("height_cm") or wk["height_cm"],
            "sex": sex,
            "birth_year": plan.profile.get("birth_year"), "age": AP.age(plan.profile, today),
            "power_meter": plan.profile.get("power_meter"),
            "power_source": AP.profile_power_source(plan.profile),
        },
        # 首次精靈 (shell.js): asks for weight / sex until both are known, once
        "setup": {"needed": AP.setup_needed(eff_w, sex),
                  "done": read_setting(AP.SETUP_DONE_KEY, False) is True,
                  "prefill": {"weight": None if eff_w is not None else app_w,
                              "weight_source": "COROS" if eff_w is None and app_w else None}},
        "options": {**P.PROFILE_FIELDS, "power_source": AP.POWER_SOURCES},
        "power_labels": AP.POWER_LABEL,
    }


def _app_weight() -> Optional[float]:
    """The latest weight the app DB holds (the COROS login writes it,
    sync/coros_client.py) — the 精靈's pre-fill; None without one."""
    import sqlite3
    from backend.engine.wko5expr.datasource import _db_path
    db = _db_path()
    if db is None or not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT weight_kg FROM athlete_settings WHERE weight_kg IS NOT NULL "
                              "ORDER BY effective_date DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return round(float(row[0]), 1) if row and row[0] else None


@router.get("/profile/detect")
def detect_profile():
    """What the data says, for the 一般設定 pre-fill: the power source over the
    last 90 days of runs (engine/athlete_profile.detect_power_source) and the
    app DB's weight (COROS)."""
    from backend.engine import athlete_profile as AP
    try:
        from backend.api.wko5views import _dataset
        power = AP.detect_power_source(_dataset())
    except Exception as e:                  # noqa: BLE001 — no data yet
        power = {"source": None, "error": type(e).__name__}
    return {"power_source": power, "weight": _app_weight(), "labels": AP.POWER_LABEL}


class SetupIn(BaseModel):
    done: bool = True


@router.post("/profile/setup")
async def setup_done(body: SetupIn, db: AsyncSession = Depends(get_db)):
    """The 精靈 was saved or dismissed (「稍後再說」): don't ask again."""
    from backend.engine import athlete_profile as AP
    from backend.settings.repository import SettingsRepository
    await SettingsRepository(db, current_athlete_id()).set(AP.SETUP_DONE_KEY, bool(body.done))
    await db.commit()
    return {"done": bool(body.done)}


class WeightIn(BaseModel):
    date: str
    kg: float


class ProfileIn(BaseModel):
    weights: list[WeightIn] = []
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    birth_year: Optional[int] = None
    power_source: Optional[str] = None
    power_meter: Optional[str] = None      # legacy (stryd / coros / garmin / other)


@router.put("/profile")
def put_profile(body: ProfileIn):
    from backend.engine import athlete_profile as AP
    for w in body.weights:
        try:
            P._d(w.date)
        except ValueError as e:
            raise HTTPException(400, f"bad date: {e}")
        if not 25 <= w.kg <= 250:
            raise HTTPException(400, _("體重 {kg} kg 不合理", kg=w.kg))
    if body.height_cm is not None and not 100 <= body.height_cm <= 250:
        raise HTTPException(400, _("身高 {cm} cm 不合理", cm=body.height_cm))
    if not AP.birth_year_ok(body.birth_year):
        raise HTTPException(400, _("出生年 {year} 不合理", year=body.birth_year))
    if body.power_source is not None and body.power_source not in AP.POWER_SOURCES:
        raise HTTPException(400, f"power_source must be one of {AP.POWER_SOURCES}")
    for k in ("sex", "power_meter"):
        v = getattr(body, k)
        if v is not None and v not in P.PROFILE_FIELDS[k]:
            raise HTTPException(400, f"{k} must be one of {P.PROFILE_FIELDS[k]}")
    plan = P.Plan.load()
    plan.weights = [P.Weight(w.date, round(w.kg, 1)) for w in body.weights]
    plan.profile = {k: v for k, v in (("sex", body.sex), ("height_cm", body.height_cm),
                                      ("birth_year", body.birth_year), ("power_source", body.power_source),
                                      ("power_meter", None if body.power_source else body.power_meter))
                    if v is not None}
    plan.save()
    _notify(True)     # weight feeds W/kg everywhere
    return get_profile()


# ---------------------------------------------------------------------------
# 設定 → 心率 (engine/hr_profile.py): max / resting HR (auto + manual override,
# stored as dated plan thresholds mhr / rhr) and the 課表心率區間 model
#   GET /api/v1/plan/hr-profile
#   PUT /api/v1/plan/hr-profile   {max_hr?, rest_hr?, clear_max?, clear_rest?, model?}
# ---------------------------------------------------------------------------

HR_NOTE = "設定頁手動輸入"


def hr_profile_view(ds, today: dt.date) -> dict:
    from backend.engine import hr_profile as HP
    acc = HP.account()
    mx = HP.max_hr(ds, today, acc)
    rs = HP.rest_hr(ds, today, acc)
    est = mx.get("estimate")
    if est is None:
        from backend.engine.thresholds import estimate_mhr
        try:
            est = estimate_mhr(ds, today)
        except Exception:                   # noqa: BLE001
            est = None
    model = HP.plan_model()
    tt = None
    try:
        from backend.engine.zones import training_targets
        tt = training_targets(ds, int(ds.today))
    except Exception:                       # noqa: BLE001 — no runs yet
        tt = None
    return {"max_hr": {k: v for k, v in mx.items() if k != "estimate"}, "rest_hr": rs,
            "estimate": est, "account": acc, "model": model,
            "models": [{"id": k, "label": _(HP.MODEL_LABEL[k]), "source": _(HP.SOURCE[k])} for k in HP.PLAN_MODELS],
            "plan_zones": (tt or {}).get("hr_model")}


class HrProfileIn(BaseModel):
    max_hr: Optional[float] = None
    rest_hr: Optional[float] = None
    clear_max: bool = False
    clear_rest: bool = False
    model: Optional[str] = None


@router.get("/hr-profile")
def get_hr_profile():
    return hr_profile_view(_estimate_dataset(), today_local())


@router.put("/hr-profile")
async def put_hr_profile(body: HrProfileIn, db: AsyncSession = Depends(get_db)):
    """A manual max / resting HR is a plan threshold row dated today (so it applies from
    today on and charts before it keep what was in effect); clear_* removes every manual
    value of that field (back to auto)."""
    from fastapi.concurrency import run_in_threadpool
    from backend.engine import hr_profile as HP
    if body.max_hr is not None and not 120 <= body.max_hr <= 240:
        raise HTTPException(400, _("最大心率 {hr:g} 不合理（120–240）", hr=body.max_hr))
    if body.rest_hr is not None and not 25 <= body.rest_hr <= 120:
        raise HTTPException(400, _("靜息心率 {hr:g} 不合理（25–120）", hr=body.rest_hr))
    if body.model is not None and body.model not in HP.PLAN_MODELS:
        raise HTTPException(400, f"model must be one of {HP.PLAN_MODELS}")
    today = today_local().isoformat()
    changed = False
    plan = P.Plan.load()
    for field_, val, clear in (("mhr", body.max_hr, body.clear_max), ("rhr", body.rest_hr, body.clear_rest)):
        if clear:
            for t in plan.thresholds:
                if getattr(t, field_) is not None:
                    setattr(t, field_, None)
                    changed = True
        if val is not None:
            row = next((t for t in plan.thresholds if t.date[:10] == today), None)
            if row is None:
                row = P.Threshold(date=today, note=HR_NOTE)
                plan.thresholds.append(row)
            setattr(row, field_, round(float(val)))
            if field_ == "mhr":
                row.mhr_method = "manual"
            changed = True
    if changed:
        plan.thresholds = [t for t in plan.thresholds
                           if any(getattr(t, f) is not None for f in P.Threshold.THRESHOLD_FIELDS)]
        plan.save()
    if body.model is not None:
        from backend.settings.repository import SettingsRepository
        await SettingsRepository(db, current_athlete_id()).set(HP.MODEL_KEY, body.model)
        await db.commit()
    if changed or body.model is not None:
        _notify(True)          # zones / targets / the pushed workouts follow
    return await run_in_threadpool(lambda: hr_profile_view(_estimate_dataset(), today_local()))


# ---- 起始 CTL／ATL (SP-68; engine/load_guard.py pmc_start) ---------------------------------

def pmc_start_view(ds) -> dict:
    """The PMC's start in effect (manual | auto | none) with the automatic seed, the stored
    manual value and today's CTL / ATL / TSB — the numbers the overview / charts and the
    guardrails read (same dataset as the overview)."""
    from backend.engine import load_guard as LG
    from backend.engine.wko5expr.dataset import day_to_date
    from backend.engine.wko5expr.evaluator import Evaluator
    t = int(ds.today)
    ev = Evaluator(ds, t - 1, t)
    ctl, atl, st = ev.pmc()
    iso = lambda d: None if d is None else day_to_date(d).isoformat()      # noqa: E731
    num = lambda v: None if v is None or v != v else round(float(v), 1)    # noqa: E731
    manual = LG.manual_start()
    a = st["auto"]
    return {"source": st["source"],
            "effective": {"date": iso(st["day"]), "ctl": num(st["ctl"]), "atl": num(st["atl"])},
            "manual": manual,
            "manual_ignored": manual is not None and st["source"] != LG.MANUAL,   # dated after today
            "auto": {"date": iso(a["day"]), "seed": num(a["seed"]), "days": a["days"],
                     "final": a["days"] >= LG.SEED_DAYS},
            "now": {"date": iso(t), "ctl": num(ctl.at(t)), "atl": num(atl.at(t)),
                    "tsb": num(ctl.at(t - 1) - atl.at(t - 1)) if ctl.at(t - 1) is not None else None},
            "seed_days": LG.SEED_DAYS, "startup_days": LG.STARTUP_DAYS,
            "manual_startup_days": LG.MANUAL_STARTUP_DAYS, "max": LG.START_MAX}


class PmcStartIn(BaseModel):
    date: Optional[str] = None
    ctl: Optional[float] = None
    atl: Optional[float] = None
    clear: bool = False


def _overview_dataset():
    from backend.api.wko5views import _dataset
    return _dataset()


@router.get("/pmc-start")
def get_pmc_start():
    return pmc_start_view(_overview_dataset())


@router.put("/pmc-start")
async def put_pmc_start(body: PmcStartIn, db: AsyncSession = Depends(get_db)):
    """Save (or with clear: remove) the manual CTL / ATL at the start of `date` (≤ today)."""
    from fastapi.concurrency import run_in_threadpool
    from backend.engine import load_guard as LG
    from backend.settings.repository import SettingsRepository
    value = None
    if not body.clear:
        value = LG.parse_manual({"date": body.date, "ctl": body.ctl, "atl": body.atl})
        if value is None:
            raise HTTPException(400, f"date (YYYY-MM-DD), ctl and atl (0–{LG.START_MAX:g}) are required")
        if value["date"] > today_local().isoformat():
            raise HTTPException(400, "date must not be after today")
        value["ctl"], value["atl"] = round(value["ctl"], 1), round(value["atl"], 1)
    await SettingsRepository(db, current_athlete_id()).set(LG.PMC_START_KEY, value)
    await db.commit()
    return await run_in_threadpool(lambda: pmc_start_view(_overview_dataset()))


def _estimate_dataset():
    # the estimate reads samples; use the athlete's own-formula dataset so
    # approved data corrections apply
    from backend.api.wko5views import _dataset
    return _dataset(parity=False)


@router.get("/threshold-estimate")
def threshold_estimate():
    """Suggested LTHR / AeT from recent runs. Nothing is saved."""
    from backend.engine.thresholds import estimate
    return estimate(_estimate_dataset(), today_local())


class ApplyEstimate(BaseModel):
    lthr: Optional[float] = None
    aethr: Optional[float] = None
    note: str = ""
    date: Optional[str] = None      # the test day (「套用這次的 AeT」); None = today
    # how the values were obtained (planning.LTHR_METHODS / AETHR_METHODS). Default:
    # LTHR = estimate (「套用估計」); AeT = test when a test day is given (「套用這次的
    # AeT」, aet_test.apply_body), else estimate. zones-and-thresholds.md §3.4 change 1.
    lthr_method: Optional[str] = None
    aethr_method: Optional[str] = None
    # 最大心率 (SP-64, engine/threshold_confidence.py): a max-HR test's filtered peak (test) or
    # the sustained-peak candidate (estimate) — only ever on the user's 「套用」
    mhr: Optional[float] = None
    mhr_method: Optional[str] = None


@router.get("/threshold-check")
def threshold_check():
    """設定 → 閾值 (SP-64, engine/threshold_confidence.py): how believable the LTHR / max /
    resting HR in effect are, which one is likely wrong, the suggested tests, the latest
    LTHR 30-min / max-HR test results with their 「套用」 bodies. Nothing is saved."""
    from backend.engine import threshold_confidence as TC
    from backend.engine import zone_events as ZE
    from backend.engine import reentry as RE
    ds = _estimate_dataset()
    today = today_local()
    plan = P.Plan.load()
    try:
        brk = RE.find(ds, today)
    except Exception:                       # noqa: BLE001
        brk = None
    try:
        cool = ZE.suggestions(ds, plan, today, brk=brk)["checks"].get("cool_season")
    except Exception:                       # noqa: BLE001
        cool = None
    g = P.goals(plan, today)
    ph = P.phase_on(plan, today)
    return TC.check(ds, plan, today, brk=brk, cool=cool, kind=getattr(ph, "kind", None),
                    days_to_a=(g or {}).get("days_to_next_a"))


@router.post("/thresholds/apply-estimate")
def apply_estimate(body: ApplyEstimate):
    """The approval step: add a dated row (today, or the test's `date`) with the accepted estimate(s)."""
    if body.lthr is None and body.aethr is None and body.mhr is None:
        raise HTTPException(400, "nothing to apply")
    if body.lthr_method not in (None, *P.LTHR_METHODS) or body.aethr_method not in (None, *P.AETHR_METHODS) \
            or body.mhr_method not in (None, *P.MHR_METHODS):
        raise HTTPException(400, "unknown lthr_method / aethr_method / mhr_method")
    if body.mhr is not None and not 120 <= body.mhr <= 240:
        raise HTTPException(400, _("最大心率 {hr:g} 不合理（120–240）", hr=body.mhr))
    plan = P.Plan.load()
    today = today_local().isoformat()
    if body.date:
        try:
            d = dt.date.fromisoformat(body.date[:10]).isoformat()
        except ValueError:
            raise HTTPException(400, f"bad date {body.date!r}")
        if d > today:
            raise HTTPException(400, "date is in the future")
        today = d
    row = next((t for t in plan.thresholds if t.date == today), None)
    if row is None:
        row = P.Threshold(today)
        plan.thresholds.append(row)
    if body.lthr is not None:
        row.lthr = round(body.lthr)
        row.lthr_method = body.lthr_method or "estimate"
    if body.aethr is not None:
        row.aethr = round(body.aethr)
        row.aethr_method = body.aethr_method or ("test" if body.date else "estimate")
    if body.mhr is not None:
        row.mhr = round(body.mhr)
        row.mhr_method = body.mhr_method or ("test" if body.date else "estimate")
    row.note = (row.note + "；" if row.note else "") + (body.note or "由活動資料自動估算")
    plan.save()
    _notify(True)
    return {"threshold": row.__dict__}


class ApplyCP(BaseModel):
    date: str                           # the test day (not today)
    cp: float
    cp_method: str
    wprime: Optional[float] = None      # J; only a measured (two-point) W′
    activity_index: Optional[int] = None
    note: str = ""


@router.post("/thresholds/apply-cp")
def apply_cp(body: ApplyCP):
    """「套用這次的 CP」: write the CP-test result (engine/cp_protocols.py) as a
    threshold row dated the test day, so the testing indicator's age and the
    90-day freshness are right. Merges into an existing row of that day."""
    from backend.engine import cp_protocols as CPP
    try:
        d = P._d(body.date)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"bad date: {e}")
    if d is None or d > today_local():
        raise HTTPException(400, _("測試日期不能在未來"))
    if body.cp_method not in CPP.METHOD_LABEL:
        raise HTTPException(400, f"cp_method must be one of {tuple(CPP.METHOD_LABEL)}")
    if not 50 <= body.cp <= 700:
        raise HTTPException(400, _("CP {cp} W 不合理", cp=body.cp))
    if body.wprime is not None and not 0 < body.wprime <= 60000:
        raise HTTPException(400, _("W′ {wp} J 不合理", wp=body.wprime))
    if body.wprime is not None and body.cp_method != "2pt":
        raise HTTPException(400, _("W′ 只在兩點測試量得到"))
    plan = P.Plan.load()
    iso = d.isoformat()
    row = next((t for t in plan.thresholds if t.date == iso), None)
    if row is None:
        row = P.Threshold(iso)
        plan.thresholds.append(row)
    row.cp = round(body.cp)
    row.wprime = None if body.wprime is None else round(body.wprime)
    row.cp_method = body.cp_method
    note = body.note or f"CP 測試（{CPP.METHOD_LABEL[body.cp_method]}）"
    if body.activity_index is not None:
        note += f"；活動 #{body.activity_index}"
    row.note = (row.note + "；" if row.note else "") + note
    plan.save()
    _notify(True)
    return {"threshold": row.__dict__}


@router.get("/page", include_in_schema=False)
def page():
    return render_page("plan")
