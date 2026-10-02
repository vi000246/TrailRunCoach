"""
How much heat acclimation the race calculator may credit (2026-10-02,
docs/research/unsourced-rules.md §A8).

The S index (engine/heat.py) is shown as before, but its effect a — the
share of the Hadley heat penalty an acclimatised athlete wins back
(heat.A_RECOVER 0.75 from Racinais 2015, a population mean) — defaults to 0
here: the athlete's own heat back-test found β larger in late summer than in
early summer (0.260 vs 0.149 bpm per Hadley unit) and a best a_hr of 0, i.e.
no visible acclimation. The calculator credits a only when the athlete's own
HRC observations (heat.hr_cost: the HR cost of heat at a steady power, the
last 84 days) fall over time with a one-sided p < 0.1 (推估 test; the
literature has no threshold for an individual).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

A_DEFAULT = 0.0                # 推估: no credit until the athlete's own data show acclimation
MIN_ROWS = 8                   # 推估: hot steady segments needed for the slope test
# one-sided t critical values at p = 0.10 by degrees of freedom (standard t table)
T_CRIT = ((6, 1.440), (8, 1.397), (10, 1.372), (15, 1.341), (20, 1.325), (30, 1.310), (60, 1.296))
T_CRIT_INF = 1.282


def _t_crit(df: int) -> float:
    for d, t in T_CRIT:
        if df <= d:
            return t
    return T_CRIT_INF


def hrc_slope_test(rows) -> dict:
    """OLS of HRC (bpm per 10 Hadley units) on the day: slope per 30 days,
    its SE, t, and `supported` = slope < 0 with t below the one-sided 10 %
    critical value and ≥ MIN_ROWS rows. rows = heat.hr_cost()["rows"]."""
    pts = []
    for r in rows or []:
        try:
            d = dt.date.fromisoformat(str(r["date"])[:10]).toordinal()
            h = float(r["hrc"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(h):
            pts.append((d, h))
    out = {"n": len(pts), "slope_per_30d": None, "se": None, "t": None, "t_crit": None, "supported": False}
    if len(pts) < MIN_ROWS:
        out["why"] = f"熱天穩定段 {len(pts)} 段（需 ≥ {MIN_ROWS}）：無法檢定，熱適應不折抵熱懲罰"
        return out
    n = len(pts)
    mx = sum(p for p, _ in pts) / n
    my = sum(h for _, h in pts) / n
    sxx = sum((p - mx) ** 2 for p, _ in pts)
    if sxx <= 0:
        out["why"] = "熱天穩定段都在同一天：無法檢定"
        return out
    b = sum((p - mx) * (h - my) for p, h in pts) / sxx
    res = [h - (my + b * (p - mx)) for p, h in pts]
    s2 = sum(e * e for e in res) / (n - 2)
    se = math.sqrt(s2 / sxx) if s2 > 0 else 0.0
    t = b / se if se > 0 else (-math.inf if b < 0 else math.inf)
    tc = _t_crit(n - 2)
    ok = b < 0 and t < -tc
    out.update(slope_per_30d=b * 30.0, se=se * 30.0, t=t, t_crit=tc, supported=bool(ok),
               why=("熱天心率成本隨時間下降（單尾 p < 0.1）：照文獻折抵熱懲罰" if ok else
                    "熱天心率成本沒有顯著下降：你的資料不支持熱適應，不折抵熱懲罰"))
    return out


def acclimation_a(test: Optional[dict], a_lit: float) -> tuple[float, str]:
    """(a, reason): the literature a (heat.A_RECOVER) only when the HRC test
    supports acclimation; else A_DEFAULT (0)."""
    if test and test.get("supported"):
        return a_lit, test.get("why") or "HRC 檢定支持熱適應"
    why = (test or {}).get("why") or "沒有 HRC 觀測：熱適應不折抵熱懲罰"
    return A_DEFAULT, why
