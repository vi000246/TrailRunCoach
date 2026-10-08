"""
完整檢查 and the weekly check (SP-362 A3 / A4): find activities a sync missed.

A sync lists only from its cursor (minus CURSOR_OVERLAP_DAYS for COROS; workouts/changed for
TP), so an activity that fell behind the cursor would never be fetched. These runs list the
remote history and compare it BY PROVIDER ID with the DB:

  missing      on the watch / remote, not in the DB and not on the failed list -> 「補下載」
               (FILL) fetches exactly these through the sync's own fetch path (_fetch_one:
               same files, same failed list on a failure)
  local_only   in the DB, not on the remote (deleted there?) -> LISTED ONLY, never deleted
               (owner 2026-10-08)
  failed       the source's rows on the failed list (sync/failures.py)

Modes:
  FULL    设定 › 資料同步 › 進階設定 「完整檢查」: the whole history (COROS: every page of the
          activity list from FIRST_SYNC_DAY, ~41 pages for 819 activities; TP: 90-day
          date-range calls from DEFAULT_FIRST_SYNC_DAY). Downloads nothing.
  FILL    「補下載」: the last FULL result's missing ones.
  WEEKLY  once a week from the scheduler loop (sync/scheduler.py): the last WEEKLY_DAYS days
          (~3 COROS pages), the missing ones fetched at once (normal sync scope, at most
          WEEKLY_FETCH_MAX). Only the 資料來源 in use, like every automatic sync.

Each run goes through sync/runner.stream with its own client stream: the source's busy flag
(never beside a sync / deletion / another check of that source; COROS's self-rating job gives
way), the post-run steps when something was downloaded (warm-up, automatic plan,
calibration), the per-step timing log line. It never touches the sync cursor nor the
source's last_result / last_ok. Results: settings sync.<name>.check (FULL / FILL) and
sync.<name>.check_weekly (WEEKLY), so the page shows them after a reload; the progress of a
running check is in memory (progress()).

COROS's list answer is used for ids, dates, sport types and trainingLoad only; whether it
carries a total count is not verified, so no per-month count comparison is made.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncIterator, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Athlete, WorkoutFile
from backend.settings.repository import SettingsRepository
from backend.sync import failures as FL
from backend.sync import http, runner, session_check

log = logging.getLogger(__name__)

FULL, FILL, WEEKLY = "full", "fill", "weekly"
WEEKLY_DAYS = 60                  # the weekly check's window (~3 COROS pages)
WEEKLY_EVERY = timedelta(days=7)
WEEKLY_RETRY = timedelta(days=1)  # after a failed weekly check (network, login)
WEEKLY_FETCH_MAX = 30             # downloads per weekly check
STARTUP_DELAY_S = 600             # the scheduler's weekly check waits this long after start
MISSING_MAX = 500                 # items kept in the stored result (counts are exact)
LIST_MAX = 200
TRIGGER = {FULL: "check", FILL: "check-fill", WEEKLY: "weekly-check"}


def key(source: str, mode: str) -> str:
    return f"sync.{runner.SETTING_NAME[source]}.{'check_weekly' if mode == WEEKLY else 'check'}"


_PROGRESS: dict[str, dict] = {}   # source -> {mode, phase, pages, listed, done, total, at}
_TASKS: set[asyncio.Task] = set()


def progress(source: str) -> Optional[dict]:
    p = _PROGRESS.get(source)
    return dict(p) if p else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ListError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


# ---------------------------------------------------------------------------
# remote listing + fetch, per source (the sync clients' own calls)
# ---------------------------------------------------------------------------

class _Coros:
    source = "coros"

    def __init__(self, db: AsyncSession, athlete_id: int):
        self.db, self.aid = db, athlete_id

    def first_day(self) -> date:
        from backend.sync import coros_client as C
        return datetime.strptime(C.FIRST_SYNC_DAY, "%Y%m%d").date()

    async def open(self) -> None:
        from backend.sync import coros_client as C
        try:
            self.token, self.base, self.user_id = await C._get_token_and_base(self.db, self.aid)
        except ValueError as e:
            raise ListError("COROS_AUTH_REQUIRED", str(e))

    async def close(self) -> None:
        pass

    async def pages(self, since: date, until: date) -> AsyncIterator[list[dict]]:
        """Every page of /activity/query in [since, until] (the sync's call, PAGE_SIZE items;
        one automatic re-login on "Access token is invalid", as the sync does)."""
        from backend.sync import coros_client as C
        page, relogged = 1, False
        a, b = since.strftime("%Y%m%d"), until.strftime("%Y%m%d")
        while True:
            t0 = time.monotonic()
            try:
                acts = await C._list_page(self.token, self.base, self.user_id, a, b, page, C.PAGE_SIZE)
            except C.CorosTokenInvalid as e:
                if not relogged and await C.relogin(self.db, self.aid, since=t0):
                    relogged = True
                    self.token, self.base, self.user_id = await C._get_token_and_base(self.db, self.aid,
                                                                                      auto_relogin=False)
                    continue
                session_check.mark_expired("coros", self.aid)
                raise ListError("COROS_AUTH_REQUIRED", str(e))
            except Exception as e:               # noqa: BLE001
                raise ListError("COROS_API_ERROR", str(e)[:200])
            yield [self.item(x) for x in acts if isinstance(x, dict) and x.get("labelId")]
            if len(acts) < C.PAGE_SIZE:
                return
            page += 1

    @staticmethod
    def item(act: dict) -> dict:
        from backend.sync import coros_client as C
        d = C._parse_coros_date(act.get("date") or act.get("startTime", 0))
        out = {"id": str(act.get("labelId")), "date": d.isoformat() if d else None}
        try:
            out["sport_type"] = int(act["sportType"]) if act.get("sportType") is not None else None
        except (TypeError, ValueError):
            out["sport_type"] = None
        tl = C.list_training_load(act)
        if tl is not None:
            out["tl"] = tl
        return out

    def run_state(self) -> dict:
        return {"downloaded": 0, "errors": [], "feel_read": set(), "hold": False}

    async def known(self, pid: str) -> bool:
        return (await self.db.execute(select(WorkoutFile.id).where(
            WorkoutFile.coros_activity_id == pid))).scalars().first() is not None

    async def fetch(self, it: dict, run: dict) -> AsyncIterator[dict]:
        from backend.sync import coros_client as C
        act = {"labelId": it["id"], "sportType": it.get("sport_type") or 0}
        if it.get("tl") is not None:
            act["trainingLoad"] = it["tl"]
        d = date.fromisoformat(it["date"]) if it.get("date") else None
        async for ev in C._fetch_one(self.db, self.aid, self.token, self.base, self.user_id, act, d, run):
            yield ev

    async def local(self) -> dict[str, Optional[date]]:
        rows = (await self.db.execute(select(WorkoutFile.coros_activity_id, WorkoutFile.workout_date).where(
            WorkoutFile.athlete_id == self.aid, WorkoutFile.coros_activity_id.is_not(None)))).all()
        return {str(pid): d for pid, d in rows}


class _Tp:
    source = "tp"

    def __init__(self, db: AsyncSession, athlete_id: int):
        self.db, self.aid, self.client = db, athlete_id, None

    def first_day(self) -> date:
        from backend.sync import tp_client as T
        return date.fromisoformat(T.DEFAULT_FIRST_SYNC_DAY)

    async def open(self) -> None:
        from backend.sync import tp_client as T
        token = await T._get_valid_token(self.db, self.aid)
        if not token:
            raise ListError("TP_AUTH_REQUIRED")
        athlete = (await self.db.execute(select(Athlete).where(Athlete.id == self.aid))).scalar_one_or_none()
        if athlete is None or not athlete.tp_athlete_id:
            raise ListError("TP_ATHLETE_UNKNOWN")
        self.who = SimpleNamespace(tp_athlete_id=athlete.tp_athlete_id)
        self.client = http.client(base_url=T.TP_API_BASE, headers={**T.TP_HEADERS, "Authorization": f"Bearer {token}"},
                                  timeout=60)
        await self.client.__aenter__()

    async def close(self) -> None:
        if self.client is not None:
            await self.client.__aexit__(None, None, None)
            self.client = None

    async def pages(self, since: date, until: date) -> AsyncIterator[list[dict]]:
        """fitness/v6 workouts by date range, in RANGE_CHUNK_DAYS (90-day) calls (the sync's
        first-sync listing). Planned (future) workouts are skipped as the sync does."""
        from backend.sync import tp_client as T
        today = date.today().isoformat()
        async for items, info in T._iter_date_range(self.client, self.who.tp_athlete_id,
                                                    since.isoformat(), until.isoformat()):
            if "error" in info:
                if info.get("status") in (401, 403):
                    session_check.mark_expired("tp", self.aid)
                    raise ListError("TP_AUTH_REQUIRED")
                raise ListError("TP_API_ERROR", str(info.get("status") or info.get("detail") or "")[:200])
            out = []
            for wo in items:
                wid = wo.get("workoutId") or wo.get("id") if isinstance(wo, dict) else None
                day = ((wo.get("workoutDay") or wo.get("startTime") or "") if wid else "")[:10]
                if not wid or (day and day > today):
                    continue
                out.append({"id": str(wid), "date": day or None})
            yield out

    def run_state(self) -> dict:
        return {"downloaded": 0, "errors": [], "hold": False}

    @staticmethod
    def _wid(pid: str):
        try:
            return int(pid)
        except (TypeError, ValueError):
            return pid

    async def known(self, pid: str) -> bool:
        return (await self.db.execute(select(WorkoutFile.id).where(
            WorkoutFile.tp_workout_id == self._wid(pid), WorkoutFile.athlete_id == self.aid))).scalars().first() is not None

    async def fetch(self, it: dict, run: dict) -> AsyncIterator[dict]:
        from backend.sync import tp_client as T
        async for ev in T._fetch_one(self.db, self.client, self.who, self.aid, self._wid(it["id"]),
                                     it.get("date") or "", run):
            yield ev

    async def local(self) -> dict[str, Optional[date]]:
        rows = (await self.db.execute(select(WorkoutFile.tp_workout_id, WorkoutFile.workout_date).where(
            WorkoutFile.athlete_id == self.aid, WorkoutFile.tp_workout_id.is_not(None)))).all()
        return {str(pid): d for pid, d in rows}


REMOTES = {"coros": _Coros, "tp": _Tp}


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def compare(remote: list[dict], local: dict[str, Optional[date]], failed: dict[str, dict],
            since: date, until: date, whole: bool) -> dict:
    """The three groups. `local` = provider id -> workout date of every DB row of the source;
    `failed` = provider id -> failed-list row summary. local_only looks only inside the
    listed window (`whole`: also rows without a date) without its first and last day (near
    an edge the remote date and the athlete-local date can differ by a day; today's rows were
    just synced)."""
    seen = {it["id"] for it in remote}
    missing = [it for it in remote if it["id"] not in local and it["id"] not in failed]
    local_only = sorted(({"id": pid, "date": d.isoformat() if d else None} for pid, d in local.items()
                         if pid not in seen and ((d is None and whole) or (d is not None and since < d < until))),
                        key=lambda x: (x["date"] or "", x["id"]))
    n_local = sum(1 for d in local.values() if (d is None and whole) or (d is not None and since <= d <= until))
    missing.sort(key=lambda x: (x.get("date") or "", x["id"]))
    rows = sorted(failed.values(), key=lambda x: (x.get("date") or "", x["id"]))
    return {"remote": len(seen), "local": n_local,
            "missing": missing[:MISSING_MAX], "missing_n": len(missing),
            "local_only": local_only[:LIST_MAX], "local_only_n": len(local_only),
            "failed": rows[:LIST_MAX], "failed_n": len(rows)}


async def _failed_rows(db: AsyncSession, athlete_id: int, source: str) -> dict[str, dict]:
    rows = await FL.listing(db, athlete_id)
    return {str(r["provider_id"]): {"id": str(r["provider_id"]), "date": r["date"], "state": r["state"]}
            for r in rows if r["source"] == source}


# ---------------------------------------------------------------------------
# the client streams (sync/runner.stream(client=...))
# ---------------------------------------------------------------------------

async def _store(db: AsyncSession, athlete_id: int, source: str, mode: str, value: dict) -> None:
    try:
        await db.rollback()
        keep = {k: v for k, v in value.items() if not k.startswith("_")}    # _errors / _handled: this run only
        await SettingsRepository(db, athlete_id).set(key(source, mode), keep)
        await db.commit()
    except Exception as e:                       # noqa: BLE001
        log.warning("%s check result not stored: %s", source, type(e).__name__)


def _prog(source: str, **kw) -> None:
    p = _PROGRESS.setdefault(source, {})
    p.update(kw)


async def check_stream(db: AsyncSession, source: str, athlete_id: int, mode: str,
                       ids: Optional[list] = None, today: Optional[date] = None) -> AsyncIterator[dict]:
    """One FULL / WEEKLY / FILL run's events (runner.stream's `client`). A failure of the
    whole run (login, listing) is one error event without an activity id (the runner's
    "failed") and is stored as the result's status / error."""
    t0 = time.monotonic()
    today = today or date.today()
    remote = REMOTES[source](db, athlete_id)
    _prog(source, mode=mode, phase="start", pages=0, listed=0, done=0, total=0)
    out = {"mode": mode, "at": None, "status": "running", "error": None}
    try:
        yield {"status": "started", "check": mode}
        await remote.open()
        if mode == FILL:
            async for ev in _fill(db, remote, source, athlete_id, ids, out):
                yield ev
        else:
            async for ev in _list_and_compare(db, remote, source, athlete_id, mode, today, out):
                yield ev
        out["status"] = "partial" if out.get("fetch_errors") else "ok"
    except ListError as e:
        out.update(status="failed", error=e.code)
        log.warning("%s %s check failed: %s", source, mode, e.code)
        yield {"status": "error", "error": e.code, "detail": e.detail[:200]}
    finally:
        try:
            await remote.close()
        except Exception:                        # noqa: BLE001
            pass
        out["at"] = _now_iso()
        out["secs"] = round(time.monotonic() - t0, 1)
        if out["status"] == "running":
            out["status"] = "aborted"
        if mode == FILL:
            await _store_fill(db, athlete_id, source, out)
        else:
            await _store(db, athlete_id, source, mode, out)
        _PROGRESS.pop(source, None)
    if out["status"] not in runner.OK_STATUSES:
        return                                   # the error event above is the run's outcome
    yield {"status": "complete", "total_downloaded": out.get("fetched", 0),
           "total_checked": out.get("remote", out.get("tried", 0)),
           "errors": out.get("_errors", []), "check": mode}


