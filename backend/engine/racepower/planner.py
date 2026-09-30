"""
Race plan v2 — the three modes on a segmented course (docs/research/
racepower-v2.md §4, §5, §6, §7). Orchestrates the pure modules:

    difficulty (F1–F6)  pacing (F7–F11b, F15, F16)  hike (F12, F13, F17)
    env.segment_factors (F14)  course (segments)  grade_model (RE(g), v_h(g))

Validation gate (§4 "驗證前的降級路徑"): until the §3B back-test passes for
the course's category (road / trail / hike), the whole-race time and average
power come from the v1 method — Riegel F1–F3 on v1's RE (road RE, or effort km
× trail RE), or v1's EP/h walking model — and the v2 segment model only
distributes that total: segment times are scaled so they add up to the v1
time, powers keep the v2 allocation (so the average is conserved), and every
segment target carries 推估. Once a category passes, the v2 segment sum is the
result. A manual course has no grade information, so it always uses v1.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.engine.algorithms import minetti
from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import env as ENV
from backend.engine.racepower import hike as HK
from backend.engine.racepower import pacing as PC
from backend.engine.racepower import predict as PR
from backend.engine.racepower import riegel as R
from backend.engine.zones import zones_json

MODES = ("time", "power", "auto")
AUTO_TARGETS = {"max": 1.00, "hard": 0.95, "steady": 0.85}
STRATEGY_SIGN = {"even": 0.0, "negative": -1.0, "positive": 1.0}
STRATEGY_DEFAULT = {"road": {"negative": 0.02, "positive": 0.02}, "trail": {"negative": 0.02, "positive": 0.03}}
EXTRAP_FACTOR = 1.5
DAMAGE_NOTE = 1.05
MAX_COROS_STEPS = 50
# Stryd Race Power Calculator table (help.stryd.com 6879547, verbatim, % of
# 10 km power). F18: the table is a cross-check (已驗證: it equals Riegel's
# D1 form with k ≈ −0.069 from 10 km up); the linear formula on the same
# page (it disagrees with the table by 1.8 points at 21.1 km) is 不採用 and
# deliberately absent here.
STRYD_TABLE = ((10.0, 100.0), (21.1, 94.6), (42.2, 89.9))
HINT_30S = "看 30 秒平均功率"
STEEP_POWER_GRADE = 0.08       # Stryd ≈ metabolic power validated to 8 % (van Rassel 2026)
HR_FIRST_SHARE = 0.30          # 自組: above this share of steep distance, HR targets come first


def _pace(v: float) -> Optional[float]:
    return 1000.0 / v if v and v > 0 else None


def _clock(start: Optional[str], secs: float) -> Optional[str]:
    if not start:
        return None
    try:
        h, m = (int(x) for x in start.split(":")[:2])
    except ValueError:
        return None
    t = dt.datetime(2000, 1, 1, h, m) + dt.timedelta(seconds=round(secs))
    day = (t.date() - dt.date(2000, 1, 1)).days
    return t.strftime("%H:%M") + (f" (+{day})" if day else "")


def _stops_before(stops, km: float) -> float:
    return sum(float(s.get("minutes") or 0) * 60.0 for s in stops or [] if float(s.get("km") or 0) <= km + 1e-6)


def stryd_table_power(p10: float, km: float) -> Optional[float]:
    """F18 cross-check: race power from the athlete's 10 km power and
    Stryd's table, interpolated in log distance; only 10–42.2 km."""
    if km < 10 or km > 42.2 + 0.5:
        return None
    xs = [math.log(a) for a, _ in STRYD_TABLE]
    ys = [b for _, b in STRYD_TABLE]
    return p10 * float(np.interp(math.log(km), xs, ys)) / 100.0


def altitude_note(segs: list[dict], accl: str) -> Optional[str]:
    """Above 2800 m (Wehrlin's measured range) the altitude factor is an
    extrapolation; quote Bassett 1999's two curves at the top as a
    cross-check (their own range is 0–4000 m, 推估 above 3000 m)."""
    zs = [s.get("z_mean") for s in segs if s.get("z_mean") is not None]
    if not zs or max(zs) <= 2800:
        return None
    top = max(s.get("z_max") or s["z_mean"] for s in segs)
    return (f"最高約 {top:.0f} m：超過 2800 m 的海拔修正是外插（推估）。對照：Bassett 1999 在這個高度"
            f"已適應 {ENV.bassett_pct(top, True):.1f}%、未適應 {ENV.bassett_pct(top, False):.1f}% 的海平面有氧能力")


