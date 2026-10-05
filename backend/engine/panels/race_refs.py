"""
Target-race reference line on 「每次路線難度（コース定数）」 (views/training.json, chart key
"race_refs": "course_constant").

TWO dashed lines at most (owner 2026-10-02): the next two upcoming races, nearest first, any
grade, each at its single-day target — a single-day race's own course constant, a multi-day
trip's per-day average — labelled with the grade (「A 大小霸 每天 22」「B 台北馬 11」; A red,
B orange, other grades blue). The whole-trip number and the per-day breakdown are in the
line's hover. The light 80–100 % band only under the nearest A race (when it is one of the
two). Points read against the nearest race. Every race's constant uses the formula the chart's points use
(chart_metrics.course_constant, 山本正嘉: 1.8 × h + 0.3 × km + 10 × climb km + 0.6 × descent km):
  * course = the GPX stored with the event (engine/event_gpx.py: real km / climb / descent,
             per day at the stored day ends), else the event's km / climb with descent = climb
             (same start and finish assumed — 推估)
  * time   = the race calculator's predicted finish (api/racepower.predict: road / trail
             moving time, 百岳 the walking model per day), else the plan's 預估移動時間

Multi-day trips (百岳縦走, stage races) — ONE number for the trip, as the official grading
does it: 信州 山のグレーディング grades a multi-day route on its whole-route ルート定数 (the
course constant of the total time / km / climb / descent, e.g. 裏銀座 113 → 体力度 10;
長野県 grading matrix, sangakusogocenter.com list_A4.pdf). The formula is linear, so the trip
total = the sum of its days. ITRA does the same for stage races (one km-effort for the sum
of all stages, then one point less for the rest between stages). Because the chart's points
are single activities and 山本's bands (≈20 一般, 30 健脚, 40+ = 日帰り困難) are per day, the
line of a trip is its per-day average (= total / days). No recovery / overnight factor: no
source gives one (the multi-stage fatigue studies show day 2+ is harder, not by how much).

The viewer labels each point 「＝ <race> 單日目標的 X%」 against `race_ref.target` (the
nearest race) `.goal`.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Optional

from backend.engine.algorithms.chart_metrics import course_constant
from backend.i18n import _

COLOR = {"A": "#dc2626", "B": "#f97316"}
COLOR_OTHER = "#2563eb"          # C and any other grade
MAX_LINES = 2                    # owner 2026-10-02: the next two races only
BAND_LO = 0.8                    # 推估: 「close enough」 = 80–100 % of the single-day target
CALC_TYPE = {"race": "trail", "road": "road", "baiyue": "baiyue", "other": "trail"}   # = racepower.html KIND

NOTE_NONE = ("賽季計畫沒有未來的賽事（或賽事沒填距離），所以沒有目標線。"
             "在「賽季計畫」加賽事的距離和爬升，這裡就會畫出來。")
NOTE_MULTI = ("多天行程的整趟定數＝每天相加（信州山岳グレーディング的ルート定數；ITRA 分站賽同理），"
              "但山本正嘉的分級（約 20 一般、30 健腳、40 以上一天走不完）是「每天」的，所以目標線用每天平均。"
              "沒有加「隔天疲勞」係數：沒有可用的倍數。")


def upcoming(plan, today: dt.date) -> list:
    """Every event not over yet that has a distance, any grade, nearest first."""
    return sorted((e for e in plan.events if e.end >= today and e.distance_km), key=lambda e: e.start)


def stored_course(e) -> Optional[dict]:
    """The event's stored GPX as per-day km / climb / descent (event_gpx.day_stats), None = none."""
    from backend.engine import event_gpx as EG
    try:
        return EG.day_stats(e.id, split_days(e))
    except Exception:                       # noqa: BLE001 — a broken file = the plan's numbers
        return None


def split_days(e) -> int:
    """The days the course is cut into (planning.Event.split_days: a 連續 race is one piece)."""
    n = getattr(e, "split_days", None)
    return int(n) if n else max(1, int(e.days or 1))


