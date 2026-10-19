"""
Trail race time from HR and terrain, not power (2026-10-01).

Why: the back-test showed the power-envelope capacity off by +46 % power /
−35 % time on trail, while the terrain model (effort-km / RE, given the
actual power) is ~10 % off. On trail, power is a poor effort signal (low on
descents; fatigue turns flats into walking), so the athlete's effort is read
from HR (activity_tags.effort_hr) and the time from the effort-km pace that
HR level buys on their own trail runs.

Model (all 推估 until the back-test validates it):

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
   per hour after T0 = 1 h (the user's "beyond ~1–2 h"; 1 h 推估) is the run's
   δ; the personal δ is the median over runs, clamped to 0…0.15 /h. Speed
   at a given HR then is v(t) = v₀·max(f_min, 1 − δ·(t − T0)⁺) (SP-239,
   2026-10-06: straight down, then flat at the floor f_min = v_floor 0.80),
   whose mean over a run of length T is, with t1 = T0 + (1 − f_min)/δ,
   D̄(T) = 1 − δ·(T − T0)²/(2T) for T0 < T ≤ t1 and
   D̄(T) = [T0 + (t1 − T0)·(1 + f_min)/2 + f_min·(T − t1)] / T for T > t1
   (docs/research/long-race-durability-shape.md §4.1). Before SP-239 the
   line ran on to 0 at 1 + 1/δ h and below, so D̄ hit its 0.5 guard and any
   course over ~11.3 h of no-decay time (δ 0.05) came out at exactly 2×.
3. v₀(x) = a + b·x, OLS across runs on the durability-corrected
   v₀ = v / D̄(T). Needs ≥ 6 runs and b > 0; else v₀ = c·x with
   c = median(v₀/x) (proportional fallback).
4. Prediction for a course of effort distance E at HR level x: T solves
   T = E / (v₀(x)·D̄(T)), i.e. the distance covered T·D̄(T) (in no-decay
   hours) equals E / v₀(x). That distance rises strictly with T (its slope
   is the speed, ≥ f_min > 0), so T is its closed-form inverse
   (time_for_nodecay): continuous and monotone in E.
5. The race HR level x*(T) (2026-10-02, docs/research/unsourced-rules.md
   §0.7 / §A2): what "全力" means depends on how long the race is. A full-
   effort race HR falls with duration — Fornasiero 2018 (J Sports Sci 36:
   1287, DOI 10.1080/02640414.2017.1374707: 65 km / 11.8 h at 77 % HRmax,
   86 % of the time below VT1), Kerhervé 2015 (PLoS ONE 10:e0145482: HR
   falls with race progress over 106 km). Shape: x*(T) = x₀ − s·ln(T / 1 h)
   (log-linear, 推估). Prior = the least-squares line through three anchors
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
   target f scales it (推估).
6. Durability δ (2026-10-02, §0.6 / §A3): each run's δ is measured on its
   drift-v2-cleaned window (athlete._trail_durability: adaptive start,
   return-leg cool-down, trailing idle, re-acceleration after stops and
   slow stretches of the effort-km speed cut, workout_review DRIFT §7 / §4.3)
   and the personal δ = (n·median + k·δ_prior)/(n + k) with δ_prior =
   0.05 /h (Clark et al. 2019, J Appl Physiol 127:726 and Am J Physiol 317:
   R59: CP −9 … −11 % after 2 h of heavy exercise; per hour 推估) and k = 3
   (推估). The shrunk δ is kept only when it lowers the leave-one-out
   given-HR error of the runs against the prior alone (§0.4: "加了參數 LOO
   誤差要降才留"; choose_delta) — on one runner's trail runs the per-run δ
   reads 0.2–0.4 /h (terrain order, not fatigue) and the prior wins.
   Fuelling covariate: when the user's free-form tags mark runs as
   fuelled / unfuelled and both groups have ≥ 2 runs, the fuelled group's δ
   predicts races (Clark: carbohydrate offsets the CP loss); else no split.
7. Terrain-matched δ (2026-10-02, owner decision: measure δ dynamically but
   without the terrain confound). The rolling effort-km-speed / HR ratio of
   step 2 compares a climb early in the run with a descent late in it, so
   its slope is the course's order, not fatigue (0.2–0.4 /h on this
   athlete's runs). Instead, per run: 100 m windows (terrain_windows) on the
   drift-v2-cleaned samples, and the within-run regression
       ln v = f(g) + c·(HR_lag − mean) − d·(t − T0)⁺
   with f a linear spline in grade (knots at the back-test's grade edges;
   the terrain model) fitted only on the grades BOTH halves of the run cover
   (the overlap of their p10–p90 grade ranges: like-for-like terrain, so a
   climb-first / descend-later course cannot pose as fatigue), HR read 60 s
   later (HR lag, athlete.HR_LAG_S), t = moving hours. d is the
   loss of speed per hour at the same grade and the same HR — the run's δ,
   with its OLS standard error (within_run_delta, 推估). Across runs: the
   random-effects mean (DerSimonian–Laird) with a 95 % CI (pool_deltas),
   then the same n/(n+3) shrinkage to 0.05 /h and the LOO gate. Steps 2's
   ratio stays only as `delta_uncleaned` for comparison.
8. Within-activity δ (SP-722, 2026-10-19; SP-716 research). The athlete has few ≥ 2 h runs, so
   step 7 alone leaves δ at the prior. Instead every own-paced trail run (no group hike: the
   user's 爬山 / 百岳跟團 mark or a hiking sport type counts only when opted in as solo,
   athlete.own_paced) of the last INSIDE["window_days"] gives 50 m windows (moving share ≥ 0.8,
   drift-v2-cleaned share ≥ 0.8, |grade| ≤ 45 %, the first 15 min skipped as warm-up), and
       r = ln v − g(grade)          (g = the pooled grade curve: median ln v per 2 % grade bin)
       r − r̄ᵢ = c·(HR_lag − H̄Rᵢ) − δ·(L − L̄ᵢ),   L = (t − T0)⁺ moving hours
   on the runs ≥ INSIDE["min_run_s"] moving, each run demeaned (its own intercept: the day's form
   and the route's surface cancel), one pooled fixed-effects slope with an activity-cluster SE
   (inside_delta). Same definition as the model's δ: the speed lost per hour after T0 at the same
   grade and the same HR. The long records are in it too, with the most weight (a run's weight
   is its spread of L: a 1.5 h run has 0.5 h after T0, a 4 h run 3 h — the "short runs weigh
   little" rule falls out of the regression). Runs under 1.5 h only shape g. Shrinkage to the
   0.05 /h prior by precision (normal–normal, shrink_inside): δ = w·raw + (1 − w)·prior,
   w = σ₀² / (σ₀² + SE²), σ₀ = INSIDE["prior_sd"] 0.02 /h (推估: the prior counts as a
   measurement with SE 2 %/h, i.e. k = SE²/σ₀² "prior units"), then the LOO gate — since
   SP-722 scored only on the runs ≥ 1.5 h (δ acts after T0; the short runs must not let a δ
   through that makes the long ones worse), and the prior when fewer than 4 such runs exist.
   The long-run rows of step 7 stay as `delta_info["all"]` (shown, and used per group when
   the fuelling split applies).
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional

import numpy as np

TRAILHR = {
    "divisor": None,           # None = the athlete's own (effort.divisor_of("fitted_run")); a number overrides
    "min_moving_s": 45 * 60.0,  # athlete.TRAIL_MIN_MOVING_S
    "dur_min_s": 2 * 3600.0,   # 推估: durability only from runs ≥ 2 h moving
    "t0_h": 1.0,               # 推估: the decline starts after 1 h
    "delta_max": 0.15,         # 推估 clamp (/h)
    "min_runs": 6,             # 推估
    "window_days": 365,        # as RE_WINDOW_DAYS
    "race_min_s": 90 * 60.0,   # maximal.MAXIMAL["trail_min_s"]
    "x_default": 0.90,         # Friel Z3 lower bound (activity_tags.AUTO_EFFORT max_hr_frac)
    "dbar_min": 0.5,           # guard only (fade_shape's clamp); D̄ itself never goes below v_floor
    # SP-239 (2026-10-06, owner decision): the same-HR speed falls δ per hour after T0, then holds
    # at v_floor × v₀ instead of running on to 0. 推估. Long-race speed flattens rather than falling
    # to 0: Martin 2010 (24 h treadmill: speed fell until 16 h, then constant), Bossi 2017 (24 h
    # track: reverse-J, bottoming mid-race), Markovic 2025 (Western States: last split ≈ 0.78 × the
    # first, HR drop + stops + terrain included, so the same-HR loss is smaller → 0.80 is on the
    # conservative side). docs/research/long-race-durability-shape.md §3, §4.1. Not personalised.
    "v_floor": 0.80,
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
          "加耐久衰減（1 h 後每小時下降：你自己步調的越野跑活動內部、同坡度同心率的斜率，往先驗 0.05/h 收縮，"
          "降到 80 % 後持平）；比賽心率 x*(T) 隨時長下降（Fornasiero 2018、Kerhervé 2015 當形狀先驗）；推估")
XSTAR_SOURCE = ("全力心率曲線 x*(T) = x₀ − s·ln(T)：先驗錨點 0.5 h 1.00（5K 最後 1/4 ≥ LTHR）、3 h 0.90"
                "（Friel Z3 下緣）、12 h 0.85（Fornasiero 2018 77 % HRmax 換算）；你的比賽／全力跑以當天 LTHR 擬合，"
                "錨點當 3 場虛擬比賽收縮；推估")
# terrain-matched within-run δ (module docstring step 7), all 推估
DUR = {
    "win_m": 100.0,            # window length along the distance (grade_model.windows)
    "knots": (-0.15, -0.08, -0.02, 0.02, 0.08, 0.15),   # grade-spline knots (backtest GRADE_EDGES)
    "overlap_q": 10.0,         # the grades both halves cover: overlap of their p10–p90 ranges
    "min_windows": 30,         # usable windows per run
    "keep_min": 0.8,           # a window needs ≥ 80 % of its moving time in the cleaned samples
    "late_min_h": 0.5,         # moving time after T0 the slope needs
    "hr_lag_s": 60.0,          # athlete.HR_LAG_S
    "g_max": 0.40,
}
# within-activity δ (module docstring step 8, SP-722), all 推估 unless noted
INSIDE = {
    "win_m": 50.0,              # SP-716: 50 m windows along the distance
    "moving_min": 0.8,          # SP-716: a window needs ≥ 80 % of its elapsed time moving
    "keep_min": 0.8,            # ≥ 80 % of its moving time inside the drift-v2-cleaned samples (as DUR)
    "g_max": 0.45,              # SP-716: |grade| ≤ 45 %
    "v_range": (0.2, 7.0),      # m/s: GPS glitches out
    "warmup_h": 0.25,           # SP-716: the first 15 min moving skipped (warm-up)
    "grade_bin": 0.02,          # SP-716: the pooled grade curve, one median per 2 % bin
    "bin_min": 20,              # a bin needs ≥ 20 windows (else its windows are dropped)
    "min_windows": 20,          # usable windows per run
    "min_run_s": 1.5 * 3600.0,  # SP-716: shorter runs' slopes are noise (0.5 h after T0 at most)
    "late_min_h": 0.5,          # a run's spread of (t − T0)⁺ the slope needs (as DUR)
    "window_days": 3 * 365,     # durability changes slowly: three years of runs (the v₀ fit keeps 365 d)
    "prior_sd": 0.02,           # σ₀: the 0.05 /h prior counts as a measurement with SE 0.02 /h
    "hr_lag_s": 60.0,           # athlete.HR_LAG_S
}
INSIDE_SOURCE = ("耐久衰減：你自己步調的越野跑（跟團健行不算）每 50 m 視窗扣掉你的坡度曲線，在同一筆活動內對"
                 "「1 小時後的移動時數」回歸（每筆去截距、加心率項＝同坡度同心率），≥ 1.5 小時的活動合併成一條斜率，"
                 "長的活動權重自然大；再往 Clark 2019 的 5 %/h 先驗依精度收縮（先驗當作 ±2 %/h 的一次量測）；推估")
FUEL_YES = ("有補給", "補給", "吃膠", "能量膠", "fuelled", "fueled", "fuel", "gel")
FUEL_NO = ("沒補給", "無補給", "不補給", "沒吃", "空腹", "unfuelled", "unfueled", "no fuel", "fasted")


def _floor() -> float:
    """f_min: TRAILHR["v_floor"] kept inside (0, 1] (a 0 floor would let the speed reach 0)."""
    return min(1.0, max(1e-3, float(TRAILHR["v_floor"])))


def speed_mult(t_h: float, delta: float, t0: float = TRAILHR["t0_h"]) -> float:
    """v(t)/v₀ at moving hour t_h: max(f_min, 1 − δ·(t − T0)⁺) (SP-239)."""
    if delta <= 0 or t_h <= t0:
        return 1.0
    return max(_floor(), 1.0 - delta * (t_h - t0))


def dist_nodecay(T_h: float, delta: float, t0: float = TRAILHR["t0_h"]) -> float:
    """The distance covered in T_h moving hours, in no-decay hours (∫₀ᵀ v(t)/v₀ dt = T·D̄(T)):
    T up to T0; T − δ(T − T0)²/2 on the slope up to t1 = T0 + (1 − f_min)/δ; f_min per hour after
    (docs/research/long-race-durability-shape.md §4.1). Continuous, strictly increasing."""
    if T_h <= t0 or delta <= 0:
        return float(T_h)
    fm = _floor()
    t1 = t0 + (1.0 - fm) / delta
    if T_h <= t1:
        return T_h - delta * (T_h - t0) ** 2 / 2.0
    return t0 + (t1 - t0) * (1.0 + fm) / 2.0 + fm * (T_h - t1)


def dbar(T_h: float, delta: float, t0: float = TRAILHR["t0_h"]) -> float:
    """Mean speed multiplier over a run of T_h hours: linear-then-floor closed form (SP-239)."""
    if T_h <= t0 or delta <= 0:
        return 1.0
    return dist_nodecay(T_h, delta, t0) / T_h


def time_for_nodecay(tn_h: float, delta: float, t0: float = TRAILHR["t0_h"]) -> float:
    """The moving hours T with T·D̄(T) = tn_h (the inverse of dist_nodecay; tn_h = E / v₀, the
    no-decay time). Closed form, continuous and strictly increasing in tn_h:
    tn ≤ T0 → tn; on the slope T0 + 2u/(1 + √(1 − 2δu)) with u = tn − T0; past the floor
    t1 + (tn − dist(t1)) / f_min."""
    if tn_h <= t0 or delta <= 0:
        return float(tn_h)
    fm = _floor()
    t1 = t0 + (1.0 - fm) / delta
    s1 = dist_nodecay(t1, delta, t0)
    if tn_h <= s1:
        u = tn_h - t0
        return t0 + 2.0 * u / (1.0 + math.sqrt(max(0.0, 1.0 - 2.0 * delta * u)))
    return t1 + (tn_h - s1) / fm


def run_point(km, gain_m, moving_s, hr_avg, lthr) -> Optional[dict]:
    k = TRAILHR
    if not km or not moving_s or moving_s < k["min_moving_s"] or not hr_avg or not lthr:
        return None
    e = km + (gain_m or 0.0) / (k["divisor"] or effort_divisor())
    T = moving_s / 3600.0
    return {"x": hr_avg / lthr, "x_raw": hr_avg / lthr, "v": e / T, "T_h": T, "eff_km": e,
            "hr": float(hr_avg), "lthr": float(lthr)}


# ---- heat: the athlete's own β (engine/heat.HR_BETA, §A5 / §0.10 step 5) ----

def heat_shift(hadley: Optional[float], lthr: Optional[float]) -> float:
    """The HR-level shift of heat, in LTHR units: β·(Hadley − 120)/LTHR with
    the athlete's own β (engine/heat_calib.hr_beta: the route efforts' fit
    shrunk toward 0.3 bpm per Hadley unit). 0 without a Hadley or an LTHR, or with the switch off.
    Using it to move the HR model's x is 推估 (推估)."""
    if not TRAILHR["heat_beta"] or hadley is None or not lthr or not math.isfinite(hadley):
        return 0.0
    from backend.engine import heat as HT
    from backend.engine.heat_calib import hr_beta
    return hr_beta()["beta"] * (float(hadley) - HT.HR_BETA_REF) / float(lthr)


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


