"""
位置記憶 (SP-726): a route that goes where you have run before uses how fast you were THERE.

SP-716: the biggest single error of a trail estimate is the route itself — 10/18 北插天山 ran 15–25 %
slower than the athlete's usual pace at the same grade from the first hour on (technical, a scramble
down). Nothing on a map says so; your own earlier runs over the same ground do. So each segment of the
planned course that lies on one of your earlier trail runs (same direction, within TOL_M) takes how
slow or fast you were on that stretch, relative to what the trail HR model expects of you there:

    ρ_a      = the run's actual moving time ÷ the HR model's moving time at its own HR on its own
               effort km (trailhr.predict_time; the 一般 terrain level: a run that was hard all the
               way through has ρ > 1 — the route-level difficulty)
    piece    = a stretch of the plan's segment j that run a covered (route_match.common_runs on the
               two tracks, 25 m points, then the run's point nearest each plan point, moving forward)
    r        = ρ_a × (the stretch's share of the run's moving time) ÷ (its share of the run's
               slope-cost effort: Σ effort km × trailhr.slope_cost(grade), downhill included) —
               > 1: you were slower THERE than the model expects at that grade on that day
    cov_a,j  = the length share of segment j run a covered; ln r_a,j its length-weighted mean
    f_j      = max over the runs of cov_a,j (how much of the segment you know)
    R_j      = exp(Σ_a cov_a,j · ln r_a,j ÷ (Σ_a cov_a,j + K)), clipped to R_RANGE — shrunk toward
               1 by K pseudo-passes: one pass counts half, three passes three quarters (one day's
               form is in every pass, 推估)
    t_j'     = t_j × (1 − f_j + f_j · R_j ÷ m)            m = the plan's 地形難度 multiplier

The known part replaces the 地形難度 multiplier (SP-725) — your own pace there already holds it; the
rest of the segment keeps the plan's time. The whole-race time is the sum, so it moves too.

Everything here is 推估 (one athlete's design, no literature on place-specific pace). The back-test
(memory_backtest) predicts each trail run from the HR model fitted without it and only the runs
before it, with and without the memory, on the runs that go over earlier ground.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Callable, Optional, Sequence

import numpy as np

from backend.engine.algorithms import route_match as RM

MEMORY = {
    "step_m": 25.0,            # the tracks resampled every 25 m (route_match.STEP_M)
    "tol_m": 30.0,             # on the same path (route_match.TOL_M)
    "min_run_m": 300.0,        # 推估: a shared stretch shorter than this says nothing about a place
    "piece_min_m": 200.0,      # 推估: … and a piece of a segment shorter than this is left out
    "piece_min_s": 60.0,
    "k_passes": 1.0,           # 推估: pseudo-passes at r = 1
    "r_range": (0.6, 1.8),     # 推估: one stretch never more than this far off the model
    "smooth_pts": 5,           # the elevation's moving average for the grade (5 × 25 m)
}
SOURCE = ("位置記憶（推估）：路線經過你以前跑過的地方（同方向、30 m 內），那一段用你當時「實際時間 ÷ 心率配速模型在同坡度的預期」"
          "（那一天整體的快慢也算進去），一次經過算一半、越多次越接近當時的值；只改走過的那部分，地形難度倍率在那部分不再乘")


# ---- the compact track of one earlier run ----------------------------------------------------

def compact(lat, lon, d, z, t, moving, step_m: float = MEMORY["step_m"]) -> Optional[dict]:
    """One activity's points every ≥ step_m along its distance (valid GPS only): lat, lon, d (m),
    z (m), cm (cumulative moving s up to the point). None without ≥ 20 such points."""
    lat, lon, d, z, t = (np.asarray(x, float) for x in (lat, lon, d, z, t))
    mv = np.asarray(moving, bool)
    n = min(len(lat), len(lon), len(d), len(z), len(t), len(mv))
    lat, lon, d, z, t, mv = lat[:n], lon[:n], d[:n], z[:n], t[:n], mv[:n]
    dt = np.diff(t, prepend=t[0] if n else 0.0)
    dt[~np.isfinite(dt) | (dt < 0)] = 0.0
    cm = np.cumsum(np.where(mv, dt, 0.0))
    ok = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 0.01) & (np.abs(lon) > 0.01) & np.isfinite(z) & np.isfinite(d)
    idx = np.nonzero(ok)[0]
    if len(idx) < 20:
        return None
    keep, last = [], None
    for i in idx:
        if last is None or d[i] - last >= step_m:
            keep.append(i)
            last = d[i]
    if keep[-1] != idx[-1]:
        keep.append(int(idx[-1]))
    if len(keep) < 20:
        return None
    k = np.asarray(keep)
    return {"lat": [round(float(x), 6) for x in lat[k]], "lon": [round(float(x), 6) for x in lon[k]],
            "d": [round(float(x), 1) for x in d[k]], "z": [round(float(x), 1) for x in z[k]],
            "cm": [round(float(x), 1) for x in cm[k]]}


def _smooth(z: np.ndarray, k: int) -> np.ndarray:
    if len(z) < k or k <= 1:
        return z
    pad = k // 2
    zp = np.concatenate([np.full(pad, z[0]), z, np.full(pad, z[-1])])
    return np.convolve(zp, np.ones(k) / k, mode="valid")[:len(z)]


def cost_effort(d: np.ndarray, z: np.ndarray, cost: Callable[[float], float], divisor: float) -> np.ndarray:
    """Cumulative slope-cost effort at each point: Σ (Δkm + climb ÷ divisor) × cost(grade) of each step,
    the grade on the smoothed elevation."""
    d = np.asarray(d, float)
    zs = _smooth(np.asarray(z, float), MEMORY["smooth_pts"])
    dd = np.diff(d)
    dz = np.diff(zs)
    g = np.where(dd > 0.5, dz / np.maximum(dd, 0.5), 0.0)
    e = (np.maximum(dd, 0.0) / 1000.0 + np.maximum(dz, 0.0) / divisor) * np.array([cost(x) for x in g])
    return np.concatenate([[0.0], np.cumsum(e)])


# ---- matching a plan to the earlier runs ------------------------------------------------------

def plan_points(profile: dict, step_m: float = MEMORY["step_m"]) -> Optional[dict]:
    """The planned course every step_m: {xy (m, around its start), km, origin}; None without
    coordinates (a manual course, a share snapshot)."""
    lat, lon, km = (profile or {}).get("lat"), (profile or {}).get("lon"), (profile or {}).get("km")
    if not lat or not lon or not km or len(lat) < 2:
        return None
    lat0, lon0 = float(lat[0]), float(lon[0])
    xy = RM.project(lat, lon, lat0, lon0)
    s = RM.path_length(xy)
    if s[-1] <= 0:
        return None
    se = np.append(np.arange(0.0, s[-1], step_m), s[-1])
    xe = np.column_stack((np.interp(se, s, xy[:, 0]), np.interp(se, s, xy[:, 1])))
    return {"xy": xe, "km": np.interp(se, s, np.asarray(km, float)), "origin": (lat0, lon0)}


def _forward_nearest(a: np.ndarray, b: np.ndarray, j0: int, j1: int) -> np.ndarray:
    """For each point of a, the nearest point of b[j0..j1], never moving backward."""
    bb = b[j0:j1 + 1]
    d2 = ((a[:, None, :] - bb[None, :, :]) ** 2).sum(axis=2)
    j = np.argmin(d2, axis=1)
    return j0 + np.maximum.accumulate(j)


def pieces(plan: dict, entry: dict, segs: Sequence[dict]) -> list[dict]:
    """The pieces of the plan's segments one earlier run covered: [{seg (index), km0, km1, r, t_s}].
    `entry`: {lat, lon, d, cm, ce (cumulative slope-cost effort), rho, T (moving s), CE (total)}."""
    lat0, lon0 = plan["origin"]
    b = RM.project(entry["lat"], entry["lon"], lat0, lon0)
    a = plan["xy"]
    runs = RM.common_runs(a, b, tol=MEMORY["tol_m"], min_len=MEMORY["min_run_m"])
    cm, ce = np.asarray(entry["cm"], float), np.asarray(entry["ce"], float)
    T, CE, rho = float(entry["T"]), float(entry["CE"]), float(entry["rho"])
    if T <= 0 or CE <= 0 or not rho or rho <= 0:
        return []
    km = plan["km"]
    ends = np.array([float(s["end_km"]) for s in segs])
    half = MEMORY["step_m"] / 2000.0          # each plan point stands for half a step either side (km)
    out = []
    for i0, i1, j0, j1 in runs:
        jj = _forward_nearest(a[i0:i1 + 1], b, j0, j1)
        ii = np.arange(i0, i1 + 1)
        which = np.clip(np.searchsorted(ends, km[ii], side="left"), 0, len(segs) - 1)
        for sj in np.unique(which):
            m = np.nonzero(which == sj)[0]
            ia, ib = ii[m[0]], ii[m[-1]]
            ja, jb = jj[m[0]], jj[m[-1]]
            s = segs[int(sj)]
            k0 = max(float(s["start_km"]), float(km[ia]) - half)
            k1 = min(float(s["end_km"]), float(km[ib]) + half)
            ts, cs = float(cm[jb] - cm[ja]), float(ce[jb] - ce[ja])
            if (k1 - k0) * 1000.0 < MEMORY["piece_min_m"] or ts < MEMORY["piece_min_s"] or cs <= 0:
                continue
            r = rho * (ts / T) / (cs / CE)
            lo, hi = MEMORY["r_range"]
            out.append({"seg": int(sj), "km0": k0, "km1": k1, "r": float(min(hi, max(lo, r))), "t_s": ts})
    return out


def segment_memory(plan: Optional[dict], segs: Sequence[dict], entries: Sequence[dict]) -> Optional[dict]:
    """Per plan segment {i, f, R, n, passes: [{idx, date, label, km0, km1, r}]} for the segments some
    earlier run covered; None when none did (or the plan has no coordinates)."""
    if plan is None or not segs:
        return None
    by: dict[int, dict[int, list]] = {}
    meta = {}
    for e in entries or []:
        for p in pieces(plan, e, segs):
            by.setdefault(p["seg"], {}).setdefault(e["idx"], []).append(p)
            meta[e["idx"]] = e
    if not by:
        return None
    K = MEMORY["k_passes"]
    lo, hi = MEMORY["r_range"]
    rows = {}
    for sj, acts in by.items():
        s = segs[sj]
        L = max(1e-6, float(s["end_km"]) - float(s["start_km"]))
        num, wsum, f, passes = 0.0, 0.0, 0.0, []
        for idx, ps in acts.items():
            ln = sum((p["km1"] - p["km0"]) * math.log(p["r"]) for p in ps)
            cl = sum(p["km1"] - p["km0"] for p in ps)
            cov = min(1.0, cl / L)
            num += cov * ln / cl
            wsum += cov
            f = max(f, cov)
            e = meta[idx]
            passes.append({"idx": idx, "date": e.get("date"), "label": e.get("label"),
                           "km0": min(p["km0"] for p in ps), "km1": max(p["km1"] for p in ps),
                           "r": math.exp(ln / cl), "cover": cov})
        R = math.exp(num / (wsum + K))
        rows[sj] = {"i": s.get("i", sj + 1), "f": f, "R": float(min(hi, max(lo, R))), "n": len(passes),
                    "passes": sorted(passes, key=lambda x: x["date"] or "")}
    return {"segments": rows, "n_activities": len(meta), "k": K, "source": SOURCE, "badge": "推估"}


def apply(times: Sequence[float], mem: Optional[dict], terrain_mult: float = 1.0) -> list[float]:
    """t_j' = t_j × (1 − f_j + f_j · R_j ÷ m) on the remembered segments (module docstring)."""
    out = [float(t) for t in times]
    for sj, row in ((mem or {}).get("segments") or {}).items():
        if 0 <= sj < len(out):
            out[sj] *= (1.0 - row["f"]) + row["f"] * row["R"] / (terrain_mult or 1.0)
    return out


