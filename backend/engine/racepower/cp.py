"""
Critical power from efforts (SuperPower tasks 6 / 20) and the RWC (W′)
rating (task 19) — docs/research/superpower-calculator.md §1.5, §2.1.

    work = CP · t + W′        (OLS, like the workbook's LINEST)
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

CP_DURATIONS = (180, 300, 420, 600, 720, 900, 1200)     # s, sampled from the envelope


def fit_cp(points: Sequence[tuple[float, float]]) -> Optional[dict]:
    """points = [(t_s, power_W), ...]. Returns cp, w_prime (J), r2, n."""
    pts = [(float(t), float(p)) for t, p in points if t and p and t > 0 and p > 0]
    if len(pts) < 2:
        return None
    t = np.array([p[0] for p in pts])
    w = np.array([p[0] * p[1] for p in pts])
    if np.ptp(t) == 0:
        return None
    cp, wp = np.polyfit(t, w, 1)
    pred = cp * t + wp
    ss_tot = float(((w - w.mean()) ** 2).sum())
    r2 = 1.0 - float(((w - pred) ** 2).sum()) / ss_tot if ss_tot > 0 else 1.0
    return {"cp": float(cp), "w_prime": float(wp), "r2": r2, "n": len(pts)}


def validity(points: Sequence[dict], envelope: bool = False) -> list[dict]:
    """The workbook's checks. `points` = [{t, p, date?}]. Each check:
    {id, ok, level ('error' | 'warn'), text}. For an envelope the dates span
    several days by construction, so that check is only a warning."""
    ts = [p["t"] for p in points]
    ps = [p["p"] for p in points]
    out = []

    def add(cid, ok, level, text):
        out.append({"id": cid, "ok": bool(ok), "level": level, "text": text})

    add("short", any(t <= 360 for t in ts), "error", "至少一個 ≤ 6:00 的努力")
    add("long", any(t >= 900 for t in ts), "error", "至少一個 ≥ 15:00 的努力")
    add("span", bool(ts) and max(ts) - min(ts) >= 360, "error", "最長與最短相差 ≥ 6 分鐘")
    order = sorted(zip(ts, ps))
    add("falling", all(b[1] < a[1] for a, b in zip(order, order[1:])), "error",
        "功率隨時間增加而遞減")
    add("not_too_long", not ts or max(ts) <= 1800, "warn", "最長努力不超過 30 分鐘（再長會低估 CP）")
    dates = sorted(p["date"] for p in points if p.get("date"))
    if dates:
        import datetime as dt
        span = (dt.date.fromisoformat(dates[-1][:10]) - dt.date.fromisoformat(dates[0][:10])).days
        add("dates", span <= 14, "warn" if envelope else "error",
            f"所有努力在 14 天內（目前跨 {span} 天）" + ("；曲線包絡線必然跨多天" if envelope else ""))
    return out


# RWC rating bands: upper bound (inclusive) of Too Low, Low, Medium, High; above = Too High
RWC_BANDS = {
    ("male", False, "jkg"): (61, 79, 136, 154),
    ("female", False, "jkg"): (73, 84, 119, 130),
    ("male", True, "jkg"): (91, 109, 166, 184),
    ("female", True, "jkg"): (103, 114, 149, 160),
    ("male", False, "kj"): (4.28, 5.63, 9.82, 11.17),
    ("female", False, "kj"): (3.97, 4.65, 6.78, 7.46),
    ("male", True, "kj"): (6.28, 7.63, 11.82, 13.17),
    ("female", True, "kj"): (5.97, 6.65, 8.78, 9.46),
}
RATINGS = ("Too Low", "Low", "Medium", "High", "Too High")
RATING_ZH = {"Too Low": "過低", "Low": "偏低", "Medium": "中等", "High": "偏高", "Too High": "過高"}
RATING_NOTE = {"Too Low": "W′ 過低 → CP 可能被高估", "Too High": "W′ 過高 → CP 可能被低估"}


def _band(value: float, bounds) -> str:
    for name, ub in zip(RATINGS, bounds):
        if value <= ub:
            return name
    return RATINGS[-1]


def rwc_rating(w_prime_j: float, weight_kg: float, sex: str = "male", wind: bool = False) -> dict:
    sex = "female" if str(sex).lower().startswith("f") else "male"
    jkg = round(w_prime_j / weight_kg) if weight_kg else None
    kj = round(w_prime_j / 1000.0, 2)
    r_kg = _band(jkg, RWC_BANDS[(sex, bool(wind), "jkg")]) if jkg is not None else None
    r_kj = _band(kj, RWC_BANDS[(sex, bool(wind), "kj")])
    main = r_kg or r_kj
    return {"j_per_kg": w_prime_j / weight_kg if weight_kg else None, "kj": w_prime_j / 1000.0,
            "rating": main, "rating_zh": RATING_ZH.get(main), "rating_kj": r_kj,
            "note": RATING_NOTE.get(main), "sex": sex, "wind": bool(wind)}


def envelope_points(envelope: dict, durations=CP_DURATIONS) -> list[dict]:
    """Pick the CP-test durations present in an envelope
    {t: {"p", "date", "activity", ...}} (only durations with data)."""
    return [{"t": float(t), **envelope[t]} for t in durations if t in envelope and envelope[t].get("p")]