def solve_whole(d_m: float, re: float, weight: float, f_target: float, m: float, psus) -> float:
    """Whole-race mode C on one RE: t = D / (RE·f*·M·P_sus(t)/W), solved by
    bisection on log t (the right side increases slowly with t). With f* = 1
    and T ≥ TTE this is v1's `solve_riegel_re` fixed point."""
    def g(t):
        return t - d_m / (re * f_target * m * psus(t) / weight)
    lo, hi = 10.0, 1e7
    for _ in range(300):
        mid = math.sqrt(lo * hi)
        if g(mid) > 0:
            hi = mid
        else:
            lo = mid
        if hi / lo < 1 + 1e-13:
            break
    return math.sqrt(lo * hi)


def _scale(res: dict, t_total: float) -> dict:
    """Degraded path: scale every segment time to a total of t_total (powers
    unchanged, so the time-weighted average is conserved)."""
    c = t_total / res["T"]
    rows = [{**r, "t": r["t"] * c, "v": r["v"] / c} for r in res["rows"]]
    return {**res, "rows": rows, "T": t_total, "scale": c}


def _merge_for_export(segs: list[dict], limit: int = MAX_COROS_STEPS) -> list[dict]:
    s = [dict(x) for x in segs]
    while len(s) > limit:
        i = min(range(len(s)), key=lambda j: s[j]["t"])
        j = i + 1 if i == 0 else i - 1 if i == len(s) - 1 else (i - 1 if s[i - 1]["t"] < s[i + 1]["t"] else i + 1)
        a, b = sorted((i, j))
        t = s[a]["t"] + s[b]["t"]
        s[a] = {**s[a], "end_km": s[b]["end_km"], "dist_m": s[a]["dist_m"] + s[b]["dist_m"], "t": t,
                "power": (s[a]["power"] * s[a]["t"] + s[b]["power"] * s[b]["t"]) / t,
                "cls_label": s[a]["cls_label"] if s[a]["t"] >= s[b]["t"] else s[b]["cls_label"]}
        del s[b]
    return s


def coros_steps(segments: list[dict], band: float = 0.03) -> list:
    """Plan segments → COROS steps through the existing mapping
    (backend/sync/coros_workouts.py Step / build_program): one time-based
    training step per segment, power target ± 3 %."""
    from backend.sync import coros_workouts as CW
    steps = []
    for s in _merge_for_export(segments):
        p = s["power"]
        name = f"{s['start_km']:.1f}-{s['end_km']:.1f}k {s['cls_label']}"
        steps.append(CW.Step(CW.EX_TRAIN, max(1, int(round(s["t"]))), ("power", p * (1 - band), p * (1 + band)), name))
    return steps


# ---------------------------------------------------------------------------
# road / trail
# ---------------------------------------------------------------------------

