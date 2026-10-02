"""
Garmin Connect — a STUB provider (not enabled, no network). The offline payload is the
probe's converter (scripts/garmin_probe.garmin_workout: steps -> Garmin workout JSON, ids
from garminconnect 0.3.17); research in docs/research/garmin.md. Push / remove / list raise
ProviderDisabled until a real client (login, token storage, schedule) is wired in.
"""
from __future__ import annotations

from typing import Optional

from backend.sync.workout_targets.base import Capabilities, WorkoutProvider


class GarminProvider(WorkoutProvider):
    id = "garmin"
    label = "Garmin（尚未開放）"
    enabled = False
    capabilities = Capabilities(
        targets=("hr",), repeat_groups=True, nested_repeats=False, open_steps=True,
        distance_steps=False, distance_unit="m", max_steps=50,
        notes=("功率：app 的 CP 不是 Garmin 的功率，只寫在說明", "配速、距離段：轉換器還沒做",
               "一堂最多 50 步（Garmin 文件）"))

    def build_payload(self, session: dict, thresholds: Optional[dict], power_targets: bool = False) -> dict:
        from backend.scripts.garmin_probe import garmin_workout
        return garmin_workout(session, thresholds, power_targets)
