"""
Sustained-climb detection.

Mountain fitness is climbing fitness, and the climbs you do on local hills are
the same capability a 百岳 trip draws on. Treating each sustained climb as its
own data point — rather than each activity — roughly doubles the evidence for
this athlete (≈60-80 climbs a year from ≈40 trail runs), which is what makes a
climbing-ability trend meaningful when trail and 百岳 days are sparse.

A climb starts at a low point and runs to the highest point reached before the
elevation drops more than `tolerance` below it. Small dips (a flat or a short
descent inside a long climb) are absorbed; the tolerance grows with the climb
(5% of the gain so far, at least `abs_tolerance`).

Use the SMOOTHED elevation (`_elevation`) — raw GPS/barometric jitter would
split one climb into many and inflate gain.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Sequence


@dataclass
class Climb:
    start_index: int
    end_index: int
    t_start: float           # s from workout start
    t_end: float
    duration_s: float
    distance_km: float       # horizontal
    gain_m: float
    grade: float             # gain / horizontal distance
    start_elev_m: float
    top_elev_m: float
    vam_m_per_h: float       # vertical ascent rate
    avg_hr: Optional[float]  # time-weighted over the climb
    max_hr: Optional[float]
    hr_per_100m: Optional[float]   # heartbeats per 100 m climbed (lower = more economical)

    def to_dict(self) -> dict:
        return asdict(self)


def detect_climbs(t: Sequence[Optional[float]], dist_km: Sequence[Optional[float]],
                  elev_m: Sequence[Optional[float]], hr: Optional[Sequence[Optional[float]]] = None,
                  min_gain_m: float = 80.0, min_grade: float = 0.03,
                  abs_tolerance_m: float = 10.0, rel_tolerance: float = 0.05,
                  moving: Optional[Sequence[bool]] = None) -> list[Climb]:
    """Find sustained climbs. Arrays must be sample-aligned; None = no data.

    `moving` is an optional per-sample mask. When given, a climb's duration —
    and so its VAM — counts only moving samples, so a climb that spans a night
    in camp or a long rest reports the pace you actually climbed at.
    """
    n = len(elev_m)
    out: list[Climb] = []
    i = 0
    while i < n:
        if elev_m[i] is None or dist_km[i] is None or t[i] is None:
            i += 1
            continue
        start = peak = i
        j = i
        while j + 1 < n:
            e = elev_m[j + 1]
            if e is None:
                j += 1
                continue
            if e > elev_m[peak]:
                peak = j + 1
            elif peak == start and e <= elev_m[start]:
                # still on the flat or descending into the climb's base: the
                # climb starts at the LAST low point, not the first
                start = peak = j + 1
            else:
                gain = elev_m[peak] - elev_m[start]
                if elev_m[peak] - e > max(abs_tolerance_m, rel_tolerance * gain):
                    break
            j += 1
        climb = _measure(t, dist_km, elev_m, hr, start, peak, moving)
        if climb and climb.gain_m >= min_gain_m and climb.grade >= min_grade:
            out.append(climb)
        i = peak + 1 if peak > i else i + 1
    return out


def _measure(t, dist_km, elev_m, hr, a: int, b: int, moving=None) -> Optional[Climb]:
    if b <= a or t[a] is None or t[b] is None or dist_km[a] is None or dist_km[b] is None:
        return None
    if moving is None:
        duration = t[b] - t[a]
    else:
        duration, prev = 0.0, t[a]
        for k in range(a + 1, b + 1):
            if t[k] is None:
                continue
            if k < len(moving) and moving[k]:
                duration += t[k] - prev
            prev = t[k]
    horiz_km = dist_km[b] - dist_km[a]
    gain = elev_m[b] - elev_m[a]
    if duration <= 0 or horiz_km <= 0 or gain <= 0:
        return None
    avg_hr = max_hr = beats = None
    if hr is not None:
        num = den = 0.0
        peak_hr = None
        prev_t = t[a]
        for k in range(a + 1, b + 1):
            if t[k] is None:
                continue
            dt, prev_t = t[k] - prev_t, t[k]
            h = hr[k] if k < len(hr) else None
            if h is None or dt <= 0:
                continue
            if moving is not None and not (k < len(moving) and moving[k]):
                continue
            num += h * dt
            den += dt
            peak_hr = h if peak_hr is None else max(peak_hr, h)
        if den > 0:
            avg_hr, max_hr = num / den, peak_hr
            beats = num / 60.0                # bpm * s / 60 = beats
    return Climb(
        start_index=a, end_index=b, t_start=t[a], t_end=t[b], duration_s=duration,
        distance_km=horiz_km, gain_m=gain, grade=gain / (horiz_km * 1000.0),
        start_elev_m=elev_m[a], top_elev_m=elev_m[b],
        vam_m_per_h=gain / duration * 3600.0,
        avg_hr=avg_hr, max_hr=max_hr,
        hr_per_100m=(beats / gain * 100.0) if beats is not None else None,
    )
