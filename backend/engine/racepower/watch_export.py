"""
The race calculator's plan as a watch workout (「匯出至課表」): one step per leg of
the course, each with the target that leg is run by, in the workout editor's step
format (engine/workout_steps.py), so the existing provider push (sync/workout_targets)
sends it.

Two ways to end a step:

  lap       (default for trail / 百岳) open steps ended with the lap button. On a trail
            the watch's GPS distance drifts (switchbacks, tree cover), so a distance
            step gets out of step with the real course; the athlete presses lap at a
            landmark the step names instead: 「→ 補給站 2 · 約 1:35 · 爬 640 m」.
            Legs end at aid stations (always), day ends (百岳), and the top / bottom of
            each long climb / descent; ≤ LAP_MAX steps.
  distance  (default for road) distance steps per segment; GPS is fine on a road.
            ≤ COROS_MAX steps (workout_steps.COROS_MAX_STEPS; COROS' own limit 未驗證).

Targets per leg (seg_targets.chart_rows): power ± 3 % where power is valid (road;
trail flat / runnable 3–8 %), the HR cap on steep / walked climbs (and wherever there
is no power), nothing on trail descents (控制、安全), pace on a road with no CP. A trail /
百岳 leg that is mostly steep / walked climbing (seg_targets.kind_of steep_climb) never
gets the estimated power or a pace: the HR cap, else no target (自由). When a leg merges
segments of different kinds, the kind with the most time decides; merged power is
time-weighted. Pure functions on the /plan payload.

The steps go to the 課表 (api/racepower.py /export/plan → plan_store.upsert_external) and
reach the watch with the plan's own push.
"""
from __future__ import annotations

from typing import Optional

from backend.engine import workout_steps as WS

LAP_MAX = 25                     # 推估: a lap-button workout the athlete can follow (owner: ~20–30)
COROS_MAX = WS.COROS_MAX_STEPS
MIN_LEG_M = 300.0                # 推估: shorter legs are merged into a neighbour
MIN_DIST_STEP_M = 50             # workout_steps' distance minimum
HR_LO_FRAC = 0.85                # 推估: a cap's watch band = 85–100 % of the cap
PACE_BAND = 0.02                 # 推估: ± 2 % around the planned pace (road, no CP)
POWER_BAND = 0.03                # the COROS power band (planner.coros_steps, seg_targets.POWER_BAND)
STOP_LABEL = {"water": "水站", "aid": "補給站", "big": "大補給站", "medical": "醫護站", "self": "補給點"}
MODES = ("lap", "distance")


def default_mode(plan_type: str) -> str:
    return "distance" if plan_type == "road" else "lap"


def _group(r: dict) -> str:
    g = float(r.get("grade") or 0.0)
    return "climb" if g >= 0.03 else "descent" if g <= -0.03 else "flat"


def _piece(r: dict) -> dict:
    t = float(r.get("t") or 0.0)
    p = r.get("power")
    return {"start_km": float(r["start_km"]), "end_km": float(r["end_km"]), "dist_m": float(r.get("dist_m") or 0.0),
            "t": t, "gain_m": float(r.get("gain_m") or 0.0), "loss_m": float(r.get("loss_m") or 0.0),
            "group": _group(r), "day": r.get("day") or 1,
            "kt": {r.get("basis") or "pace": t},                 # time per basis (the dominant one wins)
            "kd": {r.get("kind") or "": t},                      # time per segment kind (seg_targets.kind_of)
            "pt": (p * t) if p else 0.0, "ptime": t if p else 0.0,
            "hr": r.get("hr_cap"), "end": None}


def _split(pc: dict, km: float) -> tuple[dict, dict]:
    f = (km - pc["start_km"]) / max(1e-9, pc["end_km"] - pc["start_km"])
    a, b = dict(pc), dict(pc)
    a["end_km"], b["start_km"] = km, km
    for k in ("dist_m", "t", "gain_m", "loss_m", "pt", "ptime"):
        a[k], b[k] = pc[k] * f, pc[k] * (1 - f)
    for key in ("kt", "kd"):
        a[key] = {k: v * f for k, v in pc[key].items()}
        b[key] = {k: v * (1 - f) for k, v in pc[key].items()}
    a["end"] = None
    return a, b


def _merge(a: dict, b: dict) -> dict:
    kt, kd = dict(a["kt"]), dict(a["kd"])
    for k, v in b["kt"].items():
        kt[k] = kt.get(k, 0.0) + v
    for k, v in b["kd"].items():
        kd[k] = kd.get(k, 0.0) + v
    hr = [x for x in (a["hr"], b["hr"]) if x]
    return {"start_km": a["start_km"], "end_km": b["end_km"], "dist_m": a["dist_m"] + b["dist_m"],
            "t": a["t"] + b["t"], "gain_m": a["gain_m"] + b["gain_m"], "loss_m": a["loss_m"] + b["loss_m"],
            "group": a["group"] if a["t"] >= b["t"] else b["group"], "day": a["day"], "kt": kt, "kd": kd,
            "pt": a["pt"] + b["pt"], "ptime": a["ptime"] + b["ptime"], "hr": max(hr) if hr else None,
            "end": b["end"]}