def plan_course(e) -> dict:
    """The event's own km / climb split equally over its days; descent = climb (推估)."""
    n = split_days(e)
    km, climb = float(e.distance_km), float(e.climbing_m or 0.0)
    return {"totals": {"km": km, "gain_m": climb, "loss_m": climb},
            "days": [{"day": i, "km": km / n, "gain_m": climb / n, "loss_m": climb / n} for i in range(1, n + 1)],
            "split_source": "equal" if n > 1 else "single", "filename": None, "descent_assumed": True}


def day_plan_course(e) -> Optional[dict]:
    """The trip's own per-day numbers (Event.day_plan, SP-114) as a course; a day without its
    descent takes descent = climb (推估, as plan_course). None without a full day plan."""
    rows = getattr(e, "day_plan", None) or []
    n = split_days(e)
    if n < 2 or len(rows) != n:
        return None
    days = [{"day": i, "km": float(r["km"]), "gain_m": float(r.get("gain_m") or 0.0),
             "loss_m": float(r["loss_m"] if r.get("loss_m") is not None else r.get("gain_m") or 0.0)}
            for i, r in enumerate(rows, 1)]
    return {"totals": {k: sum(d[k] for d in days) for k in ("km", "gain_m", "loss_m")}, "days": days,
            "split_source": "day_plan", "filename": None,
            "descent_assumed": any(r.get("loss_m") is None for r in rows)}


def course_of(e, gpx: Callable = stored_course) -> dict:
    """SP-114: a GPX with its own day ends (the calculator's split points, camp waypoints) wins;
    then the trip's own per-day numbers (Event.day_plan); then the GPX cut into equal days; the
    event's km / climb split equally only when there is neither."""
    c = gpx(e)
    if c and (split_days(e) < 2 or c.get("split_source") in ("stored", "camp")):
        return {**c, "descent_assumed": False}
    dp = day_plan_course(e)
    if dp is not None:
        return {**dp, "filename": (c or {}).get("filename")}
    return {**c, "descent_assumed": False} if c else plan_course(e)


def calculator_hours(e, course: Optional[dict] = None) -> Optional[list[float]]:
    """Moving hours per day from the race calculator (POST /racepower/predict with the
    course's distance / climb / descent / days), None when it can't predict (no CP, no RE, …)."""
    from backend.api import racepower as RP
    course = course or plan_course(e)
    t, days = course["totals"], course["days"]
    try:
        body = RP.PredictIn(type=CALC_TYPE.get(e.kind, "trail"), distance_km=float(t["km"]),
                            gain_m=float(t["gain_m"] or 0.0),
                            loss_m=None if course.get("descent_assumed") else float(t["loss_m"]),
                            days=len(days), date=e.date, pack_kg=e.pack_kg,
                            day_plan=[RP.DayIn(km=d["km"], gain_m=d["gain_m"], loss_m=d["loss_m"]) for d in days]
                            if len(days) > 1 else None)
        res = RP.predict(body)["result"]
    except Exception:                       # noqa: BLE001 — the plan's own estimate is the fallback
        return None
    if res.get("days"):
        hs = [float(d.get("moving_h") or 0.0) for d in res["days"]]
    else:
        tt = res.get("time_s")
        hs = [float(tt) / 3600.0] if tt else []
    return hs if hs and all(math.isfinite(h) and h > 0 for h in hs) else None


def spread(total_h: float, days: list[dict]) -> list[float]:
    """A whole-trip time over its days by each day's km-effort (km + climb / 100, ITRA) — 推估."""
    w = [max(0.0, d["km"] + d["gain_m"] / 100.0) for d in days]
    s = sum(w)
    return [total_h * x / s for x in w] if s > 0 else [total_h / len(days)] * len(days)


def taiyokudo(total_cc: float) -> int:
    """信州 体力度 1–10 from the route constant: ≤ 10 → 1, … > 90 → 10 (推估 boundaries)."""
    return int(min(10, max(1, math.ceil(total_cc / 10.0))))


