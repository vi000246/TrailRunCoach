"""
Turn a parsed .wko5chart chart (backend/files/wko5chart_reader.read_view) plus
evaluator results into plot-ready JSON.

Series output kinds:
    points  [[x, y], ...]   x = ISO date (daily) or ISO datetime (per workout)
    hline   y               constant line across the chart
    band    [lo, hi]        horizontal band (e.g. @indexvalues:={-15:10})
    values  [v1, v2, ...]   gauge-style values
    none                    blank / label-only series
    error   message         unsupported or failed expression
"""
from __future__ import annotations

import datetime as dt
import math
import time
from typing import Any, Optional

import numpy as np

from backend.engine.wko5expr.dataset import Dataset, day_to_date
from backend.engine.wko5expr.evaluator import (
    Curve, Daily, EvalError, Evaluator, ListV, PairV, RangeV, WS,
)
from backend.engine.wko5expr.parser import ParseError


def _f(v) -> Optional[float]:
    if v is None or isinstance(v, str):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _day_iso(day: float) -> str:
    return day_to_date(day).isoformat()


def _dt_iso(day: float) -> str:
    base = day_to_date(day)
    secs = round((day - math.floor(day)) * 86400)
    return (dt.datetime.combine(base, dt.time()) + dt.timedelta(seconds=secs)).isoformat()


MAX_POINTS = 3000


def _downsample(xs: np.ndarray, ys: np.ndarray) -> list:
    step = max(1, int(math.ceil(len(xs) / MAX_POINTS)))
    return [[_f(x), _f(y)] for x, y in zip(xs[::step], ys[::step])]


def workout_result_to_json(r: Any, ds: Dataset, w) -> dict:
    """Workout-level result: samples vs elapsed seconds, xy pairs, or a value."""
    if isinstance(r, Curve):
        return _curve_json(r)
    t = ds.channel(w.idx, "elapsedtime")
    if isinstance(r, np.ndarray):
        if t is None or len(t) != len(r):
            return {"kind": "error", "message": "sample length mismatch"}
        return {"kind": "points", "x": "seconds", "points": _downsample(t, r)}
    if isinstance(r, PairV) and isinstance(r.x, np.ndarray) and isinstance(r.y, np.ndarray):
        return {"kind": "points", "x": "seconds", "points": _downsample(r.x, r.y)}
    if isinstance(r, ListV):
        if r.items and all(isinstance(i, RangeV) for i in r.items):
            i = r.items[0]
            return {"kind": "band", "range": [_f(i.lo), _f(i.hi)]}
        return _list_json(r)
    if isinstance(r, PairV) and r.x is None:
        y = _f(r.y)
        return {"kind": "hline", "y": y} if y is not None else {"kind": "none"}
    if isinstance(r, str):
        return {"kind": "value", "value": r}
    if isinstance(r, (Daily, WS)):
        # athleterange(...) inside a workout chart: report the latest value
        js = result_to_json(r, ds, int(math.floor(w.day)) - 1, int(math.floor(w.day)))
        pts = [p for p in js.get("points", []) if p[1] is not None]
        return {"kind": "value", "value": pts[-1][1] if pts else None}
    return {"kind": "value", "value": _f(r)}


def _cell(v):
    """A list / pair element for JSON: numbers (None for na) or strings."""
    return v if isinstance(v, str) else _f(v)


def _list_json(r: ListV) -> dict:
    """{a, b, ...} values (numbers or strings, e.g. string(@from)+" to "+...),
    or a list of (x, y) pairs such as bin(power, {("Recovery", 150), ...})."""
    if r.items and all(isinstance(i, PairV) and i.x is not None
                       and not isinstance(i.x, np.ndarray) for i in r.items):
        return {"kind": "points", "x": "value",
                "points": [[_cell(i.x), _cell(i.y)] for i in r.items]}
    return {"kind": "values", "values": [_cell(i) for i in r.items]}


