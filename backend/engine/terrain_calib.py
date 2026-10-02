"""
Terrain per athlete (generalize-athlete plan B6, engine/calibrate.py item).

G3 — climb_divisor_run: metres of ascent that cost one flat km on the
     athlete's runs (effort km = km + gain / divisor; algorithms/effort.py
     "fitted_run", racepower/trailhr TRAILHR divisor). Fit: least squares on
     the runs of the last year, moving_h · v − gain · u = km for one speed v
     and u = 1 / divisor (flat runs anchor v, climbing runs give u). At least
     MIN_CLIMB_RUNS runs with ≥ 20 m/km; shrunk toward ITRA's 100 with
     k = 10 (推估). The author's 430 runs gave 153.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL

DEFAULT = 100.0                  # ITRA km-effort
MIN_CLIMB_RUNS = 8
CLIMB_M_PER_KM = 20.0            # algorithms/classify.TRAIL_CLIMB_RATE_M_PER_KM
MIN_MOVING_S = 20 * 60.0
DAYS = 365


def fit_rows(rows: list[tuple[float, float, float]]) -> Optional[CAL.Fit]:
    """rows = [(km, gain_m, moving_h)]."""
    rows = [r for r in rows if r[0] > 0 and r[2] > 0 and r[1] >= 0]
    climb = [r for r in rows if r[1] / r[0] >= CLIMB_M_PER_KM]
    if len(climb) < MIN_CLIMB_RUNS or len(rows) < MIN_CLIMB_RUNS + 2:
        return None
    A = np.array([[r[2], -r[1]] for r in rows], float)
    y = np.array([r[0] for r in rows], float)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    v, u = float(coef[0]), float(coef[1])
    if not (v > 0 and u > 0):
        return None
    res = y - A @ coef
    s2 = float(res @ res) / max(1, len(rows) - 2)
    try:
        cov = s2 * np.linalg.inv(A.T @ A)
        se_u = math.sqrt(max(cov[1, 1], 0.0))
    except np.linalg.LinAlgError:
        se_u = None
    d = 1.0 / u
    se_d = None if not se_u else se_u / (u * u)          # delta method
    return CAL.Fit(d, se_d, len(climb))


def fit_divisor(ds, today: Optional[dt.date]):
    from backend.engine.wko5expr.dataset import date_to_day
    today = today or dt.date.today()
    tday = date_to_day(today)
    rows = []
    for w in ds.workouts:
        if w.sport != "run" or not (tday - DAYS < w.day <= tday + 1):
            continue
        st = (w.sport_type or "").lower()
        if "treadmill" in st or "indoor" in st:
            continue
        m = w.metrics
        km, gain = m.get("distance"), m.get("climbing")
        mv = m.get("movingduration") or m.get("duration")
        if not km or gain is None or not mv or mv < MIN_MOVING_S:
            continue
        rows.append((float(km), float(gain), float(mv) / 3600.0))
    return fit_rows(rows)


CAL.register(CAL.Item(
    name="climb_divisor_run", label="爬升換算（每幾 m 爬升 = 1 km）", unit="m", default=DEFAULT,
    default_src="ITRA km-effort（健行筆記也用）", k=10, min_n=MIN_CLIMB_RUNS, fit=fit_divisor,
    bounds=(50.0, 300.0), digits=0, default_is_literature=True,
    help="越野的「等效距離」= 公里 + 爬升 ÷ 這個數。本人值 = 你一年內的跑步（平路定速度、爬坡定爬升成本）最小平方擬合。"))


def divisor() -> float:
    return CAL.value("climb_divisor_run")
