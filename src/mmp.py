"""
Mean Maximal Power (MMP) curve calculation.

Reverse-engineered from PowerKitOSX.framework:
  searchMeanMaximalTimeOrDistancePK8PKVectorS1_S1_bR16PKMeanMaxEntriesP8PKThread

Algorithm: sliding window maximum average over a power time-series.
Reference: GoldenCheetah RideFile::meanMaximalPower (open-source cross-validation).
"""

import numpy as np
from typing import Optional

# Standard MMP durations (seconds) — matches WKO5 display breakpoints
MMP_DURATIONS = [
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    12, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60,
    75, 90, 105, 120, 150, 180, 210, 240, 270, 300,
    360, 420, 480, 540, 600, 660, 720, 780, 840, 900,
    1050, 1200, 1350, 1500, 1650, 1800,
    2100, 2400, 2700, 3000, 3300, 3600,
]

# Gap threshold: power==0 for longer than this is considered a stop (seconds)
# WKO5 doesn't expose this constant in strings; 30s is the GoldenCheetah default.
GAP_THRESHOLD_S = 30


def compute_mmp(
    power: np.ndarray,
    time: np.ndarray,
    durations: Optional[list[int]] = None,
    ignore_gaps: bool = False,
) -> dict[int, float]:
    """
    Compute MMP curve from power and time arrays.

    Args:
        power: power values in watts (1-s samples, may have gaps)
        time: elapsed time in seconds, must be same length as power
        durations: target window durations in seconds (defaults to MMP_DURATIONS)
        ignore_gaps: if True, treat gaps as valid data (meanmax); if False, skip
                     gaps (meanmaxgaps behaviour — excludes stopped segments)

    Returns:
        dict mapping duration_s -> best mean power (W) for that window
    """
    if durations is None:
        durations = MMP_DURATIONS

    power = np.asarray(power, dtype=np.float64)
    time = np.asarray(time, dtype=np.float64)

    n = len(power)
    if n == 0:
        return {d: 0.0 for d in durations}

    # Build a cleaned power array: replace NaN/negative with 0
    power = np.where(np.isnan(power) | (power < 0), 0.0, power)

    if ignore_gaps:
        result = _mmp_with_gaps(power, time, durations)
    else:
        result = _mmp_no_gaps(power, time, durations)

    return result


def _mmp_no_gaps(
    power: np.ndarray,
    time: np.ndarray,
    durations: list[int],
) -> dict[int, float]:
    """
    Standard sliding-window MMP (no gap exclusion).

    For uniformly sampled data (1s), a window of k samples covers k seconds.
    We infer sample rate from the time array and use a fixed-count sliding window,
    which avoids off-by-one errors from elapsed-time comparisons.
    """
    result: dict[int, float] = {}
    n = len(power)
    if n < 2:
        return {d: float(power[0]) if n == 1 else 0.0 for d in durations}

    # Infer sample rate (median interval handles occasional dropped samples)
    dt = float(np.median(np.diff(time)))
    if dt <= 0:
        dt = 1.0

    # Cumulative sum for O(1) window averages
    cumsum = np.zeros(n + 1, dtype=np.float64)
    cumsum[1:] = np.cumsum(power)

    for target_d in durations:
        # Number of samples required to cover target_d seconds
        k = max(1, round(target_d / dt))
        if k > n:
            result[target_d] = 0.0
            continue

        # Vectorised: all windows of length k simultaneously
        window_sums = cumsum[k:] - cumsum[:n - k + 1]
        best = float(np.max(window_sums)) / k
        result[target_d] = best

    return result


def _mmp_with_gaps(
    power: np.ndarray,
    time: np.ndarray,
    durations: list[int],
) -> dict[int, float]:
    """
    Gap-aware MMP: segments where power==0 for >GAP_THRESHOLD_S are excluded.

    Strategy: split power/time into continuous segments, compute MMP per segment,
    take the maximum across segments for each duration.
    """
    segments = _split_segments(power, time)
    if not segments:
        return {d: 0.0 for d in durations}

    result: dict[int, float] = {}
    for target_d in durations:
        best = 0.0
        for seg_power, seg_time in segments:
            seg_result = _mmp_no_gaps(seg_power, seg_time, [target_d])
            best = max(best, seg_result.get(target_d, 0.0))
        result[target_d] = best

    return result


def _split_segments(
    power: np.ndarray,
    time: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split power/time into continuous movement segments, removing long stops."""
    segments = []
    start = 0
    n = len(power)

    i = 0
    while i < n:
        # Detect gap start: power == 0
        if power[i] == 0.0:
            gap_start = i
            while i < n and power[i] == 0.0:
                i += 1
            gap_duration = time[i - 1] - time[gap_start] if gap_start > 0 else 0
            if gap_duration >= GAP_THRESHOLD_S:
                # Save segment before gap
                if gap_start > start:
                    seg_time = time[start:gap_start] - time[start]
                    segments.append((power[start:gap_start], seg_time))
                start = i
        else:
            i += 1

    # Final segment
    if start < n:
        seg_time = time[start:n] - time[start]
        segments.append((power[start:n], seg_time))

    return segments


def mmp_at(mmp_curve: dict[int, float], duration_s: int) -> float:
    """
    Interpolate MMP curve at an arbitrary duration.
    Uses linear interpolation between adjacent breakpoints.
    """
    if duration_s in mmp_curve:
        return mmp_curve[duration_s]

    keys = sorted(mmp_curve.keys())
    if duration_s <= keys[0]:
        return mmp_curve[keys[0]]
    if duration_s >= keys[-1]:
        return mmp_curve[keys[-1]]

    for i, k in enumerate(keys[:-1]):
        if k <= duration_s <= keys[i + 1]:
            lo, hi = k, keys[i + 1]
            t = (duration_s - lo) / (hi - lo)
            return mmp_curve[lo] + t * (mmp_curve[hi] - mmp_curve[lo])

    return 0.0
