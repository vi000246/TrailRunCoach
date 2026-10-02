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
the number. rolling() = each run with the earlier runs of its own band.

AeT heat covariate (unsourced-rules.md §B6; feat/aet-heat-covariate):
aet_points keeps the cool band, runs without a temperature (as before the
bands) and the warm band (25–28 °C). A warm run's first-half HR is moved to
the cool band's top, 25 °C: hr1' = hr1 − β·(T − 25), before the between-run
regression; the drift itself is not adjusted (β is a between-run HR level,
not the within-run rise — workout-review.spec.md, heat bands). Cool runs are
left as they are: the AeT test is run < 25 °C and the gate compares the
estimate with the tested AeT (quality_gate.aet_test_reason "moved"), so the
reference is the test's condition, not heat.HR_BETA_REF (Hadley 120 would
move every estimate ~7 bpm below a cool-day test). Hot runs (> 28 °C) stay
out: there the drift itself is inflated (Lafrenz 2008), not only the level.
β (bpm per °C of air) = heat_beta(): the literature default shrunk toward
the athlete's own fit —
  default  1.0 bpm/°C: Jenkins, Campbell, Lee, Mündel & Cotter 2023 (Exp
           Physiol 108:207–220, doi 10.1113/EP090969): 14 trained cyclists,
           45 min at 70 % VO2peak at 18 / 27 / 36 °C, same vapour pressure —
           「higher heart rate (1 bpm/°C)」; humidity had no reliable effect
           on %HRmax. Cycling in a chamber, used for running: 推估.
  personal OLS hr1 = a + b·x1 + β·T over the athlete's road runs with a
           drift tier and a temperature in the last BETA_DAYS (x1 = first-
           half power, else speed — the basis most runs have), needs
           ≥ BETA_MIN_N runs and a temperature SD ≥ BETA_MIN_SD_C.
  used     w·β_personal + (1 − w)·β_default, w = n / (n + BETA_K), clipped to
           BETA_BOUNDS. Summer is also the base season, so β_personal is
           confounded with fitness (zone_events.beta_check) — the shrinkage
           and the bounds limit that. Numbers 推估 except the default's.
No temperature at all → no adjustment (the band is "none", kept as before).
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


AET_BANDS = ("cool", "warm", "none")   # the bands the AeT aggregate reads (warm: heat-adjusted, module doc)
HEAT_REF_C = 25.0              # = workout_review.DRIFT_HEAT_C: the cool band's top, the AeT test's condition
BETA_DEFAULT = 1.0             # bpm per °C: Jenkins 2023 (module doc); for running 推估
BETA_DEFAULT_SRC = "Jenkins 2023（Exp Physiol，doi 10.1113/EP090969）：氣溫每 +1 °C 心率 +1 bpm（騎車，推估套用到跑步）"
BETA_MIN_N = 10                # 推估: runs needed before the athlete's own fit counts at all
BETA_MIN_SD_C = 2.0            # 推估: the runs' temperatures must spread this much (else β is not identified)
BETA_K = 20                    # 推估: shrinkage w = n / (n + 20) — 20 runs = half personal
BETA_BOUNDS = (0.0, 2.0)       # 推估: heat never lowers HR; at most twice the default
BETA_DAYS = 365                # 推估: a whole year of seasons for the fit


def fit_heat_beta(rows: list[dict]) -> dict:
    """The athlete's own β from `rows` [{"hr", "x", "temp_c"}] (one per run):
    OLS hr = a + b·x + β·T (x dropped when it doesn't vary). {"personal",
    "se", "n", "sd_c"}; personal None below BETA_MIN_N runs or a temperature
    SD < BETA_MIN_SD_C."""
    pts = [(float(r["hr"]), r.get("x"), float(r["temp_c"])) for r in rows
           if r.get("hr") is not None and r.get("temp_c") is not None]
    n = len(pts)
    t = np.array([p[2] for p in pts]) if pts else np.array([])
    sd = float(t.std()) if n else 0.0
    out = {"personal": None, "se": None, "n": n, "sd_c": round(sd, 2)}
    if n < BETA_MIN_N or sd < BETA_MIN_SD_C:
        return out
    y = np.array([p[0] for p in pts])
    xs = [p[1] for p in pts]
    cols = [np.ones(n), t]
    if all(x is not None for x in xs) and float(np.std(np.array(xs, float))) > 1e-9:
        cols.insert(1, np.array(xs, float))
    X = np.column_stack(cols)
    try:
        inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return out
    c = inv @ X.T @ y
    r = y - X @ c
    dof = max(1, n - X.shape[1])
    se = math.sqrt(max(0.0, float(r @ r) / dof * inv[-1, -1]))
    return {**out, "personal": float(c[-1]), "se": se}


