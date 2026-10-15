"""
The easy-run HR margin per athlete (SP-69; docs/research/estimated-constants-inventory.md
§4.1 #5; engine/calibrate.py item).

  easy_hr_margin_bpm   「輕鬆」 = average HR ≤ AeT + this: the upper edge of
                       quality_gate.FRIEL_HR_BAND (default = workout_review.AET_MARGIN, 3 bpm).
                       adapt's rule D read it until SP-301 (2026-10-06: 偏強 = power / TSS, without power HR > 94 % LTHR;
                       since SP-565 HR > the easy-run cap + 3, hr_profile.over_easy).
                       Fit: the standard error of the athlete's aggregated AeT estimate
                       (threshold_estimate.aet_aggregate on drift_agg.aet_points — the one
                       the AeT's validity already reads), never below 3 bpm (Lamberts &
                       Lambert 2009: day-to-day HR 3 ± 1 bpm): an AeT known to ± 5 bpm
                       shouldn't call a run 3 bpm over it 「偏強」. ≥ 6 drift points
                       (threshold_estimate.AET_MIN_RUNS); 3–6 bpm; k = 6.

Retired (SP-526, owner 2026-10-14 — SP-518 Q1-1 A, coach consensus, no personalisation;
docs/research/coach-consensus-sp518/E-personalized.md §2): lthr_test_age_days, how old the last LTHR
test may get before the 測試 indicator says so. Now threshold_confidence.TEST_AGE_DAYS 56 for everyone —
the coaches' mode and median (Friel, TrainingPeaks, McMillan, RunnersConnect: every 4–8 weeks); no LTHR
coach supports the 12–24 weeks the fit could reach. Its stored row (athlete.calib.lthr_test_age_days) is
no longer registered, so not listed, fitted or read; it is left in the database, untouched.

The bounds and the k are 推估. The margin is read by
quality_gate.friel_band; workout_review's own AET_MARGIN (the session classifier, the
time-above-AeT share, the charts) stays 3 — those numbers are cached per activity.

Not items (found while doing SP-69, nothing left to fit): quality_gate.LTHR_FRESH_DAYS
(84 d) was removed on 2026-10-05 — a measured LTHR has no fixed expiry, an event
invalidates it (lthr_invalid); quality_gate.AET_FRESH_DAYS (112 d) no longer decides
anything — evaluate() replaces aet_info's "fresh" with the aggregated estimate's validity
(unsourced-rules.md §B3).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.engine import calibrate as CAL
from backend.i18n import N_

DEFAULT_MARGIN = 3.0             # = workout_review.AET_MARGIN; Lamberts & Lambert 2009
MARGIN_BOUNDS = (3.0, 6.0)       # 推估: never tighter than the day-to-day variation, at most twice it
MIN_POINTS = 6                   # = threshold_estimate.AET_MIN_RUNS

MARGIN = "easy_hr_margin_bpm"
AGE = "lthr_test_age_days"         # retired (SP-526): the stored row is not read


def _clip(v: float, bounds: tuple[float, float]) -> float:
    return min(max(float(v), bounds[0]), bounds[1])


# ---- easy_hr_margin_bpm ------------------------------------------------------------------

def margin_fit(se: Optional[float], n: int) -> Optional[CAL.Fit]:
    """max(3 bpm, the aggregated AeT estimate's SE) on `n` drift points; None without an SE."""
    if se is None or not math.isfinite(se) or se < 0:
        return None
    return CAL.Fit(_clip(max(DEFAULT_MARGIN, float(se)), MARGIN_BOUNDS), None, int(n))


def fit_easy_margin(ds, today: Optional[dt.date]):
    from backend.engine import drift_agg as DA
    from backend.engine.algorithms import threshold_estimate as TE
    today = today or dt.date.today()
    on = getattr(getattr(ds, "plan", None), "threshold_on", None)
    lthr = on("lthr", today) if on is not None else None
    pts = DA.aet_points(ds, today)
    agg = TE.aet_aggregate([(p["hr1"], p["drift"], p["se"]) for p in pts], lthr=lthr)
    return margin_fit(agg.se, agg.n)


CAL.register(CAL.Item(
    name=MARGIN, label=N_("輕鬆跑的心率餘裕（AeT 以上幾 bpm 還算輕鬆）"), unit="bpm", default=DEFAULT_MARGIN,
    default_src=N_("心率每天自然的落差約 3 bpm（Lamberts 2009），不是教練允許的餘裕"), k=6, min_n=MIN_POINTS,
    fit=fit_easy_margin, bounds=MARGIN_BOUNDS, digits=1,
    help=N_("有氧基礎的飄移檢查判斷「在 AeT 附近」時，上緣是 AeT 加這個數。"
            "輕鬆跑偏強不用這個數（看功率和訓練負荷（Training load）；沒有功率時看平均心率有沒有超過輕鬆跑上限 +3）。"
            "本人值 = 你的 AeT 估得有多不準（多次輕鬆跑心率飄移回歸的標準誤），最少 3；"
            "1 筆 = 一次可用的輕鬆跑飄移（半年內）。AeT 估得越準，這個數越接近 3。")))


def easy_margin() -> float:
    return CAL.value(MARGIN)


def margin_fields() -> dict:
    """{"aet_margin", "aet_margin_basis"}: the margin in effect and whose it is
    (calibrate.basis). adapt's rule D no longer reads it (SP-301)."""
    return {"aet_margin": easy_margin(), "aet_margin_basis": CAL.basis(MARGIN)}
