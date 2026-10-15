"""
Any two activities side by side (SP-625; decision SP-475).

Two runs on different courses — two half marathons, say — share no distance
axis, so this compares what does not need one:

  summary   time, distance, average pace, HR, power, climbing, training load,
            each with B − A;
  splits    the time of every whole kilometre (and the last part km);
  overlay   pace / HR / power per 0.5 % of the distance, so the x axis can be
            the share completed (default), the distance or the elapsed time;
  grades    pace by gradient group: uphill / flat / downhill, every 100 m of
            distance classed by its gradient.

Two runs of the same route or segment are compared point by point instead
(backend/api/routes.py `compare`, chosen by backend/api/activity_compare.py).

Every input is a plain array (seconds, km, m, bpm, W; NaN = no data), so the
functions run without a dataset. Pace and the splits use elapsed time: a stop
counts, as on a race clock.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

PCT_STEP = 0.5            # overlay: one point per this share (%) of the distance
PACE_WIN = 3              # overlay pace: centred over this many steps (1.5 % of the distance)
GRADE_BIN_M = 100.0       # 推估 gradient groups: the distance is cut into pieces this long
GRADE_UP = 3.0            # 推估 % a piece steeper than this is uphill ...
GRADE_DOWN = -3.0         # 推估 ... and below this downhill; in between flat
PACE_MAX = 60.0           # min/km: slower than this is standing, not a pace (no point drawn)
GROUPS = ("up", "flat", "down")


def _arr(v: Optional[Sequence]) -> Optional[np.ndarray]:
    if v is None:
        return None
    a = np.asarray([np.nan if x is None else x for x in v], dtype=float)
    return a if len(a) else None


def _r(v, nd: int = 1):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


class Series:
    """One activity's samples, cleaned: time and distance only where both are
    finite, the distance made non-decreasing (a device's small step back is
    noise, not running backwards)."""

    def __init__(self, t, d_km, elev=None, hr=None, power=None):
        t, d = _arr(t), _arr(d_km)
        n = 0 if t is None or d is None else min(len(t), len(d))
        ok = np.zeros(0, bool) if not n else np.isfinite(t[:n]) & np.isfinite(d[:n])
        self.t = t[:n][ok] if n else np.zeros(0)
        self.d = np.maximum.accumulate(d[:n][ok]) if n and ok.any() else np.zeros(0)

        def chan(x):
            x = _arr(x)
            if x is None or len(x) < n:
                return None
            x = x[:n][ok]
            return x if np.isfinite(x).any() else None
        self.elev, self.hr, self.power = chan(elev), chan(hr), chan(power)
        if self.hr is not None:
            self.hr = np.where(self.hr > 0, self.hr, np.nan)
        if self.power is not None:
            self.power = np.where(self.power >= 0, self.power, np.nan)
        if len(self.t):
            self.t = self.t - self.t[0]

    @property
    def ok(self) -> bool:
        return len(self.t) >= 2 and self.dist_km > 0.05

    @property
    def dist_km(self) -> float:
        return float(self.d[-1] - self.d[0]) if len(self.d) else 0.0

    @property
    def duration_s(self) -> float:
        return float(self.t[-1]) if len(self.t) else 0.0

    def time_at(self, x_km) -> np.ndarray:
        """Elapsed time when the distance first reaches x (linear between the two samples around it)."""
        x = np.atleast_1d(np.asarray(x_km, dtype=float))
        i = np.clip(np.searchsorted(self.d, x, side="left"), 1, len(self.d) - 1)
        d0, d1, t0, t1 = self.d[i - 1], self.d[i], self.t[i - 1], self.t[i]
        with np.errstate(invalid="ignore", divide="ignore"):
            f = np.where(d1 > d0, (x - d0) / (d1 - d0), 1.0)
        out = t0 + np.clip(f, 0.0, 1.0) * (t1 - t0)
        out[x <= self.d[0]] = 0.0
        out[x >= self.d[-1]] = self.t[-1] if len(x) else 0.0
        return out

    def _cum(self, ch: Optional[np.ndarray]):
        """Cumulative time-weighted sums (Σv·dt, Σdt over samples with a value), per sample."""
        if ch is None:
            return None
        dt = np.diff(self.t, prepend=self.t[0])
        good = np.isfinite(ch)
        return np.cumsum(np.where(good, ch * dt, 0.0)), np.cumsum(np.where(good, dt, 0.0))

    def means(self, ch: Optional[np.ndarray], x_edges_km: np.ndarray) -> np.ndarray:
        """Time-weighted mean of a channel over each distance interval (NaN = none)."""
        out = np.full(max(0, len(x_edges_km) - 1), np.nan)
        c = self._cum(ch)
        if c is None or not len(out):
            return out
        num, den = c
        # the samples of an interval: those whose distance is past its start, up to its end
        i = np.searchsorted(self.d, x_edges_km, side="right") - 1
        i = np.clip(i, 0, len(self.d) - 1)
        dn, dd = np.diff(num[i]), np.diff(den[i])
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(dd > 0, dn / dd, np.nan)
        return out

    def mean(self, ch: Optional[np.ndarray]) -> Optional[float]:
        c = self._cum(ch)
        if c is None or c[1][-1] <= 0:
            return None
        return float(c[0][-1] / c[1][-1])


def summary(s: Series, metrics: Optional[dict] = None) -> dict:
    """Totals of one activity. The dataset's own duration / distance / climbing /
    load win when given (the activity list shows those); else from the samples."""
    m = metrics or {}
    dur = m.get("duration") or (s.duration_s if s.ok else None)
    dist = m.get("distance") or (s.dist_km if s.ok else None)
    pace = dur / 60.0 / dist if dur and dist else None
    climb = m.get("climbing")
    if climb is None and s.elev is not None:
        e = s.elev[np.isfinite(s.elev)]
        climb = float(np.clip(np.diff(e), 0, None).sum()) if len(e) > 1 else None
    return {"duration_s": _r(dur, 0), "distance_km": _r(dist, 2), "pace_min_km": _r(pace, 2),
            "avg_hr": _r(s.mean(s.hr), 0), "avg_power": _r(s.mean(s.power), 0),
            "climbing_m": _r(climb, 0), "load": _r(m.get("load"), 0)}


def splits(s: Series) -> list[dict]:
    """One row per whole kilometre, plus the part km at the end (≥ 50 m)."""
    if not s.ok:
        return []
    L = s.dist_km
    edges = list(np.arange(0.0, np.floor(L) + 1e-9, 1.0)[: int(np.floor(L)) + 1])
    if L - edges[-1] >= 0.05:
        edges.append(L)
    edges = np.asarray(edges, dtype=float) + s.d[0]
    if len(edges) < 2:
        return []
    tt = s.time_at(edges)
    hr = s.means(s.hr, edges)
    pw = s.means(s.power, edges)
    out = []
    for k in range(len(edges) - 1):
        km = float(edges[k + 1] - edges[k])
        sec = float(tt[k + 1] - tt[k])
        out.append({"km": k + 1, "len_km": round(km, 3), "time_s": round(sec, 1),
                    "pace_min_km": _r(sec / 60.0 / km if km > 0 else None, 2),
                    "avg_hr": _r(hr[k], 0), "avg_power": _r(pw[k], 0)})
    return out


def overlay(s: Series, step_pct: float = PCT_STEP) -> dict:
    """Points every `step_pct` % of the distance: share, distance, elapsed time,
    pace (centred over PACE_WIN steps), HR and power (time-weighted over the step)."""
    if not s.ok:
        return {"pct": [], "dist_km": [], "t_s": [], "pace_min_km": [], "hr": [], "power": []}
    pct = np.arange(0.0, 100.0 + 1e-9, step_pct)
    x = s.d[0] + pct / 100.0 * s.dist_km
    tt = s.time_at(x)
    hr = s.means(s.hr, x)
    pw = s.means(s.power, x)
    h = PACE_WIN // 2
    pace = np.full(len(x), np.nan)
    for k in range(1, len(x)):
        lo, hi = max(0, k - 1 - h), min(len(x) - 1, k + h)
        dd = x[hi] - x[lo]
        if dd > 0:
            pace[k] = (tt[hi] - tt[lo]) / 60.0 / dd
    pace[0] = pace[1] if len(pace) > 1 else np.nan
    pace[(pace > PACE_MAX) | (pace <= 0)] = np.nan
    # a step's HR / power sits at its end; the first point takes the first step's
    hr = np.concatenate(([hr[0] if len(hr) else np.nan], hr))
    pw = np.concatenate(([pw[0] if len(pw) else np.nan], pw))
    j = lambda a, nd: [_r(v, nd) for v in a]
    return {"pct": j(pct, 1), "dist_km": j(x - s.d[0], 3), "t_s": j(tt, 0), "pace_min_km": j(pace, 2),
            "hr": j(hr, 0) if s.hr is not None else [], "power": j(pw, 0) if s.power is not None else []}


def grade_groups(s: Series, bin_m: float = GRADE_BIN_M) -> Optional[dict]:
    """Pace per gradient group. The distance is cut into `bin_m` pieces; a
    piece's gradient = elevation change ÷ length; its time and HR go to its group."""
    if not s.ok or s.elev is None:
        return None
    L = s.dist_km
    n = int(np.floor(L * 1000.0 / bin_m))
    if n < 1:
        return None
    edges = s.d[0] + np.arange(n + 1) * bin_m / 1000.0
    tt = s.time_at(edges)
    # the elevation at each edge: interpolated over the samples that have one
    good = np.isfinite(s.elev)
    if good.sum() < 2:
        return None
    ee = np.interp(edges, s.d[good], s.elev[good])
    grade = np.diff(ee) / bin_m * 100.0
    sec = np.diff(tt)
    hr = s.means(s.hr, edges)
    out = {}
    total = n * bin_m / 1000.0
    for g in GROUPS:
        m = grade > GRADE_UP if g == "up" else grade < GRADE_DOWN if g == "down" else \
            (grade >= GRADE_DOWN) & (grade <= GRADE_UP)
        km = float(m.sum() * bin_m / 1000.0)
        t = float(sec[m].sum())
        hw = sec[m] * np.isfinite(hr[m])
        out[g] = {"km": round(km, 2), "share": _r(km / total if total else None, 3), "time_s": round(t, 0),
                  "pace_min_km": _r(t / 60.0 / km if km > 0 else None, 2),
                  "avg_grade": _r(float(np.mean(grade[m])) if m.any() else None, 1),
                  "avg_hr": _r(float(np.nansum(hr[m] * sec[m]) / hw.sum()) if hw.sum() > 0 else None, 0)}
    return out


def diff(a: dict, b: dict) -> dict:
    """B − A of every number both summaries have."""
    return {k: (_r(b[k] - a[k], 2) if a.get(k) is not None and b.get(k) is not None else None) for k in a}


def compare(a: Series, b: Series, meta_a: Optional[dict] = None, meta_b: Optional[dict] = None) -> dict:
    sa, sb = summary(a, meta_a), summary(b, meta_b)
    return {
        "summary": {"a": sa, "b": sb, "diff": diff(sa, sb)},
        "splits": {"a": splits(a), "b": splits(b)},
        "overlay": {"a": overlay(a), "b": overlay(b)},
        "grades": {"a": grade_groups(a), "b": grade_groups(b),
                   "bin_m": GRADE_BIN_M, "up": GRADE_UP, "down": GRADE_DOWN},
    }
