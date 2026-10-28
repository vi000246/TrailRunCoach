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
from backend.i18n import N_, _

WINDOWS = (90, 180)          # try 90 days, widen to 180 if too few runs
# 「HR at CP」 is a cross-check only — never the LTHR value, never written to the
# plan (docs/research/zones-and-thresholds.md §2.2, §3.3: the individual 95 %
# limits of agreement of HR at CP vs MLSS are −16…+17 bpm, Micheli et al. 2025)
HR_AT_CP_NOTE = N_("CP 附近的心率只當交叉檢查，不寫進 LTHR（個人誤差約 ±16 bpm，Micheli 2025）")
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


def _per_run_pace(ds: Dataset, w) -> Optional[dict]:
    """SP-952: a run not judged by power (no power meter, its date's CP stale, watch power): no LTHR
    numbers (they need a CP) and the AeT drift point from Pa:HR (steady_drift_pace) on a flat road run —
    gated by the run's LTHR and its threshold-pace setting (both in the cache stamp, _settings_sig)."""
    from backend.engine.algorithms.classify import TRAIL_CLIMB_RATE_M_PER_KM
    from backend.engine.algorithms.threshold_estimate import steady_drift_pace
    t = ds.channel(w.idx, "elapsedtime")
    hr = ds.channel(w.idx, "heartrate")
    sp = ds.channel(w.idx, "speed")
    if t is None or hr is None:
        return None
    empty = RunThreshold(None, None, None, 0)
    m = w.metrics or {}
    dist, climb = m.get("distance"), m.get("climbing")
    hilly = bool(dist and climb is not None and float(dist) > 0.5 and float(climb) / float(dist) >= TRAIL_CLIMB_RATE_M_PER_KM)
    dp = None
    if sp is not None and "runningtrail" not in w.tags and not hilly:
        lthr = ds.sport_setting("thr", w) if hasattr(ds, "sport_setting") else None
        tp = ds.sport_setting("tpace", w) if hasattr(ds, "sport_setting") else None
        dp = steady_drift_pace(list(t), list(hr), list(sp), lthr, 60.0 / tp if tp else None)
    return {"lt": asdict(empty), "dp": None if dp is None else asdict(dp), "cp": None, "dp_basis": "pace"}


def _activity_power(ds: Dataset, w) -> bool:
    try:
        from backend.engine import power_state as PST
        return PST.activity_power(ds, w)
    except Exception:                       # noqa: BLE001
        return True


def _day_power(ds: Dataset, today: dt.date) -> bool:
    try:
        from backend.engine import power_state as PST
        return PST.day_power(ds, today)
    except Exception:                       # noqa: BLE001
        return True


def _nan_free(x):
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: _nan_free(v) for k, v in x.items()}
    return x


def _judged_against_cp(ds: Dataset, runs: list, tday: int):
    """SP-572: which runs' power may say 「hard enough」 against the CP of `tday`
    (estimate_lthr: best 30 min ≥ 95 % CP) — the runs whose power is the CP's
    kind. The rule of racepower.athlete._pd_mftp (cp_as_of): when the CP's
    90-day window has usable power (Stryd, or watch power with
    power.accept_watch_power) the CP comes from it, and only those runs count;
    without any it is the wrist-power fit (or a plan test made on the watch),
    and every run with power counts. Wrist power reads on another scale than a
    Stryd (~1.3–1.8 ×), so a wrist easy run judged against a Stryd CP looked
    hard and its HR pulled the estimate down (146 for a true 170 on synthetic
    runs, test_lthr_without_power_sp294 §5). Returns a predicate."""
    # SP-950 (owner 2026-10-27): watch power never counts — only the runs whose power the models use
    # (Stryd) are judged, also when the window has none (it used to judge every run then)
    from backend.engine.racepower import athlete as A
    return lambda w: A.power_ok(ds, w)


