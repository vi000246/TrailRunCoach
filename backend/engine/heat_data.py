"""
The athlete's heat-acclimation state from their own history (the I/O side of
engine/heat.py): per-activity heat exposure from route_weather's
activity_weather.json (Open-Meteo archive at each activity's point, filled by
the routes build), completed passive heat sessions, and the projection to a
race day. docs/research/heat-acclimation.md §2.4, §4.3, §5.3.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from statistics import median
from typing import Iterable, Optional

from backend.engine import heat as HT

FROM_WINDOW_DAYS = 90            # the training-conditions window of racepower (athlete.CP_WINDOW_DAYS)
HOT_MONTH_WINDOW_D = 15


def _root() -> Path:
    from backend.engine import routes as R
    return R.HOME


def exposures(root: Optional[Path] = None) -> tuple[list[dict], dict]:
    from backend.engine import route_weather as RW
    doc = RW.load_activity_weather(root or _root())
    acts = [dict(v, file=f) for f, v in (doc.get("activities") or {}).items() if v]
    return acts, {"at": doc.get("at"), "stats": doc.get("stats"), "attribution": doc.get("attribution"),
                  "missing": not doc}


def status(today: Optional[dt.date] = None, race_day: Optional[dt.date] = None,
           planned: Optional[dict] = None, passive_dates: Iterable = (), root: Optional[Path] = None,
           acts: Optional[list] = None) -> dict:
    """Today's S, its series, S_from (mean S over the racepower training
    window) and — with `race_day` — the projected race-day S under the
    three parameter sets (heat.project), counting `planned` heat sessions."""
    today = today or dt.date.today()
    meta = {"missing": False}
    if acts is None:
        acts, meta = exposures(root)
    cur = HT.current(acts, today, passive_dates)
    s_from = HT.mean_s(cur["series"], today - dt.timedelta(days=FROM_WINDOW_DAYS), today)
    out = {"today": today.isoformat(), "s": cur["s"], "level": cur["level"], "days_14": cur["days_14"],
           "last_exposure": cur["last_exposure"], "since_last_d": cur["since_last_d"],
           "s_from": s_from if s_from is not None else 0.0,
           "series": [(d.isoformat(), round(s, 4)) for d, s in cur["series"][-120:]],
           "doses": {d.isoformat(): round(v, 3) for d, v in cur["doses"].items() if d >= today - dt.timedelta(days=120)},
           "n_activities": len(acts), "data": meta, "evidence": HT.EVIDENCE, "badge": "推估",
           "a": HT.A_RECOVER, "a_range": list(HT.A_RANGE)}
    if race_day:
        pj = HT.project(cur["s"], today, race_day, planned)
        out["s_race"] = {"center": pj["center"], "low": pj["low"], "high": pj["high"], "date": race_day.isoformat()}
        out["source"] = (f"近 14 天 {cur['days_14']} 天熱暴露" +
                         (f"，最後一次 {cur['since_last_d']} 天前" if cur["since_last_d"] is not None else "") +
                         f"，推算到比賽日 {race_day.month}/{race_day.day}")
    else:
        out["source"] = f"近 14 天 {cur['days_14']} 天熱暴露"
    if meta.get("missing"):
        out["warning"] = "還沒有每筆活動的歷史天氣（路線頁重建一次、含天氣）：S 當作 0"
    return out


def month_is_hot(acts: list[dict], day: dt.date) -> Optional[bool]:
    """auto rule for Event.heat (自組): the athlete's outdoor activities within
    ±15 calendar days of that date in earlier years — median Hadley > 150 →
    hot. None without data."""
    hs = []
    for a in acts:
        try:
            d = dt.date.fromisoformat(a["date"])
        except (KeyError, ValueError):
            continue
        if d >= day:
            continue
        dd = abs((d.replace(year=2000) - day.replace(year=2000)).days)
        dd = min(dd, 366 - dd)
        if dd <= HOT_MONTH_WINDOW_D and a.get("hadley") is not None:
            hs.append(a["hadley"])
    if len(hs) < 5:
        return None
    return median(hs) > HT.HOT_HADLEY


def event_is_hot(ev, acts: list[dict]) -> dict:
    """Event.heat: hot / cool as the user set it; auto → 百岳 cool (it is at
    altitude), otherwise month_is_hot on the athlete's own history."""
    h = getattr(ev, "heat", "auto") or "auto"
    if h in ("hot", "cool"):
        return {"hot": h == "hot", "source": "你設定的"}
    if getattr(ev, "kind", "") == "baiyue":
        return {"hot": False, "source": "百岳在高海拔：預設不熱"}
    r = month_is_hot(acts, ev.start)
    if r is None:
        return {"hot": False, "source": "同月份的歷史天氣不足：當作不熱"}
    return {"hot": r, "source": "你往年同一時期戶外活動的 Hadley 中位數" + (" > 150" if r else " ≤ 150")}