async def _list_and_compare(db, remote, source: str, athlete_id: int, mode: str, today: date,
                            out: dict) -> AsyncIterator[dict]:
    since = remote.first_day() if mode == FULL else today - timedelta(days=WEEKLY_DAYS)
    out.update(since=since.isoformat(), until=today.isoformat())
    listed: list[dict] = []
    seen: set = set()
    pages = 0
    _prog(source, phase="list")
    # up to the server's today, as the sync lists (an end day in the future is not verified
    # with COROS); local rows dated today or later never count as local_only (compare)
    async for items in remote.pages(since, today):
        pages += 1
        for it in items:
            if it["id"] not in seen:
                seen.add(it["id"])
                listed.append(it)
        _prog(source, pages=pages, listed=len(listed))
        yield {"status": "check_list", "page": pages, "listed": len(listed)}
    groups = compare(listed, await remote.local(), await _failed_rows(db, athlete_id, source),
                     since, today, whole=mode == FULL)
    out.update(groups, pages=pages)
    if mode == WEEKLY and groups["missing"]:
        todo = groups["missing"][:WEEKLY_FETCH_MAX]
        async for ev in _fetch_all(remote, source, todo, out):
            yield ev
    log.warning("%s %s check: remote %s, local %s, missing %s, local only %s, failed list %s%s",
                source, mode, groups["remote"], groups["local"], groups["missing_n"], groups["local_only_n"],
                groups["failed_n"], f", fetched {out.get('fetched', 0)}" if mode == WEEKLY else "")


