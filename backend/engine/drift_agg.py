"""
Multi-run heart-rate drift (drift v2: docs/research/drift-algorithm.md §1.3,
§5.4; unsourced-rules.md §B3).

One 30–40-min run's drift is ±4–6 pp (residual lag-1 autocorrelation 0.995:
4–5 independent observations), the same size as the cross-run SD, and UA's
3.5 / 5 % bands are 1.5 pp apart: a single run can't tell them apart. So the
overview drift indicator and the season charts show the mean ± SE of the last
AGG_N eligible runs, single runs still visible; and the AeT counts as valid
from the aggregated estimate (threshold_estimate.aet_aggregate), not from a
date.

aggregate(points)
    Inverse-variance weighted mean of the last AGG_N drifts (w = 1/SE²,
    推估). SE = max(√(1/Σw), the weighted SD / √n): the larger of what the
    runs' own SEs say and how much they actually scatter (推估).
rolling(ds, basis)
    {workout index: aggregate of it and the eligible runs before it} for the
    season charts (evaluator drift_avg()).
aet_points / aet_validity
    The drift points (first-half HR, drift, SE) of road runs in the last
    AET_DAYS, and aet_aggregate on them: valid = SE ≤ 3 bpm and no shift
    > 5 bpm over the last 6 (推估, B3).

Heat bands (workout_review.temp_band: < 25 / 25–28 / > 28 °C, 推估 cut-offs):
runs are aggregated only with runs of the same band — heat inflates the drift
(Lafrenz 2008; Beiter 2025), so a mean across bands mixes the season into
the number. rolling() = each run with the earlier runs of its own band;
aet_points keeps the cool band (and runs without a temperature, as before
the bands): the AeT regression compares across runs, and a heat covariate
(unsourced-rules.md §B6) is not built yet.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

AGG_N = 6            # Ikari 2026 (SportRxiv preprint, not peer-reviewed): ≥ 6 runs for reliability 0.80;
                     # = threshold_estimate.AET_MIN_RUNS
AGG_MIN = 2          # 推估: a mean ± SE needs at least two runs (the indicator's old 「不到 2 次」 rule)
AGG_DAYS = 56        # the overview's 8 weeks (drift_series); the season charts use the same reach
AET_DAYS = 180       # thresholds.WINDOWS[-1]: the AeT points' reach
SE_FLOOR = 0.005     # 推估: = threshold_estimate.AET_SE_FLOOR
SE_DEFAULT = 0.05    # 推估: a run without an SE (cached before v11) gets the single-run noise


def _se(p: dict) -> float:
    v = p.get("se")
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = SE_DEFAULT
    return max(v if math.isfinite(v) else SE_DEFAULT, SE_FLOOR)


def aggregate(points: list[dict], n: int = AGG_N) -> Optional[dict]:
    """{"mean", "se", "n", "se_iv", "se_emp", "first", "last", "tiers"} of the
    last `n` points with a drift (oldest first; {"drift", "se", "date",
    "tier"?}); None without any."""
    pts = [p for p in points if p.get("drift") is not None][-n:]
    if not pts:
        return None
    x = np.array([float(p["drift"]) for p in pts])
    w = 1.0 / np.array([_se(p) for p in pts]) ** 2
    mean = float(np.sum(w * x) / np.sum(w))
    se_iv = float(math.sqrt(1.0 / np.sum(w)))
    k = len(pts)
    se_emp = None
    if k >= 2:
        var = float(np.sum(w * (x - mean) ** 2) / np.sum(w)) * k / (k - 1)
        se_emp = math.sqrt(var / k)
    return {"mean": mean, "se": max(se_iv, se_emp or 0.0), "n": k, "se_iv": se_iv, "se_emp": se_emp,
            "first": pts[0].get("date"), "last": pts[-1].get("date"),
            "tiers": [p.get("tier") for p in pts]}


def text(a: Optional[dict]) -> str:
    """「3.1% ± 1.8 pp（6 次平均）」."""
    if not a:
        return "–"
    return f"{a['mean'] * 100:.1f}% ± {a['se'] * 100:.1f} pp（{a['n']} 次平均）"


AET_BANDS = ("cool", "none")   # the bands the AeT aggregate reads (see the module doc)


def band_of(dr: dict) -> str:
    from backend.engine import workout_review as WR
    return dr.get("temp_band") or WR.temp_band(dr.get("temp_c"))


def pick_band(points: list[dict]) -> Optional[str]:
    """The band to report for a list of points ({"band", "drift"}, oldest
    first): the latest point's band when it has ≥ AGG_MIN points, else the
    band with the most points (ties: the latest seen). None without any."""
    pts = [p for p in points if p.get("drift") is not None]
    if not pts:
        return None
    n: dict = {}
    for p in pts:
        n[p.get("band") or "none"] = n.get(p.get("band") or "none", 0) + 1
    last = pts[-1].get("band") or "none"
    if n[last] >= AGG_MIN:
        return last
    order = [p.get("band") or "none" for p in pts]
    return max(n, key=lambda b: (n[b], max(i for i, x in enumerate(order) if x == b)))


def _basis_point(w, m: dict, basis: str) -> Optional[dict]:
    from backend.engine import workout_review as WR
    dr = (m or {}).get("drift") or {}
    d = WR.basis_drift(dr, basis, ref=True)[0]
    if d is None:
        return None
    se = dr.get("pw_drift_se" if basis == "power" else "drift_se")
    return {"idx": w.idx, "date": WR._wdate(w).isoformat(), "day": math.floor(w.day), "drift": d, "se": se,
            "tier": WR.drift_tier(dr), "band": band_of(dr)}


def rolling(ds, basis: str = "pace", n: int = AGG_N, days: int = AGG_DAYS) -> dict:
    """{workout index: aggregate()} for each run the season drift charts plot
    (drift(basis, "all"): road, test or reference tier): that run and the
    eligible runs of the same temperature band in the `days` before it, the
    last `n` (`band` on each). Only where ≥ AGG_MIN."""
    from backend.engine import workout_review as WR
    pts = []
    for w in sorted(ds.workouts, key=lambda x: x.day):
        if w.sport != "run" or "runningtrail" in w.tags:
            continue
        dur = WR._f(w.metrics.get("duration"))
        if dur is None or dur < WR.WARMUP_S + WR.DRIFT_REF_MIN_S:
            continue
        p = _basis_point(w, WR.measure(ds, w), basis)
        if p is not None:
            pts.append(p)
    WR._flush(ds)
    out = {}
    for i, p in enumerate(pts):
        win = [q for q in pts[:i + 1] if p["day"] - days < q["day"] and q["band"] == p["band"]]
        a = aggregate(win, n)
        if a and a["n"] >= AGG_MIN:
            out[p["idx"]] = {**a, "band": p["band"]}
    return out


def aet_points(ds, today: dt.date, days: int = AET_DAYS) -> list[dict]:
    """(first-half HR, drift, SE) of the road runs in `days` up to `today`
    whose drift_of passed (test or reference tier) in the cool band or
    without a temperature (AET_BANDS), oldest first: Pw:HR when the run has
    it (the AeT test's basis), else Pa:HR."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    out = []
    for w in sorted(ds.workouts, key=lambda x: x.day):
        if not (tday - days < math.floor(w.day) <= tday) or w.sport != "run" or "runningtrail" in w.tags:
            continue
        dur = WR._f(w.metrics.get("duration"))
        if dur is None or dur < WR.WARMUP_S + WR.DRIFT_REF_MIN_S or category(w) != "road":
            continue
        m = WR.measure(ds, w)
        dr = (m or {}).get("drift") or {}
        if WR.drift_tier(dr) is None or band_of(dr) not in AET_BANDS:
            continue
        if dr.get("pw_drift") is not None and (dr.get("pw_ok") or dr.get("pw_ref_ok")):
            hr1, d, se, basis = dr.get("pw_hr1"), dr["pw_drift"], dr.get("pw_drift_se"), "power"
        else:
            hr1, d, se, basis = dr.get("hr1"), dr.get("drift"), dr.get("drift_se"), "pace"
        if hr1 is None or d is None:
            continue
        out.append({"idx": w.idx, "date": WR._wdate(w).isoformat(), "hr1": hr1, "drift": d, "se": se,
                    "basis": basis, "tier": WR.drift_tier(dr)})
    WR._flush(ds)
    return out


def aet_validity(ds, today: dt.date, lthr: Optional[float] = None) -> dict:
    """Is the AeT valid (unsourced-rules.md §B3)? {"valid", "value", "se",
    "n", "shift_bpm", "slope_per_10bpm", "reason", "points"}: valid = the
    aggregated estimate's SE ≤ 3 bpm and no one-way shift > 5 bpm over the
    last 6 points (推估). Never raises: any failure is 「需要測試」."""
    from dataclasses import asdict
    from backend.engine.algorithms import threshold_estimate as TE
    try:
        pts = aet_points(ds, today)
        agg = TE.aet_aggregate([(p["hr1"], p["drift"], p["se"]) for p in pts], lthr=lthr)
    except Exception as e:      # noqa: BLE001 — the gate must not break on one bad file
        return {"valid": False, "value": None, "se": None, "n": 0, "shift_bpm": None, "slope_per_10bpm": None,
                "reason": f"AeT 聚合估計算不出來（{type(e).__name__}）：需要測試", "points": 0}
    return {**asdict(agg), "points": len(pts)}
