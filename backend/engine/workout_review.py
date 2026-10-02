"""
Single-activity review — the 判讀卡 on each 單次活動 dashboard
(views/workout.json, chart kind "review"; docs/plans/done-workout-review.plan.md).

What one expression can't do lives here:

classify
    The session type (easy / long / quality / test_cp / test_aet, or the
    category for strength / bike / walk), the terrain, and the training phase
    on the activity date.
drift_of
    Pa:HR decoupling of a steady run, first 10 minutes (the warm-up)
    excluded — later after a city start: 60 s after the last stop in the
    first 20 min when that costs no tier (steady_start, 自組). Refuses runs where the number means nothing: hilly (≥ 20 m
    climbed per km, or a trail run), stopped (> 5 % of the time standing),
    too short (< 40 min of moving time *after* the warm-up — Uphill
    Athlete's 40–60 min is the test after the warm-up), unsteady (30-s
    power CV > 15 %, steady_drift's rule), too hard (> 90 % CP,
    steady_drift's rule), a fast finish (last 10 % of the measured time
    > 5 % faster than the rest, power or pace — 自組, doc §6.2) or hot
    (> 25 °C: the route_weather archive's air temperature, else the watch's
    — 徐國峰's condition, applied here 自組; heat_gate, re-applied on every
    measure() read so a later archive fill counts). Pw:HR (power / HR)
    comes out of the same call, with the same rules and the same samples
    and halves; the aerobic card shows the basis the viewer's 配速／功率
    toggle picked.
detect_efforts
    Work bouts in the power stream (30-s power over max(0.85 CP, 1.12 ×
    the session median)), with duration, power, %CP, HR and the HR drop in
    the 60 s after each one.
form_drift
    First ⅓ vs last ⅓ of the moving time for ILR, LSS, kleg, GCT, cadence,
    VO and impact G — the knee / form card, which is a reference only.
baseline
    The same session type over the previous 8 weeks (12 when 8 has fewer
    than 5): median and IQR. Fewer than 5 samples: no comparison.
review(ds, w, section)
    The JSON the viewer's draw() already renders: `value` rows (strings),
    `values` columns (a table), `points` (the durability curve), `empty`.

Per-workout measurements are memoised on disk with Dataset.cached_series; the
phase, classification, baselines and verdicts are recomputed on each call
(they depend on the plan and on other workouts).

The verdicts follow the plan's rules (Uphill Athlete AeT / drift, Palladino
power bands, 徐國峰 drift < 10 % before intervals). They are coaching
heuristics, not medical advice — the knee / form card always says 參考.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import statistics
from typing import Callable, Optional, Sequence

import numpy as np

from backend.engine.algorithms.classify import TRAIL_CLIMB_RATE_M_PER_KM
from backend.engine.algorithms.climbs import detect_climbs
from backend.engine.algorithms.threshold_estimate import (
    AET_MAX_OF_CP, AET_MAX_POWER_CV, WARMUP_S,
)
from backend.engine.panels.workout import MAX_DT, durability, grade_bins

# cached_series keys on the file and the thresholds, not on this code: bump the
# version whenever _measure's output changes
# v6 (two branches): Pw:HR on drift_of's own halves (pw_drift / pw_ok / p1 / p2), and cp_bouts
# per CP-test protocol (engine/cp_protocols.py); v7 = both merged, so no cache from either v6 is reused
# v8: drift_of's 40 min counted after the warm-up, fast-finish refusal, Pa/Pw on one shared window,
# watch_temp_c for heat_gate
# v9: two tiers — drift / pw_drift also on 30–40 min after the warm-up (`ref_ok`, `tier` "ref")
# v10: adaptive start — drift_of's window starts after the last stop in the first 20 min (`warmup_s`, `start_shift`)
# v11: drift v2 (docs/research/drift-algorithm.md) — trailing idle cut, return-leg city tail as cool-down, VI /
# walk / halves-power gate on the window, SE (drift_se / pw_drift_se), ramp-free comparison (`ramps`)
# v13: climbs carry start/end km, elevations, avg power, moving pace and GAP (the 爬坡段 profile);
# `grade_bins` per workout (the 坡度分組 baseline)
# v14: two branches — the interval library's reps, and `form_bins` (跑姿分組: form metrics per grade bin and
# per 10 % of the work done); v15 = both merged, plus form_bins' `impact_km` (每公里衝擊量, impact_per_km)
# v16: `cad_windows` (steady 30-s ILR / cadence / speed / grade windows, the 步頻與衝擊 card)
CACHE_KEY = "workout_review_v16"

# categories that can be a quality session (session_type's `runs`)
QUALITY_CATEGORIES = ("road", "trail", "hike")

DRIFT_MIN_S = 2400            # ≥ 40 min of moving time *after* the warm-up (UA: "We don't recommend
                              # relying on tests less than 40 minutes long" — the test after a 10–15′ warm-up)
                              # = the 嚴格 / test tier: `ok`, the only tier gates and thresholds read
DRIFT_REF_MIN_S = 1800        # 自組 — the 參考 / reference tier: ≥ 30 min after the warm-up (`ref_ok`,
                              # `tier` "ref"). No source gives 30 min: Coyle & González-Alonso 2001
                              # (Exerc Sport Sci Rev 29:88, doi 10.1097/00003677-200104000-00009) show
                              # cardiovascular drift starting after ~10–20 min of exercise, so 30 min
                              # after the warm-up shows some of it; UA's 40 min is for a formal AeT test.
                              # Every other refusal (heat, hills, stops, fast finish, CV, intensity,
                              # power coverage) applies to both tiers. Display only, never a gate.
REF_LABEL = "參考（暖身後 30–40 分，未達 UA 測試標準）"
REF_TIP = ("暖身後只有 30–40 分鐘：Uphill Athlete 不建議用短於 40 分的測試判定 AeT，所以這個數字只當參考，"
           "不拿來解鎖間歇、不算 AeT 測試、不寫進門檻。30 分是自組的門檻（未找到來源）：心血管飄移約在運動"
           "10–20 分鐘後開始（Coyle & González-Alonso 2001），暖身後 30 分已看得到一部分。")
START_TIP = ("出門先過市區路口、到河濱才開始穩定跑時，前 20 分鐘內的停等不算穩定跑：飄移從最後一次停等後 1 分鐘、"
             "而且至少第 10 分鐘起算。20 分鐘和 1 分鐘都是自組（未找到來源；UA／Friel 只說要排除暖身）。"
             "坡道、快步不排除：上坡後心率不一定回得來，排除會把真的影響藏起來。")
DRIFT_FINISH_SHARE = 0.10    # 自組 (doc §6.2 / §7): the last 10 % of the measured time …
DRIFT_FAST_FINISH = 0.05      # … > 5 % above the rest (power or pace) = a fast finish, refused
DRIFT_HEAT_C = 25.0           # 徐國峰 < 25 °C (Lafrenz 2008: HR +11 % at 35 °C vs +2 % at 22 °C); in drift_of 自組
DRIFT_POWER_COVER = 0.95      # 自組: Pw:HR only when power covers ≥ 95 % of the Pa:HR window (same samples)
DRIFT_EARLY_S = 1200          # 自組 (adaptive start): stops that begin in the first 20 min are the city section
                              # before a steady path (crossings), not the steady run; no source gives 20 min
DRIFT_SETTLE_S = 60           # 自組: the window starts 60 s after the last of those stops ends (re-acceleration);
                              # no sourced settle time. UA / Friel only say "exclude the warm-up".
                              # Never later than needed: start = max(WARMUP_S, that); and never at the cost of a
                              # tier — when the later start leaves less moving time than a tier needs that the
                              # fixed 10-min start reaches, the fixed start is kept (the stops then count
                              # toward the 5 % stop rule, as before)
# ---- drift v2 (docs/research/drift-algorithm.md, "DRIFT"; every number below is 推估 — no source — unless it says
# otherwise; the research doc calls these 自組)
DRIFT_IDLE_S = 120            # 推估 (DRIFT §7.4): trailing non-moving time ≥ 2 min (the watch not stopped) is cut,
                              # and not counted toward the 5 % stop rule (precedent: aet_test.analyze's trim)
DRIFT_TAIL_S = 720            # 推估, calibrated on the user's runs (DRIFT §7.1, 8 weeks 15/15): the cool-down = the
DRIFT_TAIL_GAP_S = 360        # last cluster of stops that start ≤ 12 min before the end and ≤ 6 min apart (and after
                              # DRIFT_EARLY_S), excluded to the end. Excluding the cool-down: UA, Friel (coach);
                              # intervals.icu drops the last 10 min by default (platform)
DRIFT_MAX_VI = 1.04           # 推估, calibrated (DRIFT §4.4): VI = NP30 / AP on the measured window's moving samples
                              # (treadmill 1.003, strict 1.004, steady p90 1.019–1.020, run-walk ≥ 1.064). VI: Coggan (coach)
WALK_FRAC = 0.75              # 推估 (DRIFT §4.3): a slow stretch = 30-s speed < 75 % of the window's median moving
WALK_SEG_S = 60               # speed for ≥ 60 s; the longest ≥ 180 s = a run-walk, refused (UA / Evoke: hold the
WALK_MAX_S = 180              # effort, coach — the numbers are ours)
DRIFT_HALVES_DIFF = 0.05      # 推估 number (DRIFT §4.4): |P2 − P1| / P1 ≤ 5 % over the halves (pace without power);
                              # UA / Evoke fixed effort, Palladino symmetric pacing (coach)
DRIFT_TAU_S = 60.0            # 推估 (DRIFT §2.5, the user's max-likelihood τ), inside the literature's 55–70 s
                              # (Hunt 2015, Hunt 2019, Wang & Hunt 2021 — peer-reviewed). Only for the SE.
DRIFT_NOISY_SE = 0.05         # 推估 (DRIFT §8.3): SE > 5 pp → 「這次很吵，只當聚合的一個點」
RAMP_GRADE = 0.03             # 推估 (DRIFT §1.2): |grade over a 15-s span| ≥ 3 % for ≥ 10 s = a ramp; the
RAMP_SPAN_S = 15              # ramp-free comparison also drops the 120 s after each (≈ 2τ). Ramps are NOT excluded
RAMP_MIN_S = 10               # from the drift (the user's decision): the ramp-free value and the per-half counts
RAMP_AFTER_S = 120            # are display only
TEMP_SRC_LABEL = {"route_weather": "路線天氣（Open-Meteo 檔案）", "watch": "手錶溫度"}
DRIFT_GOOD = 0.05
DRIFT_WATCH = 0.10
STREAK_NEED = 3               # legacy only: the old unsourced 「連續 3 次」 rule (gate: engine/quality_gate.py)
STOP_KMH = 1.6                # WKO5's moving threshold (1 mph)
MAX_STOPPED_SHARE = 0.05
AET_MARGIN = 3.0              # "easy" = avg HR ≤ AeT + 3
OVER_AET_SHARE = 0.10
LONG_MIN_S = 75 * 60
TEST_AET_MIN_S = 55 * 60
EFFORT_MIN_S = 60
EFFORT_GAP_S = 30
FADE = 0.05
HR_DROP_MIN = 20.0
CP_DELTA = 0.03
CLIMB_BETTER = 0.05
LAST20_MIN = 0.90
STEEP_DOWN = -0.10
STEEP_DOWN_SHARE = 0.30
BASE_MIN_N = 5
BASE_WEEKS = (8, 12)

# Palladino / Friel run power bands (×CP); sub-threshold is the rung below
# threshold (zones.WORKOUT_TARGETS has threshold 0.95–1.01, supra 1.01–1.06,
# VO2max 1.06–1.16).
BANDS = (("閾值下", 0.88, 0.95), ("閾值", 0.95, 1.01), ("超閾值", 1.01, 1.06),
         ("VO2max", 1.06, 1.16), ("無氧", 1.16, 9.0))

TYPE_LABEL = {"easy": "輕鬆跑", "long": "長時間", "quality": "品質課（間歇）",
              "test_cp": "CP 測試", "test_aet": "AeT 飄移測試", "strength": "肌力",
              "bike": "騎車", "walk": "走路", "other": "其他"}
TERRAIN_LABEL = {"road": "路跑", "trail": "越野", "hike": "登山健行"}
PHASE_LABEL = {"transition": "轉換期", "recovery": "恢復期", "base": "基礎期",
               "specific": "專項期", "taper": "減量期", "event": "比賽週"}

# dashboard order in views/workout.json
SECTIONS = ("summary", "aerobic", "intervals", "climbs", "durability", "form")
EXTRA_SECTIONS = ("grades", "pacing", "durability_curve", "cp_test",
                  # 間歇判讀 (engine/interval_eval.py)
                  "interval_verdict", "interval_reps", "interval_power", "interval_battery", "interval_tiz",
                  "interval_hr", "wprime_battery",
                  # 跑姿依坡度／隨疲勞 (form_bins)
                  "form_grades", "form_work", "form_cadence")
SUGGESTED = {"easy": 1, "long": 1, "test_aet": 1, "quality": 2, "test_cp": 2}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _nan_free(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, (np.floating,)):
        return _nan_free(float(x))
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, dict):
        return {k: _nan_free(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_nan_free(v) for v in x]
    return x


def _hms(s) -> str:
    s = _f(s)
    if s is None:
        return "–"
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _pace(s_per_km) -> str:
    s = _f(s_per_km)
    if s is None or s <= 0 or s > 3600:
        return "–"
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def _pct(x, d=1, sign=False) -> str:
    x = _f(x)
    if x is None:
        return "–"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def _num(x, d=0) -> str:
    x = _f(x)
    return "–" if x is None else f"{x:.{d}f}"


def _arr(a, n: int) -> np.ndarray:
    if a is None:
        return np.full(n, np.nan)
    a = np.asarray(a, dtype=float)
    if len(a) >= n:
        return a[:n]
    return np.concatenate([a, np.full(n - len(a), np.nan)])


def _has(a) -> bool:
    return a is not None and bool(np.isfinite(np.asarray(a, dtype=float)).any()) and \
        bool((np.nan_to_num(np.asarray(a, dtype=float)) != 0).any())


def _dt(t: np.ndarray) -> np.ndarray:
    d = np.diff(t, prepend=t[0] if len(t) else 0.0)
    return np.where(np.isfinite(d) & (d > 0), d, 0.0)


def moving_mask(t, speed=None) -> np.ndarray:
    """Moving samples: a normal sample interval (≤ 30 s) and, when there is a
    speed channel, above WKO5's 1 mph."""
    t = np.asarray(t, dtype=float)
    d = np.diff(t, prepend=t[0] if len(t) else 0.0)
    ok = np.isfinite(d) & (d > 0) & (d <= MAX_DT)
    if speed is not None:
        s = _arr(speed, len(t))
        ok &= ~(np.isfinite(s) & (s <= STOP_KMH))
    return ok


def _wmean(v: np.ndarray, w: np.ndarray, m: np.ndarray) -> Optional[float]:
    ok = m & np.isfinite(v) & (v > 0)
    tw = w[ok].sum()
    return float((v[ok] * w[ok]).sum() / tw) if tw > 0 else None


def _grid1(t, x) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """x on a 1-s grid (linear), NaN where the source has a gap > 30 s."""
    t = np.asarray(t, dtype=float)
    x = _arr(x, len(t))
    ok = np.isfinite(t) & np.isfinite(x)
    if ok.sum() < 2:
        return None, None
    tt, xx = t[ok], x[ok]
    grid = np.arange(tt[0], tt[-1] + 1.0)
    y = np.interp(grid, tt, xx)
    j = np.clip(np.searchsorted(tt, grid), 1, len(tt) - 1)
    y[(tt[j] - tt[j - 1]) > MAX_DT] = np.nan
    return grid, y


def _best_window(p: np.ndarray, n: int) -> tuple[Optional[float], Optional[int]]:
    if p is None or len(p) < n:
        return None, None
    c = np.convolve(np.nan_to_num(p), np.ones(n) / n, "valid")
    i = int(np.argmax(c))
    return float(c[i]), i


# ---------------------------------------------------------------------------
# pure analyses (arrays in, numbers out) — unit-tested on synthetic data
# ---------------------------------------------------------------------------

def _halves_drift(h: np.ndarray, x: np.ndarray, d: np.ndarray, m: np.ndarray) -> Optional[tuple]:
    """(drift, hr1, hr2, x1, x2) with r = x / HR over the two halves of the
    time in mask `m`; None when < 600 s usable or a half is empty."""
    m = m & np.isfinite(h) & (h > 0) & np.isfinite(x) & (x > 0)
    if d[m].sum() < 600:
        return None
    cum = np.cumsum(np.where(m, d, 0.0))
    half = cum[-1] / 2.0
    a, b = m & (cum <= half), m & (cum > half)
    h1, h2 = _wmean(h, d, a), _wmean(h, d, b)
    x1, x2 = _wmean(x, d, a), _wmean(x, d, b)
    if not all((h1, h2, x1, x2)):
        return None
    r1, r2 = x1 / h1, x2 / h2
    return float((r1 - r2) / r1), h1, h2, x1, x2


def _short_reason(measured_s: float) -> str:
    """The strict tier's refusal (also the `reason` of a reference-tier result)."""
    return f"暖身後只有 {int(max(0.0, measured_s) // 60)} 分鐘（< {DRIFT_MIN_S // 60} 分，UA 不建議採用），飄移不採用"


def _too_short_reason(measured_s: float) -> str:
    """Below the reference tier too."""
    return (f"暖身後只有 {int(max(0.0, measured_s) // 60)} 分鐘（< {DRIFT_REF_MIN_S // 60} 分，"
            "參考值也不採用），飄移不採用")


def fast_finish(x: np.ndarray, d: np.ndarray, m: np.ndarray,
                share: float = DRIFT_FINISH_SHARE) -> Optional[float]:
    """Mean of `x` over the last `share` of the time in mask `m`, relative to
    the rest (time-weighted): 0.08 = the finish was 8 % above the steady part."""
    ok = m & np.isfinite(x) & (x > 0)
    cum = np.cumsum(np.where(ok, d, 0.0))
    if cum[-1] <= 0:
        return None
    last = ok & (cum > (1.0 - share) * cum[-1])
    a, b = _wmean(x, d, ok & ~last), _wmean(x, d, last)
    return (b / a - 1.0) if a and b else None


def heat_gate(dr: dict, temp_c: Optional[float], src: Optional[str]) -> dict:
    """drift_of's heat rule on its own: a fair result with a mean temperature
    > DRIFT_HEAT_C becomes refused. Returns a new dict carrying `temp_c` /
    `temp_src` (route_weather / watch / None) either way; idempotent, so
    measure() re-applies it to the cached (pre-heat) result on every read."""
    out = {**dr, "temp_c": temp_c, "temp_src": src if temp_c is not None else None}
    if (dr.get("ok") or dr.get("ref_ok")) and temp_c is not None and temp_c > DRIFT_HEAT_C:
        why = (f"{TEMP_SRC_LABEL.get(src, '溫度')} {temp_c:.0f} °C（> {DRIFT_HEAT_C:.0f} °C）："
               "熱會讓心率飄，飄移不採用")
        # both tiers: a hot run is not a reference either
        out.update(ok=False, ref_ok=False, tier=None, reason=why, pw_ok=False, pw_ref_ok=False,
                   pw_reason=why, hot=True)
    return out


def _tier_rank(measured_s: float) -> int:
    return 2 if measured_s >= DRIFT_MIN_S else 1 if measured_s >= DRIFT_REF_MIN_S else 0


def steady_start(t, hr, speed, end: Optional[float] = None) -> tuple[float, Optional[dict]]:
    """drift_of's adaptive start (自組): (start s after the first sample,
    record or None). A stop = a sample at ≤ 1.6 km/h (WKO5's moving
    threshold; a recording gap is not a stop). The window starts at
    max(WARMUP_S, end of the last stop that begins in the first
    DRIFT_EARLY_S + DRIFT_SETTLE_S) — unless that leaves less moving time
    (HR and speed valid) than a tier needs that the fixed WARMUP_S start
    reaches: then WARMUP_S, with `fallback` True in the record. The record:
    {stops, stopped_s (standing time in the first 20 min), last_stop_s (when
    the last of them ended), shifted_s (start − WARMUP_S), fallback}; None
    when no stop in the first 20 min reaches past the warm-up. `end` (s after
    the first sample, exclusive): the tier comparison only counts moving time
    before it (drift_of's cool-down / trailing-idle cut)."""
    t = np.asarray(t, dtype=float)
    n = len(t)
    s = _arr(speed, n)
    fin = np.isfinite(t)
    if not fin.any():
        return float(WARMUP_S), None
    rel = t - t[fin][0]
    stop = fin & np.isfinite(s) & (s <= STOP_KMH)
    idx = np.flatnonzero(stop & (rel < DRIFT_EARLY_S))
    if not len(idx):
        return float(WARMUP_S), None
    j = int(idx[-1])
    while j + 1 < n and stop[j + 1]:           # the last early stop, to its end
        j += 1
    start = max(float(WARMUP_S), float(rel[j]) + DRIFT_SETTLE_S)
    if start <= WARMUP_S:
        return float(WARMUP_S), None
    d = _dt(t)
    early = stop & (rel < DRIFT_EARLY_S)
    stops = int(np.sum(early & ~np.concatenate([[False], early[:-1]])))
    rec = {"stops": stops, "stopped_s": float(d[early].sum()), "last_stop_s": float(rel[j]),
           "shifted_s": start - WARMUP_S, "fallback": False}
    h = _arr(hr, n)
    valid = moving_mask(t, s) & np.isfinite(h) & (h > 0) & np.isfinite(s) & (s > 0)
    if end is not None:
        valid &= rel < end
    fixed, later = float(d[valid & (rel >= WARMUP_S)].sum()), float(d[valid & (rel >= start)].sum())
    if _tier_rank(later) < _tier_rank(fixed):
        return float(WARMUP_S), {**rec, "shifted_s": 0.0, "fallback": True}
    return start, rec


def start_text(dr: dict) -> str:
    """「前 10 分鐘不算」 or the adaptive start's 「前 11:41 不算」."""
    w = _f(dr.get("warmup_s")) or WARMUP_S
    return "前 10 分鐘不算" if abs(w - WARMUP_S) < 1 else f"前 {_hms(w)} 不算"


def _start_text(dr: dict) -> Optional[str]:
    r = dr.get("start_shift")
    if not r:
        return None
    if r.get("fallback"):
        return (f"前 20 分鐘內有路口停等（最後一次在 {_hms(r['last_stop_s'])}），但從那之後起算會不夠"
                f" {DRIFT_REF_MIN_S // 60}／{DRIFT_MIN_S // 60} 分鐘，所以仍從第 10 分鐘起算，停等計入 5% 停頓（自組）")
    return (f"前段路口停等 {r['stops']} 次（共 {_hms(r['stopped_s'])}）：從最後一次停等後 1 分鐘、"
            f"第 {_hms(_f(dr.get('warmup_s')))} 起算，多排除 {_hms(r['shifted_s'])}（自組）")


