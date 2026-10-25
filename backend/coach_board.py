"""
The coach's dashboard summary of one student (SP-790; SP-56 C: the board is a read-only summary,
代看 is SP-791). One small JSON per student, in the student's own tenant
(data_registry: cache/coach_summary.json, DERIVED), computed ahead — never when the coach opens
the board: a board of N students would otherwise build N Datasets (api/wko5views.py: a Dataset is
50 MB + 0.18 MB per activity against a 1.2 GB budget, and the NAS is 8–9.5× slower than a laptop).

What it holds (build): the last 7 / 28 days' volume (sessions, moving time, distance, climb, TSS —
engine/overview._totals, the overview's own sums), today's CTL / ATL / TSB (engine/overview.pmc,
the same started PMC as the overview; `pending` during a first import's back-fill), the next race
of the season plan, the plan's completion (stored sessions: done / (done + missed) over the last 28
days, this week's done of planned), the newest activity's date and the overview status's red /
yellow indicators (their titles in the language they were computed in; shown in the coach's by
title_in()).

When it is computed:
  - after every Dataset warm-up of a coached student (api/wko5views.warm_up → after_warm_up): after
    a sync with new activities, after a deploy / start, after the student changed something — the
    Dataset and the overview status are in memory then, so it costs a few sums;
  - right after the student (or their coach, SP-792) changed a race, the phases or the 課表 (SP-826:
    tenancy_mw calls mark_stale after a 2xx write that changes_plan): the summary is marked `stale_at`
    (the board shows 更新中) and a job is queued at once (`warm`) — never computed in the request;
  - tick() (sync/scheduler.loop, every minute): a coached student with no summary yet (just
    accepted a coach) gets one soon (`warm`), a stale one too (a change made while its job ran: that
    job kept the mark); a summary of an earlier day is computed again once a
    night, from NIGHT_AT local + a per-tenant slot (`cold`) — each a job of the server's queue
    (backend/jobs.py: one whole-Dataset job at a time), never twice for one tenant (its key).
Only students with an active link (store.coached_student_ids): nobody else's summary is computed.

The board (api/coach_board.py) reads the file through coaching.in_student — 403 unless the link
is active now — and adds what changes by the minute: the last sync (the student's settings, read
only), the race countdown, the alerts in the coach's language (board_row).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Callable, Optional

from backend import tenancy
from backend.i18n import N_, _

log = logging.getLogger(__name__)

FILE = ("cache", "coach_summary.json")
VERSION = 1
NIGHT_AT = (4, 0)                 # the nightly refresh: 04:00 local … (after the free tier's 01:00–05:00 syncs start)
NIGHT_SPAN_MIN = 60               # … + 0–59 min by tenant
RETRY_S = 3600                    # a failed computation is tried again after this
SYNC_WATCH_DAYS, SYNC_BAD_DAYS = 7, 14      # no sync for this long: yellow / red (推估: a week without data)
PLAN_LOW_RATE, PLAN_LOW_MIN_DUE = 0.6, 4    # ≥ 4 sessions due in 28 days and < 60 % done: yellow (推估)
QUIET_DAYS = 7                    # no activity in the last 7 days: yellow
ALERT_LEVELS = ("bad", "watch")
SKIP_KINDS = ("notice", "heat_passive")     # engine/compliance.SKIP_KINDS: not sessions to do
STUDENT_VIEW_URL: Optional[str] = "/api/v1/coach/view/{id}"   # SP-791 (代看): POSTed by the board, -> {"go"}

ALERT_TEXT = {
    "no_sync": N_("{days} 天沒有同步"),
    "never_synced": N_("還沒有同步過"),
    "quiet": N_("近 7 天沒有活動"),
    "plan_low": N_("近 28 天課表完成 {pct}%"),
}

_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()     # the summary file's read-modify-write (mark_stale vs a job storing)
_FAILED: dict = {}                # tenant id -> monotonic time of the last failed computation
PRIORITY = {"missing": "warm", "changed": "warm", "night": "cold"}

# SP-826: the writes that change what a summary shows of the plan — the season plan's races (and their
# GPX) and phases, the stored 課表 (sessions, tests and suggestions scheduled, reconciling, the automatic
# adjustments, 不排課日, rest days, the 排課偏好). A 2xx one marks the summary stale. Not: what stores
# nothing (previews, suggest, steps), the templates, the COROS push, the thresholds (SP-826 is races and plan)
_P = "/api/v1/overview/plan"
PLAN_WRITES = re.compile("(?:" + "|".join((
    r"/api/v1/plan/events(?:/[^/]+(?:/gpx(?:/splits|/group|/cairn)?)?)?",
    r"/api/v1/plan/phases",
    _P + r"/sessions(?:/swap|/expired/delete|/(?!suggest$)[^/]+(?:/revert|/link)?)?",
    _P + r"/(?:test-suggestions/schedule|suggestions/accept|reconcile|prefs|blackouts|rest-days(?:/[^/]+)?)",
    _P + r"/auto/(?:run|proposal/[^/]+/approve|log/[^/]+/undo)",
)) + ")")
WRITE_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))


# ---------------------------------------------------------------------------- the file

def path():
    return tenancy.private_path(*FILE)


def read() -> Optional[dict]:
    """The current tenant's summary, None when there is none (or an older version)."""
    try:
        d = json.loads(path().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("v") == VERSION else None


def write(s: dict) -> None:
    p = path()
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(s, ensure_ascii=False, default=str), "utf-8")
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------- who is coached

