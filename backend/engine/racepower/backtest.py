"""
Leave-one-out back-tests of race-power v2 on the athlete's own activities —
docs/research/racepower-v2.md §3B, redesigned 2026-09-30 after the user's
feedback (HR-aware; group hikes out) and the capacity review. Two separate
questions, reported separately:

1. 能力模型回測 (capacity / 比賽預測) — only maximal efforts can test
   CP / W′ / k (the capacity samples, 2026-10-01): runs whose effective
   effort is 全力 (activity_tags: the user's mark wins; auto = self-paced
   maximal road rules / trail HR on moving time with long rests), and the
   maximal bouts of CP tests (cptest FIT scan + workout_review test_cp). A
   season-plan race is activity type 比賽, not a sample by itself. Trail
   cases also get the HR pace model (trailhr.py, `trail_hr`), with or
   without power. The HR "race" class is no longer a sample (it
   caught hard 5 km training runs). Explicit AeT tests are submaximal
   anchors: only the HR model's power at their HR is checked. Each case
   also gets the HR-based capacity as of the day before (hrcap.py, two TTE
   anchors) and the combined rule (HR as a second lower bound when < 3
   samples in the year before) next to the power envelope. Per case, as of the day
   before and without the case: P_sus(T_actual) vs the actual power (f), and
   mode C (f* = 1) on the activity's own course → time vs actual. Plus the
   lower-bound test on EVERY run: a model that predicts P_sus(T) below a
   power the athlete then held for T (f > 1) fails, maximal or not.
2. 地形模型回測 (terrain) — mode B: the actual power → time on the own
   course and per segment the speed at that segment's power. It tests only
   RE(g) physics (gait-aware RE(g), downhill cap, trail technicality), not
   prediction accuracy. Stratified by intensity class × grade bin, and trail
   split into running vs walking-heavy outings (cadence).

Time travel: derive(today = date − 1, exclude = {case}, strict_as_of): no
WKO5 snapshot value (today's mFTP / TTE); the PD model is refitted on the
mean-max up to that day (athlete.pd_model) and raised to the lower bound
when below it; only thresholds / tests dated before the case; intensity
classes use each activity's own-date
thresholds (athlete.thresholds_as_of; the LTHR estimate measures every run
against athlete.cp_as_of its own date). planning.threshold_on no longer
applies a plan test to earlier dates (fixed 2026-10-01).

Group hikes (hiking / mountaineering) are paced by the group: no hike is a
case unless the user opted it in as solo (athlete.solo_hikes).

Pass rule (user thresholds 2026-09-30; trail 8 % with a 6 % target and the
bootstrap upper bound since 2026-10-02, unsourced-rules.md §0.9 / §0.5.6) per
category: capacity n ≥ 5 race-like or test efforts, median |mode C time
error| ≤ road 3 % / trail 8 % and its 80 % bootstrap upper bound ≤ the
threshold + 2 pp (≤ the threshold from n ≥ 10), no lower-bound violation; terrain on the race-like rows median |err| ≤ the same
threshold and downhill median speed error ≤ +5 %. The effort bar needs ≥ 5
race-like / test efforts with median f in 0.97–1.03.
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

# trail 8 % pass / 6 % target (2026-10-02, docs/research/unsourced-rules.md §0.9: population
# models reach 8–10 % MAPE, a 7-race median has a ±2–3 pp bootstrap interval, so 6 % cannot
# separate 5.5 from 7.6 — 推估)
THRESHOLDS = {"road": 0.03, "trail": 0.08, "hike": 0.10, "hike_capacity": 0.10}
TARGETS = {"trail": 0.06}
MIN_N = 5
# §0.5.6 (推估): n ≥ 5 → median ≤ threshold AND the 80 % bootstrap upper bound of the median
# |error| ≤ threshold + 2 pp; n ≥ 10 → the upper bound ≤ threshold
BOOT_SLACK = 0.02
BOOT_FULL_N = 10
BOOT_REPS = 2000
DOWNHILL_BIAS_MAX = 0.05
A_RACE_F = (0.97, 1.03)
MIN_MOVING_S = 20 * 60
RUN_WINDOW_DAYS = 365
HIKE_WINDOW_DAYS = 3 * 365
HIKE_MIN_MOVING_S = 3600.0
SEG_MIN_M = 200.0
SEG_MIN_S = 60.0
WALK_HEAVY = 0.5               # 推估: ≥ half the moving time walked (< 130 spm) = walking-heavy outing
CLEAR_DIFF = 0.03              # 推估: "clearly differs" = class medians ≥ 3 points apart
STORE = WX.HOME / "racepower_backtest.json"
CATEGORY_ZH = {"road": "路跑", "trail": "越野", "hike": "登山（自己走）"}
CLASSES = ("easy", "steady", "race")
CLASS_ZH = {"easy": "輕鬆", "steady": "穩定", "race": "比賽強度"}
DOWN = ("down", "steep_down")
# grade bins: ±2 % flat (course.py), 8 % = Stryd's validated range (van
# Rassel 2026), 15 % = the walk / run label (Giovanelli 2016)
GRADE_EDGES = (-0.15, -0.08, -0.02, 0.02, 0.08, 0.15)
BIN_LABELS = ["≤ −15%", "−15…−8%", "−8…−2%", "±2%", "+2…+8%", "+8…+15%", "≥ +15%"]
NO_CAPACITY_MSG = "沒有全力比賽或測試紀錄，無法驗證能力模型；請做 3'/12' CP 測試或報名一場 B 級比賽"

_run_lock = threading.Lock()
_state = {"running": False, "started": None, "progress": None, "error": None}


def grade_bin(g: float) -> str:
    i = 0
    while i < len(GRADE_EDGES) and g >= GRADE_EDGES[i]:
        i += 1
    return BIN_LABELS[i]


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
    """Moving time, energy and walked time inside each course segment. Every
    sample is placed on the course by the same distance build_course used."""
    td = CO.track_distance(track)
    kept = idx[td["keep"]]
    n = len(arr["t"])
    cd = np.interp(np.arange(n), kept, td["d"])
    d = _dt(arr["t"])
    mv = moving & (d > 0)
    ends = np.array([s["end_m"] for s in segs])
    which = np.clip(np.searchsorted(ends, cd, side="left"), 0, len(segs) - 1)
    p = np.nan_to_num(arr["p"]) if arr.get("p") is not None else np.zeros(n)
    cad = arr.get("cad")
    walk = (np.isfinite(cad) & (cad > 0) & (cad < 65.0)) if cad is not None else np.zeros(n, bool)
    out = []
    for i in range(len(segs)):
        m = mv & (which == i)
        tm = float(d[m].sum())
        out.append({"t": tm, "e": float((p[m] * d[m]).sum()), "walk_t": float(d[m & walk].sum())})
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


def stats(xs) -> dict:
    """n, median |x|, bias (median x), 10th / 90th percentile."""
    v = [float(x) for x in xs if x is not None and math.isfinite(x)]
    if not v:
        return {"n": 0, "median_abs": None, "bias": None, "p10": None, "p90": None}
    return {"n": len(v), "median_abs": float(median(abs(x) for x in v)), "bias": float(median(v)),
            "p10": float(np.percentile(v, 10)), "p90": float(np.percentile(v, 90))}


def boot_ub(xs, q: float = 0.90, reps: int = BOOT_REPS, seed: int = 20261002) -> Optional[float]:
    """The upper end of the 80 % bootstrap interval (90th percentile) of the
    median |x|; None with fewer than 3 values. Seeded, so a re-run gives the
    same number."""
    v = np.abs(np.array([float(x) for x in xs if x is not None and math.isfinite(x)]))
    if len(v) < 3:
        return None
    rng = np.random.default_rng(seed)
    meds = np.median(v[rng.integers(0, len(v), size=(reps, len(v)))], axis=1)
    return float(np.percentile(meds, q * 100.0))


def pass_check(xs, thr: float, min_n: int = MIN_N) -> dict:
    """§0.5.6 pass rule on errors xs: {passed, reasons, median_abs, ub80,
    ub_limit}. n < min_n never passes (errors are shown only)."""
    s = stats(xs)
    ub = boot_ub(xs)
    lim = thr if s["n"] >= BOOT_FULL_N else thr + BOOT_SLACK
    reasons = []
    if s["n"] < min_n:
        reasons.append(f"樣本 {s['n']} < {min_n}：只顯示誤差，不判定通過")
    else:
        if s["median_abs"] is not None and s["median_abs"] > thr:
            reasons.append(f"時間誤差中位數 {s['median_abs']:.1%} > {thr:.0%}")
        if ub is not None and ub > lim:
            reasons.append(f"中位數的 80 % bootstrap 上界 {ub:.1%} > {lim:.0%}")
    return {"passed": not reasons, "reasons": reasons, "median_abs": s["median_abs"], "n": s["n"],
            "ub80": ub, "ub_limit": lim, "threshold": thr}


def _seg_errs(rows, key="err", sel=lambda s: True):
    return [s[key] for r in rows for s in r.get("segments") or [] if s.get(key) is not None and sel(s)]


def summarise_terrain(rows: list[dict]) -> dict:
    """Mode-B errors per category (trail also running vs walking-heavy), per
    intensity class and per class × grade bin; pooled vs per-class RE and
    with vs without the gait split."""
    out = {"categories": {}, "classes": {}, "grid": {}, "models": {}}
    runs = [r for r in rows if r.get("err_v2") is not None and r["category"] in ("road", "trail")]
    for cat in ("road", "trail", "hike"):
        rs = [r for r in rows if r["category"] == cat and r.get("err_v2") is not None]
        c = {"label": CATEGORY_ZH[cat], "time": stats(r["err_v2"] for r in rs),
             "time_v1": stats(r.get("err_v1") for r in rs),
             "downhill": stats(_seg_errs(rs, sel=lambda s: s["cls"] in DOWN)),
             "by_class": {k: stats(r["err_v2"] for r in rs if r.get("intensity") == k) for k in CLASSES}}
        if cat == "trail":
            c["groups"] = {
                "running": {"label": "跑為主", **stats(r["err_v2"] for r in rs if not r.get("walk_heavy"))},
                "walking": {"label": f"走為主（≥ {WALK_HEAVY:.0%} 時間步頻 < 130 spm）",
                            **stats(r["err_v2"] for r in rs if r.get("walk_heavy"))}}
        out["categories"][cat] = c
    for k in CLASSES:
        rs = [r for r in runs if r.get("intensity") == k]
        out["classes"][k] = {"label": CLASS_ZH[k], "activities": len(rs), "time": stats(r["err_v2"] for r in rs),
                             "segments": stats(_seg_errs(rs)),
                             "time_class_model": stats(r.get("err_v2_cls") for r in rs),
                             "segments_class_model": stats(_seg_errs(rs, "err_cls"))}
        out["grid"][k] = {b: stats(_seg_errs(rs, sel=lambda s, b=b: s.get("bin") == b)) for b in BIN_LABELS}
    out["grid"]["all"] = {b: stats(_seg_errs(runs, sel=lambda s, b=b: s.get("bin") == b)) for b in BIN_LABELS}
    out["models"] = {"gait": stats(r["err_v2"] for r in runs), "no_gait": stats(r.get("err_nogait") for r in runs)}
    # does the error differ clearly by class? (classes with ≥ MIN_N activities)
    meds = {k: v["segments"]["median_abs"] for k, v in out["classes"].items()
            if v["activities"] >= MIN_N and v["segments"]["median_abs"] is not None}
    spread = (max(meds.values()) - min(meds.values())) if len(meds) >= 2 else None
    better = {k: (v["segments_class_model"]["median_abs"] is not None and v["segments"]["median_abs"] is not None
                  and v["segments_class_model"]["median_abs"] < v["segments"]["median_abs"])
              for k, v in out["classes"].items() if v["activities"] >= MIN_N}
    out["class_differs"] = {"spread": spread, "clear": spread is not None and spread >= CLEAR_DIFF,
                            "threshold": CLEAR_DIFF, "class_model_better": better}
    return out


def _hr_stats(rs: list[dict]) -> dict:
    """Power / time errors of the HR-based capacity variants and the
    combined rule next to the power envelope (err_p / err_c)."""
    out = {"power_envelope": {"power": stats(r.get("err_p") for r in rs), "time": stats(r.get("err_c") for r in rs)},
           "combined": {"power": stats(r.get("err_p_comb") for r in rs),
                        "time": stats(r.get("err_c_comb") if r.get("err_c_comb") is not None else r.get("err_c")
                                      for r in rs)}}
    for v, label in HR_VARIANTS:
        out[f"hr_{v}"] = {"label": label, "power": stats(r.get(f"err_p_hr_{v}") for r in rs),
                          "time": stats(r.get(f"err_c_hr_{v}") for r in rs)}
    return out


def _kinds(rs: list[dict]) -> dict:
    out: dict = {}
    for r in rs:
        k = r.get("cap_kind") or "?"
        out[k] = out.get(k, 0) + 1
    return out


def summarise_capacity(rows: list[dict], lb_rows: list[dict]) -> dict:
    """Capacity cases (race-like runs, plan races, CP-test bouts) and the
    lower-bound test over every run."""
    out = {"categories": {}}
    viol = [r for r in lb_rows if r.get("f") is not None and r["f"] > 1.0 + 1e-9]
    out["lower_bound"] = {"n": len([r for r in lb_rows if r.get("f") is not None]), "violations": len(viol),
                          "worst": sorted(viol, key=lambda r: -r["f"])[:10]}
    cap = [r for r in rows if r.get("f") is not None]
    for cat in ("road", "trail"):
        rs = [r for r in cap if r["category"] == cat]
        thr = THRESHOLDS[cat]
        t = stats(r.get("err_c") for r in rs)
        v = [r for r in viol if r["category"] == cat]
        reasons = []
        pc = pass_check([r.get("err_c") for r in rs], thr)
        if len(rs) < MIN_N:
            reasons.append(f"比賽強度／測試 {len(rs)} 次 < {MIN_N}")
        else:
            reasons += pc["reasons"]
        t["boot_ub80"] = pc["ub80"]
        if v:
            reasons.append(f"{len(v)} 次跑步的功率高於模型可持續功率（下限檢查失敗）")
        out["categories"][cat] = {"label": CATEGORY_ZH[cat], "n": len(rs), "threshold": thr, "time": t,
                                  "power": stats(r.get("err_p") for r in rs),
                                  "f_median": float(median(r["f"] for r in rs)) if rs else None,
                                  "passed": not reasons, "reasons": reasons, "hr": _hr_stats(rs),
                                  "kinds": _kinds(rs)}
    tests = [r for r in cap if r["category"] == "test"]
    out["tests"] = {"n": len(tests), "power": stats(r.get("err_p") for r in tests), "rows": tests,
                    "hr": _hr_stats(tests)}
    fs = [r["f"] for r in cap]
    fm = float(median(fs)) if fs else None
    reasons = []
    if len(fs) < MIN_N:
        reasons.append(f"比賽強度／測試 {len(fs)} 次 < {MIN_N}")
    elif not (A_RACE_F[0] <= fm <= A_RACE_F[1]):
        reasons.append(f"努力度中位數 {fm:.3f} 不在 {A_RACE_F[0]}–{A_RACE_F[1]}")
    out["effort"] = {"n": len(fs), "median_f": fm, "passed": not reasons, "reasons": reasons}
    out["n_total"] = len(cap)
    out["message"] = NO_CAPACITY_MSG if len(cap) < MIN_N else None
    return out


def validated_flags(terrain: dict, capacity: dict, race_rows: list[dict]) -> dict:
    out = {}
    for cat in ("road", "trail"):
        cc = capacity["categories"][cat]
        rs = [r for r in race_rows if r["category"] == cat and r.get("err_v2") is not None]
        t = stats(r["err_v2"] for r in rs)
        d = stats(_seg_errs(rs, sel=lambda s: s["cls"] in DOWN))
        ok_t = t["median_abs"] is not None and t["median_abs"] <= THRESHOLDS[cat] and \
            (d["bias"] is None or d["bias"] <= DOWNHILL_BIAS_MAX)
        out[cat] = bool(cc["passed"] and ok_t)
    h = terrain["categories"]["hike"]["time"]
    out["hike"] = bool(h["n"] >= MIN_N and h["median_abs"] is not None and h["median_abs"] <= THRESHOLDS["hike"])
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
        excl = {c["idx"]} if c.get("idx") is not None else set()
        ctx = context({**c, "exclude": excl, "as_of": dt.date.fromisoformat(c["date"]) - dt.timedelta(days=1)})
        try:
            r = evaluate(c, ctx)
        except Exception as e:              # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        if r is not None:
            rows.append({**{k: c[k] for k in ("idx", "date", "category", "label") if k in c},
                         "priority_a": c.get("priority_a", False), "day": c.get("day"),
                         "cap_sample": bool(c.get("cap_sample")), "cap_kind": c.get("cap_kind"),
                         "cap_reason": c.get("cap_reason"),
                         "intensity": c.get("intensity"), "intensity_reason": c.get("intensity_reason"),
                         **{k: c.get(k) for k in ("activity_type", "activity_type_overridden", "effort_tag",
                                                  "effort_overridden", "effort_reason", "file", "rest_share",
                                                  "marked", "no_power", "power_source", "power_unused") if k in c},
                         **r})
    return rows


# ---------------------------------------------------------------------------
# evaluation of one activity
# ---------------------------------------------------------------------------

def _k_of(d: dict, target_m: float) -> tuple[float, str]:
    """Personal k (valid only from race-like efforts) > the Riegel table for
    THIS case's distance (trail: effort distance) from a real-race prior >
    −0.07 (≈ Stryd's table)."""
    rg = d.get("riegel") or {}
    if rg.get("valid"):
        return rg["k"], "個人"
    a = d.get("auto_prior")
    if a and target_m:
        tk = R.table_k(target_m, a["km"] * 1000.0, a["time_s"])
        if tk.get("k") is not None:
            return tk["k"], "查表（比賽強度的前一場）"
    return -0.07, "預設 −0.07"


def _cp_of(d: dict) -> dict:
    srcs = {s["id"]: s for s in d["cp"]["sources"]}
    sid = d["cp"]["default"]
    s = srcs.get(sid) or {}
    acts = d["cp"]["activities"] or {}
    return {"cp": s.get("cp"), "w_prime": s.get("w_prime") or acts.get("w_prime"),
            "tte": s.get("tte") or d["tte"]["value"], "cp2": s.get("cp2"), "cp_source": sid or "",
            "base": s.get("base")}


HR_VARIANTS = (("tt30", "TTE 1800 s（Friel 30 分計時）"), ("tte", "當時 PD 模型 TTE"))
FEW_MAXIMAL = 3                 # 推估: < 3 capacity samples in the 365 days before → HR capacity as a second lower bound
COMBINED_VARIANT = "tte"


def hr_psus(hc: Optional[dict], t_s: float, cap: dict, w_prime: Optional[float]) -> dict:
    """Sustainable power for t_s from the HR-based capacity (hrcap): P_LTHR as
    the F1 anchor at TTE 1800 s ("tt30") and at the as-of TTE ("tte"), the
    case's k, a single anchor (no separate short-range CP) and the W′ prior."""
    if not hc or not hc.get("p_lthr"):
        return {}
    out = {}
    for v, _ in HR_VARIANTS:
        tte = 1800.0 if v == "tt30" else cap["tte"]
        out[v] = DF.p_sus(t_s, hc["p_lthr"], w_prime, tte, cap["k"])
    return out


def capacity_of(inp: dict, t_s: float, target_m: float) -> dict:
    c = _cp_of(inp)
    k, ksrc = _k_of(inp, target_m)
    cp = c["cp"]
    if cp and inp["cp"].get("lb_points"):
        from backend.engine.racepower.athlete import enforce_lower_bound
        cp, _ = enforce_lower_bound(inp, cp, c["w_prime"], c["tte"], k, c["cp2"])
        c["cp"] = cp
    return {**c, "k": k, "k_source": ksrc,
            "p_sus": DF.p_sus(t_s, cp, c["w_prime"], c["tte"], k, cp2=c["cp2"]) if cp else None,
            "lower_bound": (inp["cp"].get("lower_bound") or {}).get("cp_min")}


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
    trail = case["category"] == "trail"
    cls = case.get("intensity")
    gre = ctx["grade_re"]
    gre_cls = ctx.get("grade_re_cls") or gre
    gre_ng = ctx.get("grade_re_nogait")
    if trail and hasattr(gre, "for_trail"):
        gre, gre_cls = gre.for_trail(cls), gre_cls.for_trail(cls)
    ref = inp["training_conditions"]
    side = {"altitude_m": ref["altitude_m"], "temp_c": ref["temp_c"], "rh_pct": ref["rh_pct"]}
    ms = ENV.segment_factors([s["z_mean"] for s in segs], side, {**side, "altitude_m": None})
    for s, m in zip(segs, ms):
        s["M"] = m
    model = PC.RunModel(weight, gre.re, gre.v_max)
    res = PC.solve_power_mode(p_act, segs, model)
    res_cls = PC.solve_power_mode(p_act, segs, PC.RunModel(weight, gre_cls.re, gre_cls.v_max))
    res_ng = PC.solve_power_mode(p_act, segs, PC.RunModel(weight, gre_ng.re, gre_ng.v_max)) if gre_ng else None
    km = course["totals"]["km"]
    gain = course["totals"]["gain_m"]
    t_v1 = None
    if trail:
        tr = (inp["re"]["trail"] or {}).get("fitted_run")
        if tr:
            t_v1 = RE.effort_km(km, gain, 153.0) * 1000.0 * weight / (tr["median"] * p_act)
    else:
        rd = inp["re"]["road"]
        if rd:
            t_v1 = km * 1000.0 * weight / (rd["median"] * p_act)
    walked = getattr(gre, "walked", lambda g: False)
    seg_rows = []
    for s, a in zip(segs, act):
        row = {"i": s["i"], "cls": s["cls"], "bin": grade_bin(s["grade"]), "dist_m": s["dist_m"], "grade": s["grade"],
               "t_act": a["t"], "walk_share": a["walk_t"] / a["t"] if a["t"] else None, "err": None, "err_cls": None}
        if a["t"] >= SEG_MIN_S and s["dist_m"] >= SEG_MIN_M and a["e"] > 0:
            p_seg = a["e"] / a["t"]
            v_act = s["dist_m"] / a["t"]

            def v_of(g_):
                v = g_.re(s["grade"]) * p_seg / weight
                vm = g_.v_max(s["grade"])
                return vm if vm and v > vm else v
            row.update(p_act=p_seg, v_act=v_act, v_pred=v_of(gre), err=v_of(gre) / v_act - 1.0,
                       err_cls=v_of(gre_cls) / v_act - 1.0, gait="walk" if walked(s["grade"]) else "run")
        seg_rows.append(row)
    walk_t = sum(a["walk_t"] for a in act)
    out = {"km": km, "gain_m": gain, "segments_n": len(segs), "t_act": t_act, "p_act": p_act,
           "w_per_kg": p_act / weight, "walk_share": walk_t / t_act if t_act else None,
           "walk_heavy": bool(t_act and walk_t / t_act >= WALK_HEAVY),
           "t_v2": res["T"], "err_v2": res["T"] / t_act - 1.0,
           "err_v2_cls": res_cls["T"] / t_act - 1.0, "err_nogait": (res_ng["T"] / t_act - 1.0) if res_ng else None,
           "t_v1": t_v1, "err_v1": (t_v1 / t_act - 1.0) if t_v1 else None,
           "strategy": empirical_strategy(segs, act), "segments": seg_rows, "grade_n": gre.n_samples,
           "tech": gre.tech_factor() if trail and hasattr(gre, "tech_factor") else None}
    if case.get("aet_test"):
        # a submaximal anchor: the HR model's power at this run's HR vs the actual power
        hc = ctx.get("hrcap") or {}
        hr = arr.get("hr")
        fit = hc.get("fit")
        if fit and hr is not None:
            mh = moving & np.isfinite(hr) & (hr > 40)
            if d[mh].sum() > 0:
                h = float((hr[mh] * d[mh]).sum() / d[mh].sum())
                pp = fit["a"] + fit["b"] * h
                out["aet_check"] = {"hr": h, "p_pred": pp, "p_act": p_act, "err": pp / p_act - 1.0}
    # capacity: the power the model says is sustainable for this duration
    target_m = (RE.effort_km(km, gain, 153.0) if trail else km) * 1000.0
    cap = capacity_of(inp, t_act, target_m)
    if cap["cp"]:
        p_train = sum((a["e"] / s["M"]) for s, a in zip(segs, act)) / t_act
        f = p_train / cap["p_sus"]
        e = DF.effort(p_train, t_act, cap["cp"], cap["w_prime"], cap["tte"], cap["k"], cp2=cap["cp2"])
        out.update(capacity=cap, p_train=p_train, f=f, effort={"f": f, "label": e["label"]},
                   err_p=cap["p_sus"] / p_train - 1.0)
        if case.get("cap_sample"):
            def psus(t):
                return DF.p_sus(t, cap["cp"], cap["w_prime"], cap["tte"], cap["k"], cp2=cap["cp2"])
            rc = PC.solve_auto_mode(1.0, psus, segs, model, cap["cp"])
            out.update(t_c=rc["T"], err_c=rc["T"] / t_act - 1.0, p_c=rc["P"])
            out.update(_hr_eval(ctx, cap, t_act, p_train, psus, lambda ps: PC.solve_auto_mode(
                1.0, ps, segs, model, cap["cp"])["T"] / t_act - 1.0))
    return out


def _hr_eval(ctx: dict, cap: dict, t_s: float, p_act: float, psus_power, time_err=None) -> dict:
    """HR-based capacity next to the power envelope for one capacity case:
    P_sus by each HR variant, the combined rule (HR as a second lower bound
    when < FEW_MAXIMAL samples in the year before) and, with `time_err`
    (mode C on the course), the time errors."""
    hc = ctx.get("hrcap")
    wp = ctx.get("w_prime_prior")
    ps = hr_psus(hc, t_s, cap, wp)
    if not ps:
        return {"hr": {"p_lthr": None, "reasons": (hc or {}).get("reasons")}}
    # the second lower bound only from a valid fit (hrcap.capacity reasons empty)
    few = (ctx.get("n_max") or 0) < FEW_MAXIMAL and bool(hc.get("valid"))
    out = {"hr": {"p_lthr": hc["p_lthr"], "range": hc.get("range"), "valid": hc.get("valid"),
                  "reasons": hc.get("reasons"), "n_runs": hc.get("n_runs"), "slope": (hc.get("fit") or {}).get("b"),
                  "extrap_bpm": hc.get("extrap_bpm"), "lthr": hc.get("lthr"), "n_max": ctx.get("n_max"), "few": few}}
    for v, p in ps.items():
        out[f"err_p_hr_{v}"] = p / p_act - 1.0
        out["hr"][f"p_sus_{v}"] = p
    p_pow = psus_power(t_s)
    p_comb = max(p_pow, ps[COMBINED_VARIANT]) if few else p_pow
    out["err_p_comb"] = p_comb / p_act - 1.0
    out["hr"]["p_sus_comb"] = p_comb
    if time_err is not None:
        def mk(v):
            tte = 1800.0 if v == "tt30" else cap["tte"]
            return lambda t: DF.p_sus(t, hc["p_lthr"], wp, tte, cap["k"])
        for v in ps:
            out[f"err_c_hr_{v}"] = time_err(mk(v))
        hr_f = mk(COMBINED_VARIANT)
        out["err_c_comb"] = time_err(lambda t: max(psus_power(t), hr_f(t))) if few else None
    return out


def evaluate_test(case: dict, ctx: dict) -> Optional[dict]:
    """A maximal CP-test bout: P_sus(t) of the model as of the day before."""
    inp = ctx["inputs"]
    cap = capacity_of(inp, case["t_s"], 0.0)
    if not cap["cp"]:
        return {"error": "沒有 CP"}
    def psus(t):
        return DF.p_sus(t, cap["cp"], cap["w_prime"], cap["tte"], cap["k"], cp2=cap["cp2"])
    return {"t_act": case["t_s"], "p_act": case["p"], "p_train": case["p"], "capacity": cap,
            "f": case["p"] / cap["p_sus"], "err_p": cap["p_sus"] / case["p"] - 1.0,
            "effort": {"f": case["p"] / cap["p_sus"]}, "segments": [],
            **_hr_eval(ctx, cap, case["t_s"], case["p"], psus)}


def evaluate_trail_hr(case: dict, ctx: dict) -> dict:
    """The trail HR pace model (trailhr.py) as of the day before, without
    the case: moving time at the case's own moving HR ("given", tests the
    pace + durability model; also without durability) and at the athlete's
    race HR level from EARLIER races ("race", the prediction a race plan
    would make). No power needed.

    2026-10-02: "race" = the full-effort curve x*(T) solved at the predicted
    T (trailhr.predict_race), the case's own heat moving the level by the
    athlete's β (the race-day forecast in a real plan); "race_median" = the
    old median race level with the same heat shift (comparison). "total" =
    race moving time + the predicted non-moving time (nonmoving.py, earlier
    races only) against the actual elapsed time — reported apart: moving
    time stays the validated target."""
    from backend.engine.racepower import nonmoving as NM
    from backend.engine.racepower import trailhr as TH
    pt, m = case.get("trail_pt"), ctx.get("trail_hr")
    if not pt or not m or not (m.get("a") or m.get("c")):
        return {}
    act = pt["T_h"] * 3600.0
    sh = pt.get("heat_shift") or 0.0
    tg = TH.predict_time(m, pt["eff_km"], pt["x"])
    tn = TH.predict_time(m, pt["eff_km"], pt["x"], delta=0.0)
    tr, xr = TH.predict_race(m, pt["eff_km"], m.get("xstar"), x_shift=sh)
    trn, _ = TH.predict_race(m, pt["eff_km"], m.get("xstar"), delta=0.0, x_shift=sh)
    xm = m.get("x_race_median")
    tm = TH.predict_time(m, pt["eff_km"], xm - sh) if xm else None
    nm_act = case.get("nm_row") or {}
    nm = NM.predict(m.get("nonmoving"), tr) if tr else None
    el = nm_act.get("elapsed_s")
    tot = (tr + nm["total_s"]) if (tr and nm) else None
    return {"th": {"moving_s": act, "eff_km": pt["eff_km"], "x": pt["x"], "x_raw": pt.get("x_raw"),
                   "heat_shift": sh, "hadley": pt.get("hadley"), "x_race": xr, "x_race_median": xm,
                   "t_given": tg, "t_nodur": tn, "t_race": tr, "t_race_nodur": trn, "t_race_median": tm,
                   "delta": m["delta"], "kind": m["kind"], "n_runs": m["n"], "x_race_n": m.get("x_race_n"),
                   "xstar_n": (m.get("xstar") or {}).get("n"),
                   "nonmoving_act_s": (el - nm_act["moving_s"]) if el and nm_act.get("moving_s") else None,
                   "nonmoving_pred_s": nm["total_s"] if nm else None, "elapsed_s": el, "t_total": tot},
            "err_th_given": tg / act - 1.0 if tg else None, "err_th_nodur": tn / act - 1.0 if tn else None,
            "err_th_race": tr / act - 1.0 if tr else None, "err_th_race_nodur": trn / act - 1.0 if trn else None,
            "err_th_race_median": tm / act - 1.0 if tm else None,
            "err_th_total": tot / el - 1.0 if (tot and el) else None}


def summarise_trail_hr(rows: list[dict]) -> dict:
    """Trail HR pace model errors: every trail case (given HR), the races
    (activity type 比賽) and the 全力 capacity samples (given and race level)."""
    th = [r for r in rows if r.get("category") == "trail" and r.get("th")]
    races = [r for r in th if r.get("activity_type") == "race"]
    maxes = [r for r in th if r.get("effort_tag") == "max"]

    def st_ub(xs):
        xs = list(xs)
        s = stats(xs)
        s["boot_ub80"] = boot_ub(xs)
        return s

    def blk(rs):
        return {"n": len(rs), "given": st_ub(r.get("err_th_given") for r in rs),
                "no_durability": stats(r.get("err_th_nodur") for r in rs),
                "race_level": st_ub(r.get("err_th_race") for r in rs),
                "race_level_no_durability": stats(r.get("err_th_race_nodur") for r in rs),
                "race_level_median": stats(r.get("err_th_race_median") for r in rs),
                "total": st_ub(r.get("err_th_total") for r in rs),
                "power_envelope": stats(r.get("err_c") for r in rs)}
    return {"all": blk(th), "races": blk(races), "max_effort": blk(maxes),
            "race_rows": [{k: r.get(k) for k in ("date", "label", "file", "effort_tag", "effort_overridden",
                                                 "effort_reason", "rest_share", "no_power", "power_source",
                                                 "power_unused", "err_th_given",
                                                 "err_th_nodur", "err_th_race", "err_th_race_nodur",
                                                 "err_th_race_median", "err_th_total", "err_c",
                                                 "err_p", "error")}
                          | {"th": r.get("th")} for r in sorted(races, key=lambda r: r["date"])],
            "source": "trailhr.py（越野心率配速模型，推估）", "threshold": THRESHOLDS["trail"],
            "target": TARGETS["trail"]}


def evaluate_hike(case: dict, ctx: dict) -> Optional[dict]:
    """Opted-in solo hikes only (group hikes are never cases)."""
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
    rows = HK.hike_rows(segs, hs.v, 1.0, 1.0, alt, {})
    t_v2 = sum(r["t"] for r in rows)
    km = course["totals"]["km"]
    gain = course["totals"]["gain_m"]
    seg_rows = []
    for s, a, r in zip(segs, act, rows):
        row = {"i": s["i"], "cls": s["cls"], "bin": grade_bin(s["grade"]), "dist_m": s["dist_m"], "grade": s["grade"],
               "t_act": a["t"], "err": None}
        if a["t"] >= SEG_MIN_S and s["dist_m"] >= SEG_MIN_M:
            v_act = s["dist_m"] / a["t"]
            row.update(v_act=v_act, v_pred=r["v"], err=r["v"] / v_act - 1.0)
        seg_rows.append(row)
    return {"km": km, "gain_m": gain, "segments_n": len(segs), "t_act": t_act, "t_v2": t_v2,
            "err_v2": t_v2 / t_act - 1.0, "segments": seg_rows, "hike_n": hs.n_samples}


# ---------------------------------------------------------------------------
# the real dataset
# ---------------------------------------------------------------------------

def _slice(arr: dict, m: np.ndarray) -> dict:
    return {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in arr.items()}


def outdoor_run(w) -> bool:
    return w.sport == "run" and w.sport_type not in ("indoor running", "treadmill running") \
        and "runningtreadmill" not in w.tags and "runningindoor" not in w.tags


def _is_aet_test(ds, w) -> bool:
    """An explicit AeT drift test (engine/aet_test.py): the title says AeT (the
    scheduled session's title, workout_review.session_type) or the plan has
    an AeT row dated that day. A steady run is NOT an AeT test here (the
    session_type steady-drift path would make most steady runs tests)."""
    import re
    title = getattr(w.entry, "title", "") or ""
    if re.search(r"(?<![A-Za-z])AeT(?![A-Za-z])", title):
        return True
    iso = w.entry.start.date().isoformat()
    return any(t.date[:10] == iso and t.aethr is not None for t in ds.plan.thresholds)


def wko5_cp_tests(ds, today: dt.date, skip_dates: set) -> list[dict]:
    """CP tests among the WKO5 activities (workout_review.classify type
    test_cp, cp_protocols) not already found in the synced FIT files: their
    12′ bout, and the 3′ only when the two-point fit is valid (3′ above 12′)."""
    from backend.engine import workout_review as WR
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today)
    out = []
    for w in ds.workouts:
        if not outdoor_run(w) or not (tday - RUN_WINDOW_DAYS < w.day <= tday):
            continue
        iso = w.entry.start.date().isoformat()
        if iso in skip_dates:
            continue
        from backend.engine.racepower import athlete as A
        if not A.power_ok(ds, w):
            continue                       # a "test" on watch-estimated power is no CP test
        try:
            m = WR.measure(ds, w)
            if not m:
                continue
            c = WR.classify(ds, w, m)
            # the power pattern alone ("pattern") marks hard 5 km runs as tests against an
            # older, lower CP (2026-10-01: 43 false tests); the plan / title / race must say so
            if c["type"] != "test_cp" or c.get("test_match") in (None, "pattern"):
                continue
        except Exception:                   # noqa: BLE001
            continue
        ct = m.get("cp_test") or {}
        bouts = []
        if ct.get("p12"):
            bouts.append({"t": 720.0, "p": ct["p12"], "nominal_s": 720, "maximal": True})
        if ct.get("p3") and ct.get("method") == "2pt":
            bouts.append({"t": 180.0, "p": ct["p3"], "nominal_s": 180, "maximal": True})
        if bouts:
            out.append({"date": iso, "bouts": bouts, "idx": w.idx, "source": "workout_review"})
    WR._flush(ds)
    return out


