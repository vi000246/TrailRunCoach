"""
Per-user settings repository.

Everything that differs per person — time zone, which sync source is primary,
connection details — lives in the `user_settings` table keyed by
(user_id, key), with a JSON value. The app is single-user today (user_id 1 =
athlete 1); callers always pass the id, so adding accounts later doesn't
change this interface.

Known keys and their defaults are declared in `DEFAULTS`; unknown keys are
rejected so a typo can't silently create a new setting.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, tzinfo
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import UserSetting

DEFAULT_USER = 1
SOURCES = ("coros", "trainingpeaks", "local")

DEFAULTS: dict[str, Any] = {
    # IANA zone for local workout dates; None = WKO5COACH_TZ env, then the system zone
    "athlete.timezone": None,
    # which source wins when the same activity arrives from several; None =
    # the first one imported
    "sync.primary_source": None,
    "sync.coros.enabled": True,
    "sync.trainingpeaks.enabled": True,
    # log in with WKO5's OAuth client credentials (ToS risk, see
    # docs/deploy/tp-oauth-client.md). None = auto: on only when the
    # credentials are configured on this machine; False = website login only.
    "sync.trainingpeaks.use_wko5_client": None,
    # outcome of the last run: {at, trigger, status, downloaded, checked, errors, error}
    "sync.coros.last_result": None,
    "sync.trainingpeaks.last_result": None,
    # daily automatic sync: "HH:MM" local time, None = off
    "sync.schedule.daily_time": None,
    "sync.schedule.last_run": None,          # local ISO date of the last scheduled run
    # sync when a page is opened and the last sync is older than N hours
    "sync.auto_on_open.enabled": True,
    "sync.auto_on_open.hours": 6,
    # which data the charts / overview / race power read: wko5 | coros | tp
    "charts.data_source": "wko5",
    # workout route map (viewer 單次活動): default basemap id and overlay ids;
    # the map can switch them temporarily (remembered per browser)
    "charts.map.basemap": "rudy",
    "charts.map.overlays": [],
}
MAP_BASEMAPS = ("rudy", "google-terrain", "nlsc-emap", "nlsc-photo", "osm")
MAP_OVERLAYS = ("contour", "google-roads", "nlsc-roads")


class UnknownSetting(KeyError):
    pass


class SettingsRepository:
    def __init__(self, db: AsyncSession, user_id: int = DEFAULT_USER):
        self.db, self.user_id = db, user_id

    async def _row(self, key: str) -> Optional[UserSetting]:
        res = await self.db.execute(select(UserSetting).where(
            UserSetting.user_id == self.user_id, UserSetting.key == key))
        return res.scalar_one_or_none()

    async def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise UnknownSetting(key)
        row = await self._row(key)
        return DEFAULTS[key] if row is None else json.loads(row.value_json)

    async def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise UnknownSetting(key)
        validate(key, value)
        row = await self._row(key)
        if row is None:
            row = UserSetting(user_id=self.user_id, key=key)
            self.db.add(row)
        row.value_json = json.dumps(value)
        row.updated_at = datetime.utcnow()
        await self.db.flush()

    async def all(self) -> dict[str, Any]:
        res = await self.db.execute(select(UserSetting).where(UserSetting.user_id == self.user_id))
        stored = {r.key: json.loads(r.value_json) for r in res.scalars()}
        return {k: stored.get(k, v) for k, v in DEFAULTS.items()}

    async def timezone(self) -> tzinfo:
        return resolve_tz(await self.get("athlete.timezone"))


def validate(key: str, value: Any) -> None:
    if key == "athlete.timezone" and value is not None:
        resolve_tz(value, strict=True)
    if key == "sync.primary_source" and value not in (None, *SOURCES):
        raise ValueError(f"primary source must be one of {SOURCES}")
    if key == "sync.schedule.daily_time" and value is not None:
        import re
        if not (isinstance(value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value)):
            raise ValueError("daily sync time must be HH:MM (24 h) or null")
    if key == "sync.auto_on_open.hours" and not (isinstance(value, (int, float)) and 0 < value <= 168):
        raise ValueError("auto-sync threshold must be 1-168 hours")
    if key == "charts.data_source" and value not in ("wko5", "coros", "tp"):
        raise ValueError("chart data source must be wko5, coros or tp")
    if key == "charts.map.basemap" and value not in MAP_BASEMAPS:
        raise ValueError(f"map basemap must be one of {MAP_BASEMAPS}")
    if key == "charts.map.overlays" and not (
            isinstance(value, list) and all(v in MAP_OVERLAYS for v in value)
            and len(set(value)) == len(value)):
        raise ValueError(f"map overlays must be a list of distinct {MAP_OVERLAYS}")
    if key == "sync.trainingpeaks.use_wko5_client" and value not in (None, True, False):
        raise ValueError(f"{key} must be true/false/null")
    if key.endswith(".enabled") and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")


def resolve_tz(name: Optional[str], strict: bool = False) -> tzinfo:
    """Setting -> WKO5COACH_TZ env -> the machine's local zone."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    for cand in (name, None if strict else os.getenv("WKO5COACH_TZ")):
        if not cand:
            continue
        try:
            return ZoneInfo(cand)
        except (ZoneInfoNotFoundError, ValueError):
            if strict:
                raise ValueError(f"unknown time zone {cand!r}")
    return datetime.now().astimezone().tzinfo
