"""
Target-race reference lines on 「每次路線難度（コース定数）」 (views/training.json, chart key
"race_refs": "course_constant").

For each upcoming A / B event of the season plan (the same pool as planning.goals: A events
up to the next one, B events within ~6 months) one dashed horizontal line at the race's own
course constant, with the formula the chart's points use (chart_metrics.course_constant,
山本正嘉: 1.8 × h + 0.3 × km + 10 × climb km + 0.6 × descent km):
  * time   = the race calculator's predicted finish (api/racepower.predict: road / trail
             moving time, 百岳 the walking model per day), else the plan's 預估移動時間
  * km, climb from the event; descent = the climb (the plan has no descent field and an
             uploaded GPX is not kept with the event; same start and finish assumed — 推估)
  * a multi-day 百岳: its hardest day (the chart's points are single activities)
Plus a lighter line at 50 % of the A race: 推估 guidance for the longest training day (no
published ratio for the course constant). The viewer labels each point 「＝ 目標賽事的 X%」
against `race_ref.target`.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable, Optional

from backend.engine.algorithms.chart_metrics import course_constant

HALF = 0.5                       # 推估: the longest training day ≈ half of the A race
COLOR = {"A": "#7c3aed", "B": "#a78bfa"}
HALF_COLOR = "#c4b5fd"
CALC_TYPE = {"race": "trail", "road": "road", "baiyue": "baiyue", "other": "trail"}   # = racepower.html KIND

NOTE_NONE = ("賽季計畫沒有未來的 A／B 賽事（或賽事沒填距離），所以沒有目標賽事參考線。"
             "在「賽季計畫」加賽事的距離和爬升，這裡就會畫出每場賽事的定數。")
NOTE_LINES = ("目標賽事參考線：虛線＝每場未來 A／B 賽事的コース定數，用同一個公式代入賽事的距離、爬升，"
              "時間用賽事計算器預測的完賽時間（算不出來時用賽季計畫填的預估時間）；"
              "下降沒有資料，當成和爬升一樣（同起終點，推估）；多天的百岳取最難的一天。"
              "淡線＝A 賽事的 50%：最長一次訓練的參考（推估，沒有研究依據）。滑過每個點會顯示它是目標賽事的幾 %。")


def upcoming(plan, today: dt.date) -> list:
    """The A / B events planning.goals() trains for, by date."""
    from backend.engine.planning import goals
    ids = set(goals(plan, today)["events"])
    return sorted((e for e in plan.events if e.id in ids and e.distance_km), key=lambda e: e.start)


def calculator_hours(e) -> Optional[list[float]]:
    """Moving hours per day from the race calculator (POST /racepower/predict with the
    event's distance / climb / days), None when it can't predict (no CP, no RE, …)."""
    from backend.api import racepower as RP
    try:
        body = RP.PredictIn(type=CALC_TYPE.get(e.kind, "trail"), distance_km=float(e.distance_km),
                            gain_m=float(e.climbing_m or 0.0), days=max(1, int(e.days or 1)), date=e.date,
                            pack_kg=e.pack_kg)
        res = RP.predict(body)["result"]
    except Exception:                       # noqa: BLE001 — the plan's own estimate is the fallback
        return None
    if res.get("days"):
        hs = [float(d.get("moving_h") or 0.0) for d in res["days"]]
    else:
        t = res.get("time_s")
        hs = [float(t) / 3600.0] if t else []
    return hs if hs and all(math.isfinite(h) and h > 0 for h in hs) else None


def race_line(e, hours: Optional[list[float]], source: str) -> Optional[dict]:
    if not hours:
        if not e.est_hours:
            return None
        n = max(1, int(e.days or 1))
        hours, source = [float(e.est_hours) / n] * n, "賽季計畫的預估移動時間"
    n = len(hours)
    km, climb = float(e.distance_km) / n, float(e.climbing_m or 0.0) / n
    ccs = [course_constant(h, km, climb, climb) for h in hours]
    i = max(range(n), key=lambda k: ccs[k])
    return {"event_id": e.id, "name": e.name, "priority": e.priority, "date": e.date, "days": n,
            "cc": round(ccs[i], 1), "hours": round(hours[i], 2), "time_source": source,
            "km": round(km, 1), "climb_m": round(climb), "descent_m": round(climb), "descent_assumed": True}


def course_constant_refs(plan, today: dt.date,
                         predict: Callable[[object], Optional[list[float]]] = calculator_hours) -> dict:
    """{"lines": [race line], "target": the A race's (else the first) line, "half": 50 % of
    the A race or None, "note"}."""
    lines = []
    for e in upcoming(plan, today):
        hs = predict(e)
        ln = race_line(e, hs, "賽事計算器預測的完賽時間")
        if ln:
            lines.append(ln)
    a = next((x for x in lines if x["priority"] == "A"), None)
    target = a or (lines[0] if lines else None)
    return {"lines": lines, "target": target,
            "half": round(HALF * a["cc"], 1) if a else None,
            "note": NOTE_LINES if lines else NOTE_NONE}


def apply(res: dict, plan, today: dt.date, predict=calculator_hours) -> dict:
    """The rendered chart with the reference lines added as dashed hline series (labelled
    at the right edge) and `race_ref` for the hover; no races = the chart as it was, with
    the why behind its ?."""
    refs = course_constant_refs(plan, today, predict)
    series = list(res.get("series") or [])
    like = next((s for s in series if (s.get("data") or {}).get("kind") == "points"), None) or \
        next(iter(series), {})
    base = {"type": "line", "y_axis": like.get("y_axis") or "NONE", "unit": like.get("unit"),
            "x_unit": like.get("x_unit"), "expression": "", "role": "race_ref", "label_end": True}
    for ln in refs["lines"]:
        series.append({**base, "name": f"{ln['name']} · 定數 {ln['cc']:.0f}", "color": COLOR.get(ln["priority"], COLOR["B"]),
                       "line_style": "dash", "line_width": "medium", "data": {"kind": "hline", "y": ln["cc"]},
                       "tip": f"{ln['priority']} 賽事 {ln['date']}：{ln['km']:.1f} km、爬升 {ln['climb_m']} m、"
                              f"{ln['hours']:.1f} h（{ln['time_source']}）"})
    if refs["half"] is not None:
        series.append({**base, "name": f"{refs['target']['name']} 的 50% · {refs['half']:.0f}（推估）",
                       "color": HALF_COLOR, "line_style": "dash", "line_width": "thin",
                       "data": {"kind": "hline", "y": refs["half"]}})
    desc = (res.get("description") or "").rstrip()
    return {**res, "series": series, "description": f"{desc}\n\n{refs['note']}" if desc else refs["note"],
            "race_ref": {"target": refs["target"], "lines": refs["lines"], "half": refs["half"]}}