def _uid(t) -> Optional[int]:
    """The account of a tenant: the owner's is user 1, a user's "u<id>"; None for anything else."""
    if t.kind == tenancy.OWNER:
        return 1
    if t.kind == tenancy.USER and t.id.startswith("u") and t.id[1:].isdigit():
        return int(t.id[1:])
    return None


def coached(t=None) -> bool:
    """The tenant's account is someone's active student now (multi-user mode only)."""
    from backend import accounts
    t = t or tenancy.current()
    if not accounts.enabled() or t.is_demo:
        return False
    uid = _uid(t)
    if uid is None:
        return False
    from backend.accounts import store
    return bool(store.coached_student_ids(uid))


# ---------------------------------------------------------------------------- computing it

def _db():
    """The current tenant's app DB (read only here), None when it is not there."""
    from backend.db.database import db_path
    p = db_path()
    return p if p.exists() else None


def _round(d: dict) -> dict:
    return {"sessions": int(d.get("sessions") or 0), "moving_s": round(float(d.get("moving_s") or 0.0)),
            "distance_km": round(float(d.get("distance_km") or 0.0), 1),
            "climbing_m": round(float(d.get("climbing_m") or 0.0)), "tss": round(float(d.get("tss") or 0.0))}


def volume(ds, today: dt.date, days: int) -> dict:
    """The last `days` days up to today (all sports together): the overview's sums."""
    from backend.engine import overview as O
    return _round(O._totals(O.workouts_between(ds, today - dt.timedelta(days=days - 1), today + dt.timedelta(days=1))))


def load(ds, today: dt.date) -> Optional[dict]:
    """Today's CTL / ATL / TSB (the overview's PMC); {"pending": True} while a first import's older
    activities are still being added (CTL would read too low)."""
    from backend.api import backfill as BF
    from backend.engine import overview as O
    if BF.coverage(ds) is not None:
        return {"pending": True}
    rows = O.pmc(ds, today, today).get("series") or []
    if not rows:
        return None
    r = rows[-1]
    return {k: None if r.get(k) is None else round(float(r[k]), 1) for k in ("ctl", "atl", "tsb")}


def next_race(today: dt.date) -> Optional[dict]:
    """The season plan's next event (one still going counts): name, first day, priority."""
    from backend.engine.planning import Plan
    best = None
    for e in Plan.load().events:
        try:
            first = dt.date.fromisoformat(str(e.date)[:10])
        except ValueError:
            continue
        last = first + dt.timedelta(days=max(1, int(e.days or 1)) - 1)
        if last < today:
            continue
        if best is None or first < best[0]:
            best = (first, e)
    if best is None:
        return None
    first, e = best
    return {"name": e.name, "date": first.isoformat(), "days": int(e.days or 1), "priority": e.priority,
            "kind": e.kind, "distance_km": e.distance_km, "climbing_m": e.climbing_m}


def plan_completion(today: dt.date) -> Optional[dict]:
    """The stored plan (plan_sessions, as the 課表 reconciles it): `d28` done / (done + missed) of the
    28 days before today; `week` this week's done of every session planned in it. None without a DB."""
    db = _db()
    if db is None:
        return None
    lo = (today - dt.timedelta(days=28)).isoformat()
    mon = today - dt.timedelta(days=today.weekday())
    sun = mon + dt.timedelta(days=6)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT day, kind, state FROM plan_sessions WHERE athlete_id = 1 AND day >= ? AND day <= ? "
                "AND state IN ('active', 'done', 'missed')", (min(lo, mon.isoformat()), sun.isoformat())).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    rows = [(d, k, s) for d, k, s in rows if d and k not in SKIP_KINDS]
    past = [s for d, _k, s in rows if lo <= d < today.isoformat() and s in ("done", "missed")]
    done = sum(1 for s in past if s == "done")
    week = [s for d, _k, s in rows if mon.isoformat() <= d <= sun.isoformat()]
    return {"d28": {"due": len(past), "done": done, "rate": round(done / len(past), 3) if past else None},
            "week": {"planned": len(week), "done": sum(1 for s in week if s == "done"),
                     "start": mon.isoformat()}}


