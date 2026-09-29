"""
WKO5 `_ragpace` (grade-adjusted pace channel), NGP, rTSS duration and pace EF
— reconstructed from WKO5.exe 5.0.587 (`_ragpace` @0x65b8e0,
calculateRunPaceMetrics @0x6565e0). VERIFIED against the athlete index
(2026-09-29): NGP (4230) 568/582 bit-exact and 582/582 within 2.2e-6 (float
summation order), rTSS duration (4249) 578/582, pace EF (4251) 561/561.

`_ragpace` runs on its OWN 1 s grid built from (elapsedtime, _elevation,
speed). Per grid step: the elevation slope gives `rise`, speed gives `dist`,
and grade = rise / horizontal. A 30 s time-weighted mean grade feeds the ACSM
adjustment (0.19v + 0.9vg)/0.19; `_ragpace` is the 4th root of the 30 s mean
of adj^4.

    NGP (min/km)  = 1000 / time-weighted mean of _ragpace
    rTSS duration = total grid time with a valid _ragpace
    pace EF       = round(mean _ragpace / average heart rate, 2)
"""
from __future__ import annotations

import collections
import math
from typing import Optional, Sequence

DBL_MIN = 2.2250738585072014e-308
WINDOW = 30.0


def _is_zero(x: float) -> bool:
    """WKO5's almost-equal(x, 0) is effectively 'exactly zero': float residue
    left in a running window sum counts as NON-zero. Do not relax this."""
    return abs(x) < DBL_MIN


class _Window:
    """Time-weighted trailing window; pops while total - front >= span."""

    def __init__(self, span: float = WINDOW):
        self.q = collections.deque()
        self.total = self.wv = self.sv = 0.0
        self.span = span

    def push(self, w: float, v: Optional[float]) -> None:
        self.q.append((w, v))
        self.total += w
        if v is not None:
            self.wv += w
            self.sv += w * v
        while self.q and self.total - self.q[0][0] >= self.span:
            fw, fv = self.q.popleft()
            self.total -= fw
            if fv is not None:
                self.wv -= fw
                self.sv -= fw * fv

    def mean(self) -> Optional[float]:
        return None if _is_zero(self.wv) else self.sv / self.wv


def ragpace(t: Sequence[Optional[float]], elev: Sequence[Optional[float]],
            speed: Sequence[Optional[float]]) -> tuple[list[float], list[Optional[float]]]:
    """(grid times, _ragpace values). `elev` is the SMOOTHED _elevation channel."""
    out_t: list[float] = []
    out_v: list[Optional[float]] = []
    tprev = 0.0
    eprev = None
    wg, wp = _Window(), _Window()
    for ti, ei, si in zip(t, elev, speed):
        if ti is None:
            continue
        cnt = 0
        tg = min(ti, tprev + 1.0)
        slope = 0.0
        eg = ei
        if eprev is not None and ei is not None:
            slope = (ei - eprev) / (ti - tprev)
            eg = slope + eprev
        while ti >= tg:
            dt = tg - tprev
            tprev = tg
            dist = grade = None
            if si is not None and ei is not None:
                eprev = eg
                dist = si * dt / 3.6
                rise = slope
                if dist >= rise:
                    sq = dist * dist - rise * rise
                    if sq >= 0:
                        horiz = math.sqrt(sq)
                        if horiz >= 0.01 and horiz > rise:
                            grade = rise / horiz
            else:
                eprev = None
                if cnt >= 30:
                    tg = ti
            wg.push(dt, grade)
            g = wg.mean()
            val = None
            if dist is not None and g is not None and dt:
                v = dist * 60.0 / dt
                val = ((v * 0.19 + v * 0.9 * g) / 0.19) ** 4
            wp.push(dt, val)
            m = wp.mean()
            r = None
            if m is not None:
                m = abs(m)
                if not _is_zero(m):
                    r = m ** 0.25
            out_t.append(tg)
            out_v.append(r)
            if eg is not None and slope != 0.0:     # advance interpolated elevation
                nxt = eg + slope
                if (slope > 0 and nxt >= ei) or (slope <= 0 and not ei < nxt):
                    slope = ei - eg
                eg += slope
            if abs(tg - ti) < 1e-10:
                break
            tg = min(tg + 1.0, ti)
            if ti - tg < 0.001:
                tg = ti
            cnt += 1
        tprev = tg
    return out_t, out_v


def ngp(t, elev, speed) -> tuple[Optional[float], float, Optional[float]]:
    """(NGP in min/km, rTSS duration in s, mean _ragpace in m/min)."""
    gt, gv = ragpace(t, elev, speed)
    s = total = 0.0
    prev = 0.0
    for ti, v in zip(gt, gv):
        dt, prev = ti - prev, ti
        if v is None:
            continue
        s += dt * v
        total += dt
    if total <= 0 or _is_zero(total):
        return None, total, None
    mean = s / total
    return (1000.0 / mean if not _is_zero(mean) else None), total, mean


def run_tss(duration_s: float, tpace: Optional[float], ngp_min_km: Optional[float]) -> Optional[float]:
    """rTSS for a run without power: (d/60)^1.025 * IF^2 / 60 * 100,
    IF = threshold pace / NGP (both min/km)."""
    if not tpace or not ngp_min_km or duration_s <= 0:
        return None
    iff = tpace / ngp_min_km
    return (duration_s / 60.0) ** 1.025 * iff * iff / 60.0 * 100.0
