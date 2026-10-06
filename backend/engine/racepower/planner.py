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
from backend.engine.racepower import runwalk as RW
from backend.engine.racepower import weather as WX
from backend.engine.zones import zones_json
from backend.i18n import _

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
HR_FIRST_SHARE = 0.30          # 推估: above this share of steep distance, HR targets come first


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


def _fade(res: dict, shape: Optional[dict]) -> dict:
    """SP-222: the segment times with the athlete's own fade (trailhr.fade_times — the sum stays
    the whole-race time); speeds follow the times, powers stay the allocation's (on trail power is a
    reference: the same power late in the race buys less speed). `p_alloc` keeps the allocation's
    time-weighted power for the 「平均功率只能到」 check. Unchanged without an applied shape."""
    from backend.engine.racepower import trailhr as TH
    ts = TH.fade_times([r["t"] for r in res["rows"]], shape)
    if ts is None:
        return res
    p_alloc = sum(r["P"] * r["t"] for r in res["rows"]) / res["T"]
    rows = [{**r, "t": t, "v": r["v"] * r["t"] / t} for r, t in zip(res["rows"], ts)]
    return {**res, "rows": rows, "faded": True, "p_alloc": p_alloc}


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
# per-segment, time-of-day heat
#
# The heat penalty itself is Hadley's (env.heat_penalty_pct, ported from the
# SuperPower workbook's `v4 Calcs`, docs/research/superpower-calculator.md
# §1.1). Feeding it the forecast at each segment's predicted clock time is our
# own composition (推估; racepower-v2.md §8 proposes it for long events) —
# segment outputs carry 推估. The clock depends on the segment times and the
# times on Mᵢ, so the planner iterates to a fixed point (max |Δ cumulative
# time| < HEAT_TOL_S). On a GPX course with `heat_ref_alt_m` (the elevation
# the race-day temperature refers to: the weather point, sent by the page)
# each segment's temperature is moved to its own mean elevation by the
# standard lapse rate (env.segment_temp, −0.0065 K/m, RH kept — as 百岳 and
# the climatology do; owner decision 2026-10-06, SP-210 follow-up). Without
# it the temperatures are used as given (one height for the whole course).
# ---------------------------------------------------------------------------

HEAT_MAX_PASSES = 8
HEAT_TOL_S = 1.0
HEAT_WINDOW_H = 48.0


def _start_datetime(date: Optional[str], start: Optional[str]) -> Optional[dt.datetime]:
    if not date or not start:
        return None
    try:
        d = dt.date.fromisoformat(str(date)[:10])
        h, m = (int(x) for x in str(start).split(":")[:2])
        return dt.datetime(d.year, d.month, d.day, h, m)
    except ValueError:
        return None


def _heat_context(opts: dict) -> tuple[Optional[list], Optional[dt.datetime], Optional[str]]:
    """(hourly rows, start clock, None) when per-segment heat applies, else
    (None, None, the reason the single value is used)."""
    if opts.get("hourly_heat") is False:
        return None, None, "逐時熱修正已關閉"
    rows = []
    for r in opts.get("hourly") or []:
        x = WX._hour_row(r.get("t"), r.get("temp_c"), r.get("rh_pct"), r.get("dew_c"))
        if x:
            rows.append(x)
    rows.sort(key=lambda r: r["t"])
    if not rows:
        return None, None, "沒有逐時預報：中央氣象署一週預報沒有逐時資料、離線，或還沒取得比賽日天氣"
    start = _start_datetime(opts.get("date"), opts.get("start_time"))
    if start is None:
        return None, None, "逐時熱修正需要比賽日期與起跑時間"
    lo = start - dt.timedelta(hours=WX.HOURLY_EDGE_H)
    hi = start + dt.timedelta(hours=HEAT_WINDOW_H)
    if not any(lo <= dt.datetime.fromisoformat(r["t"]) <= hi for r in rows):
        return None, None, "逐時預報沒有涵蓋比賽時間"
    return rows, start, None


def _cum_times(rows) -> list[float]:
    out, c = [], 0.0
    for r in rows:
        c += r["t"]
        out.append(c)
    return out


def _segment_heat(hourly: list, start: dt.datetime, segs: list, rows: list, stops) -> list:
    """Conditions at each segment's midpoint clock (start + time before it +
    aid stops up to its start + half its own time); None outside the rows."""
    out, before = [], 0.0
    for s, r in zip(segs, rows):
        when = start + dt.timedelta(seconds=before + 0.5 * r["t"] + _stops_before(stops, s["start_km"]))
        h = WX.hourly_at(hourly, when)
        if h is not None:
            h["clock"] = when.strftime("%H:%M")
        out.append(h)
        before += r["t"]
    return out


def _at_height(h: Optional[dict], z_ref: Optional[float], z: Optional[float]) -> Optional[dict]:
    """Conditions moved from the weather point's elevation z_ref to the
    segment's z: temperature by env.segment_temp (−0.0065 K/m), RH kept, dew
    point rebuilt. No z_ref / z → unchanged."""
    if h is None or z_ref is None or z is None:
        return h
    t = ENV.segment_temp(h["temp_c"], z_ref, z)
    return {**h, "temp_c": t, "dew_c": ENV.dew_point(t, h["rh_pct"])["dew_c"]}


def _heat_fields(h: Optional[dict], to_side: dict, z_ref: Optional[float] = None, z: Optional[float] = None) -> dict:
    if h is None:
        if z_ref is not None and z is not None:
            one = _at_height({"temp_c": to_side["temp_c"], "rh_pct": to_side["rh_pct"]}, z_ref, z)
            return {**one, "heat_pct": ENV.heat_penalty_pct(one["temp_c"], one["rh_pct"]),
                    "heat_clock": None, "heat_src": "single"}
        return {"temp_c": to_side["temp_c"], "dew_c": to_side.get("dew_c"), "rh_pct": to_side["rh_pct"],
                "heat_pct": to_side.get("heat_penalty_pct", ENV.heat_penalty_pct(to_side["temp_c"], to_side["rh_pct"])),
                "heat_clock": None, "heat_src": "single"}
    return {"temp_c": h["temp_c"], "dew_c": h["dew_c"], "rh_pct": h["rh_pct"],
            "heat_pct": ENV.heat_penalty_pct(h["temp_c"], h["rh_pct"]), "heat_clock": h.get("clock"),
            "heat_src": "hourly"}