def status_alerts(st) -> tuple[list, dict]:
    """([{id, level, title}] of the status's red then yellow indicators, the level counts)."""
    if st is None:
        return [], {}
    from backend.i18n import current_locale
    inds = list(getattr(st, "indicators", None) or [])
    out = [{"id": i.id, "level": i.level, "title": i.title} for lv in ALERT_LEVELS for i in inds if i.level == lv]
    counts = {lv: sum(1 for i in inds if i.level == lv) for lv in ("good", "watch", "bad", "info", "na")}
    return out, {**counts, "locale": current_locale()}


def build(ds, st=None, reason: str = "") -> dict:
    """The summary of the current tenant from its Dataset `ds` (and its overview status `st`). Each
    part on its own: one that fails is None, the rest stands."""
    from backend.engine import overview as O
    today = O.day_to_date(ds.today)

    def part(fn, *a):
        try:
            return fn(*a)
        except Exception as e:             # noqa: BLE001 — that part shows as 「—」
            log.warning("coach summary part %s failed: %s", getattr(fn, "__name__", "?"), type(e).__name__)
            return None
    alerts, counts = part(status_alerts, st) or ([], {})
    newest = max((O.wdate(w) for w in ds.workouts), default=None) if getattr(ds, "workouts", None) else None
    return {"v": VERSION, "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "day": today.isoformat(), "reason": reason,
            "volume": {"d7": part(volume, ds, today, 7), "d28": part(volume, ds, today, 28)},
            "load": part(load, ds, today), "next_race": part(next_race, today),
            "plan": part(plan_completion, today),
            "last_activity": newest.isoformat() if newest else None,
            "alerts": alerts, "status_counts": counts}


def _status_of(ds, today: dt.date, compute: bool):
    """The overview status of this Dataset as cached (any language); computed (in the source
    language) only when `compute` — the warm-up has just computed it, the nightly job may have to."""
    from backend.api import overview as OV
    st = OV.status_but_hr(ds, today)
    if st is None and compute:
        from backend.i18n import DEFAULT_LOCALE, use_locale
        with use_locale(DEFAULT_LOCALE):
            st = OV._status(ds, today)
    return st


def _stale_mark() -> Optional[str]:
    """The summary's `stale_at` now (read before computing: _store compares it afterwards)."""
    return (read() or {}).get("stale_at")


def _store(s: dict, seen: Optional[str]) -> dict:
    """Write a summary computed from the data as it was when `seen` (the stale mark then) was read.
    Changed again meanwhile (a newer mark): the mark stays, so tick() computes it once more (SP-826)."""
    with _FILE_LOCK:
        cur = (read() or {}).get("stale_at")
        if cur and cur != seen:
            s = {**s, "stale_at": cur}
        write(s)
    return s


def after_warm_up(ds, reason: str) -> bool:
    """api/wko5views.warm_up's last step (its thread, the student's tenant): a coached student's
    summary from the Dataset and status just built. Never raises."""
    try:
        if ds is None or not coached():
            return False
        from backend.engine import overview as O
        seen = _stale_mark()
        _store(build(ds, _status_of(ds, O.day_to_date(ds.today), compute=True), reason=f"warm-up:{reason}"), seen)
        _FAILED.pop(tenancy.current().id, None)
        return True
    except Exception as e:                 # noqa: BLE001 — the nightly one comes anyway
        log.warning("coach summary after the warm-up failed: %s", type(e).__name__)
        return False


def refresh_now(reason: str) -> dict:
    """Build the current tenant's Dataset (a cache hit when it is warm) and write its summary. The
    job tick() / mark_stale() queue; tests call it directly."""
    from backend.api import wko5views as WV
    from backend.engine import overview as O
    seen = _stale_mark()
    ds = WV._dataset()
    return _store(build(ds, _status_of(ds, O.day_to_date(ds.today), compute=True), reason=reason), seen)