def terrain_windows(t, d_m, z, hr, moving, keep=None, win_m: float = DUR["win_m"],
                    hr_lag_s: float = DUR["hr_lag_s"]) -> list[dict]:
    """100 m windows along the distance (moving samples only): grade g,
    speed v (m/s), HR read hr_lag_s later on the elapsed clock, t_h = moving
    hours at the window start (every moving second counts, cleaned or not,
    so "hours after T0" is the run's own clock), keep = the share of the
    window's moving time inside `keep` (the cleaned samples)."""
    t = np.asarray(t, float)
    n = len(t)
    if n < 10:
        return []
    dt_ = np.diff(t, prepend=t[0])
    dt_[~np.isfinite(dt_) | (dt_ < 0) | (dt_ > 30)] = 0.0
    mv = np.asarray(moving, bool)[:n] & (dt_ > 0)
    kp = np.ones(n, bool) if keep is None else np.asarray(keep, bool)[:n]
    ct = np.cumsum(np.where(mv, dt_, 0.0))
    ck = np.cumsum(np.where(mv & kp, dt_, 0.0))
    hv = np.asarray(hr, float)[:n] if hr is not None else np.full(n, np.nan)
    okt = np.isfinite(t) & np.isfinite(hv) & (hv > 40)
    if okt.sum() < 10:
        return []
    lag = np.interp(t + hr_lag_s, t[okt], hv[okt])
    okh = mv & okt
    ch = np.cumsum(np.where(okh, lag * dt_, 0.0))
    chn = np.cumsum(np.where(okh, dt_, 0.0))
    d = np.maximum.accumulate(np.nan_to_num(np.asarray(d_m, float)[:n]))
    zz = np.asarray(z, float)[:n]
    ok = np.isfinite(zz)
    if ok.sum() < 10 or d[-1] < 2 * win_m:
        return []
    zf = np.interp(np.arange(n), np.nonzero(ok)[0], zz[ok])
    j = np.clip(np.searchsorted(d, np.arange(0.0, d[-1], win_m)), 0, n - 1)
    out = []
    for a, b in zip(j[:-1], j[1:]):
        tm, dd = ct[b] - ct[a], d[b] - d[a]
        if b <= a or tm <= 0 or dd <= 0.5 * win_m or chn[b] - chn[a] < 0.5 * tm:
            continue
        out.append({"g": float((zf[b] - zf[a]) / dd), "v": float(dd / tm), "t_h": float(ct[a]) / 3600.0,
                    "hr": float((ch[b] - ch[a]) / (chn[b] - chn[a])), "keep": float((ck[b] - ck[a]) / tm)})
    return out


