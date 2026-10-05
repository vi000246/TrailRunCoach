"""
Same-load conversion between terrains (路跑 ⇄ 越野 ⇄ 登山) — how far and how much
climb makes a session of the same time, and so the same TSS, on other terrain.

PRINCIPLE
    The easy sessions this is used for are held at the same heart rate
    (<= AeT). Same intensity => TSS grows with time at the same rate
    (hrTSS = hours x IF^2 x 100), so same time => same TSS. The conversion
    therefore only has to predict *time* from distance and climb on the
    target terrain, at easy effort, for this athlete.

MODEL — the athlete's own history, not a fixed formula
    Naismith's additive form (W. W. Naismith, 1892: 1 h per 3 mi on the flat
    + 1 h per 2000 ft of ascent), with both rates fitted to this athlete and
    Langmuir's grade-dependent descent correction (E. Langmuir, *Mountaincraft
    and Leadership*, 1984; 10 min per 300 m of descent — subtracted on gentle
    descents of 5-12 deg, added on steeper ones):

        hours = km / v_flat + gain / VAM + descent(loss, grade)

    * v_flat (km/h): flat easy running speed = median speed of the last
      WINDOW_WEEKS of road runs with avg HR <= AeT + EASY_HR_TOL (the same
      tolerance the drift streak uses for "easy", workout_review) on flat
      ground (<= FLAT_M_PER_KM m/km). Fewer than MIN_ROAD such runs: the
      speed at AeT from a speed ~ HR least-squares line over all flat road
      runs. The flat parts of a trail run are run at it.
    * trail VAM (m/h): easy trail runs of the same window; the residual
      t - km / v_flat - descent is regressed on gain through the origin.
    * hike: flat walking speed and VAM fitted together (two-parameter least
      squares on t - descent) — walking has no flat-run reference.
    * descent: the average grade of an out-and-back / loop is taken as
      (gain + loss) / horizontal distance.

    A terrain mode with fewer than MIN_SAMPLES easy samples falls back to
    effort distance EP = km + gain/100 (the ITRA "km-effort" / 健行筆記
    convention, algorithms/effort.py SIMPLE_FORMULAS["itra"]; measured there
    against integrated Minetti over 430 of one runner's activities: 6.9 %
    mean error, +6.9 % bias), at the athlete's median EP speed on that
    terrain (or on trail + hike, or v_flat).

    With enough samples both forms are fitted to the athlete and the one
    with the lower leave-one-out error on that terrain is used (`fit(method=
    "auto")`). On one runner's 26 weeks (9 easy trail runs)
    EP at the athlete's own EP speed won — 5.6 % vs 11.4 % MAPE — so the
    Naismith / Langmuir terms are kept for the comparison and for athletes
    whose data favours them.

    Converting *to* road uses EP at v_flat (a flat road is km / v_flat).

    GROUP HIKES (user decision 2026-09-30): hiking / mountaineering days are
    mostly group trips paced by the group, so only hikes the user opted in as
    solo (racepower.athlete.solo_hikes) are hike samples. With fewer than
    MIN_SAMPLES of them the 登山 mode is EP at the pooled trail EP speed and
    the backtest has nothing to test → 推估.

STATUS / VALIDATION
    `backtest()` runs leave-one-out on the athlete's own easy trail and hike
    activities: refit without the activity, predict its moving time, compare.
    It reports n, MAE (min), MAPE (%), bias for the chosen method and for EP.
    The results are in docs/spec/overview.spec.md. When the chosen method's
    MAPE is above ESTIMATE_MAPE (or it could not be backtested) the page
    labels the conversion 「推估」.

TODO(racepower-v2): the racepower-v2 branch is building a grade-cost model
    (Minetti-based) in backend/engine/racepower/. Once it is validated, pass
    it as `fit(..., grade_cost=f)` — f(grade) -> cost relative to flat — and
    the trail / hike time becomes km x mean(f(+g), f(-g)) / v_flat instead of
    the Naismith / Langmuir terms. Nothing here imports it yet.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from backend.engine.algorithms.effort import SIMPLE_FORMULAS
from backend.i18n import _

WINDOW_WEEKS = 26
EASY_HR_TOL = 3.0              # avg HR <= AeT + 3 (workout_review drift-streak "easy")
MIN_SAMPLES = 5                # per terrain, else EP
MIN_ROAD = 3                   # easy flat road runs for the median speed
FLAT_M_PER_KM = 15.0
MIN_MINUTES = 20.0
EP_M_PER_KM = SIMPLE_FORMULAS["itra"][0]          # 100 m of climb = 1 effort km
LANGMUIR_H_PER_M = 10.0 / 60.0 / 300.0           # 10 min per 300 m
GENTLE_DEG, STEEP_DEG = 5.0, 12.0
ESTIMATE_MAPE = 15.0           # above: shown as 推估
DEFAULT_V_FLAT = 8.0           # km/h, only when there is no road run and no threshold pace
TPACE_EASY_FRAC = 0.75         # 推估: easy speed ≈ 75 % of the threshold-pace speed
MODES = ("road", "trail", "hike")
HIKE_NOTE = "百岳多為跟團，速度不代表個人能力：登山換算只用你標記為自己走的登山，不足時用 EP（推估）"
SOURCES = [
    "Naismith 1892（平地時間＋爬升時間，相加）",
    "Langmuir《Mountaincraft and Leadership》1984（下坡 5–12° 每 300 m 減 10 分、> 12° 加 10 分）",
    "ITRA／健行筆記 努力距離 EP = km + 爬升/100（樣本不足時）",
]


@dataclass
class Sample:
    date: str
    mode: str                  # road / trail / hike
    km: float
    gain: float
    loss: float
    minutes: float             # moving time
    hr: Optional[float] = None


@dataclass
class ModeModel:
    mode: str
    method: str                # history / ep
    n: int
    v_flat_kmh: Optional[float] = None
    vam_mh: Optional[float] = None
    ep_kmh: Optional[float] = None
    density_range: Optional[list] = None      # [min, max] m/km of the samples: outside it is extrapolation


@dataclass
class Model:
    v_flat_kmh: float
    v_flat_source: str
    road_n: int
    modes: dict = field(default_factory=dict)          # mode -> ModeModel
    grade_cost: Optional[Callable[[float], float]] = None

    def to_dict(self) -> dict:
        return {"v_flat_kmh": self.v_flat_kmh, "v_flat_source": self.v_flat_source, "road_n": self.road_n,
                "modes": {k: asdict(v) for k, v in self.modes.items()},
                "langmuir_h_per_m": LANGMUIR_H_PER_M, "gentle_deg": GENTLE_DEG, "steep_deg": STEEP_DEG,
                "ep_m_per_km": EP_M_PER_KM}


# ---------------------------------------------------------------------------
# the time model
# ---------------------------------------------------------------------------

def descent_hours(km: float, gain: float, loss: float) -> float:
    """Langmuir's correction: − on gentle (5–12°), + on steep (> 12°) descents."""
    if km <= 0 or loss <= 0:
        return 0.0
    deg = math.degrees(math.atan((gain + loss) / (km * 1000.0)))
    if deg < GENTLE_DEG:
        return 0.0
    return (-1.0 if deg <= STEEP_DEG else 1.0) * loss * LANGMUIR_H_PER_M


