"""
Validate the app's training-load guardrails on the Lövdal et al. 2021 data
(write-up: docs/research/validation-lovdal.md).

Data (CC0 1.0, DataverseNL doi:10.34894/UWU9PV, version 2, 2024-06-05):
    Lövdal SS, den Hartigh RJR, Azzopardi G. Injury prediction in competitive
    runners with machine learning. Int J Sports Physiol Perform 2021.
    doi:10.1123/ijspp.2020-0518
The CSVs live OUTSIDE the repo (default ~/Datasets/lovdal) and are never committed.

Structure found when reading the files (see the report §1):
    * one row per (athlete, event day d); `injury` = 1 on the injury day;
    * week file: three 7-day blocks before d (W0 = d-7..d-1, W1 = d-14..d-8,
      W2 = d-21..d-15); day file: the seven days before d;
    * non-injury rows form contiguous daily streams, but each injury row is
      ISOLATED: only its own 7 (day file) / 21 (week file) days exist, with a
      gap before it. So the design is a nested case-control / day-level
      hazard: P(injury on day d | load in the 21 days before d). Exposures
      that need more than 21 days (the app's 4-week-average change, a 42-day
      CTL, EWMA ACWR 7/42) cannot be computed for the cases; they are
      replaced by the closest 21-day analogue, and the analogue is checked
      against the real metric on the contiguous non-injury streams.

Guardrails tested (engine/quality_gate.py, adapt.py, status.py,
docs/research/unsourced-rules.md B2):
    1. week-over-week volume step > 20 % (block) / 10-20 % (hold); the chart's
       4-week-average change (analogue: 2-week-average change);
    2. CTL ramp 5 (warn) / 8 (block) per week (analogue from 21 days);
    3. ACWR (rolling 7:21 coupled, 7:14 uncoupled), Foster monotony > 2, strain;
    4. low-intensity share < 75 % (21 days) vs injury and perceived recovery.

Models (numpy only; the repo ships no scipy / statsmodels):
    * random-intercept logistic (athlete), 20-point Gauss-Hermite quadrature,
      Newton on the analytic gradient;
    * pooled logistic with athlete-cluster robust (sandwich) SE as a check
      (control rows overlap day to day, so the robust CI is the honest one);
    * within-athlete OLS with cluster-robust SE for the subjective outcomes;
    * pooled ROC AUC (Mann-Whitney) with an athlete-cluster bootstrap CI.
Day-level injury risk is ~1.3 %, so OR ~ HR per day.
Multiplicity: Holm over the pre-specified primary family (one test per
guardrail), Benjamini-Hochberg over every reported test.

Unit mapping to the app (推估, no source; report §2):
    run hours  = easy km / 13.3 + Z3-4 km / 17 + Z5-T1-T2 km / 19.5
    rTSS proxy = 100 IF^2 per hour with IF 0.75 / 0.95 / 1.05
                 -> 4.2 / 5.3 / 5.65 per km; cross-training 40 per hour.

    python -m backend.scripts.validation.lovdal_guardrails [--data DIR] [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict

import numpy as np

# app constants (copied, not imported, so the script does not depend on the engine)
STEP_HOLD, STEP_BLOCK = 0.10, 0.20           # quality_gate.STEP_HOLD / STEP_BLOCK, status.VOLUME_STEP_WATCH
RAMP_SUB, RAMP_BLOCK = 5.0, 8.0              # quality_gate.RAMP_SUB / RAMP_BLOCK, status.RAMP
LOW_SHARE_MIN, LOW_SHARE_WATCH = 0.75, 0.65  # status.LOW_SHARE_GOOD / WATCH
MONOTONY_HIGH = 2.0                          # Foster 1998 (the app no longer shows monotony)
ACWR_BANDS = (0.8, 1.3, 1.5)                 # chart_metrics.LOAD_RATIO_BANDS
CTL_TAU, ATL_TAU = 42.0, 7.0
FC = 1 - math.exp(-1 / CTL_TAU)              # engine/algorithms/metrics.compute_run_pmc
FA = 1 - math.exp(-1 / ATL_TAU)
# unit mapping (推估)
SPEED = {"easy": 13.3, "z34": 17.0, "z5": 19.5}
TSS_PER_KM = {"easy": 4.2, "z34": 5.3, "z5": 5.65}
TSS_PER_ALT_H = 40.0
MIN_PREV_KM, MIN_PREV_H = 20.0, 1.5          # step undefined below this (tiny denominators)
SEG_GAP, WARMUP = 3, 42                      # control-stream EWMA: restart after >= 3 missing days, use after 42

DAY_FEATURES = ["nr. sessions", "total km", "km Z3-4", "km Z5-T1-T2", "km sprinting", "strength training",
                "hours alternative", "perceived exertion", "perceived trainingSuccess", "perceived recovery"]


# ================================================================ loading
def read(path):
    with open(path, newline="") as f:
        r = csv.reader(f)
        h = next(r)
        a = np.array([[float(x) for x in row] for row in r])
    return {k: i for i, k in enumerate(h)}, a


def week_table(ix, a):
    """Per event row: weekly load in km / estimated hours / TSS proxy for W0..W2 and the exposures."""
    col = lambda name, k: a[:, ix[name + ("" if k == 0 else f".{k}")]]
    T = {"aid": a[:, ix["Athlete ID"]].astype(int), "day": a[:, ix["Date"]].astype(int),
         "y": a[:, ix["injury"]].astype(float)}
    km, hours, run_h, easy_h, tss, sess = [], [], [], [], [], []
    for k in range(3):
        tot, z34, z5 = col("total kms", k), col("total km Z3-4", k), col("total km Z5-T1-T2", k)
        hi = np.minimum(z34 + z5, tot)
        sc = np.where(z34 + z5 > 0, hi / np.maximum(z34 + z5, 1e-9), 0)
        z34, z5 = z34 * sc, z5 * sc
        easy = tot - hi
        alt = col("total hours alternative training", k)
        eh = easy / SPEED["easy"]
        rh = eh + z34 / SPEED["z34"] + z5 / SPEED["z5"]
        km.append(tot), easy_h.append(eh), run_h.append(rh), hours.append(rh + alt)
        tss.append(easy * TSS_PER_KM["easy"] + z34 * TSS_PER_KM["z34"] + z5 * TSS_PER_KM["z5"] + alt * TSS_PER_ALT_H)
        sess.append(col("nr. sessions", k))
        if k == 0:
            T["easy_km0"] = easy
    T.update(km=np.array(km), hours=np.array(hours), run_h=np.array(run_h), easy_h=np.array(easy_h),
             tss=np.array(tss), sessions=np.array(sess))
    T["recov0"] = col("avg recovery", 0)
    T["exert0"] = col("avg exertion", 0)
    T["easy_km"] = T["km"] - np.array([np.minimum(col("total km Z3-4", k) + col("total km Z5-T1-T2", k),
                                                   col("total kms", k)) for k in range(3)])
    nan = np.full(len(a), np.nan)
    K, H, S = T["km"], T["hours"], T["tss"]
    with np.errstate(divide="ignore", invalid="ignore"):
        T["step0"] = np.where(K[1] >= MIN_PREV_KM, (K[0] - K[1]) / K[1], nan)          # week ending d-1
        T["step1"] = np.where(K[2] >= MIN_PREV_KM, (K[1] - K[2]) / K[2], nan)          # week ending d-8
        T["step0_h"] = np.where(H[1] >= MIN_PREV_H, (H[0] - H[1]) / H[1], nan)
        T["avg2"] = np.where(K[1] + K[2] >= 2 * MIN_PREV_KM, (K[0] - K[2]) / (K[1] + K[2]), nan)
        T["avg2_h"] = np.where(H[1] + H[2] >= 2 * MIN_PREV_H, (H[0] - H[2]) / (H[1] + H[2]), nan)
        T["chronic_km"] = (K[1] + K[2]) / 2
        chronic_tss = (S[1] + S[2]) / 2
        ok = K[1] + K[2] >= 2 * MIN_PREV_KM
        # CTL ramp analogue: over one week CTL moves by ~ FC * sum(x - CTL); CTL ~ the 14-day mean before
        T["ramp"] = np.where(ok, FC * (S[0] - chronic_tss), nan)
        T["ctl_proxy"] = np.where(ok, chronic_tss / 7, nan)
        T["ramp_pct"] = np.where(ok, T["ramp"] / (chronic_tss / 7), nan)
        T["acwr_c"] = np.where(ok, S[0] / ((S[0] + S[1] + S[2]) / 3), nan)    # 7:21 coupled
        T["acwr_u"] = np.where(ok, S[0] / chronic_tss, nan)                    # 7:14 uncoupled
        k21 = K.sum(0)
        T["low_km"] = np.where(k21 >= 30, T["easy_km"].sum(0) / k21, nan)
        T["low_time"] = np.where(k21 >= 30, T["easy_h"].sum(0) / T["run_h"].sum(0), nan)
        k12 = K[1] + K[2]
        T["low_prev"] = np.where(k12 >= 20, (T["easy_h"][1] + T["easy_h"][2]) / (T["run_h"][1] + T["run_h"][2]), nan)
        T["recov0"] = np.where(T["sessions"][0] >= 3, T["recov0"], nan)
        T["exert0"] = np.where(T["sessions"][0] >= 3, T["exert0"], nan)
    return T


def day_streams(ix, a):
    """Daily values per athlete from the day file (suffix k -> day d - 7 + k; '.6' = the day before)."""
    daily = defaultdict(dict)
    week0 = {}
    for row in a:
        aid, d = int(row[ix["Athlete ID"]]), int(row[ix["Date"]])
        vals = []
        for k in range(7):
            suf = "" if k == 0 else f".{k}"
            v = [row[ix[c + suf]] for c in DAY_FEATURES]
            daily[aid][d - 7 + k] = v
            vals.append(v)
        week0[(aid, d)] = np.array(vals)
    return daily, week0


def daily_tss(v):
    """v: (..., 10) day features -> TSS proxy per day."""
    km, z34, z5, spr, alt = v[..., 1], v[..., 2], v[..., 3], v[..., 4], v[..., 6]
    hi_raw = z34 + z5 + spr
    hi = np.minimum(hi_raw, km)
    sc = np.where(hi_raw > 0, hi / np.maximum(hi_raw, 1e-9), 0)
    return ((km - hi) * TSS_PER_KM["easy"] + z34 * sc * TSS_PER_KM["z34"] + (z5 + spr) * sc * TSS_PER_KM["z5"]
            + alt * TSS_PER_ALT_H)


def monotony_strain(w):
    """Foster 1998 on 7 daily loads (population SD, rest = 0, as engine/algorithms/chart_metrics)."""
    sd = float(np.std(w))
    if sd == 0:
        return np.nan, np.nan
    m = float(np.mean(w)) / sd
    return m, float(np.sum(w)) * m


def control_stream_checks(daily, events_y, T):
    """On the contiguous non-injury streams: the real 42-day EWMA CTL ramp, EWMA ACWR and the app's
    4-week-average chart metric, vs the 21-day analogues the case-control analysis has to use."""
    proxy_ramp = {(a, d): r for a, d, r in zip(T["aid"], T["day"], T["ramp"])}
    proxy_acwr = {(a, d): r for a, d, r in zip(T["aid"], T["day"], T["acwr_u"])}
    proxy_avg2 = {(a, d): r for a, d, r in zip(T["aid"], T["day"], T["avg2"])}
    pairs_r, pairs_a, pairs_v = [], [], []
    true_ramp_all, avg4_rows = [], []
    for aid, days in daily.items():
        lo, hi = min(days), max(days)
        n = hi - lo + 1
        obs = np.zeros(n, bool)
        x = np.zeros(n)
        km = np.full(n, np.nan)
        for d, v in days.items():
            obs[d - lo] = True
            x[d - lo] = daily_tss(np.array(v))
            km[d - lo] = v[1]
        ctl, atl, age = np.full(n, np.nan), np.full(n, np.nan), np.full(n, -1)
        i = 0
        while i < n:  # segments split at >= SEG_GAP missing days
            if not obs[i]:
                i += 1
                continue
            j, miss = i, 0
            while j < n and miss < SEG_GAP:
                miss = 0 if obs[j] else miss + 1
                j += 1
            end = j - miss
            c = a_ = float(np.mean(x[i:min(end, i + 14)]))
            for k in range(i, end):
                c += FC * (x[k] - c)
                a_ += FA * (x[k] - a_)
                ctl[k], atl[k], age[k] = c, a_, k - i
            i = end
        for d in range(lo + 1, hi + 1):
            e = d - 1 - lo  # last day before event day d
            if e - 7 < 0 or age[e] < WARMUP or events_y.get((aid, d), 1) != 0:
                continue
            tr = ctl[e] - ctl[e - 7]
            true_ramp_all.append(tr)
            pr = proxy_ramp.get((aid, d))
            if pr is not None and not np.isnan(pr):
                pairs_r.append((tr, pr))
            pa = proxy_acwr.get((aid, d))
            if pa is not None and not np.isnan(pa) and ctl[e] > 0:
                pairs_a.append((atl[e] / ctl[e], pa))
            # the app's chart: (W0 - W4) / (W1 + W2 + W3 + W4) on 7-day blocks ending at e
            if e - 34 >= 0 and not np.isnan(km[e - 34:e + 1]).any():
                wk = [km[e - 7 * k - 6:e - 7 * k + 1].sum() for k in range(5)]
                if sum(wk[1:]) >= 4 * MIN_PREV_KM:
                    v4 = (wk[0] - wk[4]) / sum(wk[1:])
                    avg4_rows.append((aid, d, v4))
                    p2 = proxy_avg2.get((aid, d))
                    if p2 is not None and not np.isnan(p2):
                        pairs_v.append((v4, p2))
    out = {}
    for name, pr in (("ramp", pairs_r), ("acwr", pairs_a), ("avg4_vs_avg2", pairs_v)):
        p = np.array(pr)
        sl = np.polyfit(p[:, 1], p[:, 0], 1)
        out[name] = {"n": len(p), "r": float(np.corrcoef(p[:, 0], p[:, 1])[0, 1]), "slope_true_on_proxy": float(sl[0]),
                     "intercept": float(sl[1]), "true_q": [float(q) for q in np.quantile(p[:, 0], [.1, .5, .9])],
                     "proxy_q": [float(q) for q in np.quantile(p[:, 1], [.1, .5, .9])]}
    p = np.array(pairs_r)
    for thr in (RAMP_SUB, RAMP_BLOCK):
        t, q = p[:, 0] >= thr, p[:, 1] >= thr
        out["ramp"][f"agree_{thr:g}"] = {"true_fires": float(t.mean()), "proxy_fires": float(q.mean()),
                                         "sens": float((t & q).sum() / max(t.sum(), 1)),
                                         "spec": float((~t & ~q).sum() / max((~t).sum(), 1))}
    tr = np.array(true_ramp_all)
    out["true_ramp_fires"] = {f">={RAMP_SUB:g}": float((tr >= RAMP_SUB).mean()), f">={RAMP_BLOCK:g}": float((tr >= RAMP_BLOCK).mean()),
                              "median": float(np.median(tr)), "p90": float(np.quantile(tr, 0.9)), "n_days": len(tr)}
    # the app's chart flag: avg4 > 10 % three weekly checks running (days d, d-7, d-14)
    by = {(a, d): v for a, d, v in avg4_rows}
    f3 = [all(by.get((a, d - 7 * k), -1) > 0.10 for k in range(3)) for a, d, _ in avg4_rows
          if all((a, d - 7 * k) in by for k in range(3))]
    v4 = np.array([v for _, _, v in avg4_rows])
    out["avg4_chart"] = {"n_days": len(v4), "share_gt10": float((v4 > 0.10).mean()),
                         "share_flag3": float(np.mean(f3)) if f3 else None, "n_flag3_eval": len(f3),
                         "q": [float(q) for q in np.quantile(v4, [.1, .5, .9])]}
    return out


# ================================================================ statistics
GH_X, GH_W = np.polynomial.hermite.hermgauss(20)


def _log1pexp(z):
    return np.where(z > 30, z, np.log1p(np.exp(np.minimum(z, 30))))


def logit_fit(y, X, cluster=None):
    """ML logistic; beta, model SE, athlete-cluster robust SE."""
    b = np.zeros(X.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-(X @ b)))
        step = np.linalg.solve(X.T @ (X * (p * (1 - p))[:, None]), X.T @ (y - p))
        b += step
        if np.max(np.abs(step)) < 1e-9:
            break
    p = 1 / (1 + np.exp(-(X @ b)))
    Hinv = np.linalg.inv(X.T @ (X * (p * (1 - p))[:, None]))
    rse = None
    if cluster is not None:
        sc = X * (y - p)[:, None]
        meat = np.zeros((X.shape[1],) * 2)
        groups = np.unique(cluster)
        for c in groups:
            s = sc[cluster == c].sum(0)
            meat += np.outer(s, s)
        rse = np.sqrt(np.diag(Hinv @ meat @ Hinv * len(groups) / (len(groups) - 1)))
    return b, np.sqrt(np.diag(Hinv)), rse


def glmm_logit(y, X, g):
    """Random-intercept logistic, marginal likelihood by Gauss-Hermite quadrature.
    Returns beta, SE, sigma(athlete), log-likelihood."""
    groups = np.unique(g)
    gi = np.searchsorted(groups, g)
    G, lw0 = len(groups), np.log(GH_W)

    def per_group(t):
        b, s = t[:-1], math.exp(t[-1])
        node = math.sqrt(2) * s * GH_X
        eta = (X @ b)[:, None] + node[None, :]
        ll = y[:, None] * eta - _log1pexp(eta)
        per = np.zeros((G, len(GH_X)))
        np.add.at(per, gi, ll)
        return per + lw0[None, :], eta, node

    def nll(t):
        lw, _, _ = per_group(t)
        m = lw.max(1, keepdims=True)
        return -float((np.log(np.exp(lw - m).sum(1)) + m[:, 0] - 0.5 * math.log(math.pi)).sum())

    def grad(t):
        lw, eta, node = per_group(t)
        post = np.exp(lw - lw.max(1, keepdims=True))
        post /= post.sum(1, keepdims=True)
        rw = (y[:, None] - 1 / (1 + np.exp(-eta))) * post[gi]
        return -np.append(X.T @ rw.sum(1), float((rw * node[None, :]).sum()))

    def hess(t, h=1e-5):
        H = np.array([(grad(t + h * e) - grad(t - h * e)) / (2 * h) for e in np.eye(len(t))])
        return (H + H.T) / 2

    b0, _, _ = logit_fit(y, X)
    theta = np.append(b0, math.log(0.5))
    f = nll(theta)
    for _ in range(100):
        gr, H = grad(theta), hess(theta)
        try:
            step = np.linalg.solve(H, gr)
            if gr @ step <= 0:
                step = gr
        except np.linalg.LinAlgError:
            step = gr
        t, fc = 1.0, f
        while t > 1e-6:
            cand = theta - t * step
            fc = nll(cand)
            if fc <= f:
                break
            t /= 2
        if fc > f:
            break
        converged = f - fc < 1e-9
        theta, f = cand, fc
        if converged:
            break
    V = np.linalg.pinv(hess(theta))
    se = np.sqrt(np.clip(np.diag(V), 0, None))
    return theta[:-1], se[:-1], math.exp(theta[-1]), -f


def within_ols(y, X, g):
    """Athlete fixed effects by demeaning; cluster-robust SE."""
    yd, Xd = y.astype(float).copy(), X.astype(float).copy()
    groups = np.unique(g)
    for c in groups:
        m = g == c
        yd[m] -= yd[m].mean()
        Xd[m] -= Xd[m].mean(0)
    XtX_inv = np.linalg.pinv(Xd.T @ Xd)
    b = XtX_inv @ Xd.T @ yd
    u = yd - Xd @ b
    meat = np.zeros((X.shape[1],) * 2)
    for c in groups:
        m = g == c
        s = (Xd[m] * u[m][:, None]).sum(0)
        meat += np.outer(s, s)
    return b, np.sqrt(np.diag(XtX_inv @ meat @ XtX_inv * len(groups) / (len(groups) - 1)))


def p_from_z(z):
    return math.erfc(abs(z) / math.sqrt(2))


def auc(score, y):
    order = np.argsort(score, kind="mergesort")
    s, yy = score[order], y[order]
    ranks = np.empty(len(s))
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    n1 = yy.sum()
    n0 = len(yy) - n1
    return float((ranks[yy == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def auc_ci(score, y, g, reps=400, seed=7):
    rng = np.random.default_rng(seed)
    groups = np.unique(g)
    idx = {c: np.flatnonzero(g == c) for c in groups}
    bs = [auc(score[p], y[p]) for p in
          (np.concatenate([idx[c] for c in rng.choice(groups, len(groups))]) for _ in range(reps))]
    lo, hi = np.nanpercentile(bs, [2.5, 97.5])
    return auc(score, y), float(lo), float(hi)


def holm(ps):
    order = np.argsort(ps)
    adj, run, m = np.empty(len(ps)), 0.0, len(ps)
    for r, i in enumerate(order):
        run = max(run, min(1.0, (m - r) * ps[i]))
        adj[i] = run
    return adj


def bh(ps):
    m = len(ps)
    order = np.argsort(ps)[::-1]
    adj, run = np.empty(m), 1.0
    for r, i in enumerate(order):
        run = min(run, ps[i] * m / (m - r))
        adj[i] = run
    return adj


# ================================================================ analysis helpers
TESTS: list[dict] = []


def categorize(x, cuts, labels):
    """cuts are upper bounds (inclusive) of all but the last label; NaN -> None."""
    out = np.empty(len(x), object)
    for i, v in enumerate(x):
        if np.isnan(v):
            out[i] = None
            continue
        out[i] = next((l for c, l in zip(cuts, labels) if v <= c), labels[-1])
    return out


def cat_effect(T, mask, name, cat, levels, ref, primary_level=None, adjust=True):
    """Category exposure vs injury on day d: risk table; RE-logistic OR; cluster-robust pooled OR;
    adjusted for log chronic km."""
    keep = mask & np.array([c is not None for c in cat])
    y, g, c = T["y"][keep], T["aid"][keep], cat[keep]
    table = [{"level": lv, "rows": int((c == lv).sum()), "events": int(y[c == lv].sum()),
              "risk": float(y[c == lv].mean()) if (c == lv).any() else None, "share": float((c == lv).mean())}
             for lv in levels]
    others = [t["level"] for t in table if t["level"] != ref and t["events"] > 0]
    D = np.column_stack([np.ones(len(y))] + [(c == lv).astype(float) for lv in others])
    b, se, sig, _ = glmm_logit(y, D, g)
    bp, _, rse = logit_fit(y, D, g)
    if adjust:
        lc = np.log1p(T["chronic_km"][keep])
        ba, sea, _, _ = glmm_logit(y, np.column_stack([D, (lc - lc.mean()) / lc.std()]), g)
    res = {"name": name, "n": int(keep.sum()), "athletes": int(len(np.unique(g))), "events": int(y.sum()),
           "table": table, "ref": ref, "sigma_athlete": sig, "effects": {}}
    for k, lv in enumerate(others, start=1):
        e = {"or": math.exp(b[k]), "lo": math.exp(b[k] - 1.96 * se[k]), "hi": math.exp(b[k] + 1.96 * se[k]),
             "p": p_from_z(b[k] / se[k]), "or_pooled": math.exp(bp[k]), "lo_rob": math.exp(bp[k] - 1.96 * rse[k]),
             "hi_rob": math.exp(bp[k] + 1.96 * rse[k]), "p_rob": p_from_z(bp[k] / rse[k])}
        if adjust:
            e.update(or_adj=math.exp(ba[k]), lo_adj=math.exp(ba[k] - 1.96 * sea[k]),
                     hi_adj=math.exp(ba[k] + 1.96 * sea[k]), p_adj=p_from_z(ba[k] / sea[k]))
        res["effects"][lv] = e
        # the robust p is the inference p (control rows overlap day to day)
        TESTS.append({"name": f"{name}: {lv} vs {ref}", "family": "primary" if lv == primary_level else "secondary",
                      "p": max(e["p"], e["p_rob"]), "or": e["or"], "lo": min(e["lo"], e["lo_rob"]),
                      "hi": max(e["hi"], e["hi_rob"])})
    return res


def cont_effect(T, mask, name, key, per, clip=None, sign=1.0):
    x = T[key].copy()
    keep = mask & ~np.isnan(x)
    x = x[keep]
    if clip:
        x = np.clip(x, *clip)
    x = sign * x
    y, g = T["y"][keep], T["aid"][keep]
    D = np.column_stack([np.ones(len(y)), x / per])
    b, se, sig, _ = glmm_logit(y, D, g)
    bp, _, rse = logit_fit(y, D, g)
    a, lo, hi = auc_ci(x, y, g)
    res = {"name": name, "key": key, "per": per, "sign": sign, "n": int(keep.sum()), "events": int(y.sum()),
           "or": math.exp(b[1]), "lo": math.exp(b[1] - 1.96 * se[1]), "hi": math.exp(b[1] + 1.96 * se[1]),
           "p": p_from_z(b[1] / se[1]), "lo_rob": math.exp(bp[1] - 1.96 * rse[1]),
           "hi_rob": math.exp(bp[1] + 1.96 * rse[1]), "p_rob": p_from_z(bp[1] / rse[1]),
           "auc": a, "auc_lo": lo, "auc_hi": hi,
           "case_median": float(np.median(sign * x[y == 1])), "control_median": float(np.median(sign * x[y == 0]))}
    TESTS.append({"name": f"{name}（連續，每 {per}）", "family": "secondary", "p": max(res["p"], res["p_rob"]),
                  "or": res["or"], "lo": min(res["lo"], res["lo_rob"]), "hi": max(res["hi"], res["hi_rob"])})
    return res


def bins(T, mask, key, edges):
    x = T[key]
    keep = mask & ~np.isnan(x)
    x, y = x[keep], T["y"][keep]
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (x > lo) & (x <= hi)
        out.append({"lo": lo, "hi": hi, "rows": int(m.sum()), "events": int(y[m].sum()),
                    "risk": float(y[m].mean()) if m.any() else None})
    return out


def flag_accuracy(T, mask, name, flag):
    keep = mask & ~np.isnan(flag)
    f, y = flag[keep] > 0, T["y"][keep] > 0
    tp, fp, fn, tn = (f & y).sum(), (f & ~y).sum(), (~f & y).sum(), (~f & ~y).sum()
    sens, spec = tp / max(tp + fn, 1), tn / max(tn + fp, 1)
    return {"flag": name, "n": int(keep.sum()), "fires": float(f.mean()), "sens": float(sens), "spec": float(spec),
            "ppv": float(tp / max(tp + fp, 1)), "base": float(y.mean()), "balanced_auc": float((sens + spec) / 2)}


def fatigue_effect(T, mask, name, flag, outcome, primary=False):
    keep = mask & ~np.isnan(flag) & ~np.isnan(T[outcome])
    g, y, x = T["aid"][keep], T[outcome][keep].copy(), flag[keep]
    for c in np.unique(g):  # z-score within athlete
        m = g == c
        sd = y[m].std()
        y[m] = (y[m] - y[m].mean()) / sd if sd > 0 else 0
    b, se = within_ols(y, x[:, None], g)
    res = {"name": name, "outcome": outcome, "n": int(keep.sum()), "beta_sd": float(b[0]),
           "lo": float(b[0] - 1.96 * se[0]), "hi": float(b[0] + 1.96 * se[0]), "p": p_from_z(b[0] / se[0]),
           "share_flag": float(x.mean())}
    TESTS.append({"name": name, "family": "primary" if primary else "secondary", "p": res["p"],
                  "beta_sd": res["beta_sd"], "lo": res["lo"], "hi": res["hi"]})
    return res


def within_corr(T, mask, a, b):
    keep = mask & ~np.isnan(T[a]) & ~np.isnan(T[b])
    g = T["aid"][keep]
    xa, xb = T[a][keep].copy(), T[b][keep].copy()
    for c in np.unique(g):
        m = g == c
        xa[m] -= xa[m].mean()
        xb[m] -= xb[m].mean()
    return float(np.corrcoef(xa, xb)[0, 1])


# ================================================================ main
def run(data_dir):
    TESTS.clear()
    iw, aw = read(os.path.join(data_dir, "week_approach_maskedID_timeseries.csv"))
    idd, ad = read(os.path.join(data_dir, "day_approach_maskedID_timeseries.csv"))
    T = week_table(iw, aw)
    daily, week0 = day_streams(idd, ad)
    T["km0"] = T["km"][0]
    T["monotony"], T["strain"] = np.full(len(T["y"]), np.nan), np.full(len(T["y"]), np.nan)
    for i, (a, d) in enumerate(zip(T["aid"], T["day"])):
        w = week0.get((a, d))
        if w is not None:
            T["monotony"][i], T["strain"][i] = monotony_strain(daily_tss(w))
    y = T["y"]
    inj_days = defaultdict(list)
    for a, d in zip(T["aid"][y == 1], T["day"][y == 1]):
        inj_days[a].append(d)
    T["since_inj"] = np.array([min([d - x for x in inj_days[a] if x < d], default=10 ** 6)
                               for a, d in zip(T["aid"], T["day"])], float)
    gaps = np.array([d - x for a in inj_days for x, d in zip(sorted(inj_days[a]), sorted(inj_days[a])[1:])])
    events_y = {(a, d): yy for a, d, yy in zip(T["aid"], T["day"], y)}
    active = T["km"][1] + T["km"][2] >= 2 * MIN_PREV_KM          # had a training base in W1-W2
    thin = active & ((y == 1) | (T["day"] % 7 == 0))                # sensitivity: one control day per week

    out = {"rows": int(len(y)), "athletes": int(len(np.unique(T["aid"]))), "injuries": int(y.sum()),
           "active_rows": int(active.sum()), "active_injuries": int(y[active].sum()),
           "day_risk_active": float(y[active].mean()),
           "median_week_km": float(np.median(T["km"][0][active])), "median_week_h": float(np.median(T["hours"][0][active])),
           "median_ctl_proxy": float(np.nanmedian(T["ctl_proxy"][active])),
           "km_w0_w1_w2_cases": [float(np.median(T["km"][k][active & (y == 1)])) for k in range(3)],
           "km_w0_w1_w2_controls": [float(np.median(T["km"][k][active & (y == 0)])) for k in range(3)],
           "recov_vs_km0_within_r": within_corr(T, active, "recov0", "km0"),
           "recov_vs_exert_within_r": within_corr(T, active, "recov0", "exert0"),
           "injury_gap_days_q": [float(q) for q in np.quantile(gaps, [.1, .25, .5, .75])],
           "injuries_within_42d_of_previous": float(np.mean(T["since_inj"][y == 1] <= 42))}
    out["stream"] = control_stream_checks(daily, events_y, T)
    R = out["results"] = {}

    # 1. week-over-week step ---------------------------------------------------------
    lv3 = ["<=10%", "10-20%", ">20%"]
    c3 = lambda k: categorize(T[k], [STEP_HOLD, STEP_BLOCK], lv3)
    R["step0"] = cat_effect(T, active, "週增量（km，前 1 週 vs 前 2 週）", c3("step0"), lv3, "<=10%", ">20%")
    R["step0_h"] = cat_effect(T, active, "週增量（時數推估）", c3("step0_h"), lv3, "<=10%")
    R["step1"] = cat_effect(T, active, "週增量（km，延遲 1 週：W1 vs W2）", c3("step1"), lv3, "<=10%")
    lv5 = ["<=-10%", "-10-10%", "10-20%", "20-30%", ">30%"]
    R["step0_5"] = cat_effect(T, active, "週增量（km，5 級）", categorize(T["step0"], [-0.10, 0.10, 0.20, 0.30], lv5),
                              lv5, "-10-10%")
    R["step0_thin"] = cat_effect(T, thin, "週增量（km，對照每週 1 天）", c3("step0"), lv3, "<=10%", adjust=False)
    lv2 = ["<=10%", ">10%"]
    R["avg2"] = cat_effect(T, active, "2 週平均增幅 > 10%（km；4 週平均的替代）",
                           categorize(T["avg2"], [STEP_HOLD], lv2), lv2, "<=10%", ">10%")
    R["avg2_h"] = cat_effect(T, active, "2 週平均增幅 > 10%（時數推估）", categorize(T["avg2_h"], [STEP_HOLD], lv2),
                             lv2, "<=10%")
    lv3b = ["<=10%", "10-20%", ">20%"]
    R["avg2_3"] = cat_effect(T, active, "2 週平均增幅（km，3 級）", categorize(T["avg2"], [0.10, 0.20], lv3b), lv3b,
                             "<=10%")
    R["step0_c"] = cont_effect(T, active, "週增量（km）", "step0", 0.10, clip=(-1, 2))
    R["step1_c"] = cont_effect(T, active, "週增量延遲 1 週（km）", "step1", 0.10, clip=(-1, 2))
    R["avg2_c"] = cont_effect(T, active, "2 週平均增幅（km）", "avg2", 0.10, clip=(-1, 2))
    R["step0_bins"] = bins(T, active, "step0", [-np.inf, -0.3, -0.1, 0, 0.1, 0.2, 0.3, 0.5, np.inf])
    R["avg2_bins"] = bins(T, active, "avg2", [-np.inf, -0.2, -0.1, 0, 0.05, 0.1, 0.2, 0.3, np.inf])

    # 2. CTL ramp ----------------------------------------------------------------------
    lvr = ["<=0", "0-5", "5-8", ">=8"]
    rc = categorize(T["ramp"], [0, RAMP_SUB - 1e-9, RAMP_BLOCK - 1e-9], lvr)
    R["ramp"] = cat_effect(T, active, "CTL ramp（21 天替代，TSS 推估／週）", rc, lvr, "0-5", ">=8")
    lvp = ["<=0", "0-5%", "5-10%", ">10%"]
    R["ramp_pct"] = cat_effect(T, active, "CTL ramp（% CTL／週）", categorize(T["ramp_pct"], [0, 0.05, 0.10], lvp),
                               lvp, "0-5%")
    R["ramp_c"] = cont_effect(T, active, "CTL ramp（TSS 推估）", "ramp", 1.0, clip=(-30, 30))
    R["ramp_pct_c"] = cont_effect(T, active, "CTL ramp（% CTL）", "ramp_pct", 0.05, clip=(-1, 1))
    R["ramp_bins"] = bins(T, active, "ramp", [-np.inf, -5, -2, 0, 2, 3, 5, 8, 12, np.inf])

    # 3. ACWR / monotony / strain -----------------------------------------------------
    lva = ["<0.8", "0.8-1.3", "1.3-1.5", ">1.5"]
    ca = lambda k: categorize(T[k], [ACWR_BANDS[0] - 1e-9, ACWR_BANDS[1], ACWR_BANDS[2]], lva)
    R["acwr_u"] = cat_effect(T, active, "ACWR 7:14 uncoupled（TSS 推估）", ca("acwr_u"), lva, "0.8-1.3", ">1.5")
    R["acwr_c"] = cat_effect(T, active, "ACWR 7:21 coupled（TSS 推估）", ca("acwr_c"), lva, "0.8-1.3")
    R["acwr_u_c"] = cont_effect(T, active, "ACWR uncoupled", "acwr_u", 0.1, clip=(0, 3))
    R["acwr_c_c"] = cont_effect(T, active, "ACWR coupled", "acwr_c", 0.1, clip=(0, 3))
    lvm = ["<=2", ">2"]
    R["mono"] = cat_effect(T, active, "Monotony > 2（Foster，前 7 天）", categorize(T["monotony"], [MONOTONY_HIGH], lvm),
                           lvm, "<=2", ">2")
    sq = np.nanquantile(T["strain"][active], [0.25, 0.5, 0.75])
    out["strain_quartiles"] = [float(v) for v in sq]
    lvq = ["Q1", "Q2", "Q3", "Q4"]
    R["strain"] = cat_effect(T, active, "Strain 四分位（Foster）", categorize(T["strain"], list(sq), lvq), lvq, "Q1")
    R["mono_c"] = cont_effect(T, active, "Monotony", "monotony", 0.5, clip=(0, 6))
    R["strain_c"] = cont_effect(T, active, "Strain", "strain", 500.0)
    R["mono_bins"] = bins(T, active, "monotony", [0, 0.5, 1.0, 1.5, 2.0, 2.5, np.inf])

    # 4. intensity distribution -------------------------------------------------------
    lvl = ["<65%", "65-75%", ">=75%"]
    cl = lambda k: categorize(T[k], [LOW_SHARE_WATCH - 1e-9, LOW_SHARE_MIN - 1e-9], lvl)
    lv75 = ["<75%", ">=75%"]
    R["low75"] = cat_effect(T, active, "低強度 < 75%（時間推估，21 天）", categorize(T["low_time"], [LOW_SHARE_MIN - 1e-9], lv75),
                            lv75, ">=75%", "<75%")
    R["low_time"] = cat_effect(T, active, "低強度佔比（時間推估，3 級）", cl("low_time"), lvl, ">=75%")
    R["low_km"] = cat_effect(T, active, "低強度佔比（km，3 級）", cl("low_km"), lvl, ">=75%")
    R["low_c"] = cont_effect(T, active, "低強度佔比下降（時間推估）", "low_time", 0.05, sign=-1.0)
    R["low_bins"] = bins(T, active, "low_time", [0, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0])
    out["low_time_q"] = [float(q) for q in np.nanquantile(T["low_time"][active], [.1, .25, .5, .75, .9])]
    out["low_km_q"] = [float(q) for q in np.nanquantile(T["low_km"][active], [.1, .25, .5, .75, .9])]
    f75 = np.where(np.isnan(T["low_prev"]), np.nan, (T["low_prev"] < LOW_SHARE_MIN).astype(float))
    ctrl = active & (y == 0)
    R["fat_recov"] = fatigue_effect(T, ctrl, "低強度 < 75%（W1–W2）→ W0 主觀恢復", f75, "recov0", primary=True)
    R["fat_exert"] = fatigue_effect(T, ctrl, "低強度 < 75%（W1–W2）→ W0 主觀費力", f75, "exert0")
    fs = np.where(np.isnan(T["step1"]), np.nan, (T["step1"] > STEP_BLOCK).astype(float))
    R["fat_step"] = fatigue_effect(T, ctrl, "週增量 > 20%（W1 vs W2）→ W0 主觀恢復", fs, "recov0")
    T["ramp1"] = np.where(T["km"][2] >= MIN_PREV_KM, FC * (T["tss"][1] - T["tss"][2]), np.nan)
    fr = np.where(np.isnan(T["ramp1"]), np.nan, (T["ramp1"] >= RAMP_BLOCK).astype(float))
    R["fat_ramp"] = fatigue_effect(T, ctrl, "CTL ramp ≥ 8（W1 對 W2 的替代）→ W0 主觀恢復", fr, "recov0")

    # sensitivity: one control day per athlete-week (less day-to-day overlap, so the RE-model CI is honest)
    for key, name, cat, levels, ref in (
            ("step1", "週增量延遲 1 週", c3("step1"), lv3, "<=10%"),
            ("avg2", "2 週平均增幅", categorize(T["avg2"], [STEP_HOLD], lv2), lv2, "<=10%"),
            ("ramp", "CTL ramp", rc, lvr, "0-5"),
            ("acwr_u", "ACWR uncoupled", ca("acwr_u"), lva, "0.8-1.3"),
            ("mono", "Monotony", categorize(T["monotony"], [MONOTONY_HIGH], lvm), lvm, "<=2"),
            ("strain", "Strain", categorize(T["strain"], list(sq), lvq), lvq, "Q1"),
            ("low75", "低強度 < 75%（時間）", categorize(T["low_time"], [LOW_SHARE_MIN - 1e-9], lv75), lv75, ">=75%"),
            ("low_km", "低強度（km）", cl("low_km"), lvl, ">=75%")):
        R[f"thin_{key}"] = cat_effect(T, thin, f"{name}（對照每週 1 天）", cat, levels, ref, adjust=False)
        # return-to-training after an earlier injury makes big steps and new injuries go together
        R[f"noprev_{key}"] = cat_effect(T, active & (T["since_inj"] > 42), f"{name}（排除前一次受傷後 42 天）",
                                        cat, levels, ref, adjust=False)

    # flags as classifiers -----------------------------------------------------------
    fl = lambda k, f: np.where(np.isnan(T[k]), np.nan, f(T[k]).astype(float))
    out["flags"] = [flag_accuracy(T, active, n, f) for n, f in (
        ("週增量 > 10%", fl("step0", lambda v: v > STEP_HOLD)),
        ("週增量 > 20%", fl("step0", lambda v: v > STEP_BLOCK)),
        ("2 週平均 > 10%", fl("avg2", lambda v: v > 0.10)),
        ("ramp ≥ 5", fl("ramp", lambda v: v >= RAMP_SUB)),
        ("ramp ≥ 8", fl("ramp", lambda v: v >= RAMP_BLOCK)),
        ("ACWR > 1.3", fl("acwr_u", lambda v: v > 1.3)),
        ("ACWR > 1.5", fl("acwr_u", lambda v: v > 1.5)),
        ("monotony > 2", fl("monotony", lambda v: v > MONOTONY_HIGH)),
        ("低強度 < 75%（時間）", fl("low_time", lambda v: v < LOW_SHARE_MIN)),
        ("低強度 < 75%（km）", fl("low_km", lambda v: v < LOW_SHARE_MIN)))]

    # all guardrails together (in-sample, optimistic) for comparison with the paper's models
    keys = ["step0", "avg2", "ramp", "acwr_u", "monotony", "low_time"]
    keep = active & np.all([~np.isnan(T[k]) for k in keys], axis=0)
    X = np.column_stack([np.ones(keep.sum())] + [np.clip(T[k][keep], -3, 6) for k in keys])
    b, _, _ = logit_fit(T["y"][keep], X, T["aid"][keep])
    out["combined_auc_in_sample"] = {"n": int(keep.sum()), "events": int(T["y"][keep].sum()),
                                     "auc": auc(X @ b, T["y"][keep])}

    prim = [t for t in TESTS if t["family"] == "primary"]
    for t, a in zip(prim, holm(np.array([t["p"] for t in prim]))):
        t["p_holm"] = float(a)
    for t, a in zip(TESTS, bh(np.array([t["p"] for t in TESTS]))):
        t["q_bh"] = float(a)
    out["tests"] = TESTS
    return out


def _ci(e, k="or", lo="lo", hi="hi"):
    return f"{e[k]:.2f} ({e[lo]:.2f}–{e[hi]:.2f})"


def report(out):
    print({k: v for k, v in out.items() if k not in ("results", "tests", "flags", "stream")})
    print("stream checks:", json.dumps(out["stream"], ensure_ascii=False, indent=1))
    for key, r in out["results"].items():
        if isinstance(r, list):
            print(f"\n== {key} (bins)")
            for b in r:
                rk = "-" if b["risk"] is None else f"{b['risk'] * 100:.2f}%"
                print(f"   ({b['lo']:.3g}, {b['hi']:.3g}]: rows {b['rows']:6d} events {b['events']:4d} risk {rk}")
            continue
        print(f"\n== {key}: {r['name']} n={r['n']} events={r.get('events')}")
        if "table" in r:
            for t in r["table"]:
                rk = "-" if t["risk"] is None else f"{t['risk'] * 100:.2f}%"
                print(f"   {t['level']:>8}: rows {t['rows']:6d} ({t['share'] * 100:4.1f}%) events {t['events']:4d} risk {rk}")
            for lv, e in r["effects"].items():
                s = f"   {lv} vs {r['ref']}: OR {_ci(e)} p={e['p']:.3g} | robust {_ci(e, 'or_pooled', 'lo_rob', 'hi_rob')} p={e['p_rob']:.3g}"
                if "or_adj" in e:
                    s += f" | adj {_ci(e, 'or_adj', 'lo_adj', 'hi_adj')}"
                print(s)
            print(f"   sigma_athlete={r['sigma_athlete']:.2f}")
        elif "auc" in r:
            print(f"   OR per {r['per']}: {_ci(r)} p={r['p']:.3g} | robust ({r['lo_rob']:.2f}–{r['hi_rob']:.2f}) "
                  f"| AUC {r['auc']:.3f} ({r['auc_lo']:.3f}–{r['auc_hi']:.3f}) | median case {r['case_median']:.3g} "
                  f"control {r['control_median']:.3g}")
        else:
            print(f"   beta {r['beta_sd']:+.3f} SD ({r['lo']:+.3f} to {r['hi']:+.3f}) p={r['p']:.3g} "
                  f"flagged {r['share_flag'] * 100:.1f}%")
    print("\n== flags as classifiers (injury on day d)")
    for f in out["flags"]:
        print(f"   {f['flag']:>14}: fires {f['fires'] * 100:5.1f}%  sens {f['sens']:.2f}  spec {f['spec']:.2f}  "
              f"PPV {f['ppv'] * 100:.2f}% (base {f['base'] * 100:.2f}%)  balanced AUC {f['balanced_auc']:.3f}")
    print("\ncombined:", out["combined_auc_in_sample"])
    print("\n== tests (Holm on primary, BH on all)")
    for t in out["tests"]:
        eff = f"OR {t['or']:.2f} ({t['lo']:.2f}–{t['hi']:.2f})" if "or" in t else \
            f"beta {t['beta_sd']:+.3f} SD ({t['lo']:+.3f}–{t['hi']:+.3f})"
        print(f"   [{t['family'][:4]}] {t['name']}: {eff} p={t['p']:.3g}"
              + (f" holm={t['p_holm']:.3g}" if "p_holm" in t else "") + f" q={t['q_bh']:.3g}")


def main():
    ap = argparse.ArgumentParser(description="Validate training-load guardrails on Lövdal 2021")
    ap.add_argument("--data", default=os.path.join(os.path.expanduser("~"), "Datasets", "lovdal"))
    ap.add_argument("--json", help="write all results to this file")
    a = ap.parse_args()
    out = run(a.data)
    report(out)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
