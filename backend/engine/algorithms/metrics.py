"""
PMC / load helpers (the validator and tests use them; the app's charts and PMC compute
their own in engine/wko5expr/).

The per-import power metrics (NP, IF, TSS, runFTP from a mean-max fit) and hrTSS / rTSS
that used to live here fed only the workout_metrics table, which nothing read; removed
2026-10-08 with it.
"""

import math
from datetime import date, timedelta
from typing import Optional


def compute_pmc(
    tss_series: list[tuple[date, float]],
    ctl_tau: float = 42.0,
    atl_tau: float = 7.0,
) -> list[dict]:
    """
    PMC: CTL, ATL, TSB from TSS time series.
    WKO5 formulas:
      CTL = tl(tss, ctlconstant)
      ATL = tl(tss, atlconstant)
      TSB = shift(CTL - ATL, 1)  [yesterday's CTL-ATL]
    """
    if not tss_series:
        return []
    ctl_factor = 1 - math.exp(-1 / ctl_tau)
    atl_factor = 1 - math.exp(-1 / atl_tau)
    tss_dict = {d: t for d, t in tss_series}
    start_date = min(d for d, _ in tss_series)
    end_date = max(d for d, _ in tss_series)
    ctl = atl = 0.0
    prev_ctl = prev_atl = 0.0
    result = []
    current = start_date
    while current <= end_date:
        tss_today = tss_dict.get(current, 0.0)
        prev_ctl, prev_atl = ctl, atl
        ctl = ctl + ctl_factor * (tss_today - ctl)
        atl = atl + atl_factor * (tss_today - atl)
        tsb = round(prev_ctl - prev_atl, 2)
        result.append({
            "date": current.isoformat(),
            "ctl": round(ctl, 2),
            "atl": round(atl, 2),
            "tsb": tsb,
            "tss": tss_today,
        })
        current += timedelta(days=1)
    return result


def compute_run_pmc(
    run_tss_series: list[tuple[date, float]],
    ctl_tau: float = 42.0,
    atl_tau: float = 7.0,
    ramp_days: int = 7,
    initial_ctl: float = 0.0,
    initial_atl: float = 0.0,
) -> list[dict]:
    """
    Run-specific PMC.
    WKO5: tl(if(sport="run", tss), ctlconstant/atlconstant)
    Appends acwr, daily_pct_ctl, ramp_rate, ramp_pct_ctl per day.
    """
    if not run_tss_series:
        return []
    ctl_factor = 1 - math.exp(-1 / ctl_tau)
    atl_factor = 1 - math.exp(-1 / atl_tau)
    tss_dict = {d: t for d, t in run_tss_series}
    start_date = min(d for d, _ in run_tss_series)
    end_date = max(d for d, _ in run_tss_series)
    ctl = initial_ctl
    atl = initial_atl
    prev_ctl = initial_ctl
    prev_atl = initial_atl
    history: list[dict] = []
    current = start_date
    while current <= end_date:
        tss_today = tss_dict.get(current, 0.0)
        prev_ctl, prev_atl = ctl, atl
        ctl = ctl + ctl_factor * (tss_today - ctl)
        atl = atl + atl_factor * (tss_today - atl)
        tsb = round(prev_ctl - prev_atl, 2)
        acwr = round(atl / ctl, 3) if ctl > 0 else None
        daily_pct_ctl = round(tss_today / ctl, 3) if ctl > 0 else None
        if len(history) >= ramp_days:
            ramp_ctl = round(ctl - history[-ramp_days]["ctl"], 2)
        else:
            ramp_ctl = round(ctl, 2)
        ramp_pct_ctl = round(ramp_ctl / ctl, 3) if ctl > 0 else None
        history.append({
            "date": current.isoformat(),
            "ctl": round(ctl, 2),
            "atl": round(atl, 2),
            "tsb": tsb,
            "tss": tss_today,
            "acwr": acwr,
            "daily_pct_ctl": daily_pct_ctl,
            "ramp_rate": ramp_ctl,
            "ramp_pct_ctl": ramp_pct_ctl,
        })
        current += timedelta(days=1)
    return history


def pace_rtss(
    distance_m: Optional[float],
    duration_s: Optional[float],
    threshold_pace_s_per_m: Optional[float],
) -> float:
    """
    Simplified pace-based running TSS (no elevation correction).
    IF = threshold_pace_s_per_m / avg_pace_s_per_m
    rTSS = (duration_s / 3600) * IF^2 * 100
    """
    if not distance_m or not duration_s or not threshold_pace_s_per_m:
        return 0.0
    if distance_m <= 0 or duration_s <= 0 or threshold_pace_s_per_m <= 0:
        return 0.0
    avg_pace_s_per_m = duration_s / distance_m
    intensity_factor = threshold_pace_s_per_m / avg_pace_s_per_m
    rtss = (duration_s / 3600.0) * (intensity_factor ** 2) * 100.0
    return round(rtss, 1)


def compute_intensity_load_series(
    intensity_by_date: list[tuple[date, float]],
    tau: float,
) -> list[dict]:
    """
    EWMA of per-workout high-intensity time (seconds).
    WKO5: tl(sum(if(runpower >= threshold*runFTP, deltatime)), ctlconstant/atlconstant)
    Returns {"date": str, "value": float} in minutes.
    """
    if not intensity_by_date:
        return []
    factor = 1 - math.exp(-1 / tau)
    tdict = {d: v for d, v in intensity_by_date}
    start_date = min(d for d, _ in intensity_by_date)
    end_date = max(d for d, _ in intensity_by_date)
    ewma = 0.0
    result = []
    current = start_date
    while current <= end_date:
        val = tdict.get(current, 0.0)
        ewma = ewma + factor * (val - ewma)
        result.append({"date": current.isoformat(), "value": round(ewma / 60.0, 2)})
        current += timedelta(days=1)
    return result
