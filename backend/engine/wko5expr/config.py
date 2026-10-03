"""
Engine configuration: WKO5 parity vs the athlete's own adjusted formulas.

`parity = True` reproduces WKO5 exactly — that is the mode to use when checking
that our parsing and formulas are right, because every number can be compared
against WKO5 on screen. Every custom knob below is ignored in that mode.

`parity = False` applies this project's own rules, which deliberately depart
from WKO5 where WKO5 is weak for mountain sport (it is cycling-first). Each
knob documents what it changes and why.

Defaults live in ~/.wko5coach/engine.json; missing keys fall back to the
dataclass defaults, so the file only needs the settings you actually change.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Optional

CONFIG_PATH = None      # fixed file (tests); None = the tenant's engine.json


def config_path() -> Path:
    if CONFIG_PATH is not None:
        return Path(CONFIG_PATH)
    from backend import tenancy
    return tenancy.base_path("engine.json")
FEET_PER_METRE = 1 / 0.3048


@dataclass(frozen=True)
class EngineConfig:
    # --- mode -------------------------------------------------------------
    parity: bool = True
    """True: reproduce WKO5 exactly (verification mode). False: use the knobs below."""

    # --- TSS sourcing -----------------------------------------------------
    use_tp_tss: bool = True
    """Prefer a TrainingPeaks-synced TSS over our own hrTSS, as WKO5 does.
    Forced on in parity mode. Turn off to be fully independent of TP — which is
    what a direct COROS/Garmin import will be."""

    hr_tss_moving_only: bool = False
    """Charge hrTSS only while moving. WKO5 charges every recorded second, which
    badly inflates multi-day trips (a 51 h outing with 7 h of walking scored 906
    over all recorded time vs 298 over moving time)."""

    # --- mountain adjustments (Uphill Athlete) ----------------------------
    elevation_tss_per_1000ft: float = 0.0
    """Extra TSS per 1,000 ft (305 m) of ascent, added to hrTSS-sourced workouts.
    Uphill Athlete recommends 10: heart rate cannot see the muscular cost of
    steep climbing, so hrTSS under-counts vertical days. 0 disables."""

    pack_weight_pct: float = 0.0
    """Percent of bodyweight carried. Uphill Athlete adds a further
    10 TSS per 1,000 ft for each 10% of bodyweight, above a 10% threshold."""

    elevation_tss_sports: tuple = ("hiking", "mountaineering", "trail running")
    """Sport types the elevation bonus applies to (lower-case sport_type)."""

    # --- data quality -----------------------------------------------------
    power_spike_ratio: float = 0.0
    """Drop a workout from power-duration fitting when its peak 1 s power is an
    outlier: flagged if avg(top 5 across workouts) / max < this ratio. WKO5 only
    surfaces this for manual review (the "Find Power Spikes & Bad Data" charts);
    we can act on it. 0 disables. 0.9 is a reasonable starting point."""

    def elevation_bonus(self, climbing_m: Optional[float], sport_type: str) -> float:
        """Extra TSS for a workout's ascent, per the Uphill Athlete rules."""
        if self.parity or not climbing_m or not self.elevation_tss_per_1000ft:
            return 0.0
        if sport_type and sport_type.lower() not in self.elevation_tss_sports:
            return 0.0
        thousands = climbing_m * FEET_PER_METRE / 1000.0
        bonus = thousands * self.elevation_tss_per_1000ft
        if self.pack_weight_pct > 10:
            bonus += thousands * 10.0 * (self.pack_weight_pct / 10.0)
        return bonus

    hr_tss_zone1_floor: float = 0.0
    """Score heart rates below this fraction of LTHR as 0 TSS/h instead of
    WKO5's flat 20-30 TSS/h. WKO5's lowest band has no floor, so a sleeping
    heart rate still earns ~30 TSS/h — which is most of why a 51 h trip scored
    906. Uphill Athlete's Zone 1 starts at AeT-20%; ~0.70 x LTHR is a
    reasonable floor. 0 disables."""

    @property
    def tp_tss(self) -> bool:
        return True if self.parity else self.use_tp_tss

    @property
    def moving_hr_tss(self) -> bool:
        return False if self.parity else self.hr_tss_moving_only

    def replace(self, **kw: Any) -> "EngineConfig":
        return EngineConfig(**{**asdict(self), **kw})

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EngineConfig":
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in (data or {}).items() if k in known}
        if "elevation_tss_sports" in kw:
            kw["elevation_tss_sports"] = tuple(kw["elevation_tss_sports"])
        return cls(**kw)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "EngineConfig":
        p = path or config_path()
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if "parity" not in data:
            # WKO5 parity (verification mode) is only the default when there is
            # a WKO5 folder to compare against; a COROS / TP-only runner gets
            # the project's own formulas (generalize-athlete plan S6)
            from backend.engine.wko5expr.datasource import wko5_available
            data = {**data, "parity": wko5_available()}
        return cls.from_dict(data)

    def save(self, path: Optional[Path] = None) -> None:
        p = path or config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), "utf-8")


# The preset the project recommends when parity is off: independent of
# TrainingPeaks, moving-time hrTSS, and Uphill Athlete's vertical bonus.
MOUNTAIN_PRESET = EngineConfig(
    parity=False, use_tp_tss=False, hr_tss_moving_only=True,
    elevation_tss_per_1000ft=10.0, hr_tss_zone1_floor=0.70,
)
