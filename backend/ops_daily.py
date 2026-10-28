"""
每日營運報表的排程與留存 (SP-388 Q1-3, the user 2026-10-20): the app writes the daily ops report
(backend/scripts/ops_report.py) itself, every day after RUN_AT (03:30 local, Asia/Taipei like the
report's day) for the day before, as a file the admin page shows — nothing is pushed anywhere.

  <data folder>/logs/ops/YYYY-MM-DD.md   the report of that day (markdown, the script's text)
  <data folder>/logs/ops/metrics.json    the member signal now, rewritten every METRICS_EVERY_S:
                                         {"members", "cap", "share", "near", "full", "at"} — read by
                                         Telegraf on the NAS (deploy/ops-monitoring/) for the Grafana
                                         alert 「會員數到封頂 80 %」; the app itself sends nothing
  <data folder>/logs/ops/revenue.json    本月訂閱收入 (SP-803, backend/billing/revenue.py), rewritten with it:
                                         {"ok": 1, "charged", "upcoming", "projected", "line", "share",
                                         "near", "over", "at"} in TWD — for the Grafana alert 「月收入接近
                                         NT$50,000」; {"ok": 0, "at"} when accounts.db can't be read (no
                                         amount at all rather than a 0 the alert would read as fine)

Retention: the reports of the last KEEP_DAYS (31) days, like perf.log (applog.PERF_KEEP_DAYS); only
files named like a report are ever deleted.

Run from the scheduler's loop (sync/scheduler.loop, the owner's part): tick() every minute; the
report is due when the time is past RUN_AT and yesterday's file is not there yet (so a server that
was down at 03:30 catches up the first minute it is up that day). Off with the log file
(WKO5COACH_LOG_FILE=0): no folder to write to.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RUN_AT = dt.time(3, 30)                    # 每天 03:30（當地時間）跑前一天 (SP-388 Q1-3)
KEEP_DAYS = 31                             # 留 31 天，跟 perf.log 一樣 (applog.PERF_KEEP_DAYS)
METRICS_EVERY_S = 3600                     # the member signal for Telegraf: hourly is plenty (推估)
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
METRICS = "metrics.json"
REVENUE = "revenue.json"

_last_metrics = {"at": 0.0}


def folder() -> Optional[Path]:
    """<data folder>/logs/ops; None when the log file is off."""
    from backend import applog
    f = applog.log_file()
    return None if f is None else f.parent / "ops"


def _now() -> dt.datetime:
    from backend.scripts import ops_report as R
    return dt.datetime.now(R.TZ)


def report_path(day: dt.date, where: Optional[Path] = None) -> Optional[Path]:
    where = where if where is not None else folder()
    return None if where is None else where / f"{day.isoformat()}.md"


def members() -> Optional[dict]:
    """The member signal now (ops_report.member_signal); None in owner mode (no accounts)."""
    from backend import accounts
    from backend.scripts import ops_report as R
    if not accounts.enabled():
        return None
    from backend.accounts import login, store
    return R.member_signal(store.count_users(), login.max_users())


def revenue(at: Optional[dt.datetime] = None) -> Optional[dict]:
    """本月訂閱收入 as of `at` (default now) against the 免辦稅籍 line (billing/revenue.signal: ok False
    when accounts.db can't be read); None in owner mode (no accounts, nobody pays)."""
    from backend import accounts
    from backend.accounts import store
    from backend.billing import revenue as RV
    if not accounts.enabled():
        return None
    return RV.signal(store.path(), at)


def build(day: dt.date) -> str:
    """The report of `day` as the script writes it (markdown), from the server's own logs."""
    from backend import accounts, applog
    from backend.scripts import ops_report as R
    from backend.sync import events as EV
    pf = applog.perf_file()
    js = R.jobs(R.perf_files(pf.parent)) if pf is not None else []
    db = accounts.accounts_db_path() if accounts.enabled() else None
    r = R.report(day, js, R.events(EV.path()), R.plans(db, day), members(), revenue(R.day_end(day)))
    return R.text(r)


def write(day: dt.date, where: Optional[Path] = None) -> Optional[Path]:
    """Write the report of `day` (atomically: a reader never sees half a file). None when off."""
    path = report_path(day, where)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(build(day), "utf-8")
    os.replace(tmp, path)
    return path


def prune(where: Path, keep_days: int = KEEP_DAYS, today: Optional[dt.date] = None) -> list[str]:
    """Delete the reports of days more than `keep_days` before `today`; only YYYY-MM-DD.md files."""
    today = today or _now().date()
    cut = today - dt.timedelta(days=keep_days)
    gone = []
    try:
        names = sorted(p.name for p in where.iterdir() if p.is_file())
    except OSError:
        return gone
    for name in names:
        m = NAME_RE.match(name)
        if not m:
            continue
        try:
            day = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if day < cut:
            try:
                (where / name).unlink()
                gone.append(name)
            except OSError:
                pass
    return gone


def reports(where: Optional[Path] = None) -> list[dict]:
    """[{day, bytes}] of the reports there, newest first."""
    where = where if where is not None else folder()
    if where is None or not where.is_dir():
        return []
    out = []
    for p in where.iterdir():
        m = NAME_RE.match(p.name)
        if m and p.is_file():
            out.append({"day": m.group(1), "bytes": p.stat().st_size})
    return sorted(out, key=lambda r: r["day"], reverse=True)


def read(day: str, where: Optional[Path] = None) -> Optional[str]:
    """One report's text; None when there is none (or `day` is not a date)."""
    try:
        d = dt.date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    path = report_path(d, where)
    if path is None or not path.is_file():
        return None
    return path.read_text("utf-8", errors="replace")


def write_metrics(where: Optional[Path] = None, now: Optional[float] = None) -> Optional[Path]:
    """metrics.json for Telegraf: the member signal now (nothing when owner mode or the log is off)."""
    where = where if where is not None else folder()
    m = members()
    if where is None or m is None:
        return None
    where.mkdir(parents=True, exist_ok=True)
    path = where / METRICS
    tmp = where / ("." + METRICS + ".tmp")
    body = {"members": m["count"], "cap": m["cap"], "share": m["share"], "near": int(m["near"]),
            "full": int(m["full"]), "at": int(now if now is not None else time.time())}
    tmp.write_text(json.dumps(body), "utf-8")
    os.replace(tmp, path)
    return path


def write_revenue(where: Optional[Path] = None, now: Optional[float] = None) -> Optional[Path]:
    """revenue.json for Telegraf: this month's subscription income now (SP-803); nothing in owner mode
    or with the log off. Can't read accounts.db: {"ok": 0, "at"} and no amount (never a 0)."""
    where = where if where is not None else folder()
    rv = revenue()
    if where is None or rv is None:
        return None
    where.mkdir(parents=True, exist_ok=True)
    at = int(now if now is not None else time.time())
    body = {"ok": 0, "at": at}
    if rv.get("ok"):
        body = {"ok": 1, "charged": rv["charged"], "upcoming": rv["upcoming"], "projected": rv["projected"],
                "line": rv["line"], "share": rv["share"], "near": int(rv["near"]), "over": int(rv["over"]), "at": at}
    path = where / REVENUE
    tmp = where / ("." + REVENUE + ".tmp")
    tmp.write_text(json.dumps(body), "utf-8")
    os.replace(tmp, path)
    return path


def due(now: Optional[dt.datetime] = None, where: Optional[Path] = None) -> Optional[dt.date]:
    """Yesterday when its report is due (past RUN_AT, not written yet); None otherwise."""
    now = now or _now()
    if now.time() < RUN_AT:
        return None
    day = now.date() - dt.timedelta(days=1)
    path = report_path(day, where)
    if path is None or path.exists():
        return None
    return day


def run_due(now: Optional[dt.datetime] = None, where: Optional[Path] = None) -> Optional[Path]:
    """The scheduler's minute: the member signal hourly; yesterday's report when due, then the
    retention. Returns the report written, if any."""
    t = time.time()
    if t - _last_metrics["at"] >= METRICS_EVERY_S:
        _last_metrics["at"] = t
        for what, fn in (("metrics", write_metrics), ("revenue", write_revenue)):
            try:
                fn(where)
            except Exception as e:                   # noqa: BLE001 — the report still goes
                log.warning("ops %s not written: %s", what, type(e).__name__)
    day = due(now, where)
    if day is None:
        return None
    path = write(day, where)
    if path is not None:
        prune(path.parent, today=(now or _now()).date())
    return path


async def tick() -> Optional[Path]:
    """From the scheduler's loop (in a thread: reading 31 days of perf.log is blocking work)."""
    import asyncio
    return await asyncio.to_thread(run_due)
