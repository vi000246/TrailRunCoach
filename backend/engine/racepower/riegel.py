"""
Riegel power-duration relations (SuperPower tasks 7/8/9/10/12, 16 lookup,
5 personal fit) — docs/research/superpower-calculator.md §1.2, §1.4, §2.2.

    P(t) = CP · (t / TTE)^k        k < 0 (Riegel exponent on power)
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np


# ---- tasks 7 / 8 / 9 / 10 / 12 ---------------------------------------------

def power_from_cp(cp: float, t_s: float, tte_s: float, k: float) -> float:
    """Task 8: sustainable power for a race lasting t."""
    return cp * (t_s / tte_s) ** k


def cp_from_prior(p_prior: float, t_prior_s: float, tte_s: float, k: float) -> float:
    """Task 7: CP implied by a prior race (power held for t_prior)."""
    return p_prior * (tte_s / t_prior_s) ** k


def power_from_prior_time(p_prior: float, t_prior_s: float, t_s: float, k: float) -> float:
    """Task 9."""
    return p_prior * (t_s / t_prior_s) ** k


def power_from_prior_distance(p_prior: float, d_prior: float, d: float, k: float) -> dict:
    """Task 10, decision D1: with P ∝ t^k and D ∝ t·P (constant RE),
    P2 = P1·(D2/D1)^(k/(1+k)) and t2 = t1·(D2/D1)^(1/(1+k)).
    Also returns the workbook's approximation P1·(D2/D1)^k for comparison."""
    r = d / d_prior
    return {"power": p_prior * r ** (k / (1.0 + k)),
            "time_ratio": r ** (1.0 / (1.0 + k)),
            "power_workbook": p_prior * r ** k,
            "exponent": k / (1.0 + k)}


def cp_required(p_req: float, t_s: float, tte_s: float, k: float) -> float:
    """Task 12: CP needed to hold p_req for t (no environment factor)."""
    return p_req * (tte_s / t_s) ** k


# ---- task 16: lookup table ---------------------------------------------------

STD_DISTANCES = {           # category -> (standard m, lower threshold, upper max)
    "5k": (5000.0, 4850.0, 5150.0),
    "10k": (10000.0, 9700.0, 10300.0),
    "half": (21097.5, 20465.1, 21730.9),
    "marathon": (42195.0, 40929.15, 43460.8),
}
CATEGORY_LABEL = {"5k": "5K", "10k": "10K", "half": "半馬", "marathon": "全馬"}


