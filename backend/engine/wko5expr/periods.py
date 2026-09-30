"""
Period-total charts: the 日／週／月／季／年 bucket of `sum(x, startofmonth(date))`
style charts.

* `chart_period(chart)` — the bucket a chart totals by: its `period` key
  (custom views) or the bucket call in its expressions (imported WKO5 charts).
* `with_period(chart, period)` — the chart re-bucketed: every
  `trunc(date)` / `startofweek(date)` / `startofmonth(date)` /
  `startofquarter(date)` / `startofyear(date)` becomes the chosen bucket, and a
  leading 每日／每週／每月／每季／每年 in the title follows.
* `period_locked(chart)` — the toggle would draw a mismatched line: the chart
  has series tied to a week scale (`shift(..., 7)` week-over-week, `tl(x, N)*7`
  weekly reference lines), so it keeps its default bucket.
* `min_days(chart, period)` — look-back floor for the chosen bucket (a monthly
  total over the last 30 days is one bar): month 365, quarter 730, year 1825
  days; the chart's own `min_days` only applies to its default bucket.
* `buckets(begin, end, period)` — every bucket start in the range, so empty
  buckets still get a category on the x axis.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
from typing import Optional

from backend.engine.wko5expr.dataset import date_to_day, day_to_date

PERIODS = ("day", "week", "month", "quarter", "year")
BUCKET_CALL = {"day": "trunc(date)", "week": "startofweek(date)", "month": "startofmonth(date)",
               "quarter": "startofquarter(date)", "year": "startofyear(date)"}
_FN_PERIOD = {"trunc": "day", "startofweek": "week", "startofmonth": "month",
              "startofquarter": "quarter", "startofyear": "year"}
# a grouping aggregate whose last argument is a date bucket: sum(x, startofweek(date))
_GROUPED_RE = re.compile(r",\s*(trunc|startofweek|startofmonth|startofquarter|startofyear)\s*\(\s*date\s*\)\s*\)", re.I)
PERIOD_ZH = {"day": "日", "week": "週", "month": "月", "quarter": "季", "year": "年"}
_TITLE_RE = re.compile(r"^每[日週周月季年]")
MIN_DAYS = {"month": 365, "quarter": 730, "year": 1825}
_LOCK_RE = [re.compile(r"\bshift\s*\(", re.I), re.compile(r"\btl\s*\([^)]*\)\s*\*\s*7\b", re.I)]


def _exprs(chart: dict) -> list[str]:
    return [s.get("expression") or "" for s in chart.get("series", [])]


# WKO5's own charts mostly write the period name: sum(tss, "week")
_NAMED_RE = re.compile(r',\s*"(day|week|month|quarter|year)"\s*\)', re.I)


def chart_period(chart: dict) -> Optional[str]:
    """The bucket this chart totals by, or None when it isn't a period chart
    (or mixes buckets, like WKO5's 周跑量與月跑量)."""
    if chart.get("period") in PERIODS:
        return chart["period"]
    found = set()
    for e in _exprs(chart):
        found |= {_FN_PERIOD[m.group(1).lower()] for m in _GROUPED_RE.finditer(e)}
        found |= {m.group(1).lower() for m in _NAMED_RE.finditer(e)}
    return found.pop() if len(found) == 1 else None


def period_locked(chart: dict) -> bool:
    return any(r.search(e) for e in _exprs(chart) for r in _LOCK_RE)


def retitle(title: Optional[str], period: str) -> Optional[str]:
    if not title:
        return title
    return _TITLE_RE.sub("每" + PERIOD_ZH[period], title, count=1)


def rewrite(expr: str, period: str) -> str:
    """Swap the group-by bucket of every grouping aggregate. Only the
    `sum(x, <bucket>)` position is touched — `if(trunc(date) <= ...)` stays."""
    return _GROUPED_RE.sub(lambda m: ", " + BUCKET_CALL[period] + ")", expr or "")


def with_period(chart: dict, period: str) -> dict:
    """A copy of `chart` bucketed by `period` (the caller checks the lock)."""
    out = copy.deepcopy(chart)
    for s in out.get("series", []):
        s["expression"] = rewrite(s.get("expression") or "", period)
    out["title"] = retitle(chart.get("title"), period)
    out["period"] = period
    return out


def min_days(chart: dict, period: str) -> int:
    floor = MIN_DAYS.get(period, 0)
    if period == chart_period(chart) and chart.get("min_days"):
        floor = max(floor, int(chart["min_days"]))
    return floor


def bucket_start(day: float, period: str) -> float:
    x = day_to_date(day)
    if period == "week":
        x -= dt.timedelta(days=x.weekday())
    elif period == "month":
        x = x.replace(day=1)
    elif period == "quarter":
        x = x.replace(month=(x.month - 1) // 3 * 3 + 1, day=1)
    elif period == "year":
        x = x.replace(month=1, day=1)
    return date_to_day(x)


def _next(x: dt.date, period: str) -> dt.date:
    if period == "day":
        return x + dt.timedelta(days=1)
    if period == "week":
        return x + dt.timedelta(days=7)
    months = {"month": 1, "quarter": 3, "year": 12}[period]
    m = x.month - 1 + months
    return dt.date(x.year + m // 12, m % 12 + 1, 1)


def buckets(begin: float, end: float, period: str) -> list[str]:
    """ISO dates of every bucket start from begin's bucket to end's."""
    x, last = day_to_date(bucket_start(begin, period)), day_to_date(end)
    out = []
    while x <= last and len(out) < 5000:
        out.append(x.isoformat())
        x = _next(x, period)
    return out
