"""
百岳 walking capacity from the athlete's own running data —
docs/research/baiyue-from-running.md §3 (B1–B8), §3.8, §2.4, §2.6.

    Ė_AeT        = W · (1.5 + Cr₀ · v_run,AeT)                            (B1)
    v₀(g, L, η)  = Pandolf⁻¹(M = Ė_AeT; W, L, G = 100·g, η)   g ≥ 0       (B2)
    p(g, L)      = Pandolf⁻¹(M; L) / Pandolf⁻¹(M; L₀)          g ≥ 0       (B3)
                 = (W + L₀)/(W + L)                              g < 0       (B3')
    ln v_w       = ln v₀(g_w, L_w) + δ(g_w) + α·Δz_w/1000 + γ·h_w + ε_w    (B4)
    A(z)         = exp(b_post/100 · max(0, z − 300)/1000)                    (B7)
    v_i          = min(v_flat, v₀·e^δ) (g ≥ 0) or v_down (g < 0)
                   × A(zᵢ) × f_time(hᵢ) × f_day(nᵢ) × Hᵢ                    (B8)

Status of every piece (the doc's tags): B1 is our composition of Minetti
2002's flat running cost (已驗證, test_minetti) and Pandolf's standing term
(已驗證 second-hand) — 推估, 待驗證 by the leave-one-out back-test; B2/B3 are
Pandolf 1977 (已驗證 second-hand, G ≥ 0) and agree with Ludlow & Weyand 2017's
proportionality to total mass on steep grades; B3' is 推估; B4 and the
shrinkage are 推估; B7's prior is Wehrlin & Hallén 2006 (已驗證) with a width
from Wehrlin's inter-individual range (see ALT_TAU); the descent cap uses
Tobler's shape (經驗法則) × a personal p75 (推估). Tobler is no longer the
prior — only the descent cap's shape and a cross-check.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from statistics import median
from typing import Optional, Sequence

import numpy as np

from backend.engine.algorithms import minetti
from backend.engine.racepower import hike as HK
from backend.engine.racepower.env import bassett_pct
from backend.engine.racepower.grade_model import BIN, SHRINK_N, tobler_kmh

CR0 = minetti.FLAT_RUN          # 3.6 J/kg/m, Minetti 2002 polynomial constant (measured 3.40 ± 0.24)
STAND_W_PER_KG = 1.5            # Pandolf's standing term 1.5·W
L_TRAIL = 2.0                   # vest on a trail run (kg), baiyue-from-running.md §2.2 (定義, adjustable)
# pack defaults for a trip without a recorded pack. User decision 2026-09-30:
# 9 kg for a multi-day 百岳 trip; user decision 2026-10-01: single-day is 9 kg too
# (replaces the doc's 6 kg). Labelled 預設背負.
PACK_DEFAULT_MULTI = 9.0
PACK_DEFAULT_SINGLE = 9.0
# generalize-athlete P3: the default follows the body weight — 13 % (Uphill Athlete's
# trekking example, 推估), rounded to 0.5 kg; 9 kg only when the weight is unknown.
# The author's 68 kg gives 8.84 -> 9.0, i.e. the same default as before.
PACK_PCT = 0.13


def pack_default(weight: Optional[float] = None, days: int = 1) -> float:
    """The trip pack (kg, day 1) when none was entered: weight × PACK_PCT to
    the nearest 0.5 kg, else PACK_DEFAULT_MULTI / _SINGLE."""
    if weight and weight > 0:
        return round(weight * PACK_PCT * 2) / 2
    return PACK_DEFAULT_MULTI if (days or 1) > 1 else PACK_DEFAULT_SINGLE


def pack_default_text(weight: Optional[float] = None) -> str:
    return (f"體重 {weight:.0f} kg × 13 % ≈ {pack_default(weight):g} kg（推估）" if weight and weight > 0
            else f"{PACK_DEFAULT_SINGLE:g} kg（沒有體重時的預設）")
PACK_DAILY_DROP = 0.7           # food eaten per day (推估, baiyue-from-running.md §3.2, user kept it)
TRIP_DAYS_DEFAULT = 3           # user decision 2026-09-30
TRIP_KIND_DEFAULT = "group"     # 跟團 (user decision 2026-09-30)
Z_REF = 300.0                   # every window normalised to 300 m (Wehrlin's linear range starts there)
FLAT_G = 0.05                   # below 5 % the walking speed comes from the flat model (§3.4)
DOWN_CAP_G = -0.10              # descent-cap windows (§3.4)
DOWN_CAP_Q = 75                 # p75 of speed ÷ Tobler (推估)
STEEP_MIN_G = 0.10              # windows that feed δ(g)
MIN_RUN = 3                     # ≥ 3 consecutive 100 m windows (≥ 300 m)
PER_ACT_CAP = 100               # windows per activity at most (one long outing must not dominate)
VAM_MAX = 2000.0                # vertical-kilometre world record rate (hikehr.VAM_MAX)
AET_TOL_BPM = 3.0               # v_run,AeT: |HR − AeT| ≤ 3 bpm
AET_WINDOW_DAYS = 90
FLAT_RUN_G = 0.02
# ---- altitude prior (B7) -----------------------------------------------------
# centre: Wehrlin & Hallén 2006, −6.3 % VO2max per 1000 m (已驗證, env.py).
# width τ (user decision 2026-09-30: "decided by the literature"):
#   1. inter-individual spread: Wehrlin's 8 athletes span 4.6–7.5 %/1000 m;
#      the expected range of 8 normal draws is 2.847 σ (range → SD
#      conversion, our step, 推估), so σ_ind = 2.9 / 2.847 = 1.02 points;
#   2. quantity mismatch: Wehrlin measures VO2max, the personal slope is
#      speed at a fixed HR; the one fixed-HR study (Coffman et al. 2020,
#      loaded self-paced 5 km) implies −3.8 %/1000 m (our conversion) —
#      2.5 points from Wehrlin; half of that as one SD (推估) = 1.25;
#   τ = √(1.02² + 1.25²) = 1.61 points.
# The diagnostics (spec, "百岳 capacity") show the athlete's own slope has no
# within-trip leverage (trip fixed effects: SE ≈ 28 points), so the posterior
# sits on the prior whatever τ in 1–3 points.
ALT_PRIOR_PCT = -6.3
WEHRLIN_RANGE = (4.6, 7.5)
RANGE_TO_SD_N8 = 2.847
COFFMAN_PCT = -3.8
ALT_TAU_IND = (WEHRLIN_RANGE[1] - WEHRLIN_RANGE[0]) / RANGE_TO_SD_N8
ALT_TAU_MISMATCH = abs(ALT_PRIOR_PCT - COFFMAN_PCT) / 2.0
ALT_TAU = math.sqrt(ALT_TAU_IND ** 2 + ALT_TAU_MISMATCH ** 2)
# ---- hour-of-day decay (B5) and multi-day (B6) --------------------------------
GAMMA_TAU = 0.03                # per hour, Nuuttila 2025's order (−5.5 % / 90 min), 推估
SIGMA_TIME_PER_15H = 0.05       # σ_time = 0.05·h/1.5, cap 0.10 (外插, 推估)
SIGMA_TIME_CAP = 0.10
SIGMA_DAY = 0.03                # per day after the first (no source)
SIGMA_PACK = 0.08               # Looney 2022 / Weyand 2021 ±15 % order (推估)
SIGMA_LOO_DEFAULT = 0.10        # until the back-test gives one
BAND_Z = 1.2816                 # p10–p90
HR_BANDS = ("aet", "cap")       # β ≈ 0 → only AeT and the capacity ceiling (§2.2 finding 1)
CAP_BAND_MAX_H = 3.0
BETA_MIN_T = 2.0                # |β/SE| below this → β fixed at 0 (推估)
EVIDENCE = {
    "prior": "B1 推估（Minetti 2002 平地跑步成本 + Pandolf 站立項）；B2 Pandolf 1977 已驗證（二手核對，G ≥ 0）",
    "pack": "B3 Pandolf 比值（已驗證二手核對；陡坡與 Ludlow & Weyand 2017 一致）；下坡 B3' 線性（推估）",
    "delta": "個人 2 % 坡度箱修正，往 0 收縮 n/(n + 30)（推估）",
    "altitude": "Wehrlin & Hallén 2006 −6.3 %/1000 m 先驗，寬度依 Wehrlin 個體範圍 4.6–7.5 與 Coffman 2020（推估）",
    "time": "當天第幾小時：個人斜率往 0 收縮，τ 0.03/h（Nuuttila 2025 量級，推估）",
    "day": "多日：心率差 × β；β 不可靠時 1.0（無來源）",
    "down": "下坡：個人走路窗 × 背負線性 ÷ η，上限 c_cap × Tobler（經驗法則 + 推估）",
}


# ---------------------------------------------------------------------------
# B1–B3
# ---------------------------------------------------------------------------

def aet_power(weight: float, v_run: float) -> float:
    """B1: Ė_AeT = W · (1.5 + Cr₀·v) W (running net cost 3.6 J/kg/m × speed +
    Pandolf's standing 1.5 W/kg)."""
    return weight * (STAND_W_PER_KG + CR0 * v_run)


def pandolf_speed_at(m_w: float, weight: float, load: float, grade_pct: float, eta: float = 1.0) -> float:
    """B2: the speed at which Pandolf's M equals `m_w` (bisection; G ≥ 0).
    0 when even standing costs more."""
    lo, hi = 0.0, 12.0
    if HK.pandolf(weight, load, 0.0, grade_pct, eta) >= m_w:
        return 0.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if HK.pandolf(weight, load, mid, grade_pct, eta) > m_w:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def prior_speed(g: float, load: float, e_aet: float, weight: float, eta: float = 1.0) -> float:
    """v₀(g, L, η) for g ≥ 0 (Pandolf is not used downhill)."""
    return pandolf_speed_at(e_aet, weight, load, 100.0 * max(0.0, g), eta)


def pack_ratio(g: float, load: float, load0: float, weight: float, e_aet: float) -> float:
    """B3 (g ≥ 0): Pandolf⁻¹ with L over Pandolf⁻¹ with L₀ at the same
    metabolic rate; B3' (g < 0): (W + L₀)/(W + L)."""
    if g < 0:
        return (weight + load0) / (weight + load)
    a = prior_speed(g, load0, e_aet, weight)
    return prior_speed(g, load, e_aet, weight) / a if a > 0 else 1.0


def daily_drop() -> float:
    """PACK_DAILY_DROP, or the 進階 manual value (engine/advanced_params.py)."""
    try:
        from backend.engine.advanced_params import pack_daily_drop
        return float(pack_daily_drop())
    except Exception:                       # noqa: BLE001
        return PACK_DAILY_DROP


def day_pack(pack_day1: float, day: int, drop: Optional[float] = None) -> float:
    drop = daily_drop() if drop is None else drop
    return max(0.0, pack_day1 - drop * (max(1, int(day)) - 1))


# ---------------------------------------------------------------------------
# B7 shrinkage and the altitude diagnostics (§2.4)
# ---------------------------------------------------------------------------

def shrink(b: Optional[float], se: Optional[float], prior: float = ALT_PRIOR_PCT,
           tau: float = ALT_TAU) -> dict:
    """Normal–normal precision weighting (Efron & Morris 1975 empirical
    Bayes idea): b_post = (b/SE² + b₀/τ²)/(1/SE² + 1/τ²). SE None / inf →
    the prior; SE → 0 → the personal value."""
    if b is None or se is None or not math.isfinite(se):
        return {"post": prior, "w": 0.0, "se_post": tau, "prior": prior, "tau": tau, "personal": b, "se": se}
    if se <= 0:
        return {"post": b, "w": 1.0, "se_post": 0.0, "prior": prior, "tau": tau, "personal": b, "se": se}
    wp, w0 = 1.0 / se ** 2, 1.0 / tau ** 2
    return {"post": (b * wp + prior * w0) / (wp + w0), "w": wp / (wp + w0), "se_post": math.sqrt(1.0 / (wp + w0)),
            "prior": prior, "tau": tau, "personal": b, "se": se}


def _gband(g: float) -> str:
    return "10-20" if g < 0.2 else "20-30" if g < 0.3 else "30+"


def alt_slope(wins: Sequence[dict], cell) -> Optional[dict]:
    """ln VAM on elevation inside cells (fixed effects) → % per 1000 m
    (log points × 100, the form B7 uses)."""
    cells: dict = {}
    for w in wins:
        vam = w["v"] * w["g"] * 3600.0
        if w.get("z") is None or vam <= 0:
            continue
        cells.setdefault(cell(w), []).append((w["z"] / 1000.0, math.log(vam)))
    xs, ys = [], []
    for pts in cells.values():
        if len(pts) < 2:
            continue
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        xs += [p[0] - mx for p in pts]
        ys += [p[1] - my for p in pts]
    sxx = sum(x * x for x in xs)
    if len(xs) < 5 or sxx <= 1e-9:
        return None
    b = sum(x * y for x, y in zip(xs, ys)) / sxx
    return {"pct_per_km": b * 100.0, "n": len(xs), "z_sd_m": math.sqrt(sxx / len(xs)) * 1000.0}


def cluster_se(wins: Sequence[dict], cell, reps: int = 400, seed: int = 7) -> Optional[float]:
    """Bootstrap SE of `alt_slope`, resampling whole trips (the windows of a
    trip are not independent: the effective n is close to the trip count)."""
    trips = sorted({w["trip"] for w in wins}, key=str)
    if len(trips) < 3:
        return None
    by = {t: [w for w in wins if w["trip"] == t] for t in trips}
    rng = random.Random(seed)
    bs = []
    for _ in range(reps):
        sample = []
        for j, t in enumerate(rng.choice(trips) for _ in trips):
            sample += [{**w, "trip": (t, j)} for w in by[t]]
        r = alt_slope(sample, cell)
        if r:
            bs.append(r["pct_per_km"])
    return float(np.std(bs)) if len(bs) >= 20 else None


def altitude_diagnostics(wins: Sequence[dict], reps: int = 400) -> dict:
    """The four versions of §2.4 plus the current one, each with its
    trip-cluster bootstrap SE, and the elevation distribution."""
    def hr5(w):
        return int((w.get("hr") or 0) // 5)
    versions = {
        "current": (list(wins), lambda w: (_gband(w["g"]), hr5(w))),
        "trip_fe": (list(wins), lambda w: (_gband(w["g"]), hr5(w), w["trip"])),
        "day1": ([w for w in wins if (w.get("day") or 1) == 1], lambda w: (_gband(w["g"]), hr5(w), w["trip"])),
        "first2h": ([w for w in wins if (w.get("t") or 0) < 7200], lambda w: (_gband(w["g"]), hr5(w), w["trip"])),
    }
    out = {}
    for k, (ws, cell) in versions.items():
        r = alt_slope(ws, cell)
        out[k] = None if r is None else {**r, "se": cluster_se(ws, cell, reps), "trips": len({w["trip"] for w in ws})}
    zs = [w["z"] for w in wins if w.get("z") is not None]
    per_trip: dict = {}
    for w in wins:
        per_trip.setdefault(w["trip"], []).append(w["z"])
    dist = {"n": len(zs), "quantiles": [float(x) for x in np.percentile(zs, [10, 25, 50, 75, 90])] if zs else None,
            "z_max": max(zs) if zs else None, "n_2500": sum(z >= 2500 for z in zs), "n_3000": sum(z >= 3000 for z in zs),
            "trips": len(per_trip), "trips_2500": sum(1 for v in per_trip.values() if max(v) >= 2500),
            "per_trip": sorted(({"trip": t, "n": len(v), "z_min": min(v), "z_max": max(v),
                                 "n_2500": sum(z >= 2500 for z in v)} for t, v in per_trip.items()),
                               key=lambda r: -r["n"])}
    return {"versions": out, "distribution": dist}


# ---------------------------------------------------------------------------
# window preparation (§2.2 finding 1, §4.1)
# ---------------------------------------------------------------------------

def runs_of(wins: Sequence[dict], key: str = "a") -> list[list[dict]]:
    """Consecutive 100 m windows (k, k+1, …) of the same activity / day, ≥ MIN_RUN long."""
    by: dict = {}
    for w in wins:
        by.setdefault((w.get(key), w.get("day")), []).append(w)
    out = []
    for xs in by.values():
        xs = sorted(xs, key=lambda w: w["k"])
        cur: list = []
        for w in xs:
            if cur and w["k"] != cur[-1]["k"] + 1:
                if len(cur) >= MIN_RUN:
                    out.append(cur)
                cur = []
            cur.append(w)
        if len(cur) >= MIN_RUN:
            out.append(cur)
    return out


def prepare(runs: Sequence[list], source: str) -> list[dict]:
    """Per consecutive run: drop the first window (HR still in transition),
    give every remaining window the 300 m centred mean grade (single-window
    grade is too noisy on short steep stretches, §2.2 finding 2), and the
    run-level HR read 60 s late (`hr_lag`). 推估."""
    out = []
    for seg_i, run in enumerate(runs):
        body = run[1:]
        if len(body) < 2:
            continue
        hrs = [w.get("hr_lag") if w.get("hr_lag") is not None else w.get("hr") for w in body]
        hrs = [h for h in hrs if h]
        seg_hr = float(np.mean(hrs)) if hrs else None
        for i, w in enumerate(body):
            lo, hi = max(0, i - 1), min(len(body), i + 2)
            g3 = float(np.mean([x["g"] for x in body[lo:hi]]))
            out.append({**w, "g_raw": w["g"], "g": g3, "seg_hr": seg_hr, "seg": (source, w.get("a"), w.get("day"), seg_i),
                        "src": source})
    return out


def trail_walk_windows(grade_samples: Sequence[dict]) -> list[dict]:
    """Walked steep windows of trail runs: running share < 0.5, grade ≥ 10 %,
    ≥ 3 consecutive, first dropped, HR lag handled (baiyue §4.1)."""
    w = [s for s in grade_samples if s.get("trail") and s.get("run") is not None and s["run"] < 0.5
         and s["g"] >= STEEP_MIN_G and s.get("k") is not None]
    ws = prepare(runs_of(w), "trail")
    return [x for x in ws if x["g"] >= STEEP_MIN_G and x["v"] * x["g"] * 3600.0 <= VAM_MAX]


def hike_steep_windows(hr_wins: Sequence[dict]) -> list[dict]:
    """hikehr's HR-filtered windows (already ≥ 3 consecutive, HR ≥ AeT),
    re-cut into consecutive runs with the first window dropped."""
    w = [{**x, "a": x.get("trip", x.get("a"))} for x in hr_wins if x.get("k") is not None]
    ws = prepare(runs_of(w), "hike")
    return [x for x in ws if x["g"] >= STEEP_MIN_G]


def flat_walk_windows(grade_samples: Sequence[dict]) -> list[dict]:
    """Walked / jogged flat and descent windows of trail runs (run share <
    0.5), for v_flat, v_down (§3.4)."""
    return [s for s in grade_samples if s.get("trail") and s.get("run") is not None and s["run"] < 0.5
            and s["g"] < FLAT_G and s["v"] > 0.3]


def run_speed_at_aet(grade_samples: Sequence[dict], aet_of, date_lo: Optional[str] = None) -> dict:
    """v_run,AeT: median speed of flat (|g| ≤ 2 %) running windows of road
    runs whose HR (60 s late) is within AeT ± 3 bpm (AeT as of each run's
    date). Falls back to ± 5 bpm, then to 365 days; `source` says which."""
    def pick(tol, lo):
        out = []
        for s in grade_samples:
            if s.get("trail") or abs(s["g"]) > FLAT_RUN_G or (s.get("run") is not None and s["run"] < 0.5):
                continue
            if lo and (s.get("date") or "") < lo:
                continue
            hr = s.get("hr_lag") if s.get("hr_lag") is not None else s.get("hr")
            aet = aet_of(s.get("a"))
            if hr is None or not aet:
                continue
            if abs(hr - aet) <= tol:
                out.append(s["v"])
        return out
    tries = [(AET_TOL_BPM, date_lo, f"近 {AET_WINDOW_DAYS} 天平路跑步、心率在 AeT ± 3 bpm 的速度中位數"),
             (5.0, date_lo, f"近 {AET_WINDOW_DAYS} 天平路跑步、心率在 AeT ± 5 bpm（± 3 不足 30 窗）"),
             (5.0, None, "近一年平路跑步、心率在 AeT ± 5 bpm（近 90 天不足 30 窗）")]
    for tol, lo, label in tries:
        vs = pick(tol, lo)
        if len(vs) >= 30:
            return {"v": float(median(vs)), "n": len(vs), "source": label, "fallback": tol != AET_TOL_BPM or lo is None}
    return {"v": None, "n": 0, "source": "沒有心率在 AeT 附近的平路跑步", "fallback": True}


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

def _bin(g: float) -> int:
    return int(round(max(-0.4, min(0.4, g)) / BIN))


@dataclass
class WalkCapacity:
    weight: float
    v_run: float
    e_aet: float
    delta: dict = field(default_factory=dict)          # bin -> {"n", "mean", "w", "d"}
    alpha: dict = field(default_factory=dict)          # shrink() output + diagnostics
    gamma: float = 0.0
    gamma_info: dict = field(default_factory=dict)
    beta: dict = field(default_factory=dict)
    v_flat_obs: Optional[float] = None
    flat_n: int = 0
    down: dict = field(default_factory=dict)           # bin -> {"n", "v"}
    c_cap: float = 1.0
    c_cap_n: int = 0
    tech: float = 1.0
    basis: dict = field(default_factory=dict)
    sigma_loo: float = SIGMA_LOO_DEFAULT
    sigma_loo_src: str = "回測前預設 0.10"
    pack_range: tuple = (L_TRAIL, L_TRAIL)
    z_range: tuple = (0.0, 0.0)
    v_run_info: dict = field(default_factory=dict)

    # ---- pieces -------------------------------------------------------------
    def d(self, g: float) -> float:
        s = self.delta.get(_bin(g)) or self.delta.get("level")
        return s["d"] if s else 0.0

    def data_n(self, g: float) -> int:
        s = self.delta.get(_bin(g))
        return int(s["n"]) if s else 0

    def v0(self, g: float, load: float, eta: float = 1.0) -> float:
        return prior_speed(g, load, self.e_aet, self.weight, eta)

    def A(self, z: Optional[float], accl: str = "unacclimatised") -> float:
        """B7 with the posterior slope; 'acclimatised' × Bassett acclimatised
        ÷ unacclimatised at z (推估)."""
        if z is None:
            return 1.0
        b = self.alpha.get("post", ALT_PRIOR_PCT)
        a = math.exp(b / 100.0 * max(0.0, z - Z_REF) / 1000.0)
        if accl == "acclimatised":
            a *= bassett_pct(z, True) / bassett_pct(z, False) / (bassett_pct(Z_REF, True) / bassett_pct(Z_REF, False))
        return a

    def f_time(self, h: float) -> float:
        return math.exp(self.gamma * max(0.0, h))

    def f_day(self, n: int) -> float:
        return 1.0                       # B6: β not reliable (§2.2) → 1.0, warned

    def v_flat(self, load: float, eta: float = 1.0, g: float = 0.0) -> float:
        """§3.4: min(personal walked flat speed × tech × p(g, L), Pandolf⁻¹);
        without flat data Tobler × the personal 5–10 % ratio."""
        gg = max(0.0, g)
        cap = self.v0(gg, load, eta)
        base = self.v_flat_obs
        if base is None:
            r = self.basis.get("tobler_ratio") or 1.0
            base = tobler_kmh(0.0) / 3.6 * r
        p = pack_ratio(gg, load, L_TRAIL, self.weight, self.e_aet)
        eta_r = self.v0(gg, L_TRAIL, eta) / self.v0(gg, L_TRAIL, 1.0) if eta != 1.0 and self.v0(gg, L_TRAIL, 1.0) > 0 else 1.0
        return min(base * self.tech * p * eta_r, cap)

    def v_down(self, g: float, load: float, eta: float = 1.0) -> float:
        """§3.4: personal descent-walk speed (shrunk to c_cap·Tobler) × B3' ÷ η
        × tech, capped at c_cap·Tobler(g)."""
        cap = self.c_cap * tobler_kmh(g) / 3.6
        s = self.down.get(_bin(g))
        prior = cap
        v = prior if not s else (s["n"] * s["v"] + SHRINK_N * prior) / (s["n"] + SHRINK_N)
        v = v * self.tech * (self.weight + L_TRAIL) / (self.weight + load) / eta
        return min(v, cap)

    def v(self, g: float, load: float, eta: float = 1.0, z: Optional[float] = None, h: float = 0.0,
          n_day: int = 1, accl: str = "unacclimatised") -> float:
        """B8 without the heat term (the planner multiplies Hᵢ)."""
        if g >= 0:
            # uphill never faster than the flat walk (the flat model caps it)
            vf = self.v_flat(load, eta, 0.0)
            if g >= FLAT_G:
                base = min(vf, self.v0(g, load, eta) * math.exp(self.d(g)))
            else:
                up = min(vf, self.v0(FLAT_G, load, eta) * math.exp(self.d(FLAT_G)))
                base = vf + (up - vf) * g / FLAT_G
        elif g > -FLAT_G:
            vf = self.v_flat(load, eta, 0.0)
            vd = self.v_down(-FLAT_G, load, eta)
            base = vf + (vd - vf) * (-g / FLAT_G)
        else:
            base = self.v_down(g, load, eta)
        return base * self.A(z, accl) * self.f_time(h) * self.f_day(n_day)

    def prior_only(self, g: float, load: float, eta: float = 1.0) -> float:
        """The physiological prior alone (no δ, no altitude): back-test C."""
        return self.v0(g, load, eta)

    def sigma_parts(self, load: float, z: Optional[float], h: float, n_day: int) -> dict:
        lo, hi = self.pack_range
        sp = SIGMA_PACK if (load > hi + 0.5 or load < lo - 0.5) else 0.0
        dz = max(0.0, (z or Z_REF) - Z_REF) / 1000.0
        sa = (self.alpha.get("se_post") or ALT_TAU) / 100.0 * dz
        st = min(SIGMA_TIME_CAP, SIGMA_TIME_PER_15H * h / 1.5) if abs(self.gamma_info.get("t", 0)) < 2 else 0.0
        sd = SIGMA_DAY * max(0, n_day - 1)
        return {"pack": sp, "alt": sa, "time": st, "day": sd}

    def to_json(self) -> dict:
        rows = []
        for b in range(0, int(round(0.4 / BIN)) + 1):
            g = b * BIN
            s = self.delta.get(b) or {}
            rows.append({"grade": g, "n": int(s.get("n", 0)), "delta": s.get("d", 0.0), "raw": s.get("mean"),
                         "v0_kmh": self.v0(g, L_TRAIL) * 3.6,
                         "v_kmh": self.v(g, L_TRAIL, z=Z_REF) * 3.6, "tobler_kmh": tobler_kmh(g)})
        return {"weight": self.weight, "v_run": self.v_run, "v_run_info": self.v_run_info, "e_aet": self.e_aet,
                "e_aet_w_per_kg": self.e_aet / self.weight, "alpha": self.alpha, "gamma": self.gamma,
                "gamma_info": self.gamma_info, "beta": self.beta, "v_flat_obs": self.v_flat_obs,
                "flat_n": self.flat_n, "c_cap": self.c_cap, "c_cap_n": self.c_cap_n, "tech": self.tech,
                "basis": self.basis, "sigma_loo": self.sigma_loo, "sigma_loo_src": self.sigma_loo_src,
                "pack_range": list(self.pack_range), "z_range": list(self.z_range), "bins": rows,
                "evidence": EVIDENCE, "hr_bands": list(HR_BANDS)}


def _residuals(wins, e_aet, weight, alpha_pct, gamma):
    out = []
    for w in wins:
        v0 = prior_speed(w["g"], w["L"], e_aet, weight)
        if v0 <= 0 or w["v"] <= 0:
            continue
        a = alpha_pct / 100.0 * max(0.0, (w.get("z") or Z_REF) - Z_REF) / 1000.0
        out.append({**w, "r": math.log(w["v"]) - math.log(v0) - a - gamma * w.get("h", 0.0)})
    return out


def _capped(wins, cap=PER_ACT_CAP, seed=3):
    by: dict = {}
    for w in wins:
        by.setdefault(w.get("a"), []).append(w)
    rng = random.Random(seed)
    out = []
    for xs in by.values():
        out += xs if len(xs) <= cap else rng.sample(xs, cap)
    return out


def _fit_gamma(trail, e_aet, weight) -> dict:
    """γ from the trail walk windows (low elevation, long outings): ln v −
    ln v₀ on moving hours inside (activity × 2 % bin) cells, shrunk to 0
    with τ_γ (B5)."""
    cells: dict = {}
    for w in trail:
        v0 = prior_speed(w["g"], w["L"], e_aet, weight)
        if v0 <= 0:
            continue
        cells.setdefault((w.get("a"), _bin(w["g"])), []).append((w.get("h", 0.0), math.log(w["v"] / v0)))
    xs, ys = [], []
    for pts in cells.values():
        if len(pts) < 2:
            continue
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        xs += [p[0] - mx for p in pts]
        ys += [p[1] - my for p in pts]
    sxx = sum(x * x for x in xs)
    if len(xs) < 20 or sxx <= 1e-6:
        return {"personal": None, "se": None, "post": 0.0, "n": len(xs), "t": 0.0}
    b = sum(x * y for x, y in zip(xs, ys)) / sxx
    res = [y - b * x for x, y in zip(xs, ys)]
    acts = len({k[0] for k in cells})
    # activity-level dependence: inflate by the windows per activity (design effect, 推估)
    deff = max(1.0, len(xs) / max(1, acts))
    se = math.sqrt(sum(r * r for r in res) / max(1, len(xs) - 1) / sxx * deff)
    s = shrink(b, se, 0.0, GAMMA_TAU)
    return {"personal": b, "se": se, "post": s["post"], "w": s["w"], "n": len(xs), "t": b / se if se else 0.0,
            "tau": GAMMA_TAU}


def _fit_beta(wins, aet_of) -> dict:
    """β (ln v per bpm of segment HR above AeT) inside 2 % bin cells at the
    consecutive-run level; fixed at 0 unless |β/SE| ≥ 2 (§2.2 finding 1)."""
    segs: dict = {}
    for w in wins:
        if w.get("seg_hr") is None:
            continue
        segs.setdefault(w["seg"], []).append(w)
    pts = []
    for ws in segs.values():
        aet = aet_of(ws[0].get("a"))
        if not aet:
            continue
        g = float(np.mean([w["g"] for w in ws]))
        v = len(ws) * 100.0 / sum(100.0 / w["v"] for w in ws)
        pts.append((_bin(g), ws[0]["seg_hr"] - aet, math.log(v)))
    cells: dict = {}
    for b, h, lv in pts:
        cells.setdefault(b, []).append((h, lv))
    xs, ys = [], []
    for c in cells.values():
        if len(c) < 2:
            continue
        mh = sum(p[0] for p in c) / len(c)
        ml = sum(p[1] for p in c) / len(c)
        xs += [p[0] - mh for p in c]
        ys += [p[1] - ml for p in c]
    sxx = sum(x * x for x in xs)
    if len(xs) < 10 or sxx <= 0:
        return {"personal": None, "se": None, "used": 0.0, "n": len(xs), "reliable": False}
    b = sum(x * y for x, y in zip(xs, ys)) / sxx
    res = [y - b * x for x, y in zip(xs, ys)]
    se = math.sqrt(sum(r * r for r in res) / max(1, len(xs) - 1) / sxx)
    ok = se > 0 and abs(b / se) >= BETA_MIN_T and b > 0
    return {"personal": b, "se": se, "used": b if ok else 0.0, "n": len(xs), "reliable": ok,
            "per_10bpm_pct": b * 1000.0}


def fit_walk_capacity(*, weight: float, v_run: dict, trail: Sequence[dict], hike: Sequence[dict],
                      flat: Sequence[dict] = (), hike_down: Sequence[dict] = (), aet_of=lambda a: None,
                      pack_of=lambda trip: PACK_DEFAULT_SINGLE, tech: float = 1.0,
                      alpha_diag: Optional[dict] = None, boot_reps: int = 400,
                      sigma_loo: Optional[tuple] = None) -> WalkCapacity:
    """B4 on the prepared windows. `trail` = trail_walk_windows(), `hike` =
    hike_steep_windows() (with "trip"), `flat` = flat_walk_windows(),
    `hike_down` = every moving hike window (the descent cap)."""
    vr = v_run.get("v") or 0.0
    if not vr:
        # no flat AeT running: the personal walked 10–20 % windows fix Ė
        # instead (a labelled fallback; the plan must never fail)
        vr = 2.0
        v_run = {**v_run, "v": vr, "source": v_run.get("source", "") + "；改用預設 2.0 m/s（推估）", "fallback": True}
    e = aet_power(weight, vr)
    tw = [{**w, "L": L_TRAIL, "h": (w.get("t") or 0.0) / 3600.0} for w in trail]
    hw = [{**w, "L": pack_of(w.get("trip")), "h": (w.get("t") or 0.0) / 3600.0} for w in hike]
    # α: the athlete's trip-fixed-effect slope (§2.4 diagnostic 1) shrunk to Wehrlin
    diag = alpha_diag if alpha_diag is not None else altitude_diagnostics(hike, boot_reps)
    fe = (diag.get("versions") or {}).get("trip_fe")
    alpha = shrink(fe["pct_per_km"] if fe else None, fe.get("se") if fe else None)
    alpha.update(diagnostics=diag, source="trip_fe")
    g = _fit_gamma(tw, e, weight)
    gamma = g["post"]
    pool = _capped(_residuals(tw, e, weight, alpha["post"], gamma) + _residuals(hw, e, weight, alpha["post"], gamma))
    # drop above the personal p99 (elevation noise, §2.2 finding 2)
    if len(pool) >= 20:
        hi = float(np.percentile([w["r"] for w in pool], 99))
        pool = [w for w in pool if w["r"] <= hi]
    # δ(g) = δ̄ + δ_bin: the athlete's overall level against the prior (their
    # own running economy and B1's Minetti average differ; Breiner 2019: the
    # difference is shared across grades) over every window, then each 2 %
    # bin's deviation from δ̄ shrunk to 0 with n/(n + 30). Shrinking each bin
    # straight to 0 would pull every prediction towards a biased prior (the
    # first back-test: +7.7 % slow on held-out trail segments). 推估.
    n_all = len(pool)
    d_bar = float(np.mean([w["r"] for w in pool])) * n_all / (n_all + SHRINK_N) if pool else 0.0
    delta = {}
    by: dict = {}
    for w in pool:
        by.setdefault(_bin(w["g"]), []).append(w["r"])
    for b, rs in by.items():
        n = len(rs)
        m = float(np.mean(rs))
        wgt = n / (n + SHRINK_N)
        delta[b] = {"n": n, "mean": m, "w": wgt, "d": d_bar + wgt * (m - d_bar)}
    delta["level"] = {"n": n_all, "mean": d_bar, "w": n_all / (n_all + SHRINK_N) if n_all else 0.0, "d": d_bar}
    beta = _fit_beta(tw + hw, aet_of)
    # flat and descent (§3.4)
    fl = [w for w in flat if abs(w["g"]) < FLAT_G]
    v_flat_obs = float(median(w["v"] for w in fl)) if len(fl) >= 20 else None
    down: dict = {}
    for w in flat:
        if w["g"] <= -FLAT_G:
            down.setdefault(_bin(w["g"]), []).append(w["v"])
    down = {b: {"n": len(v), "v": float(median(v))} for b, v in down.items()}
    ratios = [w["v"] / (tobler_kmh(w["g"]) / 3.6) for w in list(flat) + list(hike_down)
              if w["g"] <= DOWN_CAP_G and w["v"] > 0.3]
    c_cap = float(np.percentile(ratios, DOWN_CAP_Q)) if len(ratios) >= 20 else 1.0
    t510 = [w["v"] / (tobler_kmh(w["g"]) / 3.6) for w in tw + hw if 0.05 <= w.get("g_raw", w["g"]) < 0.10]
    loads = [w["L"] for w in tw + hw]
    zs = [w["z"] for w in tw + hw if w.get("z") is not None]
    rr = [w["r"] for w in pool]
    basis = {"resid_p75_minus_mean": float(np.percentile(rr, 75) - np.mean(rr)) if len(rr) >= 20 else None,
             "trail_windows": len(tw), "trail_activities": len({w.get("a") for w in tw}),
             "hike_windows": len(hw), "hike_trips": len({w.get("trip") for w in hw}),
             "flat_windows": len(fl), "descent_cap_windows": len(ratios),
             "tobler_ratio": float(median(t510)) if len(t510) >= 10 else None,
             "trail_segments": len({w["seg"] for w in tw}), "hike_segments": len({w["seg"] for w in hw})}
    cap = WalkCapacity(weight=weight, v_run=vr, e_aet=e, delta=delta, alpha=alpha, gamma=gamma, gamma_info=g,
                       beta=beta, v_flat_obs=v_flat_obs, flat_n=len(fl), down=down, c_cap=c_cap,
                       c_cap_n=len(ratios), tech=tech, basis=basis, v_run_info=v_run,
                       pack_range=(min(loads), max(loads)) if loads else (L_TRAIL, L_TRAIL),
                       z_range=(min(zs), max(zs)) if zs else (0.0, 0.0))
    if sigma_loo:
        cap.sigma_loo, cap.sigma_loo_src = sigma_loo
    return cap


# ---------------------------------------------------------------------------
# course-level helpers (plan_hike, /predict)
# ---------------------------------------------------------------------------

def course_eph(cap: WalkCapacity, km: float, gain_m: float, loss_m: Optional[float], load: float) -> float:
    """EP/h the capacity model implies on a course known only by its totals
    (the same two-segment split as hike.tobler_eph, 推估) — the v1 /predict
    百岳 rate when there are no solo days."""
    loss_m = gain_m if loss_m is None else loss_m
    if km <= 0:
        return cap.v(0.0, load) * 3.6
    if gain_m + loss_m <= 0:
        h = km / (cap.v(0.0, load) * 3.6)
    else:
        g = (gain_m + loss_m) / (km * 1000.0)
        d_up = km * gain_m / (gain_m + loss_m)
        h = d_up / (cap.v(g, load) * 3.6) + (km - d_up) / (cap.v(-g, load) * 3.6)
    return (km + gain_m / 100.0) / h


def group_time(ep: float, group_days: Sequence[dict]) -> Optional[dict]:
    """跟團時間 = EP ÷ past group days' EP/h (p25 / p50 / p75; ≥ 3 days).
    A real planning figure, not the athlete's capacity."""
    e = [d["ep_per_h"] for d in group_days if d.get("ep_per_h")]
    if len(e) < 3:
        return None
    q25, q50, q75 = (float(x) for x in np.percentile(e, [25, 50, 75]))
    return {"p50_s": ep / q50 * 3600.0, "p25_s": ep / q75 * 3600.0, "p75_s": ep / q25 * 3600.0,
            "eph": [q25, q50, q75], "n": len(e)}


def band(t_s: float, sigma: float) -> dict:
    return {"p10_s": t_s * math.exp(-BAND_Z * sigma), "p90_s": t_s * math.exp(BAND_Z * sigma), "sigma": sigma}


# ---------------------------------------------------------------------------
# solo-day suggestion (§2.6)
# ---------------------------------------------------------------------------

SOLO_SHARE_MIN = 0.60          # 推估
GROUP_SHARE_MAX = 0.15         # 推估
STOPS_PER_KM_MAX = 1.0         # 推估
LOW_HR_MARGIN = 5.0
SLOW_FACTOR = 1.15
PLATEAU_N, PLATEAU_CV, PLATEAU_DG = 5, 0.05, 0.10
SOLO_RESID = 0.15
VALIDATE_MIN_DAYS = 5          # per class, manual marks before the suggestion may apply itself
VALIDATE_MIN_AGREE = 0.80


def classify_day(wins: Sequence[dict], aet: Optional[float], cap: Optional[WalkCapacity],
                 load: float, stops_per_km: Optional[float] = None) -> dict:
    """Suggest solo vs group for one hiking day from its moving windows
    ({"g", "v", "hr", "k", "z"}). Rules (推估, §2.6): among steep (≥ 10 %)
    windows, "limited" = HR < AeT − 5 while the model predicts > 1.15 × the
    actual speed, or a plateau (≥ 5 windows, speed CV < 5 % while the grade
    spans > 10 points); "own" = HR ≥ AeT and |model residual| < 15 %. Solo
    when own ≥ 60 %, limited ≤ 15 % and short stops < 1 / km. A suggestion
    only: the user confirms (racepower_solo_hikes.json)."""
    steep = sorted((w for w in wins if w["g"] >= STEEP_MIN_G), key=lambda w: w["k"])
    if not steep or not aet:
        return {"suggest": None, "own_share": None, "limited_share": None, "n": len(steep),
                "reason": "沒有陡坡窗或沒有 AeT"}
    plateau = set()
    ws = sorted(wins, key=lambda w: w["k"])
    for i in range(len(ws) - PLATEAU_N + 1):
        blk = ws[i:i + PLATEAU_N]
        if blk[-1]["k"] - blk[0]["k"] != PLATEAU_N - 1:
            continue
        vs = [w["v"] for w in blk]
        gs = [w["g"] for w in blk]
        if np.std(vs) / np.mean(vs) < PLATEAU_CV and max(gs) - min(gs) > PLATEAU_DG:
            plateau.update(w["k"] for w in blk)
    own = lim = 0
    for w in steep:
        pred = cap.v(w["g"], load, z=w.get("z")) if cap else None
        hr = w.get("hr")
        limited = (hr is not None and hr < aet - LOW_HR_MARGIN and pred and pred > SLOW_FACTOR * w["v"]) or \
            w["k"] in plateau
        if limited:
            lim += 1
        elif hr is not None and hr >= aet and pred and abs(w["v"] / pred - 1.0) < SOLO_RESID:
            own += 1
    n = len(steep)
    so, sl = own / n, lim / n
    stops_ok = stops_per_km is None or stops_per_km < STOPS_PER_KM_MAX
    suggest = "solo" if (so >= SOLO_SHARE_MIN and sl <= GROUP_SHARE_MAX and stops_ok) else "group"
    return {"suggest": suggest, "own_share": so, "limited_share": sl, "n": n, "stops_per_km": stops_per_km,
            "reason": f"自己走的段 {so:.0%}、被限制的段 {sl:.0%}" +
                      ("" if stops_per_km is None else f"、短停 {stops_per_km:.1f} 次/km")}


def classifier_validation(days: Sequence[dict]) -> dict:
    """Agreement of the suggestion with the manually marked days (§2.6 /
    back-test F). Applies automatically only with ≥ 5 marked days per class
    and ≥ 80 % agreement; until then it is a suggestion."""
    marked = [d for d in days if d.get("marked") in ("solo", "group") and d.get("suggest")]
    per = {c: [d for d in marked if d["marked"] == c] for c in ("solo", "group")}
    agree = sum(1 for d in marked if d["suggest"] == d["marked"])
    rate = agree / len(marked) if marked else None
    enough = all(len(v) >= VALIDATE_MIN_DAYS for v in per.values())
    return {"n_solo": len(per["solo"]), "n_group": len(per["group"]), "agreement": rate,
            "validated": bool(enough and rate is not None and rate >= VALIDATE_MIN_AGREE),
            "rule": f"每類 ≥ {VALIDATE_MIN_DAYS} 天手動標記且符合率 ≥ {VALIDATE_MIN_AGREE:.0%} 才自動套用"}
