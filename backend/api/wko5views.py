"""
WKO5 view reproduction API — renders the user's exported .wko5chart views
straight from a WKO5 athlete folder, for side-by-side verification against
WKO5 itself.

Env:
    WKO5_ATHLETE_DIR  folder containing <Name>.wko5athlete and year/*.wko4
    WKO5_VIEWS_DIR    folder searched (recursively) for *.wko5chart
"""
from __future__ import annotations

import datetime as dt
import json
import os
import weakref
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dataclasses import asdict

from backend.engine.wko5expr.chartfixes import FIXES_PATH, apply_fixes, load_fixes
from backend.engine.wko5expr.config import CONFIG_PATH, MOUNTAIN_PRESET, EngineConfig
from backend.engine.wko5expr.corrections import (
    CorrectionStore, detect_spikes, proposals_to_corrections,
)
from backend.engine.wko5expr.customviews import (
    REPO_VIEWS, USER_VIEWS, load_custom_views, view_dirs,
)
from backend.engine.wko5expr.dataset import Dataset, date_to_day
from backend.engine.wko5expr import periods as PD
from backend.engine.wko5expr.render import render_chart, render_map
from backend.engine.wko5expr.render_cache import CACHE as RENDER_CACHE, chart_key, data_fingerprint
from backend.files.wko5chart_reader import read_view

ROOT = Path(__file__).resolve().parents[2]
ATHLETE_DIR = Path(os.getenv(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))
VIEWS_DIR = Path(os.getenv("WKO5_VIEWS_DIR", str(ROOT)))

router = APIRouter(prefix="/api/v1/wko5", tags=["wko5-views"])


_LIVE: "weakref.WeakSet[Dataset]" = weakref.WeakSet()


@lru_cache(maxsize=4)
def _dataset_cfg(cfg_json: str, athlete_stamp: str = "") -> Dataset:
    ds = Dataset(ATHLETE_DIR, config=EngineConfig.from_dict(json.loads(cfg_json)))
    _LIVE.add(ds)
    return ds


def _athlete_stamp() -> str:
    """WKO5 rewrites the .wko5athlete index when workouts are added or
    removed (sync, file delete) — a new stamp means a fresh Dataset."""
    try:
        return ";".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
                        for p in sorted(ATHLETE_DIR.glob("*.wko5athlete")))
    except OSError:
        return ""


def plan_changed(thresholds: bool) -> None:
    """Season plan edited. Event / phase edits only move goal lines, so the
    cached datasets just pick up the new plan; threshold edits change hrTSS
    and zones everywhere, so the datasets are rebuilt."""
    if thresholds:
        _dataset_cfg.cache_clear()
        return
    from backend.engine.planning import Plan
    for ds in list(_LIVE):
        if not ds.config.parity:
            ds.plan = Plan.load()   # per-workout memo doesn't depend on events


def _dataset(parity: Optional[bool] = None) -> Dataset:
    """parity=True reproduces WKO5 exactly (verification mode); False applies
    the athlete's own adjusted formulas from ~/.wko5coach/engine.json."""
    cfg = EngineConfig.load()
    if parity is not None and parity != cfg.parity:
        cfg = cfg.replace(parity=parity)
    return _dataset_cfg(json.dumps(cfg.to_dict(), sort_keys=True), _athlete_stamp())


@lru_cache(maxsize=1)
def _wko5_views_raw() -> dict[str, dict]:
    """Views imported from WKO5 `.wko5chart` binaries, exactly as WKO5 has them."""
    out = {}
    for p in sorted(VIEWS_DIR.rglob("*.wko5chart")):
        if ".venv" in p.parts or "node_modules" in p.parts:
            continue
        v = read_view(p)
        v["source"] = "wko5"
        out[p.stem] = v
    return out


@lru_cache(maxsize=4)
def _wko5_views_fixed(fixes_mtime: float) -> dict[str, dict]:
    """WKO5 views with views/wko5_fixes.json applied (keyed on the file's
    mtime, so editing the fixes and reloading picks them up)."""
    try:
        fixes = load_fixes()
    except (OSError, ValueError) as e:
        print(f"[wko5views] ignoring {FIXES_PATH}: {e}")
        return _wko5_views_raw()
    return apply_fixes(_wko5_views_raw(), fixes)


