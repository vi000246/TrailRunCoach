"""
Turn a parsed .wko5chart chart (backend/files/wko5chart_reader.read_view) plus
evaluator results into plot-ready JSON.

Series output kinds:
    points  [[x, y], ...]   x = ISO date (daily) or ISO datetime (per workout)
    hline   y               constant line across the chart
    band    [lo, hi]        horizontal band (e.g. @indexvalues:={-15:10})
    values  [v1, v2, ...]   gauge-style values
    vline   x               vertical line, WKO5's (x,) pair (e.g. (avg(power),))
    none                    blank / label-only series
    error   message         unsupported or failed expression

A chart whose series all come out empty gets `empty`: a short sentence saying
why (the workout has no power, no activity yet this week, the PD model could
not be fitted...), so the card says so instead of drawing bare axes.
"""
from __future__ import annotations

import datetime as dt
import math
import re
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


def _is_time(x: np.ndarray, t: Optional[np.ndarray]) -> bool:
    """x is the workout's elapsed time (possibly with some samples masked)."""
    if t is None or len(t) != len(x):
        return False
    ok = ~np.isnan(x) & ~np.isnan(t)
    return bool(ok.any()) and bool(np.allclose(x[ok], t[ok]))


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
        # (elapsedtime, y) is a time plot; any other x — (ewma(power,30), heartrate),
        # (rgrade, lss) — is an x-y scatter, which must not share a time axis
        return {"kind": "points", "x": "seconds" if _is_time(r.x, t) else "value",
                "points": _downsample(r.x, r.y)}
    if isinstance(r, ListV):
        if r.items and all(isinstance(i, RangeV) for i in r.items):
            i = r.items[0]
            return {"kind": "band", "range": [_f(i.lo), _f(i.hi)]}
        return _list_json(r)
    if isinstance(r, PairV) and r.x is None:
        y = _f(r.y)
        return {"kind": "hline", "y": y} if y is not None else {"kind": "none"}
    if isinstance(r, PairV) and not isinstance(r.x, np.ndarray) and not isinstance(r.y, np.ndarray):
        return _scalar_pair_json(r)
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


def _scalar_pair_json(r: PairV) -> dict:
    """(x, y) of two single values: a point; (x,) — y omitted — is WKO5's
    vertical line at x (e.g. `(avg(power),)` on a HR-vs-power scatter)."""
    x = _f(r.x)
    if r.y is None:
        return {"kind": "vline", "x": x} if x is not None else {"kind": "none"}
    return {"kind": "points", "x": "value", "points": [[x, _f(r.y)]]}


def _pair_series_json(r: PairV, ds: Dataset, begin: int, end: int) -> Optional[dict]:
    """(x, y) where x and/or y is a per-workout (WS) or per-day (Daily) series:
    one x-y point per workout / day, e.g. EPH by EP's (@ep, @eph). A single
    value on one side is broadcast. None when neither side is a series."""
    xs, ys = r.x, r.y
    if isinstance(xs, WS) or isinstance(ys, WS):
        keys = [k for k in (xs if isinstance(xs, WS) else ys)
                if begin <= math.floor(ds.workouts[k].day) <= end]
        if isinstance(xs, WS) and isinstance(ys, WS):
            keys = [k for k in keys if k in ys]
        get = lambda s, k: s.get(k) if isinstance(s, WS) else s   # noqa: E731
        pts = [[_f(get(xs, k)), _cell(get(ys, k))] for k in
               sorted(keys, key=lambda k: ds.workouts[k].day)]
    elif isinstance(xs, Daily) or isinstance(ys, Daily):
        get = lambda s, d: s.at(d) if isinstance(s, Daily) else s   # noqa: E731
        pts = [[_f(get(xs, d)), _f(get(ys, d))] for d in range(int(begin), int(end) + 1)]
    else:
        return None
    return {"kind": "points", "x": "value",
            "points": [p for p in pts if p[0] is not None and p[1] not in (None, "")]}


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
        series = _pair_series_json(r, ds, begin, end)
        return series if series is not None else _scalar_pair_json(r)
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


