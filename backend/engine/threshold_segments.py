"""
閾值段 (SP-801): the stretches of a run or hike in Zone 3 for ≥ 10 min, whatever the
session's format — a long climb, a tempo run, a 長天 (`hard_long`) — so threshold work
shows without being an interval session. Display only: the plan match, the interval
count and the hard-day count don't read this.

Zone 3 = power OR heart rate (interval_reps.z3_on, user 2026-10-20). A hike, or a run's
segment steeper than 8 % on average, is judged on VAM, not power: Stryd doesn't see the
vertical work (10/18 北插天山: 29 W on a 53 % grade at 107 % LTHR) — user 2026-10-20 for
hikes; the 8 % is session_stimulus.GRADE_MAX, where it stops trusting power on a trail.

Per segment (`basis` "power" | "vam"): duration, power / % CP (power), HR / % LTHR, grade,
VAM, and on power only the second half's power vs the first and the HR drift — power ÷ HR
of the second half vs the first (Pa:HR decoupling; on VAM the terrain swings it ±30–80 %).
The trend: the earlier segments of the same basis within ±30 % of the length of this run's
longest one, by date — W per bpm, or VAM per bpm.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

SEG_MIN_S = 600         # user 2026-10-20: a segment is ≥ 10 min (as 「Z3 ≥ 10 分算強度」)
SEG_BRIDGE_S = 30       # 推估: a dip under the line ≤ 30 s (a stile, a rock) doesn't split a segment
MATCH_LEN = 0.30        # 推估: the trend compares segments within ±30 % of the length
TREND_DAYS = 180        # the trend's window
TREND_N = 30            # the earlier segments shown, most recent
HIKE_MIN_GRADE = 0.05   # 推估: a VAM segment joins the trend when it climbs ≥ 5 % on average
POWER_MAX_GRADE = 0.08  # session_stimulus.GRADE_MAX: steeper on average → the segment is read on VAM
CP_HINT_S = 1200        # 推估: a ≥ 20-min segment …
CP_HINT_PCT = 0.95      # … averaging ≥ 95 % CP says the CP may be set low (a 20-min effort ≈ 1.03 × CP)
CACHE_KEY = "threshold_segs_v1"


def _kind(cat: str) -> str:
    return "hike" if cat == "hike" else "run"


def _mean(x: Optional[np.ndarray], a: int, b: int) -> Optional[float]:
    if x is None:
        return None
    seg = x[a:b]
    return float(np.nanmean(seg)) if np.isfinite(seg).any() else None


def _vam(elev: Optional[np.ndarray], a: int, b: int) -> Optional[float]:
    """m/h climbed (net) between a and b."""
    if elev is None or b - a < 60:
        return None
    e0, e1 = _mean(elev, a, a + 30), _mean(elev, b - 30, b)
    if e0 is None or e1 is None:
        return None
    return (e1 - e0) / ((b - a) / 3600.0)


def _decouple(out: Optional[np.ndarray], hr: Optional[np.ndarray], a: int, b: int) -> Optional[float]:
    """(output ÷ HR first half) ÷ (second half) − 1: > 0 = the HR rose for the same output."""
    if out is None or hr is None:
        return None
    mid = (a + b) // 2
    o1, o2, h1, h2 = _mean(out, a, mid), _mean(out, mid, b), _mean(hr, a, mid), _mean(hr, mid, b)
    if not all(v is not None and v > 0 for v in (o1, o2, h1, h2)):
        return None
    return (o1 / h1) / (o2 / h2) - 1.0


def segments_of(t, power, hr, elev, dist, cp: Optional[float], lthr: Optional[float], cat: str,
                grade=None, cadence_spm=None) -> dict:
    """{"kind", "cp", "lthr", "segs": [...]} for one activity's samples (no dataset: tests).
    elev and dist in metres, grade a fraction per sample (rgrade), cadence in spm."""
    from backend.engine import interval_reps as IR
    from backend.engine import session_stimulus as SS
    kind = _kind(cat)
    out = {"kind": kind, "cp": cp, "lthr": lthr, "segs": []}
    z = IR.z3_on(t, power, hr, cp, lthr, grade, cadence_spm)
    if z is None:
        return out
    g = z["g"]
    tt = np.asarray(t, dtype=float)
    e = SS._roll(SS._grid(tt, elev, g), 30)
    d = SS._grid(tt, dist, g)
    p30 = SS._roll(z["p"], 30) if z["p"] is not None else None
    for k, bt in enumerate(IR.z3_bouts(t, power, hr, cp, lthr, grade, cadence_spm, SEG_MIN_S, SEG_BRIDGE_S, z=z), 1):
        a = int(bt["start_s"])
        b = a + int(bt["duration_s"])
        hm = _mean(z["h"], a, b)
        vam = _vam(e, a, b)
        km = None
        gr = None
        if d is not None and np.isfinite(d[a]) and np.isfinite(d[b - 1]):
            km = (float(d[b - 1]) - float(d[a])) / 1000.0
            if km > 0.05 and e is not None and vam is not None:
                gr = vam * ((b - a) / 3600.0) / (km * 1000.0)
        basis = "power" if kind == "run" and z["p"] is not None and (gr is None or gr <= POWER_MAX_GRADE) else "vam"
        pw = _mean(z["p"], a, b) if basis == "power" else None
        fade = drift = None
        if basis == "power":
            mid = (a + b) // 2
            p1, p2 = _mean(z["p"], a, mid), _mean(z["p"], mid, b)
            fade = (p2 / p1 - 1.0) if p1 and p2 else None
            drift = _decouple(p30, z["h"], a, b)
        eff = None
        if hm:
            if basis == "power" and pw:
                eff = pw / hm
            elif basis == "vam" and vam is not None and (gr or 0) >= HIKE_MIN_GRADE:
                eff = vam / hm
        out["segs"].append({
            "k": k, "start_s": float(a), "duration_s": float(b - a), "by": bt["by"], "basis": basis,
            "power": pw, "pct_cp": (pw / cp) if pw and cp else None,
            "hr": hm, "pct_lthr": (hm / lthr) if hm and lthr else None,
            "km": km, "grade": gr, "vam": vam, "fade": fade, "drift": drift, "eff": eff})
    return out


def _compute(ds, w) -> dict:
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    s = WR._samples(ds, w)
    m = WR.measure(ds, w) or {}
    cat = category(w)
    # a hike synced as its own sport has no LTHR / CP of its own: the run's, as its hrTSS (SP-63)
    lthr = m.get("lthr") or _run_setting(ds, w, "runthr")
    cp = m.get("cp") or _run_setting(ds, w, "runftp")
    if s is None or cat not in WR.QUALITY_CATEGORIES:            # road, trail, hike (a hike is sport "hike")
        return {"kind": _kind(cat), "cp": cp, "lthr": lthr, "segs": []}
    cad = None if s.get("cadence") is None else np.asarray(s["cadence"], dtype=float) * 2.0
    dist_m = None if s.get("dist") is None else np.asarray(s["dist"], dtype=float) * 1000.0   # elapseddistance is km
    return segments_of(s["t"], s.get("power"), s.get("hr"), s.get("elev"), dist_m, cp, lthr,
                       cat, WR._rgrade(ds, w, s), cad)


def _run_setting(ds, w, name: str) -> Optional[float]:
    try:
        v = ds.setting(name, w.day)
    except Exception:                       # noqa: BLE001 — a dataset without dated settings
        return None
    return float(v) if v else None


def segments(ds, w) -> dict:
    """segments_of on an activity, disk-memoised per activity (ds.cached_series) and in ds.memo."""
    memo = getattr(ds, "memo", None)
    key = ("threshold_segs", w.idx)
    if isinstance(memo, dict) and key in memo:
        return memo[key]
    cache = getattr(ds, "cached_series", None)
    r = cache(CACHE_KEY, w, lambda: _compute(ds, w)) if cache is not None else _compute(ds, w)
    if isinstance(memo, dict):
        memo[key] = r
    return r


def cp_hint(seg: dict) -> bool:
    return (seg.get("pct_cp") or 0) >= CP_HINT_PCT and seg["duration_s"] >= CP_HINT_S


def trend(ds, w, cur: dict) -> dict:
    """{"basis", "ref_s", "points": [{"date", "eff", "duration_s", "current"}]}: this run's segments
    and the earlier ones of the basis of its longest one, within ±MATCH_LEN of that length."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    segs = [x for x in cur["segs"] if x.get("eff")]
    if not segs:
        return {"basis": None, "ref_s": None, "points": []}
    top = max(segs, key=lambda x: x["duration_s"])
    ref, basis = top["duration_s"], top["basis"]
    near = lambda x: x.get("basis") == basis and abs(x["duration_s"] - ref) <= MATCH_LEN * ref and x.get("eff")
    d0 = math.floor(w.day)
    pts = []
    for x in sorted(ds.workouts, key=lambda q: q.day):
        if x.idx == w.idx or category(x) not in WR.QUALITY_CATEGORIES or not (d0 - TREND_DAYS <= math.floor(x.day) < d0):
            continue
        try:
            r = segments(ds, x)
        except Exception:                   # noqa: BLE001 — an unreadable file: left out of the trend
            continue
        day = WR._wdate(x).isoformat()
        pts += [{"date": day, "eff": sg["eff"], "duration_s": sg["duration_s"], "current": False}
                for sg in r.get("segs") or [] if near(sg)]
    pts = pts[-TREND_N:]
    day = WR._wdate(w).isoformat()
    pts += [{"date": day, "eff": sg["eff"], "duration_s": sg["duration_s"], "current": True} for sg in segs if near(sg)]
    WR._flush(ds)
    return {"basis": basis, "ref_s": ref, "points": pts}