def tail_text(dr: dict) -> Optional[str]:
    """「回程市區段 m:ss（當緩和）」 (steady_end) and the trailing idle cut; None when neither."""
    parts = []
    r = dr.get("tail")
    if r:
        parts.append(f"回程市區段 {_hms(r['excluded_s'])}（當緩和：從 {_hms(r['first_stop_s'])} 起、"
                     f"停等 {r['stops']} 次；門檻推估）")
    idle = _f(dr.get("idle_s"))
    if idle:
        parts.append(f"結尾靜止 {_hms(idle)}（錶沒按停，已裁掉；門檻推估）")
    return "；".join(parts) or None


def excluded_text(dr: dict) -> Optional[str]:
    """The 「已排除」 row: what the adaptive start, the return-leg cool-down and
    the trailing idle cut left out (None when nothing), e.g.
    「已排除：回程市區段 6:12（當緩和…）」."""
    parts = [x for x in (_start_text(dr), tail_text(dr)) if x]
    return "；".join(parts) or None


SE_TIP = ("單次 30–40 分鐘的飄移，誤差大約 ±4–6 個百分點：心率樣本前後高度相關（1 秒自相關 0.995），等效只有 4–5 個"
          "獨立觀測。± 是回歸法的標準誤（心率 = 截距 + 功率經 60 秒延遲 + 時間；τ = 60 s 落在文獻 55–70 s，"
          "Hunt 2015／2019、Wang & Hunt 2021；把兩者接成一個回歸是推估）。UA 的 3.5／5% 帶只差 1.5 個百分點，"
          "單次分不出，要看多次平均（總覽、賽季圖的「6 次平均」）。")
TAIL_TIP = ("回程市區段：從結尾往回，最後一群相隔 ≤ 6 分、離結束 ≤ 12 分、在第 20 分鐘之後的停等，從第一次停等起當緩和排除"
            "（UA／Friel 都說不含暖身和緩和；12／6 分是推估，用你的跑步校正）。錶沒按停的結尾靜止 ≥ 2 分整段裁掉，"
            "不算進 5% 停頓（推估）。")
STABILITY_TIP = ("穩定度只算量測視窗內移動中的樣本：VI = NP30／平均功率 ≤ 1.04（VI 是 Coggan 的定義，1.04 推估、用你的跑步校正）；"
                 "30 秒速度 < 中位速度 75% 連續 ≥ 3 分 = 跑走（推估）；後半功率和前半差 ≤ 5%（UA／Evoke 固定強度，5% 推估）。"
                 "舊的「30 秒功率變異 > 15%」沒有來源，只留數字參考。")
RAMP_TIP = ("坡道不排除（你的決定）：|15 秒坡度| ≥ 3% 連續 ≥ 10 秒算一段。「去坡道」是把坡道和坡後 120 秒拿掉再算一次前後半，"
            "只當對照：河濱的小坡 Stryd 功率幾乎不升、心率會升，坡又多在後半，所以飄移會被往上推約 1–2 個百分點"
            "（drift-algorithm.md §1.2，門檻推估）。")


def se_text(se: Optional[float]) -> str:
    """「±4.2 pp」."""
    return "" if se is None else f"±{se * 100:.1f} pp"


def stability_text(dr: dict, power: bool = True) -> Optional[str]:
    """The card's 穩定度 row: VI, the longest slow stretch, the halves output
    difference and the old 30-s CV (information only)."""
    parts = []
    if dr.get("vi") is not None:
        parts.append(f"VI {dr['vi']:.3f}（≤ {DRIFT_MAX_VI:.2f}）")
    if dr.get("walk_max_s") is not None:
        parts.append(f"最長慢段 {_hms(dr['walk_max_s'])}（< {_hms(WALK_MAX_S)}）")
    if dr.get("halves_diff") is not None:
        name = "功率" if dr.get("halves_basis") == "power" else "速度"
        parts.append(f"後半{name} {_pct(dr['halves_diff'], sign=True)}（±{DRIFT_HALVES_DIFF * 100:.0f}% 內）")
    if dr.get("cv30") is not None:
        parts.append(f"舊規則 30 秒變異 {dr['cv30'] * 100:.0f}%（只供參考）")
    return " · ".join(parts) or None


def ramps_text(dr: dict, power: bool = True) -> Optional[str]:
    """The card's 坡道 row: ramps per half (climb) and the ramp-free comparison value."""
    r = dr.get("ramps")
    if not r:
        return None
    wo = r.get("pw_drift" if power else "drift")
    tail = f"；去坡道 {_pct(wo)}（只當對照，不排除）" if wo is not None else "；去坡道後資料不夠"
    return f"前半 {r['n1']} 段／後半 {r['n2']} 段（爬升 {r['climb1']:.0f}／{r['climb2']:.0f} m）{tail}"


def trailing_idle(t, speed) -> tuple[float, float]:
    """(end, idle_s) — DRIFT §7.4 (推估). `end` = seconds after the first
    sample, exclusive: just after the last moving sample when the trailing
    non-moving time (standing, or a recording gap) is ≥ DRIFT_IDLE_S (the watch
    not stopped), else past the last sample; `idle_s` = the time cut (0.0)."""
    t = np.asarray(t, dtype=float)
    fin = np.isfinite(t)
    if not fin.any():
        return 0.0, 0.0
    rel = t - t[fin][0]
    last = float(np.nanmax(rel))
    idx = np.flatnonzero(moving_mask(t, speed) & fin)
    if not len(idx):
        return last + 1e-3, 0.0
    j = int(idx[-1])
    idle = last - float(rel[j])
    if idle >= DRIFT_IDLE_S:
        return float(rel[j]) + 1e-3, idle
    return last + 1e-3, 0.0


def _stop_segments(rel: np.ndarray, stop: np.ndarray) -> list[tuple[float, float]]:
    """[(start, end)] seconds of each unbroken run of stop samples."""
    e = np.diff(np.concatenate([[0], stop.astype(int), [0]]))
    a, b = np.flatnonzero(e == 1), np.flatnonzero(e == -1) - 1
    return [(float(rel[i]), float(rel[j])) for i, j in zip(a, b)]


def steady_end(t, speed, end: float) -> tuple[float, Optional[dict]]:
    """drift_of's cool-down (DRIFT §7.1, 推估 numbers calibrated on the user's
    runs): the return leg through the city. A stop = a sample at ≤ 1.6 km/h
    (as in steady_start; a recording gap is not a stop). From `end` (the
    trailing-idle cut) backwards, the last cluster of stops: each starts
    ≤ DRIFT_TAIL_S (12 min) before `end` and after DRIFT_EARLY_S (20 min), and
    is ≤ DRIFT_TAIL_GAP_S (6 min) from the next one. The window then ends at
    the cluster's first stop. Returns (end, record or None); record = {stops,
    first_stop_s, excluded_s, stopped_s}. No tier protection (unlike the
    start): the return leg is not steady running, losing a tier for it is right."""
    t = np.asarray(t, dtype=float)
    n = len(t)
    s = _arr(speed, n)
    fin = np.isfinite(t)
    if not fin.any():
        return end, None
    rel = t - t[fin][0]
    stop = fin & np.isfinite(s) & (s <= STOP_KMH) & (rel < end)
    if not stop.any():
        return end, None
    lo = max(end - DRIFT_TAIL_S, float(DRIFT_EARLY_S))
    clus: list[tuple[float, float]] = []
    for a, b in reversed(_stop_segments(rel, stop)):
        if a < lo or (clus and clus[0][0] - b > DRIFT_TAIL_GAP_S):
            break
        clus.insert(0, (a, b))
    if not clus:
        return end, None
    cut = clus[0][0]
    d = _dt(t)
    return cut, {"stops": len(clus), "first_stop_s": cut, "excluded_s": max(0.0, end - 1e-3 - cut),
                 "stopped_s": float(d[stop & (rel >= cut)].sum())}