def _wko5_views(parity: bool = True) -> dict[str, dict]:
    """Parity mode shows WKO5's charts untouched (side-by-side verification);
    otherwise the chart-design fixes are applied."""
    if parity:
        return _wko5_views_raw()
    try:
        mtime = FIXES_PATH.stat().st_mtime
    except OSError:
        return _wko5_views_raw()
    return _wko5_views_fixed(mtime)


def _views(parity: Optional[bool] = None) -> dict[str, dict]:
    """WKO5 views plus the athlete's own JSON views. Custom views are NOT
    cached, so editing a file and reloading the page picks it up."""
    if parity is None:
        parity = EngineConfig.load().parity
    return {**_wko5_views(parity), **load_custom_views()}


def _view(name: str, parity: Optional[bool] = None) -> dict:
    v = _views(parity).get(name)
    if v is None:
        raise HTTPException(404, f"view {name!r} not found")
    if v.get("error"):
        raise HTTPException(400, f"{v.get('path')}: {v['error']}")
    return v


MAP_PANEL = "PKMapPanelConfig"


def _panel_kind(c: dict) -> Optional[str]:
    """WKO5's map panel has no series; it is a workout's GPS track ("map").
    A review card (workout_review.py) is a workout chart to the viewer."""
    if c.get("kind") == "review":
        return "workout"
    return "map" if c.get("kind") == "other" and c.get("class") == MAP_PANEL else c.get("kind")


@router.get("/views")
def list_views():
    return [
        {"name": name, "source": v.get("source", "wko5"), "path": v.get("path"),
         "error": v.get("error"),
         "dashboards": [
            {"index": i, "title": d["title"], "description": d.get("description"),
             "charts": [{"index": j, "title": c.get("title"), "kind": _panel_kind(c),
                         "series": len(c.get("series", []))} for j, c in enumerate(d["charts"])]}
            for i, d in enumerate(v["dashboards"])]}
        for name, v in _views().items()
    ]


@router.get("/views/dirs")
def custom_view_dirs():
    """Where to put your own view JSON files."""
    return {"dirs": [str(p) for p in view_dirs()],
            "repo": str(REPO_VIEWS), "user": str(USER_VIEWS)}


def _sports(sports: Optional[str]) -> Optional[set[str]]:
    if not sports:
        return None
    return {s.strip().lower() for s in sports.split(",") if s.strip()}


def _range(ds: Dataset, begin: Optional[str], end: Optional[str]) -> tuple[float, float]:
    e = date_to_day(dt.date.fromisoformat(end)) if end else ds.today
    b = date_to_day(dt.date.fromisoformat(begin)) if begin else e - 365
    return b, e


@router.get("/views/{view}/dashboards/{d}/charts/{c}")
def chart(request: Request, view: str, d: int, c: int, begin: Optional[str] = None,
          end: Optional[str] = None, sports: Optional[str] = None, workout: Optional[int] = None,
          parity: Optional[bool] = None):
    """Athlete charts use begin/end/sports (the RHE); workout charts need `workout`.

    Served from the render cache (render_cache.py): the key covers the chart
    definition, every query parameter (so a future `source` / `period` param
    is part of it automatically), the data fingerprint and the code version."""
    ds = _dataset(parity)
    v = _view(view, ds.config.parity)
    try:
        ch = v["dashboards"][d]["charts"][c]
    except IndexError:
        raise HTTPException(404, "chart not found")
    b, e = _range(ds, begin, end)
    needs_workout = _panel_kind(ch) in ("workout", "map")
    if needs_workout and (workout is None or not 0 <= workout < len(ds.workouts)):
        raise HTTPException(400, "workout charts need ?workout=<index>")
    if not needs_workout and ch.get("kind") not in ("athlete", "zones", "targets"):
        raise HTTPException(400, f"unsupported panel {ch.get('class')}")
    pinfo = None
    if ch.get("kind") == "athlete":
        ch, b, pinfo = _apply_period(ch, b, e, request.query_params.get("period"),
                                     custom=v.get("source") == "custom")
    params = {k: val for k, val in request.query_params.items() if k not in ("begin", "end", "parity")}
    req = {"view": view, "d": d, "c": c, "begin": b, "end": e, "parity": ds.config.parity,
           "params": params, "workout_file": ds.workouts[workout].entry.file if needs_workout else None}
    key = chart_key(ch, req, data_fingerprint(ds))

    def compute():
        res = _render(ch, ds, b, e, sports, ds.workouts[workout] if needs_workout else None)
        return {**res, **pinfo} if pinfo else res
    return RENDER_CACHE.get_or_compute(key, compute)


