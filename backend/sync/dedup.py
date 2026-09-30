"""
Cross-source de-duplication.

The same run can arrive from COROS, from TrainingPeaks and from a local
folder. Rows whose start times are within `WINDOW` of each other are one
activity; exactly one of them is canonical and the others get
`duplicate_of = <canonical id>`, which the PMC / analytics queries skip.

Canonical choice: the row from the user's primary source
(`sync.primary_source`), else the earliest imported (lowest id). Stubs of
corrupt files never count as canonical while a readable row exists.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import WorkoutFile

WINDOW = timedelta(minutes=2)


def canonical_clause():
    """`.where(...)` predicate: only canonical rows (use in every total)."""
    return WorkoutFile.duplicate_of.is_(None)


def choose_canonical(rows: Sequence, primary: Optional[str]):
    """rows: objects with id, source, file_format. Pure; unit tested."""
    readable = [r for r in rows if r.file_format != "corrupt"] or list(rows)
    if primary:
        pref = [r for r in readable if r.source == primary]
        if pref:
            return min(pref, key=lambda r: r.id)
    return min(readable, key=lambda r: r.id)


async def _primary(db: AsyncSession, user_id: int) -> Optional[str]:
    from backend.settings.repository import SettingsRepository
    return await SettingsRepository(db, user_id).get("sync.primary_source")


async def resolve(db: AsyncSession, wf: WorkoutFile, primary: Optional[str] = None,
                  primary_known: bool = False) -> Optional[int]:
    """Re-decide the group `wf` belongs to. Returns the canonical id (or None
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
    group = {r.id: r for r in res.scalars()}
    group[wf.id] = wf
    # pull in rows already linked to any member's canonical row
    linked = {r.duplicate_of for r in group.values() if r.duplicate_of}
    for cid in linked - set(group):
        c = await db.get(WorkoutFile, cid)
        if c is not None:
            group[c.id] = c
    canon = choose_canonical(list(group.values()), primary)
    for r in group.values():
        r.duplicate_of = None if r.id == canon.id else canon.id
    await db.flush()
    return canon.id


async def rebuild(db: AsyncSession, athlete_id: int) -> dict:
    """Recompute every group (after changing the primary source or a backfill)."""
    primary = await _primary(db, athlete_id)
    res = await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == athlete_id).order_by(WorkoutFile.start_time_utc))
    rows = list(res.scalars())
    for r in rows:
        r.duplicate_of = None
    groups, cur = [], []
    for r in rows:
        if r.start_time_utc is None:
            continue
        if cur and r.start_time_utc - cur[-1].start_time_utc > WINDOW:
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)
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
