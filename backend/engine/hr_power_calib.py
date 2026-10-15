"""
Heart rate → the structure chart's common intensity scale (SP-642; SP-630 Q1 A).

The 課表 structure chart and the planned TSS put every step on one scale, threshold = 1.0:
power ÷ CP, speed ÷ threshold speed, and a heart-rate step converted here. This replaces
workout_steps._HR_P (2026-10-02, no source at all).

1. The runner's own relation (personal; an engine/calibrate.py item, `hr_power_slope`)
   Per outdoor run with usable power of the last DAYS days, its steady flat stretches
   (zone_events.run_point: |grade| < 3 %, running, after the first 10 min, the first 2 min of
   each stretch dropped, ≥ 10 min with power CV < 10 %) — read only up to MAX_T_S into the run,
   and runs whose whole-run Pw:HR decoupling is > DRIFT_MAX left out (heart-rate drift of the
   later part: racepower/hrcap.py uses the same two limits). One point per run:
   x = median HR ÷ LTHR, y = time-weighted power ÷ CP (both as of the fit day).
   Below threshold the relation is a line through the threshold (1, 1):
       y = 1 + b·(x − 1)
   — a linear HR–work-rate relation below threshold (Åstrand & Ryhming 1954; %HRR ≈ %VO2R,
   Swain & Leutholtz 1997, Med Sci Sports Exerc 29:410–414), anchored where the scale is
   defined. b = the median of the runs' (y − 1) ÷ (x − 1) (robust to an optical-HR outlier),
   only points at least MIN_GAP under the threshold. ≥ MIN_RUNS runs (= hrcap min_runs), else
   nothing is written. Shrunk toward the literature's own slope (below) with k = K.
   Range check: b within BOUNDS — the slope of a line through rest (HR = rest, P = 0) and
   threshold is 1 ÷ (1 − rest/LTHR); rest/LTHR between 0.15 (30 / 200 bpm) and 0.55 (90 / 165)
   gives 1.18–2.22 (推估). Above threshold the literature part below is used as is (HR lags a
   change of power, short reps never show a steady HR there).

2. Too little data: the literature table (推估)
   Joe Friel, "Quick Guide to Setting Zones" (TrainingPeaks,
   https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/;
   also The Triathlete's Training Bible, 4th ed. 2016): the run HR zones in % LTHR and the run
   pace zones in % threshold pace, both from the same 30-min time trial — zones.FRIEL_HR and
   zones.FRIEL_PACE. Each zone edge of one is paired with the same zone's edge of the other
   (85 % LTHR ↔ 129 % pace, 90 ↔ 114, 95 ↔ 106, 100 ↔ 100, 103 ↔ 97, 106 ↔ 90), pace turned
   into speed (1 ÷ pace) — on flat ground running power ≈ proportional to speed, so % threshold
   speed is the % CP of the scale (the same rule the pace steps use). Friel lists the two
   tables side by side but does not state that the same zone number is the same effort: that
   pairing is our reading, so the chart and the TSS tip call it 推估. Below 85 % LTHR (Friel's
   open Zone 1) the chord from there to the threshold goes on (the linear relation of 1.), above
   106 % the 5b segment's slope; never below Palladino's lowest edge (1A 0.50 × CP) nor above his
   Zone 5 top (1.16 × CP) — zones.PALLADINO_POWER_ZONES.

Reading: hr_to_p(f) with the entry in effect (calibrate.value: stored fit / manual / default;
calibrate reads the store at most every 10 s), so a chart never refits; the fit runs with the
other items after a sync, at most weekly (calibrate.CALIB_EVERY_DAYS).
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Optional

from backend.engine import calibrate as CAL
from backend.engine import zones as Z
from backend.i18n import N_, _

NAME = "hr_power_slope"
DAYS = 90                         # = racepower/hrcap HRCAP["window_days"] (the CP window)
MAX_T_S = 3600.0                  # = hrcap t_max_s: HR drift past the first hour
DRIFT_MAX = 0.05                  # = hrcap drift_max (Uphill Athlete: Pw:HR decoupling < 5 %)
MIN_RUNS = 8                      # = hrcap min_runs
MIN_GAP = 0.02                    # a point this close to the threshold says nothing about the slope
X_RANGE = (0.60, 1.0)             # HR ÷ LTHR of a usable steady point (below threshold)
Y_RANGE = (0.40, 1.10)            # power ÷ CP of a usable steady point
K = 10.0                          # shrinkage, as intensity_calib (推估)
REST_RATIO = (0.15, 0.55)         # rest HR ÷ LTHR (30/200 … 90/165 bpm; 推估) → the slope's bounds
BOUNDS = (round(1.0 / (1.0 - REST_RATIO[0]), 2), round(1.0 / (1.0 - REST_RATIO[1]), 2))

FRIEL_SRC = ("Joe Friel, Quick Guide to Setting Zones (TrainingPeaks): run HR zones % LTHR ↔ run pace "
             "zones % threshold pace, paired by zone")


def _friel_points() -> list[tuple[float, float]]:
    """[(HR ÷ LTHR, speed ÷ threshold speed)] at Friel's zone edges: each HR zone's lower edge with
    the same zone's fast pace edge (zones.FRIEL_HR / FRIEL_PACE, listed easy → hard)."""
    hr = {zid: lo for zid, _n, lo, _hi in Z.FRIEL_HR}
    pace = {zid: hi for zid, _n, _lo, hi in Z.FRIEL_PACE}      # the zone's slower edge = the lower intensity
    pts = [(float(hr[z]), 1.0 / float(pace[z])) for z in hr if hr[z] and pace.get(z)]
    return sorted(pts)


