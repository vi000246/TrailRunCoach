"""
近 N 天新高 — the custom-view version of WKO5's "New Bests" series (Season
View > PDC耐力模型 > PD Curve with Metrics (Run)):

    @historic := athleterange(min({begindate,today-7}), min({enddate,today-7}), meanmax(runpower)),
    @recent   := athleterange(today-6, today, meanmax(runpower)),
    if(@recent > @historic, @recent)

Same semantics as WKO5: "recent" is the last N days up to `today` (the real
date, not the chart's end), "historic" is the selected range up to
today-N. A custom chart opts in with

    "window": {"default": 7, "choices": [7, 14, 28]}

and writes its window-dependent expressions with a leading `@win := 7, …`
(still a valid WKO5 expression at the default). The viewer's 7／14／28 天
toggle asks for `?window=14`; `apply_window` rewrites only that leading
literal and the "近 7 天" in titles / legend names.

A series with `"role": "recent_gain"` (the gain curve, `@recent - @historic`
where it is positive) is not drawn: `summarize` turns it into the note under
the chart, e.g. 「近 7 天新高：12 分鐘 +18 W」, or says why there is nothing
(no activity in the window, or nothing beat the earlier bests).
"""
from __future__ import annotations

import copy
import math
import re
from typing import Optional

from backend.engine.wko5expr.dataset import day_to_date

GAIN_ROLE = "recent_gain"
_WIN_RE = re.compile(r"^(\s*@win\s*:=\s*)\d+")
_TEXT_RE = re.compile(r"近\s*\d+\s*天")
# the durations a note names (s); a band with none of them names its peak
NOTE_DURATIONS = (5, 15, 30, 60, 120, 180, 300, 480, 600, 720, 1200, 1800, 2400, 3600, 5400, 7200)
MAX_ITEMS = 6


def window_spec(chart: dict) -> Optional[dict]:
    w = chart.get("window")
    if not isinstance(w, dict):
        return None
    return {"default": int(w["default"]), "choices": [int(c) for c in w["choices"]]}


def _retext(s: Optional[str], n: int) -> Optional[str]:
    return _TEXT_RE.sub(f"近 {n} 天", s) if s else s


def apply_window(chart: dict, asked: Optional[str]) -> tuple[dict, Optional[dict]]:
    """(chart with the chosen window, extra JSON for the viewer's toggle)."""
    spec = window_spec(chart)
    if spec is None:
        return chart, None
    try:
        n = int(asked) if asked is not None else spec["default"]
    except ValueError:
        n = spec["default"]
    if n not in spec["choices"]:
        n = spec["default"]
    out = copy.deepcopy(chart)
    for s in out.get("series", []):
        s["expression"] = _WIN_RE.sub(lambda m: f"{m.group(1)}{n}", s.get("expression") or "", count=1)
        s["name"] = _retext(s.get("name"), n)
    out["title"] = _retext(out.get("title"), n)
    return out, {"window": n, "window_default": spec["default"],
                 "window_choices": spec["choices"], "window_toggle": True}


def duration_label(secs: float) -> str:
    s = int(round(secs))
    if s < 60:
        return f"{s} 秒"
    if s < 3600:
        m, r = divmod(s, 60)
        return f"{m} 分鐘" if r == 0 or m >= 10 else f"{m} 分 {r} 秒"
    h, r = divmod(s, 3600)
    m = round(r / 60)
    return f"{h} 小時" if m == 0 else f"{h} 小時 {m} 分"


def _bands(points: list) -> list[list]:
    """[x, y] points split at null breaks (render._curve_json)."""
    out, cur = [], []
    for x, y in points:
        if y is None:
            if cur:
                out.append(cur)
            cur = []
        elif x is not None and x > 0:
            cur.append((float(x), float(y)))
    if cur:
        out.append(cur)
    return out


def gains(points: list) -> list[dict]:
    """The durations to name: every NOTE_DURATIONS entry inside an improved
    band (nearest grid point within 10 %), else the band's peak gain."""
    items = []
    for band in _bands(points):
        lo, hi = band[0][0], band[-1][0]
        picked = []
        for d in NOTE_DURATIONS:
            if lo * 0.95 <= d <= hi * 1.05:
                x, g = min(band, key=lambda p: abs(p[0] - d))
                if abs(x - d) <= 0.1 * d:
                    picked.append((d, g))
        if not picked:
            x, g = max(band, key=lambda p: p[1])
            picked = [(x, g)]
        items += [{"secs": d, "label": duration_label(d), "gain": round(g)} for d, g in picked]
    items = [i for i in items if i["gain"] >= 1]
    if len(items) > MAX_ITEMS:
        keep = sorted(items, key=lambda i: -i["gain"])[:MAX_ITEMS]
        items = [i for i in items if i in keep]
    return items


def _md(day: float) -> str:
    d = day_to_date(math.floor(day))
    return f"{d.month}/{d.day}"


def summarize(res: dict, ds, window: int, unit: str = "W") -> dict:
    """Pull the gain series out of a rendered chart and describe it."""
    series = res.get("series") or []
    gain = next((s for s in series if s.get("role") == GAIN_ROLE), None)
    res["series"] = [s for s in series if s.get("role") != GAIN_ROLE]
    today = math.floor(ds.today)
    recent = [w for w in ds.workouts if today - window + 1 <= math.floor(w.day) <= today]
    last = _md(ds.workouts[-1].day) if ds.workouts else None
    items = gains(((gain or {}).get("data") or {}).get("points") or []) if gain else []
    # data older than today (WKO5 not synced yet): say how far it goes
    upto = f"（資料到 {last}）" if last and math.floor(ds.workouts[-1].day) < today else ""
    if not recent:
        note = f"近 {window} 天沒有新活動" + (f"（資料到 {last}）" if last else "")
    elif not items:
        note = f"近 {window} 天沒有超越先前的最佳" + upto
    else:
        note = f"近 {window} 天新高：" + "、".join(f"{i['label']} +{i['gain']} {unit}" for i in items)
    return {"window": window, "items": items, "recent_workouts": len(recent),
            "last_day": day_to_date(math.floor(ds.workouts[-1].day)).isoformat() if ds.workouts else None,
            "note": note}
