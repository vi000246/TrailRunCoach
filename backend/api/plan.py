"""
Season plan API — target events (races / 百岳), training phases and dated HR
thresholds. Data lives in ~/.wko5coach/plan.json (backend/engine/planning.py).
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.engine import planning as P
from backend.engine.zones import SOURCE, zones_json
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


@lru_cache(maxsize=1)
def _wko5_settings() -> dict:
    ath = read_athlete(next(ATHLETE_DIR.glob("*.wko5athlete")))
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


def _notify(thresholds: bool) -> None:
    from backend.api.wko5views import plan_changed
    plan_changed(thresholds)


def _effective(plan: P.Plan, today: dt.date) -> dict:
    wk = _wko5_settings()
    lthr = plan.threshold_on("lthr", today)
    mhr = plan.threshold_on("mhr", today)
    aet = plan.threshold_on("aethr", today)
    lthr_v = lthr if lthr is not None else (wk["runthr"] or {}).get("value")
    cp = plan.threshold_on("cp", today)
    return {
        "cp": {"value": cp if cp is not None else wk.get("mftp"),
               "source": "plan" if cp is not None else "wko5_mftp"},
        "lthr": {"value": lthr_v, "source": "plan" if lthr is not None else "wko5",
                 "wko5_default": lthr is None and bool((wk["runthr"] or {}).get("is_default"))},
        "mhr": {"value": mhr if mhr is not None else (wk["runmhr"] or {}).get("value"),
                "source": "plan" if mhr is not None else "wko5"},
        "aethr": {"value": aet if aet is not None else (None if lthr_v is None else round(0.89 * lthr_v, 1)),
                  "source": "plan" if aet is not None else "friel_0.89"},
    }


@router.get("")
def get_plan(begin: Optional[str] = None, end: Optional[str] = None):
    today = dt.date.today()
    plan = P.Plan.load()
    b = P._d(begin) or today - dt.timedelta(days=120)
    ev_last = max((e.end for e in plan.events), default=today)
    e = P._d(end) or max(today, ev_last) + dt.timedelta(days=42)
    phases = P.phases(plan, b, e)
    cur = next((p for p in phases if P._d(p.start) <= today <= P._d(p.end)), None)
    return {
        "today": today.isoformat(),
        "begin": b.isoformat(), "end": e.isoformat(),
        "events": [P.event_json(x, today) for x in sorted(plan.events, key=lambda x: x.date)],
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
        "phase_labels": P.PHASES, "kinds": P.KINDS,
        "rules": {"taper_days": P.TAPER_DAYS, "specific_weeks": P.SPECIFIC_WEEKS,
                  "mini_taper_days": P.MINI_TAPER_DAYS, "long_event_hours": P.LONG_EVENT_HOURS},
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


@router.put("/events")
def put_event(body: EventIn):
    plan = P.Plan.load()
    try:
        ev = plan.upsert_event(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, f"bad date: {e}")
    plan.save()
    _notify(False)
    return {"event": P.event_json(ev, dt.date.today())}


@router.delete("/events/{eid}")
def delete_event(eid: str):
    plan = P.Plan.load()
    if not plan.delete_event(eid):
        raise HTTPException(404, "no such event")
    plan.save()
    _notify(False)
    return {"removed": eid}


class ThresholdIn(BaseModel):
    date: str
    lthr: Optional[float] = None
    aethr: Optional[float] = None
    mhr: Optional[float] = None
    cp: Optional[float] = None
    note: str = ""
    wprime: Optional[float] = None       # carried through so an edit keeps what apply-cp wrote
    cp_method: Optional[str] = None


@router.put("/thresholds")
def put_thresholds(body: list[ThresholdIn]):
    plan = P.Plan.load()
    try:
        for t in body:
            P._d(t.date)
    except ValueError as e:
        raise HTTPException(400, f"bad date: {e}")
    plan.thresholds = [P.Threshold(**t.model_dump()) for t in body
                       if any(v is not None for v in (t.lthr, t.aethr, t.mhr, t.cp))]
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
    ath = read_athlete(next(ATHLETE_DIR.glob("*.wko5athlete")))
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
    today = dt.date.today()
    wk = _wko5_profile()
    eff_w = plan.weight_on(today)
    if eff_w is None and wk["weights"]:
        eff_w = wk["weights"][-1]["kg"]
    return {
        "weights": [w.__dict__ for w in sorted(plan.weights, key=lambda w: w.date)],
        "profile": plan.profile,
        "wko5": wk,
        "effective": {
            "weight": eff_w, "weight_source": "設定頁" if plan.weights else "WKO5",
            "height_cm": plan.profile.get("height_cm") or wk["height_cm"],
            "sex": plan.profile.get("sex") or wk["sex"],
            "power_meter": plan.profile.get("power_meter"),
        },
        "options": P.PROFILE_FIELDS,
    }


class WeightIn(BaseModel):
    date: str
    kg: float


class ProfileIn(BaseModel):
    weights: list[WeightIn] = []
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    power_meter: Optional[str] = None


@router.put("/profile")
def put_profile(body: ProfileIn):
    for w in body.weights:
        try:
            P._d(w.date)
        except ValueError as e:
            raise HTTPException(400, f"bad date: {e}")
        if not 25 <= w.kg <= 250:
            raise HTTPException(400, f"體重 {w.kg} kg 不合理")
    if body.height_cm is not None and not 100 <= body.height_cm <= 250:
        raise HTTPException(400, f"身高 {body.height_cm} cm 不合理")
    for k in ("sex", "power_meter"):
        v = getattr(body, k)
        if v is not None and v not in P.PROFILE_FIELDS[k]:
            raise HTTPException(400, f"{k} must be one of {P.PROFILE_FIELDS[k]}")
    plan = P.Plan.load()
    plan.weights = [P.Weight(w.date, round(w.kg, 1)) for w in body.weights]
    plan.profile = {k: v for k, v in (("sex", body.sex), ("height_cm", body.height_cm),
                                      ("power_meter", body.power_meter)) if v is not None}
    plan.save()
    _notify(True)     # weight feeds W/kg everywhere
    return get_profile()


def _estimate_dataset():
    # the estimate reads samples; use the athlete's own-formula dataset so
    # approved data corrections apply
    from backend.api.wko5views import _dataset
    return _dataset(parity=False)


@router.get("/threshold-estimate")
def threshold_estimate():
    """Suggested LTHR / AeT from recent runs. Nothing is saved."""
    from backend.engine.thresholds import estimate
    return estimate(_estimate_dataset(), dt.date.today())


class ApplyEstimate(BaseModel):
    lthr: Optional[float] = None
    aethr: Optional[float] = None
    note: str = ""
    date: Optional[str] = None      # the test day (「套用這次的 AeT」); None = today


@router.post("/thresholds/apply-estimate")
def apply_estimate(body: ApplyEstimate):
    """The approval step: add a dated row (today, or the test's `date`) with the accepted estimate(s)."""
    if body.lthr is None and body.aethr is None:
        raise HTTPException(400, "nothing to apply")
    plan = P.Plan.load()
    today = dt.date.today().isoformat()
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
    if body.aethr is not None:
        row.aethr = round(body.aethr)
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
    if d is None or d > dt.date.today():
        raise HTTPException(400, "測試日期不能在未來")
    if body.cp_method not in CPP.METHOD_LABEL:
        raise HTTPException(400, f"cp_method must be one of {tuple(CPP.METHOD_LABEL)}")
    if not 50 <= body.cp <= 700:
        raise HTTPException(400, f"CP {body.cp} W 不合理")
    if body.wprime is not None and not 0 < body.wprime <= 60000:
        raise HTTPException(400, f"W′ {body.wprime} J 不合理")
    if body.wprime is not None and body.cp_method != "2pt":
        raise HTTPException(400, "W′ 只在兩點測試量得到")
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
    return FileResponse(STATIC / "plan.html")
