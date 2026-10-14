"""
The intensity of easy runs and of RPE steps per athlete (SP-511; SP-69 #6,
docs/research/estimated-constants-inventory.md §4.1 #6; engine/calibrate.py items).

Two numbers of engine/workout_steps.py were one value for everyone (推估) and decide the
planned TSS of the steps that have no power / HR target of their own:

  easy_run_if    ≈ % CP of an easy run (workout_steps.EASY_F 0.78; Palladino EZ ≤ 80 % CP).
                 The anchor of the athlete's easy speed in speed_kmh: a distance step is timed
                 by interpolating between the easy speed at this fraction and the threshold
                 pace at 100 % — so it sets the time (and the TSS) of every distance step.
                 Fit: the median IF of the athlete's easy flat road runs of the last
                 WINDOW_WEEKS — the same runs (equivalence._flat_speed: road, ≤ 15 m/km,
                 ≥ 20 min, avg HR ≤ AeT + 3) whose median speed is that easy speed, so the
                 anchor's speed and its % CP come from one set. ≥ 10 runs (the report);
                 0.65–0.85; k = 10.
  rpe_if_scale   × the RPE → % CP table (workout_steps.RPE_FRAC, 1.00 = the table as is):
                 an RPE target step (技術地形／下坡, SP-62) counts at RPE_FRAC[rpe] × this
                 for the chart and the TSS. Fit: the activities the athlete rated (the FIT's
                 own RPE or COROS's post-run rating, activity_tags.load_recorded — the same
                 ratings rpe_load fits) of the last DAYS days; each RPE value (rounded
                 1–10) with ≥ MIN_PER_RPE activities gives median IF ÷ RPE_FRAC[value]; the
                 scale is their mean weighted by the activities. One number keeps the table's
                 shape (a higher RPE never counts less); session RPE rates the whole run,
                 warm-up included, so it is a 推估 of a step's intensity. ≥ 3 activities of
                 one RPE value (the report: 「RPE 每一級 3 次」); 0.85–1.15; k = 10.

The activity's IF is the one its TSS was counted with (wko5expr Dataset._metrics): NP ÷ CP
with power; without power the threshold-pace IF (rTSS) or the HR IF (hrTSS). So a runner
without a power meter gets the same fit in the units their planned and recorded TSS use; an
activity without any IF (no threshold set) is left out, and with too few the default stands.

What stays (nothing to fit it on): workout_steps.NONE_IF — warm-up / cool-down / rest count
at the runner's own easy TSS rate since SP-437 (Ctx.easy_tph) when it is known; the work
step's 1.05 is an all-out bout with no target.

The watch never sees these numbers: a 「負荷」 step with an RPE target is timed and converted
to COROS TL at the default table (workout_steps.load_if), and distance / time steps are pushed
as distance / time — so a refit changes the plan's TSS, never a pushed workout (no re-push).

Bounds, k, the windows and the per-value minimum are 推估; every fit is clipped to its bounds
before the shrinkage (calibrate.shrink clips again after it). Read with easy_if() / rpe_scale().
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL
from backend.i18n import N_

DEFAULT_EASY_IF = 0.78           # = workout_steps.EASY_F (Palladino EZ ≤ 80 % CP; 推估)
EASY_BOUNDS = (0.65, 0.85)       # 推估: running-power easy 65–80 % CP; above 0.85 is Palladino zone 2
MIN_EASY = 10                    # the report: 輕鬆跑 10 次
WINDOW_WEEKS = 26                # = equivalence.WINDOW_WEEKS (the easy speed's window)
FLAT_M_PER_KM = 15.0             # = equivalence.FLAT_M_PER_KM
MIN_MINUTES = 20.0               # = equivalence.MIN_MINUTES
EASY_HR_TOL = 3.0                # = equivalence.EASY_HR_TOL

DEFAULT_SCALE = 1.0              # the RPE_FRAC table as is
SCALE_BOUNDS = (0.85, 1.15)      # 推估: ± 15 % ≈ one Palladino zone
MIN_PER_RPE = 3                  # the report: RPE 每一級 3 次
DAYS = 365                       # 推估: a rating's IF depends on the threshold of its day
RPE_MIN_MINUTES, RPE_MAX_HOURS = 10.0, 20.0       # = rpe_load.MIN_MINUTES / MAX_HOURS
IF_RANGE = (0.3, 1.5)            # an IF outside: a mis-recorded activity or a wrong threshold

EASY, SCALE = "easy_run_if", "rpe_if_scale"


def _clip(v: float, bounds: tuple[float, float]) -> float:
    return min(max(float(v), bounds[0]), bounds[1])


def _if(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and IF_RANGE[0] <= f <= IF_RANGE[1] else None


# ---- easy_run_if --------------------------------------------------------------------------

def is_easy(row: dict) -> bool:
    """A row {km, gain, minutes, hr, aet, if} is an easy flat road run (equivalence's easy speed)."""
    km, mins, hr, aet = row.get("km") or 0.0, row.get("minutes") or 0.0, row.get("hr"), row.get("aet")
    return (km >= 1.0 and mins >= MIN_MINUTES and (row.get("gain") or 0.0) / km <= FLAT_M_PER_KM
            and hr is not None and aet is not None and hr <= aet + EASY_HR_TOL and _if(row.get("if")) is not None)


def fit_easy_rows(rows: list[dict]) -> Optional[CAL.Fit]:
    """The median IF of the easy runs; with fewer than MIN_EASY the Fit only carries the count
    (calibrate.shrink writes nothing below min_n; the run's report says 「n=8<10」)."""
    vals = [_if(r["if"]) for r in rows if is_easy(r)]
    if not vals:
        return None
    return CAL.Fit(_clip(float(np.median(vals)), EASY_BOUNDS), float(np.std(vals)) / math.sqrt(len(vals)), len(vals))


def easy_rows(ds, today: dt.date) -> list[dict]:
    """The road runs of the WINDOW_WEEKS before `today` as rows {km, gain, minutes, hr (avg), aet
    (of that day), if} — equivalence.samples_from's road runs with their IF."""
    from backend.engine import overview as O
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.evaluator import WS, Evaluator
    lo = today - dt.timedelta(weeks=WINDOW_WEEKS)
    ws = [w for w in O.workouts_between(ds, lo, today + dt.timedelta(days=1))
          if w.sport == "run" and O.category(w) == "road"]
    if not ws:
        return []
    b, e = int(date_to_day(lo)), int(date_to_day(today))
    hr = Evaluator(ds, b, e).evaluate(f"athleterange({b}, {e}, avg(heartrate))")
    hr = hr if isinstance(hr, WS) else {}
    out = []
    for w in ws:
        m = w.metrics
        h = hr.get(w.idx) if hasattr(hr, "get") else None
        h = float(h) if h is not None and h == h and h > 60 else None
        try:
            aet = ds.aethr(w)
        except Exception:                   # noqa: BLE001 — a Dataset without thresholds: not easy
            aet = None
        out.append({"km": O._n(m.get("distance")) or 0.0, "gain": O._n(m.get("climbing")) or 0.0,
                    "minutes": O.moving_s(w) / 60.0, "hr": h, "aet": aet, "if": m.get("if")})
    return out


def fit_easy_if(ds, today: Optional[dt.date]):
    return fit_easy_rows(easy_rows(ds, today or dt.date.today()))


# ---- rpe_if_scale -------------------------------------------------------------------------

def rpe_value(v) -> Optional[int]:
    """A rating 1–10 rounded half up (rpe_load.level_of_rpe's rounding); None outside."""
    try:
        r = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or not 0 < r <= 10:
        return None
    return min(10, max(1, int(math.floor(r + 0.5))))


def fit_scale_rows(rows: list[dict]) -> Optional[CAL.Fit]:
    """rows {rpe, if}: the activity-weighted mean of median IF ÷ RPE_FRAC over the RPE values
    with ≥ MIN_PER_RPE activities; n = those activities."""
    from backend.engine.workout_steps import RPE_FRAC
    by: dict = {}
    for r in rows:
        k, f = rpe_value(r.get("rpe")), _if(r.get("if"))
        if k is not None and f is not None:
            by.setdefault(k, []).append(f)
    use = {k: v for k, v in by.items() if len(v) >= MIN_PER_RPE}
    n = sum(len(v) for v in use.values())
    if not n:
        # no RPE value has MIN_PER_RPE runs: only the count of the best-covered value (shrink writes nothing)
        return CAL.Fit(DEFAULT_SCALE, None, max(len(v) for v in by.values())) if by else None
    s = sum(len(v) * float(np.median(v)) / RPE_FRAC[k] for k, v in use.items()) / n
    ratios = [f / RPE_FRAC[k] for k, v in use.items() for f in v]
    return CAL.Fit(_clip(s, SCALE_BOUNDS), float(np.std(ratios)) / math.sqrt(n), n)


def rated_rows(ds, today: dt.date, recorded: Optional[list] = None) -> list[dict]:
    """The runs of the DAYS before `today` with a recorded rating, as rows {rpe, if}."""
    from backend.engine import activity_tags as AT
    from backend.engine.wko5expr.dataset import day_to_date
    recorded = AT.load_recorded() if recorded is None else recorded
    if not recorded:
        return []
    lo = today - dt.timedelta(days=DAYS)
    out = []
    for w in ds.workouts:
        if w.sport != "run" or not lo < day_to_date(w.day) <= today:
            continue
        rec = AT.recorded_of(recorded, w.entry.start, getattr(w.entry, "file", None))
        rpe = (rec or {}).get("rpe")
        if rpe is None:
            continue
        m = w.metrics
        secs = m.get("movingduration") or m.get("duration")
        try:
            h = float(secs) / 3600.0
        except (TypeError, ValueError):
            continue
        if not RPE_MIN_MINUTES <= h * 60.0 or h > RPE_MAX_HOURS:
            continue
        out.append({"rpe": rpe, "if": m.get("if")})
    return out


def fit_rpe_scale(ds, today: Optional[dt.date]):
    return fit_scale_rows(rated_rows(ds, today or dt.date.today()))


CAL.register(CAL.Item(
    name=EASY, label=N_("輕鬆跑的強度（× CP）"), unit="", default=DEFAULT_EASY_IF,
    default_src=N_("推估（Palladino 輕鬆區 80% CP 以下，取 0.78）"), k=10, min_n=MIN_EASY, fit=fit_easy_if,
    bounds=EASY_BOUNDS, digits=2,
    help=N_("你的輕鬆路跑速度對應的強度比（Intensity）。課表裡用距離寫的段，用這個數和閾值配速之間內插出每段的速度，"
            "決定這段要跑多久、算多少訓練負荷（Training load）。"
            "本人值 = 你近半年輕鬆平路跑（平均心率 ≤ AeT + 3、爬升每公里 15 m 以內、20 分鐘以上）強度比的中位數；"
            "1 筆 = 一次這樣的跑步。沒有功率時用心率或配速算的強度比。只影響計畫的訓練負荷，不會改已推到手錶的課。")))
CAL.register(CAL.Item(
    name=SCALE, label=N_("RPE 步驟的強度倍率"), unit="", default=DEFAULT_SCALE,
    default_src=N_("推估（RPE 1–10 對 55–110% CP 的表照用）"), k=10, min_n=MIN_PER_RPE, fit=fit_rpe_scale,
    bounds=SCALE_BOUNDS, digits=2,
    help=N_("用 RPE 當目標的步驟（技術地形、下坡）照「RPE 對 % CP 表 × 這個數」算強度，決定這段的訓練負荷（Training load）。"
            "本人值 = 你一年內有自評 RPE 的跑步：同一個 RPE 至少 3 次才算，實際強度比（Intensity）的中位數 ÷ 表上的值，"
            "依筆數平均；1 筆 = 一次有自評的跑步。自評是整趟的感覺（含暖身），所以是推估。"
            "只影響計畫的訓練負荷，不會改已推到手錶的課。")))


def easy_if() -> float:
    """≈ % CP of this athlete's easy run in effect (stored fit / manual / default)."""
    try:
        return CAL.value(EASY)
    except Exception:                       # noqa: BLE001 — no DB: the default
        return DEFAULT_EASY_IF


def rpe_scale() -> float:
    """× RPE_FRAC in effect (stored fit / manual / default)."""
    try:
        return CAL.value(SCALE)
    except Exception:                       # noqa: BLE001 — no DB: the default
        return DEFAULT_SCALE
