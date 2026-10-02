"""
「長時間輕鬆跑的心率飄移」 as one bar per run, coloured by verdict (views/periodization.json,
chart key "drift_bars": true).

Owner 2026-10-02: the old chart (single runs, 參考 markers, a 6-run mean per temperature band,
± SE markers, 5 % and 10 % lines) was too many scattered points and too much statistics for a
non-technical reader. The data and its rules are unchanged — the bars are the same drift()
the card computes (workout_review.drift_of: warm-up excluded, the fairness refusals, both
tiers) — split by the app's own thresholds (workout_review.DRIFT_GOOD 5 % / DRIFT_WATCH 10 %,
the overview's 心率飄移 card; UA AeT test < 5 %, 徐國峰 90-min test < 10 %) into three bar
series in the view. This panel only adds what the expression language can't: each bar's
hover line (date, duration, temperature band, 參考 tier) and the latest bar's label.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

from backend.i18n import N_, _


def _measure(ds, w) -> Optional[dict]:
    from backend.engine import workout_review as WR
    return WR.measure(ds, w)


def dur_text(sec) -> str:
    try:
        m = int(round(float(sec) / 60.0))
    except (TypeError, ValueError):
        return "—"
    return _("{h} 小時 {m} 分", h=m // 60, m=m % 60) if m >= 60 else _("{m} 分", m=m)


def temp_text(dr: dict) -> str:
    from backend.engine import workout_review as WR
    band = dr.get("temp_band") or WR.temp_band(dr.get("temp_c"))
    label = _(WR.TEMP_BAND_LABEL.get(band) or N_("溫度不明"))
    t = dr.get("temp_c")
    if t is None or not math.isfinite(float(t)):
        return "🌡 " + label
    return _("🌡 {t:.0f} °C（{band}）", t=float(t), band=label)


def tip_of(ds, w, measure: Callable = _measure) -> str:
    from backend.engine import workout_review as WR
    from backend.engine.wko5expr.dataset import day_to_date
    dr = (measure(ds, w) or {}).get("drift") or {}
    parts = [day_to_date(w.day).isoformat(), _("跑 {d}", d=dur_text(w.metrics.get("duration"))), temp_text(dr)]
    out = "・".join(parts)
    if WR.drift_tier(dr) == "ref":
        out += "\n" + _("暖身後不到 40 分鐘，只當參考")
    if WR.is_heat(dr.get("temp_band") or WR.temp_band(dr.get("temp_c"))):
        out += "\n" + _("天熱，飄移本來就會偏高")
    return out


def apply(res: dict, ds, measure: Callable = _measure) -> dict:
    """Every bar series gets `tips` (one per point) and the latest bar of all of them a
    「最新 X%」 label."""
    from backend.engine.wko5expr.render import _dt_iso
    by_iso = {_dt_iso(w.day): w for w in ds.workouts}
    series, latest = [], None
    for s in res.get("series") or []:
        pts = (s.get("data") or {}).get("points") if (s.get("data") or {}).get("kind") == "points" else None
        if s.get("type") != "bar" or not pts:
            series.append(s)
            continue
        tips = []
        for x, y in pts:
            w = by_iso.get(x)
            tips.append(tip_of(ds, w, measure) if w is not None and y is not None else "")
        s = {**s, "tips": tips}
        series.append(s)
        for i, (x, y) in enumerate(pts):
            if y is not None and (latest is None or x > latest[0]):
                latest = (x, len(series) - 1, i, y)
    if latest is not None:
        _x, si, i, y = latest
        s = series[si]
        labels = [""] * len(s["data"]["points"])
        labels[i] = _("最新 {v:.1f}%", v=float(y) * 100)
        series[si] = {**s, "labels": labels}
    return {**res, "series": series}
