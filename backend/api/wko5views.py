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

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dataclasses import asdict

from backend.engine.wko5expr.config import CONFIG_PATH, MOUNTAIN_PRESET, EngineConfig
from backend.engine.wko5expr.corrections import (
    CorrectionStore, detect_spikes, proposals_to_corrections,
)
from backend.engine.wko5expr.customviews import (
    REPO_VIEWS, USER_VIEWS, load_custom_views, view_dirs,
)
from backend.engine.wko5expr.dataset import Dataset, date_to_day
from backend.engine.wko5expr.render import render_chart
from backend.files.wko5chart_reader import read_view

ROOT = Path(__file__).resolve().parents[2]
ATHLETE_DIR = Path(os.getenv(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))
VIEWS_DIR = Path(os.getenv("WKO5_VIEWS_DIR", str(ROOT)))

router = APIRouter(prefix="/api/v1/wko5", tags=["wko5-views"])


_LIVE: "weakref.WeakSet[Dataset]" = weakref.WeakSet()


@lru_cache(maxsize=4)
def _dataset_cfg(cfg_json: str) -> Dataset:
    ds = Dataset(ATHLETE_DIR, config=EngineConfig.from_dict(json.loads(cfg_json)))
    _LIVE.add(ds)
    return ds


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
    return _dataset_cfg(json.dumps(cfg.to_dict(), sort_keys=True))


@lru_cache(maxsize=8)
def _wko5_views() -> dict[str, dict]:
    """Views imported from WKO5 `.wko5chart` binaries — read-only, for parity."""
    out = {}
    for p in sorted(VIEWS_DIR.rglob("*.wko5chart")):
        if ".venv" in p.parts or "node_modules" in p.parts:
            continue
        v = read_view(p)
        v["source"] = "wko5"
        out[p.stem] = v
    return out


def _views() -> dict[str, dict]:
    """WKO5 views plus the athlete's own JSON views. Custom views are NOT
    cached, so editing a file and reloading the page picks it up."""
    return {**_wko5_views(), **load_custom_views()}


def _view(name: str) -> dict:
    v = _views().get(name)
    if v is None:
        raise HTTPException(404, f"view {name!r} not found")
    if v.get("error"):
        raise HTTPException(400, f"{v.get('path')}: {v['error']}")
    return v


@router.get("/views")
def list_views():
    return [
        {"name": name, "source": v.get("source", "wko5"), "path": v.get("path"),
         "error": v.get("error"),
         "dashboards": [
            {"index": i, "title": d["title"], "description": d.get("description"),
             "charts": [{"index": j, "title": c.get("title"), "kind": c.get("kind"),
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
def chart(view: str, d: int, c: int, begin: Optional[str] = None, end: Optional[str] = None,
          sports: Optional[str] = None, workout: Optional[int] = None, parity: Optional[bool] = None):
    """Athlete charts use begin/end/sports (the RHE); workout charts need `workout`."""
    v = _view(view)
    try:
        ch = v["dashboards"][d]["charts"][c]
    except IndexError:
        raise HTTPException(404, "chart not found")
    ds = _dataset(parity)
    b, e = _range(ds, begin, end)
    if ch.get("kind") == "workout":
        if workout is None or not 0 <= workout < len(ds.workouts):
            raise HTTPException(400, "workout charts need ?workout=<index>")
        return render_chart(ch, ds, b, e, workout=ds.workouts[workout])
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