def user_marked(ds, tags: list) -> set:
    """Outdoor runs the user marked as a race (activity type 比賽) or 全力
    (activity_tags): back-test cases over the FULL history, not only the
    last 365 days (the user's diary races go back to 2024; each case is
    still predicted as of the day before with own-date thresholds, so an
    older case is no leak — it just tests an older model)."""
    from backend.engine import activity_tags as AT
    out = set()
    if not tags:
        return out
    for w in ds.workouts:
        if not outdoor_run(w):
            continue
        u = AT.find(tags, w.entry.start, w.entry.file)
        if u and (AT.user_type(u) == "race" or AT.user_effort(u) == "max"):
            out.add(w.idx)
    return out


def candidates(ds, today: dt.date, classes: Optional[dict] = None, marked: Optional[set] = None) -> list[dict]:
    """Outdoor runs ≥ 20 min in the last 365 days (road vs trail by the trail
    tag; season-plan races whatever their length) plus the user-marked races
    / 全力 runs of any date (`marked`, user_marked), each with its intensity
    class, and opted-in solo hikes (3 years). Group hikes never."""
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today)
    races = A.plan_race_runs(ds)
    marked = marked or set()
    runs = [w for w in ds.workouts if outdoor_run(w) and w.day <= tday
            and (tday - RUN_WINDOW_DAYS < w.day or w.idx in marked)]
    classes = classes if classes is not None else A.classify_runs(ds, runs)
    out = []
    for w in runs:
        date = w.entry.start.date().isoformat()
        mv = w.metrics.get("movingduration") or 0
        ev = races.get(w.idx)
        if mv < MIN_MOVING_S and ev is None:
            continue
        if ev is not None and date >= today.isoformat():
            ev = None                      # only past events
        cat = "trail" if A.is_trail(w) else "road"
        c = classes.get(w.idx) or {}
        cs = c.get("capacity") or {}
        tg = cs.get("tags") or {}
        try:
            pt = A.trail_hr_points(ds, [w]) if cat == "trail" else []
            nm_row = A.nonmoving_row(ds, w) if cat == "trail" else None
        except Exception:                   # noqa: BLE001 — the HR pace model is optional per case
            pt, nm_row = [], None
        out.append({"idx": w.idx, "date": date, "category": cat, "label": A.label(w),
                    "priority_a": bool(ev and ev.get("priority") == "A"), "event": ev,
                    "intensity": c.get("cls"), "intensity_reason": c.get("reason"),
                    "cap_sample": bool(cs.get("ok")), "cap_kind": cs.get("kind") if cs.get("ok") else None,
                    "cap_reason": cs.get("reason"), "aet_test": _is_aet_test(ds, w),
                    "activity_type": tg.get("activity_type"), "activity_type_overridden": tg.get("activity_type_overridden"),
                    "effort_tag": tg.get("effort"), "effort_overridden": tg.get("effort_overridden"),
                    "effort_reason": tg.get("effort_reason"), "file": w.entry.file,
                    "rest_share": (cs.get("effort") or {}).get("rest_share"),
                    "trail_pt": pt[0] if pt else None, "nm_row": nm_row, "marked": w.idx in marked})
    solo = A.solo_hikes()
    for w in A.hike_workouts(ds, today):
        if w.day > tday or w.entry.file not in solo:
            continue
        out.append({"idx": w.idx, "date": w.entry.start.date().isoformat(), "category": "hike", "label": A.label(w),
                    "priority_a": False, "intensity": None})
    return sorted(out, key=lambda c: c["date"])


