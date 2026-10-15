"""
Cross-source de-duplication.

The same run can arrive from COROS, from TrainingPeaks and from a local
folder. Rows whose start times are within `WINDOW` of each other are one
activity; exactly one of them is canonical and the others get
`duplicate_of = <canonical id>`, which the PMC / analytics queries skip.

Canonical choice: the row from the 資料來源 in use (`sync.primary_source`,
sync/primary.py), else the most complete copy (an original FIT before a
rebuilt / exported one: FORMAT_RANK), else the earliest imported (lowest
id); switching the source rebuilds the groups. Stubs of corrupt files never
count as canonical while a readable row exists. Totals use `in_use_clause`:
canonical rows of the 資料來源 (and local imports), never the other synced
source's rows — also not the activities only that source has (no back-fill).
The chart Dataset skips a duplicate whose canonical copy it also holds
(fitdataset.FitFolderDataset), so TSS / PMC / bests count it once.

Many sources (SP-344: COROS, Garmin, intervals.icu, COROS MCP, manual
imports, export files, Apple Health through a bridge) — one start-time window
is not enough on its own, so two rows are the same activity (`same_activity`)
when they start within WINDOW AND, where both rows know it:
  * their moving spans overlap and the shorter lasts ≥ MIN_DURATION_RATIO of
    the longer (a 10-min warm-up recorded on its own is not the run after it)
  * their distances differ by ≤ MAX_DISTANCE_DIFF
  * their sports are the same family (a ride and a run at the same minute
    stay apart)
or they hold the same content (`content_sha256`, SP-308) whatever the times.
The user can merge (`merge`) or split (`split`) by hand: activity_links rows
(kind same / different) that every regrouping obeys.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ActivityLink, WorkoutFile
from backend.engine.localtime import local_iso

WINDOW = timedelta(minutes=2)
MIN_DURATION_RATIO = 0.6
MAX_DISTANCE_DIFF = 0.25          # relative; 推估: GPS vs. a watch's own distance differ by a few %
MIN_DISTANCE_M = 500.0            # below this (indoor, a treadmill without a pod) distances say nothing
# an original device FIT before a rebuilt / exported copy (SP-344: 原始 FIT 優先、資料最完整的優先)
FORMAT_RANK = {"fit": 0, "fit.gz": 0, "wko4": 1, "tcx": 2, "gpx": 3, "json": 4, "healthkit": 4, "xml": 5}
SPORT_FAMILY = {"run": "foot", "running": "foot", "trail": "foot", "trail_running": "foot", "walk": "foot",
                "walking": "foot", "hike": "foot", "hiking": "foot", "mountaineering": "foot",
                "bike": "bike", "cycling": "bike", "ride": "bike", "swim": "swim", "swimming": "swim"}


def canonical_clause():
    """`.where(...)` predicate: only canonical rows (use in every total)."""
    return WorkoutFile.duplicate_of.is_(None)


def in_use_clause(primary: str):
    """`.where(...)` predicate for every total: canonical rows that are not
    from the synced sources NOT in use (primary = "coros" | "garmin" | "trainingpeaks")."""
    from sqlalchemy import and_
    from backend.sync import primary as P
    others = [P.to_db(f) for f in P.other_folders(primary)]     # two since Garmin (SP-91)
    return and_(canonical_clause(), WorkoutFile.source.notin_(others))


async def in_use(db: AsyncSession, user_id: int = 1):
    """in_use_clause of the athlete's 資料來源."""
    return in_use_clause(await _primary(db, user_id))


def _family(sport) -> Optional[str]:
    if not sport:
        return None
    k = str(sport).strip().lower().replace(" ", "_").split("/")[0]
    return SPORT_FAMILY.get(k)


_SHA_IN_PATH = re.compile(r"([0-9a-f]{64})\.fit(?:\.gz)?$")


def content_hash(r) -> Optional[str]:
    """The row's content sha256: its own field, or the hash-named object key of SP-308
    (fit/objects/<2>/<sha256>.fit.gz); None for a path-named file."""
    h = getattr(r, "content_sha256", None)
    if h:
        return h
    m = _SHA_IN_PATH.search(str(getattr(r, "file_path", "") or ""))
    return m.group(1) if m else None