def _job(reason: str) -> None:
    tid = tenancy.current().id
    try:
        if not coached():                  # withdrawn while it waited
            return
        refresh_now(reason)
        _FAILED.pop(tid, None)
    except Exception as e:                 # noqa: BLE001 — tried again after RETRY_S
        with _LOCK:
            _FAILED[tid] = time.monotonic()
        log.warning("coach summary (%s) failed: %s", reason, type(e).__name__)


# ---------------------------------------------------------------------------- when

def night_offset(tenant_id: str) -> int:
    return int(hashlib.sha256(("coach:" + tenant_id).encode("utf-8")).hexdigest(), 16) % NIGHT_SPAN_MIN


def due(now: Optional[dt.datetime] = None) -> Optional[str]:
    """Why the current tenant's summary is to be computed now: "missing" (none yet), "changed" (marked
    stale: SP-826), "night" (one of an earlier day, past tonight's slot) or None."""
    t = tenancy.current()
    with _LOCK:
        failed = _FAILED.get(t.id)
    if failed is not None and time.monotonic() - failed < RETRY_S:
        return None
    s = read()
    if s is None:
        return "missing"
    if s.get("stale_at"):
        return "changed"
    from backend.engine import localtime
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(localtime.zone())
    slot = dt.datetime.combine(local.date(), dt.time(*NIGHT_AT), tzinfo=local.tzinfo) \
        + dt.timedelta(minutes=night_offset(t.id))
    if str(s.get("day") or "") < local.date().isoformat() and local >= slot:
        return "night"
    return None


async def tick(now: Optional[dt.datetime] = None, submit: Optional[Callable] = None) -> list[tuple[str, str]]:
    """The scheduler's minute (sync/scheduler.loop): each coached student whose summary is due gets
    a job of the server's queue (one per tenant at a time: its key). [(tenant id, why)] queued."""
    import asyncio
    from backend import accounts
    if not accounts.enabled():
        return []
    from backend.accounts import store, tenants as TN
    if submit is None:
        from backend import jobs
        submit = jobs.submit
    out = []
    for sid in await asyncio.to_thread(store.coached_student_ids):
        u = store.get_user(sid)
        if u is None or u.disabled:
            continue
        t = TN.tenant_of(u)
        try:
            await TN.ensure_ready(t)
            with tenancy.use(t):
                why = due(now)
                if why is None:
                    continue
                _queue(why, submit)
                out.append((t.id, why))
        except asyncio.CancelledError:
            raise
        except Exception as e:             # noqa: BLE001 — never the loop for one student
            log.warning("coach summary tick failed: %s", type(e).__name__)
    return out


def _queue(why: str, submit: Optional[Callable] = None) -> None:
    """The current tenant's summary job in the server's queue: one per tenant (its key — a job of that key
    still queued is the same job; one already running keeps a newer stale mark, see _store)."""
    if submit is None:
        from backend import jobs
        submit = jobs.submit
    submit(lambda job, why=why: _job(why), cls="full", priority=PRIORITY.get(why, "warm"),
           label=f"coach summary ({why})", key=("coach-summary", tenancy.current().id))


def changes_plan(method: str, path: str) -> bool:
    """A write that changes a race, the phases or the 課表 (PLAN_WRITES)."""
    return method.upper() in WRITE_METHODS and PLAN_WRITES.fullmatch(path) is not None


def mark_stale(submit: Optional[Callable] = None) -> bool:
    """SP-826, after a 2xx write that changes_plan (tenancy_mw, in the tenant written to — the student's,
    also when their coach made the change): a coached student's summary is marked stale (the board shows
    更新中) and its computation queued now, at `warm` — the request computes nothing. A failed one waiting
    for RETRY_S is tried again (a person changed something). False for anyone who has no coach. Never raises."""
    try:
        if not coached():
            return False
        t = tenancy.current()
        with _FILE_LOCK:
            s = read()
            if s is not None:
                write({**s, "stale_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")})
        with _LOCK:
            _FAILED.pop(t.id, None)
        _queue("changed", submit)
        return True
    except Exception as e:                 # noqa: BLE001 — the change itself went through; the night comes anyway
        log.warning("coach summary mark failed: %s", type(e).__name__)
        return False


# ---------------------------------------------------------------------------- the board's row

def last_sync() -> Optional[str]:
    """The current tenant's newest sync that worked (any source; sync.<source>.last_ok), read only."""
    from backend.sync.runner import SETTING_NAME
    db = _db()
    if db is None:
        return None
    keys = [f"sync.{n}.last_ok" for n in sorted(set(SETTING_NAME.values()))]
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(f"SELECT value_json FROM user_settings WHERE user_id = 1 AND key IN "
                               f"({','.join('?' * len(keys))})", keys).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    best = None
    for (raw,) in rows:
        try:
            v = json.loads(raw)
        except (TypeError, ValueError):
            continue
        at = v.get("at") if isinstance(v, dict) else None
        if isinstance(at, str) and (best is None or at > best):
            best = at
    return best


