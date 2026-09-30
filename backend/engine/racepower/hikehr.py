"""
HR-filtered steep-climb windows from 百岳 / hiking days (user decision
2026-09-30). Group hikes are paced by the group, so their total times, EP/h
and target-time predictions stay excluded; but a sustained climb at or above
the aerobic threshold is the athlete's own effort whatever the group does,
and it is the only high-altitude data there is.

Filter (user-specified, one constant HIKE_FILTER):
    100 m windows with moving speed > 1.5 km/h AND HR ≥ AeT, in runs of ≥ 3
    consecutive windows (≥ 300 m); of those only grade ≥ 10 % is kept (flats
    and descents dropped). Windows above VAM_MAX (the vertical-kilometre
    world-record rate) are dropped as elevation noise.
VAM = horizontal speed × grade × 3600 (m/h).

Uses, each with its n and evidence status:
    vam       VAM per HR band (AeT–0.95·LTHR, ≥ 0.95·LTHR) × grade band —
              descriptive (定義).
    altitude  the athlete's own altitude factor: ln VAM regressed on
              elevation inside cells of the same grade band and HR band
              (cell-demeaned, i.e. fixed effects) → % per 1000 m, compared
              with Wehrlin & Hallén 2006 (−6.3 % VO2max per 1000 m, 已驗證 in
              env.py). 自組; needs ≥ 30 windows over ≥ 800 m of elevation.
    fatigue   multi-day: HR at the same VAM, day n vs day 1 — per trip a
              least-squares line HR ~ VAM on day 1, the median residual on
              day n (bpm). No external source (F17), personal data only;
              needs ≥ 5 day-1 windows per trip.
    walking   the windows join the walking-speed model's steep bins
              (grade_model.HikeSpeed, g ≥ 10 %) — shrunk towards Tobler.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional, Sequence

import numpy as np

HIKE_FILTER = {"min_kmh": 1.5, "min_run": 3, "min_grade": 0.10}
# physical cap on a 100 m window: the Vertical Kilometer world record (Fully,
# 29:42 in 2017) is ≈ 2000 m/h; faster windows are GPS / elevation noise
VAM_MAX = 2000.0
GRADE_BANDS = ((0.10, 0.20), (0.20, 0.30), (0.30, 1.0))
ALT_MIN_N, ALT_MIN_RANGE_M = 30, 800.0
FAT_MIN_N = 5
WEHRLIN_PER_KM = 0.063
EVIDENCE = {
    "vam": "定義（描述統計）",
    "altitude": "自組（個人資料），對照 Wehrlin & Hallén 2006 −6.3 %/1000 m（已驗證）",
    "fatigue": "無外部來源（F17），僅個人資料",
    "walking": "Tobler 1993 形狀為先驗（經驗法則），個人陡坡分箱為自組",
}


def filter_windows(rows: Sequence[dict], aet: float) -> list[dict]:
    """rows = consecutive 100 m windows of one day ({"k", "g", "v", "hr", …})."""
    f = HIKE_FILTER
    ok = [r for r in rows if r.get("hr") is not None and r["v"] * 3.6 > f["min_kmh"] and r["hr"] >= aet]
    out, run = [], []
    for r in ok:
        if run and r.get("k") != run[-1].get("k", -10) + 1:
            if len(run) >= f["min_run"]:
                out.extend(run)
            run = []
        run.append(r)
    if len(run) >= f["min_run"]:
        out.extend(run)
    wins = [dict(r, vam=r["v"] * r["g"] * 3600.0) for r in out if r["g"] >= f["min_grade"]]
    return [w for w in wins if w["vam"] <= VAM_MAX]


def _band(g: float) -> Optional[str]:
    for lo, hi in GRADE_BANDS:
        if lo <= g < hi:
            return f"{lo:.0%}–{hi:.0%}" if hi < 1 else f"≥ {lo:.0%}"
    return None


def _hr_band(hr: float, aet: float, lthr: float) -> str:
    return "high" if hr >= 0.95 * lthr else "steady"


def vam_table(wins: Sequence[dict], aet: float, lthr: float) -> list[dict]:
    cells: dict = {}
    for w in wins:
        cells.setdefault((_hr_band(w["hr"], aet, lthr), _band(w["g"])), []).append(w["vam"])
    return [{"hr_band": hb, "grade_band": gb, "n": len(v), "vam_median": float(median(v)),
             "vam_p10": float(np.percentile(v, 10)), "vam_p90": float(np.percentile(v, 90))}
            for (hb, gb), v in sorted(cells.items(), key=lambda x: (x[0][0], x[0][1] or ""))]


def altitude_factor(wins: Sequence[dict], aet: float, lthr: float) -> dict:
    """ln VAM on elevation with (grade band × HR 5-bpm) fixed effects."""
    cells: dict = {}
    for w in wins:
        if w.get("z") is None or w["vam"] <= 0:
            continue
        cells.setdefault((_band(w["g"]), int(w["hr"] // 5)), []).append((w["z"] / 1000.0, math.log(w["vam"])))
    xs, ys = [], []
    for pts in cells.values():
        if len(pts) < 2:
            continue
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        xs += [p[0] - mx for p in pts]
        ys += [p[1] - my for p in pts]
    zs = [w["z"] for w in wins if w.get("z") is not None]
    rng = (max(zs) - min(zs)) if zs else 0.0
    out = {"n": len(xs), "z_range_m": rng, "wehrlin_pct_per_km": -WEHRLIN_PER_KM * 100, "pct_per_km": None,
           "evidence": EVIDENCE["altitude"], "enough": False}
    sxx = sum(x * x for x in xs)
    if len(xs) >= ALT_MIN_N and rng >= ALT_MIN_RANGE_M and sxx > 0:
        b = sum(x * y for x, y in zip(xs, ys)) / sxx
        out.update(pct_per_km=(math.exp(b) - 1.0) * 100.0, slope=b, enough=True)
    return out


def fatigue(wins: Sequence[dict]) -> dict:
    """wins carry "trip" (activity idx) and "day" (1, 2, …)."""
    by_trip: dict = {}
    for w in wins:
        if w.get("day"):
            by_trip.setdefault(w.get("trip"), []).append(w)
    per_day: dict = {}
    trips = 0
    for ws in by_trip.values():
        d1 = [w for w in ws if w["day"] == 1]
        if len(d1) < FAT_MIN_N or len({w["day"] for w in ws}) < 2:
            continue
        x = np.array([w["vam"] for w in d1])
        y = np.array([w["hr"] for w in d1])
        if np.ptp(x) > 0:
            b, a = np.polyfit(x, y, 1)
        else:
            b, a = 0.0, float(y.mean())
        trips += 1
        for n in sorted({w["day"] for w in ws if w["day"] > 1}):
            res = [w["hr"] - (a + b * w["vam"]) for w in ws if w["day"] == n]
            if res:
                per_day.setdefault(n, []).append(float(median(res)))
    return {"trips": trips, "evidence": EVIDENCE["fatigue"],
            "days": [{"day": n, "trips": len(v), "hr_shift_bpm": float(median(v))} for n, v in sorted(per_day.items())]}


def summary(wins: Sequence[dict], aet: Optional[float], lthr: Optional[float], n_days: int) -> dict:
    if not wins or not aet or not lthr:
        return {"n": 0, "days": n_days, "filter": HIKE_FILTER, "aet": aet, "lthr": lthr,
                "uses": {k: {"n": 0, "evidence": e} for k, e in EVIDENCE.items()}}
    alt = altitude_factor(wins, aet, lthr)
    fat = fatigue(wins)
    return {"n": len(wins), "days": n_days, "filter": HIKE_FILTER, "aet": aet, "lthr": lthr,
            "uses": {"vam": {"n": len(wins), "evidence": EVIDENCE["vam"], "table": vam_table(wins, aet, lthr)},
                     "altitude": alt,
                     "fatigue": {"n": sum(d["trips"] for d in fat["days"]), **fat},
                     "walking": {"n": len(wins), "evidence": EVIDENCE["walking"]}}}
