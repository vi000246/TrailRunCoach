"""
Trail / mountain panels.

vam_vs_grade
    One point per sustained climb (backend/engine/algorithms/climbs.py):
    x = average grade (%), y = VAM (m/h). The fit over the last 90 days is a
    least-squares line VAM = a + b * grade — on hills steeper than ~10 % VAM
    grows roughly linearly with grade for the same effort, so comparing this
    season's line with an earlier window shows climbing progress without
    needing the same hill twice [coach practice: Uphill Athlete VAM
    benchmarks; the linear form is ours]. Points can be restricted to an HR
    band so the comparison is at similar effort.

downhill
    Time / distance / vertical loss spent below -10 % grade: the eccentric
    load that makes quads sore on long descents. Weekly sums of it are the
    "downhill load" chart.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional, Sequence

import numpy as np

DOWNHILL_GRADE = -0.10
MAX_DT = 30.0


def fit_line(xs: Sequence[float], ys: Sequence[float]) -> Optional[dict]:
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or np.ptp(x) == 0:
        return None
    b, a = np.polyfit(x, y, 1)
    pred = a + b * x
    ss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(((y - pred) ** 2).sum()) / ss if ss > 0 else 1.0
    return {"a": float(a), "b": float(b), "r2": r2, "n": int(len(x)),
            "x0": float(x.min()), "x1": float(x.max())}


def vam_vs_grade(climbs: Iterable[dict], today: dt.date, window_days: int = 90,
                 compare_days: Optional[int] = None) -> dict:
    """climbs: dicts with date (dt.date), grade (fraction), vam_m_per_h,
    duration_s, gain_m, avg_hr, workout (index). Returns recent / earlier point
    sets and a fit for each."""
    recent, earlier = [], []
    lo = today - dt.timedelta(days=window_days)
    lo2 = lo - dt.timedelta(days=compare_days or window_days)
    for c in climbs:
        d = c["date"]
        p = {"x": c["grade"] * 100.0, "y": c["vam_m_per_h"], "date": d.isoformat(),
             "gain_m": c.get("gain_m"), "duration_s": c.get("duration_s"),
             "hr": c.get("avg_hr"), "workout": c.get("workout")}
        if lo < d <= today:
            recent.append(p)
        elif lo2 < d <= lo:
            earlier.append(p)

    def fit(ps):
        return fit_line([p["x"] for p in ps], [p["y"] for p in ps])

    return {"recent": recent, "earlier": earlier, "fit_recent": fit(recent),
            "fit_earlier": fit(earlier), "window_days": window_days}


def vam_at(fit: Optional[dict], grade_pct: float) -> Optional[float]:
    return None if not fit else fit["a"] + fit["b"] * grade_pct


def downhill(dt_s, grade, distance_km=None, elevation_m=None,
             threshold: float = DOWNHILL_GRADE) -> dict:
    d = np.asarray(dt_s, dtype=float)
    n = len(d)
    g = np.asarray(grade, dtype=float)[:n]
    ok = np.isfinite(d) & (d > 0) & (d <= MAX_DT) & np.isfinite(g) & (g < threshold)
    out = {"time_s": float(d[ok].sum()), "distance_km": None, "loss_m": None}
    if distance_km is not None:
        s = np.diff(np.asarray(distance_km, dtype=float)[:n], prepend=np.nan)
        s = np.where(np.isfinite(s) & (s >= 0) & (s < 1), s, 0.0)
        out["distance_km"] = float(s[ok].sum())
    if elevation_m is not None:
        e = np.diff(np.asarray(elevation_m, dtype=float)[:n], prepend=np.nan)
        e = np.where(np.isfinite(e) & (e < 0), -e, 0.0)
        out["loss_m"] = float(e[ok].sum())
    return out


def weekly_sum(items: Iterable[tuple[dt.date, dict]], begin: dt.date, end: dt.date,
               keys=("time_s", "distance_km", "loss_m")) -> list[dict]:
    start = begin - dt.timedelta(days=begin.weekday())
    weeks: dict[dt.date, dict] = {}
    s = start
    while s <= end:
        weeks[s] = {k: 0.0 for k in keys}
        s += dt.timedelta(days=7)
    for d, v in items:
        wk = d - dt.timedelta(days=d.weekday())
        if wk in weeks:
            for k in keys:
                if v.get(k) is not None:
                    weeks[wk][k] += v[k]
    return [{"week": w.isoformat(), **v} for w, v in sorted(weeks.items())]
