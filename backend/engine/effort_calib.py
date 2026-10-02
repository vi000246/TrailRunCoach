"""
Full-effort rules per athlete (generalize-athlete plan B5, engine/calibrate.py items).

P8 — trail_max_min_km / trail_max_min_min: the shortest trail race that can
     count as a full effort (racepower/maximal.trail_maximal). Fit: p10 of
     the distance / moving time of the athlete's own trail runs marked 全力
     (activity tags, effort "max"). ≥ 3 races; defaults 10 km / 90 min
     (the author's rule — his races are all longer; 推估 for anyone else).
P9 — effort_rest_max: the long-rest share above which a hard day is 「有拼
     但有休息」, not 全力 (activity_tags.AUTO_EFFORT["rest_max"]). Fit:
     p90 × 1.5 of the rest share of those full-effort activities (trail runs
     and hikes), as unsourced-rules §0.5.2. ≥ 3; default 10 % (推估).

Read with `calibrate.value(name)` (stored fit / manual / default); the fit
runs after a sync (calibrate.after_sync) on the chart Dataset.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL

DAYS = 3 * 365


def _max_runs(ds, today: dt.date, trail_only: bool) -> list:
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    rows = AT.load()
    if not rows:
        return []
    tday = date_to_day(today)
    out = []
    for w in ds.workouts:
        if not (tday - DAYS < w.day <= tday + 1):
            continue
        if trail_only and not (w.sport == "run" and A.is_trail(w)):
            continue
        if not trail_only and w.sport not in ("run", "hike") and w.sport_type not in ("hiking", "mountaineering"):
            continue
        if AT.user_effort(AT.user_of(w, rows)) != "max":
            continue
        out.append(w)
    return out


def _stats(ds, ws) -> list[dict]:
    from backend.engine.racepower import athlete as A
    out = []
    for w in ws:
        s = A.maximal_stats(ds, w)
        if s:
            out.append({**s, "km": (w.metrics.get("distance") or 0) or s.get("km")})
    return out


def _pct_fit(vals: list, q: float, scale: float = 1.0) -> Optional[CAL.Fit]:
    vals = [float(v) for v in vals if v]
    if not vals:
        return None
    v = float(np.percentile(vals, q)) * scale
    sd = float(np.std(vals)) * scale if len(vals) > 1 else None
    return CAL.Fit(v, None if sd is None else sd / np.sqrt(len(vals)), len(vals))


def fit_trail_km(ds, today):
    return _pct_fit([s.get("km") for s in _stats(ds, _max_runs(ds, today, True))], 10)


def fit_trail_min(ds, today):
    return _pct_fit([(s.get("moving_s") or 0) / 60.0 for s in _stats(ds, _max_runs(ds, today, True))], 10)


def fit_rest_max(ds, today):
    rs = [s.get("rest_share") for s in _stats(ds, _max_runs(ds, today, False)) if s.get("rest_share") is not None]
    if len(rs) < 1:
        return None
    v = max(float(np.percentile(rs, 90)) * 1.5, 0.02)
    return CAL.Fit(v, None, len(rs))


def _register() -> None:
    CAL.register(CAL.Item(
        name="trail_max_min_km", label="越野全力的最短距離", unit="km", default=10.0,
        default_src="推估（作者的規則：他的比賽都超過 10 km）", k=3, min_n=3, fit=fit_trail_km,
        bounds=(3.0, 60.0), digits=1,
        help="越野跑要多長才可能算全力（給比賽功率／心率模型當全力樣本）。本人值 = 你標記全力的越野比賽距離的第 10 百分位。"))
    CAL.register(CAL.Item(
        name="trail_max_min_min", label="越野全力的最短時間", unit="分", default=90.0,
        default_src="推估（作者的規則）", k=3, min_n=3, fit=fit_trail_min, bounds=(20.0, 600.0), digits=0,
        help="越野跑要多久才可能算全力。本人值 = 你標記全力的越野比賽移動時間的第 10 百分位。"))
    CAL.register(CAL.Item(
        name="effort_rest_max", label="全力時最多的長休息比例", unit="比例", default=0.10,
        default_src="推估（作者 7 場比賽 0–5 %，留餘裕）", k=3, min_n=3, fit=fit_rest_max,
        bounds=(0.02, 0.30), digits=2,
        help="停下 ≥ 5 分鐘的時間佔總時間超過這個比例，強度再高也算「有拼但有休息」。本人值 = 你的全力活動長休息比例的 p90 × 1.5。"))


_register()


def trail_min_km() -> float:
    return CAL.value("trail_max_min_km")


def trail_min_s() -> float:
    return CAL.value("trail_max_min_min") * 60.0


def rest_max() -> float:
    return CAL.value("effort_rest_max")