# ---- why is this chart empty? ---------------------------------------------

def _has_data(d: dict) -> bool:
    k = d.get("kind")
    if k == "points":
        return any(p[1] is not None for p in d.get("points") or [])
    if k == "values":
        return any(v is not None for v in d.get("values") or [])
    if k == "value":
        return d.get("value") is not None
    return False


def chart_is_empty(series: list[dict]) -> bool:
    """Nothing to show: no series carries data. Constant lines (hline / vline /
    band) don't count when the chart also has data series — EPH=5 on an empty
    scatter is still an empty chart — but a report made only of single values
    (MMP Peaks and Clusters Report) is data."""
    datas = [s["data"] for s in series if (s.get("expression") or "").strip()]
    if any(_has_data(d) for d in datas):
        return False
    if any(d.get("kind") in ("points", "values", "value") for d in datas):
        return True
    return not any(d.get("kind") == "hline" and d.get("y") is not None for d in datas)


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# channel an expression names -> the recorded channel it needs, and what to call it
_NEEDS = {"power": "power", "runpower": "power", "bikepower": "power", "ecpower": "power",
          "_rapower": "power", "_rapower4": "power", "heartrate": "heartrate",
          "runheartrate": "heartrate", "speed": "speed", "runspeed": "speed", "rngp": "speed",
          "cadence": "cadence", "runcadence": "cadence", "stancetime": "stancetime",
          "kleg": "stancetime", "fmax": "stancetime", "verticaloscillation": "verticaloscillation",
          "temperature": "temperature", "elevation": "elevation", "rgrade": "elevation",
          # workout metrics computed from power
          "np": "power", "pwhr": "power", "tisaerobic": "power", "tisanaerobic": "power",
          "work": "power", "vi": "power"}
_CHANNEL_ZH = {"power": "功率", "heartrate": "心率", "speed": "速度", "cadence": "步頻",
               "stancetime": "觸地時間", "verticaloscillation": "垂直振幅", "temperature": "溫度",
               "elevation": "海拔"}
_PD_FUNCS = re.compile(r"\b(pdcurve|ftpcurve|frccurve|ftp|frc|pmax|tte|vo2max|stamina|pdprofile)\b",
                       re.I)


def _missing_channels(exprs: list[str], ds: Dataset, w) -> list[str]:
    """Recorded channels the expressions read that this workout doesn't have."""
    need = []
    for e in exprs:
        for tok in _TOKEN.findall(e):
            ch = _NEEDS.get(tok.lower())
            if ch and ch not in need:
                need.append(ch)
    missing = []
    for ch in need:
        v = ds.channel(w.idx, ch)
        if v is None or not len(v) or not np.any(~np.isnan(v)) or \
                (ch in ("power", "cadence") and not np.any(np.nan_to_num(v) > 0)):
            missing.append(ch)
    return missing


def _pd_reason(ds: Dataset, begin: float, end: float) -> str:
    base = "這段期間擬合不出功率–時間（PD）模型，所以 PD 曲線、mFTP、FRC、Pmax、TTE 都沒有值。"
    if getattr(ds, "corrections", None) is None:
        return base + "WKO5 對照模式不套用資料校正；切回「我的算式」並核准功率尖峰的校正後再看。"
    try:
        from backend.engine.wko5expr.corrections import detect_spikes
        spikes = [p for p in detect_spikes(ds)
                  if begin <= math.floor(ds.workouts[p["workout"]].day) <= end]
    except Exception:
        spikes = []
    if spikes:
        which = "、".join(f'{p["start"][:10]} {p["sport"]}（{p["peak"]:.0f} W）' for p in spikes[:3])
        return (base + f"原因是功率資料有異常尖峰：{which}。到「設定 › 資料校正」按「掃描壞點」"
                "核准校正後就畫得出來（不會改到原始檔，可以撤銷）。")
    return base + "模型需要至少一段 40 分鐘以上的最大努力紀錄。"