def within_run_delta(wins, t0: float = TRAILHR["t0_h"]) -> Optional[dict]:
    """One run's terrain-matched δ (module docstring step 7): OLS of ln v on
    grade-bin intercepts, centred HR and the moving hours after T0, on the
    cleaned windows of the bins present both before and after the run's
    midpoint. {delta (per hour, + = slowing), se, n, bins, hr_coef} or None
    (too few windows, no time after T0, no shared terrain)."""
    k = DUR
    w = [x for x in wins or [] if x.get("keep", 1.0) >= k["keep_min"] and x.get("v", 0) > 0
         and x.get("hr") and abs(x["g"]) <= k["g_max"]]
    if len(w) < k["min_windows"]:
        return None
    th = np.array([x["t_h"] for x in w])
    if th.max() - t0 < k["late_min_h"]:
        return None
    mid = float(np.median(th))
    g = np.array([x["g"] for x in w])
    # like-for-like terrain: only the grades both halves of the run cover (the overlap of
    # their p10–p90 grade ranges) — a climb-first / descend-later course has little overlap
    e, l_ = g[th <= mid], g[th > mid]
    if len(e) < 5 or len(l_) < 5:
        return None
    lo = max(np.percentile(e, k["overlap_q"]), np.percentile(l_, k["overlap_q"]))
    hi = min(np.percentile(e, 100 - k["overlap_q"]), np.percentile(l_, 100 - k["overlap_q"]))
    sel = (g >= lo) & (g <= hi)
    if hi <= lo or sel.sum() < k["min_windows"] or (sel & (th <= mid)).sum() < 10 or (sel & (th > mid)).sum() < 10:
        return None
    th, gg = th[sel], g[sel]
    y = np.log([x["v"] for x, s in zip(w, sel) if s])
    hr = np.array([x["hr"] for x, s in zip(w, sel) if s])
    late = np.clip(th - t0, 0.0, None)
    if np.ptp(late) < k["late_min_h"]:
        return None
    # the terrain model: a linear spline in grade (knots inside the covered range only)
    knots = [q for q in k["knots"] if lo < q < hi]
    ub = knots
    hcol = [hr - hr.mean()] if np.ptp(hr) > 1.0 else []      # a flat HR trace carries no HR term
    X = np.column_stack([np.ones_like(gg), gg] + [np.clip(gg - q, 0.0, None) for q in knots]
                        + hcol + [late])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ coef
    dof = len(y) - X.shape[1]
    if dof < 5:
        return None
    s2 = float(res @ res) / dof
    try:
        cov = s2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    se = float(math.sqrt(max(cov[-1, -1], 0.0)))
    return {"delta": float(-coef[-1]), "se": se, "n": int(len(y)), "bins": len(ub) + 1, "hr_coef": float(coef[-2]) if hcol else None,
            "g_range": [float(lo), float(hi)]}