def _hike_days(arr: dict, start: dt.datetime) -> list[tuple[int, dict]]:
    days = np.array([(start + dt.timedelta(seconds=float(x))).date().toordinal() if np.isfinite(x) else -1
                     for x in arr["t"]])
    uniq = [x for x in sorted(set(days.tolist())) if x > 0]
    if len(uniq) <= 1:
        return [(1, arr)]
    return [(n, _slice(arr, days == x)) for n, x in enumerate(uniq, 1)]


def backtest(ds, today: Optional[dt.date] = None, progress=None, tags: Optional[list] = None) -> dict:
    """`tags` = activity_tags rows (default: activity_tags.load(), the app DB
    or WKO5COACH_TAGS_DB)."""
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import grade_model as GM
    from backend.engine.wko5expr.dataset import date_to_day
    today = today or dt.date.today()
    tday = date_to_day(today)
    by_idx = {w.idx: w for w in ds.workouts}
    tags = AT.load() if tags is None else tags
    marked = user_marked(ds, tags)
    all_runs = [w for w in ds.workouts if w.sport == "run" and w.day <= tday + 1
                and (tday - 2 * RUN_WINDOW_DAYS < w.day or w.idx in marked)]
    classes = A.classify_runs(ds, all_runs, tags=tags)
    cmap = {i: c.get("cls") for i, c in classes.items()}
    # trail race HR level: runs whose effective type is 比賽, or 全力 samples (trail_hr_model
    # only reads the ones before each case's as-of date)
    race_idx = {i for i, c in classes.items()
                if ((c.get("capacity") or {}).get("tags") or {}).get("activity_type") == "race"
                or (c.get("capacity") or {}).get("ok")}
    cases, arrays = [], {}
    for c in candidates(ds, today, classes, marked):
        w = by_idx[c["idx"]]
        arr = A.activity_arrays(ds, w)
        if arr is None:
            continue
        c = {**c, "power_source": A.power_source(ds, w)}
        if c["category"] == "hike":
            for n, part in _hike_days(arr, w.entry.start):
                cc = {**c, "day": n, "date": (w.entry.start + dt.timedelta(seconds=float(np.nanmin(part["t"])))).date().isoformat()}
                arrays[(c["idx"], n)] = part
                cases.append(cc)
        else:
            no_pw = arr.get("p") is None or not np.any(np.nan_to_num(arr["p"]) > 0)
            if no_pw or not A.power_ok(ds, w):
                # watch-estimated power counts as no power (power_source.py)
                if c["category"] != "trail":
                    continue
                # trail without power (e.g. the 2024-09-21 race): only the HR pace model
                c = {**c, "no_power": True, "cap_sample": False,
                     "power_unused": not no_pw}
            arrays[(c["idx"], None)] = arr
            cases.append(c)
    weight = ds.setting("weight", tday)
    sex = (ds.plan.profile or {}).get("sex") or "male"
    from backend.engine.racepower import cptest as T
    w_prior = T.w_prime_prior(weight, sex)["mid"]
    fit_tests = A.cp_tests(ds, today, weight, sex)
    try:
        wr_tests = wko5_cp_tests(ds, today, {t["date"] for t in fit_tests})
    except Exception:                       # noqa: BLE001
        wr_tests = []
    test_dates = []
    for t in fit_tests + wr_tests:
        for b in t["bouts"]:
            if b["maximal"]:
                test_dates.append(t["date"])
                cases.append({"idx": None, "date": t["date"], "category": "test", "t_s": b["t"], "p": b["p"],
                              "label": f"{t['date']} CP 測試 {b['nominal_s'] // 60:.0f}′ {b['p']:.0f} W",
                              "intensity": "race", "intensity_reason": "正式 CP 測試（全力段）",
                              "cap_sample": True, "cap_kind": "cp_test",
                              "cap_reason": "FIT 偵測的 3′/12′ 測試" if t.get("source") != "workout_review"
                              else "workout_review 判定的 CP 測試"})
    cap_idx_dates = {w.idx: w.entry.start.date() for w in all_runs
                     if (classes.get(w.idx, {}).get("capacity") or {}).get("ok")}
    derived: dict = {}
    hrcaps: dict = {}
    trailhr: dict = {}

    def n_max(as_of, exclude):
        lo = as_of - dt.timedelta(days=RUN_WINDOW_DAYS)
        n = sum(1 for i, d in cap_idx_dates.items() if lo < d <= as_of and i not in exclude)
        return n + sum(1 for d in set(test_dates) if lo < dt.date.fromisoformat(d) <= as_of)

    def context(c):
        as_of = c["as_of"]
        key = (c.get("idx"), c.get("day"))
        ctx = {"arrays": arrays.get(key), "exclude": c["exclude"]}
        dk = (tuple(sorted(c["exclude"])), as_of)
        if c["category"] == "trail":
            if dk not in trailhr:
                try:
                    trailhr[dk] = A.trail_hr_model(ds, as_of, c["exclude"], race_idx=race_idx, tags=tags)
                except Exception as e:      # noqa: BLE001
                    trailhr[dk] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            ctx["trail_hr"] = trailhr[dk]
            if c.get("no_power"):
                return ctx
        if dk not in derived:
            try:
                derived[dk] = A.derive(ds, as_of, fetch_weather=False, exclude=c["exclude"], strict_as_of=True,
                                       classes=classes, hiking=False)
            except Exception as e:          # noqa: BLE001 — e.g. no data before an old marked race
                derived[dk] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        inp = derived[dk]
        ctx["inputs"] = inp
        if "error" in inp:
            return ctx
        if c.get("cap_sample") or c.get("aet_test"):
            if dk not in hrcaps:
                hrcaps[dk] = A.hr_capacity(ds, as_of, c["exclude"], distribution=False)
            ctx.update(hrcap=hrcaps[dk], w_prime_prior=w_prior, n_max=n_max(as_of, c["exclude"]))
        if c["category"] == "test":
            return ctx
        if c["category"] == "hike":
            gm = A.grade_models(ds, as_of, re_flat=1.0, exclude=c["exclude"], classes=classes)
            ctx["hike_speed"] = gm["hike_speed"]
            return ctx
        road = (inp["re"]["road"] or {}).get("median")
        ctx["grade_re"] = A.grade_models(ds, as_of, re_flat=road, exclude=c["exclude"], classes=classes,
                                         hikes=False)["grade_re"]
        if c.get("intensity"):
            ctx["grade_re_cls"] = A.grade_models(ds, as_of, re_flat=road, exclude=c["exclude"], classes=classes,
                                                 only_classes={c["intensity"]}, hikes=False)["grade_re"]
        runs = [w for w in ds.workouts if w.sport == "run"
                and date_to_day(as_of) - A.RE_WINDOW_DAYS < w.day <= date_to_day(as_of) + 1]
        ctx["grade_re_nogait"] = GM.fit_grade_re(A.grade_samples(ds, runs, c["exclude"]), road or 1.0)
        return ctx

    def evaluate(c, ctx):
        if c["category"] == "test":
            return evaluate_test(c, ctx)
        if c["category"] == "hike":
            return evaluate_hike(c, ctx)
        th = evaluate_trail_hr(c, ctx) if c["category"] == "trail" else {}
        if c.get("no_power"):
            return {**th, "error": "沒有功率：只評估越野心率配速模型"} if th else {"error": "沒有功率"}
        if "error" in (ctx.get("inputs") or {}):
            return {**th, "error": ctx["inputs"]["error"]}
        try:
            r = evaluate_run(c, ctx) or {}
        except Exception as e:              # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        return {**r, **th}

    t0 = time.time()
    rows = run_harness(sorted(cases, key=lambda c: c["date"]), context, evaluate, progress)
    ok = [r for r in rows if "error" not in r]
    run_rows = [r for r in ok if r["category"] in ("road", "trail", "hike")]
    cap_rows = [r for r in ok if r.get("cap_sample") and r.get("f") is not None]
    lb_rows = [{"date": r["date"], "label": r.get("label"), "category": r["category"], "t_s": r["t_act"],
                "p_train": r.get("p_train"), "p_sus": (r.get("capacity") or {}).get("p_sus"), "f": r.get("f"),
                "intensity": r.get("intensity"), "cp": (r.get("capacity") or {}).get("cp"),
                "cp_source": (r.get("capacity") or {}).get("cp_source")}
               for r in ok if r["category"] in ("road", "trail") and r.get("f") is not None]
    terrain = summarise_terrain(run_rows)
    capacity = summarise_capacity(cap_rows, lb_rows)
    validated = validated_flags(terrain, capacity, [r for r in run_rows if r.get("intensity") == "race"])
    try:
        hike_cap = capacity_backtest(ds, today, progress)
    except Exception as e:                  # noqa: BLE001
        hike_cap = {"error": f"{type(e).__name__}: {str(e)[:160]}", "passed": False}
    validated["hike_capacity"] = bool(hike_cap.get("passed"))
    gm_now = A.grade_models(ds, today, classes=classes)
    counts = {k: sum(1 for r in run_rows if r.get("intensity") == k) for k in CLASSES}
    leaks = sum(1 for r in ok if (r.get("capacity") or {}).get("cp_source") == "wko5")
    win_lo = date_to_day(today) - RUN_WINDOW_DAYS
    in_win = [w for w in all_runs if (win_lo < w.day or w.idx in marked) and w.day <= date_to_day(today)]
    samples = []
    for w in in_win:
        cs = (classes.get(w.idx) or {}).get("capacity") or {}
        if not cs:
            continue
        near = cs.get("ok") or cs.get("user_marked") or cs.get("category") in ("5k", "10k", "half", "marathon") or \
            (cs.get("rule") == "trail_race_like" and all(c["ok"] for c in cs.get("checks", []) if c["id"] in ("km", "time")))
        if near:
            tg = cs.get("tags") or {}
            samples.append({"idx": w.idx, "date": w.entry.start.date().isoformat(), "label": A.label(w),
                            "category": "trail" if A.is_trail(w) else "road", "ok": bool(cs.get("ok")),
                            "kind": cs.get("kind"), "reason": cs.get("reason"), "checks": cs.get("checks"),
                            "km": w.metrics.get("distance"), "climb_m": w.metrics.get("climbing"),
                            "moving_s": w.metrics.get("movingduration"), "event": cs.get("event"),
                            "hr_class": (classes.get(w.idx) or {}).get("cls"), "file": w.entry.file,
                            "activity_type": tg.get("activity_type"), "effort": tg.get("effort"),
                            "effort_overridden": tg.get("effort_overridden"),
                            "activity_type_overridden": tg.get("activity_type_overridden"),
                            "rest_share": (cs.get("effort") or {}).get("rest_share"),
                            "hr_frac": (cs.get("effort") or {}).get("hr_frac")})
    kinds: dict = {}
    for s in samples:
        if s["ok"]:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    kinds["cp_test_bouts"] = sum(1 for c in cases if c["category"] == "test")
    kinds["aet_test"] = sum(1 for c in cases if c.get("aet_test"))
    try:
        hr_now = A.hr_capacity(ds, today)
    except Exception as e:                  # noqa: BLE001
        hr_now = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    try:
        th_now = {k: v for k, v in A.trail_hr_model(ds, today, race_idx=race_idx, tags=tags).items() if k != "points"}
    except Exception as e:                  # noqa: BLE001
        th_now = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    trail_hr = summarise_trail_hr(rows)
    trail_hr["model_now"] = th_now
    pc = pass_check([r.get("err_th_race") for r in rows if r.get("category") == "trail" and r.get("th")
                     and r.get("activity_type") == "race"], THRESHOLDS["trail"])
    t_med = trail_hr["races"]["race_level"]["median_abs"]
    pc["target_met"] = bool(t_med is not None and t_med <= TARGETS["trail"] and pc["passed"])
    trail_hr["pass_rule"] = pc
    trail_hr["passed"] = bool(pc["passed"])
    validated["trail_hr"] = trail_hr["passed"]
    return {"computed_at": dt.datetime.now(WX.TZ).isoformat(timespec="seconds"), "today": today.isoformat(),
            "seconds": round(time.time() - t0, 1), "version": 2, "rows": rows,
            "terrain": terrain, "capacity": capacity, "validated": validated, "hike_capacity": hike_cap,
            "effort_validated": bool(capacity["effort"]["passed"]), "thresholds": THRESHOLDS, "min_n": MIN_N,
            "intensity_counts": counts, "grade_bins": BIN_LABELS, "hike_hr": gm_now["hike_hr"],
            "hike_basis": gm_now["hike_basis"], "leaks_wko5_cp": leaks,
            "classes_all": {k: sum(1 for v in cmap.values() if v == k) for k in CLASSES},
            "capacity_samples": {"counts": kinds, "rows": samples,
                                 "trail_race_like": [s for s in samples if s["category"] == "trail" and s["ok"]]},
            "hr_capacity": {k: v for k, v in hr_now.items() if k != "points"} | {"points": hr_now.get("points")},
            "trail_hr": trail_hr, "user_marked": len(marked),
            "power_source": A.power_summary(ds, [w for w in all_runs if A.outdoor(w)]),
            "notes": [A.GROUP_HIKE_NOTE + "；只有你標記為自己走的登山才會成為回測案例",
                      "能力樣本看「努力度」不看「是不是比賽」：你標記的努力度優先（全力＝一定算、其他＝一定不算）；"
                      "自動：路跑用全力路跑規則，越野用移動心率（≥ x*(T) − 0.03 × LTHR，x*(T) 是文獻先驗的全力心率曲線，"
                      "2 小時 0.90、8 小時 0.83，推估）＋長休息（≥ 5 分的停留 ≤ 10 %）",
                      "越野心率模型的比賽心率 x*(T)：你之前的比賽／全力跑（路跑＋越野，當天 LTHR）擬合，收縮到"
                      "Fornasiero 2018／Kerhervé 2015 的形狀先驗；耐久 δ 收縮到 0.05/h（Clark 2019），只在清過的視窗上量；"
                      "熱用你的 β 0.224 bpm／Hadley 把心率移到 Hadley 120；推估",
                      "越野通過門檻 8 %（目標 6 %）：n ≥ 5 且中位數 80 % bootstrap 上界 ≤ 門檻 + 2 個百分點（n ≥ 10 時 ≤ 門檻）",
                      "總時間 = 移動時間（驗證目標）＋ 非移動時間（之前比賽的停留，另外預估、另外列）",
                      "你標記為比賽或全力的活動不受 365 天限制（每場仍用前一天的資料預測）；自動偵測的樣本只看近 365 天",
                      "越野心率配速模型（推估）：effort km ÷ 移動時間 對 移動心率/LTHR，加耐久衰減；不需要功率",
                      "每一場都用活動前一天的資料、排除該活動；不用 WKO5 今天存的 mFTP / TTE，"
                      "改用當天以前的 mean-max 重算 PD 模型（mFTP / TTE），再加上 CP 下限",
                      "強度分類用每次活動當天以前的 LTHR / AeT（計畫測試只算當天以前做過的；否則用當天以前資料的自動估算，"
                      "每次跑步對照它當天以前重算的 CP）",
                      "地形模型回測餵實際功率，只檢驗「功率 → 速度」（坡度-RE、走跑、下坡上限、技術係數），不是預測準確度",
                      "能力模型只用能力樣本檢驗：努力度＝全力的跑步（你的標記，或自動：自配速全力路跑、"
                      "≥ 10 km / ≥ 90 分且心率全力、沒有長休息的越野）與 CP 測試全力段；賽季計畫比賽只標為「比賽」",
                      "心率能力（推估）：近 90 天穩定平路跑的心率–功率回歸外插到 LTHR；"
                      f"能力樣本 < {FEW_MAXIMAL} 次時當第二個下限（合併）"]}