def estimate(ds: Dataset, today: Optional[dt.date] = None, cp_of=None) -> dict:
    """`cp_of(day) -> CP or None`: the CP in effect on a date, for a back-test
    that must not see later values (racepower.athlete.cp_as_of). Each run is
    then measured against the CP of its own date and the estimate against the
    CP of `today`. Default: Dataset.cp (the plan test on or before the run's
    date, else WKO5's current mFTP snapshot)."""
    today = today or dt.date.today()
    tday = int(math.floor(date_to_day(today)))
    runs = [w for w in ds.workouts if w.sport == "run" and tday - WINDOWS[-1] < math.floor(w.day) <= tday]
    off = cp_of is None and _no_meter(ds, today)
    # SP-950 ③ (no power meter) / SP-952 ② (the CP stale today): no CP in today's estimate — no LTHR from
    # ordinary runs (no_meter_lthr / stale_lthr: CP evidence never moves the LTHR then), the AeT from Pa:HR
    # drift (steady_drift_pace). Each run is judged by power only when it was that day
    # (power_state.activity_power). The load-time grid (fitdataset._estimate_settings, cp_of given: the
    # history the TSS stands on) is left as it was
    use_power = cp_of is not None or (not off and _day_power(ds, today))
    data = {}
    for w in runs:
        if cp_of is None and (not use_power or not _activity_power(ds, w)):
            v = ds.cached_series(_KEY, w, lambda w=w: _nan_free(_per_run_pace(ds, w)), extra={"power_on": False})
        elif cp_of is None:
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
        cp_now = plan.threshold_on("cp", today) if plan is not None and use_power else None
        if cp_now is None and use_power:
            cp_now = ds.cp(runs[-1]) if runs else None
        if cp_now is None and use_power and _has_power(ds, runs):
            # SP-573: no plan test and no Stryd fit (Dataset.cp) but runs with power — a watch-power
            # history. The estimate made at load (fitdataset._estimate_settings → runthr) measures
            # them against cp_as_of, the wrist-power fit; the suggestion (threshold-estimate API,
            # overview) takes the same route, so it gives the same value instead of nothing
            from backend.engine.racepower import athlete as A
            return estimate(ds, today, cp_of=lambda d: A.cp_as_of(ds, d))
    else:
        cp_now = cp_of(today)

    judged = _judged_against_cp(ds, runs, tday)

    def pick(days):
        ws = [w for w in runs if math.floor(w.day) > tday - days and w.idx in data]
        # the LTHR side only: AeT's drift points are measured against each run's own-date CP
        lts = [RunThreshold(**data[w.idx]["lt"]) for w in ws if judged(w)]
        # SP-952: one basis for the AeT line — Pw:HR while power is in effect today, Pa:HR otherwise
        want = "pace" if not use_power else None
        dps = [DriftPoint(**data[w.idx]["dp"]) for w in ws if data[w.idx]["dp"]
               and data[w.idx].get("dp_basis") == want]
        return ws, lts, dps

    out = {"cp": cp_now, "today": today.isoformat()}
    if not use_power:
        out["aet_basis"] = "pace"
    for days in WINDOWS:
        ws, lts, dps = pick(days)
        if not cp_now:
            out["lthr"] = no_meter_lthr() if off else stale_lthr() if not use_power else no_cp_lthr()
            break
        lt = estimate_lthr(lts, cp_now)
        if lt.value is not None or days == WINDOWS[-1]:
            out["lthr"] = {**asdict(lt), "days": days, "hr_at_cp_note": _(HR_AT_CP_NOTE)}
            break
    lthr_v = (out.get("lthr") or {}).get("value")
    if lthr_v is None and not use_power and runs:
        try:                                # SP-952: the LTHR in effect bounds the Pa:HR AeT line
            lthr_v = ds.sport_setting("thr", runs[-1])
        except Exception:                   # noqa: BLE001
            lthr_v = None
    for days in WINDOWS:
        ws, lts, dps = pick(days)
        ae = estimate_aet(dps, lthr=lthr_v)
        if ae.value is not None or days == WINDOWS[-1]:
            from backend.engine.zones import aet_uncertainty
            out["aethr"] = {**asdict(ae), "days": days,
                            "points": [[round(p.hr1, 1), round(p.drift, 4)] for p in dps],
                            "pm": None if ae.value is None else aet_uncertainty(
                                "regression" if ae.se else "estimate", ae.se)}
            break
    return out


