"""
The charts drawn lately, drawn again on the new data after a sync (SP-499).

A chart request (api/wko5views.chart) keeps, with its render-cache slot (render_cache.slot_put), how
to ask for it again: the view / dashboard / chart, its query parameters, its range as days before
and after today (only a range that reaches today: an older one never changes with new data) and
the locale. After a sync brought new activities, the warm-up's low-priority thread
(wko5views._low_priority) calls after_sync: once the activity classification's hot tier is done
(the recent days the charts read), one job of the server's queue (backend/jobs.py: `single`,
`cold` — after every page someone waits for and every tenant's warm-up) draws the PRERENDER_MAX
slots drawn most recently, within PRERENDER_DAYS, through the chart endpoint itself, so the
cache key is exactly the one the page will ask for. The page then finds them in the cache: no
「更新中」 drawing, no wait.

In the server's process (a chart is ~0.05–0.5 s locally; the queue runs one such job per tenant
and two at most), never for a demo tenant, and never for a plan whose charts are counted per day
(membership quota "charts": drawing on its behalf would use it up).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
from typing import Optional

log = logging.getLogger(__name__)

PRERENDER_MAX = 12          # charts per sync (推估: a dashboard or two the person opens)
PRERENDER_DAYS = 14         # slots drawn within this many days
WAIT_S = 600.0              # the classification's hot tier is waited for at most this long


def recent_slots(now: Optional[dt.datetime] = None) -> list[dict]:
    """The replays of this tenant's slots drawn within PRERENDER_DAYS, the newest first (at most
    PRERENDER_MAX; one per distinct replay)."""
    from backend.api import wko5views as WV
    from backend.engine.wko5expr.render_cache import SLOTS_DIR
    now = now or dt.datetime.now()
    found = []
    root = WV.RENDER_CACHE.root / SLOTS_DIR
    try:
        files = list(root.glob("*.json"))
    except OSError:
        return []
    for p in files:
        try:
            d = json.loads(p.read_text("utf-8"))
            at = dt.datetime.fromisoformat(d["at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        rp = d.get("replay")
        if isinstance(rp, dict) and (now - at).total_seconds() <= PRERENDER_DAYS * 86400:
            found.append((at, rp))
    out, seen = [], set()
    for _at, rp in sorted(found, key=lambda x: x[0], reverse=True):
        k = json.dumps(rp, sort_keys=True, default=str)
        if k not in seen:
            seen.add(k)
            out.append(rp)
        if len(out) >= PRERENDER_MAX:
            break
    return out


def _bool(v) -> Optional[bool]:
    if v is None:
        return None
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def draw(rp: dict) -> None:
    """Ask for one chart again, its range moved to today (as the page will ask for it)."""
    from urllib.parse import urlencode
    from starlette.requests import Request
    from backend.api import wko5views as WV
    from backend.engine.wko5expr.dataset import day_to_date
    from backend.i18n import use_locale
    q = dict(rp.get("q") or {})
    ds = WV._dataset(_bool(q.get("parity")))
    today = math.floor(ds.today)
    q["begin"] = day_to_date(int(round(today - float(rp["back"])))).isoformat()
    q["end"] = day_to_date(int(round(today + float(rp["ahead"])))).isoformat()
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                   "query_string": urlencode(q).encode()})
    with use_locale(rp.get("locale")):
        WV.chart(req, str(rp["view"]), int(rp["d"]), int(rp["c"]), begin=q["begin"], end=q["end"],
                 sports=q.get("sports"), workout=None, parity=_bool(q.get("parity")))


def run() -> dict:
    """Draw this tenant's recent charts: {drawn, failed}. A failure only counts."""
    n = {"drawn": 0, "failed": 0}
    for rp in recent_slots():
        try:
            draw(rp)
            n["drawn"] += 1
        except Exception as e:                   # noqa: BLE001 — the page draws it then
            n["failed"] += 1
            log.info("chart pre-render skipped one: %s", type(e).__name__)
    return n


def wanted() -> bool:
    """This tenant's charts may be drawn ahead: not a demo, charts not counted per day."""
    from backend import membership as MS, tenancy
    t = tenancy.current()
    if t.is_demo or tenancy.demo_mode():
        return False
    try:
        return MS.quota_for("charts") is None
    except Exception:                            # noqa: BLE001
        return False


def after_sync(job=None):
    """The warm-up's low-priority thread, after a sync with new activities: wait (holding no
    place) for the classification `job`'s hot tier, then queue run() — the queued job, or None."""
    if not wanted():
        return None
    wait_for = getattr(job, "wait_for", None)
    if wait_for is not None:
        wait_for("hot", WAIT_S)
    from backend import jobs, tenancy
    tid = tenancy.current().id
    return jobs.submit(lambda _job: run(), cls="single", priority="cold", label="chart pre-render (sync)",
                       key=("prerender", tid))