def predict_hours(model: Model, mode: str, km: float, gain: float, loss: Optional[float] = None) -> float:
    loss = gain if loss is None else loss
    mm = model.modes.get(mode)
    if mode == "road" or mm is None:
        return (km + gain / EP_M_PER_KM) / model.v_flat_kmh
    if model.grade_cost is not None and km > 0:
        g = (gain + loss) / 2.0 / (km * 500.0)       # mean grade of each half
        return km * (model.grade_cost(g) + model.grade_cost(-g)) / 2.0 / model.v_flat_kmh
    if mm.method == "ep":
        return (km + gain / EP_M_PER_KM) / mm.ep_kmh
    return km / mm.v_flat_kmh + gain / mm.vam_mh + descent_hours(km, gain, loss)


def hours_per_km(model: Model, mode: str, climb_per_km: float) -> float:
    """Loop / out-and-back (loss = gain): time is linear in km at a fixed climb density."""
    return predict_hours(model, mode, 1.0, climb_per_km)


def design(model: Model, mode: str, minutes: float, climb_per_km: float) -> dict:
    """Distance and climb that take `minutes` on `mode` at `climb_per_km`."""
    hpk = hours_per_km(model, mode, climb_per_km)
    km = minutes / 60.0 / hpk if hpk > 0 else 0.0
    return {"mode": mode, "minutes": minutes, "km": round(km, 2), "gain_m": round(km * climb_per_km),
            "climb_per_km": climb_per_km}


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------

