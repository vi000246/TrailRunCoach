"""
LTHR and AeT estimated from the athlete's own runs (see
backend/engine/algorithms/threshold_estimate.py for the methods).

Per-run numbers are memoised on disk per .wko4 (keyed by file stamp and the
thresholds in effect, via Dataset.cached_series), so a re-estimate only reads
new runs. The estimate is a suggestion: applying it is a separate, explicit
step on the season-plan page.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict
from typing import Optional

from backend.engine.algorithms.threshold_estimate import (
    DriftPoint, RunThreshold, estimate_aet, estimate_lthr, run_threshold, steady_drift,
)
from backend.engine.wko5expr.dataset import Dataset, date_to_day

WINDOWS = (90, 180)          # try 90 days, widen to 180 if too few runs
# 「HR at CP」 is a cross-check only — never the LTHR value, never written to the
# plan (docs/research/zones-and-thresholds.md §2.2, §3.3: the individual 95 %
# limits of agreement of HR at CP vs MLSS are −16…+17 bpm, Micheli et al. 2025)
HR_AT_CP_NOTE = "CP 附近的心率只當交叉檢查，不寫進 LTHR（個人誤差約 ±16 bpm，Micheli 2025）"
_KEY = "thresholds_v1"


_KEY_ASOF = "thresholds_asof_v1"   # per-run values computed with an explicit (as-of) CP


def _per_run(ds: Dataset, w, cp: Optional[float] = None, use_ds_cp: bool = True) -> Optional[dict]:
    t = ds.channel(w.idx, "elapsedtime")
    hr = ds.channel(w.idx, "heartrate")
    pw = ds.channel(w.idx, "power")
    if t is None or hr is None or pw is None:
        return None
    cp = ds.cp(w) if use_ds_cp else cp
    t, hr, pw = list(t), list(hr), list(pw)
    rt = run_threshold(t, hr, pw, cp)
    dp = steady_drift(t, hr, pw, cp) if "runningtrail" not in w.tags else None
    return {"lt": asdict(rt), "dp": None if dp is None else asdict(dp), "cp": cp}


def _nan_free(x):
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: _nan_free(v) for k, v in x.items()}
    return x


def estimate(ds: Dataset, today: Optional[dt.date] = None, cp_of=None) -> dict:
    """`cp_of(day) -> CP or None`: the CP in effect on a date, for a back-test
    that must not see later values (racepower.athlete.cp_as_of). Each run is
    then measured against the CP of its own date and the estimate against the
    CP of `today`. Default: Dataset.cp (the plan test on or before the run's
    date, else WKO5's current mFTP snapshot)."""
    today = today or dt.date.today()
    tday = int(math.floor(date_to_day(today)))
    runs = [w for w in ds.workouts if w.sport == "run" and tday - WINDOWS[-1] < math.floor(w.day) <= tday]
    data = {}
    for w in runs:
        if cp_of is None:
            v = ds.cached_series(_KEY, w, lambda w=w: _nan_free(_per_run(ds, w)))
        else:
            cp_w = cp_of(w.entry.start.date())
            mk = (_KEY_ASOF, w.idx, cp_w)          # in-memory: many as-of dates share a run's CP
            memo = getattr(ds, "memo", None)
            if isinstance(memo, dict) and mk in memo:
                v = memo[mk]
            else:
                v = ds.cached_series(_KEY_ASOF, w, lambda w=w, c=cp_w: _nan_free(_per_run(ds, w, c, False)))
                if v is not None and v.get("cp") != cp_w:  # cached with another CP: recompute, don't store
                    v = _nan_free(_per_run(ds, w, cp_w, False))
                if isinstance(memo, dict):
                    memo[mk] = v
        if v:
            data[w.idx] = v
    ds.flush_series()
    if cp_of is None:
        # the CP in effect on `today` (a plan test dated today counts even when
        # the last WKO5 run is older), else the last run's
        plan = getattr(ds, "plan", None)
        cp_now = plan.threshold_on("cp", today) if plan is not None else None
        if cp_now is None:
            cp_now = ds.cp(runs[-1]) if runs else None
    else:
        cp_now = cp_of(today)

    def pick(days):
        ws = [w for w in runs if math.floor(w.day) > tday - days and w.idx in data]
        lts = [RunThreshold(**data[w.idx]["lt"]) for w in ws]
        dps = [DriftPoint(**data[w.idx]["dp"]) for w in ws if data[w.idx]["dp"]]
        return ws, lts, dps

    out = {"cp": cp_now, "today": today.isoformat()}
    for days in WINDOWS:
        ws, lts, dps = pick(days)
        lt = estimate_lthr(lts, cp_now) if cp_now else None
        if lt and lt.value is not None or days == WINDOWS[-1]:
            out["lthr"] = None if lt is None else {**asdict(lt), "days": days, "hr_at_cp_note": HR_AT_CP_NOTE}
            break
    lthr_v = (out.get("lthr") or {}).get("value")
    for days in WINDOWS:
        ws, lts, dps = pick(days)
        ae = estimate_aet(dps, lthr=lthr_v)
        if ae.value is not None or days == WINDOWS[-1]:
            out["aethr"] = {**asdict(ae), "days": days,
                            "points": [[round(p.hr1, 1), round(p.drift, 4)] for p in dps]}
            break
    return out


# ---------------------------------------------------------------------------
# threshold pace (推估) — for a source without a threshold-pace setting
#
# 1. Pace at CP: threshold power and threshold pace are the same intensity
#    (Friel / Coggan define both as the ~1-hour sustainable effort; Stryd's CP
#    is its FTP equivalent). With Stryd power the athlete's own speed per
#    watt on road runs (moving samples, the running-effectiveness idea of
#    racepower/re.py, without the body weight) turns CP into a flat pace:
#    pace = 1000 / (CP · median speed-per-watt) / 60 min/km.
# 2. Otherwise pace at LTHR: Friel sets LTHR and threshold pace from the
#    same 30-minute solo time trial, the average HR / pace of its last 20
#    minutes (Friel, "Quick Guide to Setting Zones", TrainingPeaks).
#    Ordinary runs are not time trials, so this takes, per road run, the
#    fastest 20-minute stretch after a 10-minute warm-up whose mean HR is
#    within ±3 % of the LTHR in effect that day. In summer heat this reads
#    far too slow (cardiac drift: on this athlete's 2026-07…09 runs HR sat at
#    LTHR at ~8:00 /km, while the 2025-12 half marathon averaged 6:54 /km),
#    which is why the CP route comes first.
# Both are 推估; the ±3 % band, the warm-up, the minimum of 3 runs and the
# 90 → 180-day windows have no source. Trail and treadmill runs are left out
# (pace on a hill or a belt is not road pace).
# ---------------------------------------------------------------------------

TPACE_KEY = "tpace_at_lthr_v1"
TPACE_WINDOW_S = 1200
TPACE_WARMUP_S = 600
TPACE_HR_BAND = 0.03
TPACE_MIN_RUNS = 3


def _tpace_run(t, hr, dist_km, lthr: Optional[float]) -> Optional[dict]:
    """{"pace": min/km, "hr": bpm} of the fastest 20-min stretch at LTHR ±3 %."""
    import numpy as np
    if t is None or hr is None or dist_km is None or not lthr:
        return None
    t = np.asarray(t, float)
    h = np.asarray([np.nan if v is None else v for v in hr], float)
    d = np.asarray([np.nan if v is None else v for v in dist_km], float)
    ok = np.isfinite(t) & np.isfinite(h) & np.isfinite(d) & (h > 0)
    if ok.sum() < TPACE_WINDOW_S // 2 or t[ok][-1] - t[ok][0] < TPACE_WARMUP_S + TPACE_WINDOW_S:
        return None
    grid = np.arange(t[ok][0], t[ok][-1] + 1.0)
    h1, d1 = np.interp(grid, t[ok], h[ok]), np.interp(grid, t[ok], d[ok])
    n = TPACE_WINDOW_S
    c = np.concatenate([[0.0], np.cumsum(h1)])
    m = len(grid) - n                                   # windows [i, i + n], i < m
    mean_hr = (c[n:n + m] - c[:m]) / n
    km = d1[n:n + m] - d1[:m]
    mean_hr, km = mean_hr[TPACE_WARMUP_S:], km[TPACE_WARMUP_S:]
    sel = (np.abs(mean_hr / lthr - 1.0) <= TPACE_HR_BAND) & (km > 0.5)
    if not sel.any():
        return None
    i = int(np.argmax(np.where(sel, km, -1.0)))
    return {"pace": n / 60.0 / float(km[i]), "hr": float(mean_hr[i])}


SPW_KEY = "speed_per_watt_v1"
MOVING_KMH = 1.609344498          # WKO5's run moving threshold (wko5_time.MOVING_SPEED_KMH)


def _speed_per_watt(t, power, speed_kmh) -> Optional[float]:
    """Mean speed / mean power over the moving samples with power, m/s per W."""
    import numpy as np
    if t is None or power is None or speed_kmh is None:
        return None
    t = np.asarray(t, float)
    p = np.asarray([np.nan if v is None else v for v in power], float)
    s = np.asarray([np.nan if v is None else v for v in speed_kmh], float)
    n = min(len(t), len(p), len(s))
    t, p, s = t[:n], p[:n], s[:n]
    dt_ = np.diff(t, prepend=t[0] if n else 0.0)
    ok = np.isfinite(p) & np.isfinite(s) & (p > 0) & (s > MOVING_KMH) & (dt_ > 0) & (dt_ <= 5)
    if dt_[ok].sum() < 600:
        return None
    return float((s[ok] / 3.6 * dt_[ok]).sum() / (p[ok] * dt_[ok]).sum())


def _road_runs(ds: Dataset, tday: int) -> list:
    return [w for w in ds.workouts if w.sport == "run" and "runningtrail" not in w.tags
            and w.sport_type != "indoor running" and tday - WINDOWS[-1] < math.floor(w.day) <= tday]


def _tpace_at_cp(ds: Dataset, today: dt.date, runs: list) -> Optional[dict]:
    import statistics
    from backend.engine import power_source as PS
    from backend.engine.wko5expr.dataset import Workout
    tday = int(math.floor(date_to_day(today)))
    ref = next((w for w in reversed(ds.workouts) if w.sport == "run" and math.floor(w.day) <= tday), None)
    if ref is None:
        return None
    import dataclasses
    cp = ds.cp(dataclasses.replace(ref, day=float(tday)) if isinstance(ref, Workout) else ref)
    if not cp:
        return None
    src = getattr(ds, "power_source", None)
    per = {}
    for w in runs:
        if src is not None and src(w) != PS.STRYD:
            continue
        v = ds.cached_series(SPW_KEY, w, lambda w=w: _speed_per_watt(
            ds.channel(w.idx, "elapsedtime"), ds.channel(w.idx, "power"), ds.channel(w.idx, "speed")))
        if v:
            per[w.idx] = v
    ds.flush_series()
    for days in WINDOWS:
        vals = [per[w.idx] for w in runs if w.idx in per and math.floor(w.day) > tday - days]
        if len(vals) >= TPACE_MIN_RUNS:
            spw = statistics.median(vals)
            return {"value": 1000.0 / (cp * spw) / 60.0, "n": len(vals), "days": days, "method": "cp",
                    "cp": cp, "speed_per_watt": spw,
                    "reason": f"推估：CP {cp:.0f} W × 近 {days} 天 {len(vals)} 次 Stryd 路跑的速度／功率比"
                              f"（中位數 {spw * 1000:.1f} mm/s/W）＝ CP 對應的平路配速"}
    return None


def estimate_tpace(ds: Dataset, today: dt.date) -> dict:
    """Threshold pace (min/km) as of `today` from the road runs before it:
    pace at CP (Stryd runs), else pace at LTHR. {"value": None, ...} with
    the reason when too few runs qualify."""
    import statistics
    tday = int(math.floor(date_to_day(today)))
    runs = _road_runs(ds, tday)
    at_cp = _tpace_at_cp(ds, today, runs)
    if at_cp:
        return at_cp
    per = {}
    for w in runs:
        lthr = ds.sport_setting("thr", w)

        def compute(w=w, lthr=lthr):
            return _tpace_run(ds.channel(w.idx, "elapsedtime"), ds.channel(w.idx, "heartrate"),
                              ds.channel(w.idx, "elapseddistance"), lthr)
        v = ds.cached_series(TPACE_KEY, w, compute)
        if v:
            per[w.idx] = v
    ds.flush_series()
    for days in WINDOWS:
        vals = [per[w.idx]["pace"] for w in runs if w.idx in per and math.floor(w.day) > tday - days]
        if len(vals) >= TPACE_MIN_RUNS:
            return {"value": statistics.median(vals), "n": len(vals), "days": days, "method": "lthr",
                    "reason": f"推估：近 {days} 天 {len(vals)} 次路跑中，心率在 LTHR ±3% 的最快 20 分鐘配速中位數"
                              "（Friel 30 分鐘測試的後 20 分鐘）"}
    return {"value": None, "n": len(per), "days": WINDOWS[-1],
            "reason": f"近 {WINDOWS[-1]} 天只有 {len(per)} 次路跑有 20 分鐘心率在 LTHR ±3%（需要 ≥ {TPACE_MIN_RUNS} 次）"}