def shrink_beta(fit: dict) -> dict:
    """{"beta", "w", "personal", "se", "n", "default", "src"}: the personal β
    shrunk toward BETA_DEFAULT with w = n / (n + BETA_K), clipped."""
    p, n = fit.get("personal"), int(fit.get("n") or 0)
    w = n / (n + BETA_K) if p is not None else 0.0
    b = BETA_DEFAULT if p is None else w * p + (1.0 - w) * BETA_DEFAULT
    b = min(max(b, BETA_BOUNDS[0]), BETA_BOUNDS[1])
    src = "default" if p is None else "personal"
    return {"beta": b, "w": w, "personal": p, "se": fit.get("se"), "n": n, "sd_c": fit.get("sd_c"),
            "default": BETA_DEFAULT, "src": src}


def heat_beta(ds, today: dt.date, days: int = BETA_DAYS) -> dict:
    """shrink_beta(fit_heat_beta(…)) on the road runs of the last `days`
    with a drift tier and a temperature (any band). Never raises: the
    default on any failure."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    from backend.engine.wko5expr.dataset import date_to_day
    try:
        tday = math.floor(date_to_day(today))
        power, pace = [], []
        for w in ds.workouts:
            if not (tday - days < math.floor(w.day) <= tday) or w.sport != "run" or "runningtrail" in w.tags:
                continue
            dur = WR._f(w.metrics.get("duration"))
            if dur is None or dur < WR.WARMUP_S + WR.DRIFT_REF_MIN_S or category(w) != "road":
                continue
            dr = (WR.measure(ds, w) or {}).get("drift") or {}
            tc = WR._f(dr.get("temp_c"))
            if tc is None or WR.drift_tier(dr) is None:
                continue
            if dr.get("pw_hr1") is not None and WR._f(dr.get("p1")) is not None:
                power.append({"hr": dr["pw_hr1"], "x": WR._f(dr["p1"]), "temp_c": tc})
            if dr.get("hr1") is not None and WR._f(dr.get("v1")) is not None:
                pace.append({"hr": dr["hr1"], "x": WR._f(dr["v1"]), "temp_c": tc})
        WR._flush(ds)
        rows, basis = (power, "power") if len(power) >= len(pace) else (pace, "pace")
        return {**shrink_beta(fit_heat_beta(rows)), "basis": basis}
    except Exception:           # noqa: BLE001 — the gate must not break on one bad file
        return {**shrink_beta({}), "basis": None}


def beta_text(b: dict) -> str:
    """「β 0.85 bpm／°C（本人 24 次 × 0.55 ＋ 文獻 1.0）」."""
    if b.get("src") == "personal":
        return (f"β {b['beta']:.2f} bpm／°C（本人 {b['n']} 次跑步擬合 {b['personal']:.2f}，"
                f"權重 {b['w']:.0%}，其餘用文獻 {BETA_DEFAULT:.1f}）")
    return f"β {b['beta']:.2f} bpm／°C（文獻預設；本人有溫度的跑步 {b.get('n') or 0} 次，不夠自己擬合）"


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


def aet_points(ds, today: dt.date, days: int = AET_DAYS, bands: tuple = AET_BANDS,
               beta: Optional[dict] = None) -> list[dict]:
    """(first-half HR, drift, SE) of the road runs in `days` up to `today`
    whose drift_of passed (test or reference tier) in `bands` (AET_BANDS:
    cool, warm, no temperature), oldest first: Pw:HR when the run has it
    (the AeT test's basis), else Pa:HR. A warm run's "hr1" is heat-adjusted
    to HEAT_REF_C with `beta` (heat_beta() when None and there is a warm
    run): "hr1_raw", "heat_bpm" (the amount taken off), "temp_c", "band"."""
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
        band = band_of(dr)
        if WR.drift_tier(dr) is None or band not in bands:
            continue
        if dr.get("pw_drift") is not None and (dr.get("pw_ok") or dr.get("pw_ref_ok")):
            hr1, d, se, basis = dr.get("pw_hr1"), dr["pw_drift"], dr.get("pw_drift_se"), "power"
        else:
            hr1, d, se, basis = dr.get("hr1"), dr.get("drift"), dr.get("drift_se"), "pace"
        if hr1 is None or d is None:
            continue
        out.append({"idx": w.idx, "date": WR._wdate(w).isoformat(), "hr1": hr1, "hr1_raw": hr1, "drift": d,
                    "se": se, "basis": basis, "tier": WR.drift_tier(dr), "band": band,
                    "temp_c": WR._f(dr.get("temp_c")), "heat_bpm": 0.0})
    WR._flush(ds)
    if any(p["band"] == "warm" for p in out):
        heat_adjust(out, (beta if beta is not None else heat_beta(ds, today))["beta"])
    return out


def heat_adjust(pts: list[dict], b: float) -> list[dict]:
    """In place: each warm point's hr1 = hr1_raw − b·(T − HEAT_REF_C)."""
    for p in pts:
        if p.get("band") == "warm" and p.get("temp_c") is not None:
            p["heat_bpm"] = b * max(0.0, float(p["temp_c"]) - HEAT_REF_C)
            p["hr1"] = float(p["hr1_raw"]) - p["heat_bpm"]
    return pts


def heat_note(pts: list[dict], beta: Optional[dict]) -> str:
    """「含 3 次 25–28 °C 的跑步：心率先扣掉熱的影響（…，推估）」, or "" without a warm run."""
    warm = [p for p in pts if p.get("band") == "warm"]
    if not warm or not beta:
        return ""
    lo, hi = min(p["heat_bpm"] for p in warm), max(p["heat_bpm"] for p in warm)
    amt = f"{lo:.1f}" if abs(hi - lo) < 0.05 else f"{lo:.1f}–{hi:.1f}"
    return (f"含 {len(warm)} 次 25–28 °C 的跑步：前半心率先扣掉熱的影響 {amt} bpm（移到 25 °C，{beta_text(beta)}；"
            f"推估），飄移本身不校正；> 28 °C 的不用")


def aet_validity(ds, today: dt.date, lthr: Optional[float] = None) -> dict:
    """Is the AeT valid (unsourced-rules.md §B3)? {"valid", "value", "se",
    "n", "shift_bpm", "slope_per_10bpm", "reason", "points"}: valid = the
    aggregated estimate's SE ≤ 3 bpm and no one-way shift > 5 bpm over the
    last 6 points (推估). Never raises: any failure is 「需要測試」."""
    from dataclasses import asdict
    from backend.engine.algorithms import threshold_estimate as TE
    try:
        beta = None
        pts = aet_points(ds, today, beta={"beta": 0.0})
        if any(p["band"] == "warm" for p in pts):
            beta = heat_beta(ds, today)
            heat_adjust(pts, beta["beta"])
        agg = TE.aet_aggregate([(p["hr1"], p["drift"], p["se"]) for p in pts], lthr=lthr)
    except Exception as e:      # noqa: BLE001 — the gate must not break on one bad file
        return {"valid": False, "value": None, "se": None, "n": 0, "shift_bpm": None, "slope_per_10bpm": None,
                "reason": f"AeT 聚合估計算不出來（{type(e).__name__}）：需要測試", "points": 0, "heat": None}
    out = {**asdict(agg), "points": len(pts),
           "heat": None if beta is None else {**beta, "warm": sum(1 for p in pts if p["band"] == "warm"),
                                              "ref_c": HEAT_REF_C, "default_src": BETA_DEFAULT_SRC}}
    note = heat_note(pts, beta)
    if note:
        out["reason"] = f"{out['reason']}（{note}）"
    return out
