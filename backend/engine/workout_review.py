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
    excluded. Refuses runs where the number means nothing: hilly (≥ 20 m
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
CACHE_KEY = "workout_review_v9"

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
DRIFT_FINISH_SHARE = 0.10     # 自組 (doc §6.2 / §7): the last 10 % of the measured time …
DRIFT_FAST_FINISH = 0.05      # … > 5 % above the rest (power or pace) = a fast finish, refused
DRIFT_HEAT_C = 25.0           # 徐國峰 < 25 °C (Lafrenz 2008: HR +11 % at 35 °C vs +2 % at 22 °C); in drift_of 自組
DRIFT_POWER_COVER = 0.95      # 自組: Pw:HR only when power covers ≥ 95 % of the Pa:HR window (same samples)
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
EXTRA_SECTIONS = ("grades", "pacing", "durability_curve", "cp_test")
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


def drift_of(t, hr, speed, power=None, cp: Optional[float] = None,
             climb_m_per_km: Optional[float] = None, trail: bool = False,
             temp_c: Optional[float] = None, temp_src: Optional[str] = None) -> dict:
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
    basis_drift(…, ref=True)."""
    out = {"drift": None, "ok": False, "ref_ok": False, "tier": None, "reason": "", "hr1": None, "hr2": None,
           "v1": None, "v2": None, "pw_drift": None, "pw_ok": False, "pw_ref_ok": False, "pw_reason": "",
           "p1": None, "p2": None, "pw_hr1": None, "pw_hr2": None, "measured_s": None,
           "finish": None, "temp_c": None, "temp_src": None}
    if hr is None or speed is None or not _has(hr) or not _has(speed):
        out["reason"] = "沒有心率或速度"
        return out
    t = np.asarray(t, dtype=float)
    n = len(t)
    h, s = _arr(hr, n), _arr(speed, n)
    d = _dt(t)
    t0 = t[np.isfinite(t)][0]
    elapsed = float(np.nanmax(t) - t0)
    if elapsed < WARMUP_S + DRIFT_REF_MIN_S:        # can't reach 30 min after the warm-up
        out["reason"] = _too_short_reason(elapsed - WARMUP_S)
        return out
    if trail or (climb_m_per_km is not None and climb_m_per_km >= TRAIL_CLIMB_RATE_M_PER_KM):
        out["reason"] = "有坡（越野或每公里爬升 ≥ 20 m），飄移數字不採用"
        return out
    after = (t - t0) >= WARMUP_S
    mov = moving_mask(t, s)
    span = float(d[after].sum())
    stopped = float(d[after & ~mov].sum())       # standing still + recording gaps
    if span > 0 and stopped / span > MAX_STOPPED_SHARE:
        out["reason"] = f"中途停了 {_hms(stopped)}（> 5%），飄移數字不採用"
        return out
    has_power = power is not None and _has(power)
    if has_power:
        _, p1 = _grid1(t, power)
        if p1 is not None:
            p = p1[WARMUP_S:]
            p = p[np.isfinite(p)]
            if len(p) > 60:
                p30 = np.convolve(p, np.ones(30) / 30, "valid")
                if p30.mean() > 0 and p30.std() / p30.mean() > AET_MAX_POWER_CV:
                    out["reason"] = f"功率起伏大（變異 {p30.std() / p30.mean() * 100:.0f}% > 15%），不是穩定跑，飄移不採用"
                    return out
                if cp and p.mean() > AET_MAX_OF_CP * cp:
                    out["reason"] = f"強度 {p.mean() / cp * 100:.0f}% CP（> 90%），不是有氧跑，飄移不採用"
                    return out
    # the measured window: moving, after the warm-up, HR and speed valid; with
    # power (≥ 95 % coverage) also power valid, so Pa:HR and Pw:HR share it
    m0 = after & mov & np.isfinite(h) & (h > 0) & np.isfinite(s) & (s > 0)
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


def form_drift(t, speed, chans: dict, cadence=None) -> dict:
    """{name: {first, last, change}} over the first ⅓ vs the last ⅓ of the
    moving time. With `cadence` (strides/min) only running steps count —
    walking a steep climb would otherwise read as a collapse in stiffness."""
    t = np.asarray(t, dtype=float)
    n = len(t)
    d = _dt(t)
    mov = moving_mask(t, speed)
    if cadence is not None and _has(cadence):
        c = _arr(cadence, n)
        mov &= np.isfinite(c) & (c >= RUN_CADENCE)
    cum = np.cumsum(np.where(mov, d, 0.0))
    if not len(cum) or cum[-1] <= 0:
        return {}
    a, b = mov & (cum <= cum[-1] / 3), mov & (cum > cum[-1] * 2 / 3)
    out = {}
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
        out["drift"] = drift_of(t, s["hr"], s["speed"], s["power"], cp, cpm, trail=cat == "trail")
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
            climbs = [{"t_start": c.t_start, "duration_s": c.duration_s, "distance_km": c.distance_km,
                       "gain_m": c.gain_m, "grade": c.grade, "vam": c.vam_m_per_h, "avg_hr": c.avg_hr,
                       "hr_per_100m": c.hr_per_100m} for c in cl]
        except Exception:
            climbs = []
    out["climbs"] = climbs
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
    out["form"] = form_drift(t, s["speed"], fchans, s["cadence"]) if w.sport == "run" else {}
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
               "title": "標題", "pattern": "功率型態", "steady": "≥ 55 分鐘穩定跑"}
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
                    "reason": dr.get("reason")})
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
    if d is not None and power:
        rows += [_row("Pw:HR 飄移", f"{_pct(d)}（前 10 分鐘不算）{tag}", REF_TIP if ref else None),
                 _row("前半／後半心率", f"{dr['pw_hr1']:.0f} → {dr['pw_hr2']:.0f} bpm"),
                 _row("前半／後半功率", f"{dr['p1']:.0f} → {dr['p2']:.0f} W")]
    elif d is not None:
        rows += [_row("Pa:HR 飄移", f"{_pct(d)}（前 10 分鐘不算）{tag}", REF_TIP if ref else None),
                 _row("前半／後半心率", f"{dr['hr1']:.0f} → {dr['hr2']:.0f} bpm"),
                 _row("前半／後半速度", f"{dr['v1']:.2f} → {dr['v2']:.2f} km/h")]
    if ref:
        rows.append(_row("飄移等級", REF_LABEL, REF_TIP))
    elif d is not None:
        rows.append(_row("飄移等級", "嚴格（暖身後 ≥ 40 分，UA 測試標準）"))
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


def _climbs(ds, w, m, c, base):
    cl = m.get("climbs") or []
    if not cl:
        return {**base, "empty": "這次沒有 ≥ 80 m、坡度 ≥ 3% 的連續爬坡"}
    cols = [_col("開始", [_hms(x["t_start"]) for x in cl]),
            _col("爬升 m", [_num(x["gain_m"]) for x in cl]),
            _col("距離 km", [_num(x["distance_km"], 2) for x in cl]),
            _col("坡度", [_pct(x["grade"], 0) for x in cl]),
            _col("VAM m/h", [_num(x["vam"]) for x in cl]),
            _col("平均心率", [_num(x.get("avg_hr")) for x in cl]),
            _col("每 100 m 心跳", [_num(x.get("hr_per_100m")) for x in cl])]
    return {**base, "series": cols + _verdict_rows(_trail_lines(ds, w, m) or ["（沒有心率，無法比較爬坡經濟性）"])}


def _grades(ds, w, m, c, base):
    s = _samples(ds, w)
    g = _eval(ds, w, "rgrade")
    if s is None or g is None or s["dist"] is None:
        return {**base, "empty": "沒有距離或海拔，算不出坡度"}
    rows = grade_bins(_dt(s["t"]), g, s["dist"], s["power"], s["hr"], s["cadence"])
    if not rows:
        return {**base, "empty": "沒有坡度資料"}
    cols = [_col("坡度", [r["label"] for r in rows]),
            _col("時間", [_hms(r["time_s"]) for r in rows]),
            _col("佔比", [f"{r['time_pct']:.0f}%" for r in rows]),
            _col("距離 km", [_num(r["distance_km"], 2) for r in rows]),
            _col("配速 /km", [_pace(r["pace_s_per_km"]) for r in rows]),
            _col("心率", [_num(r["hr"]) for r in rows]),
            _col("功率 W", [_num(r["power"]) for r in rows])]
    return {**base, "series": cols}


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
    s = [{"name": "耐久（輸出／心率）", "type": "line", "expression": "", "y_axis": "PERCENT", "unit": pct,
          "x_unit": xu, "color": "#2a78d6", "data": {"kind": "points", "x": "value", "points": pts}},
         {"name": "90%", "type": "line", "expression": "", "y_axis": "PERCENT", "unit": pct, "x_unit": xu,
          "color": "#8a8984", "line_style": "dash", "data": {"kind": "hline", "y": 0.9}}]
    return {**base, "axes": [{"id": "PERCENT", "unit": pct}], "series": s}


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
    cols = [_col("指標（參考）", names), _col("前 ⅓", first), _col("後 ⅓", last),
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


_SECTIONS = {"summary": _summary, "aerobic": _aerobic, "intervals": _intervals, "climbs": _climbs,
             "durability": _durability, "form": _form, "grades": _grades, "pacing": _pacing,
             "durability_curve": _durability_curve, "cp_test": _cp}
