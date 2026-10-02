"""
主要資料來源 (2026-10-02): which synced source an activity is read from when
COROS and TrainingPeaks both have it.

Setting `sync.primary_source`: "auto" (default) | "coros" | "trainingpeaks"
(None, the old default, reads as "auto"; "local" stays valid for the DB
de-dup of locally scanned files).

自動 picks the source with the most recent complete data (`choose_auto`):

  1. the newest activity day;
  2. on the same day: the source whose last sync finished cleanly (status
     ok, or never synced through the app = files put there by hand) over one
     whose last sync failed / was partial / was aborted;
  3. then more activities in the last RECENT_DAYS days;
  4. then COROS (the watch itself; TP gets its files from COROS).

The same chooser serves the DB de-dup (sync/dedup.py, which row of a
duplicate group is canonical), the merged chart Dataset
(wko5expr/fitdataset.py: data source "synced", one file per activity) and the
sync order (the primary syncs first; the other one only when asked, or when
`sync.secondary.auto` is on).

Owner rule 「算不出來就不要補了」: the other source only fills activities the
primary does not have at all. A value the primary's file cannot give (no
power, a bad file that is excluded) is never taken from the other source.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Iterable, Optional

AUTO = "auto"
SETTING_KEY = "sync.primary_source"
SECONDARY_AUTO_KEY = "sync.secondary.auto"
DB_SOURCES = ("coros", "trainingpeaks")          # workout_files.source / settings names
FOLDER = {"coros": "coros", "trainingpeaks": "tp"}  # -> sync/storage.py folder names
DB_OF = {v: k for k, v in FOLDER.items()}
LABELS = {"coros": "COROS", "trainingpeaks": "TrainingPeaks", "local": "本機資料夾", AUTO: "自動"}
RECENT_DAYS = 90
BAD_STATUS = ("failed", "partial", "aborted")


def normalize(value) -> str:
    """The stored setting -> "auto" | a source name."""
    return AUTO if value in (None, "", AUTO) else str(value)


def to_db(name: Optional[str]) -> Optional[str]:
    """A folder ("tp") or DB ("trainingpeaks") name -> the DB name."""
    if name is None:
        return None
    return DB_OF.get(name, name)


def stats_from_starts(starts: Iterable, status: Optional[str] = None) -> dict:
    """{latest, recent, status} of one source from its activity start times
    (datetime or date)."""
    days = sorted(d.date() if isinstance(d, dt.datetime) else d for d in starts if d is not None)
    if not days:
        return {"latest": None, "recent": 0, "status": status}
    latest = days[-1]
    since = latest - dt.timedelta(days=RECENT_DAYS)
    return {"latest": latest, "recent": sum(1 for d in days if d > since), "status": status}


def choose_auto(stats: dict) -> Optional[str]:
    """stats = {source: {latest: date | None, recent: int, status: str | None}}
    -> the source 自動 picks; None when no source has any activity. Pure."""
    cands = [(s, v) for s, v in stats.items() if v and v.get("latest") is not None]
    if not cands:
        return None

    def key(item):
        s, v = item
        return (v["latest"], v.get("status") not in BAD_STATUS, int(v.get("recent") or 0),
                1 if s == "coros" else 0)
    return max(cands, key=key)[0]


def resolve(setting, stats: dict) -> Optional[str]:
    """The source in effect (DB name): the chosen one, or 自動's pick."""
    v = normalize(setting)
    return choose_auto(stats) if v == AUTO else v


def last_status(result) -> Optional[str]:
    return result.get("status") if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
# merging two sources' activities (the merged Dataset)
# ---------------------------------------------------------------------------

WINDOW_S = 120.0       # sync/dedup.py WINDOW: one activity when the starts are this close


