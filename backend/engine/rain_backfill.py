"""
The one-time backfill of the rain over the last 12 months (SP-299 follow-up, owner 2026-10-07).

The activities' archive weather (route_weather.fill_activities → activity_weather.json, the
disk cache <routes>/weather/) was fetched before `precipitation` joined route_weather.HOURLY,
so those activities have no rain and never get the rain hint of 活動編輯
(activity_tags.rain_hint). A build never refetches a cached day; this job does, once:

  * the activities of activity_weather.json in the last DAYS days whose rain is unknown;
  * their archive days (the same point and (cell, day) grouping as the build,
    route_weather.activity_point) asked again through route_weather.Fetcher — only the
    cached points without precipitation (`refetch`), one call at a time with PACE_S between
    calls (Open-Meteo allows 600 calls / min; ~194 calls for the owner's year);
  * each cache entry gains only the precipitation as its call lands (`merge`: the temperature /
    humidity / dew point a build used stay as they were; an interrupted run keeps what it
    fetched — the next run asks only for what is still missing), then the rain written into
    activity_weather.json; nothing else in that file changes.

It runs in the routes Builder's slot (api/rain_backfill.py → routes.Builder.start_task), so no
routes build writes the same cache files meanwhile; a `stop` event (the app quitting) ends it
before the next call.

Idempotent: a day once fetched with precipitation is never asked again. The scheduler starts it
(api/rain_backfill.py) and marks it done in the setting `weather.rain_backfill`; never in tests
or the demo. Pure I/O on the routes folder: the HTTP client and the tracks are passed in.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from backend.engine import route_weather as RW

DAYS = 365                       # 「近 12 個月」 (the ticket)
PACE_S = 1.0                     # between calls: ≤ 60 calls / min, a tenth of Open-Meteo's limit
WORKERS = 1                      # one call at a time


def _start_date(v: dict) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(v.get("start") or v.get("date") or "")[:10])
    except ValueError:
        return None


def run(root: Path, track_of: Callable[[str], Any], get: Callable, today: dt.date,
        pace_s: float = PACE_S, sleep: Optional[Callable[[float], None]] = None,
        stop: Optional[threading.Event] = None) -> dict:
    """Backfill the rain of the routes folder `root`. `track_of(file)` → the routes.Track of an
    activity (None = no track); `get` = the archive client (racepower/weather._http_get).
    → {activities, no_track, calls, failed, skipped, filled, complete, stopped, errors}
    ({no_doc: True} while there is no activity_weather.json yet). `complete` = nothing failed or
    was skipped. `stop` set (the app is quitting): no further call, the pause cut short, nothing
    written to activity_weather.json — what was fetched is in the cache, so the next run only
    computes it (resumable)."""
    root = Path(root)
    doc = RW.load_activity_weather(root)
    if not doc:
        return {"no_doc": True, "complete": False}
    since = today - dt.timedelta(days=DAYS)
    need: dict = {}
    todo, no_track = [], 0
    for f, v in sorted((doc.get("activities") or {}).items()):
        if not isinstance(v, dict) or v.get("rain_mm") is not None:
            continue
        d = _start_date(v)
        if d is None or not since <= d <= today:
            continue
        try:
            ap = RW.activity_point(track_of(f))
        except Exception:                # noqa: BLE001 — an unreadable track: skipped
            ap = None
        if ap is None:
            no_track += 1
            continue
        a, b, pt, keys = ap
        for k in keys:
            need.setdefault(k, set()).add(pt)
        todo.append((f, a, b, pt, keys))
    fch = RW.Fetcher(root / "weather", get, today, refetch=lambda js: not RW.has_rain(js),
                     workers=WORKERS, pace_s=pace_s, sleep=sleep, merge=True, stop=stop)
    got = fch.fetch_all(need) if need else {}
    st = fch.stats
    if stop is not None and stop.is_set():
        return {"activities": len(todo), "no_track": no_track, "calls": st["calls"], "failed": st["failed"],
                "skipped": st["skipped"], "filled": 0, "complete": False, "stopped": True, "errors": fch.errors}
    rain = {}
    for f, a, b, pt, keys in todo:
        days = [got[(*k, pt)] for k in keys if (*k, pt) in got]
        if len(days) == len(keys):
            r = RW.activity_rain(days, a, b)
            if r is not None:
                rain[f] = r
    return {"activities": len(todo), "no_track": no_track, "calls": st["calls"], "failed": st["failed"],
            "skipped": st["skipped"], "filled": _write_rain(root, rain) if rain else 0,
            "complete": not (st["failed"] or st["skipped"]), "errors": fch.errors}


def _write_rain(root: Path, rain: dict) -> int:
    """Set `rain_mm` on the activity_weather.json rows still without it (re-read just before the
    write, so a build that saved it meanwhile is kept). → rows written."""
    doc = RW.load_activity_weather(root)
    acts = doc.get("activities") or {}
    n = 0
    for f, r in rain.items():
        e = acts.get(f)
        if isinstance(e, dict) and e.get("rain_mm") is None:
            e["rain_mm"] = r
            n += 1
    if n:
        RW.write_atomic(root / RW.ACTIVITY_WX_FILE, json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    return n