def _no_meter(ds: Dataset, today: dt.date) -> bool:
    """SP-950 ③ for this Dataset's runner (power_state.no_meter); False when it cannot tell."""
    try:
        from backend.engine import power_state as PST
        return PST.no_meter(ds, today)
    except Exception:                       # noqa: BLE001
        return False


def _has_power(ds: Dataset, runs: list) -> bool:
    """Some run of the window has power the models use (Stryd; SP-950: never watch power), the kind
    cp_as_of can fit. False for a Dataset that cannot tell (no power_source)."""
    from backend.engine import power_source as PS
    from backend.engine.racepower import athlete as A
    return any(A.power_source(ds, w) == PS.STRYD and A.power_ok(ds, w) for w in runs)


# SP-573 (docs/research/lthr-without-power.md §5 單 3): without a CP no LTHR comes from ordinary
# runs — a run counts when its best 30 min is ≥ 95 % CP, and an HR- or pace-only gate is circular
# or a lower bound only (§3.2). Say so, and point at the 30-minute test
NO_CP_REASON = N_("沒有 CP（沒有 Stryd 功率，也沒有 CP 測試），所以不能從一般跑步估 LTHR；"
                  "可以做一次 30 分鐘 LTHR 測試")


NO_METER_REASON = N_("沒有功率計：一般跑步估不出 LTHR（要用功率判斷哪幾次夠硬）；LTHR 沿用目前的值，可以手動輸入或用比賽成績")


def no_meter_lthr() -> dict:
    """SP-950 ③: the `lthr` of today's estimate without a power meter — no value, no test suggested
    (owner 2026-10-27: no 30-min test; the LTHR in effect stays, manual entry / races)."""
    return {"value": None, "n": 0, "hr_at_cp": None, "n_at_cp": 0, "spread": None,
            "reason": _(NO_METER_REASON), "days": None, "no_cp": True, "no_meter": True, "links": []}


STALE_REASON = N_("CP 已過期：不用 CP 判斷哪幾次跑步夠硬，所以一般跑步估不出 LTHR；LTHR 沿用目前的值，"
                  "可以手動輸入、用比賽成績，或做一次 CP 測試切回功率")


def stale_lthr() -> dict:
    """SP-952 ②: the `lthr` of today's estimate while the CP is stale — no value, no CP evidence."""
    return {"value": None, "n": 0, "hr_at_cp": None, "n_at_cp": 0, "spread": None,
            "reason": _(STALE_REASON), "days": None, "no_cp": True, "stale": True, "links": []}


def no_cp_lthr() -> dict:
    """The `lthr` of an estimate without a CP: no value, why, and the 「安排課表」 link of the LTHR
    test (threshold_confidence.schedule_link, the link its test suggestion carries)."""
    from backend.engine.threshold_confidence import schedule_link
    return {"value": None, "n": 0, "hr_at_cp": None, "n_at_cp": 0, "spread": None,
            "reason": _(NO_CP_REASON), "days": None, "no_cp": True, "links": [schedule_link("tt30")]}


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
#    far too slow (cardiac drift: on one runner's summer runs HR sat at
#    LTHR at a pace ~15 % slower than the same runner's winter half marathon),
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
                    "reason": _("推估：CP {cp:.0f} W × 近 {days} 天 {n} 次 Stryd 路跑的速度／功率比"
                                "（中位數 {spw:.1f} mm/s/W）＝ CP 對應的平路配速",
                                cp=cp, days=days, n=len(vals), spw=spw * 1000)}
    return None


