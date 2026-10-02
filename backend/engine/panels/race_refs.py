"""
Target-race reference lines on 「每次路線難度（コース定数）」 (views/training.json, chart key
"race_refs": "course_constant").

For each upcoming A / B event of the season plan (the same pool as planning.goals: A events
up to the next one, B events within ~6 months) one dashed horizontal line at the race's own
course constant, with the formula the chart's points use (chart_metrics.course_constant,
山本正嘉: 1.8 × h + 0.3 × km + 10 × climb km + 0.6 × descent km):
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
trip also gets a lighter line at its per-day average (= total / days); the hover lists every
day and the hardest one. No recovery / overnight factor: no source gives one (the multi-stage
fatigue studies show day 2+ is harder, not by how much).

Plus a lighter line at 50 % of the A race: 推估 guidance for the longest training day (no
published ratio for the course constant). The viewer labels each point 「＝ 目標賽事的 X%」
against `race_ref.target` (multi-day: of the trip and of its per-day average).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Optional

from backend.engine.algorithms.chart_metrics import course_constant

HALF = 0.5                       # 推估: the longest training day ≈ half of the A race
COLOR = {"A": "#7c3aed", "B": "#a78bfa"}
HALF_COLOR = "#c4b5fd"
DAY_COLOR = {"A": "#a78bfa", "B": "#c4b5fd"}
CALC_TYPE = {"race": "trail", "road": "road", "baiyue": "baiyue", "other": "trail"}   # = racepower.html KIND

NOTE_NONE = ("賽季計畫沒有未來的 A／B 賽事（或賽事沒填距離），所以沒有目標賽事參考線。"
             "在「賽季計畫」加賽事的距離和爬升，這裡就會畫出每場賽事的定數。")
NOTE_LINES = ("目標賽事參考線：虛線＝每場未來 A／B 賽事的コース定數，用同一個公式代入賽事的距離、爬升、下降，"
              "時間用賽事計算器預測的完賽時間（算不出來時用賽季計畫填的預估時間）。"
              "賽事有存 GPX 就用 GPX 的距離、爬升、下降；沒有 GPX 時下降當成和爬升一樣（同起終點，推估）。"
              "淡線＝A 賽事的 50%：最長一次訓練的參考（推估，沒有研究依據）。滑過每個點會顯示它是目標賽事的幾 %。")
NOTE_MULTI = ("多天行程（百岳縱走、分站賽）用「整趟」一個數字：長野縣・信州山岳グレーディング對多天縱走就是用整條路線的"
              "ルート定數（整趟時間、距離、爬升、下降代入同一個公式，例：裏銀座 113 → 體力度 10），"
              "公式是線性的，所以整趟＝每天相加；ITRA 的分站賽也是把各站距離、爬升加總算一個 km-effort。"
              "體力度 ≈ 整趟定數 ÷ 10 無條件進位（1–10，推估：官方表沒印分界，對照維基百科與官方路線表）。"
              "圖上的點是單次活動、山本正嘉的分級（約 20 一般、30 健腳、40 以上一天走不完）是「每天」的，"
              "所以多天行程另畫一條淡虛線＝每天平均（整趟 ÷ 天數），滑過線看每一天和最難的一天。"
              "沒有加「隔天疲勞」係數：多日超馬研究顯示第二天後會更累，但沒有可用的倍數。")


def upcoming(plan, today: dt.date) -> list:
    """The A / B events planning.goals() trains for, by date."""
    from backend.engine.planning import goals
    ids = set(goals(plan, today)["events"])
    return sorted((e for e in plan.events if e.id in ids and e.distance_km), key=lambda e: e.start)


def stored_course(e) -> Optional[dict]:
    """The event's stored GPX as per-day km / climb / descent (event_gpx.day_stats), None = none."""
    from backend.engine import event_gpx as EG
    try:
        return EG.day_stats(e.id, max(1, int(e.days or 1)))
    except Exception:                       # noqa: BLE001 — a broken file = the plan's numbers
        return None


def plan_course(e) -> dict:
    """The event's own km / climb split equally over its days; descent = climb (推估)."""
    n = max(1, int(e.days or 1))
    km, climb = float(e.distance_km), float(e.climbing_m or 0.0)
    return {"totals": {"km": km, "gain_m": climb, "loss_m": climb},
            "days": [{"day": i, "km": km / n, "gain_m": climb / n, "loss_m": climb / n} for i in range(1, n + 1)],
            "split_source": "equal" if n > 1 else "single", "filename": None, "descent_assumed": True}


