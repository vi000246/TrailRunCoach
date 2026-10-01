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
    # COROS / TP source: read thresholds / weight from the WKO5 athlete file
    # (opt-in cross-check; default = plan → athlete_settings → estimates, fitdataset.py)
    "charts.fit_settings_from_wko5": False,
    # power-based models (race-power envelope / CP / PD, power TSS, power effort
    # checks) also read watch-estimated (wrist) power; False = Stryd only
    # (backend/engine/power_source.py). HR / pace paths always use every run.
    "power.accept_watch_power": False,
    # workout route map (viewer 單次活動): default basemap id and overlay ids;
    # the map can switch them temporarily (remembered per browser)
    "charts.map.basemap": "rudy",
    "charts.map.overlays": [],
    # 課表偏好 (engine/plan_prefs.py). Every default reproduces the planner's
    # own behaviour, so an athlete who never opens the panel gets today's plan.
    "plan.prefs.days": None,                  # 7 bools Mon..Sun (False = rest day); None = every day
    "plan.prefs.long_day": "auto",            # sat | sun | auto (the athlete's usual long day)
    "plan.prefs.cap_weekday": None,           # minutes per session; None = no cap
    "plan.prefs.cap_long": None,              # minutes for the long day; None = same as weekday
    "plan.prefs.cap_mode": "soft",            # soft (盡量不超過) | hard (絕對不超過)
    "plan.prefs.runs_per_week": None,         # 3-7; None = auto
    "plan.prefs.quality_per_week": None,      # 0-2; None = auto (at most 1, gated)
    "plan.prefs.strength_per_week": None,     # 0-3; None = auto
    "plan.prefs.strength_days": [],           # weekdays 0-6 for strength; [] = with easy runs
    "plan.prefs.weekly_hours": None,          # custom weekly cap (h); None = CTL ramp rules
    "plan.prefs.terrain_easy": "any",         # road | trail | any
    "plan.prefs.terrain_long": "auto",        # road | trail | hike | auto
    "plan.prefs.terrain_quality": "any",      # flat | hill | any
    "plan.prefs.interval_target": "power",    # power | hr
    # CP 測試方式 (engine/cp_protocols.py): quick 20 min all-out | standard 12′ +
    # 30′ + 3′ | race (a 5–10 K race instead). The athlete chose quick as the default.
    "plan.prefs.cp_test_protocol": "quick",
    # 熱適應課 (engine/heat_plan.py): auto = only before a hot A/B race; off = never
    "plan.prefs.heat": "auto",
    "plan.prefs.heat_method": "run",          # run | overdress | bath | sauna | mixed
    # 間歇門檻 (engine/quality_gate.py): what unlocks base-phase intervals
    "plan.prefs.quality_gate": "auto",        # auto | ua_gap | friel_drift | xu_drift | plateau | weeks | none
    "plan.prefs.quality_gate_weeks": 8,       # weeks mode: base-phase weeks before intervals (2-16)
    # AeT 飄移測試 (engine/aet_test.py, 50 min): weekday = Mon–Fri only (the athlete
    # trail-runs on weekends) | any = the interval days (Tue first)
    "plan.prefs.aet_test_days": "weekday",
    # 不排課日期 (engine/blackouts.py): one-off ranges [{id, start, end, label}]
    # on which nothing is planned; separate from the weekly plan.prefs.days
    "plan.blackouts": [],
    # 自動調整課表 (engine/plan_auto.py): after a sync that imported an activity,
    # reconcile + adapt (engine/adapt.py) + push the next N days to COROS
    "plan.auto.enabled": True,
    "plan.auto.push": True,                   # push the window to COROS automatically
    "plan.auto.push_days": 7,                 # 1-14 days from today
    "plan.auto.confirm_big": True,            # hold big changes for the user's approval
    "plan.auto.notify": "watch",              # watch (a 課表待確認 workout on COROS) | overview (banner only)
    # internal: {stamp, phase, rejected: [fingerprint]} of the last automatic run
    "plan.auto.state": None,
}
AUTO_NOTIFY = ("watch", "overview")
AUTO_KEYS = ("plan.auto.enabled", "plan.auto.push", "plan.auto.push_days", "plan.auto.confirm_big",
             "plan.auto.notify")
