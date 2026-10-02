"""
The drift windows per athlete (generalize-athlete plan B7; unsourced-rules
§0.5.1; engine/calibrate.py items).

Fitted (road runs of the last year, ≥ 30 min):
  drift_early_s   stops that begin this early are the warm-up / city
                  section (workout_review.DRIFT_EARLY_S, 20 min): p95 of the
                  last early stop's start, looking in the first 25 min.
                  ≥ 20 runs with an early stop; bounds 10–25 min; k = 20.
  drift_tail_s    the return leg: the last cluster of stops (≤ 6 min apart)
                  starting this close to the end (DRIFT_TAIL_S, 12 min): p95
                  of the cluster's first stop before the end, looking in the
                  last 15 min. ≥ 20 runs with such a cluster; 5–15 min; k = 20.
Manual only (進階; the methods in §0.5.1 need reference runs the app cannot
recognise yet):
  drift_max_vi    VI ceiling of a steady window (DRIFT_MAX_VI 1.04)
  drift_tau_s     HR lag τ (DRIFT_TAU_S 60 s; Hunt 2015/2019: 55–70 s)
  walk_max_s      a slow stretch this long = a run-walk, refused (WALK_MAX_S 180 s)

workout_review.apply_calibration() puts the values in effect into the
module constants (memoised) and the measure() cache key carries them, so a
new value re-measures instead of reading stale drift.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL

EARLY_LOOK_S, TAIL_LOOK_S, TAIL_GAP_S = 1500.0, 900.0, 360.0
MIN_RUNS = 20
MIN_MOVING_S = 30 * 60.0
DAYS = 365


def run_stops(t, speed) -> Optional[tuple[np.ndarray, list[tuple[float, float]], float]]:
    from backend.engine import workout_review as WR
    t = np.asarray(t, float)
    s = WR._arr(speed, len(t))
    fin = np.isfinite(t)
    if not fin.any():
        return None
    rel = t - t[fin][0]
    stop = fin & np.isfinite(s) & (s <= WR.STOP_KMH)
    return rel, WR._stop_segments(rel, stop), float(np.nanmax(rel))


def early_value(segs: list) -> Optional[float]:
    starts = [a for a, _ in segs if a < EARLY_LOOK_S]
    return max(starts) if starts else None


def tail_value(segs: list, end: float) -> Optional[float]:
    clus = []
    for a, b in reversed(segs):
        if a < end - TAIL_LOOK_S or (clus and clus[0][0] - b > TAIL_GAP_S):
            break
        clus.insert(0, (a, b))
    return (end - clus[0][0]) if clus else None


def _road_runs(ds, today: dt.date):
    from backend.engine.overview import category
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today or dt.date.today())
    for w in ds.workouts:
        if w.sport != "run" or not (tday - DAYS < w.day <= tday + 1) or category(w) != "road":
            continue
        dur = w.metrics.get("duration") or 0
        if dur < MIN_MOVING_S:
            continue
        yield w


def _values(ds, today, which: str) -> list[float]:
    from backend.engine import workout_review as WR
    out = []
    for w in _road_runs(ds, today):
        try:
            s = WR._samples(ds, w)
            r = run_stops(s["t"], s["speed"]) if s else None
        except Exception:                   # noqa: BLE001 — one unreadable file
            r = None
        if r is None:
            continue
        rel, segs, end = r
        v = early_value(segs) if which == "early" else tail_value(segs, end)
        if v is not None:
            out.append(float(v))
    return out


def _p95_fit(vals: list[float]) -> Optional[CAL.Fit]:
    if len(vals) < MIN_RUNS:
        return None
    v = float(np.percentile(vals, 95))
    return CAL.Fit(v, float(np.std(vals)) / math.sqrt(len(vals)), len(vals))


def fit_early(ds, today):
    return _p95_fit(_values(ds, today, "early"))


def fit_tail(ds, today):
    return _p95_fit(_values(ds, today, "tail"))


def _none(ds=None, today=None):
    return None


CAL.register(CAL.Item(
    name="drift_early_s", label="飄移：暖身段（前段停等）", unit="秒", default=1200.0,
    default_src="推估（市區路跑的典型值 20 分）", k=20, min_n=MIN_RUNS, fit=fit_early, bounds=(600.0, 1500.0), digits=0,
    help="跑步開頭這段時間裡的停等（紅綠燈、等人）都當暖身，飄移從最後一次停等後才開始算。本人值 = 你路跑前段最後一次停等時間的 p95。"))
CAL.register(CAL.Item(
    name="drift_tail_s", label="飄移：回程段（結尾停等）", unit="秒", default=720.0,
    default_src="推估（8 週路跑資料校正）", k=20, min_n=MIN_RUNS, fit=fit_tail, bounds=(300.0, 900.0), digits=0,
    help="結尾這段時間裡的一群停等當回程緩和，不算進飄移。本人值 = 你路跑結尾那群停等起點距結束的 p95。"))
CAL.register(CAL.Item(
    name="drift_max_vi", label="飄移：功率起伏上限（VI）", unit="", default=1.04,
    default_src="推估（單一跑者資料校正）", k=1, min_n=10 ** 9, fit=_none, bounds=(1.01, 1.15), digits=2,
    manual_only=True, help="穩定段的 VI（NP ÷ 平均功率）超過這個值就不算穩定跑，不判讀飄移。"))
CAL.register(CAL.Item(
    name="drift_tau_s", label="飄移：心率延遲 τ", unit="秒", default=60.0,
    default_src="Hunt 2015／2019、Wang & Hunt 2021：55–70 秒", k=1, min_n=10 ** 9, fit=_none,
    bounds=(30.0, 120.0), digits=0, manual_only=True, default_is_literature=True,
    help="心率跟上強度變化的時間常數；算 Pw:HR 時先把功率延遲這麼多。"))
CAL.register(CAL.Item(
    name="walk_max_s", label="飄移：多長的慢段算跑走", unit="秒", default=180.0,
    default_src="推估", k=1, min_n=10 ** 9, fit=_none, bounds=(60.0, 600.0), digits=0, manual_only=True,
    help="速度低於中位 75% 的慢段，最長的一段超過這麼久就當跑走課，不判讀飄移。"))

NAMES = {"DRIFT_EARLY_S": "drift_early_s", "DRIFT_TAIL_S": "drift_tail_s", "DRIFT_MAX_VI": "drift_max_vi",
         "DRIFT_TAU_S": "drift_tau_s", "WALK_MAX_S": "walk_max_s"}


def values() -> dict:
    """{constant name: value in effect}."""
    return {c: CAL.value(n) for c, n in NAMES.items()}
