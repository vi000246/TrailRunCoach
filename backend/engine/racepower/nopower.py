"""
The race calculator without power (SP-953; SP-950 問答 Q1-8 / Q2-2, docs/research/no-power-meter-audit.md
「拍板的規則」): the calculator follows the plan's one power rule (engine/power_state.py). Power is in effect
only in ① (a Stryd and a CP test or race in the last 42 days); in ② (the CP is stale) and ③ (no power meter)
no power model, CP, W′, RE, power targets or W/kg is used or shown:

  trail  the whole-race moving time from the athlete's trail HR pace model (planner.trail_hr_estimate —
         before SP-953 it was only reached after the power model's v1 had succeeded, so without a CP there
         was no trail plan at all); the segments get that total by the grade-adjusted pace (Minetti's
         running cost, the same GAP the page shows), then the athlete's own slope cost, place memory and
         fade when the model has them (as plan_run does with the HR total). No trail HR model yet → the
         race-results estimate below (§0.5.5 effort pace).
  road   the race-results estimate (race_estimate.py: Riegel from the newest road race) is the main
         route, split by GAP on a GPX course (evenly on a manual one).

Both are 推估. The environment (altitude, heat) moves the time as on the power plan: M per segment
(env.segment_factors), the HR model's own heat β when it applies. Target time / pace mode keeps working
(the goal against this model); the power goal mode is not offered (a body asking for it is planned as
the model's prediction). The result has the /plan shape with every power field None and
`power_off` / `model` saying which model was used and why (power_state.reason).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.engine import hints as H
from backend.engine.algorithms import minetti
from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import env as ENV
from backend.engine.racepower import planner as PL
from backend.engine.racepower import runwalk as RW
from backend.i18n import N_, _

TRAIL_HR, RACE_ESTIMATE = "trail_hr", "race_estimate"
MODEL_LABEL = {TRAIL_HR: N_("越野心率配速模型"), RACE_ESTIMATE: N_("比賽成績推估")}
LINE = {TRAIL_HR: N_("用越野心率配速模型算完賽時間與分段，不用功率：{why}"),
        "road": N_("用比賽成績推估完賽時間（Riegel），分段照坡度換算配速，不用功率：{why}"),
        "trail_est": N_("還沒有越野心率配速模型（要有越野跑紀錄），先用比賽成績推估，不用功率：{why}")}
NO_MODEL = N_("不用功率時，越野要用越野心率配速模型（還沒有越野跑紀錄）或比賽成績推估：{reason}")
NO_MODEL_ROAD = N_("不用功率時，路跑用比賽成績推估：{reason}")
SPLIT_GAP = N_("分段時間照坡度換算配速分配（Minetti 的跑步坡度成本，推估）：整場時間不變，上坡段變長、緩下坡段變短")
SPLIT_EVEN = N_("手動路線沒有坡度剖面：分段只是平均切開")


def state_of(ctx) -> Optional[dict]:
    """The power state the context reports (LiveContext.power_state, engine/power_state.py); None when the
    context has none (a test double, the intro page's fictional runner) — read as ①, as before."""
    fn = getattr(ctx, "power_state", None)
    if fn is None:
        return None
    try:
        ps = fn()
    except Exception:                       # noqa: BLE001 — unreadable: the long-standing behaviour (power)
        return None
    return ps if isinstance(ps, dict) and ps.get("state") else None


def off(ps: Optional[dict]) -> bool:
    """Power is not in effect (② or ③)."""
    return bool(ps) and not ps.get("power")


def _today(ctx) -> dt.date:
    fn = getattr(ctx, "today", None)
    if callable(fn):
        return fn()
    from backend.engine.localtime import today_local
    return today_local()


def _race_results(ctx) -> list:
    fn = getattr(ctx, "race_results", None)
    if callable(fn):
        return fn() or []
    from backend.engine import race_results as RR
    try:
        return RR.load()
    except Exception:                       # noqa: BLE001 — no store: no results
        return []


def _survey_hours(ctx) -> Optional[float]:
    fn = getattr(ctx, "survey_hours", None)
    if callable(fn):
        return fn()
    from backend.engine.race_feasibility import survey_hours
    return survey_hours()


def environment(ctx, body, course: dict) -> tuple[dict, dict]:
    """(env, used) as v1 would have them (calc.predict / v1_for): the training conditions → race day,
    a GPX course's mean elevation when no race-day altitude is given; the body weight. No CP, RE, k."""
    from backend.engine.racepower import calc as CALC
    d = ctx.inputs()
    weight = body.weight or (d.get("weight") or {}).get("value")
    used = {"weight": CALC._src(weight, _("手動") if body.weight else (d.get("weight") or {}).get("source"))}
    a = d.get("aet") or {}
    used["aet"] = CALC._src(a.get("aet"), a.get("source"))
    tc = d.get("training_conditions") or {}
    frm = {k: (getattr(body.env_from, k) if body.env_from and getattr(body.env_from, k) is not None
               else tc.get(k)) for k in ("altitude_m", "temp_c", "rh_pct")}
    to = dict(body.env_to.model_dump() if body.env_to else {})
    segs = course.get("segments") or []
    if course.get("source") == "gpx" and to.get("altitude_m") is None and segs:
        to["altitude_m"] = sum(s["z_mean"] * s["dist_m"] for s in segs) / (sum(s["dist_m"] for s in segs) or 1.0)
    return ENV.multiplier(frm, to), used


def model_line(kind: str, plan_type: str, ps: Optional[dict]) -> dict:
    """{kind, label, why, line, state}: which model the plan used and why power is not (the page's line)."""
    why = (ps or {}).get("reason") or ""
    key = TRAIL_HR if kind == TRAIL_HR else ("road" if plan_type == "road" else "trail_est")
    return {"kind": kind, "label": _(MODEL_LABEL[kind]), "why": why, "line": _(LINE[key], why=why),
            "state": (ps or {}).get("state")}


def _gap_rows(segs: list[dict], t_whole: float) -> dict:
    """The whole-race time split by the grade-adjusted pace: tᵢ ∝ distᵢ × Minetti cost(gᵢ) ÷ Mᵢ (equal
    GAP everywhere, the environment slowing its own segments). Rows as the planner's ({t, v, P})."""
    w = [float(s["dist_m"]) * minetti.grade_factor(float(s.get("grade") or 0.0)) / (s.get("M") or 1.0) for s in segs]
    tot = sum(w) or 1.0
    rows = []
    for s, x in zip(segs, w):
        t = t_whole * x / tot
        rows.append({"t": t, "v": float(s["dist_m"]) / t if t > 0 else 0.0, "P": 0.0})
    return {"rows": rows, "T": t_whole}


def plan(ctx, body, course: dict, opts: dict, ps: Optional[dict]) -> dict:
    """The road / trail plan without power (the module docstring), in /plan's shape. CalcError 400 when
    there is neither a trail HR model nor a usable race result (the reason says what to enter)."""
    from backend.engine.racepower import calc as CALC
    from backend.engine.racepower import goal as GOAL
    from backend.engine.racepower import race_estimate as RX
    kind = body.type
    trail = kind == "trail"
    env, used = environment(ctx, body, course)
    inp = ctx.inputs()
    lthr = (inp.get("aet") or {}).get("lthr")
    segs = [dict(s) for s in course["segments"]]
    gpx = course.get("source") == "gpx"
    km = float(course["totals"]["km"])
    gain = float(course["totals"].get("gain_m") or 0.0)
    mode = "time" if body.mode == "time" else "auto"
    f = float(body.effort_target or 1.0)
    stops = opts.get("stops") or []
    warnings: list = []

    th = None
    if trail:
        try:
            th = ctx.trail_hr()
        except Exception:                   # noqa: BLE001 — no model: the race-results estimate
            th = None
        if not th or not (th.get("a") or th.get("c")):
            th = None
    stop_kind = opts.get("stop_kind") or "race"

    def hr_for(hadley, ff=f):
        if th is None:
            return None
        return PL.trail_hr_estimate(th, km, gain, ff, hadley=hadley, lthr=lthr, stops=stops, stop_kind=stop_kind,
                                    terrain=body.terrain_difficulty)

    hr_est = hr_for(env["to"].get("heat_index_sum_f"))
    est = None
    if hr_est is None:
        est = RX.estimate("trail" if trail else "road", km, gain, _race_results(ctx), _today(ctx),
                          _survey_hours(ctx), power_state=ps)
        if not est.get("available"):
            raise CALC.CalcError(400, _(NO_MODEL if trail else NO_MODEL_ROAD, reason=est.get("reason") or ""))
    method = TRAIL_HR if hr_est is not None else RACE_ESTIMATE

    # the environment per segment, as plan_run (altitude by each segment's height on a GPX course)
    accl = "acclimatised"
    frm = {x: env["from"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    to = {x: env["to"][x] for x in ("altitude_m", "temp_c", "rh_pct")}
    zs = [s["z_mean"] for s in segs] if gpx else [to["altitude_m"]] * len(segs)
    hacc = PL.heat_acclimation(opts)
    hs_pair = (hacc["s_from"], hacc["s"]) if hacc else None

    def factors(heat=None):
        if gpx or heat is not None or hs_pair is not None:
            if hacc:
                return ENV.segment_factors(zs, frm, to, accl, heat, heat_s=hs_pair, a=hacc["a"])
            return ENV.segment_factors(zs, frm, to, accl, heat, heat_s=hs_pair)
        return [env["M"]] * len(segs)

    dsum = sum(s["dist_m"] for s in segs) or 1.0
    # the HR model is at its own heat (β): only the altitude part of M applies to its time
    m_alt = ENV.segment_factors(zs, frm, to, accl, [(frm["temp_c"], frm["rh_pct"])] * len(segs))
    mbar_alt = sum(m * s["dist_m"] for m, s in zip(m_alt, segs)) / dsum

    def target_time() -> Optional[float]:
        t = body.target_time_s
        if not t and body.target_pace_s_per_km:
            t = body.target_pace_s_per_km * km
        return float(t) if t else None

    if mode == "time" and not target_time():
        raise CALC.CalcError(400, _("模式「目標時間」需要時間或配速"))

    def model_time(mbar: float, hr) -> float:
        """The model's own whole-race moving time at this M (the goal compares against it)."""
        if hr is not None:
            return hr["time_s"] / (mbar_alt if hr.get("heat_beta") else mbar)
        return float(est["time_s"]) / f / mbar

    def solve_all(ms: list[float], hr) -> dict:
        for s, m in zip(segs, ms):
            s["M"] = m
        mbar = sum(s["M"] * s["dist_m"] for s in segs) / dsum
        t_whole = target_time() if mode == "time" else model_time(mbar, hr)
        res = _gap_rows(segs, t_whole)
        if hr is not None and mode == "auto":
            if gpx:
                res = PL._slope_split(res, segs, hr.get("slope_cost"), mbar, PL._kind_surface(opts, hr))
                res = PL._place_memory(res, opts.get("memory"), (hr.get("terrain") or {}).get("mult") or 1.0)
            res = PL._fade(res, hr.get("fade"))
        return {"res": res, "mbar": mbar, "t_whole": t_whole}

    # ---- per-segment, time-of-day heat (as plan_run) ----
    heat_rows, start_dt, heat_reason = PL._heat_context(opts)
    wx_secs = PL._weather_sections(opts) if heat_rows else []
    z_ref = opts.get("heat_ref_alt_m") if gpx else None
    one_to = {"temp_c": to["temp_c"], "rh_pct": to["rh_pct"]}

    def heat_of(hs):
        out = []
        for h, sg in zip(hs, segs):
            x = h if h is not None else PL._at_height(one_to, z_ref, sg.get("z_mean"))
            out.append((x["temp_c"], x["rh_pct"]))
        return out

    st = solve_all(factors(heat_of([None] * len(segs)) if z_ref is not None else None), hr_est)
    seg_heat = None
    heat_info = {"mode": "single", "passes": 0, "converged": None, "delta_s": None, "outside": 0,
                 "reason": heat_reason, "badge": None}
    if heat_rows:
        prev = PL._cum_times(st["res"]["rows"])
        for n in range(1, PL.HEAT_MAX_PASSES + 1):
            seg_heat = [PL._at_height(h, z_ref, sg.get("z_mean") if gpx else None)
                        for h, sg in zip(PL._segment_heat(heat_rows, start_dt, segs, st["res"]["rows"], stops, wx_secs),
                                         segs)]
            if hr_est is not None:
                from backend.engine import heat as HT
                tw = [(r["t"], HT.hadley_sum(h["temp_c"], h["rh_pct"])) for r, h in zip(st["res"]["rows"], seg_heat) if h]
                if tw:
                    hr_est = hr_for(sum(t * x for t, x in tw) / (sum(t for t, _x in tw) or 1.0)) or hr_est
            st = solve_all(factors(heat_of(seg_heat)), hr_est)
            cur = PL._cum_times(st["res"]["rows"])
            delta = max(abs(a - b) for a, b in zip(cur, prev))
            prev = cur
            heat_info.update(passes=n, delta_s=delta, converged=delta < PL.HEAT_TOL_S)
            if delta < PL.HEAT_TOL_S:
                break
        out_n = sum(1 for h in seg_heat if h is None)
        heat_info.update(mode="hourly", outside=out_n, badge="推估")
        if wx_secs:
            heat_info["points"] = len({h["wx_point"] for h in seg_heat if h and h.get("wx_point") is not None})
        if out_n:
            warnings.append(H.tag("RC12", _("{n} 段的 ETA 超出逐時預報範圍：這些段用單一溫度 {t:.1f} °C", n=out_n, t=to["temp_c"])))
    elif heat_reason and opts.get("hourly_heat", True):
        warnings.append(H.tag("RC12", _("熱修正用單一溫度 {t:.1f} °C / 濕度 {rh:.0f} %（{why}）",
                                        t=to["temp_c"], rh=to["rh_pct"], why=_(heat_reason))))
    if z_ref is not None:
        heat_info["ref_alt_m"] = z_ref
    res, mbar = st["res"], st["mbar"]
    T, rows = res["T"], res["rows"]

    # ---- the segments ----
    rw_shift = 0.0
    try:
        rw_shift = float(getattr((ctx.grade_models() or {}).get("grade_re"), "rw_shift", 0.0) or 0.0)
    except Exception:                       # noqa: BLE001 — the default walk–run curve
        rw_shift = 0.0
    out_segs, cum = [], 0.0
    for i, (s, r) in enumerate(zip(segs, rows)):
        cum += r["t"]
        v = r["v"]
        gait = RW.gait(s["grade"], v, rw_shift) if gpx else None
        walk = RW.walk_label(gait)
        gf = minetti.grade_factor(s["grade"])
        out_segs.append({
            **{x: s.get(x) for x in ("i", "start_km", "end_km", "dist_m", "gain_m", "loss_m", "grade", "max_grade",
                                     "z_start", "z_end", "z_mean", "z_max", "cls", "cls_label", "climb_no")},
            "walk": walk, "gait": gait, "gait_curve": None,
            "M": s["M"], "power": None, "pct_cp": None, "zone": None,
            "speed_ms": v, "pace_s_per_km": PL._pace(v), "gap_pace_s_per_km": PL._pace(v * gf) if trail else None,
            "tech_f": None,
            "vert_m_per_h": v * s["grade"] * 3600.0 if abs(s["grade"]) >= 0.15 else None,
            "t": r["t"], "cum_s": cum, "eta": PL._clock(opts.get("start_time"), cum + PL._stops_before(stops, s["end_km"])),
            "capped": False, "locked": False, "notes": [walk] if walk else [],
            "badge": "推估", "trusted": True, "hint": None,
            **PL._heat_fields(seg_heat[i] if seg_heat else None, env["to"], z_ref, s.get("z_mean")),
            "wx_point": (seg_heat[i] or {}).get("wx_point") if seg_heat else None,
            "memory": PL._seg_memory(res.get("memory"), i),
        })

    # ---- the method lines ----
    if hr_est is not None and mode == "auto":
        warnings.append(H.tag("RC7", _("越野整場移動時間用心率配速模型（推估）：全力心率 {x:.0%} LTHR（{src}）",
                                       x=hr_est["x_star"], src=hr_est.get("x_race_source") or "")
                              + (_("，熱 −{h:.1%}（你的 β）", h=hr_est["heat_shift"])
                                 if hr_est.get("heat_beta") and hr_est["heat_shift"] > 0 else "")
                              + _("，effort km {e:.1f}，", e=hr_est["eff_km"]) + PL.terrain_text(hr_est)
                              + PL.durability_text(hr_est)))
        if hr_est.get("delta_warning"):
            warnings.append(H.tag("RC7", hr_est["delta_warning"]))
        sc = hr_est.get("slope_cost")
        if sc and res.get("slope_split"):
            c = sc["cost"]
            warnings.append(H.tag("RC8", _("分段時間照你自己的坡度成本分配（推估）：同一筆活動裡，陡下 ×{a:.2f}、下 ×{b:.2f}、平 ×{c:.2f}、"
                                  "上 ×{d:.2f}、陡上 ×{e:.2f}（相對努力距離的平均時間；{n} 次自己步調的越野跑，資料少時往預設收縮）。"
                                  "整場時間不變，只改各段怎麼分：下坡段變長、陡上段變短",
                                  a=c[0], b=c[1], c=c[2], d=c[3], e=c[4], n=sc.get("n_runs") or 0)))
        elif gpx:
            warnings.append(H.tag("RC8", _(SPLIT_GAP)))
        if res.get("kind_split"):
            warnings.append(H.tag("RC8", PL.kind_text(hr_est["surface_kind"])))    # SP-727
        if res.get("memory"):
            warnings.append(H.tag("RC8", PL.memory_text(res["memory"])))
    elif est is not None:
        warnings.append(H.tag("RC1", est["text"]))
        if est.get("note"):
            warnings.append(H.tag("RC1", est["note"]))
        if gpx:
            warnings.append(H.tag("RC8", _(SPLIT_GAP)))
    elif gpx:
        warnings.append(H.tag("RC8", _(SPLIT_GAP)))
    if not gpx:
        warnings.append(H.tag("RC8", _(SPLIT_EVEN)))
    an = PL.altitude_note(segs, accl) if gpx else None
    if an:
        warnings.append(H.tag("RC13", an))

    # ---- the effort and the goal ----
    model_T = None
    goal = None
    if mode == "time":
        hr1 = hr_for(env["to"].get("heat_index_sum_f"), 1.0) if th is not None else None
        model_T = model_time(mbar, hr1) if hr1 is not None else float(est["time_s"]) / mbar
        goal = GOAL.check(T, model_T, "time")
        goal.update(model_power=None, model_pace_s_per_km=model_T / km if km else None)
    ef = (model_T / T) if (mode == "time" and model_T and T) else f
    effort = {"f": ef, **DF.label_of(ef), "basis": "hr" if method == TRAIL_HR else "race", "band": None,
              "cuts": [{"key": k, "label": _(n), "from": lo} for k, n, lo in DF.LABELS], "badge": "推估",
              "multiple": None, "t_lim_s": None, "power": None}

    nm_now = (hr_est or {}).get("nonmoving")
    if nm_now and (res.get("memory") or {}).get("applied"):
        from backend.engine.racepower import nonmoving as NM
        nm_now = NM.predict((th or {}).get("nonmoving"), T, stops, kind=stop_kind) or nm_now
    summary = {"time_s": T, "power": None, "power_train": None, "pct_cp": None, "w_per_kg": None,
               # SP-727: whether the segment times also carry the surface (trail dictionary)
               "kind_split": ({"applied": True, "mult": (hr_est.get("surface_kind") or {}).get("mult"),
                               "n_runs": (hr_est.get("surface_kind") or {}).get("n_runs"), "badge": "推估"}
                              if hr_est is not None and res.get("kind_split") else None),
               "slope_split": ({"applied": True, "cost": (hr_est.get("slope_cost") or {}).get("cost"),
                                "n_runs": (hr_est.get("slope_cost") or {}).get("n_runs"), "badge": "推估"}
                               if hr_est is not None and res.get("slope_split") else None),
               "pace_s_per_km": T / km if km else None, "km": km, "gain_m": course["totals"].get("gain_m"),
               "loss_m": course["totals"].get("loss_m"), "M": mbar,
               "total_method": method, "trail_hr": hr_est,
               "race_estimate": {k: est.get(k) for k in ("method", "race", "time_s", "k", "pace_s_per_km", "effort_km", "vdot")}
               if est is not None else None,
               "fade": {"applied": bool(res.get("faded")), "n_runs": (hr_est.get("fade") or {}).get("n_runs"),
                        "badge": "推估"} if hr_est is not None and hr_est.get("fade") else None,
               "nonmoving": nm_now,
               "time_total_s": T + nm_now["total_s"] if nm_now else None,
               "time_total_range_s": [T + nm_now["p25_s"], T + nm_now["p75_s"]] if nm_now else None,
               "memory": PL.memory_summary(res.get("memory")),
               "category": "trail" if trail else "road", "mode": mode, "effort_target": f if mode == "auto" else None,
               "finish_eta": PL._clock(opts.get("start_time"), T + PL._stops_before(stops, km + 1)),
               "stops_s": PL._stops_before(stops, km + 1), "badge": "推估",
               "alpha_used": None, "sigma": 0.0, "beta": None, "damage": None, "hr_first": None, "cp2": None,
               "tech": None, "tech_ground": None, "surface": None, "strategy": "even", "strategy_amount": 0.0,
               "alpha": None, "heat": heat_info,
               "runwalk": {"shift": rw_shift, "personal": False, "speed_gait": False},
               "heat_accl": None, "locks_dropped": [], "power_off": True}
    heat_profile = PL._heat_profile(heat_rows, start_dt, out_segs, stops) if heat_info["mode"] == "hourly" else None
    v1 = {"type": kind, "used": used, "env": env, "result": {"time_s": T, "distance_km": km, "scenarios": []},
          "tasks": {}, "zones": [], "warnings": []}
    return {"type": kind, "summary": summary, "effort": effort, "segments": out_segs, "target": None,
            "heat_profile": heat_profile, "goal": goal,
            "compare": {}, "crosscheck": {}, "wbal": None, "wprime_runs": [],
            "profile": course.get("profile"), "climbs": course.get("climbs"), "wpts": course.get("wpts"),
            "course_totals": course["totals"], "course_warnings": course.get("warnings") or [],
            "zones": [], "warnings": warnings, "validated": {}, "hint": None,
            "v1": v1, "used": used, "env": env,
            "power_off": True, "power_state": ps, "model": model_line(method, kind, ps)}