def _speed(s: Sample) -> float:
    return s.km / (s.minutes / 60.0)


def _flat_speed(road_all: list[Sample], aet: Optional[float],
                tpace_min_per_km: Optional[float] = None) -> tuple[float, str, int]:
    flat = [s for s in road_all if s.km > 0 and s.gain / s.km <= FLAT_M_PER_KM and s.minutes >= MIN_MINUTES]
    easy = [s for s in flat if s.hr is not None and aet is not None and s.hr <= aet + EASY_HR_TOL]
    if len(easy) >= MIN_ROAD:
        return statistics.median(_speed(s) for s in easy), _("近 {weeks} 週 {n} 次輕鬆路跑的中位數", weeks=WINDOW_WEEKS, n=len(easy)), len(easy)
    hr_ok = [s for s in flat if s.hr]
    if aet is not None and len(hr_ok) >= 8:
        xs, ys = [s.hr for s in hr_ok], [_speed(s) for s in hr_ok]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx > 0:
            b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            if b > 0:
                return my + b * (aet - mx), _("{n} 次路跑的速度－心率迴歸，取 AeT 的速度", n=len(hr_ok)), len(hr_ok)
    if flat:
        return statistics.median(_speed(s) for s in flat), _("{n} 次路跑的中位數（沒有心率可篩）", n=len(flat)), len(flat)
    if tpace_min_per_km and tpace_min_per_km > 0:
        # generalize-athlete G1: no road run at all — easy ≈ 75 % of the threshold speed (推估)
        v = 60.0 / tpace_min_per_km * TPACE_EASY_FRAC
        return v, _("沒有路跑資料：閾值配速的 {frac:.0%} 速度（推估）", frac=TPACE_EASY_FRAC), 0
    return DEFAULT_V_FLAT, _("沒有路跑資料，暫用 8 km/h（推估）"), 0


def _ep_speed(ss: list[Sample]) -> Optional[float]:
    v = [(s.km + s.gain / EP_M_PER_KM) / (s.minutes / 60.0) for s in ss if s.minutes > 0]
    return statistics.median(v) if v else None


def _fit_trail(ss: list[Sample], v: float) -> Optional[float]:
    """VAM (m/h) from residual time after the flat and descent terms."""
    sxy = sxx = 0.0
    for s in ss:
        r = s.minutes / 60.0 - s.km / v - descent_hours(s.km, s.gain, s.loss)
        sxy += s.gain * r
        sxx += s.gain * s.gain
    if sxx <= 0 or sxy <= 0:
        return None
    vam = sxx / sxy
    return vam if 50.0 <= vam <= 3000.0 else None


def _fit_hike(ss: list[Sample]) -> Optional[tuple[float, float]]:
    """(v_flat km/h, VAM m/h) by two-parameter least squares on t − descent."""
    a11 = sum(s.km * s.km for s in ss)
    a12 = sum(s.km * s.gain for s in ss)
    a22 = sum(s.gain * s.gain for s in ss)
    b1 = b2 = 0.0
    for s in ss:
        y = s.minutes / 60.0 - descent_hours(s.km, s.gain, s.loss)
        b1 += s.km * y
        b2 += s.gain * y
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-9:
        return None
    ia = (b1 * a22 - b2 * a12) / det
    ib = (a11 * b2 - a12 * b1) / det
    if ia <= 0 or ib <= 0:
        return None
    v, vam = 1.0 / ia, 1.0 / ib
    return (v, vam) if 1.0 <= v <= 15.0 and 50.0 <= vam <= 3000.0 else None


