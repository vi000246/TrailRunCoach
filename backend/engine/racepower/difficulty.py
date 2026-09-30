"""
Sustainable power and the effort bar — docs/research/racepower-v2.md §2.2,
§3 F1–F6, §5. Pure functions.

Each formula states its source and its verification status (§3A):
已驗證 = checked against the source and a worked example is a test;
待驗證 = our own combination or not yet back-tested on the athlete's races —
it may shape the page but is labelled 推估 there until §3B passes.

(Not to be confused with backend/engine/algorithms/effort.py, which is the
effort-distance formulas.)
"""
from __future__ import annotations

import math
from typing import Optional

SHORT_MAX_S = 1200.0          # F2 range upper end (≈ 2–20 min)
CP_UNCERTAINTY = 0.03         # ±3 % CP band on the bar (§5.1)
K_UNCERTAINTY = 0.01          # ±0.01 k band (the table's own likely range)

# (key, label, lower bound of f). The rule (user decision 2026-09-30:
# 輕鬆 <80, 穩定 80–90, 吃力 90–97, 極限 97–100, 超出 >100):
# the lower bounds 0.80 / 0.90 / 0.97 are inclusive (0.80 → 穩定), and
# 1.00 itself is still 極限 — 超出 is strictly above 100 %.
LABELS = (
    ("easy", "輕鬆", 0.0),
    ("steady", "穩定", 0.80),
    ("hard", "吃力", 0.90),
    ("max", "極限", 0.97),
    ("over", "超出", 1.00),
)
_OVER_EPS = 1e-9


def p_sus(t_s: float, cp: float, w_prime: Optional[float], tte: float, k: float,
          m: float = 1.0) -> float:
    """Sustainable power for a race lasting t_s, in the conditions of M = m.

    F1 (T ≥ TTE): CP·M·(T/TTE)^k — Riegel power law as the SuperPower workbook
       writes it (task 8; Riegel 1981; Vandewalle 2018 compared the models).
       已驗證 10–42 km (V-F1: Stryd's official table rebuilt with k −0.07 within
       0.3 points); > 3 h and ultras 待驗證 (§3B back-test).
    F2 (T ≤ b): CP·M + W′/T — the 2-parameter CP model (Jones & Vanhatalo 2017,
       Sports Med 47:65–78). 已驗證 (V-F2: CP 285, W′ 10800, 600 s → 303 W).
    F3 (b < T < TTE): linear in log T between F2(b) and F1(TTE) — our own
       bridge that removes the W′/TTE step between F1 and F2 (自組, 待驗證;
       V-F3 checks continuity). b = min(1200 s, TTE/2).
    Without W′ the F1 power law is used for every T (v1 behaviour)."""
    t = max(float(t_s), 1.0)
    cpm = cp * m
    if t >= tte or not w_prime:
        return cpm * (t / tte) ** k
    b = min(SHORT_MAX_S, 0.5 * tte)
    if t <= b:
        return cpm + w_prime / t
    lo, hi = cpm + w_prime / b, cpm
    x = (math.log(t) - math.log(b)) / (math.log(tte) - math.log(b))
    return lo + (hi - lo) * x


def t_lim(power: float, cp: float, w_prime: Optional[float], tte: float, k: float,
          m: float = 1.0, t_max: float = 1e7) -> float:
    """Time to exhaustion at `power` — F4, the inverse of F1–F3 by bisection on
    log T (p_sus is strictly decreasing). In the F2 range it equals
    W′/(P − CP·M) (Jones & Vanhatalo 2017). 已驗證 (math; V-F4 round trip and
    the 285 W / 15 kJ / 105 % → 1053 s hand value). Returns t_max when the
    power is sustainable longer than that."""
    if power <= p_sus(t_max, cp, w_prime, tte, k, m):
        return t_max
    lo, hi = 1.0, t_max
    if power >= p_sus(lo, cp, w_prime, tte, k, m):
        return lo
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if p_sus(mid, cp, w_prime, tte, k, m) > power:
            lo = mid
        else:
            hi = mid
        if hi / lo < 1 + 1e-10:
            break
    return math.sqrt(lo * hi)


def label_of(f: float) -> dict:
    """Effort label for f (§5.2 cut-points 80 / 90 / 97 / 100 %). The
    cut-points are our own (自組) — anchored on Smyth & Muniz-Pumares 2020
    (amateur marathoners finish at 84.8 % ± 13.6 % of critical speed) and the
    ±0.01 k band — and stay 待驗證 until the §3B A-race check passes."""
    if f > 1.0 + _OVER_EPS:
        key, name, _ = LABELS[-1]
        return {"key": key, "label": name}
    for key, name, lo in reversed(LABELS[:-1]):
        if f >= lo - _OVER_EPS:
            return {"key": key, "label": name}
    return {"key": LABELS[0][0], "label": LABELS[0][1]}


def endurance_multiple(f: float, k: float) -> Optional[float]:
    """F6: m = f^(1/k) — how many times the race duration this average power
    could be held (algebra of F1: f = (mT/T)^k). 已驗證 (math; V-F6:
    k −0.07, f 0.90 → 4.50, 0.95 → 2.08)."""
    if f <= 0 or k >= 0:
        return None
    return f ** (1.0 / k)


def effort(p_train: float, t_s: float, cp: float, w_prime: Optional[float], tte: float,
           k: float) -> dict:
    """F5: f = P̄_train / P_sus,train(T) — race-day power converted back to
    training conditions (Σ(Pᵢ/Mᵢ)tᵢ/T, done by the caller) against the curve in
    the same conditions. 自組, 待驗證 (§3B: A races should land at 0.97–1.03).
    Returns f, the label, F6's multiple, t_lim and the uncertainty band from
    CP ± 3 % and k ± 0.01 (§5.1)."""
    ps = p_sus(t_s, cp, w_prime, tte, k)
    f = p_train / ps
    fs = []
    for dc in (-CP_UNCERTAINTY, CP_UNCERTAINTY):
        for dk in (-K_UNCERTAINTY, K_UNCERTAINTY):
            fs.append(p_train / p_sus(t_s, cp * (1 + dc), w_prime, tte, k + dk))
    tl = t_lim(p_train, cp, w_prime, tte, k)
    return {"f": f, **label_of(f), "p_sus": ps, "multiple": endurance_multiple(f, k),
            "t_lim_s": tl, "band": [min(fs), max(fs)],
            "cuts": [{"key": key, "label": name, "from": lo} for key, name, lo in LABELS]}


def hike_cuts(q25: float, med: float, q75: float, p90: float) -> list[float]:
    """百岳 bar cut-points (§5.1): the athlete's own EP/h distribution as ratios
    to the median — q25/med, 1, q75/med, p90/med. Definition, 已驗證 (V-HE)."""
    return [q25 / med, 1.0, q75 / med, p90 / med]


def hike_effort(r: float, cuts: list[float]) -> dict:
    """r = speed needed ÷ your usual speed (1.0 = your median day). Labels:
    輕鬆 ≤ q25, 穩定 ≤ 1.0, 吃力 ≤ q75, 極限 ≤ p90, 超出 > p90 (§5.2)."""
    names = [(k_, n) for k_, n, _ in LABELS]
    for (key, name), c in zip(names, cuts):
        if r <= c + _OVER_EPS:
            return {"r": r, "key": key, "label": name, "cuts": cuts}
    return {"r": r, "key": names[-1][0], "label": names[-1][1], "cuts": cuts}