def heat_acclimation(opts: dict) -> Optional[dict]:
    """The race calculator's #heat-accl choice → S on both sides of M
    (heat-acclimation.md §4.3, §5.5; engine/heat.py). opts:
      heat_acclimatisation = {"mode": auto | none | partial | acclimatised | custom, "s"}
      heat_status = {"s_race": {"center", "low", "high"}, "s_from", "source"} (the API
                    projects it from the athlete's exposure history; auto uses it)
    None (no choice) = v1 behaviour. Every S is 推估 (the S model is 推估).

    a (2026-10-02, unsourced-rules.md §A8; racepower/heatacc.py): 0 — S is
    shown but does not discount the heat penalty — unless heat_status
    carries an HRC slope test (`hrc_test`) that supports acclimation; then
    heat.A_RECOVER. The band keeps a = 0 as its conservative end and the
    literature's optimistic a as the other."""
    from backend.engine import heat as HT
    from backend.engine.racepower import heatacc as HA
    ha = opts.get("heat_acclimatisation")
    if not ha:
        return None
    mode = (ha.get("mode") if isinstance(ha, dict) else ha) or "auto"
    st = opts.get("heat_status") or {}
    race = st.get("s_race") or {}
    if mode == "auto":
        if race.get("center") is None:
            s = s_lo = s_hi = 0.0
            src = "沒有熱暴露資料：當作未適應"
        else:
            s, s_lo, s_hi = race["center"], race.get("low", race["center"]), race.get("high", race["center"])
            src = st.get("source") or "近期熱暴露推算到比賽日"
    elif mode == "custom":
        s = min(1.0, max(0.0, float(ha.get("s") or 0.0)))
        s_lo = s_hi = s
        src = "自訂"
    else:
        s = s_lo = s_hi = HT.PRESET_S.get(mode, 0.0)
        src = {"none": "未適應", "partial": "部分（S 0.5，推估）", "acclimatised": "已適應（S 0.9，推估）"}.get(mode, mode)
    s_from = st.get("s_from")
    a, a_why = HA.acclimation_a(st.get("hrc_test"), HT.SCENARIOS["center"]["a"])
    supported = a > 0
    if mode in ("partial", "acclimatised", "custom") and not supported:
        src += "（你的資料不支持熱適應：S 只顯示，不折抵熱懲罰）"
    return {"mode": mode, "s": s, "s_from": s_from if s_from is not None else 0.0, "a": a,
            "a_literature": HT.A_RECOVER, "a_reason": a_why, "a_supported": supported,
            "hrc_test": st.get("hrc_test"),
            "scenarios": {"center": (a, s),
                          # conservative: no credit, lowest S
                          "low": (HT.SCENARIOS["low"]["a"] if supported else 0.0, s_lo),
                          "high": (HT.SCENARIOS["high"]["a"], s_hi)},   # optimistic: the literature's a
            "source": src, "badge": "推估"}


def _heat_band(segs: list, ts: list, h_from_pct: float, hacc: dict) -> dict:
    """Heat effect under the three parameter sets of §4.4: the time-weighted
    to-side penalty after acclimation minus the from side, and the matching
    time ratio T_x / T_center ≈ M̄_center / M̄_x (power ∝ M; 推估)."""
    T = sum(ts) or 1.0
    out = {}
    for name, (a, s) in hacc["scenarios"].items():
        sf = hacc["s_from"]
        hf = h_from_pct * (1.0 - a * min(1.0, sf))
        pen = sum(t * max(0.0, sg["heat_pct"] * (1.0 - a * s)) for sg, t in zip(segs, ts)) / T
        out[name] = {"a": a, "s": s, "penalty_pct": pen - hf}
    c = out["center"]["penalty_pct"]
    for name in out:
        out[name]["time_ratio"] = (1.0 - c / 100.0) / (1.0 - out[name]["penalty_pct"] / 100.0)
    raw = sum(t * sg["heat_pct"] for sg, t in zip(segs, ts)) / T - h_from_pct
    return {**out, "raw_penalty_pct": raw}


def _heat_profile(hourly: list, start: dt.datetime, out_segs: list, stops) -> list[dict]:
    """The forecast rows inside the race window, each placed at the km the
    plan reaches at that clock (for the profile chart)."""
    xs, ys, before = [], [], 0.0
    for s in out_segs:
        dep = before + _stops_before(stops, s["start_km"])
        xs += [dep, dep + s["t"]]
        ys += [s["start_km"], s["end_km"]]
        before += s["t"]
    if not xs:
        return []
    out = []
    for r in hourly:
        sec = (dt.datetime.fromisoformat(r["t"]) - start).total_seconds()
        if sec < 0 or sec > xs[-1]:
            continue
        out.append({"t": r["t"], "clock": r["t"][11:16], "km": float(np.interp(sec, xs, ys)),
                    "temp_c": r["temp_c"], "dew_c": r["dew_c"], "rh_pct": r["rh_pct"],
                    "heat_pct": ENV.heat_penalty_pct(r["temp_c"], r["rh_pct"])})
    return out


# ---------------------------------------------------------------------------
# road / trail
# ---------------------------------------------------------------------------