def estimate_tpace(ds: Dataset, today: dt.date) -> dict:
    """Threshold pace (min/km) as of `today` from the road runs before it:
    pace at CP (Stryd runs), else pace at LTHR. {"value": None, ...} with
    the reason when too few runs qualify."""
    import statistics
    tday = int(math.floor(date_to_day(today)))
    runs = _road_runs(ds, tday)
    # SP-952: pace at CP only while power is in effect that day (not with a stale CP / no power meter)
    at_cp = _tpace_at_cp(ds, today, runs) if _day_power(ds, today) else None
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
                    "reason": _("推估：近 {days} 天 {n} 次路跑中，心率在 LTHR ±3% 的最快 20 分鐘配速中位數"
                                "（Friel 30 分鐘測試的後 20 分鐘）", days=days, n=len(vals))}
    return {"value": None, "n": len(per), "days": WINDOWS[-1],
            "reason": _("近 {days} 天只有 {n} 次路跑有 20 分鐘心率在 LTHR ±3%（需要 ≥ {need} 次）",
                        days=WINDOWS[-1], n=len(per), need=TPACE_MIN_RUNS)}


# ---------------------------------------------------------------------------
# maximum heart rate (推估) — the highest HR the athlete's own runs reached
#
# Wrist optical HR only (no chest strap): its errors are short spikes (a jump
# of 20–40 bpm within a second, often to 215–225) and cadence lock-on. Per
# run, the shared cleaning (engine/hr_quality.clean, SP-265: gaps > 5 s, out
# of range, spikes, moving up-steps, cadence lock — the numbers live there), then the run's
# peak = the best 10-s mean HR (the max of the rolling 10-s mean over fully valid
# windows, hr_quality.rolling_mean_peak), so a 1–4 s spike can't decide it.
# Result: the highest per-run peak of the runs (road, trail, treadmill) in the
# 365 days up to the date — the top run is used as it is.
# SP-493 R2 (owner 2026-10-20, Q4): it was the highest HR held ≥ 5 s, and the top run
# was dropped when it stood > 5 bpm above the next (MHR_OUTLIER, 2026-10-03). That rule
# predates the shared cleaning (SP-265, 10-06), which removes the spikes, the up-steps,
# the values > 220 and the cadence lock first; and one all-out test or race is exactly
# the evidence a max HR needs. 「撐 10 秒」 (every second ≥ the value) was too hard to
# reach, so the 10-s mean. A 10-s artefact that survives the cleaning still shows in the
# confidence card's 120-s check (threshold_confidence.hrmax_check).
# ≥ 3 runs with HR are needed. Every number here is 推估 (no source); the
# value is a floor — a runner rarely reaches true HRmax outside a test.
# ---------------------------------------------------------------------------

MHR_KEY = "mhr_peak5_v3"      # v2 (SP-265): the shared cleaning; v3 (SP-493 R2): the best 10-s mean
MHR_DAYS = 365
MHR_MEAN_S = 10
MHR_MIN_RUNS = 3
MHR_REASON = N_("推估：近 {days} 天 {n} 次跑步中，10 秒平均心率的最高值"
                "（先濾掉光學心率尖刺：3 秒內跳 ≥ 15 bpm 又回來、超過 220、跟著步頻）")
MHR_TOO_FEW = N_("近 {days} 天只有 {n} 次跑步有心率（需要 ≥ {need} 次）")


def peak_sustained_hr(t, hr, cadence_spm=None, speed_kmh=None) -> Optional[float]:
    """The best MHR_MEAN_S-second mean HR after the shared cleaning
    (hr_quality.clean, then hr_quality.rolling_mean_peak); None without enough valid samples."""
    from backend.engine import hr_quality as HQ
    c = HQ.clean(t, hr, cadence_spm, speed_kmh=speed_kmh)
    if c is None:
        return None
    return HQ.rolling_mean_peak(c[1], MHR_MEAN_S)


def _cadence_spm(ds, w):
    """The run's cadence in steps / min (the channel is strides / min), or None."""
    try:
        c = ds.channel(w.idx, "cadence")
    except Exception:                       # noqa: BLE001
        return None
    if c is None:
        return None
    import numpy as np
    return np.asarray(c, float) * 2.0


