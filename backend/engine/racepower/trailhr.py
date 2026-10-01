"""
Trail race time from HR and terrain, not power (2026-10-01).

Why: the back-test showed the power-envelope capacity off by +46 % power /
−35 % time on trail, while the terrain model (effort-km / RE, given the
actual power) is ~10 % off. On trail, power is a poor effort signal (low on
descents; fatigue turns flats into walking), so the athlete's effort is read
from HR (activity_tags.effort_hr) and the time from the effort-km pace that
HR level buys on their own trail runs.

Model (all 自組, 推估 until the back-test validates it):

1. Per past trail run (outdoor, ≥ 45 min moving, with HR): effort distance
   E = km + gain / 153 (algorithms.effort SIMPLE_FORMULAS "fitted_run", the
   least-squares divisor on this athlete's runs), moving time T (speed > 1
   km/h), x = moving HR ÷ the LTHR of the run's own date
   (athlete.thresholds_as_of), v = E / T (effort-km per moving hour).
2. Durability (the athlete: "I can't hold full effort for a whole long
   race"): per run ≥ 2 h moving, panels.workout.durability on the moving-
   time axis with output = effort-km speed (Δd + max(Δz, 0)/153 per second)
   and HR — the rolling output/HR ratio as % of the first window [coach
   practice: Uphill Athlete / Maunder et al. 2021 "durability"]. Its decline
   per hour after T0 = 1 h (the user's "beyond ~1–2 h"; 1 h 自組) is the run's
   δ; the personal δ is the median over runs, clamped to 0…0.15 /h. Speed
   at a given HR then is v(t) = v₀·(1 − δ·(t − T0)⁺), whose mean over a run
   of length T is D̄(T) = 1 − δ·(T − T0)²/(2T) for T > T0.
3. v₀(x) = a + b·x, OLS across runs on the durability-corrected
   v₀ = v / D̄(T). Needs ≥ 6 runs and b > 0; else v₀ = c·x with
   c = median(v₀/x) (proportional fallback).
4. Prediction for a course of effort distance E at HR level x: T solves
   T = E / (v₀(x)·D̄(T)) (fixed point).
5. The race HR level x* (what "全力" means for this athlete on trail): the
   median x of their earlier trail races (activity type 比賽) and auto /
   user 全力 trail runs ≥ 90 min; none → 0.90 (Friel Z3 lower bound, the
   activity_tags max_hr_frac). The planner's effort target f scales it
   (x = f·x*, 自組).
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional

import numpy as np

TRAILHR = {
    "divisor": 153.0,          # SIMPLE_FORMULAS["fitted_run"]
    "min_moving_s": 45 * 60.0,  # athlete.TRAIL_MIN_MOVING_S
    "dur_min_s": 2 * 3600.0,   # 自組: durability only from runs ≥ 2 h moving
    "t0_h": 1.0,               # 自組: the decline starts after 1 h
    "delta_max": 0.15,         # 自組 clamp (/h)
    "min_runs": 6,             # 自組
    "window_days": 365,        # as RE_WINDOW_DAYS
    "race_min_s": 90 * 60.0,   # maximal.MAXIMAL["trail_min_s"]
    "x_default": 0.90,         # Friel Z3 lower bound (activity_tags.AUTO_EFFORT max_hr_frac)
    "dbar_min": 0.5,
}
SOURCE = ("越野心率配速模型：effort km（km + 爬升/153）÷ 移動時間 對 移動心率/LTHR 的個人回歸，"
          "加耐久衰減（panels.workout.durability，1 h 後每小時下降）；自組，推估")


def dbar(T_h: float, delta: float, t0: float = TRAILHR["t0_h"]) -> float:
    """Mean speed multiplier over a run of T_h hours."""
    if T_h <= t0 or delta <= 0:
        return 1.0
    return max(TRAILHR["dbar_min"], 1.0 - delta * (T_h - t0) ** 2 / (2.0 * T_h))


def run_point(km, gain_m, moving_s, hr_avg, lthr) -> Optional[dict]:
    k = TRAILHR
    if not km or not moving_s or moving_s < k["min_moving_s"] or not hr_avg or not lthr:
        return None
    e = km + (gain_m or 0.0) / k["divisor"]
    T = moving_s / 3600.0
    return {"x": hr_avg / lthr, "v": e / T, "T_h": T, "eff_km": e}


def durability_delta(points, t0: float = TRAILHR["t0_h"]) -> Optional[float]:
    """One run's decline per hour after t0 from durability() points
    [[hours, pct], ...]: OLS of pct on hours over hours ≥ t0, divided by the
    fitted value at t0. Positive = slowing."""
    if not points:
        return None
    p = np.asarray(points, float)
    m = p[:, 0] >= t0
    if m.sum() < 10 or np.ptp(p[m, 0]) < 0.5:
        return None
    x, y = p[m, 0] - t0, p[m, 1]
    b, a = np.polyfit(x, y, 1)
    return float(-b / a) if a > 0 else None


def effort_speed_series(t, d_m, z, moving, divisor: float = TRAILHR["divisor"]):
    """(moving-time axis s, effort-km speed m/s) for durability(): only
    moving samples, so rests do not count as time."""
    t = np.asarray(t, float)
    dt = np.diff(t, prepend=t[0])
    dt[~np.isfinite(dt) | (dt < 0) | (dt > 30)] = 0.0
    dd = np.diff(np.asarray(d_m, float), prepend=d_m[0])
    dz = np.diff(np.nan_to_num(np.asarray(z, float)), prepend=np.nan_to_num(z[0]))
    mv = np.asarray(moving, bool) & (dt > 0)
    tm = np.cumsum(np.where(mv, dt, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        # effort metres: d + gain·1000/divisor (E km = km + gain m / divisor)
        es = (np.clip(dd, 0, None) + np.clip(dz, 0, None) * 1000.0 / divisor) / dt
    es = np.where(mv & np.isfinite(es), es, np.nan)
    return tm[mv], es[mv], mv


def fit(points: list[dict], delta: Optional[float]) -> dict:
    """v₀(x) from run points (see the module docstring)."""
    k = TRAILHR
    d = float(min(k["delta_max"], max(0.0, delta or 0.0)))
    pts = [p for p in points if p]
    out = {"n": len(pts), "delta": d, "delta_raw": delta, "a": None, "b": None, "c": None,
           "kind": None, "valid": False, "source": SOURCE, "label": "推估"}
    if not pts:
        return out
    x = np.array([p["x"] for p in pts])
    v0 = np.array([p["v"] / dbar(p["T_h"], d) for p in pts])
    out["c"] = float(median(v0 / x))
    if len(pts) >= k["min_runs"] and np.ptp(x) > 0:
        b, a = np.polyfit(x, v0, 1)
        if b > 0:
            res = v0 - (a + b * x)
            ss = float(((v0 - v0.mean()) ** 2).sum())
            out.update(a=float(a), b=float(b), kind="ols", valid=True,
                       r2=1.0 - float((res ** 2).sum()) / ss if ss > 0 else None,
                       x_lo=float(x.min()), x_hi=float(x.max()))
            return out
    out.update(kind="proportional", valid=len(pts) >= 3)
    return out


def v0_at(m: dict, x: float) -> Optional[float]:
    if m.get("kind") == "ols":
        return m["a"] + m["b"] * x
    if m.get("c"):
        return m["c"] * x
    return None


def predict_time(m: dict, eff_km: float, x: float, delta: Optional[float] = None) -> Optional[float]:
    """Moving seconds for eff_km at HR level x (fixed point on D̄(T))."""
    v = v0_at(m, x)
    if not v or v <= 0 or not eff_km:
        return None
    d = m["delta"] if delta is None else delta
    T = eff_km / v
    for _ in range(100):
        T2 = eff_km / (v * dbar(T, d))
        if abs(T2 - T) < 1e-6:
            break
        T = T2
    return T * 3600.0


def race_level(xs) -> tuple[float, str]:
    v = [float(x) for x in xs if x and math.isfinite(x)]
    if v:
        return float(median(v)), f"之前 {len(v)} 場越野比賽／全力的移動心率中位數"
    return TRAILHR["x_default"], "沒有之前的越野比賽：用 0.90 × LTHR（Friel Z3 下緣）"