_REVERSE: dict = {}               # locale -> (catalog id, {text: msgid})


def title_in(title: str, made_in: Optional[str]) -> str:
    """A status indicator's title computed in `made_in`, in the request's language."""
    from backend.i18n import DEFAULT_LOCALE, catalog, current_locale, translate
    loc = current_locale()
    if not title or made_in in (None, loc):
        return title
    msgid = title
    if made_in != DEFAULT_LOCALE:          # back to the msgid (zh-TW) through that language's catalog
        cat = catalog(made_in)
        hit = _REVERSE.get(made_in)
        if hit is None or hit[0] != id(cat):
            hit = (id(cat), {v: k for k, v in cat.items() if isinstance(v, str)})
            _REVERSE[made_in] = hit
        msgid = hit[1].get(title, title)
    return translate(msgid, loc)


def _days_between(a: Optional[str], b: dt.date) -> Optional[int]:
    if not a:
        return None
    try:
        d = dt.datetime.fromisoformat(a.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    from backend.engine import localtime
    return (b - d.astimezone(localtime.zone()).date()).days


def board_row(s: Optional[dict], today: Optional[dt.date] = None) -> dict:
    """What the board shows of the current tenant (a student, already checked): the stored summary
    `s` as it is, plus the last sync, the race countdown and the alerts in the request's language.
    Reads two small things (the summary file was read by the caller; the sync times here) — never
    the Dataset."""
    from backend.engine import localtime
    today = today or localtime.today_local()
    sync_at = last_sync()
    alerts = []
    if s is not None:
        made_in = (s.get("status_counts") or {}).get("locale")
        alerts += [{"id": a.get("id"), "level": a.get("level"), "text": title_in(a.get("title") or "", made_in),
                    "from": "status"} for a in s.get("alerts") or [] if a.get("level") in ALERT_LEVELS]
    days = _days_between(sync_at, today)
    if sync_at is None:
        alerts.append({"id": "never_synced", "level": "watch", "text": _(ALERT_TEXT["never_synced"]), "from": "board"})
    elif days is not None and days >= SYNC_WATCH_DAYS:
        alerts.append({"id": "no_sync", "level": "bad" if days >= SYNC_BAD_DAYS else "watch",
                       "text": _(ALERT_TEXT["no_sync"], days=days), "from": "board"})
    race = None
    if s is not None:
        d7 = (s.get("volume") or {}).get("d7")
        if d7 is not None and not d7.get("sessions") and sync_at is not None:
            alerts.append({"id": "quiet", "level": "watch", "text": _(ALERT_TEXT["quiet"]), "from": "board"})
        p28 = ((s.get("plan") or {}).get("d28")) or {}
        if p28.get("rate") is not None and (p28.get("due") or 0) >= PLAN_LOW_MIN_DUE and p28["rate"] < PLAN_LOW_RATE:
            alerts.append({"id": "plan_low", "level": "watch",
                           "text": _(ALERT_TEXT["plan_low"], pct=round(p28["rate"] * 100)), "from": "board"})
        r = s.get("next_race")
        if r:
            try:
                first = dt.date.fromisoformat(r["date"])
                left = (first - today).days
                if left + max(1, int(r.get("days") or 1)) - 1 >= 0:
                    race = {**r, "in_days": left}
            except (KeyError, TypeError, ValueError):
                race = None
    alerts.sort(key=lambda a: ALERT_LEVELS.index(a["level"]))
    stale_days = None
    if s is not None:
        try:
            stale_days = (today - dt.date.fromisoformat(s["day"])).days
        except (KeyError, TypeError, ValueError):
            stale_days = None
    return {"summary": None if s is None else {k: s.get(k) for k in ("computed_at", "day", "volume", "load", "plan",
                                                                     "last_activity")},
            "next_race": race, "last_sync": sync_at, "alerts": alerts, "summary_age_days": stale_days,
            "refreshing": bool(s is not None and s.get("stale_at"))}     # SP-826: changed, being computed again


def view_url(student_id: int) -> Optional[str]:
    """The board's link into one student (SP-791's 代看): None until that exists."""
    return STUDENT_VIEW_URL.format(id=int(student_id)) if STUDENT_VIEW_URL else None