FRIEL_POINTS = _friel_points()
FLOOR = Z.PALLADINO_POWER_ZONES[0][2]                 # 1A's lower edge, 0.50 × CP
CEIL = Z.Z5_TOP                                       # Zone 5's top, 1.16 × CP
# the literature's chord from Zone 2's lower edge to the threshold: the default of the personal slope
DEFAULT_SLOPE = round((1.0 - FRIEL_POINTS[0][1]) / (1.0 - FRIEL_POINTS[0][0]), 4)


def literature(f: float) -> float:
    """HR ÷ LTHR → the scale by the Friel pairing (piecewise linear, the end segments extended)."""
    pts = FRIEL_POINTS
    f = float(f)
    if f <= pts[0][0]:
        # Friel's Zone 1 is open (HR < 85 %, pace > 129 %): the chord to the threshold goes on — the
        # linear HR–work-rate relation, not Zone 2's steeper local slope
        return max(FLOOR, pts[0][1] - (pts[0][0] - f) * DEFAULT_SLOPE)
    if f >= pts[-1][0]:
        (a0, b0), (a1, b1) = pts[-2], pts[-1]
    else:
        i = next(k for k in range(len(pts) - 1) if pts[k][0] <= f <= pts[k + 1][0])
        (a0, b0), (a1, b1) = pts[i], pts[i + 1]
    v = b0 + (f - a0) / (a1 - a0) * (b1 - b0)
    return min(CEIL, max(FLOOR, v))


def personal(f: float, slope: float) -> float:
    """The runner's line through (1, 1) below threshold, the literature above."""
    f = float(f)
    if f > 1.0:
        return literature(f)
    return min(CEIL, max(FLOOR, 1.0 + slope * (f - 1.0)))


def hr_to_p(f: float, entry: Optional[dict] = None) -> float:
    """HR ÷ LTHR → the common intensity scale (≈ × CP) with `entry` (calibrate.entry; None = the
    one in effect): fitted / manual → the runner's slope, default → the literature table."""
    e = entry if entry is not None else current()
    if e.get("source") in ("fitted", "user") and e.get("value"):
        return personal(f, float(e["value"]))
    return literature(f)


def current() -> dict:
    try:
        return CAL.entry(NAME)
    except Exception:                       # noqa: BLE001 — no DB: the literature
        return {"value": DEFAULT_SLOPE, "source": "default", "n": 0}


