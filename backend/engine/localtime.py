"""
The athlete's local time zone and "today" (generalize-athlete plan L2, batch B3).

Order (settings/repository.resolve_tz):
  1. athlete.timezone — set by hand (進階設定)
  2. WKO5COACH_TZ env
  3. athlete.timezone.auto — detected:
       fit:     the latest synced FIT's activity message, local_timestamp −
                timestamp, rounded to 15 min (the watch knows where it was)
       browser: the browser's Intl zone, written on the first page open
                (shell.js); used when it agrees with the FIT offset, or
                alone when there is no FIT yet. With both and in agreement
                the IANA zone wins (it knows DST); otherwise the FIT offset.
  4. the machine's zone (a Docker container without TZ is UTC — why 3 exists)

`today_local()` replaces `date.today()` in the API: the day boundary of the
athlete, not of the server.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

log = logging.getLogger(__name__)

KEY = "athlete.timezone.auto"
OFFSET_STEP_MIN = 15


def _iana(name: Optional[str]):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                       # noqa: BLE001 — unknown / malformed zone
        return None


def zone_of_auto(auto: Optional[dict], now: Optional[dt.datetime] = None) -> Optional[dt.tzinfo]:
    """The tzinfo for a detected {"fit": {"offset_min"}, "browser": IANA}; None without either."""
    if not isinstance(auto, dict):
        return None
    fit = auto.get("fit") or {}
    off = fit.get("offset_min")
    br = _iana(auto.get("browser"))
    if off is None:
        return br
    now = now or dt.datetime.now(dt.timezone.utc)
    if br is not None:
        bo = now.astimezone(br).utcoffset()
        if bo is not None and abs(bo.total_seconds() / 60 - off) < 1:
            return br
    return dt.timezone(dt.timedelta(minutes=int(off)))


def zone(user_id: int = 1) -> dt.tzinfo:
    from backend.engine.wko5expr.datasource import athlete_tz
    return athlete_tz(user_id)


def today_local(user_id: int = 1) -> dt.date:
    """The athlete's calendar date now."""
    try:
        return dt.datetime.now(zone(user_id)).date()
    except Exception:                       # noqa: BLE001
        return dt.date.today()


def fit_offset_min(path) -> Optional[int]:
    """local_timestamp − timestamp of a FIT's activity message, rounded to
    15 min; None when the file has no local_timestamp."""
    try:
        import fitdecode
        with fitdecode.FitReader(str(path), error_handling=fitdecode.ErrorHandling.IGNORE) as fit:
            for fr in fit:
                if isinstance(fr, fitdecode.FitDataMessage) and fr.name == "activity":
                    ts = fr.get_value("timestamp", fallback=None)
                    loc = fr.get_value("local_timestamp", fallback=None)
                    if ts is None or loc is None:
                        return None
                    if isinstance(ts, dt.datetime) and ts.tzinfo is not None:
                        ts = ts.astimezone(dt.timezone.utc).replace(tzinfo=None)
                    if isinstance(loc, (int, float)):
                        loc = dt.datetime(1989, 12, 31) + dt.timedelta(seconds=int(loc))
                    if isinstance(loc, dt.datetime) and loc.tzinfo is not None:
                        loc = loc.replace(tzinfo=None)
                    m = (loc - ts).total_seconds() / 60.0
                    if abs(m) > 14 * 60:
                        return None
                    return int(round(m / OFFSET_STEP_MIN) * OFFSET_STEP_MIN)
    except Exception:                       # noqa: BLE001 — unreadable file
        return None
    return None


async def refresh_from_fits(db, athlete_id: int = 1, n: int = 3) -> Optional[dict]:
    """Store the offset of the newest FIT that has one (of the latest `n` imported)."""
    from pathlib import Path
    from sqlalchemy import select
    from backend.db.models import WorkoutFile
    from backend.settings.repository import SettingsRepository
    rows = (await db.execute(select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id,
                                                       WorkoutFile.file_format == "fit",
                                                       WorkoutFile.start_time_utc.is_not(None))
                             .order_by(WorkoutFile.start_time_utc.desc()).limit(n))).scalars().all()
    for r in rows:
        p = Path(r.file_path)
        if not p.exists():
            continue
        off = fit_offset_min(p)
        if off is None:
            continue
        repo = SettingsRepository(db, athlete_id)
        cur = dict(await repo.get(KEY) or {})
        cur["fit"] = {"offset_min": off, "file": p.name, "at": r.start_time_utc.isoformat()}
        await repo.set(KEY, cur)
        await db.commit()
        return cur
    return None


async def set_browser_zone(db, name: str, athlete_id: int = 1) -> dict:
    """The browser's Intl zone (shell.js, once); unknown names are refused."""
    from backend.settings.repository import SettingsRepository
    if _iana(name) is None:
        raise ValueError(f"unknown time zone {name!r}")
    repo = SettingsRepository(db, athlete_id)
    cur = dict(await repo.get(KEY) or {})
    cur["browser"] = name
    await repo.set(KEY, cur)
    await db.commit()
    return cur