def _hms(s: str) -> int:
    parts = [int(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


# Band upper bounds (inclusive, D5) of the PRIOR race time, per prior category.
# Two sets: marathon / half targets use A (10 bands), 10k / 5k targets use B (11).
_BANDS_A = {
    "5k": "14:30 16:30 18:30 20:30 22:30 24:30 26:40 28:45 31:00 2:04:00",
    "10k": "29:50 34:10 38:20 42:40 46:50 51:10 55:25 59:40 1:04:00 4:16:00",
    "half": "1:06:30 1:16:00 1:25:30 1:35:00 1:44:30 1:54:00 2:03:30 2:13:00 2:22:30 9:30:00",
    "marathon": "2:20:00 2:40:00 3:00:00 3:20:00 3:40:00 4:00:00 4:20:00 4:40:00 5:00:00 20:00:00",
}
_BANDS_B = {
    "5k": "14:30 15:10 16:30 18:30 20:30 22:30 24:30 26:40 28:45 31:00 2:04:00",
    "10k": "29:50 31:30 34:10 38:20 42:40 46:50 51:10 55:25 59:40 1:04:00 4:16:00",
    "half": "1:06:30 1:10:10 1:16:00 1:25:30 1:35:00 1:44:30 1:54:00 2:03:30 2:13:00 2:22:30 9:30:00",
    "marathon": "2:20:00 2:28:00 2:40:00 3:00:00 3:20:00 3:40:00 4:00:00 4:20:00 4:40:00 5:00:00 20:00:00",
}
BANDS = {
    "marathon": {c: [_hms(x) for x in v.split()] for c, v in _BANDS_A.items()},
    "half": {c: [_hms(x) for x in v.split()] for c, v in _BANDS_A.items()},
    "10k": {c: [_hms(x) for x in v.split()] for c, v in _BANDS_B.items()},
    "5k": {c: [_hms(x) for x in v.split()] for c, v in _BANDS_B.items()},
}
# most-likely k per band, fastest -> slowest (column K of the Riegels sheet)
MOST_LIKELY_K = {
    "marathon": [-.06, -.07, -.08, -.09, -.09, -.10, -.10, -.11, -.12, -.12],
    "half": [-.05, -.06, -.07, -.08, -.08, -.09, -.09, -.10, -.10, -.10],
    "10k": [-.04, -.05, -.06, -.07, -.08, -.08, -.09, -.09, -.09, -.10, -.10],
    "5k": [-.04, -.05, -.06, -.06, -.07, -.07, -.08, -.08, -.09, -.09, -.09],
}
K_MIN, K_MAX = -0.12, -0.03
ULTRA_K = -0.12


def distance_category(d_m: float) -> Optional[str]:
    """Largest standard threshold ≤ d; None when below 5k or 'non-standard'
    (above 1.03 × the category's standard)."""
    cat = None
    for c, (_lab, lo, _hi) in STD_DISTANCES.items():
        if d_m >= lo:
            cat = c
    if cat is None or d_m > STD_DISTANCES[cat][2]:
        return None
    return cat


def table_k(target_m: float, prior_m: float, prior_s: float) -> dict:
    """Riegel lookup: k for a target distance given a prior race. Ultra targets
    (beyond the marathon) get the table's slowest value, −0.12, with a warning."""
    out = {"k": None, "lo": None, "hi": None, "target_cat": None, "prior_cat": None,
           "band": None, "warning": None}
    prior_cat = distance_category(prior_m)
    out["prior_cat"] = prior_cat
    if target_m > STD_DISTANCES["marathon"][2]:
        out.update(k=ULTRA_K, lo=ULTRA_K, hi=ULTRA_K + 0.01, target_cat="ultra",
                   warning="超出查表範圍（全馬以上）：k 取表中最慢值 −0.12")
        return out
    target_cat = distance_category(target_m)
    out["target_cat"] = target_cat
    if target_cat is None:
        out["warning"] = "目標距離不是標準距離（5K / 10K / 半馬 / 全馬 ±3%），表格不適用"
        return out
    if prior_cat is None:
        out["warning"] = "先前賽事不是標準距離（5K / 10K / 半馬 / 全馬 ±3%），表格不適用"
        return out
    bounds = BANDS[target_cat][prior_cat]
    for i, ub in enumerate(bounds):
        if prior_s <= ub:
            k = MOST_LIKELY_K[target_cat][i]
            out.update(k=k, lo=max(K_MIN, round(k - 0.01, 2)), hi=min(K_MAX, round(k + 0.01, 2)), band=i)
            return out
    out["warning"] = "先前賽事時間超出表格範圍"
    return out


# ---- task 5: personal fit ----------------------------------------------------

def fit_loglog(ts: Sequence[float], ps: Sequence[float]) -> Optional[dict]:
    """OLS of ln P on ln t: slope = k. Returns k, a (intercept), r2, n."""
    t = np.asarray(ts, dtype=float)
    p = np.asarray(ps, dtype=float)
    ok = (t > 0) & (p > 0) & np.isfinite(t) & np.isfinite(p)
    t, p = t[ok], p[ok]
    if len(t) < 2:
        return None
    x, y = np.log(t), np.log(p)
    k, a = np.polyfit(x, y, 1)
    pred = a + k * x
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(((y - pred) ** 2).sum()) / ss_tot if ss_tot > 0 else 1.0
    return {"k": float(k), "a": float(a), "r2": r2, "n": int(len(t)),
            "t_min": float(t.min()), "t_max": float(t.max())}


def personal_k(xs: Sequence[float], ys: Sequence[float], tte_s: float,
               min_s: float = 1200.0, keep_frac: Optional[float] = 0.8) -> Optional[dict]:
    """k from the mean-max envelope between max(TTE, min_s) and the longest
    duration with data (§2.2).

    Deviation (keep_frac): the fit stops at the first duration where the
    envelope falls below keep_frac × its value at the start of the range.
    Past that point the envelope is set by paused or easy outings (a 9-hour
    trail day at 30 W), which are not maximal efforts and would drag k to
    −0.3…−0.7. keep_frac=None reproduces the spec as written."""
    lo = max(float(tte_s or 0), min_s)
    pts = sorted((x, y) for x, y in zip(xs, ys) if x is not None and y is not None and x >= lo and y > 0)
    if not pts:
        return None
    cut = None
    if keep_frac:
        y0 = pts[0][1]
        for i, (x, y) in enumerate(pts):
            if y < keep_frac * y0:
                cut = x
                pts = pts[:i]
                break
    fit = fit_loglog([p[0] for p in pts], [p[1] for p in pts])
    if fit is None:
        return None
    fit["from_s"] = lo
    fit["cut_s"] = cut
    fit["keep_frac"] = keep_frac
    return fit


def extrapolation_warning(target_s: float, longest_s: Optional[float]) -> Optional[str]:
    if longest_s and target_s > 1.5 * longest_s:
        return (f"預估時間 {target_s / 3600:.1f} h 超過你最長有效紀錄 {longest_s / 3600:.1f} h 的 1.5 倍"
                "——Riegel 在此是外插")
    return None


def is_reasonable_k(k: Optional[float]) -> bool:
    return k is not None and math.isfinite(k) and -0.25 <= k <= -0.01