def describe(entry: Optional[dict] = None) -> dict:
    """What the chart says about its heart-rate heights: {"source": "fitted" | "user" | "default",
    "estimate" (the 推估 mark), "n", "slope", "text", "tip", "points": [[HR ÷ LTHR, scale]] at
    0.60 … 1.10 (the template menu's mini charts interpolate these; the fit itself stays here)}."""
    e = entry if entry is not None else current()
    src = e.get("source") if e.get("source") in ("fitted", "user") else "default"
    n = int(e.get("n") or 0)
    if src == "fitted":
        text = _("心率高度：用你的資料校正（{n} 次穩定跑）", n=n)
    elif src == "user":
        text = _("心率高度：用你手動指定的斜率")
    else:
        text = _("心率高度：推估（Friel 心率與配速區間對照）")
    tip = _("心率段的高度：心率 ÷ LTHR 換成共同強度（門檻＝100）。"
            "你有功率又有心率的穩定平路段（每次跑步取一點、前 10 分鐘與 1 小時後不用、心率飄移 > 5% 的跑步不用）"
            "滿 {min_n} 次，就用你自己的關係（通過門檻的直線）；不夠時用 Joe Friel 的跑步心率區間和配速區間（同一份 30 分鐘測試）"
            "依區號對照，這個對照是推估。只影響這張圖和預估訓練負荷，不會改推到手錶的目標。", min_n=MIN_RUNS)
    xs = [round(0.60 + 0.05 * i, 2) for i in range(11)]
    return {"source": src, "estimate": src == "default", "n": n, "slope": e.get("value"), "text": text,
            "tip": tip, "points": [[x, round(hr_to_p(x, e), 4)] for x in xs]}


# ---- the fit -----------------------------------------------------------------------------------

def fit_points(points: list[dict]) -> Optional[CAL.Fit]:
    """points [{x: HR ÷ LTHR, y: P ÷ CP}] (one per run) → the slope through (1, 1): the median of
    the per-run slopes, its SE (1.2533 × SD ÷ √n), n = usable runs. None without any."""
    slopes = []
    for p in points:
        x, y = p.get("x"), p.get("y")
        if x is None or y is None or not (math.isfinite(x) and math.isfinite(y)):
            continue
        if not (X_RANGE[0] <= x <= X_RANGE[1] - MIN_GAP and Y_RANGE[0] <= y <= Y_RANGE[1]):
            continue
        slopes.append((y - 1.0) / (x - 1.0))
    if not slopes:
        return None
    b = statistics.median(slopes)
    se = 1.2533 * statistics.pstdev(slopes) / math.sqrt(len(slopes)) if len(slopes) > 1 else None
    return CAL.Fit(min(max(b, BOUNDS[0]), BOUNDS[1]), se, len(slopes))


def run_points(ds, today: dt.date) -> list[dict]:
    """[{x, y, date, idx}] of the outdoor road runs with usable power of the DAYS before `today`."""
    from backend.engine import zone_events as ZE
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    th = A.thresholds_as_of(ds, today)
    lthr, cp = th.get("lthr"), A.cp_as_of(ds, today) or th.get("cp")
    if not lthr or not cp:
        return []
    tday = date_to_day(today)
    out = []
    for w in ds.workouts:
        if not (A.outdoor(w) and tday - DAYS < w.day <= tday + 1) or A.is_trail(w) or not A.power_ok(ds, w):
            continue
        st = A.intensity_stats(ds, w) or {}
        if st.get("drift") is not None and st["drift"] > DRIFT_MAX:
            continue
        ws = [x for x in A.grade_samples(ds, [w]) if (x.get("t") or 0) <= MAX_T_S]
        pt = ZE.run_point(ws)
        if pt:
            out.append({"x": float(pt["hr"]) / float(lthr), "y": float(pt["p"]) / float(cp),
                        "date": w.entry.start.date().isoformat(), "idx": w.idx})
    flush = getattr(ds, "flush_series", None)
    if flush:
        flush()
    return out


def fit(ds, today: Optional[dt.date]):
    return fit_points(run_points(ds, today or dt.date.today()))


CAL.register(CAL.Item(
    name=NAME, label=N_("心率換強度的斜率（門檻以下）"), unit="", default=DEFAULT_SLOPE,
    default_src=N_("推估（Friel 跑步心率區間與配速區間依區號對照；Zone 2 下緣到門檻的斜率）"), k=K, min_n=MIN_RUNS,
    fit=fit, bounds=BOUNDS, digits=2,
    help=N_("課表結構圖和預估訓練負荷（Training load）把心率段換成共同強度（門檻＝100）：門檻以下照"
            "「強度 = 1 + 斜率 ×（心率 ÷ LTHR − 1）」。本人值 = 你近 90 天有功率又有心率的穩定平路段"
            "（坡度 3% 以內、前 10 分鐘與 1 小時後不用、功率變異 < 10% 至少 10 分鐘、心率飄移 > 5% 的跑步不用），"
            "每次跑步一點，取各點斜率的中位數；1 筆 = 一次這樣的跑步。不夠 8 次時用 Friel 區間對照（推估）。"
            "不會改推到手錶的目標。")))
