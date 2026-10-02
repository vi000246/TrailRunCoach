"""
COROS Training Hub behind the provider interface. The implementation stays in
sync/coros_workouts.py (unchanged behaviour); every call goes through the module
attribute at call time, so tests that patch coros_workouts.push_sessions still apply.
"""
from __future__ import annotations

from typing import Optional

from backend.sync import coros_workouts as CW
from backend.sync.workout_targets.base import Capabilities, WorkoutProvider


class CorosProvider(WorkoutProvider):
    id = "coros"
    label = "COROS"
    enabled = True
    capabilities = Capabilities(
        targets=("power", "hr", "pace"), repeat_groups=True, nested_repeats=False, open_steps=True,
        distance_steps=True, distance_unit="cm", max_steps=None,
        notes=("功率只收絕對瓦數", "配速：秒／公里（2026-10-02 手錶驗證）",
               "「最後一趟不休息」的重複要攤平", "距離段單位依第三方整理（未驗證）"))

    def build_payload(self, session: dict, thresholds: Optional[dict]) -> dict:
        return CW.session_workout(session, thresholds).payload

    def status_of(self, session, thresholds, row, today=None) -> dict:
        return CW.status_of(session, thresholds, row, today)

    def row_view(self, row) -> dict:
        return CW._row_view(row)

    async def all_rows(self, db) -> dict:
        return await CW.all_rows(db)

    async def rows_by_key(self, db, keys) -> dict:
        return await CW.rows_by_key(db, 1, keys)

    async def push_sessions(self, db, sessions, thresholds, today, *, stale_keys=(), missed_keys=()) -> dict:
        return await CW.push_sessions(db, sessions, thresholds, today, stale_keys=stale_keys, missed_keys=missed_keys)

    async def remove_keys(self, db, keys) -> list[dict]:
        return await CW.remove_keys(db, keys)

    async def list_remote(self, db) -> list[dict]:
        hub = await CW.TrainingHub.from_db(db)
        return await hub.list_programs(CW.NAME_PREFIX)
