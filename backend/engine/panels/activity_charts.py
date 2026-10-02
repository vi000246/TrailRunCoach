"""
Single-activity charts of 圖表分析 → 單次活動 (views/workout.json, chart
kind "activity" with a `chart`):

hrpower
    Heart rate and 30-s power over elapsed time as two stacked panels on one
    time axis — two units, so never one shared y-scale. Each plotted point
    also carries running totals (cumulative sums on a 1-s grid), so the
    viewer computes the stats of any brushed range exactly at the plot's
    resolution without another request: elapsed and moving time, distance,
    moving pace, average / max HR, average power, NP, Pw:HR (average power ÷
    average HR), EF (NP ÷ average HR) and the range's Pw:HR halves
    (WKO5's definition: the range cut at half its length). `range_stats`
    is the reference implementation; the viewer mirrors it.
hrzones / powerzones
    Time in zones of this activity under a zone model the viewer picks (and
    remembers; defaults Friel % LTHR and Palladino % CP, no %HRmax model).
    The tables are zones.py's (Friel, Classic, Palladino, Stryd, RQ) plus the evaluator's (WKO5 Classic power, iLevels from the
    PD model of the previous 90 days' mean-max power — the WKO5 chart
    「Time in iLevels」's own expression).
hrtrend
    WKO5 「Heart Rate Variation and Trend」 (WKO5 Workout View → Zone &
    Variation, docs/wko5-views/workout-view.json): heart rate, its least-
    squares trend `slr(heartrate)`, avg ± 1 sample SD (`stddev`, n − 1), and
    from the companion 「Heart Rate Format」 chart the variability class
    (SD / avg < .33 steady, < .66 mixed, else variable) and the trend class
    (slope `slrm` < −0.0005 bpm/s declining, < +0.0005 consistent, else
    increasing), plus PWHR (WKO5: the recording cut at half its length,
    (e1 − e2) / e1 with e = avg power / avg HR). All on the raw samples, x =
    elapsed seconds, unweighted — WKO5's formulas (functions.md §7).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from backend.engine import zones as Z

MAX_DT = 30.0            # a source interval > 30 s is a gap (workout_review.MAX_DT)
STOP_KMH = 1.6           # moving = above WKO5's 1 mph (workout_review.STOP_KMH)
POINTS = 1500            # plotted points per activity (each with its running totals)
NP_WINDOW = 30           # Coggan's NP: 30-s rolling power, 4th-power mean
NP_SCALE = 100.0         # cumulative NP sums are of (P/100)^4, to keep the numbers small
TREND_MOVE = 0.0005      # WKO5 Heart Rate Format: |slrm| < 0.0005 bpm/s = consistent
CV_STEADY, CV_MIXED = 0.33, 0.66   # WKO5 Heart Rate Format: stddev / avg

CHARTS = ("hrpower", "hrzones", "powerzones", "hrtrend")


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _r(v, nd: int = 1) -> Optional[float]:
    x = _f(v)
    return None if x is None else round(x, nd)


# ---------------------------------------------------------------------------
# the 1-s grid
# ---------------------------------------------------------------------------

def _on_grid(rel: np.ndarray, x, g: np.ndarray) -> Optional[np.ndarray]:
    """x (aligned with rel, seconds from the start) linearly on the grid g;
    NaN outside its samples and inside a source gap > 30 s."""
    if x is None:
        return None
    x = np.asarray(x, dtype=float)
    n = min(len(rel), len(x))
    r, x = rel[:n], x[:n]
    ok = np.isfinite(r) & np.isfinite(x)
    if ok.sum() < 2:
        return None
    tt, xx = r[ok], x[ok]
    y = np.interp(g, tt, xx, left=np.nan, right=np.nan)
    j = np.clip(np.searchsorted(tt, g), 1, len(tt) - 1)
    y[(tt[j] - tt[j - 1]) > MAX_DT] = np.nan
    return y


def _ffill(x: np.ndarray) -> np.ndarray:
    ok = np.isfinite(x)
    if not ok.any():
        return np.zeros(len(x))
    idx = np.where(ok, np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    y = x[idx]
    y[: int(np.argmax(ok))] = x[ok][0]
    return y


def rolling(x: np.ndarray, n: int, min_frac: float = 0.5) -> np.ndarray:
    """Trailing n-sample mean of the finite values; NaN until n samples have
    passed or when fewer than min_frac of the window is finite."""
    ok = np.isfinite(x)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))])
    cn = np.concatenate([[0.0], np.cumsum(ok.astype(float))])
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    s = cs[n:] - cs[:-n]
    c = cn[n:] - cn[:-n]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[n - 1:] = np.where(c >= n * min_frac, s / np.maximum(c, 1), np.nan)
    return out


def grid(ds, w) -> Optional[dict]:
    """The activity on a 1-s grid (seconds from the first sample): hr, power,
    speed (km/h), dist (km, carried over gaps), rec (recorded second: the
    source interval ≤ 30 s), moving (recorded and, with speed, > 1.6 km/h)."""
    from backend.engine.workout_review import _samples
    s = _samples(ds, w)
    if s is None:
        return None
    t = np.asarray(s["t"], dtype=float)
    ok = np.isfinite(t)
    if ok.sum() < 2:
        return None
    t0, t1 = float(t[ok][0]), float(t[ok][-1])
    rel = t - t0
    g = np.arange(0.0, math.floor(t1 - t0) + 1.0)
    ones = np.where(ok, 1.0, np.nan)
    rec = np.isfinite(_on_grid(rel, ones, g))
    hr = _on_grid(rel, s.get("hr"), g)
    if hr is not None:
        hr = np.where(hr > 0, hr, np.nan)
    power = _on_grid(rel, s.get("power"), g)
    if power is not None:
        power = np.where(power >= 0, power, np.nan)
    speed = _on_grid(rel, s.get("speed"), g)
    dist = _on_grid(rel, s.get("dist"), g)
    moving = rec.copy()
    if speed is not None:
        moving &= ~(np.isfinite(speed) & (speed <= STOP_KMH))
    return {"g": g, "rec": rec, "moving": moving, "hr": hr, "power": power, "speed": speed,
            "dist": None if dist is None else _ffill(dist), "t0": t0}


# ---------------------------------------------------------------------------
# hrpower: two stacked panels + running totals for the brushed range
# ---------------------------------------------------------------------------

def _cum(v: np.ndarray, bounds: np.ndarray) -> list:
    c = np.concatenate([[0.0], np.cumsum(v)])
    return c[bounds]


def hrpower(ds, w, points: int = POINTS) -> dict:
    G = grid(ds, w)
    if G is None or (G["hr"] is None and G["power"] is None):
        return {"empty": "這筆活動沒有心率也沒有功率"}
    n = len(G["g"])
    step = max(1, int(math.ceil(n / points)))
    starts = np.arange(0, n, step)
    bounds = np.append(starts, n)
    nanv = np.full(n, np.nan)
    hr = G["hr"] if G["hr"] is not None else nanv
    p = G["power"] if G["power"] is not None else nanv
    r30 = rolling(p, NP_WINDOW)
    hv, pv, nv = np.isfinite(hr), np.isfinite(p), np.isfinite(r30)
    both = hv & pv

    def bucket_mean(x, ok):
        s = np.add.reduceat(np.where(ok, x, 0.0), starts)
        c = np.add.reduceat(ok.astype(float), starts)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(c > 0, s / np.maximum(c, 1), np.nan)

    hmax = np.fmax.reduceat(np.where(hv, hr, -np.inf), starts)
    hmax = np.where(np.isfinite(hmax), hmax, np.nan)
    dist = G["dist"]
    d_at = None if dist is None else np.append(dist, dist[-1])[bounds]
    cum = {
        "mov": _cum(G["moving"].astype(float), bounds),
        "hn": _cum(hv.astype(float), bounds), "hs": _cum(np.where(hv, hr, 0.0), bounds),
        "pn": _cum(pv.astype(float), bounds), "ps": _cum(np.where(pv, p, 0.0), bounds),
        "nn": _cum(nv.astype(float), bounds),
        "n4": _cum(np.where(nv, (r30 / NP_SCALE) ** 4, 0.0), bounds),
        "bn": _cum(both.astype(float), bounds), "bh": _cum(np.where(both, hr, 0.0), bounds),
        "bp": _cum(np.where(both, p, 0.0), bounds),
        "d": d_at,
    }
    from backend.engine.workout_review import _thresholds
    aet, lthr, cp = _thresholds(ds, w)
    has_hr, has_p = bool(hv.any()), bool(pv.any())
    return {
        "step": step, "n": n,
        "x": [float(x) for x in starts],
        "hr": [_r(v, 0) for v in bucket_mean(hr, hv)] if has_hr else None,
        "power": [_r(v, 0) for v in bucket_mean(r30, nv)] if has_p else None,
        "hr_max": [_r(v, 0) for v in hmax] if has_hr else None,
        "cum": {k: (None if v is None else [_r(x, 4 if k in ("n4", "d") else 1) for x in v]) for k, v in cum.items()},
        "ref": {"aet": _r(aet, 0), "lthr": _r(lthr, 0), "cp": _r(cp, 0)},
        "has_hr": has_hr, "has_power": has_p, "has_dist": dist is not None,
        "empty": None,
    }


def range_stats(res: dict, ka: int, kb: int) -> dict:
    """Stats of plotted buckets ka … kb−1 (the viewer's brushed range) from
    the running totals of `hrpower` — the viewer computes the same."""
    K = len(res["x"])
    ka, kb = max(0, min(ka, K - 1)), max(1, min(kb, K))
    if kb <= ka:
        kb = ka + 1
    c = res["cum"]
    bnd = lambda k: res["x"][k] if k < K else float(res["n"])
    d = lambda name: c[name][kb] - c[name][ka]

    def ratio(a, b):
        return a / b if b else None
    elapsed = bnd(kb) - bnd(ka)
    moving = d("mov")
    km = d("d") if c.get("d") is not None else None
    hr_avg = ratio(d("hs"), d("hn"))
    p_avg = ratio(d("ps"), d("pn"))
    n4 = ratio(d("n4"), d("nn"))
    npw = NP_SCALE * n4 ** 0.25 if n4 else None
    hmax = [v for v in (res["hr_max"] or [])[ka:kb] if v is not None]
    # Pw:HR halves (WKO5: the range cut at half its length)
    km_mid = ka + (kb - ka) / 2.0
    mid = int(round(km_mid))
    drift = None
    if kb - ka >= 2 and ka < mid < kb:
        def e(a, b):
            n_ = c["bn"][b] - c["bn"][a]
            if not n_:
                return None
            h = (c["bh"][b] - c["bh"][a]) / n_
            return ((c["bp"][b] - c["bp"][a]) / n_) / h if h else None
        e1, e2 = e(ka, mid), e(mid, kb)
        drift = (e1 - e2) / e1 if e1 and e2 is not None else None
    return {"elapsed_s": elapsed, "moving_s": moving, "km": km,
            "pace_s_per_km": moving / km if km and km > 0.05 else None,
            "hr_avg": hr_avg, "hr_max": max(hmax) if hmax else None,
            "p_avg": p_avg, "np": npw,
            "pw_hr": p_avg / hr_avg if p_avg is not None and hr_avg else None,
            "ef": npw / hr_avg if npw and hr_avg else None,
            "pw_hr_drift": drift}


# ---------------------------------------------------------------------------
# time in zones
# ---------------------------------------------------------------------------

ILEVEL_NAMES = [("1", "Recovery"), ("2", "Endurance"), ("3", "Tempo"), ("4a", "Sweet Spot"), ("4", "FTP"),
                ("5", "FTP/FRC"), ("6", "FRC"), ("7a", "FRC/Pmax"), ("7", "Pmax")]
# WKO5 「Time in iLevels」 (WKO5 Workout View → Zone & Variation): level i's top
ILEVEL_EXPR = "levelto(athleterange(date-89,date,(meanmax(power))),{i})"


HR_MODELS = [
    {"id": "frielhr", "title": "Friel 7 區（% LTHR）", "basis": "lthr", "zones": lambda: Z.FRIEL_HR,
     "source": "Friel 心率區間（WKO5 的 Friel HR 表，% LTHR）"},
    {"id": "classichr", "title": "WKO5 Classic 5 區（% LTHR）", "basis": "lthr", "zones": lambda: Z.CLASSIC_HR,
     "source": "WKO5 Classic HR 區間（% LTHR）"},
    {"id": "seiler3", "title": "Seiler 3 區（AeT／LTHR）", "basis": "aet_lthr",
     "source": "Seiler 三區模型：1 區 < 第一閾值、2 區 兩閾值之間、3 區 > 第二閾值；這裡第一閾值用 AeT、第二用 LTHR"},
    # no %HRmax model (zones.py: Iannetta 2020); a remembered 「hrmax5」 choice is no
    # longer in the list, so the viewer falls back to the default (Friel)
    {"id": "rqhrr", "title": "徐國峰 RQ 儲備心率（% HRR）", "basis": "hrr", "zones": lambda: Z.RQ_HRR_ZONES,
     "estimate": True,
     "source": "RQ 跑力（徐國峰）儲備心率法；T 區 84–88% HRR 出自 runningquotient.com/article/single/52，"
               "其他區界照你的筆記，沒有逐一對過 RQ 原文（推估）"},
]
POWER_MODELS = [
    {"id": "ilevels", "title": "WKO5 iLevels", "basis": "ilevels",
     "source": "WKO5 iLevels：前 90 天所有活動 mean-max 功率的 PD 模型（mFTP、FRC、Pmax；"
               "docs/wko5-internals/formulas.md §6.9），和 WKO5「Time in iLevels」同一條式子"},
    {"id": "palladino", "title": "Palladino 10 區（% CP）", "basis": "cp", "zones": lambda: Z.PALLADINO_POWER_ZONES,
     "source": Z.SOURCE},
    # power zones are Palladino's everywhere (owner 2026-10-02): the Coggan (cycling % FTP) and
    # Stryd sets are gone; a remembered choice falls back to the default. iLevels stays as the
    # WKO5 cross-check (its own PD model, not a fixed % table).
    {"id": "palladino3", "title": "3 區（Palladino 80／95% CP）", "basis": "cp",
     "zones": lambda: [("1", "低強度", 0.0, Z.PALLADINO_3ZONE["low"]),
                       ("2", "中強度", Z.PALLADINO_3ZONE["low"], Z.PALLADINO_3ZONE["high"]),
                       ("3", "高強度", Z.PALLADINO_3ZONE["high"], None)],
     "source": "Palladino 的三區摘要（低 < 80% CP、中 80–95%、高 ≥ 95%），即功率版的 Seiler 三區"},
]
MODELS = {"hr": HR_MODELS, "power": POWER_MODELS}
# defaults: Friel % LTHR and Palladino % CP (docs/research/zones-and-thresholds.md §3.1–3.2)
DEFAULT_MODEL = {"hr": "frielhr", "power": "palladino"}


def _threshold_text(ds, w, basis: str) -> Optional[str]:
    """Where the threshold in effect comes from (zones.threshold_info)."""
    try:
        i = Z.threshold_info(ds, basis, w, int(math.floor(w.day)))
    except Exception:            # noqa: BLE001 — a dataset without a plan (tests)
        return None
    return i.get("source")


def ilevels_for(ds, w) -> Optional[list[tuple]]:
    """[(id, name, from W, to W)] of WKO5 iLevels on the workout date; None
    when the PD model can't be fitted (no power in the last 90 days, or the
    fit fails WKO5's validity gate)."""
    from backend.engine.wko5expr.evaluator import Evaluator
    d = int(math.floor(w.day))
    try:
        ev = Evaluator(ds, d, d)
        tops = [_f(ev.evaluate(ILEVEL_EXPR.format(i=i), w)) for i in range(len(ILEVEL_NAMES) - 1)]
    except Exception:            # noqa: BLE001 — no model
        return None
    if any(v is None for v in tops):
        return None
    out, lo = [], 0.0
    for (zid, nm), hi in zip(ILEVEL_NAMES, tops + [None]):
        out.append((zid, nm, lo, hi))
        lo = hi
    return out


def _bounds(ds, w, kind: str, model: dict, ctx: dict) -> dict:
    """{"rows": [(id, name, from, to)] in bpm / W, "basis_text", "estimate"}
    or {"reason"} when the model can't be used for this activity."""
    b = model["basis"]
    est = bool(model.get("estimate"))
    aet, lthr, cp = ctx["thr"]
    if b == "ilevels":
        # a caller may pass the levels in ctx (period_zones memoises them per file)
        lv = ctx["ilevels"] if "ilevels" in ctx else ctx.setdefault("ilevels", ilevels_for(ds, w))
        if lv is None:
            return {"reason": "算不出 iLevels：前 90 天的功率不夠擬合 PD 模型（WKO5 的有效性門檻）"}
        ftp = lv[4][3] / 1.05 if lv[4][3] else None
        return {"rows": lv, "basis_text": f"mFTP {ftp:.0f} W（前 90 天 PD 模型）" if ftp else None, "estimate": est}
    if b == "aet_lthr":
        if not aet or not lthr:
            return {"reason": "沒有 AeT 或 LTHR"}
        return {"rows": [("1", "低強度（< AeT）", 0.0, aet), ("2", "中強度（AeT–LTHR）", aet, lthr),
                         ("3", "高強度（≥ LTHR）", lthr, None)],
                "basis_text": f"AeT {aet:.0f}、LTHR {lthr:.0f} bpm", "estimate": est}
    if b == "hrr":
        return {"reason": "沒有靜息心率資料，算不出儲備心率（HRR = 最大心率 − 靜息心率）"}
    T = {"lthr": lthr, "cp": cp}[b]
    if not T:
        return {"reason": f"沒有 {'LTHR' if b == 'lthr' else 'CP'}，區間算不出來"}
    unit = "bpm" if b == "lthr" else "W"
    src = _threshold_text(ds, w, b)
    text = f"{'LTHR' if b == 'lthr' else 'CP'} {T:.0f} {unit}" + (f"（{src}）" if src else "")
    rows = [(zid, nm, (lo or 0.0) * T, None if hi is None else hi * T) for zid, nm, lo, hi in model["zones"]()]
    # the first zone takes everything below it too (Palladino starts at 50 % CP; zones.zone_of
    # counts below 50 % as 1A), so every second has a zone
    rows[0] = (rows[0][0], rows[0][1], 0.0, rows[0][3])
    return {"rows": rows, "basis_text": text, "estimate": est}


def time_in(values: Optional[np.ndarray], rows: list[tuple]) -> tuple[list[float], float]:
    """Seconds (1-s grid) per zone of the values > 0; zone i = [from, to)."""
    if values is None:
        return [0.0] * len(rows), 0.0
    v = values[np.isfinite(values) & (values > 0)]
    secs = []
    for _, _, lo, hi in rows:
        m = v >= (lo or 0.0)
        if hi is not None:
            m &= v < hi
        secs.append(float(m.sum()))
    return secs, float(sum(secs))


def zone_times(ds, w, kind: str) -> dict:
    """Every model of `kind` ("hr" / "power") for this activity: boundaries,
    seconds and share per zone, or why the model isn't available."""
    from backend.engine.workout_review import _thresholds
    G = grid(ds, w)
    vals = None if G is None else G["hr" if kind == "hr" else "power"]
    ctx = {"thr": _thresholds(ds, w)}
    out = []
    for m in MODELS[kind]:
        b = _bounds(ds, w, kind, m, ctx)
        item = {"id": m["id"], "title": m["title"], "source": m["source"]}
        if "reason" in b:
            out.append({**item, "available": False, "reason": b["reason"]})
            continue
        secs, total = time_in(vals, b["rows"])
        out.append({**item, "available": True, "estimate": b.get("estimate", False),
                    "basis_text": b.get("basis_text"), "total_s": total,
                    "rows": [{"id": zid, "name": nm, "from": _r(lo, 0), "to": _r(hi, 0), "seconds": s,
                              "share": s / total if total else None}
                             for (zid, nm, lo, hi), s in zip(b["rows"], secs)]})
    has = vals is not None and bool(np.isfinite(vals).any() and (np.nan_to_num(vals) > 0).any())
    return {"zone_kind": kind, "models": out, "default": DEFAULT_MODEL[kind],
            "empty": None if has else ("這筆活動沒有心率" if kind == "hr" else "這筆活動沒有可用的功率（手錶推估功率不採用）")}


# ---------------------------------------------------------------------------
# WKO5 Heart Rate Variation and Trend
# ---------------------------------------------------------------------------

def hr_variation(t, hr, power=None, speed=None) -> Optional[dict]:
    """WKO5's numbers on the raw samples: avg, stddev (n − 1), slrm / slrb
    (least squares on elapsed seconds), the variability and trend classes,
    and PWHR (PAHR without power) — the recording cut at half its length."""
    t = np.asarray(t, dtype=float)
    h = np.asarray(hr, dtype=float) if hr is not None else None
    if h is None:
        return None
    n = min(len(t), len(h))
    t, h = t[:n], h[:n]
    ok = np.isfinite(t) & np.isfinite(h) & (h > 0)
    if ok.sum() < 3:
        return None
    x, y = t[ok], h[ok]
    # avg of samples is weighted by deltatime (the evaluator's _reduce, as WKO5);
    # stddev and the regression are unweighted (functions.md §7)
    dtv = np.diff(t, prepend=t[0])
    wts = np.where(np.isfinite(dtv) & (dtv > 0), dtv, 0.0)

    def wavg(v, m):
        s = wts[m].sum()
        return float((v[m] * wts[m]).sum() / s) if s > 0 else (float(v[m].mean()) if m.any() else None)
    avg, sd = wavg(h, ok), float(y.std(ddof=1))
    nn = len(x)
    den = nn * float((x * x).sum()) - float(x.sum()) ** 2
    if den == 0:
        return None
    m = (nn * float((x * y).sum()) - float(x.sum()) * float(y.sum())) / den
    b = (float(y.sum()) - m * float(x.sum())) / nn
    cv = sd / avg if avg else None
    variability = None if cv is None else ("steady" if cv < CV_STEADY else "mixed" if cv < CV_MIXED else "variable")
    trend = "declining" if m < -TREND_MOVE else "consistent" if m < TREND_MOVE else "increasing"
    # PWHR / PAHR: halves (0, L/2] and (L/2, L] of the range length (workout-metrics.md)
    out_ratio = None
    basis = None
    for name, other, scale in (("pwhr", power, 1.0), ("pahr", speed, 1000.0 / 60.0)):
        if other is None:
            continue
        o = np.asarray(other, dtype=float)[:n]
        tt = t - t[np.isfinite(t)][0]
        L = float(np.nanmax(tt))
        es = []
        for half in ((tt > 0) & (tt <= L / 2), (tt > L / 2) & (tt <= L)):
            ah = wavg(h, half & np.isfinite(h) & (h > 0))
            ao = wavg(o, half & np.isfinite(o))
            es.append(ao * scale / ah if ah and ao is not None else None)
        if es[0] and es[1] is not None and (np.nan_to_num(o) > 0).any():
            out_ratio, basis = (es[0] - es[1]) / es[0], name
            break
    return {"avg": avg, "sd": sd, "cv": cv, "slope": m, "intercept": b,
            "x0": float(x.min()), "x1": float(x.max()), "variability": variability, "trend": trend,
            "decoupling": out_ratio, "decoupling_basis": basis}


VARIABILITY_LABEL = {"steady": "穩定", "mixed": "混合", "variable": "變化大"}
TREND_LABEL = {"declining": "下降", "consistent": "持平", "increasing": "上升"}


def hrtrend(ds, w, points: int = POINTS) -> dict:
    from backend.engine.workout_review import _samples
    s = _samples(ds, w)
    if s is None or s.get("hr") is None:
        return {"empty": "這筆活動沒有心率"}
    t = np.asarray(s["t"], dtype=float)
    v = hr_variation(t, s["hr"], s.get("power"), s.get("speed"))
    if v is None:
        return {"empty": "這筆活動沒有心率"}
    G = grid(ds, w)
    hr = G["hr"]
    smooth = rolling(hr, 60)
    n = len(hr)
    step = max(1, int(math.ceil(n / points)))
    t0 = G["t0"]
    pts = [[float(i), _r(smooth[i], 1)] for i in range(0, n, step)]
    # the trend line on the plot's axis (seconds from the first sample)
    line = [[v["x0"] - t0, v["slope"] * v["x0"] + v["intercept"]],
            [v["x1"] - t0, v["slope"] * v["x1"] + v["intercept"]]]
    change = v["slope"] * (v["x1"] - v["x0"])
    return {"points": pts, "trend_line": [[_r(a, 0), _r(b, 2)] for a, b in line],
            "avg": _r(v["avg"], 2), "sd": _r(v["sd"], 2), "cv": _r(v["cv"], 4),
            "slope_bpm_h": _r(v["slope"] * 3600.0, 2), "change_bpm": _r(change, 1),
            "variability": v["variability"], "variability_label": VARIABILITY_LABEL.get(v["variability"]),
            "trend": v["trend"], "trend_label": TREND_LABEL[v["trend"]],
            "decoupling": _r(v["decoupling"], 4), "decoupling_basis": v["decoupling_basis"],
            "empty": None}


# ---------------------------------------------------------------------------

def render(ds, w, ch: dict) -> dict:
    """The JSON of one kind "activity" chart (wko5views._render)."""
    name = ch.get("chart")
    base = {"title": ch.get("title"), "description": ch.get("description"), "kind": f"act_{name}"}
    if w is None:
        return {**base, "empty": "要選一筆活動"}
    if name == "hrpower":
        return {**base, **hrpower(ds, w)}
    if name in ("hrzones", "powerzones"):
        return {**base, **zone_times(ds, w, "hr" if name == "hrzones" else "power")}
    if name == "hrtrend":
        return {**base, **hrtrend(ds, w)}
    raise ValueError(f"unknown activity chart {name!r}")
