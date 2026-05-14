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