def _cut_at(pieces: list[dict], km: float, end: dict) -> None:
    """Make `km` a piece boundary carrying the landmark `end` (an aid station / a day end)."""
    for j, pc in enumerate(pieces):
        if abs(pc["end_km"] - km) < 0.02:
            pc["end"] = end
            return
        if pc["start_km"] < km < pc["end_km"]:
            a, b = _split(pc, km)
            a["end"] = end
            pieces[j:j + 1] = [a, b]
            return


def _reduce(pieces: list[dict], limit: int, min_m: float, same_group_first: bool) -> list[dict]:
    """Merge neighbours (never across a landmark unless nothing else is left) until
    ≤ limit pieces and none shorter than min_m."""
    ps = list(pieces)
    if same_group_first:
        out = []
        for pc in ps:
            if out and out[-1]["end"] is None and out[-1]["group"] == pc["group"] and out[-1]["day"] == pc["day"]:
                out[-1] = _merge(out[-1], pc)
            else:
                out.append(pc)
        ps = out
    while len(ps) > 1:
        over = len(ps) > limit
        short = {j for j, pc in enumerate(ps) if pc["dist_m"] < min_m}
        if not over and not short:
            break
        best, cost = None, None
        for j in range(len(ps) - 1):
            a, b = ps[j], ps[j + 1]
            hard = a["end"] is not None or a["day"] != b["day"]
            if not over and (hard or (j not in short and j + 1 not in short)):
                continue                   # a short leg goes into its non-landmark neighbour only
            c = (a["t"] + b["t"]) * (1.0 if a["group"] == b["group"] else 1.5) * (50.0 if hard else 1.0)
            if cost is None or c < cost:
                best, cost = j, c
        if best is None:
            break
        ps[best:best + 2] = [_merge(ps[best], ps[best + 1])]
    return ps


def _hm(sec: float) -> str:
    m = int(round(sec / 60.0))
    return f"{m // 60}:{m % 60:02d}" if m >= 60 else f"{m} 分"


def _target(pc: dict, plan_type: str) -> tuple[dict, str]:
    """(workout_steps target, basis) of one leg."""
    basis = max(pc["kt"].items(), key=lambda kv: kv[1])[0] if pc["kt"] else "pace"
    kind = max(pc["kd"].items(), key=lambda kv: kv[1])[0] if pc.get("kd") else ""
    if kind == "steep_climb" and plan_type != "road":
        # a steep / walked climb: the HR cap only, never the estimated power or a pace —
        # without a cap the step is open (自由)
        if pc["hr"]:
            return {"type": "hr", "mode": "abs", "lo": round(pc["hr"] * HR_LO_FRAC), "hi": round(pc["hr"])}, "hr"
        return {"type": "none"}, "none"
    if basis == "power" and pc["ptime"] > 0:
        p = pc["pt"] / pc["ptime"]
        return {"type": "power", "mode": "abs", "lo": round(p * (1 - POWER_BAND)), "hi": round(p * (1 + POWER_BAND))}, "power"
    if basis in ("hr", "power") and pc["hr"]:
        return {"type": "hr", "mode": "abs", "lo": round(pc["hr"] * HR_LO_FRAC), "hi": round(pc["hr"])}, "hr"
    if basis == "safe" or (plan_type != "road" and pc["group"] == "descent"):
        return {"type": "none"}, "safe"
    if pc["dist_m"] > 0 and pc["t"] > 0:
        pace = pc["t"] / (pc["dist_m"] / 1000.0)
        if 120 <= pace <= 1200:
            return {"type": "pace", "mode": "abs", "lo": round(pace * (1 - PACE_BAND)),
                    "hi": round(pace * (1 + PACE_BAND))}, "pace"
    if pc["hr"]:
        return {"type": "hr", "mode": "abs", "lo": round(pc["hr"] * HR_LO_FRAC), "hi": round(pc["hr"])}, "hr"
    return {"type": "none"}, "none"


def _landmark(pc: dict, nxt: Optional[dict], last: bool) -> str:
    e = pc["end"]
    if last:
        return "終點"
    if e and e.get("kind") == "stop":
        return e["name"]
    if e and e.get("kind") == "day":
        return f"第 {e['day']} 天終點"
    if pc["group"] == "climb" and nxt is not None and nxt["group"] != "climb":
        return "坡頂"
    if pc["group"] == "descent" and nxt is not None and nxt["group"] != "descent":
        return "坡底"
    return f"{pc['end_km']:.1f} km"


