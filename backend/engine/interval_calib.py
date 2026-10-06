"""
The interval verdict per athlete (SP-69; docs/research/estimated-constants-inventory.md
§4.1 #1; engine/calibrate.py items).

Three numbers decide whether an interval session is 達標 — and so whether the ladder
moves (quality_gate.interval_outcome / dose_step, interval_eval). All three are 推估
(interval-adaptation.md §4.2–4.3, interval-prescription.md §C2) and were one value for
everyone; each is now that default shrunk toward the athlete's own data:

  interval_in_band_tol  a rep is in band at ≥ this × the planned lower bound
                        (quality_gate.IN_BAND_TOL 0.98). The 2 % is there because the CP
                        the band hangs on is itself an estimate. Fit: 1 − e, e = half the
                        median |change| between consecutive CP tests (the plan's dated CP
                        rows) ≤ CP_PAIR_MAX_DAYS apart — a session sits on average halfway
                        between two tests, so half the usual step is how far off the CP in
                        effect usually is (推估). ≥ 3 pairs; 0.95–0.99; k = 5.
  interval_last_fade    only the last rep fell out of the band: a drop (1 − last ÷ first)
                        up to this is still 達標 (LAST_FADE 0.05). Fit: p90 of that drop
                        over the athlete's planned sessions that did every rep with reps
                        1 … n−1 in band — what a session that went right looks like for
                        this runner. The selection never reads the last rep's verdict, so
                        the fitted value can move either way. ≥ 20 sessions
                        (unsourced-rules.md §0.5.3: 「≥ 20 堂後可調」); 0.03–0.10; k = 20.
  interval_tiz_goal     time in the target zone ≥ this × the variant's plan (TIZ_GOAL
                        0.85). Fit: p10 of that ratio over the planned sessions that did
                        every rep in band. The report names the number but gives no method:
                        this one mirrors the fade's (推估, SP-69). ≥ 20 sessions; 0.75–0.90;
                        k = 20.

Sessions: quality_gate.dose_history over the last year, planned ones only (an unplanned
run has no band to be judged against). "In band" in the two selections uses the DEFAULT
tolerance, so one fitted number never moves another's sample. Bounds, k and the
percentiles are 推估; every fit is clipped to its bounds before the shrinkage, so one odd
season cannot drag the stored value to an edge.

Read with in_band_tol() / last_fade() / tiz_goal() (stored fit / manual / default);
calibrate.basis(name) gives the words for the texts that quote the number.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL
from backend.i18n import N_

# the defaults = quality_gate.IN_BAND_TOL / LAST_FADE / TIZ_GOAL (推估; test_interval_calib keeps them equal)
DEFAULT_TOL, DEFAULT_FADE, DEFAULT_TIZ = 0.98, 0.05, 0.85
TOL_BOUNDS, FADE_BOUNDS, TIZ_BOUNDS = (0.95, 0.99), (0.03, 0.10), (0.75, 0.90)   # 推估
MIN_SESSIONS = 20            # unsourced-rules.md §0.5.3 「≥ 20 堂後可調」
MIN_CP_PAIRS = 3             # 推估
CP_PAIR_MAX_DAYS = 182       # 推估: tests further apart say more about the season than about the CP's error
DAYS = 365

TOL, FADE, TIZ = "interval_in_band_tol", "interval_last_fade", "interval_tiz_goal"


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
        return None
    return CAL.Fit(_clip(1.0 - float(np.median(ch)) / 2.0, TOL_BOUNDS),
                   float(np.std(ch)) / 2.0 / math.sqrt(len(ch)), len(ch))


def fit_in_band_tol(ds, today: Optional[dt.date]):
    rows = [(t.date, t.cp) for t in (getattr(getattr(ds, "plan", None), "thresholds", None) or [])
            if getattr(t, "cp", None)]
    return fit_tol_rows(rows, today or dt.date.today())


# ---- interval_last_fade / interval_tiz_goal: the planned sessions --------------------------

def sessions(ds, today: dt.date, days: int = DAYS) -> list[dict]:
    """The planned interval sessions of the `days` before `today` as plain rows
    {"powers": W of the planned reps done, "planned": n, "floor": default tolerance ×
    lower bound × CP, "tiz_ratio"}."""
    from backend.engine import quality_gate as QG
    out = []
    for h in QG.dose_history(ds, today, days):
        spec = None if h.get("unplanned") else QG.planned_variant_spec(h)
        if spec is None or not h.get("cp"):
            continue
        if hasattr(spec, "works"):
            spec = QG.variant_tuple(spec)
        n, lo = int(spec[2] or 0), spec[5]
        ps = [float(b["power"]) for b in (h.get("bouts") or [])[:n] if b.get("power")]
        if not n or lo is None or not ps:
            continue
        out.append({"powers": ps, "planned": n, "floor": DEFAULT_TOL * float(lo) * float(h["cp"]),
                    "tiz_ratio": h.get("tiz_ratio")})
    return out


def _sessions_of(ds, today: dt.date) -> list[dict]:
    """sessions() once per Dataset: the two fits below read the same scan (kept on the
    Dataset, gone with it; re-read when the day or the number of activities changes)."""
    stamp = (today, len(getattr(ds, "workouts", None) or ()))
    hit = getattr(ds, "_interval_calib", None)
    if hit and hit[0] == stamp:
        return hit[1]
    rows = sessions(ds, today)
    try:
        ds._interval_calib = (stamp, rows)
    except AttributeError:                  # a Dataset stub without attributes: scan again next time
        pass
    return rows


def last_drops(rows: list[dict]) -> list[float]:
    """1 − last ÷ first rep power of the sessions that did every planned rep (≥ 2) with
    reps 1 … n−1 in band."""
    out = []
    for r in rows:
        ps, n = r["powers"], r["planned"]
        if n < 2 or len(ps) < n or any(p < r["floor"] for p in ps[:n - 1]):
            continue
        out.append(1.0 - ps[n - 1] / ps[0])
    return out


def tiz_ratios(rows: list[dict]) -> list[float]:
    """Time in zone ÷ the plan (≤ 1) of the sessions that did every planned rep in band."""
    return [min(float(r["tiz_ratio"]), 1.0) for r in rows
            if r.get("tiz_ratio") is not None and len(r["powers"]) >= r["planned"]
            and all(p >= r["floor"] for p in r["powers"][:r["planned"]])]


def _pct_fit(vals: list[float], q: float, bounds: tuple[float, float]) -> Optional[CAL.Fit]:
    if len(vals) < MIN_SESSIONS:
        return None
    return CAL.Fit(_clip(float(np.percentile(vals, q)), bounds), float(np.std(vals)) / math.sqrt(len(vals)), len(vals))


def fit_fade_rows(rows: list[dict]) -> Optional[CAL.Fit]:
    return _pct_fit(last_drops(rows), 90, FADE_BOUNDS)


def fit_tiz_rows(rows: list[dict]) -> Optional[CAL.Fit]:
    return _pct_fit(tiz_ratios(rows), 10, TIZ_BOUNDS)


def fit_last_fade(ds, today: Optional[dt.date]):
    return fit_fade_rows(_sessions_of(ds, today or dt.date.today()))


def fit_tiz_goal(ds, today: Optional[dt.date]):
    return fit_tiz_rows(_sessions_of(ds, today or dt.date.today()))


CAL.register(CAL.Item(
    name=TOL, label=N_("間歇：每趟的達標線（× 目標下限）"), unit="", default=DEFAULT_TOL,
    default_src=N_("推估（比目標下限低 2% 以內都算做到）"), k=5, min_n=MIN_CP_PAIRS, fit=fit_in_band_tol,
    bounds=TOL_BOUNDS, digits=3,
    help=N_("間歇每一趟的平均功率到「目標下限 × 這個數」就算做到；留一點空間，是因為 CP 本身是估出來的。"
            "本人值 = 1 − 你相鄰兩次 CP 測試差距（取中位數）的一半；1 筆 = 一組相鄰的 CP 測試（間隔半年內）。"
            "CP 測試越穩，這個數越接近 1。")))
CAL.register(CAL.Item(
    name=FADE, label=N_("間歇：最後一趟可以掉多少"), unit=N_("比例"), default=DEFAULT_FADE,
    default_src=N_("推估（比第一趟掉 5% 以內）"), k=20, min_n=MIN_SESSIONS, fit=fit_last_fade,
    bounds=FADE_BOUNDS, digits=2,
    help=N_("只有最後一趟沒到目標時，它比第一趟掉的幅度在這個比例以內仍算達標，超過就同一份課表再做一次。"
            "本人值 = 你照課表做完、前面每趟都達標的間歇課裡，最後一趟掉幅的第 90 百分位；"
            "1 筆 = 一堂這樣的間歇課（一年內）。")))
CAL.register(CAL.Item(
    name=TIZ, label=N_("間歇：目標區時間要做到計畫的多少"), unit=N_("比例"), default=DEFAULT_TIZ,
    default_src=N_("推估（計畫的 85%）"), k=20, min_n=MIN_SESSIONS, fit=fit_tiz_goal,
    bounds=TIZ_BOUNDS, digits=2,
    help=N_("每趟都達標，但待在目標功率區的時間不到計畫的這個比例，就算「部分達到」，同一階再做一次。"
            "本人值 = 你照課表做完、每趟都達標的間歇課裡，這個比例的第 10 百分位；"
            "1 筆 = 一堂這樣的間歇課（一年內）。")))


def in_band_tol() -> float:
    return CAL.value(TOL)


def last_fade() -> float:
    return CAL.value(FADE)


def tiz_goal() -> float:
    return CAL.value(TIZ)
