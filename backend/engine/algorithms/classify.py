"""Trail vs road classification for running activities.

A running activity is classified as ``trail`` when its climb rate
(elevation gain per kilometre) meets or exceeds a threshold, otherwise ``road``.
Non-running sports and activities missing distance/elevation are ``unknown``.
The threshold is intentionally a module constant so it can be tuned after
observing real activity data (see SRS Open Question).
"""
from typing import Optional

TRAIL_CLIMB_RATE_M_PER_KM = 20.0  # >= 20 m/km climb → trail (tunable)


def classify_trail(
    sport: Optional[str],
    distance_m: Optional[float],
    elevation_gain_m: Optional[float],
) -> str:
    if sport != "running":
        return "unknown"
    if not distance_m or distance_m <= 0 or elevation_gain_m is None:
        return "unknown"
    climb_rate = elevation_gain_m / (distance_m / 1000.0)
    return "trail" if climb_rate >= TRAIL_CLIMB_RATE_M_PER_KM else "road"