def fit(samples: list[Sample], aet: Optional[float], grade_cost: Optional[Callable[[float], float]] = None,
        method: str = "auto", tpace_min_per_km: Optional[float] = None) -> Model:
    """`method`: history (Naismith / Langmuir terms when there are enough
    samples), ep, or auto — with enough samples, the one with the lower
    leave-one-out error on this terrain's samples (both are fitted to the
    athlete; EP has one parameter, the athlete's own EP speed)."""
    road_all = [s for s in samples if s.mode == "road"]
    v, vsrc, rn = _flat_speed(road_all, aet, tpace_min_per_km)
    model = Model(v_flat_kmh=v, v_flat_source=vsrc, road_n=rn, grade_cost=grade_cost)
    easy = [s for s in samples if s.mode in ("trail", "hike")]
    pooled_ep = _ep_speed(easy)
    for mode in ("trail", "hike"):
        ss = [s for s in easy if s.mode == mode]
        ep = _ep_speed(ss) or pooled_ep or v
        mm = ModeModel(mode=mode, method="ep", n=len(ss), ep_kmh=ep)
        hist = None
        if method != "ep" and len(ss) >= MIN_SAMPLES:
            if mode == "trail":
                vam = _fit_trail(ss, v)
                if vam:
                    hist = ModeModel(mode=mode, method="history", n=len(ss), v_flat_kmh=v, vam_mh=vam, ep_kmh=ep)
            else:
                r = _fit_hike(ss)
                if r:
                    hist = ModeModel(mode=mode, method="history", n=len(ss), v_flat_kmh=r[0], vam_mh=r[1], ep_kmh=ep)
        if hist is not None and method == "history":
            mm = hist
        elif hist is not None:
            # only folds that could fit history count (at n = MIN_SAMPLES every fold falls back to EP)
            e_h = _stats([r for r in _loo_rows(samples, mode, aet, "history") if r["method"] == "history"])["mape_pct"]
            e_e = _stats(_loo_rows(samples, mode, aet, "ep"))["mape_pct"]
            if e_h is not None and (e_e is None or e_h <= e_e):
                mm = hist
        if ss:
            dens = [s.gain / s.km for s in ss if s.km > 0]
            mm.density_range = [round(min(dens)), round(max(dens))]
        model.modes[mode] = mm
    return model


# ---------------------------------------------------------------------------
# validation: leave-one-out on the athlete's own trail and hike activities
# ---------------------------------------------------------------------------

def _stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "mae_min": None, "mape_pct": None, "bias_pct": None}
    err = [r["pred_min"] - r["actual_min"] for r in rows]
    pct = [e / r["actual_min"] * 100.0 for e, r in zip(err, rows)]
    return {"n": len(rows), "mae_min": round(statistics.mean(abs(e) for e in err), 1),
            "mape_pct": round(statistics.mean(abs(p) for p in pct), 1),
            "bias_pct": round(statistics.mean(pct), 1)}


def _loo_rows(samples: list[Sample], mode: str, aet: Optional[float], method: str) -> list[dict]:
    rows = []
    for i, s in enumerate(samples):
        if s.mode != mode:
            continue
        m = fit(samples[:i] + samples[i + 1:], aet, method=method)
        rows.append({"date": s.date, "km": round(s.km, 2), "gain": round(s.gain), "actual_min": round(s.minutes, 1),
                     "pred_min": round(predict_hours(m, mode, s.km, s.gain, s.loss) * 60.0, 1),
                     "method": m.modes[mode].method})
    return rows


