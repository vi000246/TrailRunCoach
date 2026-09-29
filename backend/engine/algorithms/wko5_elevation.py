"""
WKO5 `_elevation` (the smoothed elevation channel climbing is measured on),
plus climbing / elevation change — reconstructed from WKO5.exe 5.0.587
(@0x65c670) and VERIFIED bit-exact on 628/628 files (2026-09-29).

Two passes (tau = 6, then 1.5) of a time-adaptive forward/backward EWMA over a
COPY of the elevation channel; every write back is quantized to the channel's
storage scale (1/10 m, half away from zero). A linear drift correction then
pins the first and last valid values back to the raw channel.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

TAUS = (6.0, 1.5)
ELEVATION_SCALE = 10.0     # wko4 stores elevation at 1/10 m


def _quant(v: float, scale: float) -> float:
    x = v * scale
    return (math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)) / scale


def smooth_elevation(t: Sequence[Optional[float]], e: Sequence[Optional[float]],
                     scale: float = ELEVATION_SCALE) -> list[Optional[float]]:
    n = len(e)
    if n <= 2 or all(v is None for v in e):
        return list(e)
    first_valid = lambda s: next((v for v in s if v is not None), None)
    last_valid = lambda s: next((v for v in reversed(s) if v is not None), None)
    x = list(e)
    for tau in TAUS:
        f = [None] * n
        f[0] = first_valid(x)
        for i in range(1, n):
            a = (t[i] - t[i - 1]) / tau
            f[i] = f[i - 1] if x[i] is None else (f[i - 1] + a * x[i]) / (a + 1.0)
        b = [None] * n
        b[n - 1] = last_valid(x)
        for i in range(n - 2, -1, -1):
            a = (t[i + 1] - t[i]) / tau
            b[i] = b[i + 1] if x[i] is None else (b[i + 1] + a * x[i]) / (a + 1.0)
        x = [_quant((f[i] + b[i]) * 0.5, scale) for i in range(n)]
    # Pin the smoothed endpoints back onto the raw ones, spreading the drift.
    d0 = first_valid(e) - x[0]
    d1 = last_valid(e) - x[n - 1]
    # The result lands back in a channel, so it is stored quantized.
    return [None if e[i] is None else _quant(x[i] + d0 + (d1 - d0) / (n - 1) * i, scale)
            for i in range(n)]


def climbing(elev: Sequence[Optional[float]]) -> float:
    """Total ascent over a (smoothed) elevation series."""
    total, prev = 0.0, None
    for v in elev:
        if v is None:
            continue
        if prev is not None and v > prev:
            total += v - prev
        prev = v
    return total


def descending(elev: Sequence[Optional[float]]) -> float:
    total, prev = 0.0, None
    for v in elev:
        if v is None:
            continue
        if prev is not None and v < prev:
            total += prev - v
        prev = v
    return total


def elevation_change(elev: Sequence[Optional[float]]) -> Optional[float]:
    vals = [v for v in elev if v is not None]
    return (vals[-1] - vals[0]) if vals else None