def pool_deltas(rows) -> Optional[dict]:
    """Random-effects mean of per-run δ ± SE (DerSimonian–Laird): {mean, se,
    ci95, tau, n}; None without rows. One run → its own value and SE."""
    r = [x for x in rows or [] if x and x.get("delta") is not None and x.get("se") and x["se"] > 0]
    if not r:
        return None
    d = np.array([x["delta"] for x in r])
    v = np.array([x["se"] ** 2 for x in r])
    w = 1.0 / v
    m_fe = float((w * d).sum() / w.sum())
    q = float((w * (d - m_fe) ** 2).sum())
    c = float(w.sum() - (w ** 2).sum() / w.sum())
    tau2 = max(0.0, (q - (len(r) - 1)) / c) if len(r) > 1 and c > 0 else 0.0
    wr = 1.0 / (v + tau2)
    m = float((wr * d).sum() / wr.sum())
    se = float(math.sqrt(1.0 / wr.sum()))
    return {"mean": m, "se": se, "ci95": [m - 1.96 * se, m + 1.96 * se], "tau": math.sqrt(tau2), "n": len(r)}


# ---- within-activity δ (module docstring step 8, SP-722) ---------------------

def inside_windows(t, d_m, z, hr, moving, keep=None, win_m: float = INSIDE["win_m"],
                   hr_lag_s: float = INSIDE["hr_lag_s"]) -> list[list[float]]:
    """50 m windows of one activity for inside_delta: [[grade, ln v (m/s), moving hours at the
    window start, HR read hr_lag_s later], …], only windows that pass the INSIDE filters (moving
    share, cleaned share, grade, speed, an HR). The warm-up is cut later (inside_delta), so the
    disk cache does not depend on it."""
    k = INSIDE
    t = np.asarray(t, float)
    n = len(t)
    if n < 10:
        return []
    dt_ = np.diff(t, prepend=t[0])
    dt_[~np.isfinite(dt_) | (dt_ < 0) | (dt_ > 30)] = 0.0
    mv = np.asarray(moving, bool)[:n] & (dt_ > 0)
    kp = np.ones(n, bool) if keep is None else np.asarray(keep, bool)[:n]
    cm = np.cumsum(np.where(mv, dt_, 0.0))
    ce = np.cumsum(dt_)
    ck = np.cumsum(np.where(mv & kp, dt_, 0.0))
    hv = np.asarray(hr, float)[:n] if hr is not None else np.full(n, np.nan)
    okt = np.isfinite(t) & np.isfinite(hv) & (hv > 40)
    if okt.sum() < 10:
        return []
    lag = np.interp(t + hr_lag_s, t[okt], hv[okt])
    okh = mv & okt
    ch = np.cumsum(np.where(okh, lag * dt_, 0.0))
    chn = np.cumsum(np.where(okh, dt_, 0.0))
    d = np.maximum.accumulate(np.nan_to_num(np.asarray(d_m, float)[:n]))
    zz = np.asarray(z, float)[:n]
    ok = np.isfinite(zz)
    if ok.sum() < 10 or d[-1] < 2 * win_m:
        return []
    zf = np.interp(np.arange(n), np.nonzero(ok)[0], zz[ok])
    j = np.clip(np.searchsorted(d, np.arange(0.0, d[-1], win_m)), 0, n - 1)
    lo, hi = k["v_range"]
    out = []
    for a, b in zip(j[:-1], j[1:]):
        mt, el, dd = cm[b] - cm[a], ce[b] - ce[a], d[b] - d[a]
        if b <= a or mt <= 0 or el <= 0 or dd < 0.5 * win_m:
            continue
        if mt / el < k["moving_min"] or (ck[b] - ck[a]) / mt < k["keep_min"] or chn[b] - chn[a] < 0.5 * mt:
            continue
        g, v = (zf[b] - zf[a]) / dd, dd / mt
        if abs(g) > k["g_max"] or not lo < v < hi:
            continue
        out.append([round(float(g), 4), round(math.log(v), 4), round(float(cm[a]) / 3600.0, 4),
                    round(float((ch[b] - ch[a]) / (chn[b] - chn[a])), 1)])
    return out


def _gbin(g: float) -> int:
    return int(round(g / INSIDE["grade_bin"]))


def grade_curve(acts) -> dict[int, float]:
    """The pooled grade curve g: {2 % bin: median ln v} over every activity's windows after the
    warm-up; bins with fewer than bin_min windows are left out."""
    by: dict[int, list] = {}
    for a in acts or []:
        for g, lv, h, _hr in a.get("wins") or []:
            if h >= INSIDE["warmup_h"]:
                by.setdefault(_gbin(g), []).append(lv)
    return {b: float(np.median(v)) for b, v in by.items() if len(v) >= INSIDE["bin_min"]}


def inside_delta(acts, t0: float = TRAILHR["t0_h"]) -> Optional[dict]:
    """The pooled within-activity δ (module docstring step 8). `acts` = [{"wins": inside_windows
    rows, "T_h": moving hours, "idx", "date"}] (the own-paced runs; all of them shape the grade
    curve, those ≥ min_run_s give the slope). Returns {raw (/h, + = slowing), se (activity-cluster),
    n (runs in the slope), n_curve (runs in the curve), windows, hr_coef, runs [{idx, date, T_h,
    lever}]} — lever = the run's Σ(L − L̄)², its share of the slope — or None (no usable run)."""
    k = INSIDE
    curve = grade_curve(acts)
    blocks = []
    for a in acts or []:
        if (a.get("T_h") or 0.0) * 3600.0 < k["min_run_s"]:
            continue
        w = [x for x in a.get("wins") or [] if x[2] >= k["warmup_h"] and _gbin(x[0]) in curve]
        if len(w) < k["min_windows"]:
            continue
        h = np.array([x[2] for x in w])
        L = np.clip(h - t0, 0.0, None)
        if np.ptp(L) < k["late_min_h"]:
            continue
        r = np.array([x[1] - curve[_gbin(x[0])] for x in w])
        hr = np.array([x[3] for x in w])
        X = np.column_stack([hr - hr.mean(), L - L.mean()])
        blocks.append((X, r - r.mean(), a, float(((L - L.mean()) ** 2).sum())))
    if not blocks:
        return None
    X = np.vstack([b[0] for b in blocks])
    y = np.concatenate([b[1] for b in blocks])
    xtx = X.T @ X
    try:
        inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError:
        return None
    c = inv @ (X.T @ y)
    # activity-cluster sandwich SE: neighbouring windows of one run are not independent
    meat = np.zeros((2, 2))
    for xb, yb, _a, _l in blocks:
        s = xb.T @ (yb - xb @ c)
        meat += np.outer(s, s)
    g = len(blocks)
    adj = g / (g - 1) if g > 1 else 1.0
    var = float((inv @ meat @ inv)[1, 1]) * adj
    # one run: no between-run spread to cluster on → its plain OLS SE
    if g == 1:
        res = y - X @ c
        var = float(res @ res) / max(1, len(y) - 2) * float(inv[1, 1])
    tot = sum(b[3] for b in blocks) or 1.0
    return {"raw": float(-c[1]), "se": math.sqrt(max(var, 0.0)), "n": g, "n_curve": len(acts or []),
            "windows": int(len(y)), "hr_coef": float(c[0]),
            "runs": [{"idx": b[2].get("idx"), "date": b[2].get("date"), "T_h": b[2].get("T_h"),
                      "lever": b[3] / tot} for b in blocks]}


