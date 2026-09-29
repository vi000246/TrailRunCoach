"""Trail running analysis: GAP, climb segmentation, HR drift."""
from __future__ import annotations
import numpy as np
from typing import Optional


def compute_grade(altitude: np.ndarray, distance: np.ndarray) -> np.ndarray:
    """Per-sample grade in percent from altitude and cumulative distance arrays."""
    delta_d = np.diff(distance, prepend=distance[0])
    delta_d = np.where(delta_d < 0.1, 0.1, delta_d)
    delta_alt = np.diff(altitude, prepend=altitude[0])
    return (delta_alt / delta_d) * 100.0


def compute_gap(pace_s_per_m: np.ndarray, grade_pct: np.ndarray) -> np.ndarray:
    """Grade-adjusted pace (s/m) using Strava linear approximation."""
    factor = np.where(
        grade_pct >= 0,
        1.0 + 0.033 * grade_pct,
        np.maximum(0.5, 1.0 + 0.015 * grade_pct),
    )
    return pace_s_per_m / factor


def segment_climbs(
    altitude: np.ndarray,
    distance: np.ndarray,
    min_gain_m: float = 30.0,
    min_grade_pct: float = 3.0,
) -> list[dict]:
    """
    Identify climb segments where grade ≥ min_grade_pct and gain ≥ min_gain_m.
    Returns list of dicts with start_m, end_m, gain_m, grade_pct, vam, distance_m.
    """
    n = len(altitude)
    if n < 2:
        return []

    grade = compute_grade(altitude, distance)
    climbing = grade >= min_grade_pct

    segments = []
    i = 0
    while i < n:
        if not climbing[i]:
            i += 1
            continue
        j = i
        while j < n and climbing[j]:
            j += 1
        gain = float(altitude[j - 1] - altitude[i])
        if gain >= min_gain_m:
            seg_dist = float(distance[j - 1] - distance[i])
            avg_grade = (gain / seg_dist * 100.0) if seg_dist > 0 else 0.0
            # VAM in m/hr; need time — approximate from distance and grade
            # stored separately; return raw values for caller to enrich
            segments.append({
                "start_m": float(distance[i]),
                "end_m": float(distance[j - 1]),
                "gain_m": round(gain, 1),
                "distance_m": round(seg_dist, 1),
                "grade_pct": round(avg_grade, 1),
                "start_idx": int(i),
                "end_idx": int(j - 1),
            })
        i = j

    return segments


def compute_vam(segments: list[dict], time: np.ndarray) -> list[dict]:
    """Enrich climb segments with VAM (m/hr)."""
    enriched = []
    for seg in segments:
        si, ei = seg["start_idx"], seg["end_idx"]
        duration_s = float(time[ei] - time[si]) if ei > si else 0.0
        vam = (seg["gain_m"] / duration_s * 3600.0) if duration_s > 0 else 0.0
        enriched.append({**seg, "duration_s": round(duration_s), "vam": round(vam)})
    return enriched


def compute_hr_drift(
    hr: np.ndarray,
    gap_s_per_m: np.ndarray,
) -> Optional[dict]:
    """
    Aerobic decoupling (Pa:HR), the TrainingPeaks / Uphill Athlete convention.

    The efficiency ratio is output per heartbeat — speed / HR — compared
    between the first and second half:

        decoupling % = (ratio_first - ratio_second) / ratio_first * 100

    POSITIVE means drift: the second half cost more heartbeats per unit of
    speed (you slowed at the same HR, or held pace at a higher HR). Uphill
    Athlete's aerobic-threshold test treats under 5% as a pass.

    `gap_s_per_m` is pace, so speed is its reciprocal. Returns None when there
    is not enough data.
    """
    n = len(hr)
    if n < 60:
        return None

    mid = n // 2
    hr1 = float(np.mean(hr[:mid]))
    hr2 = float(np.mean(hr[mid:]))
    gap1 = float(np.mean(gap_s_per_m[:mid]))
    gap2 = float(np.mean(gap_s_per_m[mid:]))

    if gap1 <= 0 or gap2 <= 0 or hr1 <= 0 or hr2 <= 0:
        return None

    ratio1 = (1.0 / gap1) / hr1          # speed per heartbeat
    ratio2 = (1.0 / gap2) / hr2
    decoupling_pct = ((ratio1 - ratio2) / ratio1) * 100.0

    return {
        "hr_first_half": round(hr1, 1),
        "hr_second_half": round(hr2, 1),
        "gap_first_half_s_per_km": round(gap1 * 1000, 1),
        "gap_second_half_s_per_km": round(gap2 * 1000, 1),
        "decoupling_pct": round(decoupling_pct, 1),
    }