def backtest(samples: list[Sample], aet: Optional[float]) -> dict:
    """Per terrain, leave-one-out: the method fit() chooses (the choice is
    re-made inside every fold, so the error is not tuned on the held-out
    activity), and history / EP alone for comparison."""
    out = {}
    for mode in ("trail", "hike"):
        chosen = _loo_rows(samples, mode, aet, "auto")
        st = _stats(chosen)
        out[mode] = {"method": fit(samples, aet).modes[mode].method, **st,
                     "history": _stats([r for r in _loo_rows(samples, mode, aet, "history") if r["method"] == "history"]),
                     "ep": _stats(_loo_rows(samples, mode, aet, "ep")), "rows": chosen,
                     "estimate": st["mape_pct"] is None or st["mape_pct"] > ESTIMATE_MAPE}
    return out


# ---------------------------------------------------------------------------
# samples from the dataset
# ---------------------------------------------------------------------------

def samples_from(ds, today: dt.date, aet: Optional[float], weeks: int = WINDOW_WEEKS,
                 solo: Optional[set] = None) -> list[Sample]:
    """Road runs (any HR, for the flat speed) and easy trail / hike sessions
    (avg HR <= AeT + EASY_HR_TOL) of the last `weeks` weeks. Hikes only when
    the user opted them in as solo (racepower.athlete.solo_hikes): group
    hikes (百岳多為跟團) are paced by the group, not by the athlete, so with
    too few solo hikes the 登山 conversion falls back to EP, labelled 推估."""
    from backend.engine import overview as O
    from backend.engine.wko5expr.dataset import date_to_day
    from backend.engine.wko5expr.evaluator import WS, Evaluator
    if solo is None:
        from backend.engine.racepower.athlete import solo_hikes
        solo = solo_hikes()
    lo = today - dt.timedelta(weeks=weeks)
    ws = [w for w in O.workouts_between(ds, lo, today + dt.timedelta(days=1)) if O.category(w) in MODES
          and (O.category(w) != "hike" or w.entry.file in solo)]
    if not ws:
        return []
    b, e = int(date_to_day(lo)), int(date_to_day(today))
    hr = Evaluator(ds, b, e).evaluate(f"athleterange({b}, {e}, avg(heartrate))")
    hr = hr if isinstance(hr, WS) else {}
    out = []
    for w in ws:
        m, cat = w.metrics, O.category(w)
        km = O._n(m.get("distance")) or 0.0
        mins = O.moving_s(w) / 60.0
        h = hr.get(w.idx) if hasattr(hr, "get") else None
        h = float(h) if h is not None and h == h and h > 60 else None
        if km < 1.0 or mins < MIN_MINUTES:
            continue
        if cat != "road" and (h is None or aet is None or h > aet + EASY_HR_TOL):
            continue
        out.append(Sample(date=O.wdate(w).isoformat(), mode=cat, km=km, gain=O._n(m.get("climbing")) or 0.0,
                          loss=O._n(m.get("descending")) or 0.0, minutes=mins, hr=h))
    return out


def summary(ds, today: dt.date, aet: Optional[float]) -> dict:
    """What GET /plan/equivalence returns: model parameters, backtest, sources."""
    ss = samples_from(ds, today, aet)
    try:
        from backend.engine.wko5expr.dataset import date_to_day
        tp = ds.setting("runtpace", date_to_day(today))
    except Exception:                       # noqa: BLE001
        tp = None
    model = fit(ss, aet, tpace_min_per_km=tp)
    bt = backtest(ss, aet)
    return {"model": model.to_dict(), "backtest": bt, "aet": aet, "window_weeks": WINDOW_WEEKS,
            "easy_hr_max": None if aet is None else aet + EASY_HR_TOL, "min_samples": MIN_SAMPLES,
            "estimate_mape": ESTIMATE_MAPE,
            "estimate": {m: bt[m]["estimate"] if m in bt else True for m in ("trail", "hike")},
            "sources": SOURCES, "hike_note": HIKE_NOTE}