def same_activity(a, b) -> bool:
    """Two rows are one activity (pure; the rows' fields as in workout_files). Links are applied
    by the grouping, not here."""
    ha, hb = content_hash(a), content_hash(b)
    if ha and hb and ha == hb:
        return True
    if a.start_time_utc is None or b.start_time_utc is None:
        return False
    if abs(a.start_time_utc - b.start_time_utc) > WINDOW:
        return False
    fa, fb = _family(getattr(a, "sport", None)), _family(getattr(b, "sport", None))
    if fa and fb and fa != fb:
        return False
    da, db_ = getattr(a, "duration_s", None), getattr(b, "duration_s", None)
    if da and db_ and da > 0 and db_ > 0:
        if min(da, db_) / max(da, db_) < MIN_DURATION_RATIO:
            return False
        sa, sb = a.start_time_utc, b.start_time_utc
        ea, eb = sa + timedelta(seconds=da), sb + timedelta(seconds=db_)
        overlap = (min(ea, eb) - max(sa, sb)).total_seconds()
        if overlap < 0.5 * min(da, db_):
            return False
    ma, mb = getattr(a, "total_distance_m", None), getattr(b, "total_distance_m", None)
    if ma and mb and ma >= MIN_DISTANCE_M and mb >= MIN_DISTANCE_M:
        if abs(ma - mb) / max(ma, mb) > MAX_DISTANCE_DIFF:
            return False
    return True


def choose_canonical(rows: Sequence, primary: Optional[str]):
    """rows: objects with id, source, file_format. Pure; unit tested. The 資料來源's row, else the
    most complete copy (FORMAT_RANK; a file that is not the original one step lower: not_original),
    else the earliest imported; never a corrupt stub while a readable row exists."""
    readable = [r for r in rows if r.file_format != "corrupt"] or list(rows)
    if primary:
        pref = [r for r in readable if r.source == primary]
        if pref:
            return min(pref, key=lambda r: r.id)
    return min(readable, key=lambda r: (FORMAT_RANK.get(str(r.file_format or "").lower(), 3)
                                        + (1 if not_original(r) else 0), r.id))


def not_original(r) -> bool:
    """A FIT that is not the watch's own file: Huawei's activities through intervals.icu (its API has
    no FIT export, SP-346) — ranked after an original copy of the same run."""
    return str(getattr(r, "upstream_source", None) or "").upper() == "HUAWEI"