def steps_for(plan: dict, rows: list[dict], *, mode: Optional[str] = None, stops: Optional[list[dict]] = None,
              day_splits_km: Optional[list[float]] = None) -> dict:
    """{"mode", "doc" (workout_steps format), "legs" [{name, start_km, end_km, t, basis, target}],
    "merged" (segments folded into fewer steps), "limit", "notes"}."""
    ptype = plan.get("type") or "road"
    mode = mode if mode in MODES else default_mode(ptype)
    rows = [r for r in rows or [] if r.get("end_km") is not None and r.get("start_km") is not None]
    pieces = [_piece(r) for r in rows]
    notes = []
    if mode == "lap":
        names: dict = {}
        for s in sorted(stops or [], key=lambda x: x.get("km") or 0.0):
            km = s.get("km")
            if km is None or not pieces or not (pieces[0]["start_km"] < km < pieces[-1]["end_km"]):
                continue
            lab = STOP_LABEL.get(s.get("type") or "aid", "補給站")
            names[lab] = names.get(lab, 0) + 1
            nm = (s.get("name") or "").strip() or f"{lab} {names[lab]}"
            _cut_at(pieces, float(km), {"kind": "stop", "name": nm[:16]})
        for d, km in enumerate(sorted(day_splits_km or []), 1):
            _cut_at(pieces, float(km), {"kind": "day", "day": d})
        limit = LAP_MAX
        legs = _reduce(pieces, limit, MIN_LEG_M, True)
    else:
        limit = COROS_MAX
        legs = _reduce(pieces, limit, MIN_DIST_STEP_M, False)
    merged = max(0, len(rows) - len(legs))
    if merged and len(rows) > limit:
        notes.append(f"路線有 {len(rows)} 段，超過手錶建議的 {limit} 步：相鄰的段合併成 {len(legs)} 步")
    items, out_legs = [], []
    for j, pc in enumerate(legs):
        tg, basis = _target(pc, ptype)
        nxt = legs[j + 1] if j + 1 < len(legs) else None
        if mode == "lap":
            climb = f" · 爬 {pc['gain_m']:.0f} m" if pc["gain_m"] >= 30 else \
                (f" · 降 {pc['loss_m']:.0f} m" if pc["loss_m"] >= 30 else "")
            day = f"D{pc['day']} " if ptype == "baiyue" and (day_splits_km or []) else ""
            name = f"{day}→ {_landmark(pc, nxt, nxt is None)} · 約 {_hm(pc['t'])}{climb}"
            dur = {"type": "open", "est": int(max(5, min(6 * 3600, round(pc["t"]))))}
        else:
            name = f"{pc['start_km']:.1f}–{pc['end_km']:.1f} km"
            dur = {"type": "distance", "value": int(max(MIN_DIST_STEP_M, round(pc["dist_m"])))}
        if basis == "safe":
            name += " 控制、安全"
        items.append({"id": f"r{j + 1}", "kind": "work", "dur": dur, "target": tg, "note": name[:WS.MAX_NOTE]})
        out_legs.append({"name": name[:WS.MAX_NOTE], "start_km": pc["start_km"], "end_km": pc["end_km"], "t": pc["t"],
                         "gain_m": pc["gain_m"], "loss_m": pc["loss_m"], "basis": basis, "target": tg})
    if mode == "lap":
        notes.append("每段按圈（lap）結束：到步驟名稱寫的地點時按一下")
    return {"mode": mode, "doc": {"v": WS.V, "origin": "user", "items": items}, "legs": out_legs,
            "merged": merged, "limit": limit, "notes": notes}


DEFAULT_IF = 0.75                # 推估: a leg with no power / HR target (descent, 自由, pace only)
IF_RANGE = (0.5, 1.15)


def tss_estimate(legs: list[dict], th: dict) -> Optional[float]:
    """The race session's planned TSS (推估) from its legs: Σ hours × IF² × 100, IF = the
    middle of the leg's power band ÷ CP, or of its HR band ÷ LTHR (hrTSS-like), else
    DEFAULT_IF. None when the legs have no time."""
    cp, lthr = th.get("cp"), th.get("lthr")
    tot = 0.0
    for lg in legs or []:
        t, tg = float(lg.get("t") or 0.0), lg.get("target") or {}
        mid = (float(tg["lo"]) + float(tg["hi"])) / 2.0 if tg.get("lo") and tg.get("hi") else None
        if mid and tg.get("type") == "power" and cp:
            f = mid / cp
        elif mid and tg.get("type") == "hr" and lthr:
            f = mid / lthr
        else:
            f = DEFAULT_IF
        f = min(IF_RANGE[1], max(IF_RANGE[0], f))
        tot += t / 3600.0 * f * f * 100.0
    return round(min(2000.0, tot), 1) if tot > 0 else None
