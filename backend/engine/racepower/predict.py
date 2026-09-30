"""
Race predictions — docs/research/superpower-calculator.md §1.2 (tasks 8, 11,
12, 14, 15, 17), §3.1 (trail) and §3.2 (百岳). Pure functions: every input
is passed in; the API layer fills the athlete's defaults.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from backend.engine.racepower import re as RE
from backend.engine.racepower import riegel as R

SCENARIO_FACTORS = (0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2)
CLIMB_POWER_CAP = 1.10            # heuristic: climbs ≤ 110 % of the race average
ROAD_TASK17_MAX_M = 10300.0       # CP + W′ scenario only makes sense ≤ 10 km


# ---- tasks 14 / 15 / 17 / 11 / 12 --------------------------------------------

def power_for_time(d_m: float, t_s: float, re: float, weight: float) -> float:
    """Task 14: power needed to cover d in t with this RE."""
    return (d_m / t_s) / re * weight


def time_for_power(d_m: float, power: float, re: float, weight: float, m: float = 1.0) -> float:
    """Task 15: time for d at `power` (measured in training conditions) × M."""
    return d_m / (re * power / weight * m)


def cp_wprime_scenario(d_m: float, re: float, weight: float, cp: float, w_prime: float,
                       m: float = 1.0) -> Optional[dict]:
    """Task 17: E = D·W/RE (J), t = (E − W′)/CP, P = W′/t + CP (CP × M on the day)."""
    e = d_m * weight / re
    cpm = cp * m
    t = (e - w_prime) / cpm
    if t <= 0:
        return None
    return {"energy_j": e, "time_s": t, "power": w_prime / t + cpm, "pct_cp": (w_prime / t + cpm) / cp}


def solve_riegel_re(d_m: float, cp: float, tte: float, k: float, re: float, weight: float,
                    m: float = 1.0, t0: float = 3600.0, tol: float = 0.5,
                    max_iter: int = 200) -> dict:
    """Task 11 solved to convergence (D10): P(t) = CP·M·(t/TTE)^k,
    t = D / (RE·P(t)/W). Fixed-point iteration (contracts for |k| < 1), with a
    bisection fallback on f(t) = t − D/(RE·P(t)/W)."""
    def nxt(t):
        return d_m / (re * R.power_from_cp(cp * m, t, tte, k) / weight)
    t, it = float(t0), 0
    for it in range(1, max_iter + 1):
        t2 = nxt(t)
        if not math.isfinite(t2) or t2 <= 0:
            break
        if abs(t2 - t) < tol:
            # one more step past the stopping rule costs nothing and lands
            # within ~0.01 s of the exact fixed point
            t = nxt(t2)
            return {"time_s": t, "power": R.power_from_cp(cp * m, t, tte, k), "iterations": it,
                    "method": "iteration"}
        t = t2
    lo, hi = 1.0, 1e7
    f = lambda x: x - nxt(x)       # noqa: E731
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 0.01:
            break
    t = (lo + hi) / 2
    return {"time_s": t, "power": R.power_from_cp(cp * m, t, tte, k), "iterations": it,
            "method": "bisection"}


def improvement_needed(d_m: float, t_s: float, re: float, weight: float, cp: float,
                       tte: float, k: float, m: float = 1.0) -> dict:
    """Task 12. The workbook uses the day's required power with no M; we
    divide by M so that the required CP is in the same (training) conditions
    as the CP it is compared with — with M = 1 the two agree."""
    p_req = power_for_time(d_m, t_s, re, weight)
    cp_req = R.cp_required(p_req / m, t_s, tte, k)
    return {"power_required": p_req, "cp_required": cp_req, "pct_change": (cp_req - cp) / cp}


def scenarios(d_m: float, t_center: float, cp: float, tte: float, k: float, re: float,
              weight: float, m: float = 1.0, true_km: Optional[float] = None,
              factors: Sequence[float] = SCENARIO_FACTORS) -> list[dict]:
    """Power ↔ time table around a prediction: for each finish time, the power
    it needs (task 14), what you can hold that long (task 8 × M), and the CP
    that would make it your Riegel-sustainable time (task 12)."""
    out = []
    km = true_km if true_km else d_m / 1000.0
    for f in factors:
        t = t_center * f
        need = power_for_time(d_m, t, re, weight)
        can = R.power_from_cp(cp, t, tte, k) * m
        imp = improvement_needed(d_m, t, re, weight, cp, tte, k, m)
        out.append({"factor": f, "time_s": t, "power_needed": need, "power_sustainable": can,
                    "pct_cp_needed": need / cp, "feasible": need <= can + 0.5,
                    "cp_required": imp["cp_required"], "pace_s_per_km": t / km})
    return out


# ---- road / trail -------------------------------------------------------------

def predict_run(*, distance_km: float, cp: float, tte: float, k: float, re: float,
                weight: float, m: float = 1.0, gain_m: float = 0.0,
                effort_divisor: Optional[float] = None, target_time_s: Optional[float] = None,
                w_prime: Optional[float] = None, longest_effort_s: Optional[float] = None,
                road_re: Optional[float] = None, train_cvi: Optional[float] = None) -> dict:
    """Road (effort_divisor None: D = true distance) or trail (D = effort
    distance km + gain/X, RE = the personal trail RE)."""
    trail = effort_divisor is not None
    d_eff_km = RE.effort_km(distance_km, gain_m, effort_divisor) if trail else distance_km
    d_m = d_eff_km * 1000.0
    sol = solve_riegel_re(d_m, cp, tte, k, re, weight, m)
    t, p = sol["time_s"], sol["power"]
    out = {
        "kind": "trail" if trail else "road",
        "distance_km": distance_km, "effort_km": d_eff_km, "effort_divisor": effort_divisor,
        "time_s": t, "power": p, "pct_cp": p / cp, "w_per_kg": p / weight,
        "pace_s_per_km": t / distance_km,
        "effort_pace_s_per_km": t / d_eff_km if trail else None,
        "ep_per_h": d_eff_km / (t / 3600.0),
        "solver": {k_: sol[k_] for k_ in ("iterations", "method")},
        "task8_power": R.power_from_cp(cp, t, tte, k) * m,
        "task15_time_s": time_for_power(d_m, p / m, re, weight, m),
        "scenarios": scenarios(d_m, t, cp, tte, k, re, weight, m, true_km=distance_km),
        "warnings": [],
    }
    w = R.extrapolation_warning(t, longest_effort_s)
    if w:
        out["warnings"].append(w)
    if t > 3 * 3600:
        out["warnings"].append("超過 3 小時的比賽，訓練中很少有真正最大努力的紀錄——個人 k 通常偏負（預估偏慢），可與查表 k 比較")
    if target_time_s:
        imp = improvement_needed(d_m, target_time_s, re, weight, cp, tte, k, m)
        out["target"] = {"time_s": target_time_s, **imp,
                         "pct_cp": imp["power_required"] / cp,
                         "pace_s_per_km": target_time_s / distance_km}
    if not trail and w_prime and d_m <= ROAD_TASK17_MAX_M:
        out["task17"] = cp_wprime_scenario(d_m, re, weight, cp, w_prime, m)
    if trail:
        out["climb_power_cap"] = CLIMB_POWER_CAP * p
        if road_re and train_cvi is not None:
            race_cvi = RE.cvi(gain_m, distance_km)
            adj = RE.cvi_adjust(train_cvi, race_cvi)
            alt = solve_riegel_re(distance_km * 1000.0, cp, tte, k, road_re + adj, weight, m)
            out["cvi_crosscheck"] = {"road_re": road_re, "train_cvi": train_cvi, "race_cvi": race_cvi,
                                     "adjust": adj, "re_used": road_re + adj,
                                     "time_s": alt["time_s"], "power": alt["power"]}
    return out


# ---- 百岳 ---------------------------------------------------------------------

def yamamoto_cc(hours: float, km: float, gain_m: float, loss_m: float) -> float:
    """Yamamoto's course constant (kcal per kg of body + pack)."""
    return 1.8 * hours + 0.3 * km + 10.0 * gain_m / 1000.0 + 0.6 * loss_m / 1000.0