def group_rows(rows: Sequence, links: Sequence = ()) -> list[list]:
    """The duplicate groups of `rows` (sorted by start): same_activity within the window, plus the
    user's links — `same` joins two rows wherever they are, `different` keeps two rows (and so
    their groups) apart. Union–find; pure."""
    by_id = {r.id: r for r in rows}
    parent = {r.id: r.id for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    members = {r.id: {r.id} for r in rows}
    apart = {(min(l.a_id, l.b_id), max(l.a_id, l.b_id)) for l in links if l.kind == "different"}

    def blocked(ra, rb) -> bool:
        return any((min(x, y), max(x, y)) in apart for x in members[ra] for y in members[rb])

    def union(x, y) -> None:
        ra, rb = find(x), find(y)
        if ra == rb or blocked(ra, rb):
            return
        parent[rb] = ra
        members[ra] |= members.pop(rb)
    for l in links:
        if l.kind == "same" and l.a_id in by_id and l.b_id in by_id:
            union(l.a_id, l.b_id)
    timed = sorted((r for r in rows if r.start_time_utc is not None), key=lambda r: r.start_time_utc)
    for i, a in enumerate(timed):
        for b in timed[i + 1:]:
            if b.start_time_utc - a.start_time_utc > WINDOW:
                break
            if same_activity(a, b):
                union(a.id, b.id)
    hashed: dict = {}
    for r in rows:
        h = content_hash(r)
        if h:
            if h in hashed:
                union(hashed[h], r.id)
            else:
                hashed[h] = r.id
    groups: dict = {}
    for r in rows:
        groups.setdefault(find(r.id), []).append(r)
    return list(groups.values())


async def _links(db: AsyncSession, athlete_id: int) -> list:
    try:
        return list((await db.execute(select(ActivityLink).where(ActivityLink.athlete_id == athlete_id))).scalars())
    except Exception:                        # noqa: BLE001 — a DB before the table: no links
        return []


async def _primary(db: AsyncSession, user_id: int) -> str:
    """The 資料來源 in use (sync/primary.py)."""
    from backend.sync import primary as P
    return await P.current(db, user_id)


async def resolve(db: AsyncSession, wf: WorkoutFile, primary: Optional[str] = None,
                  primary_known: bool = False) -> Optional[int]:
    """Re-decide the group `wf` belongs to (a new import). Returns the canonical id (or None
    when wf has no start time and so can't be matched)."""
    if wf.start_time_utc is None:
        return None
    if not primary_known:
        primary = await _primary(db, wf.athlete_id)
    res = await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == wf.athlete_id,
        WorkoutFile.start_time_utc.isnot(None),
        WorkoutFile.start_time_utc >= wf.start_time_utc - WINDOW,
        WorkoutFile.start_time_utc <= wf.start_time_utc + WINDOW,
    ))
    near = {r.id: r for r in res.scalars()}
    near[wf.id] = wf
    # pull in rows already linked to any member's canonical row, and the user's links
    links = await _links(db, wf.athlete_id)
    want = {r.duplicate_of for r in near.values() if r.duplicate_of}
    want |= {x for l in links for x in (l.a_id, l.b_id) if l.a_id in near or l.b_id in near}
    for cid in want - set(near):
        c = await db.get(WorkoutFile, cid)
        if c is not None:
            near[c.id] = c
    # rows whose canonical is in the set (a group's other members)
    canon_ids = {r.duplicate_of or r.id for r in near.values()}
    more = await db.execute(select(WorkoutFile).where(WorkoutFile.duplicate_of.in_(canon_ids)))
    for r in more.scalars():
        near.setdefault(r.id, r)
    groups = group_rows(list(near.values()), links)
    mine = next(g for g in groups if any(r.id == wf.id for r in g))
    # the rows that left wf's group (a split, a stale link) are re-decided among themselves
    for g in groups:
        canon = choose_canonical(g, primary)
        for r in g:
            r.duplicate_of = None if r.id == canon.id else canon.id
    await db.flush()
    return choose_canonical(mine, primary).id


async def rebuild(db: AsyncSession, athlete_id: int) -> dict:
    """Recompute every group (after switching the 資料來源, a backfill, a manual merge / split)."""
    primary = await _primary(db, athlete_id)
    res = await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == athlete_id).order_by(WorkoutFile.start_time_utc))
    rows = list(res.scalars())
    for r in rows:
        r.duplicate_of = None
    groups = group_rows(rows, await _links(db, athlete_id))
    dups = 0
    for g in groups:
        if len(g) < 2:
            continue
        canon = choose_canonical(g, primary)
        for r in g:
            if r.id != canon.id:
                r.duplicate_of = canon.id
                dups += 1
    await db.flush()
    return {"rows": len(rows), "groups": sum(1 for g in groups if len(g) > 1), "duplicates": dups}


# ---------------------------------------------------------------------------- by hand (SP-344)

async def _link(db: AsyncSession, athlete_id: int, a: int, b: int, kind: str) -> None:
    a, b = min(a, b), max(a, b)
    old = (await db.execute(select(ActivityLink).where(
        ActivityLink.athlete_id == athlete_id, ActivityLink.a_id == a, ActivityLink.b_id == b))).scalars().all()
    for o in old:
        await db.delete(o)
    db.add(ActivityLink(athlete_id=athlete_id, a_id=a, b_id=b, kind=kind))


async def merge(db: AsyncSession, athlete_id: int, ids: Sequence[int]) -> dict:
    """The user says these rows are one activity (whatever the times): `same` links, then the
    groups again. The canonical copy follows the usual rule."""
    ids = sorted({int(i) for i in ids})
    rows = (await db.execute(select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id,
                                                        WorkoutFile.id.in_(ids)))).scalars().all()
    if len(rows) != len(ids) or len(ids) < 2:
        raise ValueError("merge needs two or more of this athlete's activities")
    for i in ids[1:]:
        await _link(db, athlete_id, ids[0], i, "same")
    await db.flush()
    return await rebuild(db, athlete_id)


