"""
Core training metrics calculation.

Reverse-engineered from PowerKitOSX.framework strings:
  - normalizedpower / npower channel → 30s rolling avg^4 → mean^0.25
  - tsspower → (duration_s × NP × IF) / (FTP × 3600) × 100
  - TSS formula confirmed via Coggan/Allen "Training and Racing with a Power Meter"
"""

import math
import numpy as np
from datetime import date, timedelta
from typing import Optional


def compute_run_ftp_from_mmp(mmp_points: dict[int, float]) -> Optional[float]:
    """
    Estimate running FTP (Critical Power) from MMP curve using a 2-parameter CP model.

    WKO5 formula: athleterange(date-89, date, ftp(meanmax(runpower)))

    Model: P(t) = CP + W'/t  →  fit to MMP points between 3–30 minutes.
    Returns CP (watts), which is the running FTP estimate.
    """
    # Use durations in the 3–30 min range for a reliable fit
    fit_durations = [d for d in sorted(mmp_points) if 180 <= d <= 1800 and mmp_points[d] > 0]
    if len(fit_durations) < 2:
        return None

    # P(t) = CP + W'/t  →  P(t) × t = CP × t + W'
    # This is linear in (t, 1): [t, 1] @ [CP, W'] = P(t)*t
    A = np.array([[d, 1.0] for d in fit_durations])
    b = np.array([mmp_points[d] * d for d in fit_durations])
    result = np.linalg.lstsq(A, b, rcond=None)
    cp, _ = result[0]
    return round(float(cp), 1) if cp > 0 else None


def normalized_power(power: np.ndarray, sample_rate_s: float = 1.0) -> float:
    """
    Normalized Power (NP).

    WKO5 internal channel: normalizedpower / _rapower4
    Algorithm:
      1. 30s rolling average
      2. raise to 4th power
      3. average over ride
      4. 4th root
    """
    power = np.asarray(power, dtype=np.float64)
    power = np.where(np.isnan(power) | (power < 0), 0.0, power)

    window = max(1, int(round(30.0 / sample_rate_s)))

    if len(power) < window:
        return float(np.mean(power))

    cumsum = np.zeros(len(power) + 1)
    cumsum[1:] = np.cumsum(power)
    rolling = (cumsum[window:] - cumsum[:-window]) / window

    mean_pow4 = np.mean(rolling ** 4)
    np_val = mean_pow4 ** 0.25

    return round(float(np_val), 1)


def intensity_factor(np_w: float, ftp_w: float) -> float:
    """IF = NP / FTP"""
    if ftp_w <= 0:
        return 0.0
    return round(np_w / ftp_w, 3)


def training_stress_score(
    duration_s: float,
    np_w: float,
    ftp_w: float,
) -> float:
    """
    TSS = (duration_s × NP × IF) / (FTP × 3600) × 100

    WKO5 symbol: tsspower
    Coggan/Allen formula, unchanged.
    """
    if ftp_w <= 0 or duration_s <= 0:
        return 0.0
    if_val = intensity_factor(np_w, ftp_w)
    tss = (duration_s * np_w * if_val) / (ftp_w * 3600) * 100
    return round(tss, 1)


def average_power(power: np.ndarray) -> float:
    """Mean power excluding zeros (stopped segments)."""
    power = np.asarray(power, dtype=np.float64)
    active = power[power > 0]
    if len(active) == 0:
        return 0.0
    return round(float(np.mean(active)), 1)


def variability_index(np_w: float, avg_w: float) -> float:
    """VI = NP / Average Power. 1.0 = perfectly steady."""
    if avg_w <= 0:
        return 0.0
    return round(np_w / avg_w, 3)


def compute_all_metrics(
    power: np.ndarray,
    ftp_w: Optional[float],
    duration_s: Optional[float] = None,
    hr: Optional[np.ndarray] = None,
    cadence: Optional[np.ndarray] = None,
    sample_rate_s: float = 1.0,
) -> dict:
    """Compute all metrics for a workout."""
    power = np.asarray(power, dtype=np.float64)
    power = np.where(np.isnan(power) | (power < 0), 0.0, power)

    if duration_s is None:
        duration_s = len(power) * sample_rate_s

    np_w = normalized_power(power, sample_rate_s)
    avg_w = average_power(power)
    vi = variability_index(np_w, avg_w)

    result: dict = {
        "avg_power_w": avg_w,
        "normalized_power_w": np_w,
        "variability_index": vi,
        "duration_s": round(duration_s, 0),
    }

    if ftp_w and ftp_w > 0:
        if_val = intensity_factor(np_w, ftp_w)
        tss = training_stress_score(duration_s, np_w, ftp_w)
        result["intensity_factor"] = if_val
        result["tss"] = tss
    else:
        result["intensity_factor"] = None
        result["tss"] = None

    if hr is not None:
        hr_arr = np.asarray(hr, dtype=np.float64)
        hr_arr = hr_arr[hr_arr > 0]
        if len(hr_arr) > 0:
            result["avg_hr_bpm"] = round(float(np.mean(hr_arr)), 0)
            result["max_hr_bpm"] = round(float(np.max(hr_arr)), 0)

    if cadence is not None:
        cad = np.asarray(cadence, dtype=np.float64)
        cad = cad[cad > 0]
        if len(cad) > 0:
            result["avg_cadence_rpm"] = round(float(np.mean(cad)), 0)

    return result


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
