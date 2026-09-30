"""
Per-segment power allocation and the three solve modes — docs/research/
racepower-v2.md §3 F7–F11b, F15, F16, §4, §6.

    uᵢ = h(gᵢ) · aᵢ · s(τᵢ)          weights: hill elasticity × altitude × ramp
    Pᵢ = λ·uᵢ (or the locked value)
    vᵢ = min(RE(gᵢ)·Pᵢ/W, v_max(gᵢ)) (capped segments drop to the cap's power)
    tᵢ = dᵢ/vᵢ,  T = Σtᵢ,  P̄ = ΣPᵢtᵢ/T,  P̄_train = Σ(Pᵢ/Mᵢ)tᵢ/T

Modes: power (λ so that P̄ = P*, F16), time (λ so that T = T*), auto (λ so
that P̄_train = f*·P_sus(T), §4.3). λ is found by bisection on log λ; T is
monotone in λ.

Every allocation rule here is our own combination (自組) and 待驗證 until the
§3B back-test passes for the course's category; the page labels segment
targets 推估 until then. The W′ budget (F11b) is plain algebra of the CP model
and 已驗證. The optional W′ curve defaults to WKO5's own dfrc (a port, so it
matches the app's other charts); Skiba's W′bal (F11) is the literature
alternative, display-only because its τ coefficients are quoted from memory
(待驗證).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

UP_REF_GRADE = 0.08          # van Rassel 2026's steepest validated grade
DOWN_REF_GRADE = 0.10        # Townshend 2010's downhill magnitude
ALPHA_DEFAULT, ALPHA_MAX = 0.05, 0.12
BETA_DEFAULT = 0.10
SIGMA_MAX = 0.05
WPRIME_FRAC = 0.75
TAU_ITERS = 8


@dataclass
class RunModel:
    weight: float
    re: Callable[[float], float]
    v_max: Callable[[float], Optional[float]] = lambda g: None


def hill_factor(g: float, alpha: float = ALPHA_DEFAULT, beta: float = BETA_DEFAULT) -> float:
    """F10 h(g): uphill 1 + α·min(g/0.08, 1), downhill 1 − β·min(−g/0.10, 1).

    Grounds (§2.1.4): Liedl, Swain & Branch 1999 (MSSE 31:1472–1477) — ±5 %
    alternating power at the same mean left VO2 / HR / lactate unchanged;
    Swain 1997 (MSSE 29:1104–1108) — varying power with grade saves time;
    Townshend et al. 2010 (MSSE 42:160–169) — runners spontaneously go to
    100.4 % VT uphill vs 89.3 % level vs 78.9 % downhill (ratios 1.124 /
    0.884). Our rule (自組), 待驗證: cycling / VO2 evidence, not Stryd power."""
    if g > 0:
        return 1.0 + alpha * min(g / UP_REF_GRADE, 1.0)
    if g < 0:
        return 1.0 - beta * min(-g / DOWN_REF_GRADE, 1.0)
    return 1.0


def ramp(tau: float, sigma: float) -> float:
    """F15 s(τ) = 1 + σ(1 − 2τ): σ > 0 front-loaded, σ < 0 negative split; its
    mean over τ ∈ [0, 1] is exactly 1 (已驗證, V-F15). Magnitude advice (2 %)
    is 待驗證 — pacing evidence favours small variation (Abbiss & Laursen 2008;
    Díaz et al. 2018; Santos-Lozano et al. 2014; Suter et al. 2020)."""
    return 1.0 + sigma * (1.0 - 2.0 * tau)


def altitude_weights(segs: Sequence[dict]) -> list[float]:
    """aᵢ = Mᵢ / M̄ (distance-weighted): power drops in proportion up high."""
    ms = [s.get("M", 1.0) for s in segs]
    ds = [s["dist_m"] for s in segs]
    mbar = sum(m * d for m, d in zip(ms, ds)) / sum(ds)
    return [m / mbar for m in ms]


def course_time(lam: float, segs: Sequence[dict], model: RunModel, alpha: float = ALPHA_DEFAULT,
                beta: float = BETA_DEFAULT, sigma: float = 0.0, locks: Optional[dict] = None) -> dict:
    """Evaluate the course at scale λ (§4 step 5). `segs` need dist_m, grade
    and M. `locks` = {segment index (0-based): power W}. The ramp's τᵢ depends
    on the times, so it is iterated to |ΔT| < 0.5 s."""
    locks = locks or {}
    a = altitude_weights(segs)
    total_d = sum(s["dist_m"] for s in segs)
    acc, taus = 0.0, []
    for s in segs:
        taus.append((acc + 0.5 * s["dist_m"]) / total_d)
        acc += s["dist_m"]
    last_T = None
    rows = []
    for _ in range(TAU_ITERS):
        rows = []
        for i, s in enumerate(segs):
            g = s["grade"]
            u = hill_factor(g, alpha, beta) * a[i] * ramp(taus[i], sigma)
            p = float(locks[i]) if i in locks else lam * u
            re = model.re(g)
            v = re * p / model.weight
            vmax = model.v_max(g)
            capped = False
            if vmax and v > vmax:
                v, capped = vmax, True
                p = vmax * model.weight / re
            t = s["dist_m"] / v if v > 0 else math.inf
            rows.append({"P": p, "v": v, "t": t, "u": u, "re": re, "capped": capped, "locked": i in locks})
        T = sum(r["t"] for r in rows)
        if sigma == 0 or (last_T is not None and abs(T - last_T) < 0.5):
            break
        last_T = T
        acc = 0.0
        taus = []
        for r in rows:
            taus.append((acc + 0.5 * r["t"]) / T)
            acc += r["t"]
    T = sum(r["t"] for r in rows)
    p_bar = sum(r["P"] * r["t"] for r in rows) / T
    p_train = sum(r["P"] / s.get("M", 1.0) * r["t"] for r, s in zip(rows, segs)) / T
    return {"T": T, "P": p_bar, "P_train": p_train, "rows": rows, "lam": lam}


def _bisect(fn, lo: float, hi: float, iters: int = 200) -> float:
    """Root of an increasing fn on log λ ∈ [lo, hi] (clamped to the ends)."""
    flo, fhi = fn(lo), fn(hi)
    if flo >= 0:
        return lo
    if fhi <= 0:
        return hi
    for _ in range(iters):
        mid = math.sqrt(lo * hi)
        if fn(mid) > 0:
            hi = mid
        else:
            lo = mid
        if hi / lo < 1 + 1e-12:
            break
    return math.sqrt(lo * hi)


def solve_power_mode(p_target: float, segs, model: RunModel, **kw) -> dict:
    """Mode B / F16: λ so that the time-weighted average power equals the
    target (conservation error < 0.1 W, V-F16). F16 is 自組 (Liedl 1999: same
    mean, ±5 % → same physiology), 待驗證 by §3B."""
    lam = _bisect(lambda l: course_time(l, segs, model, **kw)["P"] - p_target, 0.05 * p_target, 20 * p_target)
    return course_time(lam, segs, model, **kw)


def solve_time_mode(t_target: float, segs, model: RunModel, p_guess: float, **kw) -> dict:
    """Mode A: λ so that the finish time equals T* (T is decreasing in λ)."""
    lam = _bisect(lambda l: t_target - course_time(l, segs, model, **kw)["T"], 0.02 * p_guess, 20 * p_guess)
    return course_time(lam, segs, model, **kw)


def solve_auto_mode(f_target: float, psus: Callable[[float], float], segs, model: RunModel,
                    p_guess: float, **kw) -> dict:
    """Mode C: λ so that P̄_train = f*·P_sus,train(T) (§4.3). On a single flat
    segment with RE(0) = RE_road and M constant this is exactly v1's task 11
    fixed point `solve_riegel_re` (T1 regression, 0.5 s)."""
    def fn(l):
        r = course_time(l, segs, model, **kw)
        return r["P_train"] - f_target * psus(r["T"])
    lam = _bisect(fn, 0.02 * p_guess, 20 * p_guess)
    return course_time(lam, segs, model, **kw)


def wprime_budget(rows: Sequence[dict], segs: Sequence[dict], cp: float, w_prime: Optional[float],
                  frac: float = WPRIME_FRAC) -> list[dict]:
    """F11b: over every run of consecutive segments above CP·Mᵢ, the W′ spent
    Σ(Pᵢ − CP·Mᵢ)·tᵢ (no recovery, conservative) must stay ≤ 0.75·W′.
    Algebra of the CP model (Jones & Vanhatalo 2017) — 已驗證 (V-F11b)."""
    out, cur = [], None
    for i, (r, s) in enumerate(zip(rows, segs)):
        over = r["P"] - cp * s.get("M", 1.0)
        if over > 0:
            if cur is None:
                cur = {"from": i, "to": i, "used_j": 0.0}
            cur["to"] = i
            cur["used_j"] += over * r["t"]
        elif cur is not None:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    for c in out:
        c["limit_j"] = frac * w_prime if w_prime else None
        c["over"] = bool(w_prime) and c["used_j"] > frac * w_prime
    return out


def solve_with_budget(solve: Callable[[float], dict], segs, cp: float, w_prime: Optional[float],
                      alpha: float) -> tuple[dict, float, list[dict]]:
    """Run a solver (a function of α) and shrink α by 20 % steps while any
    above-CP stretch breaks the F11b budget (§6.3 step 4)."""
    a = alpha
    res = solve(a)
    runs = wprime_budget(res["rows"], segs, cp, w_prime)
    n = 0
    while any(r["over"] for r in runs) and a > 1e-4 and n < 15:
        a *= 0.8
        n += 1
        res = solve(a)
        runs = wprime_budget(res["rows"], segs, cp, w_prime)
    if any(r["over"] for r in runs) and a <= 1e-4:
        a = 0.0
    return res, a, runs


def wbal_skiba(rows: Sequence[dict], segs: Sequence[dict], cp: float, w_prime: float) -> list[float]:
    """F11 Skiba et al. 2012 (MSSE 44:1526–1532) W′bal at each segment end:
    expenditure above CP, exponential recovery below with
    τ = 546·e^(−0.01·D_CP) + 316, D_CP = CP − P. The τ coefficients are quoted
    from memory — the abstract does not list them — so this is 待驗證 and
    display-only (never a constraint); developed on cycling, used for running
    as an extrapolation."""
    bal = w_prime
    out = []
    for r, s in zip(rows, segs):
        cpm = cp * s.get("M", 1.0)
        if r["P"] > cpm:
            bal -= (r["P"] - cpm) * r["t"]
        else:
            tau = 546.0 * math.exp(-0.01 * (cpm - r["P"])) + 316.0
            bal = w_prime - (w_prime - bal) * math.exp(-r["t"] / tau)
        out.append(bal)
    return out


def wbal_wko5(rows: Sequence[dict], segs: Sequence[dict], cp: float, w_prime: float,
              step_s: float = 5.0) -> list[float]:
    """W′ balance the way WKO5 draws it (its `dfrc`, docs/wko5-internals/
    functions.md, disassembly 0x6d7ae0): depletion (P − CP)·dt, recovery
    bi-exponential — 70 % with τ = 300 s, 30 % with τ = 25 s, clock restarting
    after each effort. Reuses the evaluator's port (`_dfrc`). Status: a
    faithful port of WKO5 (consistent with the app's other charts), not a
    peer-reviewed model — display-only, the default W′ curve. Power is taken
    back to training conditions (Pᵢ/Mᵢ) so one CP applies. Returns W′bal (J)
    at each segment end."""
    import numpy as np
    from backend.engine.wko5expr.evaluator import _dfrc
    ps, dts, ends = [], [], []
    for r, s in zip(rows, segs):
        n = max(1, int(math.ceil(r["t"] / step_s)))
        ps.extend([r["P"] / s.get("M", 1.0)] * n)
        dts.extend([r["t"] / n] * n)
        ends.append(len(ps) - 1)
    out = _dfrc(np.array(ps), np.array(dts), float(w_prime), float(cp)) * 1000.0
    return [float(out[i]) for i in ends]


def damage_index(rows: Sequence[dict], t_lim: Callable[[float], float]) -> float:
    """Miner-style D = Σ tᵢ / t_lim(Pᵢ) — a diagnostic only (§3, F16 note):
    with the Riegel exponent it overstates short surges, so it never
    constrains the plan; > 1.05 raises a note."""
    return sum(r["t"] / max(t_lim(r["P"]), 1.0) for r in rows)
