"""
Executable targets per course segment for trail and 百岳 plans — which
number to run each climb / descent / flat by (docs/research/
vo2max-gate-and-trail-metric.md §2.3, engine/target_policy.py):

    runnable climb 3–8 %      power range (W, % CP); HR cap secondary
                              (Stryd ≈ fixed metabolic load on 0–8 %, van Rassel 2026)
    steep / walked climb      HR cap + target VAM (m/h) + segment time
                              (> 8 % power under-reads; VAM is the climbing result, UA)
    descent                   no power / HR target: time and pace as a reference,
                              「控制、安全」 (Stryd under-reads the eccentric load, Kipp 2023)
    flat / runnable           power (HR when there is no CP)
    百岳 (pack)                HR ≤ AeT + VAM + segment / day time; never pace

Road plans keep pace / power and get no targets here. Pure functions on
the /plan payload.
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import _

RUN_CLIMB = (0.03, 0.08)
FLAT = 0.03
DESCENT = -0.03
POWER_BAND = 0.03               # ± 3 % around the segment power (the COROS step band, planner.coros_steps)
LONG_RACE_H = 3.0               # longer: HR cap at AeT (「長距離壓在 AeT 附近」, §2.3 推估); else LTHR
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
KIND_LABEL = {"run_climb": "可跑的爬坡", "steep_climb": "陡坡（走）", "descent": "下坡", "flat": "平路／可跑"}
SRC = {"run_climb": "3–8 % 坡：Stryd 功率 ≈ 固定代謝負荷（van Rassel 2026）；心率只當上限",
       "steep_climb": "> 8 %：功率低估，改看心率上限與 VAM（Uphill Athlete）",
       "descent": "下坡：功率和心率都低估離心負荷（Kipp 2023；Gravina-Cognetti），看技術與安全",
       "flat": "平路與可跑段：功率（沒有 CP 時看心率）",
       "hike": "百岳揹重：心率 ≤ AeT（Uphill Athlete）＋ VAM；配速受地形與背負影響，不當目標"}


def mark(n: int) -> str:
    return CIRCLED[n - 1] if 1 <= n <= len(CIRCLED) else f"({n})"


def kind_of(seg: dict) -> str:
    g = float(seg.get("grade") or 0.0)
    if g <= DESCENT:
        return "descent"
    if g > RUN_CLIMB[1] or (g >= RUN_CLIMB[0] and seg.get("walk")):
        return "steep_climb"
    if g >= RUN_CLIMB[0]:
        return "run_climb"
    return "flat"


def vam(seg: dict) -> Optional[float]:
    t, gain = float(seg.get("t") or 0.0), float(seg.get("gain_m") or 0.0)
    return gain / t * 3600.0 if t > 0 and gain > 0 else None


def hr_cap(plan: dict, aet: Optional[float], lthr: Optional[float]) -> tuple[Optional[float], str]:
    """The race's HR cap: the trail HR model's race HR (x × LTHR) when there
    is one, else LTHR up to 3 h and AeT beyond (推估); 百岳: AeT."""
    s = plan.get("summary") or {}
    if plan.get("type") == "baiyue":
        cap = s.get("hr_cap") or aet
        return cap, "AeT"
    th = s.get("trail_hr") or {}
    if th.get("x") and lthr:
        return th["x"] * lthr, _("越野心率模型的比賽心率（{x:.0%} LTHR）", x=th['x'])
    hours = float(s.get("time_s") or 0.0) / 3600.0
    if hours > LONG_RACE_H and aet:
        return aet, _("AeT（> 3 h 壓在 AeT 附近，推估）")
    if lthr:
        return lthr, "LTHR"
    return aet, "AeT" if aet else ""


def fuel_summary(plan: dict, seg: dict) -> str:
    """The fuelling in one segment, short: the fuel points counted (「吃 3 次
    （每次約 25 g 碳水）」), station / start rows as they are."""
    ev = [e for e in ((plan.get("fuel") or {}).get("schedule") or [])
          if e.get("seg") == seg.get("i") and (e.get("day") or 1) == (seg.get("day") or 1)]
    if not ev:
        return seg.get("fuel_action") or ""
    parts = [e["action"] for e in ev if e["kind"] == "start"]
    eats = [e for e in ev if e["kind"] == "fuel"]
    if eats:
        dose = eats[0].get("cho_g")
        txt = _("行動糧") if plan.get("type") == "baiyue" else _("吃")
        parts.append(_("{what} {n} 次", what=txt, n=len(eats)) + (_("（每次約 {g:.0f} g 碳水）", g=dose) if dose else ""))
    parts += [e["action"] for e in ev if e["kind"] == "aid"]
    return "；".join(parts)


def _walked(seg: dict, k: str) -> bool:
    return k == "steep_climb" or bool(seg.get("walk")) or any("走" in str(n) for n in seg.get("notes") or [])


def chart_rows(plan: dict, *, aet: Optional[float] = None, lthr: Optional[float] = None) -> list[dict]:
    """One row per segment for the race calculator's main chart and its table, every
    plan type: the pace, power and heart-rate target that segment is run by, null where
    that measure is not a valid target there —
      power  road: every segment; trail: flat / runnable climbs only (Stryd ≈ metabolic
             load on 0–8 %, van Rassel 2026; > 8 % under-reads, descents too, Kipp 2023);
             百岳: none
      hr     the race cap (hr_cap: LTHR ≤ 3 h, AeT beyond, the trail HR model's race HR;
             百岳 AeT); none on trail / 百岳 descents (控制、安全)
      pace   every segment (the model's pace, already grade / walk / technical adjusted)
    plus the split, cumulative time, ETA, walk flag and the fuelling in the segment.
    Run after plan_targets (reads each segment's `target` when there is one)."""
    kind = plan.get("type")
    hike = kind == "baiyue"
    cp = ((plan.get("used") or {}).get("cp") or {}).get("value")
    cap, cap_src = hr_cap(plan, aet, lthr)
    out = []
    for n, s in enumerate(plan.get("segments") or [], 1):
        k = kind_of(s)
        tg = s.get("target") or {}
        pace = s.get("pace_s_per_km")
        if not pace and s.get("speed_kmh"):
            pace = 3600.0 / s["speed_kmh"]
        p = s.get("power") if (not hike and cp and s.get("power")) else None
        # where a measure is not a valid target it is still shown, as a reference only (power_ref /
        # hr_ref: the table writes them grey 「參考」; the watch export never uses them)
        p_ref = None
        if kind == "trail" and k not in ("flat", "run_climb"):
            p_ref, p = p, None
        hr = cap if cap and not (k == "descent" and kind != "road") else None
        hr_ref = cap if cap and hr is None else None
        walk = _walked(s, k) and kind != "road"
        basis = tg.get("basis") if tg.get("basis") not in (None, "none") else None
        if kind == "road":
            basis = "power" if p else "pace"
        elif basis is None and k == "descent":
            basis = "safe"
        out.append({
            "n": n, "mark": mark(n), "i": s.get("i"), "day": s.get("day"),
            "start_km": s.get("start_km"), "end_km": s.get("end_km"), "dist_m": s.get("dist_m"),
            "gain_m": s.get("gain_m"), "loss_m": s.get("loss_m"), "grade": s.get("grade"),
            "kind": k, "label": tg.get("label") or KIND_LABEL[k], "basis": basis or "pace",
            "pace_s_per_km": pace, "power": p, "power_band": [p * (1 - POWER_BAND), p * (1 + POWER_BAND)] if p else None,
            "power_ref": p_ref, "hr_ref": hr_ref,
            "hr_cap": hr, "hr_cap_src": cap_src if hr else None, "walk": walk,
            "t": s.get("t"), "cum_s": s.get("cum_s"), "eta": s.get("eta"),
            "temp_c": s.get("temp_c"), "fuel": fuel_summary(plan, s), "badge": tg.get("badge") or s.get("badge"),
        })
    return out


def plan_targets(plan: dict, *, aet: Optional[float] = None, lthr: Optional[float] = None) -> Optional[list[dict]]:
    """Adds `target` {n, mark, kind, basis, chips, text, badge, src} to every
    segment of a trail / 百岳 plan and returns the list; None for road."""
    kind = plan.get("type")
    if kind not in ("trail", "baiyue"):
        return None
    segs = plan.get("segments") or []
    cp = ((plan.get("used") or {}).get("cp") or {}).get("value")
    cap, cap_src = hr_cap(plan, aet, lthr)
    hike = kind == "baiyue"
    out = []
    for n, s in enumerate(segs, 1):
        k = kind_of(s)
        chips, basis, badge = [], "none", None
        v = vam(s)
        if hike:
            if k == "descent":
                chips.append({"icon": "🛡️", "text": "控制、安全"})
            else:
                basis = "hr"
                if cap:
                    chips.append({"icon": "❤️", "text": f"心率 ≤ {cap:.0f}", "role": "main"})
                if v and k in ("steep_climb", "run_climb"):
                    chips.append({"icon": "⛰️", "text": f"VAM {v:.0f} m/h"})
            src = SRC["hike"] if k != "descent" else SRC["descent"]
        elif k == "descent":
            chips.append({"icon": "🛡️", "text": "控制、安全"})
            src = SRC["descent"]
        elif k == "steep_climb":
            basis = "hr"
            if cap:
                chips.append({"icon": "❤️", "text": f"心率 ≤ {cap:.0f}", "role": "main"})
            if v:
                chips.append({"icon": "⛰️", "text": f"VAM {v:.0f} m/h"})
            badge = "推估"
            src = SRC["steep_climb"]
        else:
            p = s.get("power")
            if p and cp:
                basis = "power"
                lo, hi = p * (1 - POWER_BAND), p * (1 + POWER_BAND)
                chips.append({"icon": "⚡", "text": f"{lo:.0f}–{hi:.0f} W（{lo / cp:.0%}–{hi / cp:.0%} CP）", "role": "main"})
                if cap:
                    chips.append({"icon": "❤️", "text": f"心率上限 {cap:.0f}"})
                if k == "run_climb" and v:
                    chips.append({"icon": "⛰️", "text": f"VAM {v:.0f} m/h"})
            elif cap:
                basis = "hr"
                chips.append({"icon": "❤️", "text": f"心率 ≤ {cap:.0f}", "role": "main"})
            src = SRC[k]
            badge = s.get("badge")
        ref = []
        if s.get("pace_s_per_km") and not hike:
            pc = int(round(s["pace_s_per_km"]))
            ref.append(f"配速參考 {pc // 60}:{pc % 60:02d} /km")
        t = {"n": n, "mark": mark(n), "kind": k, "label": KIND_LABEL[k] if not hike or k != "flat" else "平緩",
             "basis": basis, "chips": chips, "ref": ref, "badge": badge, "src": src, "vam": v,
             "hr_cap": cap, "hr_cap_src": cap_src,
             "text": " · ".join(c["text"] for c in chips) or "—"}
        s["target"] = t
        out.append({**t, "i": s.get("i"), "day": s.get("day"), "start_km": s.get("start_km"), "end_km": s.get("end_km"),
                    "gain_m": s.get("gain_m"), "loss_m": s.get("loss_m"), "grade": s.get("grade"), "t": s.get("t"),
                    "cum_s": s.get("cum_s"), "eta": s.get("eta"), "fuel_action": fuel_summary(plan, s)})
    return out