def race_line(e, hours: Optional[list[float]], source: str, course: Optional[dict] = None) -> Optional[dict]:
    course = course or plan_course(e)
    days = course["days"]
    n = len(days)
    if not hours:
        if not e.est_hours:
            return None
        hours, source = [float(e.est_hours)], "賽季計畫的預估移動時間"
    if len(hours) != n:
        hours = spread(sum(hours), days) if n > 1 else [sum(hours)]
        if n > 1:
            source += "（依每天的 km-effort 分配到各天，推估）"
    per = [{"day": d["day"], "km": round(d["km"], 1), "climb_m": round(d["gain_m"]), "descent_m": round(d["loss_m"]),
            "hours": round(h, 2), "cc": round(course_constant(h, d["km"], d["gain_m"], d["loss_m"]), 1)}
           for d, h in zip(days, hours)]
    t = course["totals"]
    total = course_constant(sum(hours), t["km"], t["gain_m"], t["loss_m"])   # = Σ days (linear)
    hard = max(per, key=lambda d: d["cc"])
    out = {"event_id": e.id, "name": e.name, "priority": e.priority, "date": e.date, "days": n,
           "cc": round(total, 1), "hours": round(sum(hours), 2), "time_source": source,
           "km": round(t["km"], 1), "climb_m": round(t["gain_m"]), "descent_m": round(t["loss_m"]),
           "descent_assumed": bool(course.get("descent_assumed")), "gpx": course.get("filename"),
           "per_day": per}
    if n > 1:
        out.update(multi=True, day_mean=round(total / n, 1), day_max=hard["cc"], hardest_day=hard["day"],
                   taiyokudo=taiyokudo(total), split_source=course.get("split_source"))
    return out


def tip(ln: dict) -> str:
    src = f"GPX「{ln['gpx']}」" if ln.get("gpx") else "賽季計畫的距離、爬升（下降＝爬升，推估）"
    head = (f"{ln['priority']} 賽事 {ln['date']}：{ln['km']:.1f} km、↑{ln['climb_m']} m ↓{ln['descent_m']} m、"
            f"{ln['hours']:.1f} h（{ln['time_source']}）\n路線：{src}")
    if not ln.get("multi"):
        return head + f"\nコース定數 {ln['cc']:.0f}"
    split = {"stored": "你在賽事計算器點的分日點", "camp": "GPX 的營地／山屋航點", "equal": "每天距離平均（沒有分日點）",
             "day_plan": "你填的每天距離和爬升"}
    rows = "\n".join(f"第 {d['day']} 天：{d['km']:.1f} km ↑{d['climb_m']} ↓{d['descent_m']} {d['hours']:.1f} h → 定數 {d['cc']:.0f}"
                     + ("（最難）" if d["day"] == ln["hardest_day"] else "") for d in ln["per_day"])
    return (head + f"\n整趟 {ln['days']} 天コース定數 {ln['cc']:.0f}（體力度 ≈ {ln['taiyokudo']}，推估）・"
            f"每天平均 {ln['day_mean']:.0f}・最難一天 {ln['day_max']:.0f}\n分日：{split.get(ln.get('split_source'), '—')}\n{rows}")


def goal_of(ln: dict) -> float:
    """The single-day target of a race line: a multi-day trip's per-day average, else its constant."""
    return ln["day_mean"] if ln.get("multi") else ln["cc"]


def color_of(ln: dict) -> str:
    return COLOR.get(ln.get("priority"), COLOR_OTHER)


def line_name(ln: dict) -> str:
    """「A 大小霸 每天 22」 / 「B 台北馬 11」: grade, race, single-day target."""
    if ln.get("multi"):
        return _("{grade} {name} 每天 {goal:.0f}", grade=ln["priority"], name=ln["name"], goal=ln["day_mean"])
    return _("{grade} {name} {goal:.0f}", grade=ln["priority"], name=ln["name"], goal=ln["cc"])