def estimate_mhr(ds: Dataset, today: dt.date) -> dict:
    """Maximum HR (bpm, 推估) as of `today` from the runs of the 365 days up
    to it: {"value", "n", "days", "peaks": [[date, bpm]] (top 5), "reason"};
    value None with the reason when fewer than 3 runs have HR."""
    from backend.i18n import current_locale
    tday = int(math.floor(date_to_day(today)))
    memo = getattr(ds, "memo", None)
    mk = ("estimate_mhr", tday, len(getattr(ds, "workouts", []) or []), current_locale())   # the reason's language
    if isinstance(memo, dict) and mk in memo:
        return memo[mk]
    runs = [w for w in ds.workouts if w.sport == "run" and tday - MHR_DAYS < math.floor(w.day) <= tday]
    cached = getattr(ds, "cached_series", None)
    peaks = []
    for w in runs:
        def compute(w=w):
            return peak_sustained_hr(ds.channel(w.idx, "elapsedtime"), ds.channel(w.idx, "heartrate"),
                                     _cadence_spm(ds, w), ds.channel(w.idx, "speed"))
        v = cached(MHR_KEY, w, compute) if cached is not None else None
        if v is None and cached is None:
            v = compute()
        if v:
            peaks.append((math.floor(w.day), float(v)))
    if cached is not None and hasattr(ds, "flush_series"):
        ds.flush_series()
    from backend.engine.wko5expr.dataset import day_to_date
    top = sorted(peaks, key=lambda p: -p[1])
    out = {"value": None, "n": len(peaks), "days": MHR_DAYS,
           "peaks": [[day_to_date(d).isoformat(), round(v)] for d, v in top[:5]]}
    if len(peaks) < MHR_MIN_RUNS:
        out["reason"] = _(MHR_TOO_FEW, days=MHR_DAYS, n=len(peaks), need=MHR_MIN_RUNS)
    else:
        out["value"] = round(top[0][1])        # SP-493 R2: the top run stands (no outlier drop)
        out["reason"] = _(MHR_REASON, days=MHR_DAYS, n=len(peaks))
    if isinstance(memo, dict):
        memo[mk] = out
    return out


# ---------------------------------------------------------------------------
# Hard efforts (SP-294, owner 2026-10-14): did the runs of the 365 days hold anything hard enough that
# their peak (estimate_mhr: the best 10-s mean) means something? An easy-only runner's peak is the top of his easy runs
# — 147 against an age formula of 180 on docs/research/lthr-without-power.md §1.5 — so hr_profile.max_hr
# prefers the age formula when the runs' value is > 10 bpm under it and nothing below counts as hard.
# A run is hard when (first match wins, newest run first; all 推估 as markers of a hard effort):
#   race    a race on the plan's calendar that day, or a race word in the title (threshold_confidence._race)
#   test    an LTHR / max-HR test title (threshold_confidence.test_kind)
#   power   road power the models may use (Dataset.power_ok) against the run's CP: the best 30 min ≥ 95 %
#           CP (the LTHR estimate's own 「hard enough」 gate, threshold_estimate.MIN_EFFORT_OF_CP) or the
#           best 5 min ≥ CP (above CP — the severe domain, where VO2 and HR climb towards their max)
#   hr      the same two gates on the cleaned HR against an LTHR: the plan's (a test / typed / applied
#           estimate), else `lthr_ref` — hr_profile passes 0.90 × the age formula, the LTHR the age route
#           would give. Never the dataset's 0.90 × max-HR prior or the runs' own max: those come from
#           the very value being judged (circular — §3, 「用心率自己當門檻」).
# Cost: only asked when the runs' value is > 10 bpm under the age formula; titles first, then channels
# (newest first, stops at the first hard run; disk-cached per run).
# ---------------------------------------------------------------------------

HARD_KEY = "hard_effort_v1"
HARD_DAYS = 365
HARD_LONG_S, HARD_SHORT_S = 30 * 60, 5 * 60
HARD_SHORT_FRAC = 1.0


def _best_mean(y, win_s: int) -> Optional[float]:
    from backend.engine.threshold_confidence import best_mean
    return best_mean(y, win_s)


