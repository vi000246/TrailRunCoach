"""
Threshold age and the easy-run HR margin per athlete (SP-69; docs/research/
estimated-constants-inventory.md §4.1 #3 and #5; engine/calibrate.py items).

  lthr_test_age_days   how old the last LTHR test may get before the 測試 indicator says
                       so (threshold_confidence.TEST_AGE_DAYS 56 — Friel: every 4–8 weeks,
                       coach; a hint, it never changes the plan). Fit: how fast this
                       runner's LTHR has moved between tests. Consecutive tests of the same
                       kind (friel30 / race / lab — an applied estimate or a typed value is
                       not a test, threshold_confidence.lthr_info) PAIR_MIN_DAYS …
                       PAIR_MAX_DAYS apart give |Δ bpm| per 30 days; the age = the days it
                       takes to move MOVED_BPM at the median rate. Slow → longer, fast →
                       shorter, 8–24 weeks (the report's range). ≥ 2 pairs (3 tests); k = 4.
  easy_hr_margin_bpm   「輕鬆」 = average HR ≤ AeT + this: the upper edge of
                       quality_gate.FRIEL_HR_BAND (default = workout_review.AET_MARGIN, 3 bpm).
                       adapt's rule D read it until SP-301 (2026-10-06: 偏強 = power / TSS, without power HR > 94 % LTHR).
                       Fit: the standard error of the athlete's aggregated AeT estimate
                       (threshold_estimate.aet_aggregate on drift_agg.aet_points — the one
                       the AeT's validity already reads), never below 3 bpm (Lamberts &
                       Lambert 2009: day-to-day HR 3 ± 1 bpm): an AeT known to ± 5 bpm
                       shouldn't call a run 3 bpm over it 「偏強」. ≥ 6 drift points
                       (threshold_estimate.AET_MIN_RUNS); 3–6 bpm; k = 6.

MOVED_BPM, the pair window, the bounds and the k are 推估. The margin is read by
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

import numpy as np

from backend.engine import calibrate as CAL
from backend.i18n import N_

DEFAULT_AGE_DAYS = 56.0          # = threshold_confidence.TEST_AGE_DAYS (Friel: every 4–8 weeks; coach)
AGE_BOUNDS = (56.0, 168.0)       # 8–24 weeks (estimated-constants-inventory.md §4.1 #3)
TEST_METHODS = ("friel30", "race", "lab")    # threshold_confidence.lthr_info: source_kind "test"
MOVED_BPM = 5.0                  # 推估: the app's 「閾值變了」 line (threshold_estimate.AET_SHIFT_BPM,
                                 # threshold_confidence.CPBAND_DIFF_BPM)
PAIR_MIN_DAYS, PAIR_MAX_DAYS = 28, 365       # 推估: closer = test noise, not change; further = another season
MIN_PAIRS = 2                    # 3 tests of one kind

DEFAULT_MARGIN = 3.0             # = workout_review.AET_MARGIN; Lamberts & Lambert 2009
MARGIN_BOUNDS = (3.0, 6.0)       # 推估: never tighter than the day-to-day variation, at most twice it
MIN_POINTS = 6                   # = threshold_estimate.AET_MIN_RUNS

AGE, MARGIN = "lthr_test_age_days", "easy_hr_margin_bpm"


def _clip(v: float, bounds: tuple[float, float]) -> float:
    return min(max(float(v), bounds[0]), bounds[1])


# ---- lthr_test_age_days ------------------------------------------------------------------

def rates(tests: list[tuple[str, float, str]], today: dt.date) -> list[float]:
    """|Δ bpm| per 30 days between consecutive tests of the same method, PAIR_MIN_DAYS …
    PAIR_MAX_DAYS apart. `tests` = [(iso date, bpm, method)] up to `today`."""
    by: dict = {}
    for d, v, m in tests:
        day = dt.date.fromisoformat(str(d)[:10])
        if v and day <= today:
            by.setdefault(m, []).append((day, float(v)))
    out = []
    for rows in by.values():
        rows.sort()
        for (d0, a), (d1, b) in zip(rows, rows[1:]):
            gap = (d1 - d0).days
            if PAIR_MIN_DAYS <= gap <= PAIR_MAX_DAYS:
                out.append(abs(b - a) / gap * 30.0)
    return out


def fit_age_rows(tests: list[tuple[str, float, str]], today: dt.date) -> Optional[CAL.Fit]:
    rs = rates(tests, today)
    if len(rs) < MIN_PAIRS:
        return None
    r = float(np.median(rs))
    days = AGE_BOUNDS[1] if r <= 0 else 30.0 * MOVED_BPM / r
    return CAL.Fit(_clip(days, AGE_BOUNDS), None, len(rs))


def fit_lthr_test_age(ds, today: Optional[dt.date]):
    from backend.engine.planning import threshold_method
    tests = []
    for t in (getattr(getattr(ds, "plan", None), "thresholds", None) or []):
        m = threshold_method(t, "lthr")
        if m in TEST_METHODS:
            tests.append((t.date, t.lthr, m))
    return fit_age_rows(tests, today or dt.date.today())


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
    name=AGE, label=N_("LTHR 測試多久提醒重測"), unit=N_("天"), default=DEFAULT_AGE_DAYS,
    default_src=N_("Friel：每 4–8 週測一次（教練級），取 8 週"), k=4, min_n=MIN_PAIRS, fit=fit_lthr_test_age,
    bounds=AGE_BOUNDS, digits=0,
    help=N_("上次 LTHR 測試超過這麼多天，測試建議裡會多一行提醒（只是提示，不會改課表）。"
            "本人值 = 照你歷次測試之間 LTHR 變動的速度，算出變動 5 bpm 要幾天：變得慢就拉長、變得快就縮短，範圍 8–24 週；"
            "1 筆 = 一組相鄰的同一種測試（間隔 4–52 週）。")))
CAL.register(CAL.Item(
    name=MARGIN, label=N_("輕鬆跑的心率餘裕（AeT 以上幾 bpm 還算輕鬆）"), unit="bpm", default=DEFAULT_MARGIN,
    default_src=N_("推估（每天的心率本來就差約 3 bpm，Lamberts & Lambert 2009）"), k=6, min_n=MIN_POINTS,
    fit=fit_easy_margin, bounds=MARGIN_BOUNDS, digits=1,
    help=N_("有氧基礎的飄移檢查判斷「在 AeT 附近」時，上緣是 AeT 加這個數。"
            "輕鬆跑偏強不用這個數（看功率和 TSS；沒有功率時看 LTHR 的 94%）。"
            "本人值 = 你的 AeT 估得有多不準（多次輕鬆跑心率飄移回歸的標準誤），最少 3；"
            "1 筆 = 一次可用的輕鬆跑飄移（半年內）。AeT 估得越準，這個數越接近 3。")))


def lthr_test_age() -> float:
    return CAL.value(AGE)


def easy_margin() -> float:
    return CAL.value(MARGIN)


def margin_fields() -> dict:
    """{"aet_margin", "aet_margin_basis"}: the margin in effect and whose it is
    (calibrate.basis). adapt's rule D no longer reads it (SP-301)."""
    return {"aet_margin": easy_margin(), "aet_margin_basis": CAL.basis(MARGIN)}
