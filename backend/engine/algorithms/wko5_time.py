"""
WKO5 time & distance metrics — reconstructed from WKO5.exe 5.0.587
(@0x654370). VERIFIED against the athlete index (2026-09-29):
movingduration 789/790, pedalingduration 680/680, duration 1075/1075,
begindistance 780/780, distance 780/780.

dt_i = t[i] - t[i-1]; the first sample measures from the channel's base value
(wko4 packed field 119, 0 for elapsedtime).
"""
from __future__ import annotations

from typing import Optional, Sequence

# Minimum speed (km/h) that counts as moving, by sport family.
MOVING_SPEED_KMH = {"run": 1.609344498, "walk": 1.609344498, "bike": 3.218688996}
NON_DATA_CHANNELS = frozenset({"elapsedtime", "elapseddistance"})


def _dts(t: Sequence[Optional[float]], base: float = 0.0) -> list[float]:
    out, prev = [], base
    for ti in t:
        if ti is None:
            out.append(0.0)
            continue
        out.append(ti - prev)
        prev = ti
    return out


def moving_duration(t, speed, sport: str, distance=None, base: float = 0.0) -> float:
    """Time with speed above the sport's threshold (falls back to distance moving)."""
    thr = MOVING_SPEED_KMH.get(sport, 0.0)
    dts = _dts(t, base)
    if speed is not None:
        return sum(dt for dt, v in zip(dts, speed) if v is not None and v > thr)
    if distance is None:
        return 0.0
    total, prev = 0.0, None
    for dt, d in zip(dts, distance):
        if d is not None and prev is not None and d != prev:
            total += dt
        if d is not None:
            prev = d
    return total


def pedaling_duration(t, cadence, base: float = 0.0) -> float:
    return sum(dt for dt, v in zip(_dts(t, base), cadence or []) if v is not None and v != 0)


def duration(t, channels: dict, base: float = 0.0) -> float:
    """Time where ANY data channel has a value (excluding the axis channels)."""
    data = [v for name, v in channels.items() if name not in NON_DATA_CHANNELS]
    if not data:
        return 0.0
    return sum(dt for i, dt in enumerate(_dts(t, base))
               if any(i < len(c) and c[i] is not None for c in data))


def distance_range(dist: Sequence[Optional[float]],
                   base: Optional[float] = None) -> tuple[Optional[float], Optional[float]]:
    """(begindistance, distance). begindistance is the channel's base value
    (packed field 119 — the odometer before the first sample), falling back to
    the first sample; distance is measured from there."""
    vals = [v for v in dist if v is not None]
    if not vals:
        return None, None
    begin = base if base is not None else vals[0]
    return begin, vals[-1] - begin
