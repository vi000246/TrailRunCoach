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

RUN_CLIMB = (0.03, 0.08)
FLAT = 0.03
DESCENT = -0.03
POWER_BAND = 0.03               # ± 3 % around the segment power (the COROS step band, planner.coros_steps)
LONG_RACE_H = 3.0               # longer: HR cap at AeT (「長距離壓在 AeT 附近」, §2.3 推估); else LTHR
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
KIND_LABEL = {"run_climb": "可跑的爬坡", "steep_climb": "陡坡（走）", "descent": "下坡", "flat": "平路／可跑"}
SRC = {"run_climb": "3–8 % 坡：Stryd 功率 ≈ 固定代謝負荷（van Rassel 2026）；心率只當上限",
       "steep_climb": "> 8 %：功率低估，改看心率上限與 VAM（vo2max-gate-and-trail-metric.md §2.3；Uphill Athlete）",
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
        return th["x"] * lthr, f"越野心率模型的比賽心率（{th['x']:.0%} LTHR）"
    hours = float(s.get("time_s") or 0.0) / 3600.0
    if hours > LONG_RACE_H and aet:
        return aet, "AeT（> 3 h 壓在 AeT 附近，推估）"
    if lthr:
        return lthr, "LTHR"
    return aet, "AeT" if aet else ""


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
                    "cum_s": s.get("cum_s"), "eta": s.get("eta"), "fuel_action": s.get("fuel_action") or ""})
    return out