def _runs_of(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(a, b)) index ranges of each unbroken run of True."""
    e = np.diff(np.concatenate([[0], np.asarray(mask, dtype=int), [0]]))
    return list(zip(np.flatnonzero(e == 1).tolist(), np.flatnonzero(e == -1).tolist()))


def _at_grid(t, x, grid: np.ndarray) -> np.ndarray:
    """x linearly at the 1-s `grid` times, NaN outside the samples or across a gap > MAX_DT."""
    t = np.asarray(t, dtype=float)
    x = _arr(x, len(t))
    ok = np.isfinite(t) & np.isfinite(x)
    if ok.sum() < 2:
        return np.full(len(grid), np.nan)
    tt, xx = t[ok], x[ok]
    y = np.interp(grid, tt, xx, left=np.nan, right=np.nan)
    j = np.clip(np.searchsorted(tt, grid), 1, len(tt) - 1)
    y[(tt[j] - tt[j - 1]) > MAX_DT] = np.nan
    return y


def power_vi(gp: np.ndarray, wg: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    """(VI, CV30) on the window samples `wg` of a 1-s power grid (DRIFT §4.4):
    the 30-s rolling mean inside each unbroken run of window samples (never
    across a stop or a gap); NP = their 4th-power mean ^ ¼ (Coggan, coach),
    AP = mean power; VI = NP / AP. CV30 = SD / mean of the same 30-s means
    (VI ≈ 1 + 1.5·CV², DRIFT §4.2). (None, None) without 30 s of power."""
    ok = wg & np.isfinite(gp)
    rs = [np.convolve(gp[a:b], np.ones(30) / 30, "valid") for a, b in _runs_of(ok) if b - a >= 30]
    if not rs:
        return None, None
    r = np.concatenate(rs)
    ap = float(np.mean(gp[ok]))
    if ap <= 0 or r.mean() <= 0:
        return None, None
    return float(np.mean(r ** 4) ** 0.25 / ap), float(r.std() / r.mean())


def walk_breaks(gs: np.ndarray, wg: np.ndarray) -> list[tuple[int, float]]:
    """Slow stretches in the window (DRIFT §4.3, 推估): 30-s mean speed (over
    window samples only) < WALK_FRAC × the window's median moving speed, for
    ≥ WALK_SEG_S. [(grid index, seconds)]; drift_of refuses the longest ≥
    WALK_MAX_S as a run-walk."""
    ok = wg & np.isfinite(gs)
    if ok.sum() < WALK_SEG_S:
        return []
    med = float(np.median(gs[ok]))
    k = np.ones(30)
    s30 = np.convolve(np.where(ok, gs, 0.0), k, "same") / np.maximum(np.convolve(ok.astype(float), k, "same"), 1e-9)
    slow = ok & (s30 < WALK_FRAC * med)
    return [(a, float(b - a)) for a, b in _runs_of(slow) if b - a >= WALK_SEG_S]


def _ffill(x: np.ndarray) -> np.ndarray:
    ok = np.isfinite(x)
    if not ok.any():
        return np.zeros(len(x))
    idx = np.where(ok, np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    y = x[idx]
    y[:int(np.argmax(ok))] = x[int(np.argmax(ok))]
    return y


def lowpass(x: np.ndarray, tau: float = DRIFT_TAU_S) -> np.ndarray:
    """First-order low-pass (time constant `tau` s) of a 1-s series: the
    heart-rate response to a step in output (Hunt 2015 / 2019, Wang & Hunt
    2021 — peer-reviewed)."""
    a = 1.0 - math.exp(-1.0 / max(1e-9, tau))
    y = np.empty(len(x))
    acc = float(x[0]) if len(x) else 0.0
    for i, v in enumerate(x):
        acc += a * (float(v) - acc)
        y[i] = acc
    return y


def drift_regression(gh: np.ndarray, gx: np.ndarray, wg: np.ndarray, stopped: np.ndarray,
                     hr2: float, tau: float = DRIFT_TAU_S) -> Optional[dict]:
    """The drift's standard error (DRIFT §3.1; the regression itself is 推估,
    its parts are sourced). On the window samples of a 1-s grid, least squares
    HR = a + b·x_f + c·t (t in minutes), x_f = the output (power, or speed for
    Pa:HR) through lowpass(τ = 60 s), gaps forward-filled and stops 0. The
    output term is dropped when it doesn't vary (steady power: b is not
    identifiable). In the halves method's units, drift_eq = c·T/2 ÷ HR₂ (T =
    window minutes; DRIFT §3.1). SE(c) is OLS's times the AR(1) correction
    √((n−k)/(n_eff−k)), n_eff = n(1−ρ₁)/(1+ρ₁), ρ₁ = the residuals' lag-1
    autocorrelation (n_eff − k floored at 1, 推估 guard). τ only enters here.
    {"drift_eq", "se", "c", "rho1", "n_eff"}; None with < 600 s or no HR₂."""
    sel = wg & np.isfinite(gh) & (gh > 0)
    n = int(sel.sum())
    if n < 600 or not hr2:
        return None
    xf = lowpass(np.where(stopped, 0.0, _ffill(np.asarray(gx, dtype=float))), tau)
    y = gh[sel]
    tm = np.flatnonzero(sel) / 60.0
    xs = xf[sel]
    cols = [np.ones(n)]
    if xs.std() > 1e-6 * max(1.0, abs(float(xs.mean()))):
        cols.append(xs - xs.mean())
    cols.append(tm - tm.mean())
    X = np.column_stack(cols)
    k = X.shape[1]
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ beta
    rho = 0.0
    if r.std() > 1e-12:
        rho = float(np.corrcoef(r[:-1], r[1:])[0, 1])
        rho = 0.0 if not math.isfinite(rho) else min(max(rho, 0.0), 0.999)
    n_eff = n * (1.0 - rho) / (1.0 + rho)
    s2 = float(r @ r) / max(1, n - k)
    var_c = s2 * float(np.linalg.pinv(X.T @ X)[-1, -1])
    se_c = math.sqrt(max(0.0, var_c)) * math.sqrt((n - k) / max(n_eff - k, 1.0))
    c = float(beta[-1])
    f = (n / 60.0) / 2.0 / float(hr2)
    return {"drift_eq": c * f, "se": se_c * f, "c": c, "rho1": rho, "n_eff": n_eff}


def ramp_contrast(gh: np.ndarray, gv: np.ndarray, gp: Optional[np.ndarray], gd: Optional[np.ndarray],
                  ge: Optional[np.ndarray], wg: np.ndarray) -> Optional[dict]:
    """Ramps in the window, display only (DRIFT §3.2–§3.3, 推估 numbers; the
    user's decision: ramps stay in the drift). A ramp = |grade over a 15-s
    span| ≥ 3 % for ≥ 10 s. {n1, n2 (ramps starting in the first / second
    half of the window's moving time), climb1, climb2 (m, positive elevation
    change in each half, 15-s smoothed), excluded_s, drift / pw_drift (the
    halves drift with the ramps and the 120 s after each left out — the
    ramp-free comparison value)}. None without distance and elevation."""
    if gd is None or ge is None or not np.isfinite(gd).any() or not np.isfinite(ge).any():
        return None
    n = len(gd)
    h = RAMP_SPAN_S // 2
    grade = np.full(n, np.nan)
    if n > 2 * h:
        de = ge[2 * h:] - ge[:-2 * h]
        dd = (gd[2 * h:] - gd[:-2 * h]) * 1000.0
        with np.errstate(invalid="ignore", divide="ignore"):
            grade[h:n - h] = np.where(dd >= 5.0, de / dd, np.nan)
    ramp = np.isfinite(grade) & (np.abs(grade) >= RAMP_GRADE)
    segs = [(a, b) for a, b in _runs_of(ramp) if b - a >= RAMP_MIN_S and wg[a:b].any()]
    excl = np.zeros(n, dtype=bool)
    for a, b in segs:
        excl[a:min(n, b + RAMP_AFTER_S)] = True
    cum = np.cumsum(wg.astype(float))
    half = cum[-1] / 2.0
    first = wg & (cum <= half)
    second = wg & ~first
    n1 = sum(1 for a, _ in segs if cum[a] <= half)
    k = np.ones(15) / 15.0
    es = np.convolve(_ffill(np.asarray(ge, dtype=float)), k, "same")
    up = np.clip(np.diff(es, prepend=es[0]), 0.0, None)
    ones = np.ones(n)
    keep = wg & ~excl
    pa = _halves_drift(gh, gv, ones, keep)
    pw = _halves_drift(gh, gp, ones, keep) if gp is not None else None
    return {"n1": n1, "n2": len(segs) - n1, "climb1": float(up[first].sum()), "climb2": float(up[second].sum()),
            "excluded_s": float((wg & excl).sum()), "drift": pa[0] if pa else None, "pw_drift": pw[0] if pw else None}


def drift_of(t, hr, speed, power=None, cp: Optional[float] = None,
             climb_m_per_km: Optional[float] = None, trail: bool = False,
             temp_c: Optional[float] = None, temp_src: Optional[str] = None,
             dist=None, elev=None) -> dict:
    """Pa:HR decoupling (r = speed / HR, (r1 − r2) / r1 over the halves of the
    moving time after a 10-minute warm-up). Positive = HR drifted up for the
    same pace. `ok` False (with `reason`) when the run is not a fair test —
    among others, < 40 min of moving time after the warm-up, a fast finish,
    or `temp_c` > 25 °C (heat_gate).

    Pw:HR is the same measurement with power in place of speed — same
    fairness rules, same warm-up, and the *same samples* and halves: with
    power, both use the moving samples where HR, speed and power are all
    valid (`pw_drift`, `p1`, `p2`, `pw_hr1`, `pw_hr2`). When power covers
    < 95 % of the Pa:HR window, Pa:HR keeps the whole window and Pw:HR is
    refused (`pw_reason`); 「這次沒有功率」 without a power channel. `measured_s`
    = the moving time the drift was computed on.

    Two tiers (one window, so one tier for both bases):
      * 嚴格 / test — `ok` (and `pw_ok`): ≥ DRIFT_MIN_S (40 min) after the
        warm-up. Gates, AeT-test classification and thresholds read only this.
      * 參考 / reference — `ref_ok` (and `pw_ref_ok`): every other check passed
        and ≥ DRIFT_REF_MIN_S (30 min, 自組) but < 40 min. `drift` / `hr1` …
        are filled, `ok` stays False and `reason` is the strict refusal.
    `tier` = "test" / "ref" / None. Display callers opt in with
    basis_drift(…, ref=True).

    The warm-up is adaptive (steady_start, 自組): after a city section with
    crossings the window starts 60 s after the last stop that begins in the
    first 20 min, if that is past 10 min and costs no tier. `warmup_s` = the
    start used, `start_shift` = what it left out (None: the fixed 10 min).

    v2 (docs/research/drift-algorithm.md, "DRIFT"; the numbers 推估 unless noted):
      * the end: trailing idle ≥ 2 min cut (trailing_idle → `idle_s`), then the
        return-leg city section as the cool-down (steady_end → `tail`, `end_s`);
      * the stop rule (≤ 5 %) on the window [start, end) only;
      * the gate on the measured window's moving samples: run-walk (walk_breaks,
        longest slow stretch ≥ 180 s → `walks`, `walk_max_s`), VI = NP30/AP ≤ 1.04
        (`vi`; the old unsourced 30-s CV is kept as information only: `cv30` the
        old way — every power sample after the start — and `cv30_w1` on the
        window), > 90 % CP, |P2 − P1| / P1 ≤ 5 % (`halves_diff`, pace without
        power), then the fast finish;
      * precision: `drift_se` / `pw_drift_se` (drift_regression, τ = 60 s) and
        `noisy` (SE > 5 pp); single runs are ±4–6 pp (DRIFT §1.3);
      * ramps stay in (the user's decision); `ramps` (ramp_contrast, with dist /
        elev) carries the per-half counts and the ramp-free comparison value.
    Two tiers, the heat rule and the AeT test are unchanged."""
    out = {"drift": None, "ok": False, "ref_ok": False, "tier": None, "reason": "", "hr1": None, "hr2": None,
           "v1": None, "v2": None, "pw_drift": None, "pw_ok": False, "pw_ref_ok": False, "pw_reason": "",
           "p1": None, "p2": None, "pw_hr1": None, "pw_hr2": None, "measured_s": None,
           "finish": None, "temp_c": None, "temp_src": None, "warmup_s": float(WARMUP_S), "start_shift": None,
           "end_s": None, "tail": None, "idle_s": 0.0, "vi": None, "cv30": None, "cv30_w1": None,
           "walks": [], "walk_max_s": None, "halves_diff": None, "halves_basis": None,
           "drift_se": None, "pw_drift_se": None,
           "noisy": None, "ramps": None}
    if hr is None or speed is None or not _has(hr) or not _has(speed):
        out["reason"] = "沒有心率或速度"
        return out
    t = np.asarray(t, dtype=float)
    n = len(t)
    h, s = _arr(hr, n), _arr(speed, n)
    d = _dt(t)
    t0 = t[np.isfinite(t)][0]
    rel = t - t0
    elapsed = float(np.nanmax(t) - t0)
    if elapsed < WARMUP_S + DRIFT_REF_MIN_S:        # can't reach 30 min after the warm-up
        out["reason"] = _too_short_reason(elapsed - WARMUP_S)
        return out
    if trail or (climb_m_per_km is not None and climb_m_per_km >= TRAIL_CLIMB_RATE_M_PER_KM):
        out["reason"] = "有坡（越野或每公里爬升 ≥ 20 m），飄移數字不採用"
        return out
    end, idle = trailing_idle(t, s)
    end, tail = steady_end(t, s, end)
    start, shift = steady_start(t, h, s, end=end)
    out.update(warmup_s=start, start_shift=shift, end_s=end, tail=tail, idle_s=idle)
    after = rel >= start
    inwin = after & (rel < end)
    mov = moving_mask(t, s)
    span = float(d[inwin].sum())
    stopped = float(d[inwin & ~mov].sum())       # standing still + recording gaps, inside the window only
    if span > 0 and stopped / span > MAX_STOPPED_SHARE:
        out["reason"] = f"中途停了 {_hms(stopped)}（> 5%），飄移數字不採用"
        return out
    has_power = power is not None and _has(power)
    if has_power:
        # the old unsourced rule's number, information only: 30-s CV of every power
        # sample after the start (stops' 0 W and the return leg included — DRIFT §4.1)
        g1, p1 = _grid1(t, power)
        if p1 is not None:
            p = p1[(g1 - t0) >= start]           # by time: the grid starts at the first valid power sample
            p = p[np.isfinite(p)]
            if len(p) > 60:
                p30 = np.convolve(p, np.ones(30) / 30, "valid")
                if p30.mean() > 0:
                    out["cv30"] = float(p30.std() / p30.mean())
    # the measured window: moving, in [start, end), HR and speed valid; with
    # power (≥ 95 % coverage) also power valid, so Pa:HR and Pw:HR share it
    m0 = inwin & mov & np.isfinite(h) & (h > 0) & np.isfinite(s) & (s > 0)
    pw = _arr(power, n) if has_power else None
    win, pw_same = m0, False
    if pw is not None:
        mp = m0 & np.isfinite(pw) & (pw > 0)
        base_s = float(d[m0].sum())
        if base_s > 0 and float(d[mp].sum()) >= DRIFT_POWER_COVER * base_s:
            win, pw_same = mp, True
    measured = float(d[win].sum())
    out["measured_s"] = measured
    if measured < DRIFT_REF_MIN_S:
        out["reason"] = _too_short_reason(measured)
        return out
    strict = measured >= DRIFT_MIN_S             # else the reference tier (30–40 min)
    # ---- the stability gate, on the window's moving samples (1-s grid) ----
    grid = np.arange(t0, t0 + float(np.nanmax(rel)) + 1.0)
    rg = grid - t0
    gh, gs = _at_grid(t, h, grid), _at_grid(t, s, grid)
    gp = _at_grid(t, pw, grid) if has_power else None
    wg = (rg >= start) & (rg < end) & np.isfinite(gh) & (gh > 0) & np.isfinite(gs) & (gs > STOP_KMH)
    if pw_same:
        wg &= np.isfinite(gp) & (gp > 0)
    walks = walk_breaks(gs, wg)
    out["walks"] = [[float(rg[a]), dur] for a, dur in walks]
    out["walk_max_s"] = max((dur for _, dur in walks), default=0.0)
    if out["walk_max_s"] >= WALK_MAX_S:
        out["reason"] = (f"有一段 {_hms(out['walk_max_s'])} 明顯放慢（30 秒速度 < 中位速度的 {WALK_FRAC * 100:.0f}%，"
                         f"≥ {WALK_MAX_S // 60} 分）：跑走交替不是穩定跑，飄移不採用（門檻推估）")
        return out
    if has_power:
        pv = gp if pw_same else None
        if pv is None:                               # power too patchy for Pw:HR: the gate on what there is
            pv = np.where(np.isfinite(gp) & (gp > 0), gp, np.nan)
        vi, cvw = power_vi(pv, wg)
        out.update(vi=vi, cv30_w1=cvw)
        if vi is not None and vi > DRIFT_MAX_VI:
            out["reason"] = (f"功率起伏大（VI {vi:.3f} > {DRIFT_MAX_VI:.2f}，只算移動中的樣本）：不是穩定跑，"
                             "飄移不採用（門檻推估、用你的跑步校正）")
            return out
        pm = _wmean(pw, d, m0 & np.isfinite(pw) & (pw > 0))
        if cp and pm and pm > AET_MAX_OF_CP * cp:
            out["reason"] = f"強度 {pm / cp * 100:.0f}% CP（> 90%），不是有氧跑，飄移不採用"
            return out
    hx = _halves_drift(h, pw if pw_same else s, d, win)
    if hx is not None:
        out["halves_diff"] = hx[4] / hx[3] - 1.0
        out["halves_basis"] = "power" if pw_same else "pace"
        if abs(out["halves_diff"]) > DRIFT_HALVES_DIFF:
            name = "功率" if pw_same else "速度"
            out["reason"] = (f"後半{name}比前半{'高' if out['halves_diff'] > 0 else '低'} "
                             f"{abs(out['halves_diff']) * 100:.0f}%（> {DRIFT_HALVES_DIFF * 100:.0f}%）：強度沒有維持，"
                             "飄移不採用（5% 推估）")
            return out
    fin = [(name, fast_finish(x, d, win)) for name, x in (("功率", pw if pw_same else None), ("配速", s))
           if x is not None]
    fin = [(name, ff) for name, ff in fin if ff is not None]
    if fin:
        name, ff = max(fin, key=lambda z: z[1])
        out["finish"] = ff
        if ff > DRIFT_FAST_FINISH:
            out["reason"] = (f"最後 10% 的{name}比前段高 {ff * 100:.0f}%（> {DRIFT_FAST_FINISH * 100:.0f}%）："
                             "快速結尾會讓飄移看起來比較小，飄移不採用")
            return out
    r = _halves_drift(h, s, d, win)
    if r is None:
        out["reason"] = "有效資料不夠"
        return out
    out.update(drift=r[0], ok=strict, ref_ok=not strict, tier="test" if strict else "ref",
               hr1=r[1], hr2=r[2], v1=r[3], v2=r[4])
    if not strict:
        out["reason"] = _short_reason(measured)      # what the strict tier says; the value is a reference
    if not has_power:
        out["pw_reason"] = "這次沒有功率"
    elif not pw_same:
        cover = float(d[m0 & np.isfinite(pw) & (pw > 0)].sum()) / max(1e-9, float(d[m0].sum()))
        out["pw_reason"] = f"功率只涵蓋 {cover * 100:.0f}% 的時間（< {DRIFT_POWER_COVER * 100:.0f}%），Pw:HR 不採用"
    else:
        rp = _halves_drift(h, pw, d, win)
        if rp is None:
            out["pw_reason"] = "功率資料不夠"
        else:
            out.update(pw_drift=rp[0], pw_ok=strict, pw_ref_ok=not strict, pw_hr1=rp[1], pw_hr2=rp[2],
                       p1=rp[3], p2=rp[4])
            if not strict:
                out["pw_reason"] = out["reason"]
    # ---- precision (DRIFT §3.1) and ramps (display only) ----
    stopped_g = np.isfinite(gs) & (gs <= STOP_KMH)
    ra = drift_regression(gh, gs, wg, stopped_g, r[2])
    out["drift_se"] = ra["se"] if ra else None
    if out["pw_drift"] is not None:
        rb = drift_regression(gh, gp, wg, stopped_g, out["pw_hr2"])
        out["pw_drift_se"] = rb["se"] if rb else None
    se_main = out["pw_drift_se"] if out["pw_drift_se"] is not None else out["drift_se"]
    out["noisy"] = None if se_main is None else bool(se_main > DRIFT_NOISY_SE)
    if dist is not None and elev is not None:
        out["ramps"] = ramp_contrast(gh, gs, gp if out["pw_drift"] is not None else None,
                                     _at_grid(t, dist, grid), _at_grid(t, elev, grid), wg)
    return heat_gate(out, temp_c, temp_src) if temp_c is not None else out


def basis_drift(dr: dict, basis: str = "pace", ref: bool = False) -> tuple[Optional[float], str]:
    """(drift, reason) of a drift_of result for the chosen basis; drift None
    when refused (the run was unfair, or there is no power in power mode).
    Strict (the test tier) by default — gates and thresholds; `ref=True`
    (display only) also returns a reference-tier value (drift_tier says which)."""
    if not (dr.get("ok") or (ref and dr.get("ref_ok"))):
        return None, dr.get("reason") or "飄移數字不採用"
    if basis == "power":
        good = dr.get("pw_ok") or (ref and dr.get("pw_ref_ok"))
        return (dr["pw_drift"], "") if good else (None, dr.get("pw_reason") or "這次沒有功率")
    return dr["drift"], ""


def drift_tier(dr: dict) -> Optional[str]:
    """"test" / "ref" / None of a drift_of result (results cached before v9:
    `ok` = test)."""
    if dr.get("ok"):
        return "test"
    return "ref" if dr.get("ref_ok") else None


def band_of(pct_cp: Optional[float]) -> Optional[tuple[str, float, float]]:
    if pct_cp is None:
        return None
    for b in BANDS:
        if b[1] <= pct_cp < b[2]:
            return b
    return None


def detect_efforts(t, power, hr=None, cp: Optional[float] = None,
                   min_s: int = EFFORT_MIN_S, gap_s: int = EFFORT_GAP_S) -> list[dict]:
    """Work bouts: 30-s power ≥ max(0.85 CP, 1.12 × session median) for at
    least `min_s`, gaps < `gap_s` bridged. Seconds are from the workout start."""
    if power is None or not _has(power):
        return []
    grid, p = _grid1(t, power)
    if grid is None or len(grid) < min_s * 2:
        return []
    h = _grid1(t, hr)[1] if hr is not None and _has(hr) else None
    pz = np.nan_to_num(p)
    p30 = np.convolve(pz, np.ones(30) / 30, "same")
    moving = pz[pz > 0]
    if not len(moving):
        return []
    med = float(np.median(moving))
    thr = max(0.85 * cp, 1.12 * med) if cp else 1.15 * med
    on = p30 >= thr
    # runs of True
    edges = np.diff(np.concatenate([[0], on.astype(int), [0]]))
    starts, ends = list(np.where(edges == 1)[0]), list(np.where(edges == -1)[0])
    segs: list[list[int]] = []
    for a, b in zip(starts, ends):
        if segs and a - segs[-1][1] < gap_s:
            segs[-1][1] = b
        else:
            segs.append([a, b])
    out = []
    for k, (a, b) in enumerate(segs):
        if b - a < min_s:
            continue
        pa = pz[a:b]
        avg = float(pa.mean())
        e = {"start_s": float(grid[a] - grid[0]), "duration_s": float(b - a), "power": avg,
             "pct_cp": (avg / cp) if cp else None, "hr": None, "hr_max": None, "hr_drop60": None}
        if h is not None:
            hh = h[a:b]
            if np.isfinite(hh).any():
                e["hr"] = float(np.nanmean(hh))
                e["hr_max"] = float(np.nanmax(hh))
            # HR lags the effort: the peak is at the end or a few seconds after
            # the next bout that is itself an effort (≥ min_s; shorter ones are skipped above)
            nxt = next((s for s, e_ in segs[k + 1:] if e_ - s >= min_s), None)
            if b + 60 < len(h) and (nxt is None or nxt >= b + 60):
                win = h[max(a, b - 10):b + 15]
                peak = np.nanmax(win) if np.isfinite(win).any() else np.nan
                if np.isfinite(peak) and np.isfinite(h[b + 60]):
                    e["hr_drop60"] = float(peak - h[b + 60])
        out.append(e)
    return out


def interval_summary(efforts: list[dict]) -> dict:
    """Band of the set (median %CP), reps inside it, fade (last vs first)."""
    reps = [e for e in efforts if e.get("pct_cp") is not None]
    if not reps:
        return {"n": len(efforts), "band": None, "in_band": 0, "fade": None}
    med = statistics.median(e["pct_cp"] for e in reps)
    b = band_of(med)
    inb = sum(1 for e in reps if b and b[1] - 0.01 <= e["pct_cp"] < b[2] + 0.01)
    fade = (reps[-1]["power"] / reps[0]["power"] - 1.0) if len(reps) >= 2 and reps[0]["power"] else None
    drops = [e["hr_drop60"] for e in efforts if e.get("hr_drop60") is not None]
    return {"n": len(efforts), "band": None if b is None else list(b), "in_band": inb, "fade": fade,
            "median_pct": med, "hr_drop60": statistics.median(drops) if drops else None,
            "rep_s": statistics.median(e["duration_s"] for e in efforts),
            "hr": statistics.median(e["hr"] for e in efforts if e.get("hr") is not None)
            if any(e.get("hr") is not None for e in efforts) else None}


# W′ prior for a single-bout estimate: Ruiz-Alias et al. 2025 (EJSS,
# PMC11770271), amateur men's Stryd 9/3 two-point W′ 13.1 ± 4.0 kJ
CP_TEST_WPRIME_PRIOR = (13100.0, 4000.0)
CP_TEST_GAP_S = 600           # the 3′ window starts ≥ 10 min away from the 12′ bout


def cp_test(t, power) -> Optional[dict]:
    """3'/12' result: CP = (P12·720 − P3·180) / 540, W′ = (P3 − CP)·180.

    The two windows must not overlap: the best 720 s first, then the best
    180 s at least CP_TEST_GAP_S away from it (a 3′ window inside the 12′
    bout gives a meaningless "CP 219 W, W′ 2.1 kJ"). When the 3′ bout is not
    above the 12′ power (not all-out) the two-point fit is invalid and CP
    comes from the 12′ bout alone, CP = P12 − W′/720 with the W′ prior above
    (range ± 1 SD), method "1pt_prior"."""
    if power is None or not _has(power):
        return None
    _, p = _grid1(t, power)
    if p is None:
        return None
    p12, i12 = _best_window(p, 720)
    if p12 is None:
        return None
    pz = np.nan_to_num(p).copy()
    lo, hi = max(0, i12 - 180 - CP_TEST_GAP_S + 1), min(len(pz), i12 + 720 + CP_TEST_GAP_S)
    c = np.convolve(pz, np.ones(180) / 180, "valid") if len(pz) >= 180 else None
    if c is None:
        return None
    ok = np.ones(len(c), bool)
    ok[max(0, lo):min(len(c), hi)] = False
    if not ok.any():
        return None
    i3 = int(np.argmax(np.where(ok, c, -np.inf)))
    p3 = float(c[i3])
    if p3 > p12:
        cp = (p12 * 720 - p3 * 180) / 540.0
        return {"p3": p3, "p12": p12, "cp": cp, "wprime": (p3 - cp) * 180.0, "separate": True,
                "method": "2pt", "cp_range": [cp, cp]}
    w, sd = CP_TEST_WPRIME_PRIOR
    return {"p3": p3, "p12": p12, "cp": p12 - w / 720.0, "wprime": w, "separate": True, "method": "1pt_prior",
            "cp_range": [p12 - (w + sd) / 720.0, p12 - (w - sd) / 720.0],
            "note": f"3 分段 {p3:.0f} W 不高於 12 分段 {p12:.0f} W（不是全力）：只用 12 分段，W′ 用先驗 13.1 kJ"}


def looks_like_cp_test(res: Optional[dict], cp_now: Optional[float]) -> bool:
    """Two separate all-out efforts: 3' ≥ 115 % and 12' ≥ 98 % of the current CP
    (an all-out 3' is typically 115–135 % CP; a hard tempo run tops out below)."""
    if not res or not cp_now or not res.get("separate"):
        return False
    if res["p12"] < 0.98 * cp_now:
        return False
    # a 3′ that was not all-out (single-bout fallback) still makes the session
    # a test when it is a separate bout at ≥ 98 % CP (自組; the 2026-09-30 test:
    # 3′ 218 W below the 12′ 222 W, 16 min apart)
    return res["p3"] >= 1.15 * cp_now or (res.get("method") == "1pt_prior" and res["p3"] >= 0.98 * cp_now)


RUN_CADENCE = 65.0            # strides/min (130 spm): below that you're walking


def _run_work(t, speed, cadence=None, power=None) -> tuple[np.ndarray, np.ndarray, str]:
    """(running mask, cumulative work, basis) — form_drift's split, shared with
    form_bins. Running = moving and, with a cadence channel, ≥ 130 spm. The
    cumulative weight is power·dt (J) over the running steps when power covers
    ≥ 90 % of them ("work"), else the running time ("time")."""
    t = np.asarray(t, dtype=float)
    n = len(t)
    d = _dt(t)
    mov = moving_mask(t, speed)
    if cadence is not None and _has(cadence):
        c = _arr(cadence, n)
        mov &= np.isfinite(c) & (c >= RUN_CADENCE)
    w = np.where(mov, d, 0.0)
    split = "time"
    if power is not None and _has(power):
        p = _arr(power, n)
        ok = mov & np.isfinite(p) & (p > 0)
        if w[ok].sum() >= 0.9 * w.sum() > 0:          # power on (almost) all running steps
            w = np.where(ok, p * d, 0.0)
            split = "work"
    return mov, np.cumsum(w), split


# 跑姿分組 (form_bins): the form metrics per grade bin and per 10 % of the work done
FORM_KEYS = ("ilr", "impact_g", "impact_km", "lss", "kleg", "gct", "cadence", "vo")
IMPACT_KEYS = ("ilr", "impact_g")       # the per-step impact metrics the cadence hint checks


def impact_per_km(impact_g, cadence_spm, speed_kmh) -> Optional[np.ndarray]:
    """每公里衝擊量 (推估): impact G × steps per km = G × spm × 60 / (km/h), per
    sample, NaN when stopped. Cumulative-per-distance load is how Willson 2014
    (Clin Biomech 29:243) compared step lengths for the patellofemoral joint;
    multiplying a whole-body impact by the step count is our own proxy, not a
    measured knee load."""
    if impact_g is None or cadence_spm is None or speed_kmh is None:
        return None
    n = len(np.asarray(impact_g))
    g, c, v = _arr(impact_g, n), _arr(cadence_spm, n), _arr(speed_kmh, n)
    with np.errstate(all="ignore"):
        out = np.where(np.isfinite(v) & (v > STOP_KMH) & (c > 0), g * c * 60.0 / v, np.nan)
    return out
STRYD_ONLY = ("ilr", "lss")
FORM_BANDS = (("all", None, None), ("flat", -0.03, 0.03), ("up", 0.03, None), ("down", None, -0.03))
WORK_DECILES = 10


def _form_means(chans: dict, d: np.ndarray, m: np.ndarray) -> tuple[float, dict]:
    """(running time in `m`, {metric: time-weighted mean}) — None for an absent metric."""
    out = {}
    for k, x in chans.items():
        out[k] = _wmean(x, d, m) if x is not None else None
    return float(d[m].sum()), out


def form_bins(t, speed, chans: dict, cadence=None, power=None, grade=None,
              edges: Sequence[float] = None) -> dict:
    """The form metrics (`chans`, sample-aligned, display units) on the running
    steps (form_drift's mask), grouped two ways:
      * `grade`: one row per grade bin (panels.workout GRADE_EDGES, %, the
        坡度分組 rows), steepest downhill first — {label, lo, hi, time_s, m};
      * `work`: per FORM_BANDS band (all / flat −3…+3 % / up ≥ 3 % / down
        < −3 %), ten rows by the cumulative work done (form_drift's basis:
        kJ over the running steps, or the running time without power) —
        {k, time_s, m}. The deciles are cut on all running steps, then each
        band keeps only its own samples, so a band's decile k is the same
        stretch of the run as the others'.
    `m` = {metric: mean}; `split` = "work" / "time". {} without running steps."""
    from backend.engine.panels.workout import GRADE_EDGES, _label
    t = np.asarray(t, dtype=float)
    n = len(t)
    d = _dt(t)
    mov, cum, split = _run_work(t, speed, cadence, power)
    if not len(cum) or cum[-1] <= 0:
        return {}
    ch = {k: (_arr(x, n) if x is not None and _has(x) else None) for k, x in chans.items()}
    g = _arr(grade, n) if grade is not None else None
    has_g = g is not None and bool(np.isfinite(g).any())
    rows = []
    if has_g:
        gp = g * 100.0
        bounds = [None, *(edges or GRADE_EDGES), None]
        for lo, hi in zip(bounds[:-1], bounds[1:]):
            m = mov & np.isfinite(gp)
            if lo is not None:
                m &= gp >= lo
            if hi is not None:
                m &= gp < hi
            secs, mm = _form_means(ch, d, m)
            if secs > 0:
                rows.append({"label": _label(lo, hi), "lo": lo, "hi": hi, "time_s": secs, "m": mm})
    k = np.minimum((cum / cum[-1] * WORK_DECILES).astype(int), WORK_DECILES - 1)
    k = np.where(mov, k, -1)
    work = {}
    for band, lo, hi in FORM_BANDS:
        if band != "all" and not has_g:
            continue
        sel = mov.copy()
        if band != "all":
            sel &= np.isfinite(g)
            if lo is not None:
                sel &= g >= lo
            if hi is not None:
                sel &= g < hi
        out = []
        for i in range(WORK_DECILES):
            secs, mm = _form_means(ch, d, sel & (k == i))
            out.append({"k": i, "time_s": secs, "m": mm})
        if any(r["time_s"] > 0 for r in out):
            work[band] = out
    return {"split": split, "grade": rows, "work": work}


# 步頻與衝擊 (docs/research/impact-cadence.md): steady 30-s windows for ILR ~ cadence at matched speed / grade
CW_WIN_S = 30                 # 推估: window length (long enough to average ~80 steps, short enough to be steady)
CW_SPEED_CV = 0.08            # 推估: steady = speed CV inside the window < 8 %
CW_GRADE_RANGE = 4.0          # 推估: and grade range < 4 pp
CW_MIN_RUNS = 5               # personal trend needs ≥ 5 past runs with ≥ 10 windows each
CW_MIN_WIN = 10


def cadence_windows(t, speed, cadence, ilr, grade=None, impact_g=None) -> list[list]:
    """[[ILR, cadence spm, speed km/h, grade %, impact G or None], …] — one row per steady 30-s window
    of running (form_drift's running steps: moving, ≥ 130 spm). `cadence` in strides/min (the FIT
    channel), `grade` a fraction. [] without ILR or cadence."""
    if ilr is None or cadence is None or speed is None or not _has(ilr) or not _has(cadence):
        return []
    t = np.asarray(t, dtype=float)
    n = len(t)
    d = _dt(t)
    mov, _, _ = _run_work(t, speed, cadence, None)
    il, cad, sp = _arr(ilr, n), _arr(cadence, n) * 2.0, _arr(speed, n)
    g = _arr(grade, n) * 100.0 if grade is not None else np.zeros(n)     # no altitude (treadmill): flat
    ig = _arr(impact_g, n) if impact_g is not None else np.full(n, np.nan)
    ok = mov & np.isfinite(il) & (il > 0) & np.isfinite(cad) & np.isfinite(sp) & (sp > STOP_KMH)
    fin = np.isfinite(t)
    if not ok.any() or not fin.any():
        return []
    k = ((t - t[fin][0]) // CW_WIN_S).astype(int)
    rows = []
    for kk in np.unique(k[ok]):
        m = ok & (k == kk)
        if d[m].sum() < CW_WIN_S - 5 or m.sum() < 20:
            continue
        s = sp[m]
        if s.std() / s.mean() > CW_SPEED_CV:
            continue
        gg = g[m][np.isfinite(g[m])]
        if len(gg) < 10 or gg.max() - gg.min() > CW_GRADE_RANGE:
            continue
        igm = ig[m][np.isfinite(ig[m])]
        rows.append([round(float(il[m].mean()), 2), round(float(cad[m].mean()), 1), round(float(s.mean()), 2),
                     round(float(gg.mean()), 1), round(float(igm.mean()), 3) if len(igm) else None])
    return rows


def cadence_fit(runs: list[list[list]]) -> Optional[dict]:
    """Within-run regression ILR ~ cadence + speed + grade (each run demeaned = a per-run fixed effect),
    SE clustered by run. `runs` = cadence_windows() of each past run; runs with < CW_MIN_WIN windows are
    left out. {b_cad, se_cad, b_speed, b_grade, n_runs, n_windows, partial_r, r2}; None with < CW_MIN_RUNS
    runs. Coefficients per spm, per km/h, per % grade (BW/s)."""
    Xs, ys, gs = [], [], []
    for i, rows in enumerate(runs):
        if len(rows) < CW_MIN_WIN:
            continue
        a = np.array([r[:4] for r in rows], dtype=float)
        X = a[:, 1:4] - a[:, 1:4].mean(axis=0)
        Xs.append(X); ys.append(a[:, 0] - a[:, 0].mean()); gs.append(np.full(len(a), i))
    if len(Xs) < CW_MIN_RUNS:
        return None
    X, y, g = np.vstack(Xs), np.concatenate(ys), np.concatenate(gs)
    keep = X.std(axis=0) > 1e-9
    if not keep[0]:
        return None
    X = X[:, keep]
    XtX = np.linalg.pinv(X.T @ X)
    b = XtX @ X.T @ y
    u = y - X @ b
    meat = np.zeros((X.shape[1], X.shape[1]))
    for gid in np.unique(g):
        sg = X[g == gid].T @ u[g == gid]
        meat += np.outer(sg, sg)
    G = len(Xs)
    se = np.sqrt(np.diag(XtX @ meat @ XtX * G / max(1, G - 1)))
    full = np.zeros(3)
    full[keep] = b
    oth = X[:, 1:]
    if oth.shape[1]:
        res = lambda v: v - oth @ np.linalg.lstsq(oth, v, rcond=None)[0]
        pr = float(np.corrcoef(res(y), res(X[:, 0]))[0, 1])
    else:
        pr = float(np.corrcoef(y, X[:, 0])[0, 1])
    tot = float(y @ y)
    return {"b_cad": float(full[0]), "se_cad": float(se[0]), "b_speed": float(full[1]), "b_grade": float(full[2]),
            "n_runs": G, "n_windows": int(len(y)), "partial_r": pr, "r2": 1 - float(u @ u) / tot if tot > 0 else None}


def form_drift(t, speed, chans: dict, cadence=None, power=None) -> dict:
    """{name: {first, last, change}} over the first half vs the second half of
    the work done (kJ, power·dt) on the running steps; without power, of the
    moving time. Splitting by work (user request, 2026-10-01; 推估) keeps a
    trail run's long slow climbs from filling one half: each half holds the
    same effort, not the same minutes. With `cadence` (strides/min) only
    running steps count — walking a steep climb would otherwise read as a
    collapse in stiffness. `split` says which basis was used."""
    t = np.asarray(t, dtype=float)
    n = len(t)
    d = _dt(t)
    mov, cum, split = _run_work(t, speed, cadence, power)
    if not len(cum) or cum[-1] <= 0:
        return {}
    a, b = mov & (cum <= cum[-1] / 2), mov & (cum > cum[-1] / 2)
    out = {"_split": split}
    for name, x in chans.items():
        if x is None or not _has(x):
            continue
        v = _arr(x, n)
        f, l = _wmean(v, d, a), _wmean(v, d, b)
        out[name] = {"first": f, "last": l, "change": (l / f - 1.0) if f and l else None}
    return out


def downhill_share(t, grade, dist_km=None, ilr=None) -> Optional[dict]:
    """Steep downhill (grade < −10 %): share of time, distance and impact (ILR·dt)."""
    if grade is None or not np.isfinite(np.asarray(grade, dtype=float)).any():
        return None
    t = np.asarray(t, dtype=float)
    n = len(t)
    g = _arr(grade, n)
    d = np.where(moving_mask(t), _dt(t), 0.0)
    steep = np.isfinite(g) & (g < STEEP_DOWN)
    out = {"time_s": float(d[steep].sum()),
           "time_share": float(d[steep].sum() / d.sum()) if d.sum() > 0 else None,
           "dist_share": None, "impact_share": None}
    if dist_km is not None:
        x = _arr(dist_km, n)
        step = np.diff(x, prepend=x[0])
        step = np.where(np.isfinite(step) & (step >= 0) & (step < 1), step, 0.0)
        out["dist_km"] = float(step[steep].sum())
        out["dist_share"] = float(step[steep].sum() / step.sum()) if step.sum() > 0 else None
    if ilr is not None and _has(ilr):
        i = np.nan_to_num(_arr(ilr, n)) * d
        out["impact_share"] = float(i[steep].sum() / i.sum()) if i.sum() > 0 else None
    return out


def pacing_deciles(t, dist_km, hr=None, power=None, speed=None) -> list[dict]:
    """Moving pace / HR / power per 10 % of the distance."""
    t = np.asarray(t, dtype=float)
    n = len(t)
    x = _arr(dist_km, n)
    if not np.isfinite(x).any():
        return []
    total = float(np.nanmax(x))
    if total < 0.5:
        return []
    d = np.where(moving_mask(t, speed), _dt(t), 0.0)
    h, p = _arr(hr, n), _arr(power, n)
    rows = []
    for k in range(10):
        m = np.isfinite(x) & (x >= total * k / 10) & ((x < total * (k + 1) / 10) if k < 9 else (x <= total))
        secs = float(d[m].sum())
        rows.append({"k": k, "from_km": total * k / 10, "pace_s_per_km": secs / (total / 10) if secs else None,
                     "hr": _wmean(h, d, m), "power": _wmean(p, d, m)})
    return rows


def baseline(values: Sequence[Optional[float]], min_n: int = BASE_MIN_N) -> dict:
    """Median and IQR; `ok` False under `min_n` samples (no comparison)."""
    v = sorted(x for x in (_f(x) for x in values) if x is not None)
    if len(v) < min_n:
        return {"n": len(v), "ok": False, "median": None, "q1": None, "q3": None}
    q1, med, q3 = (float(q) for q in np.percentile(v, [25, 50, 75]))
    return {"n": len(v), "ok": True, "median": med, "q1": q1, "q3": q3}


def compare(x: Optional[float], b: dict) -> Optional[str]:
    """'high' above Q3, 'low' below Q1, 'within' inside the IQR, None if no comparison."""
    x = _f(x)
    if x is None or not b.get("ok"):
        return None
    if x > b["q3"]:
        return "high"
    if x < b["q1"]:
        return "low"
    return "within"


def session_type(category: str, moving_s: float, hard_s: float, title: str = "",
                 plan_test: Optional[dict] = None, cp_detected: bool = False,
                 aet_steady: bool = False, long_target_s: Optional[float] = None,
                 hard_power_s: Optional[float] = None, n_efforts: Optional[int] = None,
                 easy_hr: bool = False, plan_aet: bool = False) -> str:
    """The plan's order: category → the plan's AeT-test session (`plan_aet`,
    done_by) → test_cp → test_aet (title, a plan AeT row that day, a ≥ 55-min
    steady run) → quality → long → easy.

    quality = ≥ HARD_SESSION_S at/above threshold (overview.HARD_EXPRS). With a
    power stream, time above LTHR alone isn't enough: an easy run whose HR
    drifted over LTHR has no work bouts, so it also needs one detected effort
    (or the time at ≥ 95 % CP). `hard_s` = max(HR, power) seconds."""
    if category in ("strength", "bike", "walk", "other"):
        return category
    plan_test = plan_test or {}
    if plan_aet:
        return "test_aet"                  # the plan says this activity was its AeT test
    if AET_TITLE.search(title or "") and not cp_detected:
        return "test_aet"                  # 「AeT 飄移測試」 (engine/aet_test.py), not a CP test
    if plan_test.get("cp") is not None or re.search(r"\bCP\b|測試", title or "") or cp_detected:
        return "test_cp"
    if plan_test.get("aethr") is not None or (aet_steady and moving_s >= TEST_AET_MIN_S):
        return "test_aet"
    need = _hard_session_s()
    # hikes count too (the athlete's call, 2026-09-30): a sustained climb above
    # threshold is a quality stimulus for 百岳. A session whose average HR stayed
    # ≤ AeT+3 was easy even if short rises spiked HR or power.
    runs = category in QUALITY_CATEGORIES and not easy_hr
    if runs and hard_power_s is not None and hard_power_s >= need:
        return "quality"
    if runs and hard_s >= need and (n_efforts is None or n_efforts >= 1):
        return "quality"
    if moving_s >= LONG_MIN_S or (long_target_s and moving_s >= 0.8 * long_target_s):
        return "long"
    return "easy"


def streak_of(drifts: Sequence[Optional[float]], good: float = DRIFT_GOOD) -> int:
    """Consecutive most-recent fair drifts below `good` (None = refused, skipped)."""
    n = 0
    for d in reversed([x for x in drifts if x is not None]):
        if d < good:
            n += 1
        else:
            break
    return n


def quality_gate(kind: Optional[str], levels: dict, gate=None) -> bool:
    """allow_quality for a week: engine/quality_gate.week_decision with `gate`
    (the dict status.i_gate returns). Outside base: intensity and drift not
    bad. A legacy bool / None `gate` = no method (the drift streak is gone)."""
    from backend.engine import quality_gate as QG
    g = gate if isinstance(gate, dict) and not QG.legacy(gate) else {
        "state": "none", "guard": {"block": levels.get("intensity") == "bad", "rule": "intensity"}}
    return QG.week_decision({**g, "levels": levels}, kind or "base", kind or "base")["allow"]


def next_quality(last: Optional[dict]) -> tuple[int, int]:
    """(reps, minutes) of the base-phase sub-threshold session: 3×8 the first
    time; one rep fewer (not below 2) when the last one faded."""
    if last and last.get("faded"):
        return max(2, int(last.get("reps") or 3) - 1), 8
    return 3, 8


def _hard_session_s() -> float:
    from backend.engine.overview import HARD_SESSION_S
    return HARD_SESSION_S


# ---------------------------------------------------------------------------
# dataset adapters
# ---------------------------------------------------------------------------

def _thresholds(ds, w) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(AeT, LTHR, CP) in effect on the workout date."""
    lthr = _f(ds.sport_setting("thr", w)) if hasattr(ds, "sport_setting") else None
    aet = _f(ds.aethr(w)) if hasattr(ds, "aethr") else (None if lthr is None else 0.89 * lthr)
    if hasattr(ds, "cp"):
        cp = _f(ds.cp(w))
    else:
        cp = _f(ds.sport_setting("ftp", w)) if hasattr(ds, "sport_setting") else None
    return aet, lthr, cp


def _title(w) -> str:
    e = w.entry
    t = getattr(e, "title", None)
    if t:
        return str(t)
    rec = getattr(e, "record", None)
    if rec is not None:
        try:
            from backend.files.wko5_athlete import text_field
            return text_field(rec.get(3213)) or ""
        except Exception:
            return ""
    return ""


def _wdate(w) -> dt.date:
    from backend.engine.wko5expr.dataset import day_to_date
    return day_to_date(w.day)


def _eval(ds, w, expr: str) -> Optional[np.ndarray]:
    """A derived channel (rgrade, kleg, fmax…) through the expression engine."""
    try:
        from backend.engine.wko5expr.evaluator import Evaluator
        d = math.floor(w.day)
        r = Evaluator(ds, d, d).evaluate(expr, w)
    except Exception:
        return None
    return np.asarray(r, dtype=float) if isinstance(r, np.ndarray) else None


def _samples(ds, w) -> Optional[dict]:
    t = ds.channel(w.idx, "elapsedtime")
    if t is None or not np.isfinite(t).any():
        return None
    ch = lambda name: ds.channel(w.idx, name)
    out = {"t": t, "hr": ch("heartrate"), "speed": ch("speed"), "power": ch("power"),
           "dist": ch("elapseddistance"), "cadence": ch("cadence")}
    out["elev"] = ch("_elevation")
    if out["elev"] is None:
        out["elev"] = ch("elevation")
    for k, name in (("gct", "stancetime"), ("vo", "verticaloscillation"),
                    ("ilr", "@impact_loading_rate"), ("lss", "@leg_spring_stiffness")):
        out[k] = ch(name)
    for k in list(out):
        if out[k] is not None and not _has(out[k]) and k != "t":
            out[k] = None
    return out


def _none_list(a) -> list:
    return [None if not np.isfinite(x) else float(x) for x in np.asarray(a, dtype=float)]


def _gap_factor(grade: Optional[float], walking: bool) -> Optional[float]:
    """Minetti's cost at `grade` relative to flat (algorithms/minetti.py, Strava's 0.9 downhill floor)."""
    if grade is None or not math.isfinite(grade):
        return None
    from backend.engine.algorithms.minetti import grade_factor
    return grade_factor(grade, walking)


def _climb_dict(c, dist, p: np.ndarray, d: np.ndarray, mov: np.ndarray, walking: bool = False) -> dict:
    """One detected climb as cached JSON: the table's numbers plus where it is
    (km, elevations) and what it cost (avg power, moving pace, GAP)."""
    a, b = c.start_index, c.end_index
    dk = _arr(dist, len(d))
    sl = slice(a, b + 1)
    pace = c.duration_s / c.distance_km if c.distance_km > 0 else None
    f = _gap_factor(c.grade, walking)
    return {"t_start": c.t_start, "duration_s": c.duration_s, "distance_km": c.distance_km,
            "gain_m": c.gain_m, "grade": c.grade, "vam": c.vam_m_per_h, "avg_hr": c.avg_hr,
            "hr_per_100m": c.hr_per_100m,
            "start_km": _f(dk[a]), "end_km": _f(dk[b]), "start_elev": c.start_elev_m, "top_elev": c.top_elev_m,
            "avg_power": _wmean(p[sl], d[sl], mov[sl]),
            "pace_s_per_km": pace, "gap_s_per_km": (pace / f) if pace and f else None}


def _rgrade(ds, w, s: dict) -> Optional[np.ndarray]:
    """rgrade (fraction) when there is distance and altitude, else None."""
    if s["dist"] is None or s["elev"] is None:
        return None
    return _eval(ds, w, "rgrade")


def _grade_rows(ds, w, s: dict, g: Optional[np.ndarray] = None) -> list:
    """grade_bins (rgrade) for the cache: the 坡度分組 card and its baseline."""
    if s["dist"] is None or s["elev"] is None:
        return []
    if g is None:
        g = _eval(ds, w, "rgrade")
    if g is None:
        return []
    keep = ("label", "lo", "hi", "time_s", "distance_km", "pace_s_per_km", "power", "hr", "vam", "time_pct")
    rows = grade_bins(_dt(s["t"]), g, s["dist"], s["power"], s["hr"], s["cadence"], elev_m=s["elev"])
    return [{k: r.get(k) for k in keep} for r in rows]


def _measure(ds, w) -> Optional[dict]:
    """Per-workout raw measurements (JSON; disk-memoised by `measure`)."""
    s = _samples(ds, w)
    if s is None:
        return None
    from backend.engine.overview import category
    t = s["t"]
    n = len(t)
    d = _dt(t)
    aet, lthr, cp = _thresholds(ds, w)
    mov = moving_mask(t, s["speed"])
    h = _arr(s["hr"], n)
    p = _arr(s["power"], n)
    m = w.metrics or {}
    dist, climb = _f(m.get("distance")), _f(m.get("climbing"))
    cat = category(w)
    out = {"category": cat, "moving_s": float(d[mov].sum()), "elapsed_s": float(np.nanmax(t) - np.nanmin(t)),
           "avg_hr": _wmean(h, d, mov), "avg_power": _wmean(p, d, mov), "aet": aet, "lthr": lthr, "cp": cp}
    hm = mov & np.isfinite(h) & (h > 0)
    out["hr_s"] = float(d[hm].sum())
    out["over_aet_s"] = float(d[hm & (h > (aet or 1e9) + AET_MARGIN)].sum()) if aet else None
    out["zones"] = None if not (aet and lthr) else {
        "low": float(d[hm & (h < aet)].sum()), "mid": float(d[hm & (h >= aet) & (h < lthr)].sum()),
        "high": float(d[hm & (h >= lthr)].sum())}
    # a recording gap (> MAX_DT between samples) is not time at threshold,
    # even when the samples on both sides of it are
    hard_hr = float(d[(d <= MAX_DT) & np.isfinite(h) & (h >= lthr)].sum()) if lthr else 0.0
    hard_p = 0.0
    # runs and hikes reach quality the same way: HR ≥ LTHR or power ≥ 95 % CP
    power_q = bool(cp) and (w.sport == "run" or cat in QUALITY_CATEGORIES) and _has(s["power"])
    if power_q:
        # 30-s power: Stryd's second-by-second spikes on every short rise
        # would otherwise make each easy run a "quality" session
        _, p1 = _grid1(t, s["power"])
        if p1 is not None and len(p1) > 30:
            p30 = np.convolve(np.nan_to_num(p1), np.ones(30) / 30, "same")
            hard_p = float((p30 >= 0.95 * cp).sum())
    out["hard_s"] = max(hard_hr, hard_p)
    out["hard_power_s"] = hard_p if power_q else None
    cpm = (climb / dist) if dist and dist > 0.5 and climb is not None else None
    out["climb_m_per_km"] = cpm
    out["watch_temp_c"] = _watch_temp(ds, w, t, s["speed"])
    if w.sport == "run" or cat in ("road", "trail"):
        # heat is left to heat_gate in measure(): the archive can fill after this is cached
        out["drift"] = drift_of(t, s["hr"], s["speed"], s["power"], cp, cpm, trail=cat == "trail",
                                dist=s["dist"], elev=s["elev"])
    else:
        out["drift"] = {"drift": None, "ok": False, "reason": "不是跑步"}
    # hikes too: session_type needs a detected effort next to hard_power_s
    efforts = detect_efforts(t, s["power"], s["hr"], cp) if (w.sport == "run" or power_q) else []
    out["efforts"] = efforts[:40]
    out["intervals"] = interval_summary(efforts)
    out["cp_test"] = cp_test(t, s["power"]) if w.sport == "run" else None
    from backend.engine import cp_protocols as CPP
    out["cp_bouts"] = CPP.measure_bouts(t, s["power"], s["hr"]) if w.sport == "run" else None
    climbs = []
    if s["elev"] is not None and s["dist"] is not None:
        try:
            cl = detect_climbs(_none_list(t), _none_list(s["dist"]), _none_list(s["elev"]),
                               _none_list(h) if s["hr"] is not None else None, moving=list(mov))
            climbs = [_climb_dict(c, s["dist"], p, d, mov, walking=cat in ("hike", "walk")) for c in cl]
        except Exception:
            climbs = []
    out["climbs"] = climbs
    rg = _rgrade(ds, w, s)
    out["grade_bins"] = _grade_rows(ds, w, s, rg)
    hp = [c["hr_per_100m"] for c in climbs if c.get("hr_per_100m")]
    out["hr_per_100m"] = statistics.median(hp) if hp else None
    stryd = s["ilr"] is not None or s["lss"] is not None
    out["stryd"] = stryd
    fchans = {"ilr": s["ilr"], "lss": s["lss"],
              "gct": None if s["gct"] is None else s["gct"] * 1000.0,
              "cadence": None if s["cadence"] is None else s["cadence"] * 2.0,
              "vo": None if s["vo"] is None else s["vo"] * 100.0}
    if w.sport == "run" and s["gct"] is not None:
        fchans["kleg"] = _eval(ds, w, "kleg")
        fchans["impact_g"] = _eval(ds, w, "fmax/(metric(weight)*g)")
    out["form"] = form_drift(t, s["speed"], fchans, s["cadence"], s["power"]) if w.sport == "run" else {}
    # 跑姿分組: ILR / LSS only from Stryd (as the 前半對後半 table)
    bchans = {k: fchans.get(k) for k in FORM_KEYS if stryd or k not in STRYD_ONLY}
    bchans["impact_km"] = impact_per_km(fchans.get("impact_g"), fchans.get("cadence"), s["speed"])
    out["form_bins"] = (form_bins(t, s["speed"], bchans, s["cadence"], s["power"], rg)
                        if w.sport == "run" else {})
    out["cad_windows"] = (cadence_windows(t, s["speed"], s["cadence"], s["ilr"], rg, fchans.get("impact_g"))
                          if w.sport == "run" and stryd else [])
    return out


def _watch_temp(ds, w, t, speed) -> Optional[float]:
    """Mean watch temperature over drift_of's window (moving, after the
    warm-up); None without a temperature channel."""
    try:
        tc = ds.channel(w.idx, "temperature")
    except Exception:
        return None
    if tc is None or not _has(tc):
        return None
    tt = np.asarray(t, dtype=float)
    x = _arr(tc, len(tt))
    sel = moving_mask(tt, speed) & ((tt - tt[np.isfinite(tt)][0]) >= WARMUP_S) & np.isfinite(x)
    if not sel.any():
        sel = np.isfinite(x)
    return float(np.mean(x[sel])) if sel.any() else None


_WX_CACHE: dict = {}


def _archive_temps() -> dict:
    """{activity file: air temp °C} from route_weather's activity_weather.json
    (Open-Meteo archive at the activity's point, moving-weighted; filled by the
    routes build). {} when it isn't there. Cached on the file's mtime."""
    try:
        from backend.engine import route_weather as RW
        from backend.engine.routes import HOME
        p = HOME / RW.ACTIVITY_WX_FILE
        mt = p.stat().st_mtime_ns
    except Exception:
        return {}
    hit = _WX_CACHE.get(str(p))
    if hit and hit[0] == mt:
        return hit[1]
    acts = (RW.load_activity_weather(HOME).get("activities") or {})
    out = {f: _f(v.get("temp_c")) for f, v in acts.items() if isinstance(v, dict) and _f(v.get("temp_c")) is not None}
    _WX_CACHE[str(p)] = (mt, out)
    return out


def activity_temp(ds, w, m: Optional[dict] = None) -> tuple[Optional[float], Optional[str]]:
    """(temperature °C, source) for drift_of's heat rule: the route_weather
    archive's air temperature when it has this activity (the air is what
    徐國峰's < 25 °C means; a wrist sensor is warmed by the body — doc §6.2),
    else the watch's mean over the drift window; (None, None) without either.
    A dataset may carry its own {file: temp_c} (`activity_temps`, tests)."""
    arch = getattr(ds, "activity_temps", None)
    if arch is None:
        arch = _archive_temps()
    f = getattr(getattr(w, "entry", None), "file", None)
    v = _f(arch.get(f)) if f is not None else None
    if v is not None:
        return v, "route_weather"
    wt = _f((m or {}).get("watch_temp_c"))
    return (wt, "watch") if wt is not None else (None, None)


def measure(ds, w) -> Optional[dict]:
    """Per-workout measurements (disk-memoised on CACHE_KEY), with drift_of's
    heat rule applied on read (heat_gate + activity_temp: the archive is not
    part of the cache stamp)."""
    cache = getattr(ds, "cached_series", None)
    if cache is None:
        m = _nan_free(_measure(ds, w))
    else:
        m = cache(CACHE_KEY, w, lambda: _nan_free(_measure(ds, w)))
    if m and isinstance(m.get("drift"), dict):
        m = {**m, "drift": heat_gate(m["drift"], *activity_temp(ds, w, m))}
    return m


def _flush(ds) -> None:
    f = getattr(ds, "flush_series", None)
    if f is not None:
        try:
            f()
        except Exception:
            pass


def _plan_test(ds, day: dt.date) -> dict:
    plan = getattr(ds, "plan", None)
    out = {}
    for t in getattr(plan, "thresholds", None) or []:
        if t.date == day.isoformat():
            for k in ("cp", "aethr", "lthr"):
                if getattr(t, k, None) is not None:
                    out[k] = getattr(t, k)
    return out


SAME_DAY_BOUT = 1.05          # an unfinished test that day counts with a ≥ 3′ bout ≥ 1.05 × CP
QUICK_PATTERN = 1.03          # 自組: a 20′ window ≥ 1.03 × CP without a test session = a 20′ all-out
METHOD_PROTOCOL = {"2pt": "standard", "1pt_prior": "standard", "tt20": "quick", "race": "race"}
CP_HINT = "功率型態像 CP 測試，但課表、標題都沒說是測試，所以不當測試、不算 CP；是的話在課表標成 CP 測試或標題寫「CP 測試」"
MATCH_LABEL = {"done_by": "課表對應", "same_day": "當天課表", "race": "比賽／計時跑", "threshold": "已套用的門檻",
               "title": "標題", "pattern": "功率型態", "steady": "≥ 55 分鐘穩定跑",
               "user": "你標記為測試（活動資訊）"}
AET_TITLE = re.compile(r"(?<![A-Za-z])AeT(?![A-Za-z])")


def _plan_test_sessions(ds) -> list[dict]:
    """The stored CP-test sessions (plan_store.test_sessions); a dataset may
    carry its own list (`plan_test_sessions`, tests)."""
    ss = getattr(ds, "plan_test_sessions", None)
    if ss is None:
        try:
            from backend.engine.plan_store import test_sessions
            ss = test_sessions()
        except Exception:                       # noqa: BLE001 — no plan store: nothing scheduled
            ss = []
    return list(ss or [])


def _done_by_this(s: dict, w, iso: str) -> bool:
    db = s.get("done_by") or {}
    return s.get("state") == "done" and db.get("index") == w.idx and db.get("date", iso) == iso


def scheduled_aet_test(ds, w) -> Optional[dict]:
    """The plan's AeT-test session this activity did: a stored test session
    that is the AeT test (protocol / kind aet, gen_key test_aet, or an AeT
    title — aet_test.is_aet_session) marked done by this very activity
    (index and date), the way scheduled_test matches a CP test."""
    from backend.engine.aet_test import is_aet_session
    iso = _wdate(w).isoformat()
    for s in _plan_test_sessions(ds):
        if is_aet_session(s) and _done_by_this(s, w, iso):
            return {**s, "match": "done_by"}
    return None


def scheduled_test(ds, w, m: dict) -> Optional[dict]:
    """The plan's CP-test session this activity did, or None (AeT-test
    sessions are left to scheduled_aet_test).

    1. done_by: a stored test session marked done by this very activity
       (index and date) — the plan says it was the test.
    2. an unfinished (active / missed) test session the same day, and the
       activity has a ≥ 3-min bout at ≥ 1.05 × the CP in effect."""
    from backend.engine.aet_test import is_aet_session
    iso = _wdate(w).isoformat()
    ss = [s for s in _plan_test_sessions(ds) if not is_aet_session(s)]   # the AeT test: scheduled_aet_test
    for s in ss:
        if _done_by_this(s, w, iso):
            return {**s, "match": "done_by"}
    cp, b180 = m.get("cp"), (m.get("cp_bouts") or {}).get("best180")
    if cp and b180 and b180 >= SAME_DAY_BOUT * cp:
        for s in ss:
            if s.get("day") == iso and s.get("state") in ("active", "missed"):
                return {**s, "match": "same_day"}
    return None


def _race_test(ds, w, m: dict, title: str) -> bool:
    """A 5–10 K race or time trial (protocol race, no session of its own): a
    plan race event that day (4–11 km or no distance) or a race / TT title,
    and 15–90 min of moving time."""
    from backend.engine import cp_protocols as CPP
    mv = m.get("moving_s") or 0.0
    if not (CPP.RACE_MIN_S <= mv <= 90 * 60) or not (m.get("cp_bouts") or {}).get("race"):
        return False
    if re.search(r"計時|\bTT\b|(?<!\d)(5|10)\s*[kK](?![a-zA-Z])|比賽", title or ""):
        return True
    iso = _wdate(w).isoformat()
    for e in getattr(getattr(ds, "plan", None), "events", None) or []:
        if e.kind == "race" and e.date == iso and (e.distance_km is None or 4.0 <= e.distance_km <= 11.0):
            return True
    return False


def _looks_like_quick(m: dict) -> bool:
    """自組: a 20′ window ≥ 1.03 × CP whose HR also reached LTHR (a tempo run
    against a stale, low CP would otherwise look like a test)."""
    q = (m.get("cp_bouts") or {}).get("quick")
    cp, lthr = m.get("cp"), m.get("lthr")
    if not (q and cp and q["power"] >= QUICK_PATTERN * cp):
        return False
    return not lthr or q.get("hr_peak") is None or q["hr_peak"] >= lthr


def classify(ds, w, m: Optional[dict] = None) -> dict:
    """Session type, terrain and phase on the activity date. The AeT test is
    recognised from the plan first (scheduled_aet_test: a done AeT-test
    session done_by this activity), then the title, a plan AeT row that day,
    or a ≥ 55-min steady run. A CP test is recognised from the plan first
    (scheduled_test: done_by, then the same day), then a race / TT, then a
    threshold row that day or the title. The power pattern (two separate
    bouts, or a 20′ all-out) alone is not a test any more — `cp_hint` True —
    it only picks the protocol of a test marked otherwise. `test_match` says
    which rule matched."""
    from backend.engine import cp_protocols as CPP
    from backend.engine.overview import category
    m = m if m is not None else (measure(ds, w) or {})
    day = _wdate(w)
    cat = category(w)
    phase = None
    plan = getattr(ds, "plan", None)
    if plan is not None:
        try:
            from backend.engine.planning import phase_on
            p = phase_on(plan, day)
            phase = p.kind if p else None
        except Exception:
            phase = None
    drift = m.get("drift") or {}
    aet_steady = bool(drift.get("ok")) and cat == "road"
    title = _title(w)
    runs = w.sport == "run" and cat not in ("strength", "bike", "walk", "other")
    aet_sched = scheduled_aet_test(ds, w) if runs else None
    sched = scheduled_test(ds, w, m) if runs and aet_sched is None else None
    race = runs and aet_sched is None and sched is None and _race_test(ds, w, m, title)
    std = looks_like_cp_test(m.get("cp_test"), m.get("cp"))
    quick = runs and not std and _looks_like_quick(m)
    # A CP test is only what the plan, the title or the athlete says is one (the
    # plan's session, a race, a threshold row that day, 「CP」/「測試」 in the title):
    # the power pattern alone labelled ~35 hard 5 km runs as tests against a low
    # mFTP — now it is only a hint (`cp_hint`), and picks the protocol of a marked test
    cp_detected = bool(sched) or race
    plan_test = _plan_test(ds, day)
    typ = session_type(cat, m.get("moving_s") or 0.0, m.get("hard_s") or 0.0, title,
                       plan_test, cp_detected, aet_steady,
                       hard_power_s=m.get("hard_power_s"),
                       n_efforts=len(m.get("efforts") or []) if m.get("hard_power_s") is not None else None,
                       easy_hr=bool(m.get("aet") and m.get("avg_hr") and m["avg_hr"] <= m["aet"] + AET_MARGIN),
                       plan_aet=aet_sched is not None)
    protocol = match = None
    user_test = False
    if runs and typ not in ("test_cp", "test_aet"):
        # the user's activity tag 測試 (engine/activity_tags.py) is a test mark like the plan's
        try:
            from backend.engine import activity_tags as AT
            user_test = AT.user_type(AT.user_of(w)) == "test"
        except Exception:                   # noqa: BLE001
            user_test = False
        if user_test:
            typ = "test_aet" if AET_TITLE.search(title or "") else "test_cp"
    if user_test:
        match = "user"
    if typ == "test_aet" and not user_test:
        # session_type's order: the plan's session (done_by), the title, a plan AeT row, ≥ 55′ steady
        match = ("done_by" if aet_sched is not None else
                 "title" if AET_TITLE.search(title or "") and not cp_detected else
                 "threshold" if plan_test.get("aethr") is not None else "steady")
    if typ == "test_cp":
        applied = next((t for t in getattr(plan, "thresholds", None) or []
                        if t.date == day.isoformat() and getattr(t, "cp_method", None)), None)
        if sched:
            protocol, match = CPP.protocol_of(sched), sched["match"]
        elif race:
            protocol, match = "race", "race"
        elif applied is not None:
            protocol, match = METHOD_PROTOCOL.get(applied.cp_method), "threshold"
        if protocol is None:
            protocol = CPP.protocol_of({"title": title}) or ("standard" if std else "quick" if quick else
                                                            "standard" if (m.get("cp_bouts") or {}).get("standard")
                                                            else "quick")
            match = match or ("title" if CPP.protocol_of({"title": title}) else "pattern")
    terrain = cat if cat in TERRAIN_LABEL else None
    label = "輕鬆健行" if typ == "easy" and cat == "hike" else TYPE_LABEL.get(typ, typ)
    return {"type": typ, "type_label": label, "terrain": terrain,
            "terrain_label": TERRAIN_LABEL.get(terrain, ""), "category": cat,
            "phase": phase, "phase_label": PHASE_LABEL.get(phase, "未設定周期"),
            "date": day.isoformat(), "protocol": protocol, "test_match": match,
            "cp_hint": bool(runs and (std or quick) and typ != "test_cp")}


def peers(ds, w, weeks: int, same_type: bool = True) -> list[tuple]:
    """(workout, measure, class) of the same category (and session type) in the
    `weeks` before `w`."""
    from backend.engine.overview import category
    cat = category(w)
    me = classify(ds, w)["type"] if same_type else None
    hi, lo = math.floor(w.day), math.floor(w.day) - 7 * weeks
    out = []
    for p in ds.workouts:
        if p.idx == w.idx or not (lo <= math.floor(p.day) <= hi) or p.day >= w.day or category(p) != cat:
            continue
        pm = measure(ds, p)
        if not pm:
            continue
        pc = classify(ds, p, pm)
        if same_type and pc["type"] != me:
            continue
        out.append((p, pm, pc))
    return out


def baseline_for(ds, w, get: Callable[[dict], Optional[float]], same_type: bool = True) -> dict:
    """baseline() over the previous 8 weeks, widened to 12 when 8 has < 5."""
    b = {"n": 0, "ok": False}
    for weeks in BASE_WEEKS:
        vals = [get(pm) for _, pm, _ in peers(ds, w, weeks, same_type)]
        b = {**baseline(vals), "weeks": weeks}
        if b["ok"]:
            break
    _flush(ds)
    return b


def _easy_road(ds, w, m) -> bool:
    from backend.engine.overview import category
    if category(w) != "road" or (m.get("elapsed_s") or 0) < WARMUP_S + DRIFT_REF_MIN_S:
        return False
    aet, hr = m.get("aet"), m.get("avg_hr")
    return aet is not None and hr is not None and hr <= aet + AET_MARGIN


def drift_series(ds, today: dt.date, days: int = 56, upto_idx: Optional[int] = None,
                 ref: bool = False) -> list[dict]:
    """The i_drift runs: road, ≥ 40 min on the clock, avg HR ≤ AeT+3 in the
    `days` up to `today` — oldest first, with the review's drift (refused ones
    kept with drift None) and its `tier`. Strict by default (drift_streak);
    `ref=True` (the overview indicator, display only) also keeps the
    reference-tier drifts (30–40 min after the warm-up)."""
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    out = []
    for w in ds.workouts:
        d = math.floor(w.day)
        if not (tday - days < d <= tday) or (upto_idx is not None and w.idx > upto_idx):
            continue
        if w.sport != "run" or "runningtrail" in w.tags:
            continue
        if (_f(w.metrics.get("duration")) or 0) < WARMUP_S + DRIFT_REF_MIN_S:
            continue
        m = measure(ds, w)
        if not m or not _easy_road(ds, w, m):
            continue
        dr = m.get("drift") or {}
        tier = drift_tier(dr)
        use = tier == "test" or (ref and tier == "ref")
        out.append({"idx": w.idx, "date": _wdate(w).isoformat(),
                    "drift": dr.get("drift") if use else None, "tier": tier if use else None,
                    "se": dr.get("drift_se") if use else None, "reason": dr.get("reason")})
    _flush(ds)
    return out


def drift_streak(ds, today: dt.date, upto_idx: Optional[int] = None) -> dict:
    pts = drift_series(ds, today, upto_idx=upto_idx)
    n = streak_of([p["drift"] for p in pts])
    return {"points": pts, "streak": n, "streak_ok": n >= STREAK_NEED}


def last_quality(ds, today: dt.date, days: int = 28) -> Optional[dict]:
    """The latest quality session in the `days` before `today`:
    {"idx", "date", "reps", "faded"}; None when there was none. Runs and hikes
    (QUALITY_CATEGORIES), whatever order ds.workouts is in."""
    from backend.engine.overview import category
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    best = None
    for w in sorted(ds.workouts, key=lambda x: x.day):
        d = math.floor(w.day)
        if not (tday - days <= d < tday) or category(w) not in QUALITY_CATEGORIES:
            continue
        if (_f(w.metrics.get("duration")) or 0) < 1200:
            continue
        m = measure(ds, w)
        if not m or (m.get("hard_s") or 0) < _hard_session_s():
            continue
        c = classify(ds, w, m)
        if c["type"] != "quality":
            continue
        iv = m.get("intervals") or {}
        fade = iv.get("fade")
        best = {"idx": w.idx, "date": _wdate(w).isoformat(), "reps": iv.get("n") or 0,
                "faded": fade is not None and fade < -FADE}
    _flush(ds)
    return best


def cp_eval(ds, w, m: dict, c: dict) -> Optional[dict]:
    """The CP-test result of a test_cp activity by its protocol
    (cp_protocols.result), compared with the previous result of the same
    method (cp_protocols.reference), and the 「套用這次的 CP」 payload."""
    from backend.engine import cp_protocols as CPP
    plan = getattr(ds, "plan", None)
    sex = (getattr(plan, "profile", None) or {}).get("sex")
    res = CPP.result(m.get("cp_bouts"), c.get("protocol"), m.get("lthr"), sex)
    if res is None:
        return None
    date = c.get("date") or _wdate(w).isoformat()
    ths = getattr(plan, "thresholds", None) or []
    ref = CPP.reference(ths, date, res["method"], m.get("cp"))
    delta = (res["cp"] / ref["cp"] - 1.0) if ref.get("cp") else None
    applied = any(t.date == date and t.cp is not None for t in ths)
    return {**res, "idx": w.idx, "date": date, "cp_now": m.get("cp"), "ref": ref, "delta": delta,
            "applied": applied, "method_label": CPP.METHOD_LABEL.get(res["method"], res["method"]),
            "apply": None if applied else CPP.apply_payload(res, date, w.idx)}


def latest_cp_test(ds, today: dt.date, days: int = 120) -> Optional[dict]:
    """The latest run classified test_cp: its CP by protocol (cp_eval), the
    delta against the previous result of the same method, and the apply payload."""
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    found = None
    for w in sorted(ds.workouts, key=lambda x: x.day):
        d = math.floor(w.day)
        if not (tday - days < d <= tday) or w.sport != "run":
            continue
        if (_f(w.metrics.get("duration")) or 0) < 1500:
            continue
        m = measure(ds, w)
        if not m or not m.get("cp_bouts"):
            continue
        c = classify(ds, w, m)
        if c["type"] != "test_cp":
            continue
        ev = cp_eval(ds, w, m, c)
        if ev is None:
            continue
        found = {k: ev[k] for k in ("idx", "date", "cp", "wprime", "cp_range", "cp_now", "delta", "ref", "method",
                                    "method_label", "protocol", "protocol_label", "quality", "reasons", "apply",
                                    "applied")}
    _flush(ds)
    return found


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------

def aerobic_lines(typ: str, m: dict, streak: Optional[int] = None, basis: str = "pace") -> list[str]:
    """Verdict lines for the drift on the chosen basis. Informational: the
    interval gate is engine/quality_gate.py (the old drift streak is gone);
    `streak` is accepted and ignored."""
    lines = []
    over, tot = m.get("over_aet_s"), m.get("hr_s") or 0
    if over is not None and tot > 0 and over / tot > OVER_AET_SHARE and typ in ("easy", "long"):
        lines.append(f"心率超過 AeT+3 的時間佔 {over / tot * 100:.0f}%（> 10%）：下次放慢")
    dr = m.get("drift") or {}
    # the AeT test's bands suggest a threshold: strict only; other runs show
    # the reference tier too, labelled
    ref = typ != "test_aet"
    d, why = basis_drift(dr, basis, ref=ref)
    if d is None:
        # a fair run without power: the card's Pw:HR row already says 這次沒有功率
        fair = dr.get("ok") or (ref and dr.get("ref_ok"))
        if m.get("category") in ("road", "trail") and not (basis == "power" and fair):
            lines.append(why)
        return lines[:3]
    power = basis == "power"
    name = ("Pw:HR 飄移" if power else "飄移") + ("（參考）" if drift_tier(dr) == "ref" else "")
    hr1 = dr["pw_hr1"] if power else dr["hr1"]
    if typ == "test_aet":
        # Uphill Athlete's three bands (engine/aet_test.py has the full analysis)
        if d < 0.035:
            lines.append(f"{name} {_pct(d)} < 3.5%：前半段心率 {hr1:.0f} bpm 還在 AeT 以下，下次 +5 bpm 再測")
        elif d <= DRIFT_GOOD:
            lines.append(f"{name} {_pct(d)}（3.5–5%）：前半段心率 {hr1:.0f} bpm 就是 AeT")
        else:
            lines.append(f"{name} {_pct(d)} > 5%：AeT 低於前半段心率 {hr1:.0f} bpm，下次放慢 5 bpm 再測")
        return lines[:3]
    # informational: the interval gate is engine/quality_gate.py, not this drift
    aet, hr = m.get("aet"), m.get("avg_hr")
    if d < DRIFT_GOOD and aet and hr and hr > aet + AET_MARGIN:
        lines.append(f"{name} {_pct(d)} < 5%，但平均心率 {hr:.0f} > AeT+3，不是輕鬆跑")
    elif d < DRIFT_GOOD:
        lines.append(f"{name} {_pct(d)} < 5%：有氧基礎穩")
    elif d < DRIFT_WATCH:
        lines.append(f"{name} {_pct(d)}（5–10%）：後段心率往上跑")
    else:
        lines.append(f"{name} {_pct(d)} > 10%：有氧基礎不足或跑太快")
    if drift_tier(dr) == "ref":
        lines.append(f"{REF_LABEL}：只當參考，不是 AeT 測試")
    return lines[:3]


def interval_lines(m: dict) -> list[str]:
    iv = m.get("intervals") or {}
    n = iv.get("n") or 0
    if not n:
        return ["沒有偵測到 ≥ 1 分鐘的用力段"]
    lines = []
    b = iv.get("band")
    if b:
        lines.append(f"{n} 組中 {iv['in_band']} 組落在 {b[0]} 帶（{b[1] * 100:.0f}–{min(b[2], 2) * 100:.0f}% CP）")
    else:
        lines.append(f"{n} 組（沒有 CP，無法判斷目標帶）")
    fade = iv.get("fade")
    if fade is not None and fade < -FADE:
        lines.append(f"最後一組比第一組低 {-fade * 100:.0f}%：下次組數減 1 或多休")
    drop = iv.get("hr_drop60")
    if drop is not None and drop < HR_DROP_MIN:
        lines.append(f"休息 60 秒心率降幅中位 {drop:.0f} bpm（< 20）：休息拉長")
    aet, lthr, hr = m.get("aet"), m.get("lthr"), iv.get("hr")
    if len(lines) < 3 and b and b[0] in ("閾值下", "閾值") and aet and lthr and hr and aet <= hr < lthr:
        lines.append(f"功率有到、心率 {hr:.0f} 在 AeT–LTHR 之間：屬於閾值下")
    return lines[:3]


def cp_headline(ev: dict) -> str:
    """One line: the bouts → CP (by method)."""
    lo, hi = ev["cp_range"]
    rng = f"（{lo:.0f}–{hi:.0f} W）" if hi - lo >= 1 else ""
    if ev["method"] == "2pt":
        return f"12 分 {ev['p12']:.0f} W、3 分 {ev['p3']:.0f} W → CP {ev['cp']:.0f} W，W′ {ev['wprime'] / 1000:.1f} kJ"
    if ev["method"] == "1pt_prior":
        return (f"只用 12 分 {ev['p12']:.0f} W − W′ 先驗 {ev['wprime_prior'] / 1000:.1f} kJ ÷ 720 → "
                f"CP {ev['cp']:.0f} W{rng}，{ev['quality']}")
    if ev["method"] == "tt20":
        return f"20 分 {ev['p20']:.0f} W × 0.95 → CP {ev['cp']:.0f} W{rng}"
    return f"{ev['race_s'] / 60:.0f} 分 {ev['race_power']:.0f} W → CP {ev['cp']:.0f} W{rng}（Riegel 換算，外插）"


def cp_compare_line(ev: dict) -> Optional[str]:
    """vs the previous result of the same method (cp_protocols.reference)."""
    if ev.get("applied"):
        return f"已套用到 {ev['date']} 的門檻"
    ref, dlt = ev.get("ref") or {}, ev.get("delta")
    if dlt is None:
        return "沒有可以比較的 CP（還沒有門檻）"
    who = (f"上一次同方法（{ref['date']}）" if ref.get("same_method") else
           f"目前的 CP（{ref.get('method') or 'WKO5'}{'，換算到同一基準' if ref.get('converted') else ''}）")
    verdict = "建議更新" if abs(dlt) > CP_DELTA else "不用改"
    return f"和{who} {ref['cp']:.0f} W 差 {dlt * 100:+.1f}%" + ("（> 3%）" if abs(dlt) > CP_DELTA else "") + f"：{verdict}"


def cp_lines(ev: Optional[dict], has_power: bool = True) -> list[str]:
    if not ev:
        return ["沒有功率，算不出 CP" if not has_power else "找不到這個流程的全力段，算不出 CP"]
    lines = [cp_headline(ev)]
    if ev.get("reasons"):
        lines.append(ev["reasons"][0])
    cmp_ = cp_compare_line(ev)
    if cmp_:
        lines.append(cmp_)
    return lines


# ---------------------------------------------------------------------------
# review JSON
# ---------------------------------------------------------------------------

def _row(name: str, text: str, tip: Optional[str] = None) -> dict:
    """A card row; `tip` = hover text (wko5_viewer shows it as the row's title)."""
    data = {"kind": "value", "value": text}
    if tip:
        data["tip"] = tip
    return {"name": name, "type": "line", "expression": "", "data": data}


def _col(name: str, vals: list) -> dict:
    return {"name": name, "type": "line", "expression": "", "data": {"kind": "values", "values": vals}}


def _verdict_rows(lines: list[str], label: str = "判讀") -> list[dict]:
    return [_row(label if i == 0 else "", ln) for i, ln in enumerate(lines[:3])]


def _base_text(b: dict, fmt: Callable[[float], str]) -> str:
    if not b.get("ok"):
        return f"樣本 {b.get('n', 0)} 次（< 5），不比較"
    return f"中位 {fmt(b['median'])}（IQR {fmt(b['q1'])}～{fmt(b['q3'])}，{b['n']} 次／{b.get('weeks')} 週）"


def review(ds, w, section: str = "summary", basis: str = "pace") -> dict:
    """The card JSON for one section of views/workout.json. `basis` (pace /
    power, the chart's 配速／功率 toggle) only changes the aerobic card."""
    m = measure(ds, w)
    _flush(ds)
    base = {"kind": "review", "section": section, "workout": w.idx, "axes": [], "series": [],
            "empty": None, "description": None, "basis": basis if basis == "power" else "pace"}
    if not m:
        return {**base, "empty": "這筆活動沒有逐秒資料"}
    c = classify(ds, w, m)
    base["classification"] = c
    base["suggested_dashboard"] = (2 if c["type"] == "test_cp" else 3 if c["terrain"] in ("trail", "hike")
                                   else SUGGESTED.get(c["type"], 0))
    fn = _SECTIONS.get(section)
    if fn is None:
        return {**base, "empty": f"沒有這個判讀：{section}"}
    return fn(ds, w, m, c, base)


def _streak_for(ds, w) -> Optional[int]:
    try:
        return drift_streak(ds, _wdate(w), upto_idx=w.idx)["streak"]
    except Exception:
        return None


def _summary(ds, w, m, c, base):
    typ = c["type"]
    rows = [_row("課表", f"{c['type_label']}" + (f" · {c['terrain_label']}" if c["terrain_label"] else "")
                 + f" · {c['phase_label']}"),
            _row("時間", f"移動 {_hms(m.get('moving_s'))} ／ 全程 {_hms(m.get('elapsed_s'))}")]
    aet, lthr, cp = m.get("aet"), m.get("lthr"), m.get("cp")
    if m.get("avg_hr"):
        rows.append(_row("心率", f"平均 {m['avg_hr']:.0f} bpm（AeT {_num(aet)}、LTHR {_num(lthr)}）"))
    z = m.get("zones")
    if z and sum(z.values()) > 0:
        tot = sum(z.values())
        rows.append(_row("三區", f"< AeT {z['low'] / tot * 100:.0f}% · AeT–LTHR {z['mid'] / tot * 100:.0f}% · "
                                 f"≥ LTHR {z['high'] / tot * 100:.0f}%"))
    if typ in ("strength", "bike", "walk", "other"):
        return {**base, "series": rows + _verdict_rows([f"{c['type_label']}：只看時間與心率，沒有其他判讀"])}
    if typ == "test_aet":
        lines = _aet_test_lines(ds, w, m, base) or aerobic_lines(typ, m)
    elif typ in ("easy", "long"):
        lines = aerobic_lines(typ, m)
    elif typ == "quality":
        lines = interval_lines(m)
    else:
        ev = cp_eval(ds, w, m, c)
        lines = cp_lines(ev, m.get("avg_power") is not None)
        if ev and ev.get("apply"):
            base = {**base, "action": _apply_action(ev)}
    if c["terrain"] in ("trail", "hike"):
        cl = m.get("climbs") or []
        if cl:
            rows.append(_row("爬坡", f"{len(cl)} 段、共 {sum(x['gain_m'] for x in cl):.0f} m；"
                                     f"每 100 m 心跳 {_num(m.get('hr_per_100m'))} 下"))
        lines = (_trail_lines(ds, w, m) + lines)[:3]
    if c.get("cp_hint"):
        rows.append(_row("CP 測試？", CP_HINT))
    rows.append(_row("建議分頁", ["本次重點", "有氧／心率飄移", "間歇", "爬坡與地形", "配速與耐久",
                                "跑姿與膝蓋負荷（參考）"][base["suggested_dashboard"]]))
    return {**base, "series": rows + _verdict_rows(lines)}


def _aerobic(ds, w, m, c, base):
    dr = m.get("drift") or {}
    basis = base.get("basis") or "pace"
    power = basis == "power"
    # display: the reference tier too (labelled, with the why on hover); the AeT
    # test's own lines stay strict (_aet_test_lines / aerobic_lines)
    d, why = basis_drift(dr, basis, ref=True)
    ref = d is not None and drift_tier(dr) == "ref"
    tag = "（參考）" if ref else ""
    rows = []
    se = dr.get("pw_drift_se" if power else "drift_se")
    pm = f" {se_text(se)}" if d is not None and se is not None else ""
    if d is not None and power:
        rows += [_row("Pw:HR 飄移", f"{_pct(d)}{pm}（{start_text(dr)}）{tag}", REF_TIP if ref else SE_TIP),
                 _row("前半／後半心率", f"{dr['pw_hr1']:.0f} → {dr['pw_hr2']:.0f} bpm"),
                 _row("前半／後半功率", f"{dr['p1']:.0f} → {dr['p2']:.0f} W")]
    elif d is not None:
        rows += [_row("Pa:HR 飄移", f"{_pct(d)}{pm}（{start_text(dr)}）{tag}", REF_TIP if ref else SE_TIP),
                 _row("前半／後半心率", f"{dr['hr1']:.0f} → {dr['hr2']:.0f} bpm"),
                 _row("前半／後半速度", f"{dr['v1']:.2f} → {dr['v2']:.2f} km/h")]
    if ref:
        rows.append(_row("飄移等級", REF_LABEL, REF_TIP))
    elif d is not None:
        rows.append(_row("飄移等級", "嚴格（暖身後 ≥ 40 分，UA 測試標準）"))
    if d is not None and se is not None and se > DRIFT_NOISY_SE:
        rows.append(_row("精度", f"這次很吵（{se_text(se)} > ±{DRIFT_NOISY_SE * 100:.0f} pp）：只當多次平均的一個點（門檻推估）",
                         SE_TIP))
    ex = excluded_text(dr)
    if ex and m.get("category") == "road":
        rows.append(_row("已排除", ex, START_TIP + " " + TAIL_TIP))
    st = stability_text(dr, power)
    if st and m.get("category") == "road" and (d is not None or dr.get("vi") is not None):
        rows.append(_row("穩定度", st, STABILITY_TIP))
    rt = ramps_text(dr, power)
    if rt and d is not None:
        rows.append(_row("坡道", rt, RAMP_TIP))
    if d is None and power and (dr.get("ok") or dr.get("ref_ok") or m.get("avg_power") is None):
        # nothing to show on this basis (no power, or too little): say so, not 0 %
        rows.append(_row("Pw:HR 飄移", "這次沒有功率" if m.get("avg_power") is None else why))
    if m.get("category") in ("road", "trail"):
        tc = dr.get("temp_c")
        rows.append(_row("溫度", f"{TEMP_SRC_LABEL.get(dr.get('temp_src'), '溫度')} {tc:.0f} °C" if tc is not None
                         else f"沒有溫度資料（> {DRIFT_HEAT_C:.0f} °C 檢查不到）"))
    over, tot = m.get("over_aet_s"), m.get("hr_s") or 0
    if over is not None and tot > 0:
        rows.append(_row("超過 AeT+3", f"{_hms(over)}（{over / tot * 100:.0f}%）"))
    if c["type"] in ("easy", "long", "test_aet") and d is not None:
        b = baseline_for(ds, w, lambda pm: basis_drift(pm.get("drift") or {}, basis, ref=True)[0])
        rows.append(_row("同類課表基準", _base_text(b, lambda x: _pct(x))))
    if c["type"] not in ("easy", "long", "test_aet"):
        lines = [f"這次是{c['type_label']}，飄移只在輕鬆跑、長跑、AeT 測試判讀"]
    elif c["type"] == "test_aet":
        lines = _aet_test_lines(ds, w, m, base) or aerobic_lines("test_aet", m)
    else:
        lines = aerobic_lines(c["type"], m, None, basis)
    return {**base, "series": rows + _verdict_rows(lines)}


def _aet_test_lines(ds, w, m, base: dict) -> Optional[list[str]]:
    """AeT test card: UA's three bands on the block after the 15′ warm-up
    (engine/aet_test.py) and, in band "at", the 「套用這次的 AeT」 button
    (`base["action"]`, the viewer's drawAction). None when it isn't a fair test."""
    from backend.engine import aet_test as AT
    try:
        r = AT.analyze_workout(ds, w, m)
    except Exception:
        return None
    if r is None:
        return None
    if not r.get("ok"):
        return AT.lines(r)
    t = {"date": _wdate(w).isoformat(), "drift": r["drift"], "basis": r["basis"],
         "aethr_suggest": round(r["hr1"]) if r["band"] == "at" else None}
    body = AT.apply_body(t)
    plan = getattr(ds, "plan", None)
    if body and not AT.applied(plan, t):
        base["action"] = {"kind": "apply_aet", "label": f"套用這次的 AeT（{body['aethr']} bpm）", "method": "POST",
                          "url": "/api/v1/plan/thresholds/apply-estimate", "body": body,
                          "done": f"已套用：{body['date']} AeT {body['aethr']} bpm",
                          "confirm": f"把 {body['date']} 的 AeT 設成 {body['aethr']} bpm？區間和間歇門檻會重新計算"}
    return AT.lines(r, m.get("aet"))


def _intervals(ds, w, m, c, base):
    eff = m.get("efforts") or []
    if not eff:
        return {**base, "empty": "沒有偵測到 ≥ 1 分鐘的用力段（需要功率）"
                if c["type"] in ("quality", "test_cp") else f"這次是{c['type_label']}，不是間歇課"}
    cols = [_col("組", [str(i + 1) for i in range(len(eff))]),
            _col("開始", [_hms(e["start_s"]) for e in eff]),
            _col("時長", [_hms(e["duration_s"]) for e in eff]),
            _col("功率 W", [_num(e["power"]) for e in eff]),
            _col("%CP", [_pct(e["pct_cp"], 0) if e.get("pct_cp") is not None else "–" for e in eff]),
            _col("心率", [_num(e.get("hr")) for e in eff]),
            _col("最高心率", [_num(e.get("hr_max")) for e in eff]),
            _col("休 60 秒降", [_num(e.get("hr_drop60")) for e in eff])]
    lines = cp_lines(cp_eval(ds, w, m, c), m.get("avg_power") is not None) if c["type"] == "test_cp" \
        else interval_lines(m)
    if c["type"] not in ("quality", "test_cp"):
        lines = [f"這次是{c['type_label']}，下表只是偵測到的用力段"] + lines[:2]
    return {**base, "series": cols + _verdict_rows(lines)}


def _trail_lines(ds, w, m) -> list[str]:
    lines = []
    hp = m.get("hr_per_100m")
    if hp:
        b = baseline_for(ds, w, lambda pm: pm.get("hr_per_100m"), same_type=False)
        if b.get("ok"):
            chg = hp / b["median"] - 1
            if chg < -CLIMB_BETTER:
                lines.append(f"每 100 m 爬升 {hp:.0f} 下心跳，比近 {b['weeks']} 週中位 {b['median']:.0f} 低 {-chg * 100:.0f}%：爬坡經濟性變好")
            elif chg > CLIMB_BETTER:
                lines.append(f"每 100 m 爬升 {hp:.0f} 下心跳，比近 {b['weeks']} 週中位 {b['median']:.0f} 高 {chg * 100:.0f}%")
            else:
                lines.append(f"每 100 m 爬升 {hp:.0f} 下心跳，和近 {b['weeks']} 週差不多")
        else:
            lines.append(f"每 100 m 爬升 {hp:.0f} 下心跳（近期爬坡樣本 {b.get('n', 0)} 次，不比較）")
    last = _last20(ds, w)
    if last is not None and last < LAST20_MIN:
        lines.append(f"最後 20% 耐久 {last * 100:.0f}%（< 90%）：補給、配速要調整")
    return lines


# ---------------------------------------------------------------------------
# 爬坡段: elevation profile + per-climb comparison; 坡度分組: one row per grade bin
# (wko5_viewer drawClimbProfile / drawGradeProfile; res.climb_profile / res.grade_profile)
# ---------------------------------------------------------------------------

PROFILE_POINTS = 900          # points on the drawn profile (display only)
PROFILE_WIN_S = 60.0          # 推估: centred 60-s window for the profile's VAM / pace / power / HR (1-s VAM is noise)
POOL_WEEKS = (8, 12, 26)      # 推估: "your usual" = the same category's previous 8 weeks, widened to 12, then 26, until ≥ 5
CLIMB_GRADE_MATCH = 0.04      # 推估: a climb is compared with past climbs within ±4 pp of its grade (VAM rises with grade)
BIN_MIN_S = 60.0              # 推估: a past workout counts for a grade bin when it spent ≥ 60 s in it
CLIMB_KEYS = ("vam", "pct_cp", "hr_per_100m", "gap_s_per_km")
BIN_KEYS = ("pace_s_per_km", "vam", "power", "hr", "eff_speed", "eff_power")
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def climb_no(i: int) -> str:
    return CIRCLED[i] if i < len(CIRCLED) else f"({i + 1})"


def profile_series(t, dist, elev, grade=None, hr=None, power=None, walking: bool = False,
                   n: int = PROFILE_POINTS, win_s: float = PROFILE_WIN_S) -> Optional[dict]:
    """The elevation profile against distance, ~`n` points evenly spaced in km,
    each with the centred `win_s` window's VAM (m/h), moving pace and GAP
    (s/km; None when stopped), HR and power. None without distance and altitude."""
    t = np.asarray(t, dtype=float)
    N = len(t)
    d, e = _arr(dist, N), _arr(elev, N)
    ok = np.isfinite(t) & np.isfinite(d) & np.isfinite(e)
    if ok.sum() < 10:
        return None
    ii = np.flatnonzero(ok)
    tt, dd, ee = t[ii], np.maximum.accumulate(d[ii]), e[ii]
    if dd[-1] - dd[0] < 0.2 or not np.all(np.diff(tt) >= 0):
        return None
    sel = np.unique(np.searchsorted(dd, np.linspace(dd[0], dd[-1], n)).clip(0, len(dd) - 1))
    lo = np.searchsorted(tt, tt[sel] - win_s / 2).clip(0, len(tt) - 1)
    hi = (np.searchsorted(tt, tt[sel] + win_s / 2, side="right") - 1).clip(0, len(tt) - 1)
    span = tt[hi] - tt[lo]
    with np.errstate(all="ignore"):
        kmh = np.where(span > 0, (dd[hi] - dd[lo]) / span * 3600.0, np.nan)
        moving = np.isfinite(kmh) & (kmh > STOP_KMH)
        vam = np.where(moving, (ee[hi] - ee[lo]) / span * 3600.0, np.nan)
        pace = np.where(moving, 3600.0 / kmh, np.nan)
        if grade is not None:
            g = _arr(grade, N)[ii][sel]
        else:
            run = (dd[hi] - dd[lo]) * 1000.0
            g = np.where(run > 5, (ee[hi] - ee[lo]) / run, np.nan)
        gf = np.array([_gap_factor(float(x), walking) if np.isfinite(x) else np.nan for x in g], dtype=float)
        gap = pace / gf

    def wmean(x):
        if x is None:
            return None
        v = _arr(x, N)[ii]
        okv = np.isfinite(v) & (v > 0)
        cs = np.concatenate([[0.0], np.cumsum(np.where(okv, v, 0.0))])
        cn = np.concatenate([[0.0], np.cumsum(okv.astype(float))])
        cnt = cn[hi + 1] - cn[lo]
        with np.errstate(all="ignore"):
            return np.where(cnt > 0, (cs[hi + 1] - cs[lo]) / np.maximum(cnt, 1), np.nan)

    def r(a, k):
        if a is None:
            return None
        out = [None if not np.isfinite(x) else round(float(x), k) for x in a]
        return out if any(x is not None for x in out) else None

    pace = np.where(pace <= 3600, pace, np.nan)
    gap = np.where(gap <= 3600, gap, np.nan)
    return {"x": r(dd[sel], 3), "alt": r(ee[sel], 1), "t": r(tt[sel] - tt[0], 0), "grade": r(g * 100.0, 1),
            "vam": r(vam, 0), "pace": r(pace, 0), "gap": r(gap, 0), "hr": r(wmean(hr), 0),
            "power": r(wmean(power), 0)}


def descents_of(t, dist, elev, hr=None, moving=None) -> list[dict]:
    """Sustained descents: detect_climbs on the mirrored elevation (≥ 80 m down, ≥ 3 %)."""
    neg = [None if x is None else -x for x in _none_list(elev)]
    try:
        cl = detect_climbs(_none_list(t), _none_list(dist), neg, _none_list(hr) if hr is not None else None,
                           moving=moving)
    except Exception:
        return []
    dk = _arr(dist, len(neg))
    return [{"start_km": _f(dk[c.start_index]), "end_km": _f(dk[c.end_index]), "t_start": c.t_start,
             "duration_s": c.duration_s, "distance_km": c.distance_km, "drop_m": c.gain_m, "grade": -c.grade,
             "rate": -c.vam_m_per_h, "avg_hr": c.avg_hr, "start_elev": -c.start_elev_m, "end_elev": -c.top_elev_m,
             "pace_s_per_km": c.duration_s / c.distance_km if c.distance_km > 0 else None} for c in cl]


def _pool(ds, w, weeks: int = POOL_WEEKS[-1]) -> list[tuple[float, dict]]:
    """(age in days, measure) of the same category's workouts in the `weeks` before `w`."""
    from backend.engine.overview import category
    cat, lo = category(w), math.floor(w.day) - 7 * weeks
    out = []
    for p in ds.workouts:
        if p.idx == w.idx or p.day >= w.day or math.floor(p.day) < lo or category(p) != cat:
            continue
        pm = measure(ds, p)
        if pm:
            out.append((w.day - p.day, pm))
    _flush(ds)
    return out


def pooled(items: Sequence[tuple[float, Optional[float]]]) -> dict:
    """baseline() over the values aged ≤ 8 weeks, widened to 12 and 26 until ≥ 5 (POOL_WEEKS)."""
    b = {"n": 0, "ok": False}
    for weeks in POOL_WEEKS:
        b = {**baseline([v for age, v in items if age <= 7 * weeks]), "weeks": weeks}
        if b["ok"]:
            break
    return b


def _with_pct_cp(c: dict, cp: Optional[float]) -> dict:
    p = c.get("avg_power")
    return {**c, "pct_cp": (p / cp) if p and cp else None}


def climb_baselines(pool: list[tuple[float, dict]], climb: dict) -> dict:
    """Per metric (CLIMB_KEYS), the athlete's usual on past climbs of a similar grade."""
    near = [(age, _with_pct_cp(pc, pm.get("cp"))) for age, pm in pool for pc in (pm.get("climbs") or [])
            if pc.get("grade") is not None and abs(pc["grade"] - climb["grade"]) <= CLIMB_GRADE_MATCH]
    return {k: pooled([(age, pc.get(k)) for age, pc in near]) for k in CLIMB_KEYS}


def _bin_metrics(r: dict) -> dict:
    pace, hr, p = _f(r.get("pace_s_per_km")), _f(r.get("hr")), _f(r.get("power"))
    return {"pace_s_per_km": pace, "vam": _f(r.get("vam")), "power": p, "hr": hr,
            "eff_speed": (60000.0 / pace / hr) if pace and hr else None,     # m/min per bpm
            "eff_power": (p / hr) if p and hr else None}                    # W per bpm


def bin_baselines(pool: list[tuple[float, dict]], label: str) -> dict:
    rows = [(age, _bin_metrics(r)) for age, pm in pool for r in (pm.get("grade_bins") or [])
            if r.get("label") == label and (r.get("time_s") or 0) >= BIN_MIN_S]
    return {k: pooled([(age, x[k]) for age, x in rows]) for k in BIN_KEYS}


def _vs(x: Optional[float], b: dict) -> Optional[float]:
    x = _f(x)
    return (x / b["median"] - 1.0) if x is not None and b.get("ok") and b.get("median") else None


def _climb_cards(ds, w, m) -> list[dict]:
    pool = _pool(ds, w)
    cp = m.get("cp")
    out = []
    for i, x in enumerate(m.get("climbs") or []):
        x = _with_pct_cp(x, cp)
        b = climb_baselines(pool, x)
        out.append({**x, "no": climb_no(i), "base": b, "vs": {k: _vs(x.get(k), b[k]) for k in CLIMB_KEYS}})
    return out


def _climb_lines(cards: list[dict]) -> list[str]:
    """VAM against the athlete's usual on similar-grade climbs: which climbs were faster / slower."""
    hi = [c["no"] for c in cards if compare(c.get("vam"), c["base"]["vam"]) == "high"]
    lo = [c["no"] for c in cards if compare(c.get("vam"), c["base"]["vam"]) == "low"]
    if not any(c["base"]["vam"].get("ok") for c in cards):
        return []
    if not hi and not lo:
        return ["每段的 VAM 都在你平常（相近坡度）的四分位範圍內"]
    parts = ([f"VAM 高於平常：{''.join(hi)}"] if hi else []) + ([f"低於平常：{''.join(lo)}"] if lo else [])
    return ["；".join(parts) + "（和近期相近坡度的爬坡比）"]


def _climbs(ds, w, m, c, base):
    cl = m.get("climbs") or []
    s = _samples(ds, w)
    walking = m.get("category") in ("hike", "walk")
    prof = None
    if s is not None and s["dist"] is not None and s["elev"] is not None:
        prof = profile_series(s["t"], s["dist"], s["elev"], _eval(ds, w, "rgrade"), s["hr"], s["power"], walking)
    if not cl and prof is None:
        return {**base, "empty": "這筆活動沒有海拔資料，畫不出高度圖，也找不到爬坡段"}
    cards = _climb_cards(ds, w, m) if cl else []
    desc = []
    if prof is not None:
        desc = descents_of(s["t"], s["dist"], s["elev"], s["hr"], list(moving_mask(s["t"], s["speed"])))
    cols = []
    if cards:
        cols = [_col("段", [x["no"] for x in cards]),
                _col("開始", [_hms(x["t_start"]) for x in cards]),
                _col("從 km", [_num(x.get("start_km"), 1) for x in cards]),
                _col("爬升 m", [_num(x["gain_m"]) for x in cards]),
                _col("距離 km", [_num(x["distance_km"], 2) for x in cards]),
                _col("坡度", [_pct(x["grade"], 0) for x in cards]),
                _col("時間", [_hms(x["duration_s"]) for x in cards]),
                _col("VAM m/h", [_num(x["vam"]) for x in cards]),
                _col("VAM 平常", [_num(x["base"]["vam"].get("median")) if x["base"]["vam"].get("ok") else "–"
                                for x in cards]),
                _col("平均心率", [_num(x.get("avg_hr")) for x in cards]),
                _col("每 100 m 心跳", [_num(x.get("hr_per_100m")) for x in cards]),
                _col("功率 W", [_num(x.get("avg_power")) for x in cards]),
                _col("%CP", [_pct(x.get("pct_cp"), 0) for x in cards]),
                _col("配速 /km", [_pace(x.get("pace_s_per_km")) for x in cards]),
                _col("GAP /km", [_pace(x.get("gap_s_per_km")) for x in cards])]
    lines = (_trail_lines(ds, w, m) + _climb_lines(cards))[:3] if cards else []
    if cards and not lines:
        lines = ["（沒有心率，無法比較爬坡經濟性）"]
    note = None if cards else "這次沒有 ≥ 80 m、坡度 ≥ 3% 的連續爬坡"
    if prof is None:
        note = "這筆活動沒有海拔資料，畫不出高度圖"
    return {**base, "series": cols + _verdict_rows(lines),
            "climb_profile": {"profile": prof, "climbs": cards, "descents": desc, "cp": m.get("cp"),
                              "walking": walking, "note": note,
                              "grade_match": CLIMB_GRADE_MATCH, "pool_weeks": list(POOL_WEEKS)}}


def _grades(ds, w, m, c, base):
    rows = m.get("grade_bins") or []
    if not rows:
        return {**base, "empty": "沒有距離或海拔，算不出坡度"}
    pool = _pool(ds, w)
    bins = []
    for r in rows:
        mt = _bin_metrics(r)
        b = bin_baselines(pool, r["label"])
        bins.append({**{k: r.get(k) for k in ("label", "lo", "hi", "time_s", "time_pct", "distance_km")},
                     "metrics": mt, "base": b, "vs": {k: _vs(mt[k], b[k]) for k in BIN_KEYS}})
    cols = [_col("坡度", [r["label"] for r in rows]),
            _col("時間", [_hms(r["time_s"]) for r in rows]),
            _col("佔比", [f"{r['time_pct']:.0f}%" for r in rows]),
            _col("距離 km", [_num(r["distance_km"], 2) for r in rows]),
            _col("配速 /km", [_pace(r["pace_s_per_km"]) for r in rows]),
            _col("配速平常", [_pace(b["base"]["pace_s_per_km"].get("median")) if b["base"]["pace_s_per_km"].get("ok")
                             else "–" for b in bins]),
            _col("VAM m/h", [_num(r.get("vam")) for r in rows]),
            _col("心率", [_num(r["hr"]) for r in rows]),
            _col("功率 W", [_num(r["power"]) for r in rows])]
    return {**base, "series": cols,
            "grade_profile": {"bins": bins, "min_s": BIN_MIN_S, "pool_weeks": list(POOL_WEEKS),
                              "walking": m.get("category") in ("hike", "walk")}}


def _durability_pts(ds, w) -> Optional[dict]:
    s = _samples(ds, w)
    if s is None or s["hr"] is None:
        return None
    out = s["power"] if s["power"] is not None else s["speed"]
    if out is None:
        return None
    return durability(s["t"], out, s["hr"])


def _last20(ds, w) -> Optional[float]:
    r = _durability_pts(ds, w)
    if not r or not r["points"]:
        return None
    xs = [p[0] for p in r["points"]]
    cut = xs[0] + 0.8 * (xs[-1] - xs[0])
    tail = [p[1] for p in r["points"] if p[0] >= cut]
    return statistics.mean(tail) / 100.0 if tail else None


def _durability(ds, w, m, c, base):
    rows = [_row("移動／停留", f"移動 {_hms(m.get('moving_s'))}，停留 "
                            f"{_hms(max(0.0, (m.get('elapsed_s') or 0) - (m.get('moving_s') or 0)))}")]
    last = _last20(ds, w)
    lines = []
    if last is None:
        rows.append(_row("耐久", "資料不夠（要有心率、功率或速度，且 ≥ 20 分鐘）"))
    else:
        rows.append(_row("最後 20% 耐久", f"{last * 100:.0f}%（輸出／心率，和前段相比）"))
        if last < LAST20_MIN:
            lines.append(f"最後 20% 耐久 {last * 100:.0f}%（< 90%）：補給、配速要調整")
        else:
            lines.append(f"最後 20% 耐久 {last * 100:.0f}%：後段撐得住")
    rows.append(_row("補給", "沒有補給紀錄"))
    return {**base, "series": rows + _verdict_rows(lines)}


def _durability_curve(ds, w, m, c, base):
    r = _durability_pts(ds, w)
    if not r or not r["points"]:
        return {**base, "empty": "資料不夠：要有心率和功率（或速度），且超過 20 分鐘"}
    from backend.engine.wko5expr import units as U
    pct = U.meta("PERCENT")
    xu = {"id": "HOURS", "label": "h", "kind": "number", "dec": [[0, 1]]}
    pts = [[round(x, 4), round(y / 100.0, 4)] for x, y in r["points"]]
    s = [{"name": "同樣心率的輸出（暖身後 = 100%）", "type": "line", "expression": "", "y_axis": "PERCENT",
          "unit": pct, "x_unit": xu, "color": "#2a78d6", "data": {"kind": "points", "x": "value", "points": pts}},
         {"name": "95%：開始累（推估）", "type": "line", "expression": "", "y_axis": "PERCENT", "unit": pct,
          "x_unit": xu, "color": "#c98a00", "line_style": "dash", "data": {"kind": "hline", "y": 0.95}},
         {"name": "90%：明顯掉了，補給或配速要調", "type": "line", "expression": "", "y_axis": "PERCENT",
          "unit": pct, "x_unit": xu, "color": "#c0392b", "line_style": "dash", "data": {"kind": "hline", "y": 0.9}}]
    last = _last20(ds, w)
    out = {**base, "axes": [{"id": "PERCENT", "unit": pct}], "series": s}
    if last is not None:
        word = "後段撐得住" if last >= 0.95 else ("後段開始累" if last >= LAST20_MIN else "後段明顯掉了")
        out["subtitle"] = f"最後 20%：{last * 100:.0f}% → {word}"
    return out


def _pacing(ds, w, m, c, base):
    s = _samples(ds, w)
    if s is None or s["dist"] is None:
        return {**base, "empty": "沒有距離資料"}
    rows = pacing_deciles(s["t"], s["dist"], s["hr"], s["power"], s["speed"])
    if not rows:
        return {**base, "empty": "距離太短"}
    cols = [_col("段", [f"{r['k'] * 10}–{r['k'] * 10 + 10}%" for r in rows]),
            _col("從 km", [_num(r["from_km"], 1) for r in rows]),
            _col("配速 /km", [_pace(r["pace_s_per_km"]) for r in rows]),
            _col("心率", [_num(r["hr"]) for r in rows]),
            _col("功率 W", [_num(r["power"]) for r in rows])]
    return {**base, "series": cols}


FORM_ROWS = (("ilr", "衝擊負荷 ILR", 1), ("lss", "腿部剛性 LSS kN/m", 1), ("kleg", "kleg kN/m", 1),
             ("gct", "觸地時間 ms", 0), ("cadence", "步頻 spm", 0), ("vo", "垂直振幅 cm", 1),
             ("impact_g", "衝擊 G（fmax／體重）", 2))


def _form(ds, w, m, c, base):
    f = m.get("form") or {}
    if w.sport != "run" or not f:
        return {**base, "empty": "參考：只有跑步、而且有跑步動態資料時才有這一頁"}
    names, first, last, chg, basecol = [], [], [], [], []
    for k, label, d in FORM_ROWS:
        v = f.get(k)
        if not v or v.get("first") is None:
            continue
        if k in ("ilr", "lss") and not m.get("stryd"):
            continue
        b = baseline_for(ds, w, lambda pm, k=k: ((pm.get("form") or {}).get(k) or {}).get("change"))
        names.append(label)
        first.append(_num(v["first"], d))
        last.append(_num(v["last"], d))
        chg.append(_pct(v.get("change"), 1, sign=True))
        basecol.append(_base_text(b, lambda x: _pct(x, 1, sign=True)))
    half = "作功" if f.get("_split") == "work" else "移動時間"
    cols = [_col("指標（參考）", names), _col(f"前半（{half}）", first), _col(f"後半（{half}）", last),
            _col("變化", chg), _col("同類課表變化基準", basecol)]
    s = _samples(ds, w)
    g = _eval(ds, w, "rgrade")
    dh = downhill_share(s["t"], g, s["dist"], s["ilr"]) if s is not None else None
    rows = []
    if dh and dh.get("time_s"):
        rows.append(_row("陡下坡（< −10%）", f"{_hms(dh['time_s'])}（時間 {_pct(dh['time_share'], 0)}"
                                           f"、距離 {_pct(dh.get('dist_share'), 0)}"
                                           + (f"、衝擊量 {_pct(dh['impact_share'], 0)}" if dh.get("impact_share") is not None else "")
                                           + "）"))
    if not m.get("stryd"):
        rows.append(_row("說明", "沒有 Stryd：只看步頻、觸地、振幅、kleg、衝擊 G，無法判讀衝擊與剛性"))
    lines = []
    ilr = f.get("ilr") or {}
    if m.get("stryd") and ilr.get("change") is not None:
        b = baseline_for(ds, w, lambda pm: ((pm.get("form") or {}).get("ilr") or {}).get("change"))
        share = (dh or {}).get("impact_share")
        share = share if share is not None else (dh or {}).get("time_share")
        if compare(ilr["change"], b) == "high" and share is not None and share > STEEP_DOWN_SHARE:
            lines.append("參考：後段 ILR 升幅超出自己的 IQR，且陡下坡佔 > 30%：留意膝蓋，下坡放慢或縮步")
    lss, gct = (f.get("lss") or {}).get("change"), (f.get("gct") or {}).get("change")
    if lss is not None and gct is not None and lss < -0.02 and gct > 0.02:
        lines.append(f"參考：LSS {lss * 100:+.1f}%、觸地 {gct * 100:+.1f}%：疲勞跡象")
    if not lines:
        lines.append("參考：沒有明顯的跑姿／膝蓋負荷變化")
    return {**base, "series": cols + rows + _verdict_rows(lines, "判讀（參考）")}


# 跑姿分組 (wko5_viewer drawFormProfile; res.form_profile): the form metrics per grade bin
# (form_grades) and per 10 % of the work done (form_work), each against the athlete's usual
# for that bin — the same pool as 坡度分組 (_pool + pooled: same category, 8 → 12 → 26 weeks
# until ≥ 5 activities that spent ≥ BIN_MIN_S running in the bin; 推估)
FORM_DEC = {"ilr": 1, "impact_g": 2, "impact_km": 0, "lss": 1, "kleg": 1, "gct": 0, "cadence": 0, "vo": 1}
FORM_LABEL = {"ilr": "ILR", "impact_g": "衝擊 G", "impact_km": "每公里衝擊量（推估）", "lss": "LSS", "kleg": "kleg",
              "gct": "觸地時間", "cadence": "步頻", "vo": "垂直振幅"}
CADENCE_HINT = "衝擊高於平常、步頻低於平常 → 可試著把步頻提高 5–10%（Heiderscheit 2011）"


def cadence_hint(rows: list[dict], names: list[str]) -> Optional[str]:
    """「−10 ~ −5%、0–10%：衝擊高於平常、步頻低於平常 → …」 for the rows (≥ BIN_MIN_S) where ILR or
    impact G is above the usual's middle 50 % and the cadence below it; None when there is none.
    Heiderscheit 2011 (MSSE 43:296): +5 / +10 % step rate cut the energy absorbed at the knee."""
    hit = []
    for r, name in zip(rows, names):
        if (r.get("time_s") or 0) < BIN_MIN_S:
            continue
        m, b = r.get("m") or {}, r.get("base") or {}
        high = any(compare(m.get(k), b.get(k) or {}) == "high" for k in IMPACT_KEYS)
        if high and compare(m.get("cadence"), b.get("cadence") or {}) == "low":
            hit.append(name)
    return f"{'、'.join(hit)}：{CADENCE_HINT}" if hit else None


def _hint_rows(hint: Optional[str]) -> list[dict]:
    return _verdict_rows([hint], "判讀（參考）") if hint else []
BAND_LABEL = {"all": "全部坡度", "flat": "平路 −3～+3%", "up": "上坡 ≥ 3%", "down": "下坡 < −3%"}
NO_STRYD_NOTE = ("這次沒有 Stryd：ILR（衝擊負荷率）和 LSS（腿部剛性）是 Stryd 腳掌感測器算的，手錶沒有，"
                 "所以只看步頻、觸地時間、垂直振幅、kleg、衝擊 G")


def _form_base(items: list[tuple[float, Optional[dict]]]) -> dict:
    """{metric: pooled()} over past rows (age, row) that spent ≥ BIN_MIN_S running in the bin."""
    ok = [(age, r) for age, r in items if r and (r.get("time_s") or 0) >= BIN_MIN_S]
    return {k: pooled([(age, (r.get("m") or {}).get(k)) for age, r in ok]) for k in FORM_KEYS}


def _form_keys(rows: list[dict], stryd: bool) -> list[str]:
    return [k for k in FORM_KEYS if (stryd or k not in STRYD_ONLY)
            and any((r.get("m") or {}).get(k) is not None for r in rows)]


def _form_cell(v, b: dict, k: str) -> str:
    d = FORM_DEC[k]
    s = _num(v, d)
    return f"{s}（平常 {_num(b['median'], d)}）" if v is not None and b.get("ok") else s


def _form_note_rows(m: dict) -> list[dict]:
    return [] if m.get("stryd") else [_row("說明", NO_STRYD_NOTE)]


def _form_grades(ds, w, m, c, base):
    fb = m.get("form_bins") or {}
    rows = fb.get("grade") or []
    if w.sport != "run" or not fb:
        return {**base, "empty": "參考：只有跑步、而且有跑步動態資料時才有這一張"}
    if not rows:
        return {**base, "empty": "沒有距離或海拔，算不出坡度"}
    keys = _form_keys(rows, bool(m.get("stryd")))
    if not keys:
        return {**base, "empty": "這次沒有跑步動態資料（步頻、觸地時間…）"}
    pool = _pool(ds, w)
    total = sum(r["time_s"] for r in rows) or 1.0
    bins = []
    for r in rows:
        past = [(age, next((x for x in ((pm.get("form_bins") or {}).get("grade") or []) if x["label"] == r["label"]), None))
                for age, pm in pool]
        b = _form_base(past)
        bins.append({**{k: r[k] for k in ("label", "lo", "hi", "time_s")}, "time_pct": r["time_s"] / total * 100.0,
                     "m": {k: r["m"].get(k) for k in keys}, "base": {k: b[k] for k in keys}})
    cols = [_col("坡度", [b["label"] for b in bins]), _col("跑步時間", [_hms(b["time_s"]) for b in bins]),
            _col("佔比", [f"{b['time_pct']:.0f}%" for b in bins])]
    cols += [_col(f"{FORM_LABEL[k]}（平常）", [_form_cell(b["m"][k], b["base"][k], k) for b in bins]) for k in keys]
    hint = cadence_hint(bins, [f"坡度 {b['label']}" for b in bins])
    return {**base, "series": cols + _form_note_rows(m) + _hint_rows(hint),
            "form_profile": {"mode": "grade", "bins": bins, "keys": keys, "min_s": BIN_MIN_S,
                             "pool_weeks": list(POOL_WEEKS), "stryd": bool(m.get("stryd")),
                             "note": None if m.get("stryd") else NO_STRYD_NOTE}}


def _form_work(ds, w, m, c, base):
    fb = m.get("form_bins") or {}
    work = fb.get("work") or {}
    if w.sport != "run" or not work.get("all"):
        return {**base, "empty": "參考：只有跑步、而且有跑步動態資料時才有這一張"}
    keys = _form_keys(work["all"], bool(m.get("stryd")))
    if not keys:
        return {**base, "empty": "這次沒有跑步動態資料（步頻、觸地時間…）"}
    pool = _pool(ds, w)
    bands = {}
    for band, rows in work.items():
        out = []
        for r in rows:
            past = []
            for age, pm in pool:
                pr = ((pm.get("form_bins") or {}).get("work") or {}).get(band) or []
                past.append((age, pr[r["k"]] if r["k"] < len(pr) else None))
            b = _form_base(past)
            out.append({"k": r["k"], "label": f"{r['k'] * 10}–{r['k'] * 10 + 10}%", "time_s": r["time_s"],
                        "m": {k: r["m"].get(k) for k in keys}, "base": {k: b[k] for k in keys}})
        bands[band] = out
    split = fb.get("split") or "time"
    cols = [_col("坡度", [BAND_LABEL[b] for b, rows in bands.items() for _ in rows]),
            _col("作功段" if split == "work" else "移動時間段", [r["label"] for rows in bands.values() for r in rows]),
            _col("跑步時間", [_hms(r["time_s"]) for rows in bands.values() for r in rows])]
    cols += [_col(f"{FORM_LABEL[k]}（平常）", [_form_cell(r["m"][k], r["base"][k], k)
                                              for rows in bands.values() for r in rows]) for k in keys]
    word = "作功" if split == "work" else "時間"
    hint = cadence_hint(bands["all"], [f"{word} {r['label']}" for r in bands["all"]])
    return {**base, "series": cols + _form_note_rows(m) + _hint_rows(hint),
            "form_profile": {"mode": "work", "split": split, "bands": bands,
                             "band_labels": {b: BAND_LABEL[b] for b in bands}, "keys": keys, "min_s": BIN_MIN_S,
                             "pool_weeks": list(POOL_WEEKS), "stryd": bool(m.get("stryd")),
                             "note": None if m.get("stryd") else NO_STRYD_NOTE}}


CW_SUGGEST = (1.05, 1.10)     # Heiderscheit 2011: +5 % / +10 % step rate (at the same speed)


def _cad_pool(ds, w) -> tuple[list[list[list]], int]:
    """(past runs' cad_windows, weeks): every running category (the within-run slope is about the
    runner, not the terrain) over the last 26 weeks. Not 8 → 12 → 26 like the usual bands: a slope
    needs more data than a median — on the user's runs 8-week slopes swing between −0.4 and −0.9 BW/s
    per +5 spm with CIs near 0 (docs/research/impact-cadence.md §3.3); 26 weeks is 推估."""
    items = []
    lo = math.floor(w.day) - 7 * POOL_WEEKS[-1]
    for p in ds.workouts:
        if p.idx == w.idx or p.day >= w.day or math.floor(p.day) < lo or p.sport != "run":
            continue
        pm = measure(ds, p)
        cw = (pm or {}).get("cad_windows") or []
        if len(cw) >= CW_MIN_WIN:
            items.append((w.day - p.day, cw))
    _flush(ds)
    return [cw for _, cw in items], POOL_WEEKS[-1]


def _form_cadence(ds, w, m, c, base):
    if w.sport != "run":
        return {**base, "empty": "參考：只有跑步才有這一張"}
    if not m.get("stryd"):
        return {**base, "empty": "這次沒有 Stryd：ILR（衝擊負荷率）是 Stryd 腳掌感測器算的，手錶沒有，所以不畫步頻與衝擊"}
    cw = m.get("cad_windows") or []
    if len(cw) < CW_MIN_WIN:
        return {**base, "empty": f"這次穩定跑的 30 秒段只有 {len(cw)} 段（< {CW_MIN_WIN}），看不出步頻和衝擊的關係"}
    pool, weeks = _cad_pool(ds, w)
    fit = cadence_fit(pool)
    a = np.array([r[:4] for r in cw], dtype=float)
    med_cad, v_ref, g_ref = (float(np.median(a[:, i])) for i in (1, 2, 3))
    if fit is None:
        return {**base, "series": [_row("步頻與衝擊", f"過去的穩定跑不夠（{len(pool)} 次，要 ≥ {CW_MIN_RUNS} 次、"
                                                f"每次 ≥ {CW_MIN_WIN} 段），還算不出你自己的趨勢")],
                "cadence_profile": {"weak": True, "points": []}}
    lo_ci, hi_ci = (fit["b_cad"] - 1.96 * fit["se_cad"]) * 5, (fit["b_cad"] + 1.96 * fit["se_cad"]) * 5
    rel = (f"同速度、同坡度下，步頻每 +5 spm，ILR {fit['b_cad'] * 5:+.1f} BW/s（95% CI {lo_ci:+.1f}～{hi_ci:+.1f}；"
           f"{fit['n_runs']} 次跑步、{fit['n_windows']} 段 30 秒，近 {weeks} 週；推估）")
    if hi_ci >= 0 or abs(fit["partial_r"]) < 0.1:
        # weak or not clearly negative: say so, no trend chart (it would mislead)
        why = "95% CI 碰到 0" if hi_ci >= 0 else f"偏相關只有 {fit['partial_r']:+.2f}（|r| < 0.1）"
        return {**base, "series": [_row("步頻與衝擊", rel), _row("判讀（參考）", f"關係不明顯（{why}）：在你的資料裡，同速度下"
                                                                      "步頻高低和衝擊沒有清楚的關係，不畫趨勢線")],
                "cadence_profile": {"weak": True, "points": [], "fit": fit, "weeks": weeks}}
    adj = a[:, 0] - fit["b_speed"] * (a[:, 2] - v_ref) - fit["b_grade"] * (a[:, 3] - g_ref)
    pts = [[round(float(x), 1), round(float(y), 2), r[0], r[2], r[3]] for x, y, r in zip(a[:, 1], adj, cw)]
    cx, cy = float(a[:, 1].mean()), float(adj.mean())
    sug = [med_cad * CW_SUGGEST[0], med_cad * CW_SUGGEST[1]]
    pred5 = fit["b_cad"] * med_cad * 0.05
    sd = float(np.std(a[:, 1]))
    rows = [_row("步頻與衝擊", rel),
            _row("這次", f"中位步頻 {med_cad:.0f} spm；+5–10% = {sug[0]:.0f}–{sug[1]:.0f} spm（Heiderscheit 2011）"),
            _row("預估（推估）", f"照你的趨勢，步頻 +5% 約讓 ILR {pred5:+.1f} BW/s（{pred5 / cy * 100:+.0f}%）；你這次的步頻"
                              f"只在 ±{sd:.0f} spm 內變化，+5% 是外推")]
    return {**base, "series": rows,
            "cadence_profile": {"points": pts, "fit": fit, "weeks": weeks, "center": [cx, cy],
                                "speed_ref": v_ref, "grade_ref": g_ref, "median_cad": med_cad, "suggest": sug}}


def _apply_action(ev: dict) -> dict:
    """The review card's 「套用這次的 CP」 button (viewer draw(): res.action)."""
    a = ev["apply"]
    return {"kind": "apply_cp", "label": a["label"], "method": "POST", "url": "/api/v1/plan/thresholds/apply-cp",
            "body": {k: a[k] for k in ("date", "cp", "wprime", "cp_method", "activity_index", "note")},
            "done": f"已套用：{a['date']} CP {a['cp']} W", "confirm":
            f"把 {a['date']} 的 CP 設成 {a['cp']} W（{ev['method_label']}，品質 {ev['quality']}）？區間和 TSS 會重新計算"}


def _cp(ds, w, m, c, base):
    if c["type"] != "test_cp":
        return {**base, "empty": "這次不是 CP 測試（課表偏好的 CP 測試方式：20 分全力／12 分＋3 分／5–10 K 比賽）"}
    ev = cp_eval(ds, w, m, c)
    if not ev:
        return {**base, "empty": "沒有功率，算不出 CP" if m.get("avg_power") is None
                else "找不到這個流程的全力段，算不出 CP"}
    how = MATCH_LABEL.get(c.get("test_match"))
    rows = [_row("流程", ev["protocol_label"] + (f"（依{how}）" if how else ""))]
    for b in ev.get("bouts") or []:
        hr = f"，最高心率 {b['hr_peak']:.0f}" if b.get("hr_peak") is not None else ""
        rows.append(_row(f"{b['duration_s'] / 60:.0f} 分段", f"{b['power']:.0f} W（{_hms(b['start_s'])} 開始{hr}）"))
    lo, hi = ev["cp_range"]
    rows.append(_row("CP", f"{ev['cp']:.0f} W" + (f"（{lo:.0f}–{hi:.0f}）" if hi - lo >= 1 else "")
                     + f" · {ev['method_label']}"))
    rows.append(_row("W′", f"{ev['wprime'] / 1000:.1f} kJ" if ev.get("wprime") is not None
                     else "沒量到" + (f"（CP 用先驗 {ev['wprime_prior'] / 1000:.1f} kJ 算）"
                                      if ev.get("wprime_prior") and ev["method"] == "1pt_prior" else "")))
    rows.append(_row("品質", ev["quality"]))
    for k in ev.get("checks") or []:
        rows.append(_row("檢查", ("✓ " if k["ok"] else "✗ ") + k["text"]))
    out = {**base, "series": rows + _verdict_rows(cp_lines(ev))}
    if ev.get("apply"):
        out["action"] = _apply_action(ev)
    return out


# ---------------------------------------------------------------------------
# 間歇判讀 (engine/interval_eval.py; interval-prescription.md Part B) — the 間歇
# dashboard is shown for every run (interval_eval.card): a plan / detected interval
# session or a CP / AeT test fills every card; otherwise the verdict card says why
# (不是間歇課 + 「當作間歇判讀」, or no power) and the battery still shows W′; the
# per-rep cards (reps, power, TIZ, HR) only drop out when there are no reps to show.
# ---------------------------------------------------------------------------

GOOD, BAD, WARN, SERIES1, SERIES2, MUTED = "#0ca30c", "#d03b3b", "#c98a00", "#2a78d6", "#eb6834", "#898781"


def _ie(ds, w, base):
    """(evaluation, None) or (None, the card to return instead). Only the
    verdict card shows the not-an-interval / no-power text; the per-rep cards
    drop out then, so the dashboard reads as one short card."""
    from backend.engine import interval_eval as IE
    e = IE.card_cached(ds, w)
    if not e.get("ok"):
        return None, {**base, "hide": True}
    return e, None


def _offer_action(w) -> dict:
    """「當作間歇判讀」: adds the activity tag interval_eval.FLAG_TAG (PATCH
    /api/v1/wko5/activities, the 活動編輯 page's key-based edit), then the
    viewer reloads the dashboard (`reload`)."""
    from backend.engine import activity_tags as AT
    from backend.engine.interval_eval import FLAG_TAG
    return {"kind": "flag_interval", "label": "當作間歇判讀", "method": "PATCH", "url": "/api/v1/wko5/activities",
            "body": {"items": [{"key": AT.key_of(w.entry.start), "file": getattr(w.entry, "file", None)}],
                     "add_tags": [FLAG_TAG]},
            "done": f"已標記「{FLAG_TAG}」，重新判讀中…（到活動編輯頁移除這個標籤可以取消）", "reload": True}


def _thin(xs: list, ys: list, n: int = 1800) -> list:
    k = max(1, len(xs) // n)
    return [[round(float(x), 1), None if y is None or not math.isfinite(y) else round(float(y), 4)]
            for x, y in zip(xs[::k], ys[::k])]


def _iv_verdict(ds, w, m, c, base):
    from backend.engine import interval_eval as IE
    e = IE.card_cached(ds, w)
    if not e.get("ok"):
        if e.get("state") != "offer":
            return {**base, "empty": e.get("why") or "無法判讀"}
        n = e.get("n_bouts") or 0
        rows = [_row("間歇", e["why"])]
        if e.get("flagged"):
            return {**base, "series": rows}
        rows.append(_row("偵測", f"偵測到 {n} 趟用力段，可以當作間歇判讀" if n else "沒有偵測到用力段，當作間歇也沒有趟可判讀",
                         "用力段 = ≥ 95% CP、≥ 40 秒的短趟（至少 3 趟），否則 3 區以上（≥ 0.95 × 88% CP）≥ 2.5 分的段"))
        out = {**base, "badge": {"text": "這次不是間歇課", "level": "", "sub": e.get("type_label") or ""}, "series": rows}
        if n:
            out["action"] = _offer_action(w)
        return out
    if e.get("kind") == "test":
        rows = [_row("判定", e["verdict_label"], "測試照流程判讀：每一段是不是平均分配（不是對照目標帶的「達標」）"),
                _row("流程", e["label"])]
        for i, r in enumerate(e["reasons"]):
            rows.append(_row("各段" if i == 0 else "", r))
        rows.append(_row("W′ 用掉", f"{e['wprime_used_j'] / 1000:.1f} kJ（{e['wprime_used_j'] / e['wprime_j'] * 100:.0f}% W′）；"
                         f"dFRC 最低 {e['dfrc_min_pct'] * 100:.0f}%", e["wprime_src"] + "；dFRC = WKO5 的 dfrc 模型，跑步沒驗證"))
        return {**base, "badge": {"text": e["verdict_label"], "level": e["level"], "sub": e["label"]}, "series": rows}
    sub = f"{e['label']}" + ("" if e["planned"] else
                             "（你標了「當作間歇」：用偵測到的趟）" if e.get("flagged") else "（沒有對應的課表：用偵測到的趟）")
    rows = [_row("判定", e["verdict_label"], "對照「這次選的課表」本身的計畫；同等與否在選課時已決定"),
            _row("課表", sub)]
    for i, r in enumerate(e["reasons"]):
        rows.append(_row("理由" if i == 0 else "", r))
    rows.append(_row("W′ 用掉", f"{e['wprime_used_j'] / 1000:.1f} kJ（{e['wprime_used_j'] / e['wprime_j'] * 100:.0f}% W′）；"
                     f"dFRC 最低 {e['dfrc_min_pct'] * 100:.0f}%", e["wprime_src"] + "；dFRC = WKO5 的 dfrc 模型，跑步沒驗證"))
    src = {"lap": "COROS 推送的分段（lap）", "power": "功率型態（0.95 × 目標下限）", "short": "短趟偵測（≥ 95% CP）",
           "z3": "3 區偵測（≥ 0.95 × 88% CP）"}.get(e["rep_source"], "—")
    rows.append(_row("找趟", src))
    return {**base, "badge": {"text": e["verdict_label"], "level": e["level"], "sub": sub}, "series": rows}


def _iv_reps(ds, w, m, c, base):
    e, bad = _ie(ds, w, base)
    if bad:
        return bad
    from backend.engine.wko5expr import units as U
    wu = U.meta("WATTS")
    xu = {"id": "REP", "label": "趟", "kind": "number", "dec": [[0, 0]]}
    cp = e["cp"]
    if e.get("kind") == "test":
        return _iv_reps_test(e, base, wu, xu)
    ok = [[r["k"], round(r["power"])] for r in e["reps"] if r["in_band"]]
    no = [[r["k"], round(r["power"])] for r in e["reps"] if not r["in_band"]]
    lab = lambda rs, t: [f"{t} {round(r['power'])} W" for r in rs]
    s = [{"name": "目標帶", "type": "line", "expression": "", "y_axis": "WATTS", "unit": wu, "color": SERIES1,
          "data": {"kind": "band", "range": [round(e["lo"] * cp), round(e["hi"] * cp)]}},
         {"name": "✓ 達標", "type": "bar", "expression": "", "y_axis": "WATTS", "unit": wu, "x_unit": xu, "color": GOOD,
          "bar_width": 28, "labels": lab([r for r in e["reps"] if r["in_band"]], "✓"),
          "data": {"kind": "points", "x": "value", "points": ok}},
         {"name": "✕ 沒到", "type": "bar", "expression": "", "y_axis": "WATTS", "unit": wu, "x_unit": xu, "color": BAD,
          "bar_width": 28, "labels": lab([r for r in e["reps"] if not r["in_band"]], "✕"),
          "data": {"kind": "points", "x": "value", "points": no}}]
    desc = (f"目標帶 {e['lo'] * 100:.0f}–{e['hi'] * 100:.0f}% CP（{e['lo'] * cp:.0f}–{e['hi'] * cp:.0f} W）；"
            f"達標 = 平均 ≥ {e['floor']:.0f} W（下限 × 0.98）。{e['hit']}/{e['n_plan']} 趟達標"
            + (f"，掉速 {e['fade'] * 100:+.0f}%" if e.get("fade") is not None else ""))
    return {**base, "axes": [{"id": "WATTS", "unit": wu, "min": 0}], "series": s, "description": desc}


def _iv_reps_test(e, base, wu, xu):
    """A test's bouts: bar = the bout's mean (green = even, amber = not), the
    marker = all-out by the CP model (CP test only, 推估). No target band."""
    cp = e["cp"]
    ev = [r for r in e["reps"] if r["even"]]
    un = [r for r in e["reps"] if not r["even"]]
    lab = lambda rs, t: [f"{t} {r['name']} {round(r['power'])} W" for r in rs]
    s = [{"name": "✓ 配速平均", "type": "bar", "expression": "", "y_axis": "WATTS", "unit": wu, "x_unit": xu, "color": GOOD,
          "bar_width": 28, "labels": lab(ev, "✓"), "data": {"kind": "points", "x": "value",
                                                           "points": [[r["k"], round(r["power"])] for r in ev]}},
         {"name": "◐ 不平均", "type": "bar", "expression": "", "y_axis": "WATTS", "unit": wu, "x_unit": xu, "color": WARN,
          "bar_width": 28, "labels": lab(un, "◐"), "data": {"kind": "points", "x": "value",
                                                           "points": [[r["k"], round(r["power"])] for r in un]}},
         {"name": "CP", "type": "line", "expression": "", "y_axis": "WATTS", "unit": wu, "color": MUTED,
          "line_style": "dash", "data": {"kind": "hline", "y": round(cp)}}]
    exp = [[r["k"], round(r["expected"])] for r in e["reps"] if r.get("expected")]
    if exp:
        s.append({"name": "預期全力（CP + W′/t，推估）", "type": "line", "line_style": "none", "expression": "",
                  "y_axis": "WATTS", "unit": wu, "x_unit": xu, "color": SERIES1,
                  "labels": [f"預期 {p[1]} W" for p in exp], "data": {"kind": "points", "x": "value", "points": exp}})
    desc = (f"{e['label']}：每段一根柱子（綠＝前後半配速平均，黃＝不平均）。"
            + ("點＝這段長度的預期全力功率，用測試前的 CP 和 W′ 依 CP 模型 P = CP + W′/t 算"
               f"（Monod & Scherrer 1965；{e['wprime_src']}）。" if exp else "")
            + "平均＝後半和前半差 ±" + ("5" if e.get("intent") == "max" else "3")
            + "% 內、最後 1 分 ≤ 該段 × 1.08（推估）。測試不用目標帶判「達標」。")
    return {**base, "axes": [{"id": "WATTS", "unit": wu, "min": 0}], "series": s, "description": desc}


def _iv_power(ds, w, m, c, base):
    """A simplified 「Run VO2max & FRC Chart – 5 Min PDC & Skiba」: power with the reps,
    CP and the target band only; the 5-min PDC and W′ in the hover text."""
    e, bad = _ie(ds, w, base)
    if bad:
        return bad
    from backend.engine.wko5expr import units as U
    wu = U.meta("WATTS")
    ser = e["series"]
    p = np.asarray(ser["power"], float)
    p30 = np.convolve(np.nan_to_num(p), np.ones(30) / 30, "same") if len(p) else p
    cp = e["cp"]
    test = e.get("kind") == "test"
    s = [{"name": "功率（30 秒）", "type": "line", "expression": "", "y_axis": "WATTS", "unit": wu, "color": SERIES1,
          "line_width": "thin", "data": {"kind": "points", "x": "seconds", "points": _thin(ser["t"], list(p30))}}]
    if not test:
        s.append({"name": "目標帶", "type": "line", "expression": "", "y_axis": "WATTS", "unit": wu, "color": SERIES1,
                  "data": {"kind": "band", "range": [round(e["lo"] * cp), round(e["hi"] * cp)]}})
    s.append({"name": "CP", "type": "line", "expression": "", "y_axis": "WATTS", "unit": wu, "color": MUTED,
              "line_style": "dash", "data": {"kind": "hline", "y": round(cp)}})
    for r in e["reps"]:
        if test:
            nm, col = ("✓ 平均的段（開始）", GOOD) if r["even"] else ("◐ 不平均的段（開始）", WARN)
        else:
            nm, col = ("✓ 達標的趟（開始）", GOOD) if r["in_band"] else ("✕ 沒到的趟（開始）", BAD)
        s.append({"name": nm, "type": "line", "expression": "", "y_axis": "WATTS", "unit": wu,
                  "color": col, "line_style": "dot", "data": {"kind": "vline", "x": r["start_s"]}})
    pdc = e.get("pdc5")
    desc = (f"CP {cp:.0f} W" + ("；測試沒有目標帶。" if test else f"；目標帶 {e['lo'] * 100:.0f}–{e['hi'] * 100:.0f}% CP。")
            + (f"近 90 天最佳 5 分鐘 {pdc:.0f} W（{pdc / cp * 100:.0f}% CP，WKO5 的 5 Min PDC 參考）。" if pdc else "")
            + ("虛線 = 每段開始（綠＝配速平均、黃＝不平均）。" if test else "虛線 = 每趟開始（綠＝達標、紅＝沒到）。")
            + "W′ 剩多少見「功率電池」。")
    return {**base, "axes": [{"id": "WATTS", "unit": wu, "min": 0}], "series": s, "description": desc}


def _battery_of(ds, w):
    """The card's battery numbers: the evaluation's (rep-aware τ), else the
    not-an-interval card's (every run with power). None without power / CP."""
    from backend.engine import interval_eval as IE
    e = IE.card_cached(ds, w)
    return e if e.get("series") and e["series"].get("t") and e.get("dfrc_min_pct") is not None else None


def _iv_battery(ds, w, m, c, base, compact: bool = False):
    """「dFRC Run」as a battery: the W′ left over time (WKO5 dFRC), Skiba for
    comparison — every run with power (long and trail runs too). `compact`
    (本次重點): dFRC only, with a one-line subtitle."""
    e = _battery_of(ds, w)
    if e is None:
        return {**base, "hide": True}
    from backend.engine.wko5expr import units as U
    pct = U.meta("PERCENT")
    ser = e["series"]
    lo_t, lo_v = e["dfrc_min_t"], e["dfrc_min_pct"]
    s = [{"name": "dFRC（WKO5）", "type": "line", "expression": "", "y_axis": "PERCENT", "unit": pct, "color": SERIES1,
          "data": {"kind": "points", "x": "seconds", "points": _thin(ser["t"], ser["dfrc_pct"])}},
         {"name": f"Skiba W′bal（τ {e['tau']:.0f} 秒）", "type": "line", "expression": "", "y_axis": "PERCENT",
          "unit": pct, "color": SERIES2, "line_width": "thin",
          "data": {"kind": "points", "x": "seconds", "points": _thin(ser["t"], ser["skiba_pct"])}},
         {"name": "最低", "type": "line", "expression": "", "y_axis": "PERCENT", "unit": pct, "color": SERIES1,
          "labels": [f"最低 {lo_v * 100:.0f}%"], "data": {"kind": "points", "x": "seconds",
                                                          "points": [[round(lo_t, 1), round(lo_v, 4)]]}}]
    desc = (f"電池 = 還剩多少 W′（{e['wprime_j'] / 1000:.1f} kJ = 100%）。高於 CP（{e['cp']:.0f} W）就耗電，"
            f"休息或低於 CP 時回充。dFRC 是 WKO5 的模型（30% 25 秒＋70% 300 秒回充），Skiba 用跑步的 τ（Vassallo 2020）；"
            f"兩者都只是推估，跑步沒驗證。{e['wprime_src']}")
    sub = (f"整趟高於 CP 的功 {e['wprime_used_j'] / 1000:.1f} kJ（{e['wprime_used_j'] / e['wprime_j'] * 100:.0f}% W′，"
           f"含回充後再用），電池最低 {lo_v * 100:.0f}%（{_hms(lo_t)}）")
    if compact:
        s = [x for x in s if not x["name"].startswith("Skiba")]
        desc = "W′ 電池：dFRC（WKO5）剩多少，100% = 滿。Skiba 對照和逐趟數字在「間歇」分頁。" + desc
    return {**base, "axes": [{"id": "PERCENT", "unit": pct, "max": 1.0}], "series": s, "description": desc,
            "subtitle": sub}


def _wprime_battery(ds, w, m, c, base):
    """本次重點's small W′ card (the same numbers as 間歇 → 功率電池)."""
    return _iv_battery(ds, w, m, c, base, compact=True)


def _iv_tiz(ds, w, m, c, base):
    e, bad = _ie(ds, w, base)
    if bad:
        return bad
    if e.get("kind") == "test":
        return {**base, "hide": True}           # a test has no target zone
    from backend.engine.wko5expr import units as U
    du = U.meta("HHMMSS")
    xu = {"id": "BAR", "label": "", "kind": "number", "dec": [[0, 0]]}
    plan, act = e["tiz_plan_s"], e["tiz_s"] or 0.0
    r = e.get("tiz_ratio")
    s = [{"name": "計畫（這份課表）", "type": "bar", "expression": "", "y_axis": "HHMMSS", "unit": du, "x_unit": xu,
          "color": MUTED, "bar_width": 36, "labels": [f"計畫 {plan / 60:.0f} 分"],
          "data": {"kind": "points", "x": "value", "points": [[1, plan]]}},
         {"name": "實際", "type": "bar", "expression": "", "y_axis": "HHMMSS", "unit": du, "x_unit": xu,
          "color": GOOD if (r or 0) >= 0.85 else BAD, "bar_width": 36,
          "labels": [f"實際 {act / 60:.1f} 分（{(r or 0) * 100:.0f}%）"],
          "data": {"kind": "points", "x": "value", "points": [[2, act]]}}]
    zone = f"≥ {e['lo'] * 100:.0f}% CP" if e["z5"] else f"{e['lo'] * 100:.0f}–{e['hi'] * 105:.0f}% CP"
    return {**base, "axes": [{"id": "HHMMSS", "unit": du, "min": 0}], "series": s,
            "description": f"目標區 = 10 秒功率 {zone}、連續 ≥ 30 秒才算（推估）。≥ 85% 算達到（推估，§C2 的 ±15%）。"}


def _iv_hr(ds, w, m, c, base):
    e, bad = _ie(ds, w, base)
    if bad:
        return bad
    if e.get("kind") == "test":
        return {**base, "hide": True}           # tests aren't compared with interval sessions
    from backend.engine.wko5expr import units as U
    bu = U.meta("BPM")
    ps = e.get("peers") or []
    if len(ps) < 2:
        return {**base, "empty": "還沒有功率相近（±3%）的同類間歇可以比"}
    prev = [[p["date"], round(p["hr_end"], 1)] for p in ps if not p["current"]]
    cur = [[p["date"], round(p["hr_end"], 1)] for p in ps if p["current"]]
    med = float(np.median([p[1] for p in prev])) if prev else None
    s = [{"name": "之前的同類間歇", "type": "line", "line_style": "none", "expression": "", "y_axis": "BPM", "unit": bu,
          "color": MUTED, "data": {"kind": "points", "x": "datetime", "points": prev}},
         {"name": "這次", "type": "line", "expression": "", "y_axis": "BPM", "unit": bu, "color": SERIES1,
          "labels": [f"這次 {cur[0][1]:.0f}"] if cur else None,
          "data": {"kind": "points", "x": "datetime", "points": cur}}]
    if med:
        s.append({"name": "之前的中位 ±3%", "type": "line", "expression": "", "y_axis": "BPM", "unit": bu, "color": MUTED,
                  "data": {"kind": "band", "range": [round(med * 0.97, 1), round(med * 1.03, 1)]}})
    return {**base, "axes": [{"id": "BPM", "unit": bu}], "series": s,
            "description": "每趟後半段的平均心率，只比平均功率相差 ±3% 以內的同類課（Buchheit 2014：運動中心率雜訊約 3%，"
                           "同功率心率越低越好；超出灰帶才算有變化）。"}


_SECTIONS = {"summary": _summary, "aerobic": _aerobic, "intervals": _intervals, "climbs": _climbs,
             "interval_verdict": _iv_verdict, "interval_reps": _iv_reps, "interval_power": _iv_power,
             "interval_battery": _iv_battery, "interval_tiz": _iv_tiz, "interval_hr": _iv_hr,
             "wprime_battery": _wprime_battery,
             "durability": _durability, "form": _form, "grades": _grades, "pacing": _pacing,
             "durability_curve": _durability_curve, "cp_test": _cp,
             "form_grades": _form_grades, "form_work": _form_work, "form_cadence": _form_cadence}