def merge(items: list[tuple], primary: Optional[str], window_s: float = WINDOW_S,
          quality: Optional[Callable] = None) -> tuple[list, list]:
    """items = [(start_utc datetime, source, payload)] of every readable file
    of every source. Files whose starts chain within `window_s` are one
    activity (as sync/dedup.py groups the DB rows); each activity keeps ONE
    file: the primary source's when it has one, else the other source's
    (only for activities the primary does not have). Several files of the
    same source for one activity (an upload from two watches, a summary-only
    copy: 29 such TP pairs on the athlete's data) keep the best by
    `quality(payload)` (higher wins; e.g. the sample count), then the first.
    Returns (kept, dropped), each in the input order."""
    order = sorted(range(len(items)), key=lambda i: _ts(items[i][0]))
    groups, cur, last = [], [], None
    for i in order:
        x = _ts(items[i][0])
        if cur and x - last > window_s:
            groups.append(cur)
            cur = []
        cur.append(i)
        last = x
    if cur:
        groups.append(cur)
    keep = set()
    for g in groups:
        best = max(g, key=lambda i: (items[i][1] == primary,
                                     quality(items[i][2]) if quality else 0, -i))
        keep.add(best)
    kept = [it for i, it in enumerate(items) if i in keep]
    dropped = [it for i, it in enumerate(items) if i not in keep]
    return kept, dropped


def _ts(t: dt.datetime) -> float:
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


# ---------------------------------------------------------------------------
# the app DB (async): the sync side
# ---------------------------------------------------------------------------

async def db_stats(db, athlete_id: int) -> dict:
    """{source: {latest, recent, status}} from workout_files + the last sync
    results (sync.<source>.last_result)."""
    from sqlalchemy import func, select
    from backend.db.models import WorkoutFile
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    out = {}
    for s in DB_SOURCES:
        latest = (await db.execute(select(func.max(WorkoutFile.workout_date)).where(
            WorkoutFile.athlete_id == athlete_id, WorkoutFile.source == s,
            WorkoutFile.file_format != "corrupt"))).scalar()
        if isinstance(latest, str):
            latest = dt.date.fromisoformat(latest[:10])
        recent = 0
        if latest is not None:
            recent = (await db.execute(select(func.count(WorkoutFile.id)).where(
                WorkoutFile.athlete_id == athlete_id, WorkoutFile.source == s,
                WorkoutFile.file_format != "corrupt",
                WorkoutFile.workout_date > latest - dt.timedelta(days=RECENT_DAYS)))).scalar() or 0
        out[s] = {"latest": latest, "recent": int(recent),
                  "status": last_status(await repo.get(f"sync.{s}.last_result"))}
    return out


async def resolve_db(db, athlete_id: int) -> dict:
    """{setting, source (in effect, DB name or None), auto (bool), stats}."""
    from backend.settings.repository import SettingsRepository
    setting = normalize(await SettingsRepository(db, athlete_id).get(SETTING_KEY))
    stats = await db_stats(db, athlete_id) if setting == AUTO else {}
    return {"setting": setting, "source": resolve(setting, stats), "auto": setting == AUTO, "stats": stats}


def sync_order(ready: dict, primary: Optional[str], secondary_auto: bool) -> tuple[list, dict]:
    """Which ready sources an automatic sync (page open, daily schedule)
    starts, primary first. ready = {folder source: "ready" | reason}.
    The other source only with `secondary_auto`; with no primary known
    (no data yet) every ready source; with the primary switched off or
    logged out, the other one (else nothing would ever sync). Returns
    (start, skipped)."""
    pf = FOLDER.get(primary or "")
    if pf not in ready or ready[pf] in ("disabled", "not_logged_in"):
        pf = None
    order = sorted(ready, key=lambda s: 0 if s == pf else 1)
    start, skipped = [], {}
    for s in order:
        if ready[s] != "ready":
            skipped[s] = ready[s]
        elif pf and s != pf and not secondary_auto:
            skipped[s] = "secondary"
        else:
            start.append(s)
    return start, skipped
