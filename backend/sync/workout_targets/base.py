"""
The workout-sync provider interface: what a target (COROS, later Garmin, intervals.icu, …)
must offer so the stored plan can be pushed to it.

The plan side hands every provider the same normalized steps (sync/coros_workouts:
Step / Repeat from session_steps — warm-up / work / rest / cool-down, time, distance or
lap-button length, one ("power" | "hr" | "pace", lo, hi) target, one level of ×N
repeats). A provider turns them into its own payload and keeps an idempotent record per
(provider, session key) — for COROS the `coros_plan_push` table, whose `provider` column
holds the id — so pushing again leaves unchanged sessions alone, replaces changed ones and
removes sessions that left the plan.

Only one provider is active (setting plan.push.provider, default "coros"; 設定 → 資料同步
→ 進階設定). Providers that aren't `enabled` are stubs: their offline payload builder
works (and is tested), every call that would touch the network raises ProviderDisabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.i18n import N_, _


END_LABELS = {"time": N_("時間"), "distance": N_("距離"), "open": N_("直到按下計圈"), "load": N_("負荷")}


class SyncError(Exception):
    """The target answered with an error."""


class SyncAuthError(SyncError):
    """No usable login for the target: log in again."""


class ProviderDisabled(SyncError):
    """The provider is a stub (not enabled): nothing is sent."""


class Unsupported(Exception):
    """This session is not pushed (reason in the message)."""


@dataclass(frozen=True)
class Capabilities:
    targets: tuple = ("power", "hr", "pace")     # target types the watch gets as real targets
    repeat_groups: bool = True                    # ×N blocks stay a group (else unrolled)
    nested_repeats: bool = False
    open_steps: bool = True                       # lap-button steps
    distance_steps: bool = True
    distance_unit: str = "m"                      # what the payload's distance is in
    max_steps: Optional[int] = None               # None = not known
    notes: tuple = field(default_factory=tuple)   # short limits shown with the provider
    # the step end conditions the editor offers for this provider (時長類型, SP-38), in order,
    # and their names on this platform. A stored step with a type not listed here is still
    # pushed: "load" as the estimated time (workout_steps._secs), the rest as before
    end_conditions: tuple = ("time", "distance", "open")
    end_labels: dict = field(default_factory=lambda: dict(END_LABELS))
    load_unit: Optional[str] = None               # what a 「負荷」 step is sent as ("TL"); None = time


class WorkoutProvider:
    """Base class. Subclasses set id / label / enabled / capabilities and implement the
    calls; the defaults raise ProviderDisabled."""
    id: str = ""
    label: str = ""
    enabled: bool = False
    capabilities: Capabilities = Capabilities()

    # -- offline -----------------------------------------------------------
    def build_payload(self, session: dict, thresholds: Optional[dict]) -> Any:
        """The provider's payload for one plan session (raises Unsupported)."""
        raise NotImplementedError

    def status_of(self, session: dict, thresholds: Optional[dict], row, today: Optional[str] = None) -> dict:
        """Offline push status of one session against its record."""
        raise ProviderDisabled(_("{label} 尚未開放", label=_(self.label)))

    def row_view(self, row) -> dict:
        return {}

    # -- records (idempotent, keyed by provider + session key) ---------------
    async def all_rows(self, db) -> dict:
        return {}

    async def rows_by_key(self, db, keys) -> dict:
        return {}

    # -- network ---------------------------------------------------------------
    async def push_sessions(self, db, sessions: list[dict], thresholds: Optional[dict], today: str,
                            *, stale_keys=(), missed_keys=()) -> dict:
        raise ProviderDisabled(_("{label} 尚未開放，沒有送出任何東西", label=_(self.label)))

    async def push_workout(self, db, session: dict, thresholds: Optional[dict], today: str) -> dict:
        """One workout outside the week plan (the race calculator), idempotent per
        session["key"]: on session["day"] when that is today or later, else into the
        provider's library only."""
        raise ProviderDisabled(_("{label} 尚未開放，沒有送出任何東西", label=_(self.label)))

    async def remove_keys(self, db, keys) -> list[dict]:
        raise ProviderDisabled(_("{label} 尚未開放", label=_(self.label)))

    async def list_remote(self, db) -> list[dict]:
        raise ProviderDisabled(_("{label} 尚未開放", label=_(self.label)))

    def describe(self) -> dict:
        c = self.capabilities
        return {"id": self.id, "label": _(self.label), "enabled": self.enabled,
                "capabilities": {"targets": list(c.targets), "repeat_groups": c.repeat_groups,
                                 "nested_repeats": c.nested_repeats, "open_steps": c.open_steps,
                                 "distance_steps": c.distance_steps, "distance_unit": c.distance_unit,
                                 "max_steps": c.max_steps, "notes": [_(n) for n in c.notes],
                                 "end_conditions": list(c.end_conditions),
                                 "end_labels": {k: _(c.end_labels.get(k, END_LABELS.get(k, k)))
                                                for k in c.end_conditions},
                                 "load_unit": c.load_unit}}