def _apply_period(ch: dict, b: float, e: float, asked: Optional[str], custom: bool):
    """Period-total charts (periods.py). Returns (chart, begin, extra JSON):
    the chart re-bucketed when the viewer asked for another period (custom
    views only, and not on week-locked charts), begin moved back to the
    period's look-back floor and to a bucket start, and what the viewer needs
    for the category axis + toggle."""
    default = PD.chart_period(ch)
    if default is None:
        return ch, b, None
    toggle = custom and not PD.period_locked(ch)
    chosen = asked if toggle and asked in PD.PERIODS else default
    note = None
    if custom:
        floor = PD.min_days(ch, chosen)
        if floor and e - b + 1 < floor:
            b = e - floor + 1
            note = {365: "顯示近 12 個月", 730: "顯示近 24 個月", 1825: "顯示近 5 年"}.get(
                floor, f"顯示近 {floor} 天")
        b = PD.bucket_start(b, chosen)       # the first bucket is a whole one
    if chosen != default:
        ch = PD.with_period(ch, chosen)
    return ch, b, {"x_period": chosen, "period_default": default, "period_toggle": toggle,
                   "buckets": PD.buckets(b, e, chosen), "range_note": note}


def _render(ch: dict, ds: Dataset, b: float, e: float, sports: Optional[str], w) -> dict:
    if ch.get("kind") == "review":
        from backend.engine.workout_review import review
        return {**review(ds, w, ch.get("section") or "summary"), "title": ch.get("title"),
                "description": ch.get("description")}
    if ch.get("kind") == "workout":
        return render_chart(ch, ds, b, e, workout=w)
    if _panel_kind(ch) == "map":
        return render_map(ch, ds, w)
    if ch.get("kind") in ("zones", "targets"):
        import math
        from backend.engine.zones import training_targets, zone_table
        end_day = int(math.floor(e))
        base = {"title": ch.get("title"), "description": ch.get("description"), "kind": ch["kind"]}
        if ch["kind"] == "zones":
            return {**base, "zones": zone_table(ds, ch["system"], end_day, ch.get("days", 30))}
        from backend.engine.thresholds import estimate
        est = estimate(ds, dt.date.today()) if not ds.config.parity else {}
        return {**base, "targets": training_targets(
            ds, end_day, (est.get("lthr") or {}).get("value"), (est.get("aethr") or {}).get("value"))}
    if ch.get("kind") != "athlete":
        raise HTTPException(400, f"unsupported panel {ch.get('class')}")
    return render_chart(ch, ds, b, e, sports=_sports(sports))


@router.get("/workouts")
def workouts(begin: Optional[str] = None, end: Optional[str] = None, sports: Optional[str] = None,
             parity: Optional[bool] = None):
    """RHE activity list for the selected range / sports (newest first)."""
    ds = _dataset(parity)
    b, e = _range(ds, begin, end)
    sp = _sports(sports)
    out = []
    for w in reversed(ds.workouts):
        if not (b <= w.day < e + 1) or (sp is not None and w.sport not in sp):
            continue
        m = w.metrics
        out.append({
            "index": w.idx, "start": w.entry.start.isoformat(), "sport": w.sport,
            "sport_type": w.sport_type, "file": w.entry.file, "tags": w.tags,
            "duration": m.get("duration"), "distance": m.get("distance"),
            "climbing": m.get("climbing"), "tss": m.get("tss"), "if": m.get("if"),
            "hrtss": m.get("hrtss"), "np": m.get("np"),
            "tss_source": ("power" if m.get("np") is not None and m.get("tssduration")
                           else "rtss" if w.sport == "run" and m.get("ngp") and m.get("tss") is not None
                           and m.get("tss") != m.get("hrtss")
                           else "trainingpeaks" if w.entry.file in ds._tp_tss
                           else "hrtss" if m.get("tss") is not None else None),
        })
    return out