def _curve_json(r) -> dict:
    xkind = getattr(r, "xkind", "duration")
    if xkind == "date":
        pts = [[_day_iso(x), _f(y)] for x, y in zip(r.xs, r.ys)
               if _f(y) is not None and _f(x) is not None]
    else:
        pts = [[_f(x), _f(y)] for x, y in zip(r.xs, r.ys) if _f(y) is not None]
    out = {"kind": "points", "x": xkind, "points": pts}
    if r.fit:
        out["fit"] = {k: _f(v) for k, v in r.fit.items()
                      if k in ("FTP", "FRC", "Pmax", "TTE", "tte", "vo2max", "tau1", "tau2", "D")}
        out["fit"]["phenotype"] = r.fit.get("phenotype")
        out["fit"]["valid"] = bool(r.fit.get("valid"))
    return out


def result_to_json(r: Any, ds: Dataset, begin: int, end: int) -> dict:
    if isinstance(r, Curve):
        return _curve_json(r)
    if isinstance(r, Daily):
        c = r.clip(begin, end)
        pts = [[_day_iso(c.start + i), _f(v)] for i, v in enumerate(c.values)]
        return {"kind": "points", "x": "date", "points": pts}
    if isinstance(r, WS):
        pts = sorted(
            ([_dt_iso(ds.workouts[k].day), _cell(v)] for k, v in r.items()
             if begin <= math.floor(ds.workouts[k].day) <= end and _cell(v) not in (None, "")),
            key=lambda p: p[0])
        return {"kind": "points", "x": "datetime", "points": pts}
    if isinstance(r, PairV):
        if r.x is None:
            y = _f(r.y)
            return {"kind": "hline", "y": y} if y is not None else {"kind": "none"}
        return {"kind": "points", "x": "value", "points": [[_f(r.x), _f(r.y)]]}
    if isinstance(r, RangeV):
        return {"kind": "band", "range": [_f(r.lo), _f(r.hi)]}
    if isinstance(r, ListV):
        if r.items and all(isinstance(i, RangeV) for i in r.items):
            i = r.items[0]
            return {"kind": "band", "range": [_f(i.lo), _f(i.hi)]}
        return _list_json(r)
    if isinstance(r, str):
        return {"kind": "values", "values": [r]}
    y = _f(r)
    if y is not None:
        return {"kind": "hline", "y": y}
    return {"kind": "none"}


def render_chart(chart: dict, ds: Dataset, begin: float, end: float,
                 sports: Optional[set[str]] = None, workout=None) -> dict:
    """Athlete chart over [begin, end] (RHE sport filter), or a workout chart
    for `workout` (a dataset Workout).

    Every series and axis carries unit metadata (label / kind / decimals, see
    units.py). Outside parity mode imperial units become metric: english() is
    evaluated as metric() and FT/MI/MPH/PACEMI ids are relabelled."""
    from backend.engine.wko5expr import render_units as RU
    parity = bool(getattr(getattr(ds, "config", None), "parity", True))
    sport = RU.sport_hint(workout, sports)
    notes = list(chart.get("fixes") or [])
    ev = Evaluator(ds, begin, end, sports=sports)
    out_series = []
    for s in chart.get("series", []):
        expr, y_id, s_notes = RU.prepare(s, parity)
        notes += [n for n in s_notes if n not in notes]
        t0 = time.perf_counter()
        entry = {k: s.get(k) for k in ("id", "name", "type", "color", "y_axis", "x_axis",
                                       "line_style", "line_width")}
        entry["expression"] = expr
        if not expr or not expr.strip():
            entry["data"] = {"kind": "none"}
        else:
            try:
                if workout is not None:
                    entry["data"] = workout_result_to_json(ev.evaluate(expr, workout), ds, workout)
                else:
                    entry["data"] = result_to_json(ev.evaluate(expr), ds, ev.begin, ev.end)
            except (EvalError, ParseError) as e:
                entry["data"] = {"kind": "error", "message": str(e)}
            except Exception as e:  # keep the page usable; surface the bug
                entry["data"] = {"kind": "error", "message": f"{type(e).__name__}: {e}"}
        RU.finish(entry, s, y_id, parity, sport)
        entry["ms"] = round((time.perf_counter() - t0) * 1000)
        out_series.append(entry)
    return {
        "title": chart.get("title"),
        "description": chart.get("description"),
        "kind": chart.get("kind"),
        "axes": RU.axes(chart.get("axes"), out_series, parity, sport),
        "parity": parity,
        "fixes": notes,
        "begin": _day_iso(ev.begin),
        "end": _day_iso(ev.end),
        "workout": None if workout is None else workout.idx,
        "series": out_series,
        "unsupported": sorted(ev.unsupported),
    }