def shrink_inside(info: Optional[dict]) -> dict:
    """The within-activity δ shrunk to the prior by precision (module docstring step 8):
    δ = w·raw + (1 − w)·prior, w = σ₀²/(σ₀² + SE²), clamped to 0…delta_max. No measurement →
    the prior (w 0). {delta, raw, se, n, w, prior, prior_sd, k (= SE²/σ₀², the prior's weight in
    units of the measurement), source}."""
    prior, sd = TRAILHR["delta_prior"], INSIDE["prior_sd"]
    out = {"delta": prior, "raw": None, "se": None, "n": 0, "w": 0.0, "prior": prior, "prior_sd": sd,
           "k": None, "source": INSIDE_SOURCE, "label": "推估"}
    if not info or info.get("raw") is None or not info.get("se") or not math.isfinite(info["raw"]):
        return out
    se = float(info["se"])
    w = sd ** 2 / (sd ** 2 + se ** 2)
    d = w * float(info["raw"]) + (1.0 - w) * prior
    out.update(delta=float(min(TRAILHR["delta_max"], max(0.0, d))), raw=float(info["raw"]), se=se,
               n=int(info.get("n") or 0), w=w, k=se ** 2 / sd ** 2,
               ci95=[float(info["raw"]) - 1.96 * se, float(info["raw"]) + 1.96 * se])
    return out


def effort_divisor() -> float:
    """The effort-km ascent divisor in use (TRAILHR override, else per athlete)."""
    from backend.engine.algorithms.effort import divisor_of
    return TRAILHR["divisor"] or divisor_of("fitted_run")


