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
5. The race HR level x*(T) (2026-10-02, docs/research/unsourced-rules.md
   §0.7 / §A2): what "全力" means depends on how long the race is. A full-
   effort race HR falls with duration — Fornasiero 2018 (J Sports Sci 36:
   1287, DOI 10.1080/02640414.2017.1374707: 65 km / 11.8 h at 77 % HRmax,
   86 % of the time below VT1), Kerhervé 2015 (PLoS ONE 10:e0145482: HR
   falls with race progress over 106 km). Shape: x*(T) = x₀ − s·ln(T / 1 h)
   (log-linear, 自組). Prior = the least-squares line through three anchors
   (XSTAR["anchors"]): 1.00 @ 0.5 h (a 5K's last quarter ≥ LTHR, the
   maximal.py road rule), 0.90 @ 3 h (Friel Z3 lower bound, coach), 0.85 @
   12 h (Fornasiero's 77 % HRmax ÷ ~0.90 HRmax per LTHR; the conversion is
   推估). Personal fit: the athlete's own races (activity type 比賽) and 全力
   runs (road and trail; the user's mark wins over the auto rule), x =
   moving HR ÷ the LTHR of the run's own date, T = moving hours; penalised
   least squares with the prior level worth 1 race and the prior slope
   worth 3 races spread like the anchors (推估), so with few samples the
   line stays near the prior, and the slope (which needs races of very
   different lengths) moves least.
   Prediction: T solves T = E / (v₀(f·x*(T))·D̄(T)). The planner's effort
   target f scales it (自組).
6. Durability δ (2026-10-02, §0.6 / §A3): each run's δ is measured on its
   drift-v2-cleaned window (athlete._trail_durability: adaptive start,
   return-leg cool-down, trailing idle, re-acceleration after stops and
   slow stretches of the effort-km speed cut, workout_review DRIFT §7 / §4.3)
   and the personal δ = (n·median + k·δ_prior)/(n + k) with δ_prior =
   0.05 /h (Clark et al. 2019, J Appl Physiol 127:726 and Am J Physiol 317:
   R59: CP −9 … −11 % after 2 h of heavy exercise; per hour 推估) and k = 3
   (推估). The shrunk δ is kept only when it lowers the leave-one-out
   given-HR error of the runs against the prior alone (§0.4: "加了參數 LOO
   誤差要降才留"; choose_delta) — on this athlete's trail runs the per-run δ
   reads 0.2–0.4 /h (terrain order, not fatigue) and the prior wins.
   Fuelling covariate: when the user's free-form tags mark runs as
   fuelled / unfuelled and both groups have ≥ 2 runs, the fuelled group's δ
   predicts races (Clark: carbohydrate offsets the CP loss); else no split.
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
    "delta_prior": 0.05,       # Clark 2019 (CP −9…−11 % after 2 h heavy) per hour: 推估
    "delta_k": 3.0,            # 推估: the prior counts as 3 long runs
    "delta_warn_ratio": 3.0,   # 推估: warn when the measured δ exceeds 3 × the prior
    "fuel_min_runs": 2,        # 推估: each fuelling group needs ≥ 2 runs before δ is split
    "heat_beta": True,         # §A5: x moved to Hadley 120 with the athlete's β (heat_shift)
}
# x*(T) — the full-effort HR level against race duration (module docstring step 5)
XSTAR = {
    "anchors": ((0.5, 1.00), (3.0, 0.90), (12.0, 0.85)),   # maximal.py 5K rule; Friel Z3; Fornasiero 2018 (推估 conversion)
    "level_weight": 1.0,       # 推估: the prior LEVEL x₀ counts as 1 race (LTHR estimates differ by athlete)
    "slope_weight": 3.0,       # 推估: the prior SLOPE s counts as 3 races spread like the anchors
    "t_min_h": 0.25,           # 推估: the curve is not extrapolated below 15 min …
    "t_max_h": 30.0,           # … or above 30 h
    "auto_margin": 0.03,       # 推估 (§A2): an auto 全力 trail effort = x ≥ x*_prior(T) − 0.03
    "road_min_s": 15 * 60.0,   # 推估: a road sample needs ≥ 15 min moving
}
SOURCE = ("越野心率配速模型：effort km（km + 爬升/153）÷ 移動時間 對 移動心率/LTHR 的個人回歸，"
          "加耐久衰減（1 h 後每小時下降，先驗 0.05/h 收縮）；比賽心率 x*(T) 隨時長下降（Fornasiero 2018、"
          "Kerhervé 2015 當形狀先驗）；推估")
XSTAR_SOURCE = ("全力心率曲線 x*(T) = x₀ − s·ln(T)：先驗錨點 0.5 h 1.00（5K 最後 1/4 ≥ LTHR）、3 h 0.90"
                "（Friel Z3 下緣）、12 h 0.85（Fornasiero 2018 77 % HRmax 換算）；你的比賽／全力跑以當天 LTHR 擬合，"
                "錨點當 3 場虛擬比賽收縮；推估")
FUEL_YES = ("有補給", "補給", "吃膠", "能量膠", "fuelled", "fueled", "fuel", "gel")
FUEL_NO = ("沒補給", "無補給", "不補給", "沒吃", "空腹", "unfuelled", "unfueled", "no fuel", "fasted")


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
    return {"x": hr_avg / lthr, "x_raw": hr_avg / lthr, "v": e / T, "T_h": T, "eff_km": e,
            "hr": float(hr_avg), "lthr": float(lthr)}


# ---- heat: the athlete's own β (engine/heat.HR_BETA, §A5 / §0.10 step 5) ----

def heat_shift(hadley: Optional[float], lthr: Optional[float]) -> float:
    """The HR-level shift of heat, in LTHR units: β·(Hadley − 120)/LTHR with
    the athlete's own β = 0.224 bpm per Hadley unit (heat back-test, 271
    route efforts). 0 without a Hadley or an LTHR, or with the switch off.
    Using it to move the HR model's x is 自組 (推估)."""
    if not TRAILHR["heat_beta"] or hadley is None or not lthr or not math.isfinite(hadley):
        return 0.0
    from backend.engine import heat as HT
    return HT.HR_BETA * (float(hadley) - HT.HR_BETA_REF) / float(lthr)


def heat_adjust(p: dict, hadley: Optional[float]) -> dict:
    """A run point with x moved to Hadley 120 (x = x_raw − heat_shift)."""
    sh = heat_shift(hadley, p.get("lthr"))
    p["hadley"] = hadley
    p["heat_shift"] = sh
    p["x"] = p["x_raw"] - sh
    return p


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
    """The old single race HR level (the median x, before x*(T)); kept as the
    back-test's comparison row."""
    v = [float(x) for x in xs if x and math.isfinite(x)]
    if v:
        return float(median(v)), f"之前 {len(v)} 場越野比賽／全力的移動心率中位數"
    return TRAILHR["x_default"], "沒有之前的越野比賽：用 0.90 × LTHR（Friel Z3 下緣）"


# ---- x*(T): the full-effort HR curve (module docstring step 5) ---------------

def _wls_line(T_h, x, w) -> tuple[float, float]:
    """(x₀, s) of x = x₀ − s·ln T by weighted least squares."""
    L = np.log(np.asarray(T_h, float))
    y = np.asarray(x, float)
    W = np.asarray(w, float)
    X = np.column_stack([np.ones_like(L), -L]) * np.sqrt(W)[:, None]
    coef, *_ = np.linalg.lstsq(X, y * np.sqrt(W), rcond=None)
    return float(coef[0]), float(coef[1])


def xstar_prior() -> dict:
    """The literature prior line through XSTAR["anchors"] (equal weights)."""
    a = XSTAR["anchors"]
    x0, s = _wls_line([t for t, _ in a], [x for _, x in a], [1.0] * len(a))
    return {"x0": x0, "s": s}


def _clamp_t(T_h: float) -> float:
    return min(XSTAR["t_max_h"], max(XSTAR["t_min_h"], float(T_h)))


def xstar_at(xs: Optional[dict], T_h: float) -> float:
    """x*(T) of a fitted (or prior) curve; T clamped to 15 min – 30 h."""
    xs = xs or xstar_prior()
    return xs["x0"] - xs["s"] * math.log(_clamp_t(T_h))


def auto_max_frac(T_h: Optional[float]) -> float:
    """The auto 全力 HR threshold for a trail effort of T_h moving hours
    (§A2): x*_prior(T) − 0.03. The prior, not the fitted curve: the samples
    the curve is fitted on must not be chosen by the curve itself."""
    if not T_h or not math.isfinite(T_h):
        return TRAILHR["x_default"]
    return xstar_at(xstar_prior(), T_h) - XSTAR["auto_margin"]


def fit_xstar(samples) -> dict:
    """x*(T) from the athlete's full-effort samples [{T_h, x, ...}] shrunk to
    the prior (penalised least squares, 推估): the samples (weight 1 each),
    the prior level x₀ as `level_weight` races and the prior slope s as
    `slope_weight` races spread over ln T like the anchors. The level is
    personal quickly (an estimated LTHR can sit well below the true one, so
    a 2–3 h race can average > 1.0 × LTHR); the slope needs races of very
    different lengths, so it stays near the literature until those exist.
    No samples → the prior itself."""
    k = XSTAR
    pts = [p for p in samples or [] if p and p.get("T_h") and p.get("x")
           and math.isfinite(p["x"]) and k["t_min_h"] <= p["T_h"] <= k["t_max_h"]]
    prior = xstar_prior()
    out = {"prior": prior, "n": len(pts), "source": XSTAR_SOURCE, "label": "推估",
           "anchors": [list(a) for a in k["anchors"]], "level_weight": k["level_weight"],
           "slope_weight": k["slope_weight"],
           "samples": [{q: p.get(q) for q in ("T_h", "x", "date", "label", "category", "idx")} for p in pts]}
    if not pts:
        out.update(prior, kind="prior", resid_sd=None)
        return out
    La = np.log([t for t, _ in k["anchors"]])
    v_a = float(np.var(La))
    L = np.log([p["T_h"] for p in pts])
    # centred on the samples' mean ln T, so the level prior does not tilt the slope
    lb = float(L.mean())
    rows = [[1.0, -(li - lb)] for li in L]
    ys = [p["x"] for p in pts]
    rl, rs = math.sqrt(k["level_weight"]), math.sqrt(k["slope_weight"] * v_a)
    rows += [[rl, 0.0], [0.0, rs]]
    ys += [rl * (prior["x0"] - prior["s"] * lb), rs * prior["s"]]
    coef, *_ = np.linalg.lstsq(np.asarray(rows), np.asarray(ys), rcond=None)
    c, s = float(coef[0]), float(coef[1])
    x0 = c + s * lb
    res = [p["x"] - (x0 - s * math.log(p["T_h"])) for p in pts]
    out.update(x0=x0, s=s, kind="shrunk",
               resid_sd=float(np.std(res, ddof=1)) if len(res) >= 3 else None,
               weight_level=len(pts) / (len(pts) + k["level_weight"]))
    return out


def predict_race(m: dict, eff_km: float, xs: Optional[dict], f: float = 1.0,
                 delta: Optional[float] = None, x_shift: float = 0.0) -> tuple[Optional[float], Optional[float]]:
    """(moving seconds, x used) at the full-effort curve: T solves
    T = E / (v₀(f·x*(T) − x_shift)·D̄(T)). `x_shift` = a heat shift of the
    HR level (planner / back-test, β·ΔHadley / LTHR)."""
    T = 3.0
    x = None
    for _ in range(60):
        x = f * xstar_at(xs, T) - x_shift
        t = predict_time(m, eff_km, x, delta)
        if not t:
            return None, x
        T2 = t / 3600.0
        if abs(T2 - T) < 1e-5:
            T = T2
            break
        T = T2
    return T * 3600.0, x


# ---- durability δ: prior shrinkage and the fuelling covariate ---------------

def shrink_delta(deltas) -> dict:
    """δ = (n·median + k·prior)/(n + k), clamped to 0…delta_max; with the raw
    median, n and a warning when the raw δ exceeds 3 × the prior."""
    k = TRAILHR
    v = [float(d) for d in deltas or [] if d is not None and math.isfinite(d)]
    prior = k["delta_prior"]
    raw = float(median(v)) if v else None
    n = len(v)
    d = prior if raw is None else (n * raw + k["delta_k"] * prior) / (n + k["delta_k"])
    d = float(min(k["delta_max"], max(0.0, d)))
    warn = None
    if raw is not None and raw > k["delta_warn_ratio"] * prior:
        warn = (f"量到的耐久衰減 {raw:.0%}/h 超過先驗 {prior:.0%}/h 的 {k['delta_warn_ratio']:g} 倍：長跑裡可能還有"
                "停等、走路或回程沒清掉（推估）")
    return {"delta": d, "raw_median": raw, "n": n, "prior": prior, "k": k["delta_k"], "warning": warn}


def loo_error(points, delta: float) -> Optional[float]:
    """Median |time error| of the given-HR prediction when each run is left
    out of the v₀ fit (the §0.4 rule: a parameter stays only when it lowers
    the leave-one-out error); None with < 4 runs."""
    pts = [p for p in points if p]
    if len(pts) < 4:
        return None
    errs = []
    for i, p in enumerate(pts):
        m = fit(pts[:i] + pts[i + 1:], delta)
        t = predict_time(m, p["eff_km"], p["x"])
        if t:
            errs.append(abs(t / (p["T_h"] * 3600.0) - 1.0))
    return float(median(errs)) if errs else None


def choose_delta(points, dinfo: dict) -> dict:
    """The measured-and-shrunk δ only when its leave-one-out error is lower
    than the prior's (§0.4); else the prior 0.05 /h. Returns {delta, choice,
    loo_measured, loo_prior}."""
    prior = TRAILHR["delta_prior"]
    d = dinfo["use"]
    if dinfo["all"]["n"] == 0 or abs(d - prior) < 1e-9:
        return {"delta": d, "choice": "prior" if dinfo["all"]["n"] == 0 else "measured",
                "loo_measured": None, "loo_prior": None}
    lm, lp = loo_error(points, d), loo_error(points, prior)
    if lm is None or lp is None or lm < lp:
        return {"delta": d, "choice": "measured", "loo_measured": lm, "loo_prior": lp}
    return {"delta": prior, "choice": "prior", "loo_measured": lm, "loo_prior": lp}


def fuel_of(tags) -> Optional[bool]:
    """True / False when the user's free-form tags say the run was fuelled /
    not fuelled; None when they do not say."""
    words = [str(t).strip().lower() for t in tags or []]
    if any(any(n in w for n in FUEL_NO) for w in words):
        return False
    if any(any(y == w or y in w for y in FUEL_YES) for w in words):
        return True
    return None


def delta_by_fuel(rows) -> dict:
    """rows [{delta, fuel}] → the shrunk δ overall and, when both fuelling
    groups have ≥ fuel_min_runs runs, per group; `use` = the δ a race
    prediction should take (fuelled when split, races are fuelled)."""
    allv = shrink_delta([r["delta"] for r in rows])
    yes = [r["delta"] for r in rows if r.get("fuel") is True]
    no = [r["delta"] for r in rows if r.get("fuel") is False]
    out = {"all": allv, "split": False, "use": allv["delta"], "n_fuelled": len(yes), "n_unfuelled": len(no)}
    mn = TRAILHR["fuel_min_runs"]
    if len(yes) >= mn and len(no) >= mn:
        fy, fn = shrink_delta(yes), shrink_delta(no)
        out.update(split=True, fuelled=fy, unfuelled=fn, use=fy["delta"])
    return out
