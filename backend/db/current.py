"""The athlete this install serves.

One computer, one runner (docs/plans/todo-multi-user-sharing.plan.md §3 (a)):
the app's athlete is the `athletes` row with CURRENT_ATHLETE_ID — 1 unless
WKO5COACH_ATHLETE_ID says otherwise. The routes that used to hard-code
`athlete_id == 1` read it from here (generalize-athlete plan S8), so a
multi-user change has one place to start from.

`ensure_athlete` gives a runner without a WKO5 folder an (empty) athlete row
to hang the sync state and settings on (plan S6)."""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_NAME = "athlete"


def current_athlete_id() -> int:
    try:
        return int(os.getenv("WKO5COACH_ATHLETE_ID", "1"))
    except ValueError:
        return 1


async def ensure_athlete(db: AsyncSession, data_dir: str = "") -> bool:
    """Create the current athlete's row when the table has none for it.
    True when a row was added (the caller commits)."""
    from backend.db.models import Athlete
    aid = current_athlete_id()
    if (await db.execute(select(Athlete).where(Athlete.id == aid))).scalar_one_or_none() is not None:
        return False
    name = DEFAULT_NAME if aid == 1 else f"{DEFAULT_NAME}_{aid}"
    if (await db.execute(select(Athlete).where(Athlete.name == name))).scalar_one_or_none() is not None:
        name = f"{name}_{aid}"
    db.add(Athlete(id=aid, name=name, data_dir=data_dir))
    return True
