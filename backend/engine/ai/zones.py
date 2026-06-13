"""Compute power/HR training zone boundaries from settings.

Percentages mirror the Coggan/Palladino zone definitions used elsewhere
(`backend/api/workouts.py`) so the coach and the per-workout zone charts agree.
"""
from typing import Optional

POWER_ZONES = [
    (1, "Recovery", 0.00, 0.55),
    (2, "Endurance", 0.55, 0.75),
    (3, "Tempo", 0.75, 0.90),
    (4, "Threshold", 0.90, 1.05),
    (5, "VO2max", 1.05, 1.20),
    (6, "Anaerobic", 1.20, 1.50),
    (7, "Neuromuscular", 1.50, 99.0),
]
HR_ZONES = [
    (1, "Recovery", 0.00, 0.85),
    (2, "Aerobic", 0.85, 0.90),
    (3, "Tempo", 0.90, 0.95),
    (4, "Threshold", 0.95, 1.00),
    (5, "VO2max", 1.00, 99.0),
]


def compute_zones(run_ftp_w: Optional[float], lthr: Optional[int]) -> dict:
    power = []
    if run_ftp_w and run_ftp_w > 0:
        power = [
            {
                "zone": z, "name": n,
                "low_w": round(run_ftp_w * lo, 1),
                "high_w": round(run_ftp_w * hi, 1) if hi < 90 else None,
            }
            for z, n, lo, hi in POWER_ZONES
        ]
    hr = []
    if lthr and lthr > 0:
        hr = [
            {
                "zone": z, "name": n,
                "low_bpm": round(lthr * lo, 1),
                "high_bpm": round(lthr * hi, 1) if hi < 90 else None,
            }
            for z, n, lo, hi in HR_ZONES
        ]
    return {"power": power, "hr": hr}