# ---------------------------------------------------------------------------
# 百岳 walking capacity (docs/research/baiyue-from-running.md §7.1)
# ---------------------------------------------------------------------------

CAP_MIN_SEG_WINDOWS = 3        # a scored segment is ≥ 300 m (after the first window is dropped)
CAP_MIN = {"trail": {"segs": 30, "groups": 10}, "hike": {"segs": 30, "groups": 5}}
CAP_BIAS_MAX = 0.05
HIGH_Z = 2500.0
SANITY_BAND = (0.60, 1.00)     # capacity moving ÷ actual group moving time (推估, §7.1 D)
SANITY_SHARE = 0.80


def _segments(wins: list[dict]) -> list[list[dict]]:
    by: dict = {}
    for w in wins:
        by.setdefault(w["seg"], []).append(w)
    return [sorted(v, key=lambda w: w["k"]) for v in by.values() if len(v) >= CAP_MIN_SEG_WINDOWS]


def _seg_time(ws, vf) -> float:
    return sum(100.0 / max(1e-6, vf(w)) for w in ws)


def score_segments(cap, segs: list[list[dict]], load_of, alt_pct: Optional[float] = None) -> list[dict]:
    """Predicted ÷ actual time of each held-out segment: full model, the
    physiological prior alone (no δ, no altitude), and Tobler."""
    from dataclasses import replace
    from backend.engine.racepower.grade_model import tobler_kmh
    model = cap if alt_pct is None else replace(cap, alpha={**cap.alpha, "post": alt_pct})
    out = []
    for ws in segs:
        L = load_of(ws[0])
        t_act = sum(100.0 / w["v"] for w in ws)
        z = float(np.mean([w["z"] for w in ws]))
        h = (ws[0].get("t") or 0.0) / 3600.0
        t_full = _seg_time(ws, lambda w: model.v(w["g"], L, z=w["z"], h=(w.get("t") or 0.0) / 3600.0))
        t_prior = _seg_time(ws, lambda w: cap.prior_only(w["g"], L))
        t_tob = _seg_time(ws, lambda w: tobler_kmh(w["g"]) / 3.6)
        out.append({"a": ws[0].get("a"), "src": ws[0].get("src"), "z": z, "h": h, "n": len(ws), "L": L,
                    "g": float(np.mean([w["g"] for w in ws])), "t_act": t_act,
                    "err": t_full / t_act - 1.0, "err_prior": t_prior / t_act - 1.0, "err_tobler": t_tob / t_act - 1.0,
                    "log_err": math.log(t_full / t_act)})
    return out


