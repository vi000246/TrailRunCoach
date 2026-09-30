"""
Unit handling for render_chart(): metric conversion outside parity mode, pace
normalisation, per-series scale fixes, and the unit metadata the viewer uses to
label axes and format numbers. See units.py for the registry itself.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

from backend.engine.wko5expr import units as U


def _fv(v) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return None if math.isnan(v) or math.isinf(v) else float(v)


def map_data(data: dict, fn: Callable[[float], Optional[float]]) -> dict:
    """Apply `fn` to every y value of a rendered series (not to x)."""
    def g(v):
        v = _fv(v)
        return None if v is None else _fv(fn(v))
    k = data.get("kind")
    if k == "points":
        data["points"] = [[p[0], g(p[1])] for p in data["points"]]
    elif k == "hline":
        data["y"] = g(data["y"])
    elif k == "band":
        data["range"] = [g(x) for x in data["range"]]
    elif k == "values":
        data["values"] = [g(x) for x in data["values"]]
    elif k == "value":
        data["value"] = g(data["value"])
    return data


def data_ys(data: dict) -> list[float]:
    k = data.get("kind")
    if k == "points":
        vals = [p[1] for p in data["points"]]
    elif k == "hline":
        vals = [data["y"]]
    elif k == "band":
        vals = list(data["range"])
    elif k == "values":
        vals = list(data["values"])
    elif k == "value":
        vals = [data["value"]]
    else:
        vals = []
    return [x for x in (_fv(v) for v in vals) if x is not None]


def sport_hint(workout=None, sports: Optional[set] = None) -> Optional[str]:
    if workout is not None:
        return getattr(workout, "sport", None)
    if sports and len(sports) == 1:
        return next(iter(sports))
    return None


def prepare(s: dict, parity: bool) -> tuple[Optional[str], str, list[str]]:
    """(expression to evaluate, y-axis id to report, notes) for one series."""
    expr = s.get("expression")
    y_id = s.get("y_axis") or "NONE"
    notes: list[str] = []
    if parity:
        return expr, y_id, notes
    if U.uses_english(expr):
        expr = U.metricize_expression(expr)
        notes.append(f"「{s.get('name') or ''}」english() 英制換算改成公制")
    if U.is_imperial(y_id):
        new = U.metric_id(y_id)
        notes.append(f"「{s.get('name') or ''}」單位 {U.unit(y_id).label} → {U.unit(new).label}")
        y_id = new
    return expr, y_id, notes


def finish(entry: dict, s: dict, y_id: str, parity: bool, sport: Optional[str]) -> None:
    """Scale fix, pace normalisation and unit metadata for one rendered series."""
    data = entry.get("data") or {}
    scale = s.get("scale")
    if scale not in (None, 1, 1.0):
        map_data(data, lambda v: v * float(scale))
    u = U.unit(y_id, sport)
    extra = {}
    if u.kind == "pace":
        base = U.pace_base(data_ys(data), s.get("expression"))
        if not parity and base != "min":
            map_data(data, lambda v, b=base: U.pace_to_min_per_km(v, b))
            extra["converted_from"] = base
            base = "min"
        extra["base"] = base
    entry["y_axis"] = y_id
    entry["unit"] = U.meta(y_id, sport, **extra)
    x_id = s.get("x_axis") or "NONE"
    entry["x_unit"] = U.meta(U.metric_id(x_id) if not parity else x_id, sport)


def axes(chart_axes: Optional[list], series: list[dict], parity: bool,
         sport: Optional[str]) -> list[dict]:
    """Chart axes with unit metadata; outside parity, imperial axes become their
    metric twin (limits converted) and merge with an existing metric axis.
    Every y-axis a series uses gets an entry, `used` marks the drawn ones."""
    out: dict[str, dict] = {}
    for a in chart_axes or []:
        a = dict(a)
        aid = a.get("id") or "NONE"
        if not parity and U.is_imperial(aid):
            new = U.metric_id(aid)
            a = {**a, "id": new, "min": U.to_metric_value(aid, a.get("min")),
                 "max": U.to_metric_value(aid, a.get("max"))}
            if new in out:          # the metric axis already exists: keep it
                continue
            aid = new
        a["id"] = aid
        out.setdefault(aid, a)
    used = {e.get("y_axis") or "NONE" for e in series}
    for aid in used:
        out.setdefault(aid, {"id": aid, "min": None, "max": None})
    first = {}
    for e in series:
        first.setdefault(e.get("y_axis") or "NONE", e.get("unit"))
    res = []
    for aid, a in out.items():
        m = first.get(aid) or U.meta(aid, sport)
        res.append({**a, "used": aid in used, "unit": m})
    return res