def course_of(e, gpx: Callable = stored_course) -> dict:
    c = gpx(e)
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
    split = {"stored": "你在賽事計算器點的分日點", "camp": "GPX 的營地／山屋航點", "equal": "每天距離平均（沒有分日點）"}
    rows = "\n".join(f"第 {d['day']} 天：{d['km']:.1f} km ↑{d['climb_m']} ↓{d['descent_m']} {d['hours']:.1f} h → 定數 {d['cc']:.0f}"
                     + ("（最難）" if d["day"] == ln["hardest_day"] else "") for d in ln["per_day"])
    return (head + f"\n整趟 {ln['days']} 天コース定數 {ln['cc']:.0f}（體力度 ≈ {ln['taiyokudo']}，推估）・"
            f"每天平均 {ln['day_mean']:.0f}・最難一天 {ln['day_max']:.0f}\n分日：{split.get(ln.get('split_source'), '—')}\n{rows}")


def course_constant_refs(plan, today: dt.date,
                         predict: Callable = calculator_hours, gpx: Callable = stored_course) -> dict:
    """{"lines": [race line], "target": the A race's (else the first) line, "half": 50 % of
    the A race or None, "note"}."""
    lines = []
    for e in upcoming(plan, today):
        c = course_of(e, gpx)
        hs = predict(e, c)
        ln = race_line(e, hs, "賽事計算器預測的完賽時間", c)
        if ln:
            lines.append(ln)
    a = next((x for x in lines if x["priority"] == "A"), None)
    target = a or (lines[0] if lines else None)
    note = NOTE_LINES + ("\n\n" + NOTE_MULTI if any(x.get("multi") for x in lines) else "") if lines else NOTE_NONE
    return {"lines": lines, "target": target,
            "half": round(HALF * a["cc"], 1) if a else None, "note": note}


def apply(res: dict, plan, today: dt.date, predict=calculator_hours, gpx=stored_course) -> dict:
    """The rendered chart with the reference lines added as dashed hline series (labelled
    at the right edge, hover = the line's breakdown) and `race_ref` for the point hover; no
    races = the chart as it was, with the why behind its ?."""
    refs = course_constant_refs(plan, today, predict, gpx)
    series = list(res.get("series") or [])
    like = next((s for s in series if (s.get("data") or {}).get("kind") == "points"), None) or \
        next(iter(series), {})
    base = {"type": "line", "y_axis": like.get("y_axis") or "NONE", "unit": like.get("unit"),
            "x_unit": like.get("x_unit"), "expression": "", "role": "race_ref", "label_end": True}
    for ln in refs["lines"]:
        t = tip(ln)
        if ln.get("multi"):
            series.append({**base, "name": f"{ln['name']} · 整趟 {ln['days']} 天 定數 {ln['cc']:.0f}",
                           "color": COLOR.get(ln["priority"], COLOR["B"]), "line_style": "dash", "line_width": "medium",
                           "data": {"kind": "hline", "y": ln["cc"]}, "tip": t})
            series.append({**base, "name": f"{ln['name']} · 每天平均 {ln['day_mean']:.0f}",
                           "color": DAY_COLOR.get(ln["priority"], DAY_COLOR["B"]), "line_style": "dash",
                           "line_width": "thin", "data": {"kind": "hline", "y": ln["day_mean"]}, "tip": t})
        else:
            series.append({**base, "name": f"{ln['name']} · 定數 {ln['cc']:.0f}", "color": COLOR.get(ln["priority"], COLOR["B"]),
                           "line_style": "dash", "line_width": "medium", "data": {"kind": "hline", "y": ln["cc"]},
                           "tip": t})
    if refs["half"] is not None:
        series.append({**base, "name": f"{refs['target']['name']} 的 50% · {refs['half']:.0f}（推估）",
                       "color": HALF_COLOR, "line_style": "dash", "line_width": "thin",
                       "data": {"kind": "hline", "y": refs["half"]},
                       "tip": "最長一次訓練的參考：A 賽事（多天＝整趟）定數的一半（推估，沒有研究依據）"})
    desc = (res.get("description") or "").rstrip()
    return {**res, "series": series, "description": f"{desc}\n\n{refs['note']}" if desc else refs["note"],
            "race_ref": {"target": refs["target"], "lines": refs["lines"], "half": refs["half"]}}
