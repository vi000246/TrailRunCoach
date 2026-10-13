"""
mFTP 與 VO2max 趨勢 (views kind "pdtrend", 我的訓練 → 能力, right after 跑步功率曲線; SP-466).

mFTP = the 課表's own CP before a plan CP test: WKO5's PD model fitted to the 90-day mean-max
envelope of the Stryd runs (FitFolderDataset.stryd_cp_fit, SP-449; docs/research/wko5-mftp-logic.md).
The chart reads the fits the Dataset already made for the 課表 (`_cp_est`: every 30 days from the
first run + 30, and on the last run's day) — it never refits for a drawing (a PD fit is slow on the
NAS). Each fit is one point; the latest is the CP the 課表 and the settings page use while no plan
CP test applies.

VO2max = WKO5's, from the same fit (formulas.md §6.7, checked bit-for-bit against WKO5's own
`vo2maxkg`, wko5-mftp-logic.md §8):

    VO2max (L/min)     = (FRC[J] / 589 + mFTP[W]) / 84.5 + 0.656
    VO2max (ml/kg/min) = that × 1000 / the body weight in effect on the fit's day

An estimate (WKO5's regression), not a measured VO2max. No body weight → 「算不出」 (no default
weight, SP-414); the mFTP line is drawn anyway.

A Dataset without `_cp_est` (the WKO5 folder source) fits the same model on the same 30-day grid,
only for the grid days the range needs, memoised on the Dataset (racepower.athlete.stryd_pd).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.engine.algorithms import wko5_pdmodel as PDM
from backend.i18n import _

GRID_DAYS = 30                       # = fitdataset.ESTIMATE_STEP_DAYS (a test pins it)


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, dt.date) else str(d)[:10]


def _as_date(d) -> dt.date:
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])


def _grid_fits(ds, lo: dt.date, hi: dt.date) -> list[dict]:
    """The WKO5-folder fallback: stryd_pd on the 30-day grid (first run + 30, …, the last run),
    only the days in effect over [lo, hi]."""
    from backend.engine.racepower import athlete as A
    from backend.files.wko5_athlete import day_to_date
    runs = [w for w in ds.workouts if w.sport == "run"]
    if not runs:
        return []
    first = day_to_date(math.floor(runs[0].day)) + dt.timedelta(days=GRID_DAYS)
    end = min(day_to_date(math.floor(ds.today)), day_to_date(math.floor(runs[-1].day)))
    days, d = [], first
    while d <= end:
        days.append(d)
        d += dt.timedelta(days=GRID_DAYS)
    if first <= end and days[-1] != end:
        days.append(end)
    keep = [x for x in days if lo <= x <= hi]
    before = [x for x in days if x < lo]
    if before:
        keep.insert(0, before[-1])
    memo = ds.__dict__.setdefault("_pdtrend_fits", {})
    out = []
    for x in keep:
        if x not in memo:
            r = A.stryd_pd(ds, x)
            memo[x] = None if not r else {"date": x, "cp": float(r["mftp"]), "frc_j": float(r["frc"]),
                                          "runs": r.get("runs")}
        if memo[x]:
            out.append(memo[x])
    return out


def fits(ds, lo: dt.date, hi: dt.date) -> list[dict]:
    """The 課表's PD fits [{date, cp (mFTP), frc_j, runs}], oldest first."""
    est = getattr(ds, "_cp_est", None)
    if hasattr(ds, "stryd_cp_fit") and isinstance(est, list):
        return sorted((f for f in est if f and f.get("cp")), key=lambda f: _as_date(f["date"]))
    return _grid_fits(ds, lo, hi)


def _season_plan():
    from backend.engine.wko5expr.dataset import tenant_plan
    return tenant_plan()


def _weight(ds, day: dt.date) -> Optional[float]:
    """The Dataset's weight on `day` (the season plan, then the DB / WKO5 setting), else the season
    plan's: WKO5 對照模式 gives the Dataset an empty plan, and a COROS install has no WKO5 setting."""
    from backend.engine.wko5expr.dataset import date_to_day
    v = None
    try:
        v = ds.setting("weight", date_to_day(day))
    except Exception:                       # noqa: BLE001 — no settings: the season plan's
        v = None
    if not v:
        try:
            v = _season_plan().weight_on(day)
        except Exception:                   # noqa: BLE001 — no plan: no weight
            v = None
    return float(v) if v else None


def points(ds, b: float, e: float) -> list[dict]:
    """One point per fit in [b, e], plus the fit in effect at b (the line starts at the left edge)."""
    from backend.files.wko5_athlete import day_to_date
    lo, hi = day_to_date(math.floor(b)), day_to_date(math.floor(e))
    fs = fits(ds, lo, hi)
    keep = [f for f in fs if lo <= _as_date(f["date"]) <= hi]
    before = [f for f in fs if _as_date(f["date"]) < lo]
    if before:
        keep.insert(0, before[-1])
    out = []
    for f in keep:
        day = _as_date(f["date"])
        mftp, frc = float(f["cp"]), float(f["frc_j"])
        kg = _weight(ds, day)
        v_kg = PDM.vo2max_kg(mftp, frc, kg)
        out.append({"date": day.isoformat(), "mftp": round(mftp, 1), "frc_kj": round(frc / 1000.0, 1),
                    "vo2max_l": round(PDM.vo2max(mftp, frc), 2),
                    "vo2max_kg": None if v_kg is None else round(v_kg, 1),
                    "weight": kg, "runs": f.get("runs")})
    return out


def render(ds, ch: dict, b: float, e: float, params: Optional[dict] = None) -> dict:
    from backend.engine.wko5expr.fitdataset import CP_FIT_MIN_RUNS
    from backend.files.wko5_athlete import day_to_date
    pts = points(ds, b, e)
    lacking = [p for p in pts if p["vo2max_kg"] is None]
    no_weight = bool(pts) and len(lacking) == len(pts)
    note = None
    if no_weight:
        note = _("沒有體重：VO2max 是每公斤（ml/kg/min），算不出；請到 設定 → 個人資料 填體重。模型閾值功率照樣畫。")
    elif lacking:
        note = _("有 {n} 個點那天沒有體重，VO2max 只畫有體重的點。", n=len(lacking))
    empty = None if pts else _(
        "這段期間沒有 PD 模型擬合：90 天內至少要 {n} 次 Stryd 功率跑步（手錶推估功率不採用），"
        "模型閾值功率與 VO2max 才算得出來。", n=CP_FIT_MIN_RUNS)
    return {"title": ch.get("title"), "description": ch.get("description"), "kind": "pdtrend",
            "points": pts, "latest": pts[-1] if pts else None, "no_weight": no_weight, "note": note,
            "empty": empty,
            "x_min": day_to_date(math.floor(b)).isoformat(), "x_max": day_to_date(math.floor(e)).isoformat(),
            "method": _("每個點＝課表用的 Stryd 功率 PD 模型擬合（每 30 天一次，加上最後一次跑步那天）；"
                        "VO2max＝(W′/589 + 模型閾值功率)/84.5 + 0.656 L/min ÷ 當天體重（推估，算法參考 WKO5）")}