# ---- the earlier runs -------------------------------------------------------------------------

def rho_of(model: dict, p: dict) -> Optional[float]:
    """The run's actual ÷ the HR model's moving time at its own HR on 一般 terrain (None without)."""
    from backend.engine.racepower import trailhr as TH
    t = TH.predict_time(model, p["eff_km"], p["x"])
    return p["T_h"] * 3600.0 / t if t and t > 0 else None


def entries(model: Optional[dict], tracks: dict, before: Optional[str] = None) -> list[dict]:
    """The earlier trail runs as memory: every HR model point (model["points"]) with a compact track
    in `tracks` ({workout idx: compact()}), dated before `before` (ISO date; None = all), with its ρ
    and its slope-cost effort under the model's slope fit."""
    from backend.engine.racepower import trailhr as TH
    if not model or not (model.get("a") or model.get("c")):
        return []
    fit = model.get("slope_cost")
    div = TH.effort_divisor()
    out = []
    for p in model.get("points") or []:
        tr = tracks.get(p.get("idx"))
        if not tr or (before and (p.get("date") or "") >= before):
            continue
        rho = rho_of(model, p)
        if not rho:
            continue
        ce = cost_effort(tr["d"], tr["z"], lambda g: TH.slope_cost(fit, g), div)
        T = float(tr["cm"][-1])
        if T <= 0 or ce[-1] <= 0:
            continue
        out.append({**tr, "idx": p["idx"], "date": p.get("date"), "label": p.get("label"), "rho": rho,
                    "ce": ce, "CE": float(ce[-1]), "T": T})
    return out