@router.get("/workouts/{i}/review")
def workout_review(i: int, section: Optional[str] = None, parity: Optional[bool] = None):
    """Single-activity review (backend/engine/workout_review.py): one section's
    card, or all six dashboards' cards plus the classification."""
    from backend.engine import workout_review as WR
    ds = _dataset(parity)
    if not 0 <= i < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    w = ds.workouts[i]
    if section:
        if section not in WR.SECTIONS + WR.EXTRA_SECTIONS:
            raise HTTPException(400, f"section must be one of {list(WR.SECTIONS + WR.EXTRA_SECTIONS)}")
        return WR.review(ds, w, section)
    cards = {s: WR.review(ds, w, s) for s in WR.SECTIONS}
    head = cards["summary"]
    return {"workout": i, "classification": head.get("classification"),
            "suggested_dashboard": head.get("suggested_dashboard"), "sections": cards}


@router.get("/sports")
def sports_list():
    """Sport groups present in the athlete, with counts (RHE sport filter)."""
    counts: dict[str, int] = {}
    for w in _dataset().workouts:
        counts[w.sport] = counts.get(w.sport, 0) + 1
    return sorted(({"sport": k, "count": v} for k, v in counts.items()), key=lambda x: -x["count"])


@router.get("/athlete")
def athlete_summary(parity: Optional[bool] = None):
    ds = _dataset(parity)
    a = ds.athlete
    return {
        "name": f"{a.first_name} {a.last_name}",
        "workouts": len(ds.workouts),
        "first": ds.workouts[0].entry.start.isoformat() if ds.workouts else None,
        "last": ds.workouts[-1].entry.start.isoformat() if ds.workouts else None,
        "ctlconstant": a.ctlconstant, "atlconstant": a.atlconstant,
        "wko5_pmc_snapshot": a.pmc_snapshot,
        "settings": {k: [[d.isoformat(), val] for d, val in v] for k, v in a.settings.items()},
    }


# ---------------------------------------------------------------------------
# engine config
# ---------------------------------------------------------------------------

@router.get("/config")
def get_config():
    cfg = EngineConfig.load()
    return {"config": cfg.to_dict(), "path": str(CONFIG_PATH),
            "mountain_preset": MOUNTAIN_PRESET.to_dict()}


@router.put("/config")
def put_config(body: dict):
    """Persist the engine config. parity=True reproduces WKO5 exactly."""
    cfg = EngineConfig.from_dict({**EngineConfig.load().to_dict(), **body})
    cfg.save()
    _dataset_cfg.cache_clear()
    return {"config": cfg.to_dict()}


# ---------------------------------------------------------------------------
# data corrections — proposed automatically, applied only on approval
# ---------------------------------------------------------------------------

@router.get("/corrections")
def list_corrections():
    store = CorrectionStore()
    return {"applied": [asdict(c) for c in store.items], "path": str(store.path)}


@router.get("/corrections/proposals")
def correction_proposals(channel: str = "power", factor: float = 1.6,
                         percentile: float = 90.0):
    """Detect bad samples. This ONLY proposes; nothing is changed."""
    ds = _dataset(parity=False)
    applied = {(c.file, c.channel, c.t_start, c.t_end) for c in CorrectionStore().items}
    out = [p for p in detect_spikes(ds, channel=channel, factor=factor, percentile=percentile)
           if (p["file"], p["channel"], p["t_start"], p["t_end"]) not in applied]
    return {"proposals": out, "channel": channel, "factor": factor, "percentile": percentile}


class ApproveBody(BaseModel):
    proposals: list[dict]


@router.post("/corrections/approve")
def approve_corrections(body: ApproveBody):
    """Apply exactly the proposals passed in — the approval step."""
    store = CorrectionStore()
    added = store.add(proposals_to_corrections(body.proposals))
    _dataset_cfg.cache_clear()
    return {"added": [asdict(c) for c in added], "total": len(store.items)}


@router.delete("/corrections/{correction_id}")
def undo_correction(correction_id: str):
    store = CorrectionStore()
    if not store.remove(correction_id):
        raise HTTPException(404, "correction not found")
    _dataset_cfg.cache_clear()
    return {"removed": correction_id, "total": len(store.items)}


@router.get("/viewer", include_in_schema=False)
def viewer():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "wko5_viewer.html")


@router.get("/settings", include_in_schema=False)
def settings_page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "settings.html")