async def split(db: AsyncSession, athlete_id: int, row_id: int) -> dict:
    """The user says this row is its own activity: `different` links to every other member of its
    group (its `same` links go), then the groups again."""
    wf = await db.get(WorkoutFile, int(row_id))
    if wf is None or wf.athlete_id != athlete_id:
        raise ValueError("no such activity")
    canon = wf.duplicate_of or wf.id
    group = (await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == athlete_id,
        (WorkoutFile.id == canon) | (WorkoutFile.duplicate_of == canon)))).scalars().all()
    for o in (await db.execute(select(ActivityLink).where(
            ActivityLink.athlete_id == athlete_id, ActivityLink.kind == "same",
            (ActivityLink.a_id == wf.id) | (ActivityLink.b_id == wf.id)))).scalars().all():
        await db.delete(o)
    for r in group:
        if r.id != wf.id:
            await _link(db, athlete_id, wf.id, r.id, "different")
    await db.flush()
    return await rebuild(db, athlete_id)


async def unlink(db: AsyncSession, athlete_id: int, row_id: int) -> dict:
    """Forget the user's merges / splits of this row: the automatic rule decides again."""
    for o in (await db.execute(select(ActivityLink).where(
            ActivityLink.athlete_id == athlete_id,
            (ActivityLink.a_id == int(row_id)) | (ActivityLink.b_id == int(row_id))))).scalars().all():
        await db.delete(o)
    await db.flush()
    return await rebuild(db, athlete_id)


async def group_of(db: AsyncSession, athlete_id: int, row_id: int) -> list[dict]:
    """The rows of `row_id`'s group (for the page): id, source, format, start, duration, distance,
    canonical, and whether the user linked it."""
    wf = await db.get(WorkoutFile, int(row_id))
    if wf is None or wf.athlete_id != athlete_id:
        return []
    canon = wf.duplicate_of or wf.id
    rows = (await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == athlete_id,
        (WorkoutFile.id == canon) | (WorkoutFile.duplicate_of == canon)))).scalars().all()
    linked = {x for l in await _links(db, athlete_id) for x in (l.a_id, l.b_id)}
    return [{"id": r.id, "source": r.source, "file_format": r.file_format,
             "start": r.start_time_utc.isoformat() if r.start_time_utc else None,
             "start_local": local_iso(r.start_time_utc, athlete_id),       # what the page shows (SP-410)
             "duration_s": r.duration_s, "distance_m": r.total_distance_m,
             "canonical": r.duplicate_of is None, "linked": r.id in linked} for r in rows]


NEAR = timedelta(minutes=30)


async def near(db: AsyncSession, athlete_id: int, row_id: int) -> dict:
    """For the activity editor (SP-344): this activity's copies from every source, and the other
    rows starting within NEAR (candidates to merge by hand)."""
    wf = await db.get(WorkoutFile, int(row_id))
    if wf is None or wf.athlete_id != athlete_id:
        return {"group": [], "candidates": []}
    group = await group_of(db, athlete_id, row_id)
    ids = {g["id"] for g in group}
    cands = []
    if wf.start_time_utc is not None:
        res = await db.execute(select(WorkoutFile).where(
            WorkoutFile.athlete_id == athlete_id, WorkoutFile.start_time_utc.isnot(None),
            WorkoutFile.start_time_utc >= wf.start_time_utc - NEAR,
            WorkoutFile.start_time_utc <= wf.start_time_utc + NEAR).order_by(WorkoutFile.start_time_utc))
        for r in res.scalars():
            if r.id in ids or r.duplicate_of in ids:
                continue
            cands.append({"id": r.id, "source": r.source, "file_format": r.file_format,
                          "start": r.start_time_utc.isoformat(),
                          "start_local": local_iso(r.start_time_utc, athlete_id), "duration_s": r.duration_s,
                          "distance_m": r.total_distance_m})
    return {"group": group, "candidates": cands}
