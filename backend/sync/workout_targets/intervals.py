"""
intervals.icu — a STUB provider (not enabled, no network). The offline payload is the
probe's converter (scripts/intervals_probe.session_event: steps -> a planned-workout event
in intervals.icu's workout text); research in docs/research/data-hubs.md. Push / remove /
list raise ProviderDisabled until the API key storage and the event upsert are wired in.
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import N_
from backend.sync.workout_targets.base import Capabilities, WorkoutProvider


class IntervalsProvider(WorkoutProvider):
    id = "intervals"
    label = N_("intervals.icu（尚未開放）")
    enabled = False
    capabilities = Capabilities(
        targets=("power", "hr"), repeat_groups=True, nested_repeats=False, open_steps=False,
        distance_steps=False, distance_unit="m", max_steps=None,
        notes=(N_("心率送 % LTHR（intervals.icu 要有同一個 LTHR）"), N_("「直到按下計圈」的步驟沒有驗證過的語法"),
               N_("沒有「負荷」結束條件：送預估時間")),
        end_conditions=("time",))

    def build_payload(self, session: dict, thresholds: Optional[dict], power_targets: bool = True) -> dict:
        from backend.scripts.intervals_probe import session_event
        return session_event(session, thresholds, power_targets)