def trail_hr_estimate(model: Optional[dict], km: float, gain_m: float, f_target: float = 1.0,
                      hadley: Optional[float] = None, lthr: Optional[float] = None,
                      stops=None) -> Optional[dict]:
    """The primary trail estimate (2026-10-01, trailhr.py): moving time of a
    course from the athlete's HR pace model at f_target × their full-effort
    HR curve x*(T) (2026-10-02: solved at the predicted T), with the
    durability decline. `hadley` (race-day Hadley sum) and `lthr`: the
    athlete's heat β moves the HR level (trailhr.heat_shift; the model's x
    is at Hadley 120), so the caller must not apply the Hadley time penalty
    on top (`heat_beta` True). The non-moving time (aid stations, queues,
    stops) is predicted apart (nonmoving.py) and never added to time_s —
    the moving time stays the validated target; time_total_s = both. None
    without a usable model."""
    from backend.engine.racepower import nonmoving as NM
    from backend.engine.racepower import trailhr as TH
    if not model or not (model.get("a") or model.get("c")) or not km:
        return None
    e = km + (gain_m or 0.0) / TH.effort_divisor()
    sh = TH.heat_shift(hadley, lthr)
    xs = model.get("xstar")
    if xs:
        t, x = TH.predict_race(model, e, xs, f=f_target, x_shift=sh)
        tn, _ = TH.predict_race(model, e, xs, f=f_target, delta=0.0, x_shift=sh)
    else:
        # an older stored model without the curve: its single race level
        x = f_target * (model.get("x_race") or TH.TRAILHR["x_default"]) - sh
        t, tn = TH.predict_time(model, e, x), TH.predict_time(model, e, x, delta=0.0)
    if not t:
        return None
    nm = NM.predict(model.get("nonmoving"), t, stops)
    di = model.get("delta_info") or {}
    return {"time_s": t, "time_no_durability_s": tn, "x": x, "x_star": x + sh, "eff_km": e,
            "x_race": model.get("x_race"), "x_race_source": model.get("x_race_source"), "delta": model.get("delta"),
            "delta_raw": model.get("delta_raw"), "v_floor": TH.TRAILHR["v_floor"], "delta_warning": (di.get("all") or {}).get("warning"),
            "delta_fuel_split": bool(di.get("split")),
            # the terrain-matched measurement behind δ (trailhr step 7): raw pooled value, 95 % CI,
            # runs, and whether the LOO gate kept it or fell back to the 0.05 /h prior
            "delta_measured": {"raw": (di.get("all") or {}).get("raw_median"),
                               "ci95": (di.get("all") or {}).get("raw_ci95"),
                               "n": (di.get("all") or {}).get("n"),
                               "shrunk": (di.get("all") or {}).get("delta"),
                               "choice": (di.get("gate") or {}).get("choice"),
                               "prior": (di.get("all") or {}).get("prior")} if di else None,
            "xstar": {k: xs.get(k) for k in ("x0", "s", "n", "kind", "prior", "source")} if xs else None,
            "heat_beta": bool(TH.TRAILHR["heat_beta"] and hadley is not None and lthr),
            "heat_shift": sh, "hadley": hadley,
            "nonmoving": nm, "time_total_s": t + nm["total_s"] if nm else None,
            # SP-222: the athlete's own fade by moving hour (trailhr.fade_shape) for the segment times
            "fade": model.get("fade"),
            "n_runs": model.get("n"), "kind": model.get("kind"), "source": TH.SOURCE, "badge": "推估"}


