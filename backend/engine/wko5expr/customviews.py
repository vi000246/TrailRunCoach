"""
Custom views — charts the athlete designs, as editable JSON.

WKO5 views come from `.wko5chart` binaries: read-only, kept for parity
checking. Custom views are plain JSON with the same shape, so both render
through the same engine and can use the same expression language plus this
project's own metrics.

A view file looks like:

    {
      "name": "百岳與越野",
      "dashboards": [
        {
          "title": "垂直負荷",
          "description": "optional",
          "charts": [
            {
              "title": "每週爬升",
              "description": "optional",
              "axes": [{"id": "METERS", "min": 0}],
              "series": [
                {"name": "爬升", "type": "bar", "y_axis": "METERS",
                 "color": "#2563eb",
                 "expression": "athleterange(startofweek(today)-364, today, sum(climbing))"}
              ]
            }
          ]
        }
      ]
    }

Everything except `name`, `title` and `expression` is optional. `kind` defaults
to "athlete" (season-level); set "workout" for per-activity charts.

Files are read from, in order: the repo's `views/` directory, then
~/.wko5coach/views/. A later file with the same `name` replaces an earlier one,
so you can override a bundled view without editing the repo.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

REPO_VIEWS = Path(__file__).resolve().parents[3] / "views"
USER_VIEWS = Path.home() / ".wko5coach" / "views"
# JSON files in the views folders that are not views (see chartfixes.py)
NON_VIEW_FILES = {"wko5_fixes.json"}

SERIES_DEFAULTS = {
    "id": None, "name": None, "type": "line", "expression": "", "color": None,
    "y_axis": "NONE", "x_axis": "DATE", "line_style": "solid", "line_width": "medium",
    "label_position": None,
    # "recent_gain": computed for the chart's note (recentbests.py), not drawn
    "role": None,
    # "pace" / "power": drawn only in that mode of the chart's basis toggle (basis.py)
    "basis": None,
}


class CustomViewError(ValueError):
    pass


def _series(raw: dict, where: str) -> dict:
    if not isinstance(raw, dict):
        raise CustomViewError(f"{where}: series must be an object")
    out = {**SERIES_DEFAULTS, **{k: v for k, v in raw.items() if k in SERIES_DEFAULTS}}
    if out["expression"] is None:
        out["expression"] = ""
    return out


def _chart(raw: dict, where: str) -> dict:
    if "title" not in raw:
        raise CustomViewError(f"{where}: chart needs a title")
    kind = raw.get("kind", "athlete")
    # z5gate: the Zone 5 opening process over the season (quality_gate.z5_history)
    if kind not in ("athlete", "workout", "zones", "targets", "review", "z5gate"):
        raise CustomViewError(f"{where}: kind must be 'athlete', 'workout', 'zones', 'targets', 'review' or 'z5gate'")
    out = {
        "title": raw["title"],
        "description": raw.get("description"),
        "kind": kind,
        "axes": raw.get("axes") or [],
        "series": [_series(s, f"{where}/{raw['title']}") for s in raw.get("series", [])],
    }
    if raw.get("min_days") is not None:
        # long-term charts (monthly / yearly buckets) look back at least this far,
        # whatever shorter range the viewer has selected
        out["min_days"] = int(raw["min_days"])
    if raw.get("period") is not None:
        # default date bucket for period totals (the viewer's 日/週/月/季/年 toggle
        # starts here); expressions write it as startofweek(date) etc.
        if raw["period"] not in ("day", "week", "month", "quarter", "year"):
            raise CustomViewError(f"{where}/{raw['title']}: period must be day, week, month, quarter or year")
        out["period"] = raw["period"]
    if raw.get("window") is not None:
        # 近 N 天新高 (recentbests.py): {"default": 7, "choices": [7, 14, 28]};
        # expressions start with `@win := <default>, …`
        w = raw["window"]
        try:
            choices = [int(c) for c in w["choices"]]
            default = int(w["default"])
        except (TypeError, KeyError, ValueError):
            raise CustomViewError(f"{where}/{raw['title']}: window needs a default and choices (days)")
        if default not in choices or any(c < 1 for c in choices):
            raise CustomViewError(f"{where}/{raw['title']}: window default must be one of its choices")
        out["window"] = {"default": default, "choices": choices}
    if raw.get("basis") is not None:
        # 配速／功率 (basis.py): {"default": "pace", "choices": ["pace", "power"]};
        # series tagged "basis": "pace" / "power" are drawn only in that mode
        from backend.engine.wko5expr.basis import BASES
        bs = raw["basis"]
        choices = bs.get("choices") if isinstance(bs, dict) else None
        if not isinstance(choices, list) or not choices or any(c not in BASES for c in choices) \
                or bs.get("default") not in choices:
            raise CustomViewError(f"{where}/{raw['title']}: basis needs a default and choices from {list(BASES)}")
        out["basis"] = {"default": bs["default"], "choices": list(choices)}
        if bs.get("power_note"):
            out["basis"]["power_note"] = str(bs["power_note"])
    for s in out["series"]:
        if s["basis"] is not None and s["basis"] not in out.get("basis", {}).get("choices", ()):
            raise CustomViewError(f"{where}/{raw['title']}/{s['name']}: series basis needs a chart basis that lists it")
    if kind == "review":
        # a single-activity 判讀卡 (backend/engine/workout_review.py):
        # {"kind": "review", "section": "summary"}
        from backend.engine.workout_review import EXTRA_SECTIONS, SECTIONS
        if raw.get("section") not in SECTIONS + EXTRA_SECTIONS:
            raise CustomViewError(f"{where}/{raw['title']}: section must be one of {list(SECTIONS + EXTRA_SECTIONS)}")
        out["section"] = raw["section"]
    if kind == "zones":
        # a WKO5-style zone table: {"kind": "zones", "system": "frielhr", "days": 30}
        from backend.engine.zones import SYSTEMS
        if raw.get("system") not in SYSTEMS:
            raise CustomViewError(f"{where}/{raw['title']}: system must be one of {sorted(SYSTEMS)}")
        out["system"] = raw["system"]
        out["days"] = int(raw.get("days", 30))
    return out


def parse_view(data: dict, source_path: Optional[Path] = None) -> dict:
    where = str(source_path or "<dict>")
    if not isinstance(data, dict) or "name" not in data:
        raise CustomViewError(f"{where}: view needs a name")
    dashboards = []
    for d in data.get("dashboards", []):
        if "title" not in d:
            raise CustomViewError(f"{where}: dashboard needs a title")
        dashboards.append({
            "title": d["title"],
            "description": d.get("description"),
            "class": "CustomDashboard",
            "charts": [_chart(c, f"{where}/{d['title']}") for c in d.get("charts", [])],
        })
    return {"view": data["name"], "dashboards": dashboards,
            "source": "custom", "path": str(source_path) if source_path else None}


def view_dirs() -> list[Path]:
    return [p for p in (REPO_VIEWS, USER_VIEWS) if p.is_dir()]


def load_custom_views(dirs: Optional[Iterable[Path]] = None) -> dict[str, dict]:
    """{view name -> view}. Later directories override earlier ones."""
    out: dict[str, dict] = {}
    for d in (list(dirs) if dirs is not None else view_dirs()):
        for p in sorted(Path(d).glob("*.json")):
            if p.name in NON_VIEW_FILES:
                continue
            try:
                view = parse_view(json.loads(p.read_text("utf-8")), p)
            except (OSError, ValueError) as e:
                out[f"!error:{p.name}"] = {
                    "view": p.stem, "dashboards": [], "source": "custom",
                    "path": str(p), "error": str(e),
                }
                continue
            out[view["view"]] = view
    return out