def _cap_summary(rows: list[dict], kind: str) -> dict:
    s = stats(r["err"] for r in rows)
    groups = len({r["a"] for r in rows})
    need = CAP_MIN[kind]
    reasons = []
    if s["n"] < need["segs"] or groups < need["groups"]:
        reasons.append(f"段數 {s['n']}（需 ≥ {need['segs']}）、{'活動' if kind == 'trail' else '趟'} {groups}（需 ≥ {need['groups']}）")
    if s["median_abs"] is not None and s["median_abs"] > THRESHOLDS["hike_capacity"]:
        reasons.append(f"時間誤差中位數 {s['median_abs']:.1%} > {THRESHOLDS['hike_capacity']:.0%}")
    if s["bias"] is not None and abs(s["bias"]) > CAP_BIAS_MAX:
        reasons.append(f"偏差 {s['bias']:+.1%} 超過 ±{CAP_BIAS_MAX:.0%}")
    return {"time": s, "groups": groups, "prior": stats(r["err_prior"] for r in rows),
            "tobler": stats(r["err_tobler"] for r in rows), "passed": not reasons, "reasons": reasons,
            "log_sd": float(np.std([r["log_err"] for r in rows])) if len(rows) >= 5 else None}


def capacity_backtest(ds, today: Optional[dt.date] = None, progress=None, days: bool = True,
                      boot_reps: int = 200) -> dict:
    """§7.1 A–D, G: leave-one-activity-out on the walked trail windows,
    leave-one-trip-out on the HR-filtered 百岳 windows (z ≥ 2500 m reported
    separately, with the personal / shrunk / Wehrlin altitude slopes), the
    prior comparison, the whole-day sanity band on the group days, and the
    altitude diagnostics. Cheap: every fold refits the windows only."""
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import capacity as CAP
    today = today or dt.date.today()
    t0 = time.time()
    x = A.walk_capacity_inputs(ds, today)

    def fit_without(a):
        keep = lambda ws: [w for w in ws if w.get("a") != a]
        return CAP.fit_walk_capacity(weight=x["weight"], v_run=x["v_run"], trail=keep(x["trail"]),
                                     hike=keep(x["hike"]), flat=keep(x["flat"]), hike_down=keep(x["hike_down"]),
                                     aet_of=x["aet"].get, pack_of=x["pack_of"], boot_reps=boot_reps)
    rows_a, rows_b, rows_bh = [], [], []
    trail_segs = _segments(x["trail"])
    hike_segs = _segments(x["hike"])
    groups = sorted({s[0].get("a") for s in trail_segs} | {s[0].get("a") for s in hike_segs}, key=str)
    for i, a in enumerate(groups):
        if progress:
            progress(i, len(groups), {"label": f"百岳能力回測 {i + 1}/{len(groups)}"})
        cap = fit_without(a)
        ta = [s for s in trail_segs if s[0].get("a") == a]
        hb = [s for s in hike_segs if s[0].get("a") == a]
        rows_a += score_segments(cap, ta, lambda w: CAP.L_TRAIL)
        if hb:
            rb = score_segments(cap, hb, lambda w: x["pack_of"](w.get("a")))
            cur = (cap.alpha.get("diagnostics") or {}).get("versions", {}).get("current")
            for pct, key in ((cur["pct_per_km"] if cur else None, "err_personal"), (CAP.ALT_PRIOR_PCT, "err_wehrlin")):
                if pct is None:
                    continue
                alt = score_segments(cap, hb, lambda w: x["pack_of"](w.get("a")), alt_pct=pct)
                for r, r2 in zip(rb, alt):
                    r[key] = r2["err"]
            rows_b += rb
    rows_bh = [r for r in rows_b if r["z"] >= HIGH_Z]
    A_ = _cap_summary(rows_a, "trail")
    B_ = _cap_summary(rows_b, "hike")
    high = {"time": stats(r["err"] for r in rows_bh), "trips": len({r["a"] for r in rows_bh}),
            "personal": stats(r.get("err_personal") for r in rows_bh),
            "shrunk": stats(r["err"] for r in rows_bh), "wehrlin": stats(r.get("err_wehrlin") for r in rows_bh)}
    worst = max((v["median_abs"] for v in (high["personal"], high["wehrlin"]) if v["median_abs"] is not None), default=None)
    high["shrunk_not_worst"] = None if worst is None or high["shrunk"]["median_abs"] is None else \
        high["shrunk"]["median_abs"] <= worst + 1e-9
    high["passed"] = bool(high["time"]["bias"] is not None and abs(high["time"]["bias"]) <= CAP_BIAS_MAX)
    full = A.walk_capacity(ds, today, inputs=x, boot_reps=400)
    both = rows_a + rows_b
    c_order = None
    if both:
        m = {k: stats(r[k] for r in both)["median_abs"] for k in ("err", "err_prior", "err_tobler")}
        c_order = {**m, "ok": m["err"] <= m["err_prior"] <= m["err_tobler"]}
    out = {"version": 1, "computed_at": dt.datetime.now(WX.TZ).isoformat(timespec="seconds"),
           "today": today.isoformat(), "trail": A_, "hike": B_, "high": high, "prior_order": c_order,
           "passed": bool(A_["passed"] and B_["passed"]), "threshold": THRESHOLDS["hike_capacity"],
           "sigma_loo": float(np.std([r["log_err"] for r in both])) if len(both) >= 10 else None,
           "altitude": full.alpha.get("diagnostics"), "alpha": {k: v for k, v in full.alpha.items() if k != "diagnostics"},
           "beta": full.beta, "gamma": full.gamma_info, "basis": full.basis, "v_run": full.v_run_info,
           "rows": {"trail": rows_a, "hike": rows_b}}
    if days:
        out["days"] = capacity_days(ds, today, full, x)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def capacity_days(ds, today: dt.date, cap, x: dict) -> dict:
    """§7.1 D: every hiking day (group-paced): capacity moving time on the
    day's own track ÷ the actual moving time. A sanity band, not accuracy:
    ≥ 80 % of the days should fall in 0.60–1.00 (never slower than the
    group, never absurdly fast)."""
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import capacity as CAP
    solo = A.solo_hikes()
    rows = []
    for w in A.hike_workouts(ds, today):
        arr = A.activity_arrays(ds, w)
        if arr is None:
            continue
        for n, part in _hike_days(arr, w.entry.start):
            tk = track_of(part)
            if tk is None:
                continue
            track, idx = tk
            try:
                course = CO.build_course(track)
            except Exception:              # noqa: BLE001
                continue
            segs = course["segments"]
            d = _dt(part["t"])
            moving = (np.nan_to_num(part["kmh"]) > 0.3 * 3.6) & (d > 0)
            act = actual_segments(part, idx, track, segs, moving)
            t_act = sum(a["t"] for a in act)
            if t_act < HIKE_MIN_MOVING_S:
                continue
            L = CAP.day_pack(x["pack_of"](w.idx), n)
            h, t_cap = 0.0, 0.0
            for s in segs:
                v = cap.v(s["grade"], L, z=s["z_mean"], h=h, n_day=n)
                t_cap += s["dist_m"] / v
                h = t_cap / 3600.0
            r = t_cap / t_act
            rows.append({"date": (w.entry.start + dt.timedelta(seconds=float(np.nanmin(part["t"])))).date().isoformat(),
                         "label": A.label(w), "day": n, "t_act": t_act, "t_cap": t_cap, "ratio": r,
                         "in_band": SANITY_BAND[0] <= r <= SANITY_BAND[1], "solo": w.entry.file in solo,
                         "pack_kg": L, "km": course["totals"]["km"], "gain_m": course["totals"]["gain_m"]})
    grp = [r for r in rows if not r["solo"]]
    share = sum(r["in_band"] for r in grp) / len(grp) if grp else None
    return {"rows": rows, "n": len(grp), "share_in_band": share, "band": list(SANITY_BAND),
            "passed": share is not None and share >= SANITY_SHARE,
            "ratio": stats(r["ratio"] for r in grp)}


def store_capacity(res: dict, path=None) -> dict:
    """Merge a capacity back-test into the stored version-2 result (creating
    a minimal one when none exists) and set validated["hike_capacity"]."""
    cur = load(path) or {"version": 2, "validated": {k: False for k in THRESHOLDS}, "effort_validated": False,
                         "rows": [], "notes": []}
    cur["hike_capacity"] = res
    cur.setdefault("validated", {})["hike_capacity"] = bool(res.get("passed"))
    save(cur, path)
    return cur


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
    nothing stored (or an old-format result) → nothing validated."""
    r = load(path)
    if not r or r.get("version") != 2:
        return {k: False for k in THRESHOLDS}, False
    return {k: bool((r.get("validated") or {}).get(k)) for k in THRESHOLDS}, bool(r.get("effort_validated"))


def class_model_flag(path=None) -> bool:
    """True when the stored back-test found the error clearly different by
    class AND the race-like class's own RE(g) better than the pooled one
    (then the planner uses it)."""
    r = load(path)
    cd = ((r or {}).get("terrain") or {}).get("class_differs") or {}
    return bool(cd.get("clear") and (cd.get("class_model_better") or {}).get("race"))


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