async def _fetch_all(remote, source: str, todo: list[dict], out: dict) -> AsyncIterator[dict]:
    """Fetch `todo` through the sync's _fetch_one; out gets fetched / fetch_errors / handled."""
    run = remote.run_state()
    handled = []
    _prog(source, phase="fetch", total=len(todo), done=0)
    for i, it in enumerate(todo):
        if await remote.known(it["id"]):          # imported meanwhile (a sync)
            handled.append(it["id"])
            continue
        async for ev in remote.fetch(it, run):
            yield ev
        handled.append(it["id"])
        _prog(source, done=i + 1)
    out.update(fetched=run["downloaded"], fetch_errors=len(run["errors"]),
               tried=len(todo), _errors=list(run["errors"]), _handled=handled)


async def _fill(db, remote, source: str, athlete_id: int, ids: Optional[list], out: dict) -> AsyncIterator[dict]:
    last = await SettingsRepository(db, athlete_id).get(key(source, FULL)) or {}
    todo = list(last.get("missing") or [])
    if ids:
        want = {str(x) for x in ids}
        todo = [it for it in todo if it["id"] in want]
    async for ev in _fetch_all(remote, source, todo, out):
        yield ev


async def _store_fill(db: AsyncSession, athlete_id: int, source: str, out: dict) -> None:
    """補下載's outcome goes into the FULL result: the handled ones leave `missing` (a failed
    download is on the failed list now: group 3 of the next check)."""
    try:
        await db.rollback()
        repo = SettingsRepository(db, athlete_id)
        last = dict(await repo.get(key(source, FULL)) or {})
        done = set(out.get("_handled") or [])
        left = [it for it in last.get("missing") or [] if it["id"] not in done]
        last["missing_n"] = max(0, int(last.get("missing_n") or 0) - (len(last.get("missing") or []) - len(left)))
        last["missing"] = left
        last["filled"] = {"at": out["at"], "status": out["status"], "error": out.get("error"),
                          "downloaded": out.get("fetched", 0), "errors": out.get("fetch_errors", 0)}
        await repo.set(key(source, FULL), last)
        await db.commit()
    except Exception as e:                       # noqa: BLE001
        log.warning("%s fill result not stored: %s", source, type(e).__name__)