def plan_run(*, v1: dict, course: dict, grade_re, opts: dict, validated: dict,
             effort_validated: bool, longest_s: Optional[float] = None,
             capacity: Optional[dict] = None, trail_hr: Optional[dict] = None) -> dict:
    """v1 = the /predict response for the same inputs (used values, env, v1
    result = the cross-check and the degraded baseline). `capacity` =
    {spread, lower_bound, message, lthr, aet} from the inputs (effort band,
    the lower-bound warning, HR-first trail targets). `trail_hr` = the trail
    HR pace model (athlete.trail_hr_model): for a trail race in auto mode it
    gives the whole-race time (the power envelope is shown only as a
    cross-check — on trail it was +46 % power / −35 % time off)."""
    used, env, r1 = v1["used"], v1["env"], v1["result"]
    kind = v1["type"]
    capacity = capacity or {}
    cp, w_prime, tte, k = used["cp"]["value"], used["w_prime"]["value"], used["tte"]["value"], used["k"]["value"]
    cp2 = (used.get("cp2") or {}).get("value")
    if kind == "trail" and hasattr(grade_re, "for_trail"):
        # trail technicality on flats / descents, race-like class
        grade_re = grade_re.for_trail("race")
    # SP-250: the race-day 路況 (乾 / 濕) picks the dry or wet technicality on g ≤ +2 % — only when the
    # model has the split (both marked groups ≥ 30 windows, kept by the back-test); else ignored
    surface = None
    if kind == "trail" and getattr(grade_re, "surface_split", False):
        grade_re = grade_re.for_surface(opts.get("surface"))
        surface = grade_re.surface
    weight, re_v1 = used["weight"]["value"], used["re"]["value"]
    mode = opts.get("mode") or "auto"
    warnings: list[str] = []
    segs = [dict(s) for s in course["segments"]]
    gpx = course.get("source") == "gpx"
    accl = opts.get("acclimatisation") or "acclimatised"
    frm = {x: env["from"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    to = {x: env["to"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    zs = [s["z_mean"] for s in segs] if gpx else [to["altitude_m"]] * len(segs)
    hacc = heat_acclimation(opts)
    hs_pair = (hacc["s_from"], hacc["s"]) if hacc else None

    def factors(heat=None):
        """Mᵢ per segment; `heat` = one (temp_c, rh_pct) per segment or None
        (the single race-day value, exactly as before)."""
        if gpx or heat is not None or hs_pair is not None:
            if hacc:
                return ENV.segment_factors(zs, frm, to, accl, heat, heat_s=hs_pair, a=hacc["a"])
            return ENV.segment_factors(zs, frm, to, accl, heat, heat_s=hs_pair)
        return [env["M"] if accl == "acclimatised" else ENV.segment_factors([to["altitude_m"]], frm, to, accl)[0]] * len(segs)

    dsum = sum(s["dist_m"] for s in segs)

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
    f_target = float(opts.get("effort_target") or 1.0)

    def hr_for(hadley):
        """The HR-model total at a race-day Hadley (the athlete's β moves the HR level)."""
        if not (trail and mode == "auto"):
            return None
        return trail_hr_estimate(trail_hr, course["totals"]["km"], course["totals"].get("gain_m") or 0.0, f_target,
                                 hadley=hadley, lthr=capacity.get("lthr"), stops=opts.get("stops"))
    hr_est = hr_for(env["to"].get("heat_index_sum_f"))
    # the HR model is at Hadley 120 and its heat comes from β, so its time only takes the
    # altitude part of M: every segment's heat set equal to the training side (推估)
    m_alt = ENV.segment_factors(zs, frm, to, accl, [(frm["temp_c"], frm["rh_pct"])] * len(segs))
    mbar_alt = sum(m * s["dist_m"] for m, s in zip(m_alt, segs)) / (sum(s["dist_m"] for s in segs) or 1.0)
    # the HR estimate gives the total; the segment model only distributes it
    v2_primary = bool(validated.get(cat)) and gpx and hr_est is None

    kw = {"beta": beta, "sigma": sigma, "locks": locks}
    cp_w = cp2 or cp                # the W′ budget runs above the short-range CP

    def solve_all(ms: list[float]) -> dict:
        """Everything that depends on the segment Mᵢ: the whole-race v1 time,
        the allocation, the W′ budget, the auto-mode M fixed point, scaling."""
        for s, m in zip(segs, ms):
            s["M"] = m
        mbar = sum(s["M"] * s["dist_m"] for s in segs) / dsum
        # ---- whole race by the v1 method (always computed: baseline + 對照) --
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
        elif hr_est is not None:
            # altitude (and heat when no β applies): the HR model is in training conditions;
            # M scales the speed (推估). With β the heat is already in hr_est's HR level.
            t_whole = hr_est["time_s"] / (mbar_alt if hr_est.get("heat_beta") else mbar)
            p_whole = d_eff_m / t_whole / re_v1 * weight
        else:
            t_whole, p_whole = t_c, p_c
        tgt = {"t": t_whole, "p": p_whole}

        def solver(a):
            if v2_primary:
                if mode == "time":
                    return PC.solve_time_mode(tgt["t"], segs, model, cp, alpha=a, **kw)
                if mode == "power":
                    return PC.solve_power_mode(tgt["p"], segs, model, alpha=a, **kw)
                return PC.solve_auto_mode(f_target, psus, segs, model, cp, alpha=a, **kw)
            return PC.solve_power_mode(tgt["p"], segs, model, alpha=a, **kw)

        res, alpha_used, runs = PC.solve_with_budget(solver, segs, cp_w, w_prime, alpha)
        varied = max(ms) - min(ms) > 1e-12
        if mode == "auto" and not v2_primary and hr_est is None and (gpx or varied):
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
                tgt.update(t=t_whole, p=p_whole)
                res, alpha_used, runs = PC.solve_with_budget(solver, segs, cp_w, w_prime, alpha)
                if done:
                    break
        if not v2_primary:
            res = _scale(res, t_whole)
            if hr_est is not None:
                # SP-222: the HR model's total spread by the athlete's own fade, not evenly
                res = _fade(res, hr_est.get("fade"))
        return {"res": res, "alpha_used": alpha_used, "runs": runs, "t_whole": t_whole, "p_whole": p_whole,
                "t_c": t_c, "p_c": p_c, "mbar": mbar}

    # ---- per-segment, time-of-day heat (推估) ----------------------------
    stops = opts.get("stops") or []
    heat_rows, start_dt, heat_reason = _heat_context(opts)
    # each segment's temperature at its own height (GPX + the weather point's elevation)
    z_ref = opts.get("heat_ref_alt_m") if gpx else None
    one_to = {"temp_c": to["temp_c"], "rh_pct": to["rh_pct"]}

    def heat_of(hs):
        """(temp, rh) per segment: the hour's conditions, else the To value, at the segment's height."""
        out = []
        for h, sg in zip(hs, segs):
            x = h if h is not None else _at_height(one_to, z_ref, sg.get("z_mean"))
            out.append((x["temp_c"], x["rh_pct"]))
        return out
    st = solve_all(factors(heat_of([None] * len(segs)) if z_ref is not None else None))
    seg_heat = None
    heat_info = {"mode": "single", "passes": 0, "converged": None, "delta_s": None, "outside": 0,
                 "reason": heat_reason, "badge": None}
    if heat_rows:
        prev = _cum_times(st["res"]["rows"])
        for n in range(1, HEAT_MAX_PASSES + 1):
            seg_heat = [_at_height(h, z_ref, sg.get("z_mean"))
                        for h, sg in zip(_segment_heat(heat_rows, start_dt, segs, st["res"]["rows"], stops), segs)]
            if hr_est is not None:
                # the time-weighted race-day Hadley of the hours each segment is run
                from backend.engine import heat as HT
                tw = [(r["t"], HT.hadley_sum(h["temp_c"], h["rh_pct"])) for r, h in zip(st["res"]["rows"], seg_heat) if h]
                if tw:
                    hr_est = hr_for(sum(t * x for t, x in tw) / (sum(t for t, _ in tw) or 1.0)) or hr_est
            st = solve_all(factors(heat_of(seg_heat)))
            cur = _cum_times(st["res"]["rows"])
            delta = max(abs(a - b) for a, b in zip(cur, prev))
            prev = cur
            heat_info.update(passes=n, delta_s=delta, converged=delta < HEAT_TOL_S)
            if delta < HEAT_TOL_S:
                break
        out_n = sum(1 for h in seg_heat if h is None)
        heat_info.update(mode="hourly", outside=out_n, badge="推估")
        if not heat_info["converged"]:
            warnings.append(f"逐時熱修正沒有收斂（{HEAT_MAX_PASSES} 次後 ETA 仍差 {heat_info['delta_s']:.1f} 秒）：分段溫度是近似值")
        if out_n:
            warnings.append(f"{out_n} 段的 ETA 超出逐時預報範圍：這些段用單一溫度 {to['temp_c']:.1f} °C")
    elif heat_reason and opts.get("hourly_heat", True):
        warnings.append(f"熱修正用單一溫度 {to['temp_c']:.1f} °C / 濕度 {to['rh_pct']:.0f} %（{heat_reason}）")
    if z_ref is not None:
        heat_info["ref_alt_m"] = z_ref
        warnings.append(_("熱：每段溫度由天氣點（{z:.0f} m）以每 100 m 0.65 °C 換算到該段海拔，濕度不變（推估）", z=z_ref))
    res, alpha_used, runs = st["res"], st["alpha_used"], st["runs"]
    t_whole, p_whole, t_c, p_c, mbar = st["t_whole"], st["p_whole"], st["t_c"], st["p_c"], st["mbar"]
    if alpha_used < alpha - 1e-9:
        warnings.append(f"上坡彈性從 +{alpha:.0%} 縮到 +{alpha_used:.1%}：否則有坡段超過 CP 太久（W′ 用超過 75 %）")
    T = res["T"]
    rows = res["rows"]
    p_bar = sum(r["P"] * r["t"] for r in rows) / T
    # the bisection clamps silently when a target is out of reach (locks,
    # absurd targets): say so instead of showing a confident wrong number
    if mode == "time" and abs(T - t_whole) > 1.0:
        warnings.append(f"達不到目標時間：最接近的是 {T / 3600:.2f} h（鎖定的分段或目標超出範圍）")
    if (mode == "power" or not v2_primary) and abs(res.get("p_alloc", p_bar) - p_whole) > 0.5:
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
    if wmodel == "skiba":
        # the cycling-τ option was removed (user, 2026-10-01): old saved choices get WKO5's dFRC
        wmodel = "wko5"
    if wmodel in ("wko5", "skiba_run") and w_prime:
        if wmodel == "wko5":
            vals, lab = PC.wbal_wko5(rows, segs, cp, w_prime), "WKO5 算法 dFRC（70 % τ 300 s + 30 % τ 25 s）"
        else:
            vals, lab = PC.wbal_skiba(rows, segs, cp, w_prime, "running"), "τ = 372·e^(−0.02·D) + 102（跑步擬合）"
        wb = {"model": wmodel, "values": vals, "label": lab, "badge": None if wmodel == "wko5" else "推估",
              "w_prime": w_prime}
    zs = zones_json(cp)
    # SP-228: the athlete's shift of the walk–run curves (0 = the default curve)
    rw_shift = float(getattr(grade_re, "rw_shift", 0.0) or 0.0)
    out_segs = []
    cum = 0.0
    stops = opts.get("stops") or []
    for i, (s, r) in enumerate(zip(segs, rows)):
        cum += r["t"]
        notes = []
        # walk / either / run from grade × the predicted speed (SP-226): a label only, the time is
        # already solved; a manual course has only its net grade, so no label there
        gait = RW.gait(s["grade"], r["v"], rw_shift) if gpx else None
        walk = RW.walk_label(gait)
        if walk:
            notes.append(walk)
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
                                     "z_start", "z_end", "z_mean", "z_max", "cls", "cls_label", "climb_no")},
            "walk": walk, "gait": gait,
            "M": s["M"], "power": r["P"], "pct_cp": r["P"] / cp, "zone": z["id"] if z else "1A 以下",
            "speed_ms": v, "pace_s_per_km": _pace(v), "gap_pace_s_per_km": _pace(v * gf) if trail else None,
            "vert_m_per_h": v * s["grade"] * 3600.0 if abs(s["grade"]) >= 0.15 else None,
            "t": r["t"], "cum_s": cum, "eta": _clock(opts.get("start_time"), cum + _stops_before(stops, s["end_km"])),
            "capped": r["capped"], "locked": r.get("locked", False), "notes": notes,
            "badge": None if (v2_primary and trusted) else "推估", "trusted": trusted, "hint": HINT_30S,
            **_heat_fields(seg_heat[i] if seg_heat else None, env["to"], z_ref, s.get("z_mean")),
        })
    if wb:
        for sg, val in zip(out_segs, wb["values"]):
            sg["wbal_j"] = val
    heat_accl = None
    if hacc:
        for sg in out_segs:
            sg["heat_eff_pct"] = max(0.0, sg["heat_pct"] * (1.0 - hacc["a"] * hacc["s"]))
        hb = _heat_band(out_segs, [r["t"] for r in rows], env["from"]["heat_penalty_pct"], hacc)
        heat_accl = {k: hacc[k] for k in ("mode", "s", "s_from", "a", "source", "badge", "a_literature",
                                          "a_reason", "a_supported")}
        heat_accl.update(band=hb, time_s={k: res["T"] * hb[k]["time_ratio"] for k in ("low", "center", "high")})

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
    if gpx and not v2_primary and hr_est is None:
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
        if hr_est is not None:
            crosscheck["power_envelope"] = {"time_s": t_c, "power": p_c, "method": "功率能力（CP/Riegel，僅供對照）"}
            warnings.append(f"越野整場移動時間用心率配速模型（推估）：全力心率 {hr_est['x_star']:.0%} LTHR"
                            f"（{hr_est['x_race_source']}）"
                            + (f"，熱 −{hr_est['heat_shift']:.1%}（你的 β）" if hr_est.get("heat_beta") and hr_est["heat_shift"] > 0 else "")
                            + f"，effort km {hr_est['eff_km']:.1f}，耐久每小時 −"
                            f"{(hr_est['delta'] or 0):.1%}（1 小時後）；功率只當參考")
            if hr_est.get("delta_warning"):
                warnings.append(hr_est["delta_warning"])
            fd = hr_est.get("fade")
            if fd and res.get("faded"):
                # the shape as measured (it can rise: the athlete's data decides, not a population shape)
                seq = " / ".join(f"{m:.0%}" for _h, m in fd["points"][1:])
                warnings.append(_("分段時間照你自己的速度變化分配（推估）：用了 {n} 次 ≥ {h:g} 小時的越野跑，同坡度、同心率下，"
                                  "第 2 到第 {k} 小時的速度約是第 1 小時的 {seq}，之後維持最後一個值。整場時間不變，"
                                  "只改各段怎麼分：變慢的話，前段的 ETA 提早、後段每段變長",
                                  n=fd["n_runs"], h=fd["min_run_h"], k=len(fd["points"]), seq=seq))
            elif fd:
                warnings.append(_("分段維持平均分配：後段變慢要用你自己至少 {m} 次 ≥ {h:g} 小時的越野跑來估"
                                  "（目前 {n} 次能用，共 {a} 次 ≥ {h:g} 小時）",
                                  m=fd["min_runs"], h=fd["min_run_h"], n=fd["n_runs"], a=fd["n_long"]))
    summary = {"time_s": T, "power": p_bar, "power_train": p_train, "pct_cp": p_bar / cp, "w_per_kg": p_bar / weight,
               "pace_s_per_km": T / km, "km": km, "gain_m": course["totals"].get("gain_m"),
               "loss_m": course["totals"].get("loss_m"), "M": mbar,
               "total_method": "trail_hr" if hr_est is not None else "v2" if v2_primary else "v1",
               "trail_hr": hr_est,
               # SP-222: whether the segment times carry the athlete's own fade
               "fade": {"applied": bool(res.get("faded")), "n_runs": (hr_est.get("fade") or {}).get("n_runs"),
                        "badge": "推估"} if hr_est is not None and hr_est.get("fade") else None,
               # moving time (T, the validated target) + the predicted non-moving time, shown apart
               "nonmoving": (hr_est or {}).get("nonmoving"),
               "time_total_s": T + hr_est["nonmoving"]["total_s"] if hr_est and hr_est.get("nonmoving") else None,
               "time_total_range_s": [T + hr_est["nonmoving"]["p25_s"], T + hr_est["nonmoving"]["p75_s"]]
               if hr_est and hr_est.get("nonmoving") else None,
               "category": cat, "mode": mode, "effort_target": f_target if mode == "auto" else None,
               "finish_eta": _clock(opts.get("start_time"), T + _stops_before(stops, km + 1)),
               "stops_s": _stops_before(stops, km + 1), "badge": None if v2_primary else "推估",
               "alpha_used": alpha_used, "sigma": sigma, "beta": beta, "damage": dmg, "hr_first": hr_first,
               "cp2": cp2, "tech": grade_re.tech_factor() if trail and hasattr(grade_re, "tech_factor") else None,
               # SP-250: whether the 路況 choice is offered (available) and which one was used
               "surface": {"available": True, "used": surface, "total_from_hr": hr_est is not None,
                           "n": (grade_re.tech_surface or {}).get("n"), "min_n": (grade_re.tech_surface or {}).get("min_n"),
                           "badge": "推估"}
               if trail and getattr(grade_re, "surface_split", False) else None,
               "strategy": skind, "strategy_amount": amount, "alpha": alpha, "heat": heat_info,
               "runwalk": {"shift": rw_shift, "personal": bool((getattr(grade_re, "runwalk", None) or {}).get("personal"))},
               "heat_accl": heat_accl}
    heat_profile = _heat_profile(heat_rows, start_dt, out_segs, stops) if heat_info["mode"] == "hourly" else None
    return {"type": kind, "summary": summary, "effort": eff, "segments": out_segs, "target": target,
            "heat_profile": heat_profile,
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


def _plan_hike_v1(*, v1: dict, course: dict, hike_speed, inp: dict, opts: dict, validated: dict) -> dict:
    """百岳 three modes (§4.4): the "power" is a walking-speed multiple λ_h
    (1.0 = your usual pace). Time A → λ_h; B λ_h → time; C λ_h = 1. The
    Tobler-shrunk HikeSpeed path, kept only for when the capacity model
    cannot be fitted (no running data)."""
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
               "acclimatisation": accl, "moving_ratio": ratio, "badge": None if v2_primary else "推估",
               # racepower-v2.md §8: Hadley only on running segments → 百岳 keeps one heat value
               "heat": {"mode": "single", "passes": 0, "converged": None, "delta_s": None, "outside": 0,
                        "reason": "百岳用單一溫度（逐時熱修正只用在跑步）", "badge": None}}
    return {"type": "baiyue", "summary": summary, "effort": he, "segments": out, "days": days,
            "crosscheck": cross, "fatigue": fat, "profile": course.get("profile"), "wpts": course.get("wpts"),
            "course_totals": course["totals"], "course_warnings": course.get("warnings") or [],
            "warnings": warnings + (v1.get("warnings") or []), "validated": validated,
            "compare": {"auto": {"time_s": t1}}}


