"""
Season plan API — target events (races / 百岳), training phases and dated HR
thresholds. Data lives in ~/.wko5coach/plan.json (backend/engine/planning.py).
"""
from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.engine import planning as P
from backend.files.wko5_athlete import pd_snapshot, read_athlete

ATHLETE_DIR = Path(os.getenv(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))
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
        "effective_thresholds": _effective(plan, today),
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


@router.get("/page", include_in_schema=False)
def page():
    return FileResponse(STATIC / "plan.html")
