"""
運動紀錄 archive (SP-317): FIT originals older than the tenant's retention (default 3 years) are
removed once a month; what the long-term charts need stays in the cold summaries (SP-340,
wko5expr/coldstore.py). No preview and no confirmation (owner 2026-10-07): the archive just runs.

    cutoff_for(today, years)        the first day still kept (activities before it go)
    tick(now)                       the scheduler's per-tenant check (sync/scheduler.py loop): on
                                    the 1st of the month, from 03:00 + the tenant's slot (local
                                    time), queue run() through jobs.submit_archive — once a month
    run(cutoff)                     the job: coldstore.prepare_archive (freeze the summaries) →
                                    delete the FITs it names → one rebuild of the derived caches
    last_run() / log_entries()      what the settings page shows (最近一次清理)

Safety, in order (a deleted FIT cannot come back unless the user still has it elsewhere):

* demo tenants never archive (tick and run both refuse); the job runs inside its tenant
  (jobs.submit copies the contextvars) and only on the 資料來源's FIT folder of that tenant
  (sync/storage.source_dir: <tenant shared>/fit/<folder>); the Dataset must be that folder's;
* prepare_archive first — `NotComplete` (a first import still back-filled) deletes nothing and
  the tick tries again RETRY_S later the same day; any other failure deletes nothing either;
* only the paths prepare_archive returns in `files` / `left_out` (`failed` ones, without a
  summary, stay), each through storage.confined (resolved, no symlink on the way, strictly inside
  that tenant's source folder) and only a regular file;
* activities the current thresholds or the ongoing plan's tests point at (`referenced`) are kept
  and named in the log; they are checked again next month (a newer test frees them);
* never fit_objects.deleted (that means "the user deleted it" and takes the summary with it); the
  workout_files rows stay, so a sync does not download the activity again.

One batch = one rebuild: the files are deleted, the FIT index saved once, datasource.files_changed
once and one warm-up queued (SP-340 keeps the hot / warm chart keys, so only the Dataset object is
new). A rerun finds nothing before the cutoff and changes nothing (idempotent).

The log (`archive_log.json`, tenant private folder; data_registry) keeps the last LOG_KEEP runs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

RETENTION_KEY = "archive.retention_years"
DEFAULT_YEARS = 3
CHOICES = (2, 3, 5, 10)          # years; the floor (2) leaves the 365-day charts and their warm-up whole
MIN_YEARS = CHOICES[0]
WINDOW_START = dt.time(3, 0)     # local time on the 1st of the month
WINDOW_MIN = 120                 # tenants spread over 03:00–05:00
RETRY_S = 3600                   # after a skipped / failed run (the same day only)
LOG_FILE = "archive_log.json"
LOG_KEEP = 24
LOG_PATH: Optional[Path] = None  # fixed file (tests); None = the tenant's private archive_log.json
DAY_TOL_MIN = 3                  # activity_key.MATCH_TOL_MIN: a done_by start matches within ±3 min


def validate_years(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v not in CHOICES:
        raise ValueError(f"{RETENTION_KEY} must be one of {CHOICES}")
    return v


def cutoff_for(today: dt.date, years: int) -> dt.date:
    """`years` before `today` (29 Feb → 28 Feb): activities that started before it are archived."""
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def slot_minutes(tenant_id: str) -> int:
    """The tenant's start within the window: 0 for the owner, else a fixed 0..WINDOW_MIN-1 from a
    hash of its id (SP-338's way: the same every month)."""
    if tenant_id == "owner":
        return 0
    return int(hashlib.sha256(("archive:" + tenant_id).encode("utf-8")).hexdigest(), 16) % WINDOW_MIN


# ---------------------------------------------------------------------------
# the log
# ---------------------------------------------------------------------------

_LOG_LOCK = threading.Lock()


def log_path() -> Path:
    if LOG_PATH is not None:
        return Path(LOG_PATH)
    from backend import tenancy
    return tenancy.private_path(LOG_FILE)


def _read_log() -> dict:
    try:
        doc = json.loads(log_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {"runs": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("runs"), list):
        return {"runs": []}
    return doc


def _write_log(doc: dict) -> None:
    p = log_path()
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), "utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.warning("archive log not written (%s)", type(e).__name__)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _record(entry: dict, month: Optional[str] = None) -> None:
    with _LOG_LOCK:
        doc = _read_log()
        doc["runs"] = (doc["runs"] + [entry])[-LOG_KEEP:]
        if month:
            doc["tried"] = {"month": month, "at": entry["at"], "status": entry["status"]}
            if entry["status"] == "ok":
                doc["done_month"] = month
        _write_log(doc)


def log_entries() -> list:
    return list(_read_log()["runs"])


def last_run() -> Optional[dict]:
    """The newest run that got as far as deleting (status ok), else the newest one."""
    runs = log_entries()
    ok = [r for r in runs if r.get("status") == "ok"]
    return (ok or runs or [None])[-1]


# ---------------------------------------------------------------------------
# what must not go yet
# ---------------------------------------------------------------------------

def referenced(ds, cutoff: dt.date, today: dt.date, plan=None, tests=None) -> dict:
    """{relative file: reason} of the activities before `cutoff` that the current thresholds or the
    ongoing plan's tests point at (SP-317: 先重算再刪 — a stored threshold or a test result read
    from its activity's per-second data cannot be computed again from a summary, so the FIT
    stays and the log names it; next month checks again).

    * the threshold rows in effect today (plan.json: LTHR, AeT, max HR, CP) that were measured —
      a test / race / lab / typed value, not an applied estimate: the activities of that day;
    * the plan's test sessions that are not over (state active, or a day on / after the cutoff)
      whose done_by points at an activity: that activity (±3 min of its start)."""
    from backend.engine import planning
    from backend.engine.wko5expr.dataset import date_to_day
    cut = date_to_day(cutoff)
    old = [w for w in ds.workouts if w.day < cut]
    if not old:
        return {}
    out: dict = {}
    if plan is None:
        try:
            plan = planning.Plan.load()
        except Exception as e:                     # noqa: BLE001 — unreadable: protect nothing by it
            log.warning("archive: plan unreadable (%s)", type(e).__name__)
            plan = None
    if plan is not None:
        for name in ("lthr", "aethr", "mhr", "cp"):
            try:
                row = planning.threshold_row(plan, name, today)
            except Exception:                      # noqa: BLE001
                row = None
            if not row or not row.get("measured"):
                continue
            try:
                d = dt.date.fromisoformat(str(row["date"])[:10])
            except ValueError:
                continue
            for w in old:
                if w.entry.start.date() == d:
                    out.setdefault(w.entry.file, f"threshold:{name}")
    if tests is None:
        try:
            from backend.engine import plan_store
            tests = plan_store.test_sessions()
        except Exception as e:                     # noqa: BLE001
            log.warning("archive: plan tests unreadable (%s)", type(e).__name__)
            tests = []
    for s in tests or []:
        db_ = s.get("done_by")
        if not isinstance(db_, dict) or not db_.get("start"):
            continue
        try:
            day = dt.date.fromisoformat(str(s.get("day"))[:10])
        except ValueError:
            day = None
        if s.get("state") != "active" and (day is None or day < cutoff):
            continue                               # a test of a plan long over
        try:
            start = dt.datetime.fromisoformat(str(db_["start"]))
        except ValueError:
            continue
        start = start.replace(tzinfo=None)
        for w in old:
            if abs((w.entry.start.replace(tzinfo=None) - start).total_seconds()) <= DAY_TOL_MIN * 60:
                out.setdefault(w.entry.file, "plan_test")
    return out


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------

class Refused(RuntimeError):
    """Not a tenant / folder the archive may touch: nothing deleted."""


def _rebuild() -> None:
    """The one rebuild of a batch: the next Dataset is built (the warm-up) on the new files."""
    try:
        from backend.api import wko5views
        wko5views.warm_up("archive")
    except Exception as e:                         # noqa: BLE001 — the next page builds it then
        log.warning("archive: warm-up not started (%s)", type(e).__name__)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(cutoff: dt.date, *, ds=None, folder: Optional[str] = None, today: Optional[dt.date] = None,
        years: Optional[int] = None, month: Optional[str] = None, job=None,
        plan=None, tests=None) -> dict:
    """Archive the current tenant's FITs before `cutoff` (module doc). Returns the log entry:
    {at, month, cutoff, years, status ok | skipped | failed, deleted, bytes, npz_bytes, first, last,
    kept_failed, protected [{file, reason}], refused, missing, reason?}. Raises nothing for a
    skipped / failed run: the entry says why (and nothing was deleted)."""
    from backend import tenancy
    from backend.engine.wko5expr import coldstore, datasource
    from backend.sync import storage
    t = tenancy.current()
    entry = {"at": _now_iso(), "month": month, "cutoff": cutoff.isoformat(), "years": years, "status": "ok",
             "deleted": 0, "bytes": 0, "npz_bytes": 0, "first": None, "last": None, "kept_failed": 0,
             "protected": [], "refused": 0, "missing": 0}
    t0 = time.perf_counter()
    try:
        if t.is_demo or tenancy.demo_mode():
            raise Refused("demo")
        folder = folder or datasource.primary_folder()
        src = storage.source_dir(folder)
        if ds is None:
            from backend.api.wko5views import _dataset
            ds = _dataset(source=folder)
        if getattr(ds, "_store", None) is None or not src.exists() \
                or Path(ds.dir).resolve() != src.resolve():
            raise Refused("not this tenant's FIT folder")
        prep = coldstore.prepare_archive(cutoff, ds=ds)
    except coldstore.NotComplete:
        entry.update(status="skipped", reason="backfill")
        _record(entry, month)
        return entry
    except Refused as e:
        entry.update(status="skipped", reason=str(e))
        if not (t.is_demo or tenancy.demo_mode()):
            _record(entry, month)
        return entry
    except Exception as e:                         # noqa: BLE001 — nothing deleted; retried later
        log.warning("archive: prepare failed (%s)", type(e).__name__, exc_info=True)
        entry.update(status="failed", reason=type(e).__name__)
        _record(entry, month)
        return entry

    base = Path(ds.dir)
    when = {w.entry.file: w.entry.start.date() for w in ds.workouts}
    try:
        for x in coldstore.ColdStore(ds.dir).load_archive().get("left_out") or []:
            if isinstance(x, dict) and x.get("file") and x.get("start"):
                when.setdefault(x["file"], dt.datetime.fromisoformat(x["start"]).date())
    except (OSError, ValueError, TypeError):
        pass
    try:
        keep = referenced(ds, cutoff, today or dt.date.today(), plan=plan, tests=tests)
    except Exception as e:                         # noqa: BLE001 — unknown references: delete nothing
        log.warning("archive: references unreadable (%s)", type(e).__name__, exc_info=True)
        entry.update(status="failed", reason=type(e).__name__)
        _record(entry, month)
        return entry
    entry["kept_failed"] = len(prep.get("failed") or [])
    paths = list(prep.get("files") or []) + list(prep.get("left_out") or [])
    days = []
    for i, p in enumerate(paths):
        if job is not None:
            job.progress = {"done": i, "total": len(paths)}
        try:
            rel = Path(p).relative_to(base).as_posix()
        except ValueError:
            entry["refused"] += 1
            continue
        if rel in keep:
            entry["protected"].append({"file": rel, "reason": keep[rel],
                                       "date": when[rel].isoformat() if rel in when else None})
            continue
        try:
            rp = storage.confined(p, folder)
        except storage.PathNotConfined:
            entry["refused"] += 1
            log.warning("archive: a path outside the tenant's %s folder was refused", folder)
            continue
        if not rp.is_file():
            entry["missing"] += 1
            continue
        size = rp.stat().st_size
        npz = ds._store.npz(rel)
        try:
            nb = npz.stat().st_size
        except OSError:
            nb = 0
        rp.unlink()
        if ds._store.drop_npz(rel):
            entry["npz_bytes"] += nb
        try:
            if rp.parent != src.resolve():
                rp.parent.rmdir()                  # an empty shard / year folder
        except OSError:
            pass
        entry["deleted"] += 1
        entry["bytes"] += size
        if rel in when:
            days.append(when[rel])
    if days:
        entry["first"], entry["last"] = min(days).isoformat(), max(days).isoformat()
    if entry["deleted"]:
        ds._store.save()
        datasource.files_changed()
        _rebuild()
    entry["seconds"] = round(time.perf_counter() - t0, 2)
    _record(entry, month)
    log.warning("archive before %s: %d files deleted (%d bytes), %d protected, %d without a summary kept, "
                "%d refused", entry["cutoff"], entry["deleted"], entry["bytes"], len(entry["protected"]),
                entry["kept_failed"], entry["refused"])
    return entry


# ---------------------------------------------------------------------------
# the schedule
# ---------------------------------------------------------------------------

def _years() -> int:
    from backend.engine.wko5expr import datasource
    v = datasource.read_setting(RETENTION_KEY, DEFAULT_YEARS)
    return v if isinstance(v, int) and not isinstance(v, bool) and v in CHOICES else DEFAULT_YEARS


def _tz():
    from backend.engine.wko5expr import datasource
    return datasource.athlete_tz()


def window_start(local: dt.datetime, tenant_id: str) -> dt.datetime:
    """This month's start for the tenant: the 1st, 03:00 + its slot (local)."""
    first = local.replace(day=1, hour=WINDOW_START.hour, minute=WINDOW_START.minute, second=0, microsecond=0)
    return first + dt.timedelta(minutes=slot_minutes(tenant_id))


def next_run(now: Optional[dt.datetime] = None, tenant_id: Optional[str] = None) -> dt.datetime:
    """The next scheduled start (local, aware) — this month's if it is still ahead and not done."""
    from backend import tenancy
    tid = tenant_id or tenancy.current().id
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(_tz())
    start = window_start(local, tid)
    if local < start and _read_log().get("done_month") != local.strftime("%Y-%m"):
        return start
    nxt = (local.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return window_start(nxt, tid)


def due(now: Optional[dt.datetime] = None) -> Optional[dict]:
    """{month, cutoff, years} when the current tenant's monthly run should start now, else None:
    the 1st of the month (local), at / after its start, not done this month, and not tried within
    RETRY_S. A day 1 missed entirely (the app was down) waits for next month."""
    from backend import tenancy
    t = tenancy.current()
    if t.is_demo or tenancy.demo_mode():
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    utc = now.astimezone(dt.timezone.utc)
    if utc.day != 1 and (utc + dt.timedelta(days=1)).day != 1:
        return None                                # no zone puts this moment on a 1st: no DB read
    local = now.astimezone(_tz())
    if local.day != 1 or local < window_start(local, t.id):
        return None
    month = local.strftime("%Y-%m")
    doc = _read_log()
    if doc.get("done_month") == month:
        return None
    tried = doc.get("tried") or {}
    if tried.get("month") == month:
        try:
            at = dt.datetime.fromisoformat(tried["at"])
            if (now - at).total_seconds() < RETRY_S:
                return None
        except (KeyError, TypeError, ValueError):
            pass
    years = _years()
    return {"month": month, "years": years, "cutoff": cutoff_for(local.date(), years), "today": local.date()}


def tick(now: Optional[dt.datetime] = None, submit: Optional[Callable] = None):
    """The scheduler's check of the current tenant: queue this month's archive when due (one
    job per tenant and month: key ("archive", tenant, month)). The job, or None."""
    from backend import jobs, tenancy
    d = due(now)
    if d is None:
        return None
    tid = tenancy.current().id
    submit = submit or jobs.submit_archive

    def body(job):
        try:
            return run(d["cutoff"], today=d["today"], years=d["years"], month=d["month"], job=job)
        except Exception as e:                     # noqa: BLE001 — logged so the tick waits RETRY_S
            _record({"at": _now_iso(), "month": d["month"], "cutoff": d["cutoff"].isoformat(),
                     "status": "failed", "reason": type(e).__name__, "deleted": 0}, d["month"])
            raise
    return submit(body, label=f"monthly archive {d['month']}", key=("archive", tid, d["month"]), tenant=tid)


async def tick_async(now: Optional[dt.datetime] = None):
    return tick(now)