def for_course(course: dict, model: Optional[dict], tracks: Optional[dict] = None,
               ents: Optional[list] = None) -> Optional[dict]:
    """The plan's memory (segment_memory) on a GPX course from the model's runs with tracks (or the
    entries() already built from them)."""
    if course.get("source") != "gpx":
        return None
    plan = plan_points(course.get("profile") or {})
    if plan is None:
        return None
    return segment_memory(plan, course["segments"], ents if ents is not None else entries(model, tracks or {}))


# ---- the back-test ----------------------------------------------------------------------------

def _seg_times_actual(tr: dict, segs: Sequence[dict]) -> list[float]:
    d, cm = np.asarray(tr["d"], float), np.asarray(tr["cm"], float)
    d = d - d[0]
    ends = [0.0] + [float(s["end_m"]) for s in segs]
    c = np.interp(ends, d, cm)
    return [float(c[i + 1] - c[i]) for i in range(len(segs))]


def memory_backtest(model: dict, tracks: dict, course_of: Callable[[dict], Optional[dict]],
                    min_cover: float = 0.2) -> dict:
    """SP-726: every trail run with a track, predicted at its own HR (the 地形難度 multiplier of its
    level) from the HR model fitted WITHOUT it (trailhr.fit on the other points), split over its own
    course by the slope cost — then with the memory of the runs BEFORE it (ρ from that same model).
    On the runs whose course is ≥ min_cover known: the whole-run moving-time error and the segment
    errors (|segment time error| and the cumulative error at the segment ends ÷ the run's time),
    with and without. `course_of(track)` = the run's own course (build_course on its track)."""
    from backend.engine.racepower import trailhr as TH
    pts = [p for p in (model or {}).get("points") or [] if p.get("idx") in tracks]
    allp = (model or {}).get("points") or []
    delta = (model or {}).get("delta")
    mult = ((model or {}).get("terrain") or {}).get("mult") or {}
    rows = []
    for p in pts:
        tr = tracks[p["idx"]]
        m = TH.fit([q for q in allp if q.get("idx") != p["idx"]], delta)
        if not (m.get("a") or m.get("c")):
            continue
        m = {**model, **m}
        tm = mult.get(p.get("terrain") or "normal", 1.0)
        base = TH.predict_time(m, p["eff_km"] * tm, p["x"])
        course = course_of(tr)
        if not base or not course or len(course["segments"]) < 1:
            continue
        segs = course["segments"]
        ts = TH.slope_times(segs, [base / len(segs)] * len(segs), m.get("slope_cost")) or [base / len(segs)] * len(segs)
        plan = plan_points(course.get("profile") or {})
        ents = [e for e in entries(m, {k: v for k, v in tracks.items() if k != p["idx"]}, before=p.get("date"))]
        mem = segment_memory(plan, segs, ents)
        if not mem:
            continue
        L = sum(float(s["dist_m"]) for s in segs) or 1.0
        known = sum(r["f"] * float(segs[j]["dist_m"]) for j, r in mem["segments"].items()) / L
        if known < min_cover:
            continue
        tm_ = apply(ts, mem, tm)
        act = _seg_times_actual(tr, segs)
        A = sum(act)
        if A <= 0:
            continue

        def cum_err(pred):
            cp, ca, e = 0.0, 0.0, []
            for x, y in zip(pred, act):
                cp += x
                ca += y
                e.append(abs(cp - ca) / A)
            return float(np.mean(e))
        seg_e = lambda pred: float(median(abs(x / y - 1.0) for x, y in zip(pred, act) if y > 60))  # noqa: E731
        rows.append({"idx": p["idx"], "date": p.get("date"), "label": p.get("label"), "known": known,
                     "n_runs": mem["n_activities"], "actual_s": A,
                     "err_plain": sum(ts) / A - 1.0, "err_memory": sum(tm_) / A - 1.0,
                     "seg_plain": seg_e(ts), "seg_memory": seg_e(tm_),
                     "cum_plain": cum_err(ts), "cum_memory": cum_err(tm_)})

    def med(k):
        v = [abs(r[k]) for r in rows if r.get(k) is not None]
        return float(median(v)) if v else None
    out = {"n": len(rows), "min_cover": min_cover,
           "total_plain": med("err_plain"), "total_memory": med("err_memory"),
           "seg_plain": med("seg_plain"), "seg_memory": med("seg_memory"),
           "cum_plain": med("cum_plain"), "cum_memory": med("cum_memory"),
           "rows": rows, "source": "位置記憶留一回測（推估）：每一筆用不含它擬合的心率配速模型、只用它之前的活動當記憶"}
    # the gate: the whole-run error and the cumulative error at the segment ends (SP-723's segment
    # measure: where you are when) both not worse; the median |segment error| is reported, not gated
    # (one slow place among many plain segments barely moves a median)
    out["better"] = None if out["total_plain"] is None else bool(
        out["total_memory"] <= out["total_plain"] and out["cum_memory"] <= out["cum_plain"])
    return out
