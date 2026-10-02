"""
HR-based ("theoretical") running capacity — the power the athlete's own
HR–power relation predicts at LTHR, from SUBMAXIMAL runs (user request
2026-10-01: training is mostly easy, so the mean-max envelope may never show
the true capacity).

Method (推估 until the back-test validates it):

1. Per run, the steady flat windows (the v3 100 m grade windows: |grade| ≤
   2 %, running, 10–60 min into the run, HR read HR_LAG_S later) give one
   point (time-weighted HR, time-weighted power). Runs are the independent
   unit; windows within a run are not.
2. OLS of power on HR across the runs of the last 90 days. The individual
   linear HR–work-rate relation of submaximal exercise extrapolated to a
   reference HR is the Åstrand & Ryhming 1954 principle (J Appl Physiol
   7:218–221: aerobic capacity from pulse rate during sub-maximal work);
   the power produced at a fixed submaximal HR tracks performance (Lamberts
   et al. 2011, Br J Sports Med 45:797–804, LSCT: mean power in stages 2–3
   vs performance r = 0.80–0.94). Extrapolating to LTHR (instead of HRmax)
   and reading that power as a threshold is our own use: 推估.
3. P_LTHR = a + b·LTHR, with a bootstrap over runs (10–90 %).

Anchor (stated, not resolved): Friel's LTHR is the HR of the last 20 min of a
30-min all-out TT, so P_LTHR is ~30-min power. As an F1 anchor it is used
both at TTE = 1800 s ("tt30") and at the as-of PD-model TTE ("tte"); the
back-test reports both.

Uncertainty: heat and dehydration raise HR at a given power (cardiac drift),
so runs with Pw:HR decoupling > 5 % (Uphill Athlete, intensity.drift_easy)
are dropped and windows after 60 min are not used; HR lags power (60 s lag
applied, the first 10 min dropped); a run's point sits at its own intensity,
so the extrapolation distance (LTHR − the highest run HR) is reported — the
farther it is, the less it is worth. Needs ≥ 8 runs spanning ≥ 15 bpm, a
positive slope and R² ≥ 0.5 (推估): when the runs are all at about the same
power, HR differences come from heat / drift / fatigue and the line says
nothing about capacity (this athlete, 2026-10-01: slope 0.18 W/bpm, R² 0.01).
The combined rule in the back-test uses only a valid fit.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from backend.i18n import _, N_

HRCAP = {
    "flat_g": 0.02,          # course.py flat band
    "run_share": 0.5,        # a running window (cadence ≥ 130 spm for ≥ half of it)
    "t_min_s": 600.0,        # HR lag / warm-up (workout_review drift excludes the first 10 min)
    "t_max_s": 3600.0,       # 推估: limit cardiac drift
    "min_windows": 5,        # 推估
    "min_runs": 8,           # 推估
    "min_span_bpm": 15.0,    # 推估
    "min_r2": 0.5,           # 推估: HR must explain at least half of the between-run power variance
    "drift_max": 0.05,       # Uphill Athlete Pw:HR decoupling < 5 % = aerobic
    "window_days": 90,       # as the CP window (athlete.CP_WINDOW_DAYS)
    "boot": 300,
    "tt_s": 1800.0,          # Friel's 30-min TT
}
SOURCE = N_("心率–功率個人回歸外插到 LTHR（Åstrand & Ryhming 1954 原理；Lamberts 2011 LSCT："
            "固定次大心率下的功率對應表現）；外插到 LTHR 當閾值為推估")


def run_point(windows: list[dict]) -> Optional[dict]:
    """windows of ONE run: {g, v, p, hr_lag (or hr), run, t}. Returns
    {hr, p, n, t_s} or None when fewer than min_windows qualify."""
    k = HRCAP
    sel = []
    for w in windows:
        hr = w.get("hr_lag") if w.get("hr_lag") is not None else w.get("hr")
        if hr is None or not w.get("p") or not w.get("v") or w["v"] <= 0:
            continue
        if abs(w.get("g") or 0.0) > k["flat_g"] or (w.get("run") is not None and w["run"] < k["run_share"]):
            continue
        t = w.get("t")
        if t is None or not (k["t_min_s"] <= t <= k["t_max_s"]):
            continue
        sel.append((100.0 / w["v"], float(hr), float(w["p"])))
    if len(sel) < k["min_windows"]:
        return None
    wt = np.array([s[0] for s in sel])
    hr = np.array([s[1] for s in sel])
    p = np.array([s[2] for s in sel])
    return {"hr": float((hr * wt).sum() / wt.sum()), "p": float((p * wt).sum() / wt.sum()),
            "n": len(sel), "t_s": float(wt.sum())}


def ols(x, y) -> Optional[dict]:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2 or np.ptp(x) <= 0:
        return None
    xm, ym = x.mean(), y.mean()
    b = float(((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum())
    a = float(ym - b * xm)
    res = y - (a + b * x)
    ss = float(((y - ym) ** 2).sum())
    return {"a": a, "b": b, "r2": 1.0 - float((res ** 2).sum()) / ss if ss > 0 else 1.0,
            "resid_sd": float(res.std(ddof=2)) if len(x) > 2 else None, "n": int(len(x))}


def capacity(points: list[dict], lthr: Optional[float], boot: int = HRCAP["boot"], seed: int = 0) -> dict:
    """P at LTHR from the run points, with validity reasons and a bootstrap
    10–90 % range over runs."""
    k = HRCAP
    pts = [q for q in points if q]
    out = {"n_runs": len(pts), "lthr": lthr, "p_lthr": None, "range": None, "fit": None,
           "valid": False, "reasons": [], "source": _(SOURCE), "label": _("推估")}
    if len(pts) < 2 or not lthr:
        out["reasons"].append(_("只有 {n} 次穩定平路跑", n=len(pts)) if lthr else _("沒有 LTHR"))
        return out
    x = np.array([q["hr"] for q in pts])
    y = np.array([q["p"] for q in pts])
    f = ols(x, y)
    if f is None:
        out["reasons"].append(_("心率沒有變化，無法回歸"))
        return out
    span = float(np.ptp(x))
    out.update(fit=f, p_lthr=f["a"] + f["b"] * lthr, hr_lo=float(x.min()), hr_hi=float(x.max()),
               span_bpm=span, extrap_bpm=float(lthr - x.max()))
    if len(pts) >= 3 and boot:
        rng = np.random.default_rng(seed)
        vals = []
        for _b in range(boot):
            i = rng.integers(0, len(pts), len(pts))
            g = ols(x[i], y[i])
            if g is not None:
                vals.append(g["a"] + g["b"] * lthr)
        if vals:
            out["range"] = [float(np.percentile(vals, 10)), float(np.percentile(vals, 90))]
    if len(pts) < k["min_runs"]:
        out["reasons"].append(_("穩定平路跑 {n} 次 < {need}", n=len(pts), need=k["min_runs"]))
    if span < k["min_span_bpm"]:
        out["reasons"].append(_("心率範圍 {span:.0f} bpm < {need:.0f}", span=span, need=k["min_span_bpm"]))
    if f["b"] <= 0:
        out["reasons"].append(_("功率不隨心率上升（斜率 ≤ 0）"))
    elif f["r2"] < k["min_r2"]:
        out["reasons"].append(_("心率只解釋 {r2:.0%} 的功率差異（R² < {need:.0%}）：跑步功率幾乎固定，"
                                "心率差異多半來自熱、飄移或疲勞", r2=f["r2"], need=k["min_r2"]))
    out["valid"] = not out["reasons"]
    return out


ZONES = ((0.80, N_("低（< 80 %）")), (0.95, N_("中（80–95 %）")), (None, N_("高（≥ 95 %）")))   # Palladino three zones


def intensity_distribution(runs: list[dict], p_lthr: Optional[float]) -> Optional[dict]:
    """Each run's moving average power ÷ P_LTHR (the % of HR-implied capacity
    it represents) and the share of runs / moving time in Palladino's three
    zones (< 80 %, 80–95 %, ≥ 95 %; zones.PALLADINO_3ZONE). runs: [{p_avg,
    moving_s, ...}]."""
    if not p_lthr:
        return None
    rows = [{**r, "pct": r["p_avg"] / p_lthr} for r in runs if r.get("p_avg")]
    if not rows:
        return None
    tot_t = sum(r.get("moving_s") or 0 for r in rows) or 1.0
    bands = []
    lo = 0.0
    for hi, name in ZONES:
        sel = [r for r in rows if r["pct"] >= lo and (hi is None or r["pct"] < hi)]
        bands.append({"label": _(name), "runs": len(sel), "share_runs": len(sel) / len(rows),
                      "share_time": sum(r.get("moving_s") or 0 for r in sel) / tot_t})
        lo = hi or lo
    pct = [r["pct"] for r in rows]
    return {"p_lthr": p_lthr, "n": len(rows), "median": float(np.median(pct)),
            "p90": float(np.percentile(pct, 90)), "max": float(max(pct)), "bands": bands, "rows": rows}