def plan_run(*, v1: dict, course: dict, grade_re, opts: dict, validated: dict,
             effort_validated: bool, longest_s: Optional[float] = None,
             capacity: Optional[dict] = None) -> dict:
    """v1 = the /predict response for the same inputs (used values, env, v1
    result = the cross-check and the degraded baseline). `capacity` =
    {spread, lower_bound, message, lthr, aet} from the inputs (effort band,
    the lower-bound warning, HR-first trail targets)."""
    used, env, r1 = v1["used"], v1["env"], v1["result"]
    kind = v1["type"]
    capacity = capacity or {}
    cp, w_prime, tte, k = used["cp"]["value"], used["w_prime"]["value"], used["tte"]["value"], used["k"]["value"]
    cp2 = (used.get("cp2") or {}).get("value")
    if kind == "trail" and hasattr(grade_re, "for_trail"):
        # trail technicality on flats / descents, race-like class
        grade_re = grade_re.for_trail("race")
    weight, re_v1 = used["weight"]["value"], used["re"]["value"]
    mode = opts.get("mode") or "auto"
    warnings: list[str] = []
    segs = [dict(s) for s in course["segments"]]
    gpx = course.get("source") == "gpx"
    accl = opts.get("acclimatisation") or "acclimatised"
    frm = {x: env["from"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    to = {x: env["to"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    if gpx:
        ms = ENV.segment_factors([s["z_mean"] for s in segs], frm, to, accl)
    else:
        ms = [env["M"] if accl == "acclimatised" else ENV.segment_factors([to["altitude_m"]], frm, to, accl)[0]] * len(segs)
    for s, m in zip(segs, ms):
        s["M"] = m
    dsum = sum(s["dist_m"] for s in segs)
    mbar = sum(s["M"] * s["dist_m"] for s in segs) / dsum

    def psus(t):
        return DF.p_sus(t, cp, w_prime, tte, k, cp2=cp2)

    # hill / strategy settings
    trail = kind == "trail"
    hills = opts.get("hills") or {}
    alpha = min(PC.ALPHA_MAX, max(0.0, float(hills.get("up", PC.ALPHA_DEFAULT))))
    beta = min(0.3, max(0.0, float(hills.get("down", PC.BETA_DEFAULT))))
    strat = opts.get("strategy") or {}
    skind = strat.get("kind") or "even"
    amount = strat.get("amount")
    if amount is None:
        amount = STRATEGY_DEFAULT["trail" if trail else "road"].get(skind, 0.0)
    amount = min(PC.SIGMA_MAX, max(0.0, float(amount)))
    sigma = STRATEGY_SIGN.get(skind, 0.0) * amount
    if amount >= 0.04:
        warnings.append("配速幅度 ≥ 4 %：過去的比賽資料顯示，前後差距小的配速通常比較快")
    locks = {int(x["seg"]) - 1: float(x["power"]) for x in opts.get("locks") or []
             if x.get("power") and 0 < int(x["seg"]) <= len(segs)}

    model = PC.RunModel(weight, grade_re.re, grade_re.v_max)
    d_eff_m = (r1.get("effort_km") or r1["distance_km"]) * 1000.0
    cat = "trail" if trail else "road"
    v2_primary = bool(validated.get(cat)) and gpx
    f_target = float(opts.get("effort_target") or 1.0)

    # ---- whole race by the v1 method (always computed: baseline + 對照) ------
    t_c = solve_whole(d_eff_m, re_v1, weight, f_target, mbar, psus)
    p_c = d_eff_m / t_c / re_v1 * weight
    if mode == "time":
        t_star = opts.get("target_time_s")
        if not t_star and opts.get("target_pace_s_per_km"):
            t_star = opts["target_pace_s_per_km"] * course["totals"]["km"]
        if not t_star:
            raise ValueError("模式「目標時間」需要時間或配速")
        t_whole, p_whole = float(t_star), d_eff_m / float(t_star) / re_v1 * weight
    elif mode == "power":
        p_star = opts.get("target_power")
        if not p_star and opts.get("target_pct_cp"):
            p_star = opts["target_pct_cp"] * cp
        if not p_star:
            raise ValueError("模式「目標功率」需要功率或 %CP")
        p_star = float(p_star) * (mbar if opts.get("power_is_training") else 1.0)
        t_whole, p_whole = d_eff_m * weight / (re_v1 * p_star), p_star
    else:
        t_whole, p_whole = t_c, p_c

    kw = {"beta": beta, "sigma": sigma, "locks": locks}

    def solver(a):
        if v2_primary:
            if mode == "time":
                return PC.solve_time_mode(t_whole, segs, model, cp, alpha=a, **kw)
            if mode == "power":
                return PC.solve_power_mode(p_whole, segs, model, alpha=a, **kw)
            return PC.solve_auto_mode(f_target, psus, segs, model, cp, alpha=a, **kw)
        return PC.solve_power_mode(p_whole, segs, model, alpha=a, **kw)

    cp_w = cp2 or cp                # the W′ budget runs above the short-range CP
    res, alpha_used, runs = PC.solve_with_budget(solver, segs, cp_w, w_prime, alpha)
    if mode == "auto" and not v2_primary and gpx:
        # the whole-race M must be the one the effort uses (time-weighted
        # Σ(Pᵢ/Mᵢ)tᵢ, not the distance-weighted mean): a couple of fixed-point
        # passes make f come out at f* exactly
        for _ in range(6):
            sc = _scale(res, t_whole)
            pt = sum(r["P"] / s["M"] * r["t"] for r, s in zip(sc["rows"], segs)) / sc["T"]
            m_eff = p_whole / pt
            t_new = solve_whole(d_eff_m, re_v1, weight, f_target, m_eff, psus)
            done = abs(t_new - t_whole) < 0.05
            t_whole, t_c = t_new, t_new
            p_whole = p_c = d_eff_m / t_new / re_v1 * weight
            res, alpha_used, runs = PC.solve_with_budget(solver, segs, cp_w, w_prime, alpha)
            if done:
                break
    if alpha_used < alpha - 1e-9:
        warnings.append(f"上坡彈性從 +{alpha:.0%} 縮到 +{alpha_used:.1%}：否則有坡段超過 CP 太久（W′ 用超過 75 %）")
    if not v2_primary:
        res = _scale(res, t_whole)
    T = res["T"]
    rows = res["rows"]
    p_bar = sum(r["P"] * r["t"] for r in rows) / T
    # the bisection clamps silently when a target is out of reach (locks,
    # absurd targets): say so instead of showing a confident wrong number
    if mode == "time" and abs(T - t_whole) > 1.0:
        warnings.append(f"達不到目標時間：最接近的是 {T / 3600:.2f} h（鎖定的分段或目標超出範圍）")
    if (mode == "power" or not v2_primary) and abs(p_bar - p_whole) > 0.5:
        warnings.append(f"平均功率只能到 {p_bar:.0f} W（目標 {p_whole:.0f} W）：鎖定的分段或下坡上限限制了配置")
    p_train = sum(r["P"] / s["M"] * r["t"] for r, s in zip(rows, segs)) / T
    eff = DF.effort(p_train, T, cp, w_prime, tte, k, cp_spread=capacity.get("spread"),
                    lower_bound=(capacity.get("lower_bound") or {}).get("cp_min"), cp2=cp2)
    eff["badge"] = None if effort_validated else "推估"
    if eff.get("inconsistent"):
        eff["warning"] = capacity.get("message") or "模型 CP 低於你實際撐過的功率，請重測"
        warnings.append(eff["warning"])
    over_idx = {i for c in runs if c["over"] for i in range(c["from"], c["to"] + 1)}
    wb = None
    wmodel = opts.get("wbal")
    if wmodel in ("wko5", "skiba", "skiba_run") and w_prime:
        if wmodel == "wko5":
            vals, lab = PC.wbal_wko5(rows, segs, cp, w_prime), "WKO5 算法（70 % τ 300 s + 30 % τ 25 s）"
        elif wmodel == "skiba":
            vals, lab = PC.wbal_skiba(rows, segs, cp, w_prime, "cycling"), "Skiba τ = 546·e^(−0.01·D) + 316（自行車）"
        else:
            vals, lab = PC.wbal_skiba(rows, segs, cp, w_prime, "running"), "τ = 372·e^(−0.02·D) + 102（跑步擬合）"
        wb = {"model": wmodel, "values": vals, "label": lab, "badge": None if wmodel == "wko5" else "推估",
              "w_prime": w_prime}
    zs = zones_json(cp)
    out_segs = []
    cum = 0.0
    stops = opts.get("stops") or []
    for i, (s, r) in enumerate(zip(segs, rows)):
        cum += r["t"]
        notes = []
        if s.get("walk"):
            notes.append(s["walk"])
        if r["capped"]:
            notes.append("下坡上限")
        if i in over_idx or r["P"] > cp_w * s["M"] * 1.0001:
            notes.append("超 CP")
        if r.get("locked"):
            notes.append("已鎖定")
        if gpx and getattr(grade_re, "walked", None) and grade_re.walked(s["grade"]):
            notes.append("走（你在這個坡度多半走）")
        gf = minetti.grade_factor(s["grade"])
        v = r["v"]
        trusted = grade_re.trusted(s["grade"]) if gpx else True
        z = next((zz for zz in zs if r["P"] / cp >= zz["lo"] and (zz["hi"] is None or r["P"] / cp < zz["hi"])), None)
        out_segs.append({
            **{x: s.get(x) for x in ("i", "start_km", "end_km", "dist_m", "gain_m", "loss_m", "grade", "max_grade",
                                     "z_start", "z_end", "z_mean", "z_max", "cls", "cls_label", "walk", "climb_no")},
            "M": s["M"], "power": r["P"], "pct_cp": r["P"] / cp, "zone": z["id"] if z else "1A 以下",
            "speed_ms": v, "pace_s_per_km": _pace(v), "gap_pace_s_per_km": _pace(v * gf) if trail else None,
            "vert_m_per_h": v * s["grade"] * 3600.0 if abs(s["grade"]) >= 0.15 else None,
            "t": r["t"], "cum_s": cum, "eta": _clock(opts.get("start_time"), cum + _stops_before(stops, s["end_km"])),
            "capped": r["capped"], "locked": r.get("locked", False), "notes": notes,
            "badge": None if (v2_primary and trusted) else "推估", "trusted": trusted, "hint": HINT_30S,
        })
    if wb:
        for sg, val in zip(out_segs, wb["values"]):
            sg["wbal_j"] = val

    km = course["totals"]["km"]
    tl = eff["t_lim_s"]
    if eff["f"] > 1.0 + 1e-9:
        warnings.append(f"努力度 {eff['f']:.0%} 超出模型可持續範圍：這個平均功率大約只撐得了 {tl / 3600:.1f} h")
    if longest_s and T > EXTRAP_FACTOR * longest_s and mode == "auto" and f_target > 0.95:
        warnings.append(f"預估時間超過你最長有效紀錄的 {EXTRAP_FACTOR} 倍：建議把努力目標降到 95 %（吃力）")
    dmg = PC.damage_index(rows, lambda p: DF.t_lim(p / mbar, cp, w_prime, tte, k, cp2=cp2))
    hr_first = None
    if trail and gpx:
        steep = sum(s["dist_m"] for s in segs if abs(s["grade"]) > STEEP_POWER_GRADE)
        share = steep / dsum if dsum else 0.0
        if share > HR_FIRST_SHARE:
            hr_first = {"steep_share": share, "lthr": capacity.get("lthr"), "aet": capacity.get("aet")}
            cap_txt = f"上限 LTHR {capacity['lthr']:.0f} bpm" if capacity.get("lthr") else "上限 LTHR"
            warnings.append(f"{share:.0%} 的路段坡度超過 8 %（Stryd 功率驗證的範圍外）：以心率為主（{cap_txt}，"
                            f"長距離壓在 AeT 附近），功率為輔（推估）")
    if dmg > DAMAGE_NOTE:
        warnings.append(f"逐段耗損指數 {dmg:.2f} > {DAMAGE_NOTE}：這只是診斷數字，短時間的起伏會被高估")
    if not gpx:
        warnings.append("手動路線沒有坡度剖面：整場時間用 v1 方法，分段只是平均切開")
    if gpx and not v2_primary:
        warnings.append("分段目標是推估：回測通過前，整場時間照 v1 方法算，分段只負責分配")
    if any(not s["trusted"] for s in out_segs):
        warnings.append("有坡度超過 8 % 的段，你在這個坡度的資料不足：該段目標是外插")
    an = altitude_note(segs, accl) if gpx else None
    if an:
        warnings.append(an)
    tstar = t_whole if mode == "time" else None
    target = None
    if tstar:
        imp = PR.improvement_needed(d_eff_m, tstar, re_v1, weight, cp, tte, k, mbar)
        target = {"time_s": tstar, **imp}
    ctrl = {"auto": {"time_s": t_c, "power": p_c}}
    p10 = None
    crosscheck = {"v1": {"time_s": r1["time_s"], "power": r1["power"], "method": "v1 /predict"}}
    if not trail:
        t10 = solve_whole(10000.0, re_v1, weight, 1.0, mbar, psus)
        p10 = 10000.0 / t10 / re_v1 * weight
        sp = stryd_table_power(p10, km)
        if sp:
            crosscheck["stryd_table"] = {"power": sp, "p10k": p10, "time_s": d_eff_m * weight / (re_v1 * sp)}
    else:
        crosscheck["cvi"] = r1.get("cvi_crosscheck")
    summary = {"time_s": T, "power": p_bar, "power_train": p_train, "pct_cp": p_bar / cp, "w_per_kg": p_bar / weight,
               "pace_s_per_km": T / km, "km": km, "gain_m": course["totals"].get("gain_m"),
               "loss_m": course["totals"].get("loss_m"), "M": mbar, "total_method": "v2" if v2_primary else "v1",
               "category": cat, "mode": mode, "effort_target": f_target if mode == "auto" else None,
               "finish_eta": _clock(opts.get("start_time"), T + _stops_before(stops, km + 1)),
               "stops_s": _stops_before(stops, km + 1), "badge": None if v2_primary else "推估",
               "alpha_used": alpha_used, "sigma": sigma, "beta": beta, "damage": dmg, "hr_first": hr_first,
               "cp2": cp2, "tech": grade_re.tech_factor() if trail and hasattr(grade_re, "tech_factor") else None}
    return {"type": kind, "summary": summary, "effort": eff, "segments": out_segs, "target": target,
            "compare": ctrl, "crosscheck": crosscheck, "wbal": wb, "wprime_runs": runs,
            "profile": course.get("profile"), "climbs": course.get("climbs"), "wpts": course.get("wpts"),
            "course_totals": course["totals"], "course_warnings": course.get("warnings") or [],
            "zones": zs, "warnings": warnings, "validated": validated, "hint": HINT_30S}


# ---------------------------------------------------------------------------
# 百岳
# ---------------------------------------------------------------------------

def _quantiles(vals):
    v = [x for x in vals if x and math.isfinite(x)]
    if len(v) < 4:
        return None
    q = np.percentile(v, [25, 50, 75, 90])
    return [float(x) for x in q]


def plan_hike(*, v1: dict, course: dict, hike_speed, inp: dict, opts: dict, validated: dict) -> dict:
    """百岳 three modes (§4.4): the "power" is a walking-speed multiple λ_h
    (1.0 = your usual pace). Time A → λ_h; B λ_h → time; C λ_h = 1."""
    used, env, r1 = v1["used"], v1["env"], v1["result"]
    weight = used["weight"]["value"]
    mode = opts.get("mode") or "auto"
    warnings: list[str] = []
    gpx = course.get("source") == "gpx"
    accl = opts.get("acclimatisation") or "unacclimatised"
    frm = {x: env["from"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    to = {x: env["to"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    pack = r1["pack_factor"]
    eph = used["eph"]["value"]
    hdays = (inp.get("hiking") or {}).get("days") or []
    fat = HK.day_fatigue(hdays)
    if fat["warning"] and (opts.get("day_splits_km") or (course["totals"].get("days") or 1) > 1):
        warnings.append(fat["warning"])
    ratio, ratio_src = HK.moving_ratio(opts.get("moving_rows") or [])
    m_v1 = ENV.segment_factors([to["altitude_m"]], frm, to, accl)[0]
    q = _quantiles([d["ep_per_h"] for d in hdays])
    cuts = DF.hike_cuts(q[0], q[1], q[2], q[3]) if q else [0.8, 1.0, 1.15, 1.3]
    v2_primary = bool(validated.get("hike")) and gpx

    if gpx:
        segs = [dict(s) for s in course["segments"]]
        from backend.engine.racepower import course as CO
        segs = CO.cut_at(segs, opts.get("day_splits_km") or [])
        terrain = {int(k_): v for k_, v in (opts.get("terrain") or {}).items()}
        for s in segs:
            s["terrain"] = terrain.get(s["i"], "normal")
        ref = {"altitude_m": hike_speed.ref_alt_m if hike_speed.ref_alt_m is not None else frm["altitude_m"],
               "temp_c": to["temp_c"], "rh_pct": to["rh_pct"]}
        alt = ENV.segment_factors([s["z_mean"] for s in segs], ref, to, accl)
        rows1 = HK.hike_rows(segs, hike_speed.v, 1.0, pack, alt, fat)
        t1_v2 = sum(r["t"] for r in rows1)
        ep = course["totals"]["km"] + course["totals"]["gain_m"] / 100.0
        t1_v1 = ep / (eph * m_v1 * pack) * 3600.0
        t1 = t1_v2 if v2_primary else t1_v1
        c = t1 / t1_v2
    else:
        segs, rows1, alt = [], [], []
        t1 = r1["time_s"] * (r1["M"] / m_v1 if m_v1 else 1.0)
        c = 1.0
    if mode == "time":
        t_star = opts.get("target_time_s")
        if not t_star:
            raise ValueError("模式「目標時間」需要移動時間")
        lam = t1 / float(t_star)
    elif mode == "power":
        lam = float(opts.get("speed_factor") or 1.0)
    else:
        lam = 1.0
    T = t1 / lam
    he = DF.hike_effort(lam, cuts)
    he["badge"] = "推估"
    out = []
    cum = 0.0
    day = 1
    day_start = 0.0
    stops = opts.get("stops") or []
    for s, r, a in zip(segs, rows1, alt):
        t = r["t"] * c / lam
        if s.get("day", 1) != day:
            day, day_start = s["day"], cum
        cum += t
        v = s["dist_m"] / t
        clock = _clock(opts.get("start_time"), (cum - day_start) / ratio + _stops_before(stops, s["end_km"]))
        out.append({**{x: s.get(x) for x in ("i", "start_km", "end_km", "dist_m", "gain_m", "loss_m", "grade",
                                             "max_grade", "z_mean", "z_max", "cls", "cls_label", "walk", "day")},
                    "terrain": s["terrain"], "eta_factor": r["eta"], "f_day": r["f_day"], "A": a,
                    "speed_kmh": v * 3.6, "pace_s_per_km": _pace(v),
                    "vert_m_per_h": v * s["grade"] * 3600.0 if s["grade"] > 0.05 else None,
                    "t": t, "cum_s": cum, "eta": clock, "badge": None if v2_primary else "推估"})
    days = []
    if gpx:
        for n in sorted({s.get("day", 1) for s in out}):
            ss = [s for s in out if s.get("day", 1) == n]
            km = sum(s["dist_m"] for s in ss) / 1000.0
            gain = sum(s["gain_m"] or 0 for s in ss)
            loss = sum(s["loss_m"] or 0 for s in ss)
            h = sum(s["t"] for s in ss) / 3600.0
            kcal = PR.yamamoto_cc(h, km, gain, loss) * (weight + r1["pack_kg"])
            days.append({"day": n, "km": km, "gain_m": gain, "loss_m": loss, "moving_h": h,
                         "clock_h": h / ratio, "kcal": kcal, "water_ml": [0.7 * kcal, 0.8 * kcal]})
    else:
        for d in r1["days"]:
            days.append({"day": d["day"], "km": d["km"], "gain_m": d["gain_m"], "loss_m": d["loss_m"],
                         "moving_h": d["moving_h"] * t1 / r1["time_s"] / lam if r1["time_s"] else d["moving_h"],
                         "clock_h": None, "kcal": d["kcal"], "water_ml": d["water_ml"]})
        for d in days:
            d["clock_h"] = d["moving_h"] / ratio
    km = course["totals"]["km"]
    gain = course["totals"]["gain_m"]
    kcal = sum(d["kcal"] for d in days)
    cross = {"v1": {"time_s": r1["time_s"], "method": "v1 EP/h"},
             "naismith_s": HK.naismith_h(km, gain) * 3600.0,
             "ep": km + gain / 100.0}
    if gpx:
        cross["langmuir_s"] = HK.langmuir_h(segs) * 3600.0
    if accl == "partial":
        warnings.append("部分適應是推估：取已適應與未適應兩條海拔曲線的中間，沒有定量研究")
    an = altitude_note(segs, accl) if gpx else None
    if an:
        warnings.append(an)
    if gpx and not v2_primary:
        warnings.append("分段時間是推估：回測通過前，整趟移動時間照 v1 EP/h 模型算，分段只負責分配")
    hk = inp.get("hiking") or {}
    if not hk.get("solo_n"):
        warnings.append((hk.get("note") or "百岳多為跟團，速度不代表個人能力，不列入目標時間推算") +
                        "：步行速度用 Tobler 先驗加上你心率 ≥ AeT 的陡坡爬升窗（推估）")
    warnings.append(f"時鐘時間 = 移動時間 ÷ {ratio:.2f}（{ratio_src}）")
    warnings.append("背負係數是 v1 的線性假設（(體重 + 5 kg) ÷ (體重 + 背負)）；Pandolf 公式尚未對過原文")
    summary = {"time_s": T, "clock_s": T / ratio + _stops_before(stops, km + 1), "speed_factor": lam, "km": km,
               "gain_m": gain, "loss_m": course["totals"].get("loss_m"), "M": m_v1, "pack_factor": pack,
               "eph_personal": eph, "ep_per_h": (km + gain / 100.0) / (T / 3600.0), "kcal": kcal,
               "water_ml": [0.7 * kcal, 0.8 * kcal], "hr_cap": used.get("aet", {}).get("value"),
               "total_method": "v2" if v2_primary else "v1", "category": "hike", "mode": mode,
               "acclimatisation": accl, "moving_ratio": ratio, "badge": None if v2_primary else "推估"}
    return {"type": "baiyue", "summary": summary, "effort": he, "segments": out, "days": days,
            "crosscheck": cross, "fatigue": fat, "profile": course.get("profile"), "wpts": course.get("wpts"),
            "course_totals": course["totals"], "course_warnings": course.get("warnings") or [],
            "warnings": warnings + (v1.get("warnings") or []), "validated": validated,
            "compare": {"auto": {"time_s": t1}}}
