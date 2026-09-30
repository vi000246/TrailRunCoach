"""
百岳 v2 walking model — docs/research/racepower-v2.md §3 F12, F13, F17, §4.4, §7.

    tᵢ = dᵢ / (λ_h · v_h(gᵢ) · pack · Aᵢ · f_day(n) / ηᵢ)

λ_h = 1.0 is your usual speed; v_h is the personal Tobler-shrunk speed
(grade_model.HikeSpeed, F13); Aᵢ the altitude ratio against where those
speeds were walked (F14); pack the v1 linear load factor (F12 Pandolf is
not yet verified, see below); ηᵢ the terrain factor; f_day the multi-day
fatigue (F17).
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional, Sequence

TERRAIN_ETA = {"normal": 1.0, "gravel": 1.3, "bamboo": 1.35, "offpath": 1.67}
TERRAIN_LABEL = {"normal": "一般", "gravel": "碎石", "bamboo": "箭竹", "offpath": "無路"}
MIN_TRIPS = 3
DEFAULT_MOVING_RATIO = 0.8


def pandolf(weight: float, load: float, v: float, grade_pct: float, eta: float = 1.0) -> float:
    """F12 Pandolf, Givoni & Goldman 1977 (J Appl Physiol 43:577–581):
    M = 1.5W + 2.0(W+L)(L/W)² + η(W+L)(1.5V² + 0.35·V·G) watts (W, L kg;
    V m/s; G %). The formula is taken from a secondary source (Wikipedia via
    effort-distance-formulas.md §7), not the paper — 待驗證. Not used for the
    primary times: the v1 linear pack factor stays until it is checked."""
    return 1.5 * weight + 2.0 * (weight + load) * (load / weight) ** 2 + \
        eta * (weight + load) * (1.5 * v * v + 0.35 * v * grade_pct)


def pandolf_speed(weight: float, load0: float, load: float, v0: float, grade_pct: float,
                  eta: float = 1.0) -> float:
    """Speed with `load` at the same metabolic rate as v0 with load0 (F12
    inverse, bisection). 待驗證 like `pandolf`; uphill / level only."""
    target = pandolf(weight, load0, v0, grade_pct, eta)
    lo, hi = 0.0, max(v0 * 3, 3.0)
    if pandolf(weight, load, lo, grade_pct, eta) >= target:
        return 0.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if pandolf(weight, load, mid, grade_pct, eta) > target:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def pack_factor(weight: float, hist_pack_kg: float, pack_kg: float) -> float:
    """v1 load factor (W + L₀)/(W + L): energy ∝ total mass (Yamamoto). A
    linear approximation of Pandolf, labelled 假設 on the page."""
    return (weight + hist_pack_kg) / (weight + pack_kg)


def day_fatigue(hdays: Sequence[dict]) -> dict:
    """F17: day-n EP/h ÷ day-1 EP/h, median over the athlete's multi-day
    trips. No study was found for this (無來源): with fewer than 3 trips every
    factor is 1.0 and a warning is returned (V-F17)."""
    trips: dict[str, dict] = {}
    for d in hdays:
        if (d.get("days") or 1) > 1 and d.get("day"):
            trips.setdefault(d["trip"], {})[d["day"]] = d["ep_per_h"]
    trips = {k: v for k, v in trips.items() if 1 in v and len(v) >= 2}
    if len(trips) < MIN_TRIPS:
        return {"factors": {}, "trips": len(trips),
                "warning": f"多日行程只有 {len(trips)} 趟（需要 ≥ {MIN_TRIPS}）：沒有研究或個人資料可估多日疲勞，每天都用 1.0"}
    by_n: dict[int, list] = {}
    for v in trips.values():
        for n, e in v.items():
            if n > 1:
                by_n.setdefault(n, []).append(e / v[1])
    return {"factors": {n: float(median(x)) for n, x in by_n.items()}, "trips": len(trips), "warning": None}


def fatigue_of(n: int, fat: dict) -> float:
    fs = fat.get("factors") or {}
    if n <= 1 or not fs:
        return 1.0
    if n in fs:
        return fs[n]
    return fs[max(fs)]


def hike_rows(segs: Sequence[dict], v_of, lam: float, pack: float, alt: Sequence[float],
              fat: dict) -> list[dict]:
    """Per-segment walking time at speed multiple λ_h (§4.4)."""
    rows = []
    for s, a in zip(segs, alt):
        eta = TERRAIN_ETA.get(s.get("terrain") or "normal", 1.0)
        fd = fatigue_of(int(s.get("day") or 1), fat)
        v = lam * v_of(s["grade"]) * pack * a * fd / eta
        rows.append({"v": v, "t": s["dist_m"] / v if v > 0 else math.inf, "eta": eta, "f_day": fd, "A": a})
    return rows


def naismith_h(km: float, gain_m: float) -> float:
    """Naismith 1892: 5 km/h plus 1 h per 600 m climbed (經驗法則, cross-check)."""
    return km / 5.0 + gain_m / 600.0


def langmuir_h(segs: Sequence[dict]) -> float:
    """Naismith with Langmuir's descent corrections per segment: 5–12° down
    −10 min per 300 m, > 12° +10 min per 300 m (經驗法則, cross-check)."""
    h = 0.0
    for s in segs:
        h += s["dist_m"] / 5000.0 + max(0.0, s.get("gain_m") or 0.0) / 600.0
        loss = s.get("loss_m") or 0.0
        deg = math.degrees(math.atan(abs(min(0.0, s["grade"]))))
        if 5 <= deg <= 12:
            h -= loss / 300.0 * 10 / 60
        elif deg > 12:
            h += loss / 300.0 * 10 / 60
    return h


def moving_ratio(achievements_rows: Sequence[dict]) -> tuple[float, str]:
    """Moving ÷ elapsed time over single-day hikes, for the clock ETA."""
    rs = [r["moving_s"] / r["elapsed_s"] for r in achievements_rows
          if r.get("moving_s") and r.get("elapsed_s") and r["elapsed_s"] < 20 * 3600 and r["elapsed_s"] > 0]
    rs = [x for x in rs if 0.3 < x <= 1.0]
    if len(rs) < 3:
        return DEFAULT_MOVING_RATIO, "假設 80 %（登山紀錄不足）"
    return float(median(rs)), f"你過去 {len(rs)} 次單日登山的移動 ÷ 總時間中位數"
