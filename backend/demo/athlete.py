"""The demo athlete 「示範跑者」 — every number here is invented for the demo
(docs/plans/auth-and-demo.plan.md §3.2); nothing is derived from a real
person's data."""
from __future__ import annotations

NAME = "示範跑者"
AGE = 36
SEX = "male"
HEIGHT_CM = 172
WEIGHT_KG = 62.0
CP_W = 255.0                # current Stryd CP (the dated thresholds rise 240 -> 255 -> 260)
CP_HISTORY = (240.0, 255.0, 260.0)
LTHR = 168.0
AETHR = 150.0
HRMAX = 188.0
HR_REST = 50.0
ECOR = 1.0                  # J/kg/m on the flat (Stryd's running effectiveness ~1.0)
PACK_KG = 9.0               # 百岳 multi-day pack
TZ_OFFSET_H = 8             # Asia/Taipei

# where the fictional tracks are drawn (synthetic curves, never a real route)
AREAS = {
    "road": (24.1460, 120.7240, 60.0),      # a riverside park loop (fictional)
    "hill": (24.1185, 120.7860, 140.0),
    "trail": (24.0610, 120.8620, 320.0),
    "baiyue": (24.2940, 121.2380, 2800.0),  # a high-mountain traverse (fictional)
}


def hr_steady(frac: float) -> float:
    """Steady-state heart rate at a power fraction of CP (invented curve:
    0.78 CP -> ~AeT, 1.0 CP -> LTHR)."""
    f = max(0.0, frac)
    return 62.0 + 106.0 * f ** 0.85


def hr_steady_vec(frac):
    import numpy as np
    return 62.0 + 106.0 * np.maximum(np.asarray(frac, dtype=float), 0.0) ** 0.85


def profile() -> dict:
    """plan.json `profile` (engine/athlete_profile.py)."""
    return {"sex": SEX, "height_cm": HEIGHT_CM, "power_source": "stryd"}


def birth_year(anchor_year: int) -> int:
    return anchor_year - AGE