# ---- 百岳 on the athlete's walking capacity (baiyue-from-running.md §3.7, §5.2) --

TRIP_KINDS = ("group", "solo")
AMS_TOP_M = 2500.0          # 高山症提示: WMS guideline (Luks 2019) — symptoms start above ~2500 m
LIMITS_NOTE = ("沒有背 10–15 kg、每天 6–10 h、連走多天的跑步資料；背負與多日效應靠公式（誤差約 ±15 %，"
               "Looney 2022、Weyand 2021），所以帶比越野寬")
AMS_NOTE = "高山症會讓速度與行程失準，出現症狀以下撤為先"


def _cap_band_factor(cap) -> float:
    """「能力上限」(≥ 0.95·LTHR, ≤ 3 h): β ≈ 0 means the HR band does not
    predict the speed (§2.2 finding 1), so the ceiling is the athlete's own
    faster quarter: exp(p75 − mean) of the steep-window residuals (推估)."""
    spread = cap.basis.get("resid_p75_minus_mean")
    return math.exp(max(0.0, spread)) if spread is not None else 1.0


def plan_hike(*, v1: dict, course: dict, hike_speed, inp: dict, opts: dict, validated: dict,
              capacity=None) -> dict:
    """百岳 on the athlete's own walking capacity (B8): per segment
    v = min(v_flat, v₀·e^δ) / v_down × A(z) × f_time(h) × f_day(n) × Hᵢ with
    that day's pack, the terrain η and the lapse-rate temperature; the
    capacity time sits beside the group-pace time (past group days' EP/h).
    Without a capacity model (no running data) the old HikeSpeed path runs."""
    if capacity is None:
        return _plan_hike_v1(v1=v1, course=course, hike_speed=hike_speed, inp=inp, opts=opts, validated=validated)
    from backend.engine import heat as HT
    from backend.engine.racepower import capacity as CAP
    from backend.engine.racepower import course as CO
    cap = capacity
    used, env, r1 = v1["used"], v1["env"], v1["result"]
    weight = used["weight"]["value"]
    mode = opts.get("mode") or "auto"
    warnings: list[str] = []
    gpx = course.get("source") == "gpx"
    accl_in = opts.get("acclimatisation") or "unacclimatised"
    accl = "acclimatised" if accl_in == "acclimatised" else "unacclimatised"
    to = {x: env["to"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    kind = opts.get("trip_kind") if opts.get("trip_kind") in TRIP_KINDS else CAP.TRIP_KIND_DEFAULT
    band_id = opts.get("hr_band") if opts.get("hr_band") in CAP.HR_BANDS else "aet"
    hk = inp.get("hiking") or {}
    hdays = hk.get("days") or []
    group_days = [d for d in hdays if not d.get("solo")]
    solo_days = [d for d in hdays if d.get("solo")]
    ratio_solo, ratio_solo_src = HK.moving_ratio(opts.get("moving_rows") or [])
    ratio_grp, ratio_grp_src = HK.moving_ratio(opts.get("moving_rows_group") or [])
    ratio, ratio_src = (ratio_grp, ratio_grp_src) if kind == "group" else (ratio_solo, ratio_solo_src)
    v2_primary = bool(validated.get("hike_capacity")) and gpx
    hacc = heat_acclimation(opts)
    heat_status = {"scale": HT.scale(hacc["s"], hacc["a"]), "badge": "推估"} if hacc else None
    z0 = opts.get("heat_ref_alt_m") if opts.get("heat_ref_alt_m") is not None else to["altitude_m"]
    band_f = _cap_band_factor(cap) if band_id == "cap" else 1.0
    q = _quantiles([d["ep_per_h"] for d in hdays])
    cuts = DF.hike_cuts(q[0], q[1], q[2], q[3]) if q else [0.8, 1.0, 1.15, 1.3]
    n_days = len(r1.get("days") or []) or 1
    pack_by_day = [float(x) for x in (opts.get("pack_kg_by_day") or []) if x is not None]
    pack1 = opts.get("pack_kg")
    stops = opts.get("stops") or []

    if gpx:
        segs = CO.cut_at([dict(s) for s in course["segments"]], opts.get("day_splits_km") or [])
        terrain = {int(k_): v for k_, v in (opts.get("terrain") or {}).items()}
        n_days = max(s.get("day", 1) for s in segs)
    else:
        segs = []
    if pack1 is None:
        pack1 = CAP.pack_default(weight, n_days)
    pack_src = "手動" if opts.get("pack_kg") is not None else \
        f"預設 {CAP.pack_default_text(weight)}"

    def load_of(n):
        if len(pack_by_day) >= n:
            return pack_by_day[n - 1]
        return CAP.day_pack(pack1, n)
    rows = []
    day, h = 0, 0.0
    for s in segs:
        n = int(s.get("day") or 1)
        if n != day:
            day, h = n, 0.0
        s["terrain"] = terrain.get(s["i"], "normal")
        eta = HK.TERRAIN_ETA.get(s["terrain"], 1.0)
        L = load_of(n)
        z = s.get("z_mean")
        t_c = ENV.segment_temp(to["temp_c"], z0, z)
        ht = ENV.heat_term(t_c, to["rh_pct"], heat_status)
        v_base = cap.v(s["grade"], L, eta, z, h, n, accl)
        v = v_base * ht["H"] * band_f
        t = s["dist_m"] / v if v > 0 else math.inf
        sp = cap.sigma_parts(L, z, h, n)
        flags = []
        lo, hi = cap.pack_range
        if L > hi + 0.5:
            flags.append("背負超出資料")
        if s["grade"] >= CAP.STEEP_MIN_G and cap.data_n(s["grade"]) < SHRINK_N_TRUST:
            flags.append("坡度箱 n < 30")
        if z is not None and z > cap.z_range[1] + 1:
            flags.append("海拔超出資料")
        rows.append({"s": s, "v": v, "t": t, "L": L, "eta": eta, "A": cap.A(z, accl), "f_time": cap.f_time(h),
                     "f_day": cap.f_day(n), "H": ht["H"], "temp_c": t_c, "heat_pct": ht["penalty_pct"],
                     "heat_eff_pct": ht["penalty_eff_pct"], "sigma": sp, "flags": flags, "h": h})
        h += t / 3600.0
    ep = course["totals"]["km"] + course["totals"]["gain_m"] / 100.0
    if gpx:
        t1 = sum(r["t"] for r in rows)
    else:
        t1 = r1["time_s"]
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
    # uncertainty (§3.8): each component time-weighted over the segments, the
    # components added in quadrature (independence is 推估)
    if rows:
        tt = sum(r["t"] for r in rows)
        comp = {k: sum(r["t"] * r["sigma"][k] for r in rows) / tt for k in ("pack", "alt", "time", "day")}
    else:
        comp = {"pack": CAP.SIGMA_PACK, "alt": 0.0, "time": 0.0, "day": CAP.SIGMA_DAY * max(0, n_days - 1)}
    comp["model"] = cap.sigma_loo
    sigma = math.sqrt(sum(v * v for v in comp.values()))
    bnd = CAP.band(T, sigma)
    tot2 = sum(v * v for v in comp.values()) or 1.0
    bnd["shares"] = {k: v * v / tot2 for k, v in comp.items()}
    bnd["components"] = comp
    grp = CAP.group_time(ep, group_days)
    out = []
    cum = 0.0
    day, day_start = 1, 0.0
    for r in rows:
        s = r["s"]
        t = r["t"] / lam
        if s.get("day", 1) != day:
            day, day_start = s["day"], cum
        cum += t
        v = s["dist_m"] / t
        clock = _clock(opts.get("start_time"), (cum - day_start) / ratio + _stops_before(stops, s["end_km"]))
        sig = math.sqrt(cap.sigma_loo ** 2 + sum(x * x for x in r["sigma"].values()))
        out.append({**{x: s.get(x) for x in ("i", "start_km", "end_km", "dist_m", "gain_m", "loss_m", "grade",
                                             "max_grade", "z_mean", "z_max", "cls", "cls_label", "walk", "day")},
                    "terrain": s["terrain"], "eta_factor": r["eta"], "f_day": r["f_day"], "f_time": r["f_time"],
                    "A": r["A"], "H": r["H"], "pack_kg": r["L"], "temp_c": r["temp_c"], "rh_pct": to["rh_pct"],
                    "heat_pct": r["heat_pct"], "heat_eff_pct": r["heat_eff_pct"], "sigma": sig,
                    "extrapolated": r["flags"], "speed_kmh": v * 3.6, "pace_s_per_km": _pace(v),
                    "vert_m_per_h": v * s["grade"] * 3600.0 if s["grade"] > 0.05 else None,
                    "t": t, "cum_s": cum, "eta": clock, "badge": None if v2_primary else "推估"})
    days = []
    if gpx:
        for n in sorted({s.get("day", 1) for s in out}):
            ss = [s for s in out if s.get("day", 1) == n]
            km = sum(s["dist_m"] for s in ss) / 1000.0
            gain = sum(s["gain_m"] or 0 for s in ss)
            loss = sum(s["loss_m"] or 0 for s in ss)
            hh = sum(s["t"] for s in ss) / 3600.0
            L = load_of(n)
            kcal = PR.yamamoto_cc(hh, km, gain, loss) * (weight + L)
            g_day = CAP.group_time(km + gain / 100.0, group_days)
            days.append({"day": n, "km": km, "gain_m": gain, "loss_m": loss, "moving_h": hh,
                         "clock_h": hh / ratio, "kcal": kcal, "water_ml": [0.7 * kcal, 0.8 * kcal], "pack_kg": L,
                         "group_h": g_day["p50_s"] / 3600.0 if g_day else None,
                         "group_h_range": [g_day["p25_s"] / 3600.0, g_day["p75_s"] / 3600.0] if g_day else None})
    else:
        for d in r1["days"]:
            mh = d["moving_h"] / lam
            days.append({"day": d["day"], "km": d["km"], "gain_m": d["gain_m"], "loss_m": d["loss_m"],
                         "moving_h": mh, "clock_h": mh / ratio, "kcal": d["kcal"], "water_ml": d["water_ml"],
                         "pack_kg": load_of(d["day"]), "group_h": None, "group_h_range": None})
    km = course["totals"]["km"]
    gain = course["totals"]["gain_m"]
    kcal = sum(d["kcal"] for d in days)
    cross = {"v1": {"time_s": r1["time_s"], "method": "v1 EP/h"},
             "naismith_s": HK.naismith_h(km, gain) * 3600.0, "ep": ep,
             "tobler_s": ep / HK.tobler_eph(km, gain, course["totals"].get("loss_m")) * 3600.0}
    if gpx:
        cross["langmuir_s"] = HK.langmuir_h(segs) * 3600.0
        cross["hike_speed_s"] = sum(s["dist_m"] / max(1e-6, hike_speed.v(s["grade"])) for s in segs) if hike_speed else None
    if solo_days and (inp.get("hiking") or {}).get("eph"):
        cross["solo_eph_s"] = ep / inp["hiking"]["eph"]["median"] * 3600.0
    # ---- warnings ------------------------------------------------------------
    bs = cap.basis
    if accl_in == "partial":
        warnings.append("部分適應沒有定量研究，已改用未適應（個人海拔斜率本身就是上山第 1–2 天的未適應狀態）")
    if accl == "acclimatised":
        warnings.append("已適應：個人斜率 × Bassett 1999 已適應 ÷ 未適應的比（推估）")
    an = altitude_note(segs, accl) if gpx else None
    if an:
        warnings.append(an)
    top = max((s.get("z_max") or s.get("z_mean") or 0 for s in segs), default=to["altitude_m"] or 0)
    if top >= AMS_TOP_M:
        warnings.append(AMS_NOTE)
    warnings.append(LIMITS_NOTE)
    if n_days > 1:
        warnings.append("多日疲勞沒有研究與足夠個人資料（心率–速度斜率 β 不可靠），每天都用 1.0")
    base_txt = (f"以你的越野走路窗與百岳心率窗為主（{bs.get('trail_windows', 0) + bs.get('hike_windows', 0)} 窗、"
                f"{bs.get('trail_activities', 0)} 次越野 + {bs.get('hike_trips', 0)} 趟登山）")
    warnings.append(base_txt + ("，回測通過" if v2_primary else "，待回測（推估）"))
    if band_id == "cap":
        warnings.append(f"能力上限：你在陡坡窗較快的四分之一（× {band_f:.2f}，推估），只適合 ≤ {CAP.CAP_BAND_MAX_H:g} h")
        if T / 3600.0 / max(1, n_days) > CAP.CAP_BAND_MAX_H:
            warnings.append(f"每天超過 {CAP.CAP_BAND_MAX_H:g} h：能力上限撐不住，建議用 AeT")
    if not (cap.beta or {}).get("reliable"):
        warnings.append("心率帶只有「AeT」與「能力上限」兩檔：處理心率落後後，陡坡速度仍不隨心率帶改變（β ≈ 0）")
    if grp is None:
        warnings.append(f"跟團時間需要 ≥ 3 天過去的跟團紀錄（目前 {len(group_days)} 天）")
    warnings.append(f"時鐘時間 = 移動時間 ÷ {ratio:.2f}（{ratio_src}）")
    if hacc:
        warnings.append(f"熱：每段溫度由 {to['temp_c']:.1f} °C（{z0:.0f} m）以 0.0065 K/m 遞減率推算；"
                        f"熱適應 S {hacc['s']:.0%}（{hacc['source']}，推估）")
    if pack_src.startswith("預設"):
        warnings.append(f"背負{pack_src}，之後每天 −{CAP.daily_drop():g} kg（糧食，推估）")
    main = "group" if (kind == "group" and grp) else "capacity"
    summary = {"time_s": T, "capacity_time_s": T, "clock_s": T / ratio + _stops_before(stops, km + 1),
               "group_time_s": ({"p25": grp["p25_s"], "p50": grp["p50_s"], "p75": grp["p75_s"], "n": grp["n"],
                                 "eph": grp["eph"], "clock_p50": grp["p50_s"] / ratio_grp} if grp else None),
               "main": main, "trip_kind": kind, "hr_band": band_id, "band": bnd,
               "speed_factor": lam, "km": km, "gain_m": gain, "loss_m": course["totals"].get("loss_m"),
               "M": (sum(r["t"] * r["A"] for r in rows) / sum(r["t"] for r in rows)) if rows else r1.get("M"),
               "pack_kg": pack1, "pack_src": pack_src, "pack_by_day": [load_of(n) for n in range(1, n_days + 1)],
               "eph_personal": ep / (T / 3600.0) if T else None, "ep_per_h": ep / (T / 3600.0) if T else None,
               "kcal": kcal, "water_ml": [0.7 * kcal, 0.8 * kcal], "hr_cap": used.get("aet", {}).get("value"),
               "total_method": "capacity", "category": "hike", "mode": mode, "acclimatisation": accl,
               "moving_ratio": ratio, "moving_ratio_group": ratio_grp, "moving_ratio_solo": ratio_solo,
               "badge": None if v2_primary else "推估", "days": n_days,
               "heat": {"mode": "lapse", "passes": 0, "converged": None, "delta_s": None, "outside": 0,
                        "reason": "百岳：登山口／測站溫度依遞減率推算到每段", "badge": "推估", "ref_alt_m": z0},
               "heat_accl": ({k: hacc[k] for k in ("mode", "s", "s_from", "a", "source", "badge", "a_literature",
                                                   "a_reason", "a_supported")} if hacc else None)}
    capj = cap.to_json()
    cur = ((cap.alpha.get("diagnostics") or {}).get("versions") or {}).get("current") or {}
    capj["alt_current"] = cur.get("pct_per_km")
    capj["level"] = (cap.delta.get("level") or {}).get("d")
    capj["alpha"] = {k: v for k, v in capj["alpha"].items() if k != "diagnostics"}
    capj.pop("bins", None)
    return {"type": "baiyue", "summary": summary, "effort": he, "segments": out, "days": days,
            "crosscheck": cross, "fatigue": {"factors": {}, "warning": None}, "capacity": capj,
            "profile": course.get("profile"), "wpts": course.get("wpts"),
            "course_totals": course["totals"], "course_warnings": course.get("warnings") or [],
            "warnings": warnings + [w for w in (v1.get("warnings") or []) if "Tobler" not in w],
            "validated": validated, "compare": {"auto": {"time_s": t1}}}


SHRINK_N_TRUST = 30