def guide(lines: list, band: Optional[dict]) -> str:
    """The chart's ?: how to use the lines (owner 2026-10-02: a few short lines; the formula
    on the last one). Sourcing (docs/research/back-to-back-and-long-day.md §2.2): the specific
    phase = 賽前 10–3 週 (planning: 8-week specific + 14-day taper); 80–100 % at 6–3 weeks is
    推估 (江晏慶 「抓比賽距離爬升的七成」 about 1.5 months out, CTS's longest block 4–6 weeks
    out, Koop: don't force it in the last 2–3 weeks); taper 2 weeks: UA / Koop."""
    out = [_("虛線＝接下來 {n} 場賽事一天的難度（紅 A、橘 B、藍其他；多天行程用每天平均）；滑過線看整趟和每一天",
             n=len(lines)),
           _("專項期（賽前約 10–3 週）每 1–2 週排一次長天，點要一步步靠近最近那場的線（推估）")]
    if band is not None:
        out.append(_("{name} 前 6–3 週至少 1–2 次做到線的 {lo}–100%（淺色帶；推估，參考江晏慶「抓比賽的七成」、"
                     "CTS 賽前 4–6 週的最長一段）", name=band["name"], lo=round(BAND_LO * 100)))
    out += [_("賽前 2 週不再做接近線的長天（減量期；UA、Koop）"),
            _("定數＝時間 h×1.8＋距離 km×0.3＋爬升 km×10＋下降 km×0.6（山本正嘉）")]
    return "\n".join(out)


def course_constant_refs(plan, today: dt.date,
                         predict: Callable = calculator_hours, gpx: Callable = stored_course) -> dict:
    """{"lines": the next MAX_LINES race lines (nearest first, any grade), "target": the nearest
    one with "goal" = its single-day target (the point hover reads against it), "band": the
    nearest A race among them (its 80–100 % band) or None, "note"}."""
    lines = []
    for e in upcoming(plan, today):
        if len(lines) >= MAX_LINES:
            break
        c = course_of(e, gpx)
        hs = predict(e, c)
        ln = race_line(e, hs, "賽事計算器預測的完賽時間", c)
        if ln:
            ln["goal"] = round(goal_of(ln), 1)
            lines.append(ln)
    target = lines[0] if lines else None
    band = next((x for x in lines if x["priority"] == "A"), None)
    return {"lines": lines, "target": target, "band": band,
            "note": guide(lines, band) if lines else NOTE_NONE}


def apply(res: dict, plan, today: dt.date, predict=calculator_hours, gpx=stored_course) -> dict:
    """The rendered chart with up to two dashed reference lines (each race's single-day
    target, labelled at the right edge with its grade; hover = the whole trip and every day),
    a light 80–100 % band under the nearest A race's line, the how-to-use guide as its ?,
    and `race_ref` for the point hover; no races = the chart as it was, with the why behind its ?."""
    refs = course_constant_refs(plan, today, predict, gpx)
    series = list(res.get("series") or [])
    tg = refs["target"]
    desc = (res.get("description") or "").rstrip()
    if tg is None:
        return {**res, "series": series, "description": f"{desc}\n\n{refs['note']}" if desc else refs["note"],
                "race_ref": {"target": None, "band": None, "lines": refs["lines"]}}
    like = next((s for s in series if (s.get("data") or {}).get("kind") == "points"), None) or \
        next(iter(series), {})
    base = {"type": "line", "y_axis": like.get("y_axis") or "NONE", "unit": like.get("unit"),
            "x_unit": like.get("x_unit"), "expression": "", "role": "race_ref"}
    band = refs["band"]
    if band is not None:
        series.append({**base, "name": _("{name} 的 {lo}–100%", name=band["name"], lo=round(BAND_LO * 100)),
                       "color": color_of(band),
                       "data": {"kind": "band", "range": [round(BAND_LO * band["goal"], 1), band["goal"]]}})
    for ln in refs["lines"]:
        series.append({**base, "name": line_name(ln), "color": color_of(ln), "line_style": "dash",
                       "line_width": "medium", "label_end": True, "data": {"kind": "hline", "y": ln["goal"]},
                       "tip": tip(ln) + ("\n\n" + NOTE_MULTI if ln.get("multi") else "")})
    return {**res, "series": series, "description": refs["note"],
            "race_ref": {"target": tg, "band": band, "lines": refs["lines"]}}


