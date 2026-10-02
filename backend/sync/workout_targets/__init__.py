"""
Workout-sync providers (base.py has the interface). Registry + the active one.

    from backend.sync import workout_targets as WT
    prov = await WT.active(db)          # setting plan.push.provider, default COROS
    await prov.push_sessions(db, sessions, thresholds, today)

Providers are imported lazily: sync/coros_workouts imports base.py, and coros.py imports
coros_workouts.
"""
from __future__ import annotations

import importlib

from backend.sync.workout_targets.base import (Capabilities, ProviderDisabled, SyncAuthError,  # noqa: F401
                                               SyncError, Unsupported, WorkoutProvider)

SETTING_KEY = "plan.push.provider"
DEFAULT = "coros"
# id -> (module, class), in the order the settings page lists them
REGISTRY = {
    "coros": ("backend.sync.workout_targets.coros", "CorosProvider"),
    "garmin": ("backend.sync.workout_targets.garmin", "GarminProvider"),
    "intervals": ("backend.sync.workout_targets.intervals", "IntervalsProvider"),
}
_cache: dict = {}


def get(pid: str) -> WorkoutProvider:
    if pid not in REGISTRY:
        raise KeyError(f"unknown workout provider {pid!r}")
    if pid not in _cache:
        mod, cls = REGISTRY[pid]
        _cache[pid] = getattr(importlib.import_module(mod), cls)()
    return _cache[pid]


def available() -> list[dict]:
    """Every provider with its capabilities; `enabled` False = a stub (shown, not selectable)."""
    return [get(p).describe() for p in REGISTRY]


def enabled_ids() -> tuple:
    return tuple(p for p in REGISTRY if get(p).enabled)


def resolve(pid) -> WorkoutProvider:
    """The provider for a stored setting; an unknown or disabled one falls back to COROS."""
    if pid in REGISTRY and get(pid).enabled:
        return get(pid)
    return get(DEFAULT)


async def active(db, user_id: int = 1) -> WorkoutProvider:
    from backend.settings.repository import SettingsRepository
    return resolve(await SettingsRepository(db, user_id).get(SETTING_KEY))