def _hard_by_channels(ds, w, lthr: Optional[float]) -> Optional[str]:
    """power | hr | None for one run (the channel side of hard_efforts)."""
    import numpy as np
    from backend.engine import threshold_confidence as TC
    from backend.engine.algorithms.threshold_estimate import MIN_EFFORT_OF_CP
    t = ds.channel(w.idx, "elapsedtime")
    if t is None:
        return None
    t = np.asarray(t, float)
    c = TC.clean_hr(t, ds.channel(w.idx, "heartrate"), _cadence_spm(ds, w), ds.channel(w.idx, "speed"))
    g = c[0] if c is not None else (np.arange(0.0, float(np.nanmax(t)) + 1.0) if len(t) else None)
    if g is None or not len(g):
        return None
    pw = ds.channel(w.idx, "power") if "runningtrail" not in (w.tags or []) else None
    ok = getattr(ds, "power_ok", None)
    if pw is not None and (ok is None or ok(w)):
        try:
            cp = ds.cp(w)
        except Exception:                   # noqa: BLE001
            cp = None
        from backend.engine.session_stimulus import _grid
        p1 = _grid(t, pw, g) if cp else None
        if p1 is not None:
            p30, p5 = _best_mean(p1, HARD_LONG_S), _best_mean(p1, HARD_SHORT_S)
            if (p30 and p30 >= MIN_EFFORT_OF_CP * cp) or (p5 and p5 >= HARD_SHORT_FRAC * cp):
                return "power"
    if lthr and c is not None:
        h30, h5 = _best_mean(c[1], HARD_LONG_S), _best_mean(c[1], HARD_SHORT_S)
        if (h30 and h30 >= MIN_EFFORT_OF_CP * lthr) or (h5 and h5 >= HARD_SHORT_FRAC * lthr):
            return "hr"
    return None


def hard_efforts(ds: Dataset, today: dt.date, lthr_ref: Optional[float] = None) -> list:
    """[[date ISO, why]] of the newest hard run (the note above; why: race | test | power | hr), or []
    when the runs of the HARD_DAYS up to `today` hold none. A run that fails to read counts as not hard."""
    tday = int(math.floor(date_to_day(today)))
    memo = getattr(ds, "memo", None)
    mk = ("hard_efforts", tday, len(getattr(ds, "workouts", []) or []), lthr_ref)
    if isinstance(memo, dict) and mk in memo:
        return memo[mk]
    from backend.engine import threshold_confidence as TC
    from backend.engine import workout_review as WR
    from backend.engine.wko5expr.dataset import day_to_date
    runs = sorted((w for w in ds.workouts if w.sport == "run" and tday - HARD_DAYS < math.floor(w.day) <= tday),
                  key=lambda w: -w.day)
    out = []
    for w in runs:                          # the cheap side first: titles and the calendar
        try:
            title = WR._title(w)
            why = "race" if TC._race(ds, w, title) else "test" if TC.test_kind([title]) else None
        except Exception:                   # noqa: BLE001
            why = None
        if why:
            out = [[day_to_date(w.day).isoformat(), why]]
            break
    if not out:
        plan = getattr(ds, "plan", None)
        lthr = None
        try:
            from backend.engine.planning import threshold_row
            r = threshold_row(plan, "lthr", today) if plan is not None else None
            lthr = float(r["value"]) if r else None
        except Exception:                   # noqa: BLE001
            lthr = None
        lthr = lthr or (float(lthr_ref) if lthr_ref else None)
        cached = getattr(ds, "cached_series", None)
        for w in runs:
            def compute(w=w):
                return _hard_by_channels(ds, w, lthr) or ""
            try:
                why = cached(HARD_KEY, w, compute, extra=lthr) if cached is not None else compute()
            except Exception:               # noqa: BLE001 — an unreadable run is skipped
                why = ""
            if why:
                out = [[day_to_date(w.day).isoformat(), why]]
                break
        if cached is not None and hasattr(ds, "flush_series"):
            ds.flush_series()
    if isinstance(memo, dict):
        memo[mk] = out
    return out
