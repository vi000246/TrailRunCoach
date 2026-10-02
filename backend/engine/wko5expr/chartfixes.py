"""
Corrections for design mistakes in the athlete's WKO5 charts (wrong unit id,
axis limits that clip the data, single-leg cadence labelled as steps/min, ...).

WKO5 views are read from `.wko5chart` binaries and never edited. The fixes live
in `views/wko5_fixes.json` and are applied on top of the parsed view — only
outside parity mode, because parity mode exists to compare our numbers with
WKO5's own screen.

File shape::

    {
      "fixes": [
        {"view": "WKO5 Workout View",
         "chart_id": "palladino-run-summary-report",   # viewids.py; "chart": "<title>" also works
         "chart": "Palladino Run Summary Report",      # with an id: only a reminder for the reader
         "dashboard_id": "workout",         # optional, when titles repeat (or "dashboard": "<title>")
         "series": "Distance (mi)",         # optional: series name ...
         "series_index": 3,                 # ... or position
         "set": {"y_axis": "KM", "name": "Distance (km)"},
         "scale": 2,                        # optional: multiply the values
         "drop": true,                      # optional: remove the series
         "note": "english() 英制距離改公制"},
        {"view": "...", "chart": "...", "axis": "NONE",
         "set": {"min": null, "max": null}, "note": "..."}
      ]
    }

Every applied entry adds its `note` to the chart's `fixes` list; the viewer
shows those as a "已修正單位" badge.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Iterable, Optional

from backend.engine.wko5expr.customviews import REPO_VIEWS

FIXES_FILE = "wko5_fixes.json"
FIXES_PATH = REPO_VIEWS / FIXES_FILE

SERIES_KEYS = {"y_axis", "x_axis", "name", "type", "color", "expression", "line_style", "line_width"}
AXIS_KEYS = {"id", "min", "max"}


class FixError(ValueError):
    pass


def load_fixes(path: Optional[Path] = None) -> list[dict]:
    p = path or FIXES_PATH
    try:
        data = json.loads(Path(p).read_text("utf-8"))
    except FileNotFoundError:
        return []
    fixes = data.get("fixes") if isinstance(data, dict) else None
    if not isinstance(fixes, list):
        raise FixError(f"{p}: expected an object with a 'fixes' list")
    for i, f in enumerate(fixes):
        if not isinstance(f, dict) or not f.get("view") or not (f.get("chart_id") or f.get("chart")):
            raise FixError(f"{p}: fix #{i} needs 'view' and 'chart_id' (or 'chart')")
        if "axis" in f and ("series" in f or "series_index" in f):
            raise FixError(f"{p}: fix #{i} targets both an axis and a series")
        bad = set(f.get("set") or {}) - (AXIS_KEYS if "axis" in f else SERIES_KEYS)
        if bad and ("axis" in f or "series" in f or "series_index" in f):
            raise FixError(f"{p}: fix #{i} can't set {sorted(bad)}")
    return fixes


def _match_series(chart: dict, f: dict) -> list[dict]:
    series = chart.get("series", [])
    if "series_index" in f:
        i = int(f["series_index"])
        return [series[i]] if 0 <= i < len(series) else []
    return [s for s in series if s.get("name") == f["series"]]


def _apply_one(chart: dict, f: dict) -> bool:
    changed = False
    if "axis" in f:
        for a in chart.get("axes") or []:
            if a.get("id") == f["axis"]:
                a.update(f.get("set") or {})
                changed = True
    elif "series" in f or "series_index" in f:
        hit = _match_series(chart, f)
        if f.get("drop"):
            ids = {id(s) for s in hit}
            chart["series"] = [s for s in chart.get("series", []) if id(s) not in ids]
            return bool(hit)
        for s in hit:
            s.update(f.get("set") or {})
            if f.get("scale") is not None:
                s["scale"] = float(f["scale"]) * float(s.get("scale") or 1.0)
            changed = True
    else:  # chart-level: e.g. {"set": {"description": ...}}
        for k, v in (f.get("set") or {}).items():
            chart[k] = v
            changed = True
    return changed


def _hit(obj: dict, f: dict, what: str, required: bool = False) -> bool:
    """Match by `<what>_id` (viewids.py) when the fix has one, else by the title `<what>`."""
    if f.get(f"{what}_id"):
        return obj.get("id") == f[f"{what}_id"]
    if f.get(what):
        return obj.get("title") == f[what]
    return not required


def apply_fixes(views: dict[str, dict], fixes: Iterable[dict]) -> dict[str, dict]:
    """Copy of `views` with the fixes applied. Unmatched fixes are ignored
    (reported by `unmatched()`), so a renamed WKO5 chart can't break a view."""
    out = copy.deepcopy(views)
    for f in fixes:
        v = out.get(f["view"])
        if v is None:
            continue
        for d in v.get("dashboards", []):
            if not _hit(d, f, "dashboard"):
                continue
            for c in d.get("charts", []):
                if not _hit(c, f, "chart", required=True):
                    continue
                if _apply_one(c, f):
                    note = f.get("note") or "已修正單位"
                    notes = c.setdefault("fixes", [])
                    if note not in notes:
                        notes.append(note)
    return out


def unmatched(views: dict[str, dict], fixes: Iterable[dict]) -> list[dict]:
    """Fixes that don't hit anything in `views` (stale entries)."""
    miss = []
    for f in fixes:
        probe = apply_fixes({f["view"]: views[f["view"]]} if f["view"] in views else {}, [f])
        v = probe.get(f["view"])
        hit = v is not None and any((f.get("note") or "已修正單位") in (c.get("fixes") or [])
                                    for d in v["dashboards"] for c in d["charts"])
        if not hit:
            miss.append(f)
    return miss
