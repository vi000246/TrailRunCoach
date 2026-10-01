"""
Running power zones — Steve Palladino's published table (Palladino Power
Project zone document, 2017; unchanged in his 2019 training-intensity
review), as fractions of CP / FTP. Palladino calls them "descriptive, not
prescriptive".

  https://docs.google.com/document/d/e/2PACX-1vTHqzlWwp2Dp6f1cMlS45PycEf-hCAjy61KXG7fRoR2e4mxDyWH6gXo5ZnIvj5b9cTBWBcj9kcfJHel/pub

"Near threshold" is not a zone name in that table; it sits at the top of 3B.
Palladino's three-zone summary (used for intensity distribution) is
low < 80% CP, moderate 80–95%, high ≥ 95%.
"""
from __future__ import annotations

PALLADINO_POWER_ZONES = [
    # (id, name, lo, hi)  — fractions of CP; hi is exclusive, None = open
    ("1A", "Post-interval recovery", 0.50, 0.65),
    ("1B", "EZ warm-up", 0.65, 0.75),
    ("1C", "EZ aerobic", 0.75, 0.80),
    ("2", "Endurance / long run", 0.80, 0.88),
    ("3A", "Threshold stimulus – extensive", 0.88, 0.95),
    ("3B", "Threshold stimulus – intensive", 0.95, 1.01),
    ("4", "Supra-threshold", 1.01, 1.06),
    ("5", "Maximal aerobic power", 1.06, 1.16),
    ("6", "Anaerobic power", 1.16, 1.50),
    ("7", "Sprint / maximal power", 1.50, None),
]

PALLADINO_3ZONE = {"low": 0.80, "high": 0.95}

SOURCE = "Palladino Power Project zone table (2017/2019)"


# ---------------------------------------------------------------------------
# WKO5's own level systems for running (docs/wko5-internals/functions.md §4),
# as (id, name, lo, hi) fractions of the threshold. Checked against WKO5's
# zone-table charts at LTHR 160 / threshold pace 4.66 min/km:
#   Classic HR: AR 0–110, E 110–134, TE 134–152, TH 152–170, VM 170+
#   Friel HR:   1 0–134, 2 136–142, 3 144–150, 4 152–158, 5a 160–163,
#               5b 165–170, 5c 170+
# Friel's bands are whole percents with a 1% gap shown between them
# (Z1 ≤ 84%, Z2 85–89%, …); here each band runs up to the next one's start
# so every sample falls in exactly one zone.
# ---------------------------------------------------------------------------

CLASSIC_HR = [
    ("AR", "Active Recovery", 0.0, 0.69),
    ("E", "Endurance", 0.69, 0.84),
    ("TE", "Tempo", 0.84, 0.95),
    ("TH", "Threshold", 0.95, 1.06),
    ("VM", "VO2max", 1.06, None),
]
FRIEL_HR = [
    ("1", "Recovery", 0.0, 0.85),
    ("2", "Aerobic", 0.85, 0.90),
    ("3", "Tempo", 0.90, 0.95),
    ("4", "SubThreshold", 0.95, 1.00),
    ("5a", "SuperThreshold", 1.00, 1.03),
    ("5b", "Aerobic Capacity", 1.03, 1.06),
    ("5c", "Anaerobic Capacity", 1.06, None),
]
# pace: fractions of threshold PACE (min/km); >1 = slower. Listed easy → hard.
FRIEL_PACE = [
    ("1", "Zone 1", 1.29, None),
    ("2", "Zone 2", 1.14, 1.29),
    ("3", "Zone 3", 1.06, 1.14),
    ("4", "Zone 4", 1.00, 1.06),
    ("5a", "Zone 5a", 0.97, 1.00),
    ("5b", "Zone 5b", 0.90, 0.97),
    ("5c", "Zone 5c", None, 0.90),
]

SYSTEMS = {
    "classichr": {"title": "Classic 心率區間（跑步）", "unit": "bpm", "basis": "lthr", "zones": CLASSIC_HR},
    "frielhr": {"title": "Friel 心率區間（跑步）", "unit": "bpm", "basis": "lthr", "zones": FRIEL_HR},
    "frielpace": {"title": "Friel 配速區間（跑步）", "unit": "min/km", "basis": "tpace", "zones": FRIEL_PACE},
    "palladino": {"title": "Palladino 功率區間（跑步）", "unit": "W", "basis": "cp", "zones": PALLADINO_POWER_ZONES},
}


