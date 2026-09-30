"""
Leave-one-out back-test of race-power v2 on the athlete's own activities —
docs/research/racepower-v2.md §3B. It is what upgrades the 待驗證 (自組)
formulas: a category whose errors pass the thresholds drops its 推估 badge
and the v2 segment sum becomes the whole-race result (planner.py).

For every past race / long effort:
  1. time travel — inputs derived as of the day before (athlete.derive
     today = date − 1), with the activity itself excluded from every fit
     (envelope, RE, k, RE(g), v_h(g)): leave-one-out;
  2. the course is the activity's own GPS track (course.build_course), so
     route error is out of the picture and the model itself is measured;
  3. mode B: the actual moving average power → predicted time vs actual
     moving time (F7, F9, F10, F16), and the same for v1's method;
  4. per segment: predicted vs actual speed at the segment's actual power
     (F7 / F9 physics), grouped by class;
  5. effort f from the actual (P̄, T) (F1–F5 and the bar's cut-points);
  6. the athlete's own uphill ÷ flat power and second ÷ first half power
     (§3B.6, the personal evidence for α and σ).
Hiking days use the walking model (λ_h = 1) the same way.

Pass rule (thresholds confirmed by the user 2026-09-30): n ≥ 5 AND median
|time error| ≤ road 3 % / trail 6 % / hike 10 % AND the downhill segments'
median speed error ≤ +5 %. The effort bar's cut-points are validated only
with ≥ 5 A races (season-plan priority A) whose median f is 0.97–1.03.

Known leak (not fixed): WKO5's own model values (mFTP / TTE, pd_snapshot)
are today's, whatever the back-test date.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
from statistics import median
from typing import Callable, Optional

import numpy as np

from backend.engine.racepower import course as CO
from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import env as ENV
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import hike as HK
from backend.engine.racepower import pacing as PC
from backend.engine.racepower import re as RE
from backend.engine.racepower import riegel as R
from backend.engine.racepower import weather as WX

THRESHOLDS = {"road": 0.03, "trail": 0.06, "hike": 0.10}
MIN_N = 5
DOWNHILL_BIAS_MAX = 0.05
A_RACE_F = (0.97, 1.03)
LONG_RUN_S = 90 * 60
RUN_WINDOW_DAYS = 365
HIKE_WINDOW_DAYS = 3 * 365
HIKE_MIN_MOVING_S = 3600.0
SEG_MIN_M = 200.0
SEG_MIN_S = 60.0
STORE = WX.HOME / "racepower_backtest.json"
CATEGORY_ZH = {"road": "路跑", "trail": "越野", "hike": "登山"}
DOWN = ("down", "steep_down")

_run_lock = threading.Lock()
_state = {"running": False, "started": None, "progress": None, "error": None}


# ---------------------------------------------------------------------------
# pure pieces (tested on synthetic data)
# ---------------------------------------------------------------------------

def _dt(t: np.ndarray) -> np.ndarray:
    d = np.diff(t, prepend=t[0])
    d[~np.isfinite(d) | (d < 0) | (d > 60)] = 0.0
    return d


def track_of(arr: dict) -> Optional[tuple[GPX.Track, np.ndarray]]:
    """The activity's own GPS track (points with a valid position) and the
    sample index of each track point."""
    lat, lon, z = arr.get("lat"), arr.get("lon"), arr.get("z")
    if lat is None or lon is None or z is None:
        return None
    ok = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 0.01) & (np.abs(lon) > 0.01) & np.isfinite(z)
    idx = np.nonzero(ok)[0]
    if len(idx) < 20:
        return None
    t = arr["t"][idx]
    # device distance as the ruler: the grade model's windows are measured on
    # it, and raw-GPS haversine differs by up to ±27 % on these tracks
    return GPX.Track(lat[idx].tolist(), lon[idx].tolist(), z[idx].tolist(), (t - t[0]).tolist(),
                     dist=arr["d"][idx].tolist()), idx


def actual_segments(arr: dict, idx: np.ndarray, track: GPX.Track, segs: list[dict],
                    moving: np.ndarray) -> list[dict]:
    """Moving time and energy spent inside each course segment. Every sample
    is placed on the course by the same haversine distance build_course used
    (track_distance's kept points interpolated back to all samples), so the
    predicted and actual speeds share one distance."""
    td = CO.track_distance(track)
    kept = idx[td["keep"]]
    n = len(arr["t"])
    cd = np.interp(np.arange(n), kept, td["d"])
    d = _dt(arr["t"])
    mv = moving & (d > 0)
    ends = np.array([s["end_m"] for s in segs])
    which = np.clip(np.searchsorted(ends, cd, side="left"), 0, len(segs) - 1)
    p = np.nan_to_num(arr["p"]) if arr.get("p") is not None else np.zeros(n)
    out = []
    for i in range(len(segs)):
        m = mv & (which == i)
        tm = float(d[m].sum())
        out.append({"t": tm, "e": float((p[m] * d[m]).sum())})
    return out


def empirical_strategy(segs: list[dict], act: list[dict]) -> dict:
    """§3B.6: uphill ÷ flat time-weighted power and second ÷ first half."""
    def pw(sel):
        t = sum(a["t"] for s, a in zip(segs, act) if sel(s))
        e = sum(a["e"] for s, a in zip(segs, act) if sel(s))
        return (e / t, t) if t > 0 else (None, 0.0)
    up, tu = pw(lambda s: s["cls"] in ("up", "steep_up"))
    fl, tf = pw(lambda s: s["cls"] == "flat")
    tot = sum(a["t"] for a in act)
    acc, e1, t1, e2, t2 = 0.0, 0.0, 0.0, 0.0, 0.0
    for a in act:
        if acc + a["t"] / 2 < tot / 2:
            e1, t1 = e1 + a["e"], t1 + a["t"]
        else:
            e2, t2 = e2 + a["e"], t2 + a["t"]
        acc += a["t"]
    return {"up_over_flat": up / fl if up and fl and tu >= 300 and tf >= 300 else None,
            "second_over_first": (e2 / t2) / (e1 / t1) if t1 > 0 and t2 > 0 and e1 > 0 else None}


def summarise(rows: list[dict]) -> dict:
    """Per category: n, median |error| and signed error for v2 and v1, the
    per-class segment speed errors, the downhill bias and pass / fail."""
    out = {}
    for cat, thr in THRESHOLDS.items():
        rs = [r for r in rows if r["category"] == cat and r.get("err_v2") is not None]
        e2 = [r["err_v2"] for r in rs]
        e1 = [r["err_v1"] for r in rs if r.get("err_v1") is not None]
        classes = {}
        for cls in CO.CLASSES:
            es = [s["err"] for r in rs for s in r["segments"] if s["cls"] == cls and s.get("err") is not None]
            classes[cls] = {"n": len(es), "median_err": float(median(es)) if es else None,
                            "median_abs_err": float(median(abs(x) for x in es)) if es else None}
        down = [s["err"] for r in rs for s in r["segments"] if s["cls"] in DOWN and s.get("err") is not None]
        bias = float(median(down)) if down else None
        med_abs = float(median(abs(x) for x in e2)) if e2 else None
        reasons = []
        if len(rs) < MIN_N:
            reasons.append(f"樣本 {len(rs)} < {MIN_N}")
        if med_abs is not None and med_abs > thr:
            reasons.append(f"時間誤差中位數 {med_abs:.1%} > {thr:.0%}")
        if bias is not None and bias > DOWNHILL_BIAS_MAX:
            reasons.append(f"下坡段系統性偏快 {bias:+.1%}")
        strat = [r.get("strategy") or {} for r in rs]
        uf = [s["up_over_flat"] for s in strat if s.get("up_over_flat")]
        sf = [s["second_over_first"] for s in strat if s.get("second_over_first")]
        out[cat] = {"label": CATEGORY_ZH[cat], "n": len(rs), "threshold": thr,
                    "v2_median_abs_err": med_abs, "v2_median_err": float(median(e2)) if e2 else None,
                    "v1_median_abs_err": float(median(abs(x) for x in e1)) if e1 else None,
                    "v1_median_err": float(median(e1)) if e1 else None, "v1_n": len(e1),
                    "classes": classes, "downhill_bias": bias, "passed": not reasons, "reasons": reasons,
                    "up_over_flat": float(median(uf)) if uf else None,
                    "second_over_first": float(median(sf)) if sf else None}
    a = [r["effort"]["f"] for r in rows if r.get("priority_a") and r.get("effort")]
    long_ = [r for r in rows if r["category"] in ("road", "trail") and r.get("effort") and not r.get("priority_a")]
    labels = {}
    for r in long_:
        labels[r["effort"]["label"]] = labels.get(r["effort"]["label"], 0) + 1
    fa = float(median(a)) if a else None
    reasons = []
    if len(a) < MIN_N:
        reasons.append(f"A 級比賽 {len(a)} 場 < {MIN_N}")
    elif not (A_RACE_F[0] <= fa <= A_RACE_F[1]):
        reasons.append(f"A 級比賽 f 中位數 {fa:.3f} 不在 {A_RACE_F[0]}–{A_RACE_F[1]}")
    out["effort"] = {"n_a_races": len(a), "a_median_f": fa, "passed": not reasons, "reasons": reasons,
                     "long_run_labels": labels,
                     "long_run_median_f": float(median(r["effort"]["f"] for r in long_)) if long_ else None}
    return out


def run_harness(cases: list[dict], context: Callable[[dict], dict], evaluate: Callable[[dict, dict], Optional[dict]],
                progress: Optional[Callable[[int, int, dict], None]] = None) -> list[dict]:
    """Leave-one-out loop. `context(case)` must build every fitted input as of
    the day before, without the case's own activity; the harness passes the
    exclusion explicitly (V-BT checks it)."""
    rows = []
    for i, c in enumerate(cases):
        if progress:
            progress(i, len(cases), c)
        ctx = context({**c, "exclude": {c["idx"]}, "as_of": dt.date.fromisoformat(c["date"]) - dt.timedelta(days=1)})
        try:
            r = evaluate(c, ctx)
        except Exception as e:              # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        if r is not None:
            rows.append({**{k: c[k] for k in ("idx", "date", "category", "label") if k in c},
                         "priority_a": c.get("priority_a", False), "day": c.get("day"), **r})
    return rows


# ---------------------------------------------------------------------------
# evaluation of one activity
# ---------------------------------------------------------------------------

def _k_of(d: dict) -> tuple[float, str]:
    rg = d.get("riegel") or {}
    if rg.get("valid"):
        return rg["k"], "個人"
    a = d.get("auto_prior")
    if a:
        tk = R.table_k(42195.0, a["km"] * 1000.0, a["time_s"])
        if tk.get("k") is not None:
            return tk["k"], "查表"
    return -0.07, "預設"


def _cp_of(d: dict) -> tuple[Optional[float], Optional[float], float]:
    srcs = {s["id"]: s for s in d["cp"]["sources"]}
    sid = d["cp"]["default"]
    cp = srcs[sid]["cp"] if sid else None
    acts = d["cp"]["activities"] or {}
    return cp, acts.get("w_prime"), d["tte"]["value"]


def evaluate_run(case: dict, ctx: dict) -> Optional[dict]:
    arr = ctx["arrays"]
    tk = track_of(arr)
    if tk is None:
        return {"error": "沒有 GPS 軌跡"}
    track, idx = tk
    course = CO.build_course(track)
    segs = course["segments"]
    d = _dt(arr["t"])
    moving = (np.nan_to_num(arr["p"]) > 0) & (np.nan_to_num(arr["kmh"]) > 1.0) & (d > 0)
    act = actual_segments(arr, idx, track, segs, moving)
    t_act = sum(a["t"] for a in act)
    e_act = sum(a["e"] for a in act)
    if t_act < 600 or e_act <= 0:
        return {"error": "移動時間或功率不足"}
    p_act = e_act / t_act
    inp = ctx["inputs"]
    weight = inp["weight"]["value"]
    gre = ctx["grade_re"]
    ref = inp["training_conditions"]
    side = {"altitude_m": ref["altitude_m"], "temp_c": ref["temp_c"], "rh_pct": ref["rh_pct"]}
    ms = ENV.segment_factors([s["z_mean"] for s in segs], side, {**side, "altitude_m": None})
    for s, m in zip(segs, ms):
        s["M"] = m
    model = PC.RunModel(weight, gre.re, gre.v_max)
    res = PC.solve_power_mode(p_act, segs, model)
    km = course["totals"]["km"]
    gain = course["totals"]["gain_m"]
    trail = case["category"] == "trail"
    t_v1 = None
    if trail:
        tr = (inp["re"]["trail"] or {}).get("fitted_run")
        if tr:
            t_v1 = RE.effort_km(km, gain, 153.0) * 1000.0 * weight / (tr["median"] * p_act)
    else:
        rd = inp["re"]["road"]
        if rd:
            t_v1 = km * 1000.0 * weight / (rd["median"] * p_act)
    seg_rows = []
    for s, a, r in zip(segs, act, res["rows"]):
        row = {"i": s["i"], "cls": s["cls"], "dist_m": s["dist_m"], "grade": s["grade"], "t_act": a["t"],
               "t_pred_alloc": r["t"], "err": None}
        if a["t"] >= SEG_MIN_S and s["dist_m"] >= SEG_MIN_M and a["e"] > 0:
            p_seg = a["e"] / a["t"]
            v = gre.re(s["grade"]) * p_seg / weight
            vm = gre.v_max(s["grade"])
            if vm and v > vm:
                v = vm
            v_act = s["dist_m"] / a["t"]
            row.update(p_act=p_seg, v_act=v_act, v_pred=v, err=v / v_act - 1.0)
        seg_rows.append(row)
    cp, wp, tte = _cp_of(inp)
    k, ksrc = _k_of(inp)
    eff = None
    if cp:
        p_train = sum((a["e"] / s["M"]) for s, a in zip(segs, act)) / t_act
        e = DF.effort(p_train, t_act, cp, wp, tte, k)
        eff = {"f": e["f"], "label": e["label"], "cp": cp, "k": k, "k_source": ksrc,
               "cp_source": inp["cp"]["default"]}
    steep_n = sum(b["n"] for k_, b in gre.bins.items() if k_ * 0.02 >= 0.15)
    return {"km": km, "gain_m": gain, "segments_n": len(segs), "t_act": t_act, "p_act": p_act,
            "w_per_kg": p_act / weight, "steep_up_samples": steep_n,
            "t_v2": res["T"], "err_v2": res["T"] / t_act - 1.0,
            "t_v1": t_v1, "err_v1": (t_v1 / t_act - 1.0) if t_v1 else None,
            "effort": eff, "strategy": empirical_strategy(segs, act), "segments": seg_rows,
            "grade_n": ctx["grade_re"].n_samples}


def evaluate_hike(case: dict, ctx: dict) -> Optional[dict]:
    arr = ctx["arrays"]
    tk = track_of(arr)
    if tk is None:
        return {"error": "沒有 GPS 軌跡"}
    track, idx = tk
    course = CO.build_course(track)
    segs = course["segments"]
    d = _dt(arr["t"])
    moving = (np.nan_to_num(arr["kmh"]) > 0.3 * 3.6) & (d > 0)
    act = actual_segments(arr, idx, track, segs, moving)
    t_act = sum(a["t"] for a in act)
    if t_act < HIKE_MIN_MOVING_S:
        return None
    hs = ctx["hike_speed"]
    ref = {"altitude_m": hs.ref_alt_m if hs.ref_alt_m is not None else 1000.0, "temp_c": 15.0, "rh_pct": 70.0}
    alt = ENV.segment_factors([s["z_mean"] for s in segs], ref, {**ref, "altitude_m": None}, "unacclimatised")
    for s in segs:
        s["day"] = case.get("day") or 1
    rows = HK.hike_rows(segs, hs.v, 1.0, 1.0, alt, ctx.get("fatigue") or {})
    t_v2 = sum(r["t"] for r in rows)
    km = course["totals"]["km"]
    gain = course["totals"]["gain_m"]
    eph = ctx.get("eph")
    t_v1 = (km + gain / 100.0) / eph * 3600.0 if eph else None
    seg_rows = []
    for s, a, r in zip(segs, act, rows):
        row = {"i": s["i"], "cls": s["cls"], "dist_m": s["dist_m"], "grade": s["grade"], "t_act": a["t"], "err": None}
        if a["t"] >= SEG_MIN_S and s["dist_m"] >= SEG_MIN_M:
            v_act = s["dist_m"] / a["t"]
            row.update(v_act=v_act, v_pred=r["v"], err=r["v"] / v_act - 1.0)
        seg_rows.append(row)
    return {"km": km, "gain_m": gain, "segments_n": len(segs), "t_act": t_act, "t_v2": t_v2,
            "err_v2": t_v2 / t_act - 1.0, "t_v1": t_v1, "err_v1": (t_v1 / t_act - 1.0) if t_v1 else None,
            "effort": None, "segments": seg_rows, "hike_n": hs.n_samples}


# ---------------------------------------------------------------------------
# the real dataset
# ---------------------------------------------------------------------------

def _slice(arr: dict, m: np.ndarray) -> dict:
    return {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in arr.items()}


def candidates(ds, today: dt.date) -> list[dict]:
    """Past A races (season plan), runs ≥ 90 min with power in the last
    365 days (road vs trail by the trail tag) and hiking days ≥ 1 h moving
    in the last 3 years (multi-day trips one case per calendar day)."""
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today)
    a_dates = {e.date[:10]: e for e in ds.plan.events if (e.priority or "").upper() == "A" and e.date[:10] < today.isoformat()}
    out = []
    for w in ds.workouts:
        date = w.entry.start.date().isoformat()
        is_a = date in a_dates
        if w.sport == "run" and w.sport_type != "indoor running" and "runningtreadmill" not in w.tags:
            mv = w.metrics.get("movingduration") or 0
            if (mv >= LONG_RUN_S and tday - RUN_WINDOW_DAYS < w.day <= tday) or is_a:
                cat = "trail" if "runningtrail" in w.tags else "road"
                if is_a and a_dates[date].kind == "road":
                    cat = "road"
                out.append({"idx": w.idx, "date": date, "category": cat, "label": A.label(w), "priority_a": is_a})
    for w in A.hike_workouts(ds, today):
        if w.day > tday:
            continue
        out.append({"idx": w.idx, "date": w.entry.start.date().isoformat(), "category": "hike", "label": A.label(w),
                    "priority_a": w.entry.start.date().isoformat() in a_dates})
    return sorted(out, key=lambda c: c["date"])


def _hike_days(ds, case: dict, arr: dict, start: dt.datetime) -> list[tuple[int, dict]]:
    days = np.array([(start + dt.timedelta(seconds=float(x))).date().toordinal() if np.isfinite(x) else -1
                     for x in arr["t"]])
    uniq = [x for x in sorted(set(days.tolist())) if x > 0]
    if len(uniq) <= 1:
        return [(1, arr)]
    return [(n, _slice(arr, days == x)) for n, x in enumerate(uniq, 1)]


def backtest(ds, today: Optional[dt.date] = None, progress=None) -> dict:
    from backend.engine.racepower import athlete as A
    today = today or dt.date.today()
    by_idx = {w.idx: w for w in ds.workouts}
    cases = []
    arrays = {}
    for c in candidates(ds, today):
        w = by_idx[c["idx"]]
        arr = A.activity_arrays(ds, w)
        if arr is None:
            continue
        if c["category"] == "hike":
            for n, part in _hike_days(ds, c, arr, w.entry.start):
                cc = {**c, "day": n, "date": (w.entry.start + dt.timedelta(seconds=float(np.nanmin(part["t"])))).date().isoformat()}
                arrays[(c["idx"], n)] = part
                cases.append(cc)
        else:
            if arr.get("p") is None or not np.any(np.nan_to_num(arr["p"]) > 0):
                continue
            arrays[(c["idx"], None)] = arr
            cases.append(c)
    all_hdays = A.hiking_days(ds, today)
    derived: dict = {}

    def context(c):
        as_of = c["as_of"]
        key = (c["idx"], c.get("day"))
        ctx = {"arrays": arrays[key], "exclude": c["exclude"]}
        if c["category"] == "hike":
            trip_date = by_idx[c["idx"]].entry.start.date().isoformat()
            prior = [d for d in all_hdays if d["trip"] < trip_date]
            ws = [3.0 if d["gain_m"] >= A.HIKE_HEAVY_GAIN_M else 1.0 for d in prior]
            s = RE.summary([d["ep_per_h"] for d in prior], ws) if prior else None
            ctx["eph"] = s["median"] if s else None
            ctx["fatigue"] = HK.day_fatigue(prior)
            gm = A.grade_models(ds, as_of, re_flat=1.0, exclude=c["exclude"])
            ctx["hike_speed"] = gm["hike_speed"]
            return ctx
        dk = (c["idx"], as_of)
        if dk not in derived:
            derived[dk] = A.derive(ds, as_of, fetch_weather=False, exclude=c["exclude"])
        inp = derived[dk]
        ctx["inputs"] = inp
        road = (inp["re"]["road"] or {}).get("median")
        ctx["grade_re"] = A.grade_models(ds, as_of, re_flat=road, exclude=c["exclude"])["grade_re"]
        return ctx

    def evaluate(c, ctx):
        return evaluate_hike(c, ctx) if c["category"] == "hike" else evaluate_run(c, ctx)

    t0 = time.time()
    rows = run_harness(cases, context, evaluate, progress)
    ok = [r for r in rows if "error" not in r]
    summary = summarise(ok)
    validated = {cat: bool(summary[cat]["passed"]) for cat in THRESHOLDS}
    return {"computed_at": dt.datetime.now(WX.TZ).isoformat(timespec="seconds"), "today": today.isoformat(),
            "seconds": round(time.time() - t0, 1), "rows": rows, "summary": summary, "validated": validated,
            "effort_validated": bool(summary["effort"]["passed"]), "thresholds": THRESHOLDS, "min_n": MIN_N,
            "notes": ["WKO5 模型的 mFTP / TTE 用的是今天的值（無法回到過去），其他輸入都回到活動前一天並排除該活動",
                      "時間預測用實際平均功率（模式 B）；分段速度誤差用該段實際功率，只檢驗坡度-RE 與下坡上限"]}


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def save(result: dict, path=None) -> None:
    p = path or STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, default=float), "utf-8")


def load(path=None) -> Optional[dict]:
    try:
        return json.loads((path or STORE).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def flags(path=None) -> tuple[dict, bool]:
    """(validated per category, effort bar validated) from the stored run;
    nothing stored → nothing validated."""
    r = load(path)
    if not r:
        return {k: False for k in THRESHOLDS}, False
    return {k: bool((r.get("validated") or {}).get(k)) for k in THRESHOLDS}, bool(r.get("effort_validated"))


def state() -> dict:
    return dict(_state)


def start_background(ds_factory: Callable, today: Optional[dt.date] = None) -> bool:
    """Run the back-test in a thread; False when one is already running."""
    with _run_lock:
        if _state["running"]:
            return False
        _state.update(running=True, started=dt.datetime.now(WX.TZ).isoformat(timespec="seconds"),
                      progress=None, error=None)

    def work():
        try:
            def prog(i, n, c):
                _state["progress"] = {"i": i, "n": n, "label": c.get("label")}
            save(backtest(ds_factory(), today, prog))
        except Exception as e:              # noqa: BLE001
            _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            _state["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return True
