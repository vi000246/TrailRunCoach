"""
Numbers, dates, weekdays, durations and distances in the request's locale —
the server side of the frontend's `fmt.*` (backend/static/i18n/i18n.js; same
names). Two locales are written by hand; past two, consider Babel.

Units stay metric (km, m, kg, min/km): every distance / climb goes through
dist() / elev(), so a later km/mi preference changes only these (it will be a
unit setting of its own, not tied to the language).

zh-TW output is what the pages and the engine wrote before this module:
    date(d, "mdw")      10/2（週四）        en: Thu 10/2
    weekday(d)          週四                en: Thu
    weekday(d, "narrow") 四                 en: T
    dur(12000, "hm")    3 小時 20 分        en: 3h 20m
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional, Union

from backend.i18n import current_locale

Day = Union[dt.date, dt.datetime, str]

_WD = {
    "zh-TW": {"narrow": "一二三四五六日", "short": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"],
              "long": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]},
    "en": {"narrow": "MTWTFSS", "short": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
           "long": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]},
}


def _loc(loc: Optional[str]) -> str:
    loc = loc or current_locale()
    return loc if loc in _WD else "zh-TW"


def _day(d: Day) -> dt.date:
    if isinstance(d, str):
        return dt.date.fromisoformat(d[:10])
    if isinstance(d, dt.datetime):
        return d.date()
    return d


def weekdays(style: str = "short", loc: Optional[str] = None) -> list[str]:
    """The seven names, Monday first."""
    t = _WD[_loc(loc)][style]
    return list(t)


def weekday(d: Union[Day, int], style: str = "short", loc: Optional[str] = None) -> str:
    """A date's weekday (or an index, Monday = 0): 週四 / Thu; narrow 四 / T."""
    i = d if isinstance(d, int) else _day(d).weekday()
    return weekdays(style, loc)[i % 7]


def date(d: Day, style: str = "md", loc: Optional[str] = None) -> str:
    """md 10/2 · mdw 10/2（週四）/ Thu 10/2 · ymd 2026-10-02."""
    x = _day(d)
    lo = _loc(loc)
    if style == "ymd":
        return x.isoformat()
    md = f"{x.month}/{x.day}"
    if style == "mdw":
        w = weekday(x, "short", lo)
        return f"{md}（{w}）" if lo == "zh-TW" else f"{w} {md}"
    return md


def num(x: Optional[float], d: int = 0, loc: Optional[str] = None) -> str:
    """Fixed decimals with a thousands separator ('' for None / NaN)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return f"{x:,.{d}f}"


def pct(x: Optional[float], d: int = 0, loc: Optional[str] = None) -> str:
    """A fraction as a percentage: 0.153 -> 15%."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return f"{x * 100:.{d}f}%"


def dur(seconds: Optional[float], style: str = "hm", loc: Optional[str] = None) -> str:
    """hm: 3 小時 20 分 / 3h 20m · hms: 3:20:05."""
    if seconds is None:
        return ""
    s = int(round(seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    if style == "hms":
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
    if _loc(loc) == "zh-TW":
        return f"{h} 小時 {m} 分" if h else f"{m} 分"
    return f"{h}h {m}m" if h else f"{m}m"


def pace(s_per_km: Optional[float], loc: Optional[str] = None) -> str:
    """6:05 /km."""
    if s_per_km is None or s_per_km <= 0:
        return ""
    s = int(round(s_per_km))
    return f"{s // 60}:{s % 60:02d} /km"


def dist(km: Optional[float], d: int = 1, loc: Optional[str] = None) -> str:
    """42.2 km (the one place a km/mi preference will change)."""
    return "" if km is None else f"{km:.{d}f} km"


def elev(m: Optional[float], loc: Optional[str] = None) -> str:
    """1,250 m."""
    return "" if m is None else f"{round(m):,} m"