def zone_of(power: float, cp: float) -> str | None:
    if not cp or power is None or power <= 0:
        return None
    x = power / cp
    if x < PALLADINO_POWER_ZONES[0][2]:
        return "1A"          # below 50% still counts as recovery
    for zid, _, lo, hi in PALLADINO_POWER_ZONES:
        if x >= lo and (hi is None or x < hi):
            return zid
    return None


def _in_zone_expr(system: str, lo, hi) -> str:
    """Per-workout expression: seconds of this workout inside the zone."""
    if system == "frielpace":
        # pace min/km = 60 / speed(km/h); slower pace = lower speed.
        # pace ≥ lo·tpace  ⇔  speed ≤ 60/(lo·tpace)
        conds = ["speed > 0"]
        if lo is not None:
            conds.append(f"speed <= 60/({lo}*runtpace)")
        if hi is not None:
            conds.append(f"speed > 60/({hi}*runtpace)")
    else:
        ch, basis = ("runpower", "cp") if system == "palladino" else ("heartrate", "lthr")
        conds = [f"{ch} > 0"]
        if lo:
            conds.append(f"{ch} >= {lo}*{basis}")
        if hi is not None:
            conds.append(f"{ch} < {hi}*{basis}")
    return f"sum(if({' and '.join(conds)}, deltatime))"


def _on_day(w, end_day: int):
    """The reference run moved to `end_day`, so its thresholds are those in
    effect that day: a plan test dated after the last run but on or before
    `end_day` applies (since Plan.threshold_on stopped applying tests
    backwards, 2026-10-01, the last run's own date would miss a test done
    on a day WKO5 has no run for — the 2026-09-30 CP test)."""
    import dataclasses
    if w is None or not dataclasses.is_dataclass(w):
        return w
    return dataclasses.replace(w, day=max(float(w.day), float(end_day)))


def zone_table(ds, system: str, end_day: int, days: int = 30) -> dict:
    """WKO5-style zone table: boundaries at the threshold in effect on
    `end_day`, plus time in each zone over the last `days` days of runs."""
    import math
    from backend.engine.wko5expr.evaluator import Evaluator, WS
    spec = SYSTEMS[system]
    runs = [w for w in ds.workouts if w.sport == "run" and end_day - days < math.floor(w.day) <= end_day]
    ref = runs[-1] if runs else next((w for w in reversed(ds.workouts) if w.sport == "run"), None)
    ref = _on_day(ref, end_day)
    basis = {"lthr": lambda w: ds.sport_setting("thr", w), "cp": ds.cp,
             "tpace": lambda w: ds.sport_setting("tpace", w)}[spec["basis"]]
    T = basis(ref) if ref else None
    # WKO5 dates never-entered settings 1980-01-01; the plan's tests override
    import datetime as _dt
    from backend.files.wko5_athlete import day_to_date
    setting = {"lthr": "runthr", "tpace": "runtpace"}.get(spec["basis"])
    plan_field = {"lthr": "lthr", "cp": "cp"}.get(spec["basis"])
    hist = ds.athlete.settings.get(setting) or [] if setting else []
    overridden = ref is not None and plan_field and ds.plan.threshold_on(plan_field, day_to_date(ref.day)) is not None
    is_default = bool(hist) and not overridden and all(d == _dt.date(1980, 1, 1) for d, _ in hist)
    ev = Evaluator(ds, end_day - days + 1, end_day, sports={"run"})
    rows, total = [], 0.0
    for zid, name, lo, hi in spec["zones"]:
        r = ev.evaluate(f"athleterange({end_day - days + 1}, {end_day}, {_in_zone_expr(system, lo, hi)})")
        secs = sum(float(v) for v in r.values() if v == v) if isinstance(r, WS) else 0.0
        total += secs
        rows.append({"id": zid, "name": name, "lo": lo, "hi": hi, "seconds": secs,
                     "from": None if (lo is None or T is None) else lo * T,
                     "to": None if (hi is None or T is None) else hi * T})
    for r in rows:
        r["share"] = r["seconds"] / total if total else None
    return {"system": system, "title": spec["title"], "unit": spec["unit"], "basis": spec["basis"],
            "threshold": T, "threshold_is_default": is_default, "days": days, "runs": len(runs), "total_seconds": total, "rows": rows,
            "source": SOURCE if system == "palladino" else "WKO5 level tables (docs/wko5-internals/functions.md §4)"}