# ---------------------------------------------------------------------------
# starting a run
# ---------------------------------------------------------------------------

def start(source: str, athlete_id: int = 1, mode: str = FULL, ids: Optional[list] = None,
          session_factory: Optional[Callable] = None) -> Optional[asyncio.Task]:
    """A background run; None when the source is busy (a sync, a deletion, a check)."""
    if source not in REMOTES or mode not in TRIGGER:
        raise ValueError(f"unknown source / mode {source!r} / {mode!r}")
    if runner.is_busy(source):
        return None
    if session_factory is None:
        from backend.db.database import AsyncSessionLocal as session_factory
    mine = {"mode": mode, "phase": "start", "pages": 0, "listed": 0, "done": 0, "total": 0, "at": _now_iso()}
    _PROGRESS[source] = mine
    t = asyncio.get_running_loop().create_task(_run(source, athlete_id, mode, ids, session_factory, mine))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


async def _run(source: str, athlete_id: int, mode: str, ids, session_factory, mine: dict) -> dict:
    last = {}
    try:
        async with session_factory() as db:
            async for ev in runner.stream(db, source, athlete_id, trigger=TRIGGER[mode], remember=False,
                                          client=lambda d: check_stream(d, source, athlete_id, mode, ids)):
                last = ev
    except Exception as e:                       # noqa: BLE001 — a background run never raises
        log.warning("%s %s check run failed: %s", source, mode, type(e).__name__)
    finally:
        if _PROGRESS.get(source) is mine:        # e.g. SYNC_BUSY: the stream never ran
            _PROGRESS.pop(source, None)
    return last