MAP_BASEMAPS = ("rudy", "google-terrain", "nlsc-emap", "nlsc-photo", "osm")
MAP_OVERLAYS = ("contour", "google-roads", "nlsc-roads")
PREF_ENUMS = {
    "plan.prefs.long_day": ("sat", "sun", "auto"),
    "plan.prefs.cap_mode": ("soft", "hard"),
    "plan.prefs.terrain_easy": ("road", "trail", "any"),
    "plan.prefs.terrain_long": ("road", "trail", "hike", "auto"),
    "plan.prefs.terrain_quality": ("flat", "hill", "any"),
    "plan.prefs.interval_target": ("power", "hr"),
    "plan.prefs.cp_test_protocol": ("quick", "standard", "race"),
    "plan.prefs.heat": ("auto", "off"),
    "plan.prefs.heat_method": ("run", "overdress", "bath", "sauna", "mixed"),
    "plan.prefs.quality_gate": ("auto", "ua_gap", "friel_drift", "xu_drift", "plateau", "weeks", "none"),
    "plan.prefs.aet_test_days": ("weekday", "any"),
}
PREF_INTS = {                                 # key -> (lo, hi); None always allowed
    "plan.prefs.cap_weekday": (20, 300),
    "plan.prefs.cap_long": (20, 600),
    "plan.prefs.runs_per_week": (3, 7),
    "plan.prefs.quality_per_week": (0, 2),
    "plan.prefs.strength_per_week": (0, 3),
    "plan.prefs.quality_gate_weeks": (2, 16),
}


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
    if key in ("charts.fit_settings_from_wko5", "power.accept_watch_power") and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key.endswith(".enabled") and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key.startswith("plan.prefs."):
        _validate_pref(key, value)
    if key == "plan.blackouts":
        from backend.engine.blackouts import validate as validate_blackouts
        validate_blackouts(value)
    if key in ("plan.auto.push", "plan.auto.confirm_big") and not isinstance(value, bool):
        raise ValueError(f"{key} must be true/false")
    if key == "plan.auto.push_days" and (isinstance(value, bool) or not isinstance(value, int)
                                         or not 1 <= value <= 14):
        raise ValueError("plan.auto.push_days must be an integer 1-14")
    if key == "plan.auto.notify" and value not in AUTO_NOTIFY:
        raise ValueError(f"plan.auto.notify must be one of {AUTO_NOTIFY}")
    if key == "plan.auto.state" and value is not None and not isinstance(value, dict):
        raise ValueError("plan.auto.state must be an object or null")


def _validate_pref(key: str, value: Any) -> None:
    if key in PREF_ENUMS and value not in PREF_ENUMS[key]:
        raise ValueError(f"{key} must be one of {PREF_ENUMS[key]}")
    if key in PREF_INTS and value is not None:
        lo, hi = PREF_INTS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"{key} must be an integer {lo}-{hi} or null")
    if key == "plan.prefs.days" and value is not None:
        if not (isinstance(value, list) and len(value) == 7 and all(isinstance(v, bool) for v in value)):
            raise ValueError("plan.prefs.days must be 7 true/false (Mon..Sun) or null")
        if not any(value):
            raise ValueError("至少要有一天可以練")
    if key == "plan.prefs.strength_days" and not (
            isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 6
                                            for v in value) and len(set(value)) == len(value)):
        raise ValueError("plan.prefs.strength_days must be distinct weekdays 0-6")
    if key == "plan.prefs.weekly_hours" and value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 40):
        raise ValueError("plan.prefs.weekly_hours must be 1-40 hours or null")


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