def effort_speed_series(t, d_m, z, moving, divisor: Optional[float] = None):
    """(moving-time axis s, effort-km speed m/s) for durability(): only
    moving samples, so rests do not count as time."""
    dv = divisor or effort_divisor()
    t = np.asarray(t, float)
    dt = np.diff(t, prepend=t[0])
    dt[~np.isfinite(dt) | (dt < 0) | (dt > 30)] = 0.0
    dd = np.diff(np.asarray(d_m, float), prepend=d_m[0])
    dz = np.diff(np.nan_to_num(np.asarray(z, float)), prepend=np.nan_to_num(z[0]))
    mv = np.asarray(moving, bool) & (dt > 0)
    tm = np.cumsum(np.where(mv, dt, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        # effort metres: d + gain·1000/divisor (E km = km + gain m / divisor)
        es = (np.clip(dd, 0, None) + np.clip(dz, 0, None) * 1000.0 / dv) / dt
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
    # SP-725: a run on harder terrain (its 地形難度 multiplier m on the effort km, fit_terrain) is put
    # back on 一般 terrain: it covered m × E effort km in its time, so its v₀ = m·v / D̄(T)
    v0 = np.array([p["v"] * p.get("terrain_mult", 1.0) / dbar(p["T_h"], d) for p in pts])
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
    """Moving seconds for eff_km at HR level x: T solves T = E / (v₀(x)·D̄(T)), taken as the
    closed-form inverse of the distance covered (time_for_nodecay, SP-239)."""
    v = v0_at(m, x)
    if not v or v <= 0 or not eff_km:
        return None
    d = m["delta"] if delta is None else delta
    return time_for_nodecay(eff_km / v, d) * 3600.0


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

def shrink_delta(deltas, ses=None) -> dict:
    """δ = (n·raw + k·prior)/(n + k), clamped to 0…delta_max; raw = the
    random-effects mean when per-run SEs are given (pool_deltas, with its
    95 % CI), else the median; with n and a warning when the raw δ exceeds
    3 × the prior."""
    k = TRAILHR
    pairs = [(float(d), s) for d, s in zip(deltas or [], ses if ses is not None else [None] * len(deltas or []))
             if d is not None and math.isfinite(d)]
    v = [d for d, _ in pairs]
    prior = k["delta_prior"]
    pool = pool_deltas([{"delta": d, "se": s} for d, s in pairs]) if ses is not None else None
    raw = pool["mean"] if pool else (float(median(v)) if v else None)
    n = len(v)
    d = prior if raw is None else (n * raw + k["delta_k"] * prior) / (n + k["delta_k"])
    d = float(min(k["delta_max"], max(0.0, d)))
    warn = None
    if raw is not None and raw > k["delta_warn_ratio"] * prior:
        warn = (f"量到的耐久衰減 {raw:.0%}/h 超過先驗 {prior:.0%}/h 的 {k['delta_warn_ratio']:g} 倍：長跑裡可能還有"
                "停等、走路或回程沒清掉（推估）")
    return {"delta": d, "raw_median": raw, "n": n, "prior": prior, "k": k["delta_k"], "warning": warn,
            "raw_ci95": pool["ci95"] if pool else None, "raw_se": pool["se"] if pool else None,
            "tau": pool["tau"] if pool else None, "method": "pooled" if pool else "median"}


def loo_error(points, delta: float, min_h: Optional[float] = None) -> Optional[float]:
    """Median |time error| of the given-HR prediction when each run is left
    out of the v₀ fit (the §0.4 rule: a parameter stays only when it lowers
    the leave-one-out error); None with < 4 runs. `min_h`: only the runs of
    at least that many moving hours are scored (all of them still fit v₀)."""
    pts = [p for p in points if p]
    if len(pts) < 4:
        return None
    errs = []
    for i, p in enumerate(pts):
        if min_h is not None and p["T_h"] < min_h:
            continue
        m = fit(pts[:i] + pts[i + 1:], delta)
        t = predict_time(m, p["eff_km"] * p.get("terrain_mult", 1.0), p["x"])
        if t:
            errs.append(abs(t / (p["T_h"] * 3600.0) - 1.0))
    return float(median(errs)) if errs else None


# ---- 地形難度 multipliers (SP-725; engine/terrain_difficulty.py) ----------------
# Per marked level (技術路段多 / 需要手腳並用): each marked run's ratio r = actual ÷ predicted no-decay
# time (= the effort-km multiplier that makes the model meet the run), the run left out of the v₀ fit
# and the other marked runs put back on 一般 terrain with the current multipliers (three passes from
# the prior); the level's multiplier = (n·median r + k·prior) / (n + k), clamped to MULT_RANGE.
# 一般 (marked or not) = 1. A level with no marked run = its prior (label 推估).

TERRAIN_PASSES = 3


def _with_mult(pts, mult: dict) -> list[dict]:
    return [{**p, "terrain_mult": mult.get(p.get("terrain") or "normal", 1.0)} for p in pts]


def _terrain_ratio(train, p, delta: float) -> Optional[float]:
    """r = actual ÷ predicted no-decay time of run p from a v₀ fitted on `train` (its D̄ undone)."""
    m = fit(train, delta)
    v = v0_at(m, p["x"])
    if not v or v <= 0 or not p.get("eff_km"):
        return None
    return dist_nodecay(p["T_h"], m["delta"]) * v / p["eff_km"]


def fit_terrain(points, delta: Optional[float], prior: Optional[dict] = None,
                k: Optional[float] = None) -> dict:
    """The 地形難度 multipliers from the runs' "terrain" level (see the block above): {"mult": {level:
    m}, "levels": {level: {mult, n, raw (median r), prior, label: 推估 | 你的紀錄, ratios}}, "k",
    "source"}. 一般 is always 1."""
    from backend.engine import terrain_difficulty as TD
    prior = prior or TD.PRIOR
    k = TD.SHRINK_K if k is None else float(k)
    lo, hi = TD.MULT_RANGE
    pts = [p for p in points or [] if p]
    d = float(min(TRAILHR["delta_max"], max(0.0, delta or 0.0)))
    marked = [lv for lv in TD.LEVELS if lv != "normal"]
    mult = {"normal": 1.0, **{lv: prior[lv] for lv in marked}}
    raws: dict = {lv: [] for lv in marked}
    if any(p.get("terrain") in marked for p in pts) and len(pts) >= 4:
        for _ in range(TERRAIN_PASSES):
            cur = _with_mult(pts, mult)
            raws = {lv: [] for lv in marked}
            for i, p in enumerate(pts):
                lv = p.get("terrain")
                if lv not in marked:
                    continue
                r = _terrain_ratio(cur[:i] + cur[i + 1:], p, d)
                if r is not None and math.isfinite(r) and r > 0:
                    raws[lv].append(r)
            for lv in marked:
                n = len(raws[lv])
                if n:
                    m = (n * float(median(raws[lv])) + k * prior[lv]) / (n + k)
                    mult[lv] = float(min(hi, max(lo, m)))
    levels = {"normal": {"mult": 1.0, "n": sum(1 for p in pts if p.get("terrain") == "normal"), "raw": None,
                         "prior": 1.0, "label": "定義"}}
    for lv in marked:
        n = len(raws[lv])
        levels[lv] = {"mult": mult[lv], "n": n, "raw": float(median(raws[lv])) if n else None,
                      "prior": prior[lv], "label": "你的紀錄" if n else "推估",
                      "ratios": [round(x, 4) for x in raws[lv]]}
    return {"mult": mult, "levels": levels, "k": k, "source": TD.SOURCE}


def terrain_loo(points, delta: Optional[float]) -> dict:
    """Leave-one-out check of the 地形難度 multipliers (SP-725 acceptance 3): every run marked
    技術路段多 / 需要手腳並用 predicted (at its own HR) from the others — once with no terrain at all
    (every run 一般) and once with the multipliers fitted WITHOUT it (nested: the run is out of both the
    v₀ fit and its level's median). {n, median_abs_plain, median_abs_terrain, rows [{idx, date, level,
    err_plain, err_terrain, mult}]}."""
    pts = [p for p in points or [] if p]
    d = float(min(TRAILHR["delta_max"], max(0.0, delta or 0.0)))
    rows = []
    for i, p in enumerate(pts):
        lv = p.get("terrain")
        if lv not in ("technical", "scramble"):
            continue
        others = pts[:i] + pts[i + 1:]
        act = p["T_h"] * 3600.0
        plain = [{**q, "terrain_mult": 1.0} for q in others]
        tp = predict_time(fit(plain, d), p["eff_km"], p["x"])
        tf = fit_terrain(others, d)
        mi = tf["mult"].get(lv, 1.0)
        tt = predict_time(fit(_with_mult(others, tf["mult"]), d), p["eff_km"] * mi, p["x"])
        if tp and tt:
            rows.append({"idx": p.get("idx"), "date": p.get("date"), "level": lv, "mult": mi,
                         "err_plain": tp / act - 1.0, "err_terrain": tt / act - 1.0})
    ap = [abs(r["err_plain"]) for r in rows]
    at = [abs(r["err_terrain"]) for r in rows]
    return {"n": len(rows), "median_abs_plain": float(median(ap)) if ap else None,
            "median_abs_terrain": float(median(at)) if at else None, "rows": rows}


GATE_MIN_RUNS = 4     # 推估: the long-run gate needs ≥ 4 runs ≥ 1.5 h to score on (loo_error's floor)


def choose_delta(points, dinfo: dict) -> dict:
    """The measured-and-shrunk δ only when its leave-one-out error is lower
    than the prior's (§0.4) on the runs ≥ 1.5 h (SP-722; with fewer than
    GATE_MIN_RUNS of them, the prior); else the prior 0.05 /h. Returns
    {delta, choice, loo_measured, loo_prior, loo_min_h}."""
    prior = TRAILHR["delta_prior"]
    d = dinfo["use"]
    # SP-722: the evidence behind `use` — the within-activity runs when that estimate is used
    n = dinfo.get("n_evidence", dinfo["all"]["n"])
    if n == 0 or abs(d - prior) < 1e-9:
        return {"delta": d, "choice": "prior" if n == 0 else "measured",
                "loo_measured": None, "loo_prior": None, "loo_min_h": None}
    # SP-722: scored only on the runs δ acts on (moving ≥ INSIDE min_run_s; δ starts after T0 = 1 h)
    # — the short runs, which δ hardly touches, must not let through a δ that makes the long ones
    # worse; with fewer than GATE_MIN_RUNS of them nothing shows the δ helps → the prior
    long_ = [p for p in points if p and p["T_h"] * 3600.0 >= INSIDE["min_run_s"]]
    mh = INSIDE["min_run_s"] / 3600.0
    if len(long_) < GATE_MIN_RUNS:
        return {"delta": prior, "choice": "prior", "loo_measured": None, "loo_prior": None, "loo_min_h": mh,
                "too_few_long": len(long_)}
    lm, lp = loo_error(points, d, mh), loo_error(points, prior, mh)
    if lm is not None and lp is not None and lm < lp:
        return {"delta": d, "choice": "measured", "loo_measured": lm, "loo_prior": lp, "loo_min_h": mh}
    return {"delta": prior, "choice": "prior", "loo_measured": lm, "loo_prior": lp, "loo_min_h": mh}


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
    prediction should take (fuelled when split, races are fuelled). Rows
    with an "se" (terrain-matched δ) are pooled by random effects."""
    has_se = bool(rows) and all(r.get("se") for r in rows)

    def sh(rs):
        return shrink_delta([r["delta"] for r in rs], [r["se"] for r in rs] if has_se else None)
    allv = sh(rows)
    yes = [r for r in rows if r.get("fuel") is True]
    no = [r for r in rows if r.get("fuel") is False]
    out = {"all": allv, "split": False, "use": allv["delta"], "n_fuelled": len(yes), "n_unfuelled": len(no)}
    mn = TRAILHR["fuel_min_runs"]
    if len(yes) >= mn and len(no) >= mn:
        fy, fn = sh(yes), sh(no)
        out.update(split=True, fuelled=fy, unfuelled=fn, use=fy["delta"])
    return out


# ---- the fade shape for the segment ETAs (SP-222) ----------------------------
# docs/research/trail-pacing-strategy.md §1.2 items 1 & 7, §2.2, §4.2. The whole-race time
# already holds the durability decline (D̄(T) above); the segments used to share it evenly, so
# the early aid stations' ETAs were late. The owner (2026-10-05): the shape and its size come
# from the athlete's OWN records — no population shape; too little data → the segments stay
# evenly paced. All 推估.
#   per run ≥ FADE["min_run_s"] moving: step 7's 100 m windows (drift-v2-cleaned, HR 60 s later)
#   and the within-run OLS
#       ln v = f(g) + c·(HR_lag − mean) + Σ_b β_b·[moving hour b]        (b ≥ 1; hour 0 = reference)
#   on the grades both halves of the run cover — β_b = the speed in hour b at the same grade and
#   the same HR as in the first hour (fade_run);
#   across runs: per hour the random-effects mean of the runs that reach it (pool_deltas), hours
#   1, 2, … in a row while ≥ min_runs runs reach them; the whole shape shrunk toward even by
#   N/(N + k) (N = the usable runs, k as δ's); held flat after the last hour the data reaches (no
#   extrapolation — a race longer than the long runs gets no extra late fade), floored at
#   dbar_min (fade_shape);
#   the race: each segment's time ÷ the shape's multiplier at its midpoint (moving hours), then
#   all scaled so the sum stays the whole-race time (fade_times).
# Not the line of step 2 (the whole-race D̄, a population shape since SP-239 floors it at v_floor):
# the segments take the athlete's own shape, rescaled so their sum is the whole-race time.
FADE = {
    "min_run_s": 3 * 3600.0,    # 推估: a run must reach its third hour to show a late fade
    "min_runs": 3,              # 推估: fewer usable runs → no fade, the segments evenly paced
    "bin_h": 1.0,               # 推估: one shape point per moving hour
    "min_bin_windows": 10,      # 推估: 100 m windows (≈ 1 km) a run needs in an hour to count it
    "k": 3.0,                   # 推估: = delta_k — the shrinkage toward even
}
FADE_SOURCE = ("後段變慢的形狀：你自己 ≥ 3 小時的越野跑，同坡度、同心率下每一小時的速度和第 1 小時比（100 m 視窗，"
               "清掉停等與收操），各次合併後往「不變慢」收縮；資料沒涵蓋的小時維持最後一個值；推估")


def fade_run(wins, bin_h: float = FADE["bin_h"]) -> Optional[dict]:
    """One run's speed by moving hour at the same grade and HR (the SP-222 block above):
    {"bins": [[hour b, β_b, se], …] for b ≥ 1, "n", "hours"} or None (too few windows, no
    shared terrain, fewer than three hours with FADE["min_bin_windows"] windows)."""
    k = DUR
    w = [x for x in wins or [] if x.get("keep", 1.0) >= k["keep_min"] and x.get("v", 0) > 0
         and x.get("hr") and abs(x["g"]) <= k["g_max"]]
    if len(w) < k["min_windows"]:
        return None
    th = np.array([x["t_h"] for x in w])
    g = np.array([x["g"] for x in w])
    mid = float(np.median(th))
    e, l_ = g[th <= mid], g[th > mid]
    if len(e) < 5 or len(l_) < 5:
        return None
    lo = max(np.percentile(e, k["overlap_q"]), np.percentile(l_, k["overlap_q"]))
    hi = min(np.percentile(e, 100 - k["overlap_q"]), np.percentile(l_, 100 - k["overlap_q"]))
    if hi <= lo:
        return None
    sel = (g >= lo) & (g <= hi)
    b = np.floor(th / bin_h).astype(int)
    keep = [int(x) for x in sorted(set(b[sel])) if int(((b == x) & sel).sum()) >= FADE["min_bin_windows"]]
    if 0 not in keep or len(keep) < 3:
        return None
    sel &= np.isin(b, keep)
    gg, bb = g[sel], b[sel]
    y = np.log([x["v"] for x, s in zip(w, sel) if s])
    hr = np.array([x["hr"] for x, s in zip(w, sel) if s])
    knots = [q for q in k["knots"] if lo < q < hi]
    hcol = [hr - hr.mean()] if np.ptp(hr) > 1.0 else []
    later = [x for x in keep if x != 0]
    X = np.column_stack([np.ones_like(gg), gg] + [np.clip(gg - q, 0.0, None) for q in knots] + hcol
                        + [(bb == x).astype(float) for x in later])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    dof = len(y) - X.shape[1]
    if dof < 5:
        return None
    res = y - X @ coef
    try:
        cov = float(res @ res) / dof * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    n = len(later)
    se = np.sqrt(np.clip(np.diag(cov)[-n:], 0.0, None))
    return {"bins": [[int(x), float(c), float(s)] for x, c, s in zip(later, coef[-n:], se)],
            "n": int(len(y)), "hours": float(th.max())}


def fade_shape(rows, n_long: Optional[int] = None) -> dict:
    """The athlete's fade shape from fade_run() rows (the SP-222 block above): {"applied", "points"
    [[moving hour, speed multiplier], …] (the first hour = 1.0), "bins", "n_runs" (usable runs),
    "n_long" (runs ≥ min_run_s looked at), "min_runs", "min_run_h", "shrink", "source"}.
    "applied" False (and only the first point) with fewer than min_runs usable runs."""
    f = FADE
    rs = [r for r in rows or [] if r and r.get("bins")]
    n = len(rs)
    out = {"applied": False, "points": [[0.5 * f["bin_h"], 1.0]], "bins": [], "n_runs": n,
           "n_long": n if n_long is None else int(n_long), "min_runs": f["min_runs"],
           "min_run_h": f["min_run_s"] / 3600.0, "shrink": None, "source": FADE_SOURCE, "label": "推估"}
    if n < f["min_runs"]:
        return out
    w = n / (n + f["k"])
    out["shrink"] = w
    lo = TRAILHR["dbar_min"]
    b = 1
    while True:
        got = [{"delta": c, "se": s} for r in rs for x, c, s in r["bins"] if x == b and s and s > 0]
        if len(got) < f["min_runs"]:
            break
        p = pool_deltas(got)
        m = float(min(1.0 / lo, max(lo, math.exp(w * p["mean"]))))
        out["points"].append([(b + 0.5) * f["bin_h"], m])
        out["bins"].append({"hour": b, "mean": p["mean"], "se": p["se"], "n": p["n"], "mult": m})
        b += 1
    out["applied"] = len(out["points"]) > 1
    return out


def fade_mult(shape: dict, hours) -> np.ndarray:
    """The shape's speed multiplier at `hours` (moving): linear between the hour midpoints, flat
    before the first and after the last."""
    xs = [p[0] for p in shape["points"]]
    ys = [p[1] for p in shape["points"]]
    return np.interp(np.asarray(hours, float), xs, ys)


def fade_times(ts, shape: Optional[dict], tol_s: float = 0.01) -> Optional[list[float]]:
    """Segment moving times `ts` (s) with the fade: tᵢ ÷ the multiplier at the segment's midpoint
    hour, all scaled so the sum stays Σ ts (fixed point: the midpoints move with the times). None
    when the shape is not applied."""
    if not shape or not shape.get("applied") or not ts:
        return None
    base = [float(t) for t in ts]
    total = sum(base)
    if total <= 0:
        return None
    out = list(base)
    for _ in range(20):
        mids = np.cumsum(out) - 0.5 * np.asarray(out)
        raw = np.asarray(base) / fade_mult(shape, mids / 3600.0)
        new = list(raw * (total / raw.sum()))
        done = max(abs(a - c) for a, c in zip(new, out)) < tol_s
        out = new
        if done:
            break
    return out


# ---- SP-723: the segment split by the athlete's own slope cost (downhill included) -----------------
# The effort km (km + climb ÷ divisor) has no downhill term, so splitting a race time by it puts too
# little time on the descents and too much on the steep climbs. Inside one activity, the athlete's
# moving time of a segment ÷ (its effort km × the activity's mean time per effort km) by grade
# (SP-701 on this athlete's trail runs: steep down 1.61, down 1.13, flat 1.06, up 0.94, steep up 0.74)
# is the cost curve; the plan keeps its whole-race time (the HR model) and only re-splits it by
# effort km × cost(grade). Per grade bin the athlete's own ratio (moving-time weighted over the
# own-paced trail runs) is shrunk to the default with K_MIN minutes of evidence; between the bin
# centres the cost is linear, flat outside. Leave-one-run-out (SP-723, 107 runs, total known): the
# cumulative-time error at the segment ends 2.60 % → 2.10 % of the run (median 2.29 → 1.77 %; the
# power model's split at one power → this one, with the athlete's own effort divisor). The
# default curve and K are 推估 (one athlete's data; no literature for a within-run descent cost on
# trail); 鋪面 (trail vs road descents) waits for the SP-709 dictionary.
SLOPE = {
    "centers": (-0.20, -0.10, 0.0, 0.10, 0.20),
    "default": (1.61, 1.13, 1.06, 0.94, 0.74),     # 推估: SP-701 (this athlete, within-activity)
    "k_min": 60.0,                                # 推估: pseudo-minutes of the default per bin
    "min_runs": 3,                                # fewer own runs → the default alone
}
SLOPE_SOURCE = ("分段時間：整場時間照心率配速模型，只依你自己的坡度成本重新分配（同一筆活動裡各坡度的實際時間 ÷ 努力距離的平均時間，"
                "含下坡；陡下、下、平、上、陡上五段之間線性內插，資料少時往預設收縮）；預設值來自一位跑者的越野紀錄（SP-701），推估")


def slope_weights(g: float) -> list[float]:
    """Grade g on the five bin centres (linear between neighbours, flat outside)."""
    c = SLOPE["centers"]
    g = min(max(float(g), c[0]), c[-1])
    w = [0.0] * len(c)
    for i in range(len(c) - 1):
        if c[i] <= g <= c[i + 1]:
            f = (g - c[i]) / (c[i + 1] - c[i])
            w[i], w[i + 1] = 1.0 - f, f
            return w
    w[-1] = 1.0
    return w


def slope_row(segs: list[dict], times: list[float], divisor: Optional[float] = None) -> Optional[dict]:
    """One run's evidence: per bin Σ (weight × actual moving s) and Σ (weight × effort km × the run's
    mean s per effort km). `segs` = course segments (grade, dist_m, gain_m), `times` their actual
    moving seconds. None without time or effort."""
    div = divisor or effort_divisor()
    e = [s["dist_m"] / 1000.0 + (s.get("gain_m") or 0.0) / div for s in segs]
    T, E = float(sum(times)), float(sum(e))
    if T <= 0 or E <= 0:
        return None
    pace = T / E
    n = len(SLOPE["centers"])
    num, den = [0.0] * n, [0.0] * n
    for s, t, ei in zip(segs, times, e):
        for i, x in enumerate(slope_weights(s["grade"])):
            num[i] += x * float(t)
            den[i] += x * ei * pace
    return {"num": num, "den": den, "T": T}


def fit_slope_cost(rows) -> dict:
    """{cost: [5], own: [5 | None], minutes: [5], n_runs, applied, source, label} from slope_row()s;
    fewer than min_runs runs → the default curve (applied too: the default is the athlete's own
    SP-701 curve, 推估)."""
    rs = [r for r in rows or [] if r]
    n = len(SLOPE["centers"])
    d = list(SLOPE["default"])
    if len(rs) < SLOPE["min_runs"]:
        return {"cost": d, "own": [None] * n, "minutes": [0.0] * n, "n_runs": len(rs), "applied": True,
                "source": SLOPE_SOURCE, "label": "推估"}
    num = [sum(r["num"][i] for r in rs) for i in range(n)]
    den = [sum(r["den"][i] for r in rs) for i in range(n)]
    own = [num[i] / den[i] if den[i] > 0 else None for i in range(n)]
    mins = [x / 60.0 for x in den]
    k = SLOPE["k_min"]
    cost = [(mins[i] * own[i] + k * d[i]) / (mins[i] + k) if own[i] is not None else d[i] for i in range(n)]
    return {"cost": cost, "own": own, "minutes": mins, "n_runs": len(rs), "applied": True,
            "source": SLOPE_SOURCE, "label": "推估"}


def slope_cost(fit: Optional[dict], g: float) -> float:
    """The cost multiplier at grade g (the default curve without a fit)."""
    c = (fit or {}).get("cost") or SLOPE["default"]
    return float(sum(w * x for w, x in zip(slope_weights(g), c)))


def slope_times(segs: list[dict], ts, fit: Optional[dict], divisor: Optional[float] = None) -> Optional[list[float]]:
    """SP-723: segment times `ts` re-split by effort km × cost(grade) × the segment's environment
    factor relative to the race (segment "M_rel" = M̄ ÷ Mᵢ, set by the planner: altitude / heat keep
    slowing their own segments); the sum is unchanged. None when not applied."""
    if not fit or not fit.get("applied") or not ts or len(ts) != len(segs):
        return None
    div = divisor or effort_divisor()
    total = float(sum(ts))
    if total <= 0:
        return None
    e = [s["dist_m"] / 1000.0 + (s.get("gain_m") or 0.0) / div for s in segs]
    raw = [ei * slope_cost(fit, s["grade"]) * float(s.get("M_rel") or 1.0) for s, ei in zip(segs, e)]
    sr = sum(raw)
    if sr <= 0:
        return None
    return [x * total / sr for x in raw]