def split_days(days: int, km: float, gain_m: float, loss_m: Optional[float],
               per_day: Optional[list[dict]] = None) -> list[dict]:
    if per_day:
        return [{"km": float(d.get("km") or 0), "gain_m": float(d.get("gain_m") or 0),
                 "loss_m": float(d.get("loss_m") if d.get("loss_m") is not None else (d.get("gain_m") or 0))}
                for d in per_day]
    n = max(1, int(days or 1))
    loss = gain_m if loss_m is None else loss_m
    return [{"km": km / n, "gain_m": gain_m / n, "loss_m": loss / n} for _ in range(n)]


def predict_baiyue(*, day_plan: list[dict], eph: float, weight: float, m: float = 1.0,
                   pack_kg: float = 12.0, hist_pack_kg: float = 5.0, aet: Optional[float] = None,
                   target_moving_h: Optional[float] = None, biggest_day: Optional[dict] = None) -> dict:
    """Walking model: moving h = EP / (EP/h_personal · M · pack factor) per day,
    EP = km + gain/100. Energy = CC · (W + pack) kcal; water 0.7–0.8 ml/kcal."""
    pack_factor = (weight + hist_pack_kg) / (weight + pack_kg)
    rate = eph * m * pack_factor
    rows, tot = [], {"km": 0.0, "gain_m": 0.0, "loss_m": 0.0, "ep": 0.0, "moving_h": 0.0, "kcal": 0.0}
    for i, d in enumerate(day_plan, 1):
        ep = d["km"] + d["gain_m"] / 100.0
        h = ep / rate if rate > 0 else math.nan
        cc = yamamoto_cc(h, d["km"], d["gain_m"], d["loss_m"])
        kcal = cc * (weight + pack_kg)
        row = {"day": i, **d, "ep": ep, "moving_h": h, "ep_per_h": rate, "cc": cc, "kcal": kcal,
               "water_ml": [0.7 * kcal, 0.8 * kcal], "hr_cap": aet, "flags": []}
        if biggest_day:
            if ep > (biggest_day.get("ep") or 0):
                row["flags"].append(f"EP {ep:.1f} 超過你最大的一天 {biggest_day['ep']:.1f}")
            if d["gain_m"] > (biggest_day.get("gain_m") or 0):
                row["flags"].append(f"爬升 {d['gain_m']:.0f} m 超過你最大的一天 {biggest_day['gain_m']:.0f} m")
            if h > (biggest_day.get("moving_h") or 0):
                row["flags"].append(f"移動時間 {h:.1f} h 超過你最長的一天 {biggest_day['moving_h']:.1f} h")
        rows.append(row)
        for k_ in ("km", "gain_m", "loss_m", "ep"):
            tot[k_] += d[k_] if k_ in d else ep
        tot["moving_h"] += h
        tot["kcal"] += kcal
    tot["water_ml"] = [0.7 * tot["kcal"], 0.8 * tot["kcal"]]
    out = {"kind": "baiyue", "days": rows, "total": tot, "eph_personal": eph, "eph_used": rate,
           "pack_factor": pack_factor, "pack_kg": pack_kg, "hist_pack_kg": hist_pack_kg, "M": m,
           "time_s": tot["moving_h"] * 3600.0,
           "warnings": ["百岳是步行模型：以你過去登山日的 EP/h（km + 爬升/100 每小時）推估移動時間，"
                        "不含休息、拍照、排隊；環境係數與背負係數都是保守假設"]}
    if target_moving_h:
        need = tot["ep"] / target_moving_h
        out["target"] = {"moving_h": target_moving_h, "ep_per_h_needed": need,
                         "vs_personal": need / (eph * m * pack_factor)}
    return out