def _pd_fails(exprs: list[str], ds: Dataset, begin: float, end: float) -> bool:
    """The chart reads the PD model and the range's run-power curve can't be fitted."""
    if not any(_PD_FUNCS.search(e) for e in exprs):
        return False
    try:
        return not Evaluator(ds, begin, end).evaluate("pdcurve(meanmax(runpower))").xs
    except Exception:
        return True


def pd_notice(series: list[dict], ds: Dataset, begin: float, end: float) -> Optional[str]:
    """A chart that drew something but whose PD-model series are blank (Donny's
    targeting, PD Curve with Metrics...): say why those lines are missing."""
    exprs = [s.get("expression") or "" for s in series
             if not _has_data(s["data"]) and _PD_FUNCS.search(s.get("expression") or "")]
    return _pd_reason(ds, begin, end) if exprs and _pd_fails(exprs, ds, begin, end) else None


def empty_reason(chart: dict, series: list[dict], ds: Dataset, begin: float, end: float,
                 workout=None) -> Optional[str]:
    """One sentence saying why a chart has nothing to draw (None if it has)."""
    if not chart_is_empty(series):
        return None
    exprs = [s.get("expression") or "" for s in series if (s.get("expression") or "").strip()]
    errors = [s for s in series if s["data"].get("kind") == "error"]
    if errors:
        return f"算式執行失敗：{errors[0]['data'].get('message')}"
    if workout is not None:
        missing = _missing_channels(exprs, ds, workout)
        if missing:
            return f"這筆活動沒有{'、'.join(_CHANNEL_ZH[m] for m in missing)}資料（裝置沒有記錄）。"
        return "這筆活動沒有這張圖需要的資料。"
    if _pd_fails(exprs, ds, begin, end):
        return _pd_reason(ds, begin, end)
    if any("startofweek(today)" in e.replace(" ", "").lower() for e in exprs):
        return "本週還沒有活動，這段期間沒有資料。"
    return "這段期間沒有資料。"


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
    empty = empty_reason(chart, out_series, ds, ev.begin, ev.end, workout)
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
        "empty": empty,
        "notice": None if empty or workout is not None else pd_notice(out_series, ds, ev.begin, ev.end),
    }


MAP_MAX_POINTS = 2000


def render_map(chart: dict, ds: Dataset, workout) -> dict:
    """WKO5's map panel: the workout's GPS track as [[lon, lat, elevation m,
    elapsed s], ...] (downsampled), or `empty` when it has no GPS."""
    base = {"title": chart.get("title") or "地圖", "description": chart.get("description"),
            "kind": "map", "workout": workout.idx, "series": [], "unsupported": [], "fixes": []}
    lat, lon = ds.channel(workout.idx, "latitude"), ds.channel(workout.idx, "longitude")
    if lat is None or lon is None or len(lat) != len(lon):
        return {**base, "track": [], "empty": "這筆活動沒有 GPS 資料（室內或裝置沒有記錄位置）。"}
    elev = ds.channel(workout.idx, "elevation")
    t = ds.channel(workout.idx, "elapsedtime")
    ok = ~np.isnan(lat) & ~np.isnan(lon) & ~((lat == 0) & (lon == 0))
    idx = np.nonzero(ok)[0]
    if not len(idx):
        return {**base, "track": [], "empty": "這筆活動沒有 GPS 資料（室內或裝置沒有記錄位置）。"}
    idx = idx[::max(1, int(math.ceil(len(idx) / MAP_MAX_POINTS)))]
    col = lambda a, i: None if a is None or len(a) != len(lat) else _f(a[i])   # noqa: E731
    track = [[round(float(lon[i]), 6), round(float(lat[i]), 6), col(elev, i), col(t, i)] for i in idx]
    return {**base, "track": track, "empty": None}
