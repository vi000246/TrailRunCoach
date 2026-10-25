"""
The interval verdict per athlete (SP-69; docs/research/estimated-constants-inventory.md
§4.1 #1; engine/calibrate.py items).

Whether an interval session is 達標 — and so whether the ladder moves
(quality_gate.interval_outcome / dose_step, interval_eval) — hangs on three numbers. One is
per athlete, its 推估 default (interval-adaptation.md §4.2) shrunk toward the athlete's data:

  interval_in_band_tol  a rep is in band at ≥ this × the planned lower bound
                        (quality_gate.IN_BAND_TOL 0.98). The 2 % is there because the CP
                        the band hangs on is itself an estimate. Fit: 1 − e, e = half the
                        median |change| between consecutive CP tests (the plan's dated CP
                        rows) ≤ CP_PAIR_MAX_DAYS apart — a session sits on average halfway
                        between two tests, so half the usual step is how far off the CP in
                        effect usually is (推估). ≥ 3 pairs; 0.95–0.99; k = 5.

Retired (SP-526, owner 2026-10-14 — SP-518 Q1-1 / Q1-2 A: coach consensus, no personalisation;
docs/research/coach-consensus-sp518/E-personalized.md §1):
  interval_last_fade    now quality_gate.last_fade_for — Hunter Allen's table by rep length;
                        a single P90 over sessions of every length could not say that.
  interval_tiz_goal     now quality_gate.TIZ_GOAL 0.80 — TrainingPeaks, intervals.icu and
                        TrainerPlan all call 80 % 「completed」; the P10 fit was also circular
                        (fitted on sessions the default verdict picked).
Their stored settings rows (athlete.calib.interval_last_fade / interval_tiz_goal, fitted or
typed in 進階設定) are no longer read: not registered, so not listed, not fitted, not
written, and the settings layer treats the keys as unknown (upstream copies skip them). They
are left in the database, untouched — nothing reads them.

Read with in_band_tol() (stored fit / manual / default); calibrate.basis(TOL) gives the words
for the texts that quote the number.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL
from backend.i18n import N_

# the default = quality_gate.IN_BAND_TOL (推估; test_constant_calib keeps them equal)
DEFAULT_TOL = 0.98
TOL_BOUNDS = (0.95, 0.99)    # 推估
MIN_CP_PAIRS = 3             # 推估
CP_PAIR_MAX_DAYS = 182       # 推估: tests further apart say more about the season than about the CP's error

TOL = "interval_in_band_tol"
FADE, TIZ = "interval_last_fade", "interval_tiz_goal"       # retired (SP-526): stored rows are not read
RETIRED = (FADE, TIZ)


def _clip(v: float, bounds: tuple[float, float]) -> float:
    return min(max(float(v), bounds[0]), bounds[1])


# ---- interval_in_band_tol: the CP tests ------------------------------------------------

def cp_changes(rows: list[tuple[str, float]], today: dt.date) -> list[float]:
    """|cp_b ÷ cp_a − 1| of consecutive dated CP values [(iso date, W)] up to `today`,
    1 … CP_PAIR_MAX_DAYS days apart."""
    pts = sorted((dt.date.fromisoformat(str(d)[:10]), float(v)) for d, v in rows if v)
    pts = [p for p in pts if p[0] <= today]
    return [abs(b / a - 1.0) for (d0, a), (d1, b) in zip(pts, pts[1:]) if 0 < (d1 - d0).days <= CP_PAIR_MAX_DAYS]


def fit_tol_rows(rows: list[tuple[str, float]], today: dt.date) -> Optional[CAL.Fit]:
    ch = cp_changes(rows, today)
    if len(ch) < MIN_CP_PAIRS:
        return CAL.count_only(len(ch))                  # SP-807: the count below min_n
    return CAL.Fit(_clip(1.0 - float(np.median(ch)) / 2.0, TOL_BOUNDS),
                   float(np.std(ch)) / 2.0 / math.sqrt(len(ch)), len(ch))


def fit_in_band_tol(ds, today: Optional[dt.date]):
    rows = [(t.date, t.cp) for t in (getattr(getattr(ds, "plan", None), "thresholds", None) or [])
            if getattr(t, "cp", None)]
    return fit_tol_rows(rows, today or dt.date.today())


CAL.register(CAL.Item(
    name=TOL, label=N_("間歇：每趟的達標線（× 目標下限）"), unit="", default=DEFAULT_TOL,
    default_src=N_("推估（比目標下限低 2% 以內都算做到）"), k=5, min_n=MIN_CP_PAIRS, fit=fit_in_band_tol,
    bounds=TOL_BOUNDS, digits=3,
    help=N_("間歇每一趟的平均功率到「目標下限 × 這個數」就算做到；留一點空間，是因為 CP 本身是估出來的。"
            "本人值 = 1 − 你相鄰兩次 CP 測試差距（取中位數）的一半；1 筆 = 一組相鄰的 CP 測試（間隔半年內）。"
            "CP 測試越穩，這個數越接近 1。")))


def in_band_tol() -> float:
    return CAL.value(TOL)