def running(source: str) -> bool:
    return source in _PROGRESS


# ---------------------------------------------------------------------------
# the weekly check (A4): sync/scheduler.loop calls weekly_tick every minute
# ---------------------------------------------------------------------------

def weekly_due(last: Optional[dict], now: datetime) -> bool:
    if not isinstance(last, dict) or not last.get("at"):
        return True
    try:
        at = datetime.fromisoformat(last["at"])
    except ValueError:
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    gap = WEEKLY_RETRY if last.get("status") in ("failed", "aborted") else WEEKLY_EVERY
    return now - at >= gap


async def weekly_tick(session_factory: Callable, now: Optional[datetime] = None, athlete_id: int = 1,
                      start_fn: Optional[Callable] = None) -> list[str]:
    """Start the weekly check of every source an automatic sync would start (the 資料來源 in
    use, enabled, logged in, idle) whose last weekly check is a week old. The sources started."""
    now = now or datetime.now(timezone.utc)
    start_fn = start_fn or (lambda src, aid: start(src, aid, WEEKLY, session_factory=session_factory))
    async with session_factory() as db:
        todo, _skipped = await runner.auto_plan(db, athlete_id)
        repo = SettingsRepository(db, athlete_id)
        due = [s for s in todo if weekly_due(await repo.get(key(s, WEEKLY)), now)]
    started = []
    for src in due:
        if start_fn(src, athlete_id) is not None:
            started.append(src)
    if started:
        log.info("weekly sync check started: %s", started)
    return started


async def status(db: AsyncSession, athlete_id: int = 1) -> dict:
    """GET /api/v1/sync/check: per source — running (+ progress), the last FULL result, the
    last WEEKLY result, and whether a check can start now (ready / the reason)."""
    repo = SettingsRepository(db, athlete_id)
    ready = await runner.ready_sources(db, athlete_id)
    out = {}
    for src in runner.SOURCES:
        out[src] = {"running": running(src), "progress": progress(src), "ready": ready.get(src),
                    "last": await repo.get(key(src, FULL)), "weekly": await repo.get(key(src, WEEKLY))}
    return out
