"""
Core training metrics calculation.

Reverse-engineered from PowerKitOSX.framework strings:
  - normalizedpower / npower channel → 30s rolling avg^4 → mean^0.25
  - tsspower → (duration_s × NP × IF) / (FTP × 3600) × 100
  - TSS formula confirmed via Coggan/Allen "Training and Racing with a Power Meter"
"""

import numpy as np
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

    Args:
        power: power in watts (uniform samples)
        sample_rate_s: seconds per sample (default 1.0)
    """
    power = np.asarray(power, dtype=np.float64)
    power = np.where(np.isnan(power) | (power < 0), 0.0, power)

    # Window size for 30s
    window = max(1, int(round(30.0 / sample_rate_s)))

    if len(power) < window:
        return float(np.mean(power))

    # Rolling 30s average using cumsum (O(n))
    cumsum = np.zeros(len(power) + 1)
    cumsum[1:] = np.cumsum(power)
    rolling = (cumsum[window:] - cumsum[:-window]) / window

    # 4th power mean, then 4th root
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
    """
    Compute all metrics for a workout.

    Returns dict suitable for JSON serialization.
    """
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
        hr = np.asarray(hr, dtype=np.float64)
        hr = hr[hr > 0]
        if len(hr) > 0:
            result["avg_hr_bpm"] = round(float(np.mean(hr)), 0)
            result["max_hr_bpm"] = round(float(np.max(hr)), 0)

    if cadence is not None:
        cad = np.asarray(cadence, dtype=np.float64)
        cad = cad[cad > 0]
        if len(cad) > 0:
            result["avg_cadence_rpm"] = round(float(np.mean(cad)), 0)

    return result