# What to run by, per workout type. Power from Palladino's table; HR caps from
# the athlete's AeT / Friel HR bands. For intervals HR lags too much to steer
# by (it keeps climbing through a 5-minute rep), so power is primary there.
WORKOUT_TARGETS = [
    # (id, name, power lo, power hi, hr lo (×LTHR or "aet"), hr hi, primary, example, source)
    ("recovery", "恢復跑", None, 0.75, None, 0.85, "心率", "20–40 分鐘，隔天有強度課時", "Palladino 1A–1B；Friel Z1"),
    ("z2", "輕鬆跑（Zone 2）", 0.75, 0.80, None, "aet", "心率", "大部分的跑步；心率不超過 AeT", "Palladino 1C；Uphill Athlete AeT"),
    ("long", "長跑 / 山路長天", 0.80, 0.88, None, "aet", "心率", "60 分鐘以上；上坡可以走，心率壓在 AeT", "Palladino Z2；Uphill Athlete"),
    ("threshold", "閾值（Near threshold）", 0.95, 1.01, 0.95, 1.00, "功率", "3×10 分鐘 或 2×15 分鐘，休 2–3 分鐘", "Palladino 3B；Friel Z4"),
    ("supra", "Supra threshold", 1.01, 1.06, 1.00, 1.03, "功率", "4–6×5 分鐘，休 2:45（你的筆記）", "Palladino Z4；Friel Z5a"),
    ("vo2", "VO2max", 1.06, 1.16, 1.03, 1.06, "功率", "5×3 分鐘，休 3 分鐘；FTP 紮實後、賽前 4–6 週", "Palladino Z5；Friel Z5b"),
]


def training_targets(ds, end_day: int, lthr_est=None, aet_est=None) -> dict:
    """Suggested HR / power ranges per workout type. Uses the thresholds in
    effect; when LTHR is still WKO5's untouched default and an estimate
    exists, uses the estimate and says so."""
    import datetime as dt
    import math
    runs = [w for w in ds.workouts if w.sport == "run" and math.floor(w.day) <= end_day]
    ref = _on_day(runs[-1], end_day) if runs else None
    cp = ds.cp(ref) if ref else None
    lthr = ds.sport_setting("thr", ref) if ref else None
    hist = ds.athlete.settings.get("runthr") or []
    from backend.files.wko5_athlete import day_to_date
    planned = ref is not None and ds.plan.threshold_on("lthr", day_to_date(ref.day)) is not None
    lthr_src = "你的測試" if planned else "WKO5 設定"
    if not planned and hist and all(d == dt.date(1980, 1, 1) for d, _ in hist) and lthr_est:
        lthr, lthr_src = float(lthr_est), "自動估算（尚未套用）"
    aet_planned = ref is not None and ds.plan.threshold_on("aethr", day_to_date(ref.day)) is not None
    if aet_planned:
        aet, aet_src = ds.aethr(ref), "你的測試"
    elif aet_est:
        aet, aet_src = float(aet_est), "自動估算（尚未套用）"
    else:
        aet, aet_src = (None if lthr is None else 0.89 * lthr), "0.89 × LTHR（Friel Z2 上限）"
    rows = []
    for tid, name, plo, phi, hlo, hhi, primary, example, src in WORKOUT_TARGETS:
        def hr(x):
            if x is None or lthr is None:
                return None
            return aet if x == "aet" else x * lthr
        rows.append({"id": tid, "name": name, "primary": primary, "example": example, "source": src,
                     "power": [None if plo is None or not cp else plo * cp, None if phi is None or not cp else phi * cp],
                     "power_pct": [plo, phi], "hr": [hr(hlo), hr(hhi)]})
    return {"cp": cp, "cp_source": "你的測試" if ref is not None and ds.plan.threshold_on("cp", day_to_date(ref.day)) is not None
            else "WKO5 mFTP", "lthr": lthr, "lthr_source": lthr_src, "aet": aet, "aet_source": aet_src, "rows": rows}


def zones_json(cp: float | None) -> list[dict]:
    return [{"id": z, "name": n, "lo": lo, "hi": hi,
             "watts": None if not cp else [round(lo * cp), None if hi is None else round(hi * cp)]}
            for z, n, lo, hi in PALLADINO_POWER_ZONES]
