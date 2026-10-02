"""
Translations of the bundled views (views/*.json) — a sidecar per locale,
views/i18n/<locale>.json, addressed by view file and the stable ids
(viewids.py), so the view files themselves stay in Chinese:

    {
      "training": {                                   # views/training.json
        "name": "My training",
        "dashboards": {"load-pmc": {"title": "Load (PMC)", "description": "…"}},
        "charts": {"pmc-all": {"title": "…", "description": "…",
                               "series": {"CTL 體能": "CTL Fitness"},     # by the original series name
                               "variants": {"tss": "TSS"},                # variant key -> label
                               "power_note": "…"}}
      }
    }

null / missing fields keep the original. Only the repo's bundled views are
translated: WKO5 views and the athlete's own JSON views are user content.
Applied after views/wko5_fixes.json (wko5views._views), so the fixes never
see a translated title; the render cache key covers the chart definition,
so a translated chart is cached apart from the original.
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Optional

from backend import i18n
from backend.engine.wko5expr.customviews import REPO_VIEWS

SIDECAR_DIR = REPO_VIEWS / "i18n"

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[Optional[float], dict]] = {}


def sidecar(loc: str) -> dict:
    p = SIDECAR_DIR / f"{loc}.json"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    with _LOCK:
        hit = _CACHE.get(loc)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    with _LOCK:
        _CACHE[loc] = (mtime, data)
    return data


def _stem(view: dict) -> Optional[str]:
    """The bundled view's file stem ('training'), None for any other view."""
    if view.get("source") != "custom" or not view.get("path"):
        return None
    p = Path(view["path"])
    try:
        if p.resolve().parent != REPO_VIEWS.resolve():
            return None
    except OSError:
        return None
    return p.stem


def _set(obj: dict, key: str, val) -> None:
    if isinstance(val, str) and val:
        obj[key] = val


def _series(series: list, names: dict) -> None:
    for s in series:
        _set(s, "name", names.get(s.get("name")))


def translate_view(view: dict, tr: dict) -> dict:
    """A copy of `view` with the sidecar entry `tr` applied."""
    v = copy.deepcopy(view)
    _set(v, "label", tr.get("name"))
    dts = tr.get("dashboards") or {}
    cts = tr.get("charts") or {}
    for d in v.get("dashboards", []):
        dt = dts.get(d.get("id")) or {}
        _set(d, "title", dt.get("title"))
        _set(d, "description", dt.get("description"))
        for c in d.get("charts", []):
            ct = cts.get(c.get("id")) or {}
            if not ct:
                continue
            _set(c, "title", ct.get("title"))
            _set(c, "description", ct.get("description"))
            names = {k: x for k, x in (ct.get("series") or {}).items() if isinstance(x, str) and x}
            if names:
                _series(c.get("series", []), names)
                for var in c.get("variants", []) or []:
                    _series(var.get("series", []), names)
                if c.get("zoned") and names.get(c["zoned"].get("line")):
                    c["zoned"]["line"] = names[c["zoned"]["line"]]
            for var in c.get("variants", []) or []:
                _set(var, "label", (ct.get("variants") or {}).get(var.get("key")))
            if c.get("basis") and ct.get("power_note"):
                _set(c["basis"], "power_note", ct.get("power_note"))
    return v


def translate_views(views: dict[str, dict], loc: Optional[str] = None) -> dict[str, dict]:
    """`views` in `loc` (default: the request's). Same dict for zh-TW or without a sidecar."""
    loc = loc or i18n.current_locale()
    if loc == i18n.DEFAULT_LOCALE:
        return views
    sc = sidecar(loc)
    if not sc:
        return views
    out = dict(views)
    for name, v in views.items():
        stem = _stem(v)
        tr = sc.get(stem) if stem else None
        if isinstance(tr, dict) and tr:
            out[name] = translate_view(v, tr)
    return out
