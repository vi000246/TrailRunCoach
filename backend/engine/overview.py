"""
Overview — every sport together, by week / month / year, plus what to do this
week.

The athlete's trail, 百岳 and bike days are too few to read on their own, so
nothing here is split into per-sport pages. Activities are grouped into
categories only to colour the stacked bars; totals, load (CTL / ATL / TSB) and
the weekly plan use everything together.

Volume is moving time, not recorded time: a multi-day 百岳 file records the
nights too (one 51 h trip had about 7 h of walking).

The weekly plan is a coaching heuristic built from the cited rules, applied to
the athlete's own numbers. It is not a medical or injury prediction.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

from backend.engine.wko5expr.dataset import Dataset, Workout, date_to_day, day_to_date
from backend.engine.wko5expr.evaluator import WS, Evaluator

# ---------------------------------------------------------------------------
# categories (colour only — totals always include everything)
# ---------------------------------------------------------------------------

# colours: the validated categorical palette in its fixed slot order (light
# mode); the page swaps in the dark-mode steps itself
CATEGORIES = {
    "road": ("路跑", "#2a78d6"),
    "trail": ("越野跑", "#eb6834"),
    "hike": ("登山健行", "#1baf7a"),
    "bike": ("騎車", "#eda100"),
    "strength": ("肌力", "#e87ba4"),
    "walk": ("走路", "#008300"),
    "other": ("其他", "#4a3aa7"),
}
ENDURANCE = {"road", "trail", "hike", "bike"}
FOOT = {"road", "trail", "hike"}
MOUNTAIN = {"trail", "hike"}

# default TSS per moving hour when the athlete has no history in a category
TSS_PER_HOUR_DEFAULT = {"road": 55.0, "trail": 60.0, "hike": 45.0, "bike": 50.0,
                        "strength": 25.0, "walk": 20.0, "other": 25.0}

# moving-only HR intensity: WKO5 counts a sample as moving above 1 mph
# (backend/engine/algorithms/wko5_time.MOVING_SPEED_KMH); samples without a
# speed channel (treadmill without a footpod) still count.
_MOVING = "(speed > 1.6 or !isvalid(speed))"
INTENSITY_EXPRS = {
    "low": f"sum(if(heartrate > 0 and heartrate < aethr and {_MOVING}, deltatime))",
    "mid": f"sum(if(heartrate >= aethr and heartrate < lthr and {_MOVING}, deltatime))",
    "high": f"sum(if(heartrate >= lthr and {_MOVING}, deltatime))",
}
HARD_EXPRS = {
    "hr": "sum(if(heartrate >= lthr, deltatime))",
    "power": "sum(if(runpower >= 0.95*cp, deltatime))",
}
HARD_SESSION_S = 600          # ≥ 10 min at/above threshold = a quality session


def category(w: Workout) -> str:
    tags, st = set(w.tags), (w.sport_type or "")
    if "runningtrail" in tags or st == "trail running":
        return "trail"
    if w.sport == "run":
        return "road"
    if tags & {"hiking", "mountaineering"} or st in ("hiking", "mountaineering"):
        return "hike"
    if w.sport in ("bike", "road bike") or "cycling" in tags or "cycling" in st:
        return "bike"
    if w.sport == "strength" or st == "strength":
        return "strength"
    if w.sport == "walk":
        return "walk"
    return "other"


def _n(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def moving_s(w: Workout) -> float:
    m = w.metrics
    return _n(m.get("movingduration")) or _n(m.get("duration")) or 0.0


def wdate(w: Workout) -> dt.date:
    return day_to_date(w.day)


def ep_km(w: Workout) -> float:
    """Effort km, 健行筆記 / ITRA convention: km + gain/100."""
    return (_n(w.metrics.get("distance")) or 0.0) + (_n(w.metrics.get("climbing")) or 0.0) / 100.0


def activity_row(w: Workout) -> dict:
    m = w.metrics
    cat = category(w)
    return {
        "index": w.idx, "start": w.entry.start.isoformat(), "date": wdate(w).isoformat(),
        "category": cat, "category_label": CATEGORIES[cat][0], "sport_type": w.sport_type,
        "moving_s": moving_s(w), "duration_s": _n(m.get("duration")),
        "distance_km": _n(m.get("distance")), "climbing_m": _n(m.get("climbing")),
        "descending_m": _n(m.get("descending")), "tss": _n(m.get("tss")),
        "if": _n(m.get("if")), "np": _n(m.get("np")), "ep_km": ep_km(w),
    }


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------

UNITS = ("week", "month", "year")


def period_start(d: dt.date, unit: str) -> dt.date:
    if unit == "week":
        return d - dt.timedelta(days=d.weekday())          # Monday, like the evaluator
    if unit == "month":
        return d.replace(day=1)
    return d.replace(month=1, day=1)


def period_next(start: dt.date, unit: str) -> dt.date:
    if unit == "week":
        return start + dt.timedelta(days=7)
    if unit == "month":
        return (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start.replace(year=start.year + 1)


def period_prev(start: dt.date, unit: str) -> dt.date:
    if unit == "week":
        return start - dt.timedelta(days=7)
    if unit == "month":
        return (start - dt.timedelta(days=1)).replace(day=1)
    return start.replace(year=start.year - 1)


def period_label(start: dt.date, unit: str) -> str:
    if unit == "week":
        end = start + dt.timedelta(days=6)
        return f"{start.month}/{start.day}–{end.month}/{end.day}"
    if unit == "month":
        return f"{start.year}/{start.month:02d}"
    return str(start.year)


def workouts_between(ds: Dataset, start: dt.date, end_excl: dt.date) -> list[Workout]:
    lo, hi = date_to_day(start), date_to_day(end_excl)
    return [w for w in ds.workouts if lo <= math.floor(w.day) < hi]


def _totals(ws: list[Workout]) -> dict:
    by = {k: {"sessions": 0, "moving_s": 0.0, "distance_km": 0.0, "climbing_m": 0.0, "tss": 0.0}
          for k in CATEGORIES}
    t = {"sessions": 0, "moving_s": 0.0, "distance_km": 0.0, "climbing_m": 0.0,
         "descending_m": 0.0, "tss": 0.0, "ep_km": 0.0, "days": 0}
    days = set()
    for w in ws:
        m, c = w.metrics, category(w)
        mv = moving_s(w)
        for d in (by[c], t):
            d["sessions"] += 1
            d["moving_s"] += mv
            d["distance_km"] += _n(m.get("distance")) or 0.0
            d["climbing_m"] += _n(m.get("climbing")) or 0.0
            d["tss"] += _n(m.get("tss")) or 0.0
        t["descending_m"] += _n(m.get("descending")) or 0.0
        t["ep_km"] += ep_km(w) if c in FOOT else 0.0
        days.add(wdate(w))
    t["days"] = len(days)
    t["by_category"] = {k: v for k, v in by.items() if v["sessions"]}
    return t


def _intensity(ds: Dataset, ws: list[Workout], start: dt.date, end_excl: dt.date) -> dict:
    """Moving time below AeT / AeT–LTHR / above LTHR, endurance sessions only."""
    keep = {w.idx for w in ws if category(w) in ENDURANCE}
    out = {"low": 0.0, "mid": 0.0, "high": 0.0}
    if not keep:
        return {**out, "total": 0.0, "low_share": None}
    b = int(date_to_day(start))
    e = int(date_to_day(end_excl)) - 1
    ev = Evaluator(ds, b, e)
    for k, expr in INTENSITY_EXPRS.items():
        r = ev.evaluate(f"athleterange({b}, {e}, {expr})")
        if isinstance(r, WS):
            out[k] = sum(float(v) for i, v in r.items() if i in keep and v == v)
    tot = out["low"] + out["mid"] + out["high"]
    return {**out, "total": tot, "low_share": out["low"] / tot if tot > 0 else None}


def summary(ds: Dataset, unit: str, anchor: dt.date, n: int = 12, detail: bool = True) -> dict:
    """`n` consecutive periods ending with the one containing `anchor`; the last
    one gets the detail (intensity split, key sessions, activity list)."""
    if unit not in UNITS:
        raise ValueError(f"unit must be one of {UNITS}")
    last = period_start(anchor, unit)
    starts = [last]
    for _ in range(max(1, n) - 1):
        starts.insert(0, period_prev(starts[0], unit))
    buckets = []
    for s in starts:
        e = period_next(s, unit)
        ws = workouts_between(ds, s, e)
        buckets.append({"start": s.isoformat(), "end": (e - dt.timedelta(days=1)).isoformat(),
                        "label": period_label(s, unit), **_totals(ws)})
    cur = buckets[-1]
    out = {"unit": unit, "anchor": anchor.isoformat(), "buckets": buckets,
           "categories": {k: {"label": v[0], "color": v[1]} for k, v in CATEGORIES.items()}}
    if not detail:
        return out
    s, e = last, period_next(last, unit)
    ws = workouts_between(ds, s, e)
    prev = buckets[-2] if len(buckets) > 1 else None
    hist = buckets[:-1]
    avg = {k: statistics.mean(b[k] for b in hist) for k in ("moving_s", "tss", "climbing_m", "sessions",
                                                           "distance_km")} if hist else None
    rows = [activity_row(w) for w in sorted(ws, key=lambda w: w.day, reverse=True)]
    endurance = [r for r in rows if r["category"] in ENDURANCE]
    today = day_to_date(ds.today)
    elapsed = None
    if s <= today < e:        # the current period is still running
        elapsed = ((today - s).days + 1) / (e - s).days
    out["current"] = {
        **cur,
        "elapsed_share": elapsed,
        "intensity": _intensity(ds, ws, s, e),
        "longest": max(endurance, key=lambda r: r["moving_s"], default=None),
        "biggest_climb": max(endurance, key=lambda r: r["climbing_m"] or 0, default=None),
        "hardest": max(rows, key=lambda r: r["tss"] or 0, default=None),
        "vs_prev": None if prev is None else {
            k: (cur[k] - prev[k]) for k in ("moving_s", "tss", "climbing_m", "sessions", "distance_km")},
        "typical": avg,
        "activities": rows,
    }
    return out


# ---------------------------------------------------------------------------
# performance management chart — all sports in one load
# ---------------------------------------------------------------------------

def daily_tss(ds: Dataset) -> dict[int, float]:
    """Day number -> summed TSS, with the tl() input rule (0 <= x <= 5000)."""
    out: dict[int, float] = {}
    for w in ds.workouts:
        v = _n(w.metrics.get("tss"))
        if v is not None and 0 <= v <= 5000:
            d = int(math.floor(w.day))
            out[d] = out.get(d, 0.0) + v
    return out


def pmc(ds: Dataset, begin: dt.date, end: dt.date) -> dict:
    """CTL / ATL / TSB per day over [begin, end] — the same tl() recurrence and
    TSB = yesterday's CTL − ATL as the chart expressions `ctl`, `atl`, `tsb`."""
    b, e = int(date_to_day(begin)), int(date_to_day(end))
    ev = Evaluator(ds, b, e)
    ctl, atl, tsb = ev.evaluate("ctl"), ev.evaluate("atl"), ev.evaluate("tsb")
    tss = daily_tss(ds)
    rows = []
    for d in range(b, e + 1):
        rows.append({"date": day_to_date(d).isoformat(), "tss": tss.get(d, 0.0),
                     "ctl": _n(ctl.at(d)), "atl": _n(atl.at(d)), "tsb": _n(tsb.at(d))})
    return {"begin": begin.isoformat(), "end": end.isoformat(),
            "ctlconstant": ds.athlete.ctlconstant, "atlconstant": ds.athlete.atlconstant,
            "series": rows}


def project(ctl0: float, atl0: float, planned: list[float], cc: float, ac: float) -> list[dict]:
    """Continue the tl() recurrence from today's CTL/ATL with planned daily TSS.
    Row i is day +i+1; its TSB is the previous day's CTL − ATL."""
    out, c, a = [], ctl0, atl0
    for x in planned:
        tsb = c - a
        c = c + (x - c) / cc
        a = a + (x - a) / ac
        out.append({"tss": x, "ctl": c, "atl": a, "tsb": tsb})
    return out


# ---------------------------------------------------------------------------
# this week's plan
# ---------------------------------------------------------------------------

SRC_RAMP = "Palladino CTL ramp（每週 +1～3 可長期維持，3～5 菁英）"
SRC_TEN = "Uphill Athlete：週量增幅 ≤ 10%"
SRC_31 = "3:1 週期（三週加量、一週恢復；Friel / Uphill Athlete 常見做法）"
SRC_BOSQUET = "Bosquet 2007：減量 2 週、量減 41–60%、強度與次數維持"
SRC_UA = "Uphill Athlete"
SRC_KOOP = "Koop《Training Essentials for Ultrarunning》"
SRC_PALLADINO = "Palladino 功率區間"

RAMP_GOAL = {"base": 3.0, "specific": 4.0}            # CTL points per week
WEEKDAYS = "一二三四五六日"


@dataclass
class Session:
    id: str
    kind: str                     # long / mountain / quality / test / easy / strength / race / rest
    title: str
    minutes: int
    target: str = ""
    detail: str = ""
    source: str = ""
    tss: float = 0.0
    day: Optional[str] = None     # ISO date suggested
    done: bool = False
    done_by: Optional[dict] = None
    terrain: Optional[str] = None       # road / trail / hike (課表偏好); None = unspecified
    distance_km: Optional[float] = None
    climb_m: Optional[float] = None
    protocol: Optional[str] = None      # CP-test protocol (engine/cp_protocols.py); tests only
    heat: bool = False                  # 熱適應課 (engine/heat_plan.py); kind easy / long / heat_passive


def _week_hours(ds: Dataset, monday: dt.date) -> tuple[float, float]:
    ws = workouts_between(ds, monday, monday + dt.timedelta(days=7))
    return (sum(moving_s(w) for w in ws) / 3600.0,
            sum(_n(w.metrics.get("tss")) or 0.0 for w in ws))


def _tss_per_hour(ds: Dataset, today: dt.date) -> dict[str, float]:
    """Median TSS per moving hour by category over the last 180 days."""
    lo = today - dt.timedelta(days=180)
    per: dict[str, list[float]] = {}
    for w in workouts_between(ds, lo, today + dt.timedelta(days=1)):
        h, t = moving_s(w) / 3600.0, _n(w.metrics.get("tss"))
        if h >= 0.25 and t:
            per.setdefault(category(w), []).append(t / h)
    return {k: (statistics.median(per[k]) if len(per.get(k, [])) >= 3 else v)
            for k, v in TSS_PER_HOUR_DEFAULT.items()}


def _long_weekday(ds: Dataset, today: dt.date, weeks: int = 12) -> int:
    """The weekday the athlete usually does the week's longest session (0=Mon)."""
    monday = period_start(today, "week")
    days = []
    for i in range(1, weeks + 1):
        s = monday - dt.timedelta(weeks=i)
        ws = [w for w in workouts_between(ds, s, s + dt.timedelta(days=7)) if category(w) in ENDURANCE]
        if ws:
            days.append(wdate(max(ws, key=moving_s)).weekday())
    if not days:
        return 5
    return statistics.mode(days)


def _fmt_range(a, unit: str) -> str:
    lo, hi = a
    if lo is None and hi is None:
        return ""
    if lo is None:
        return f"< {hi:.0f} {unit}"
    if hi is None:
        return f"> {lo:.0f} {unit}"
    return f"{lo:.0f}–{hi:.0f} {unit}"


def _targets(tt: dict) -> dict[str, str]:
    """Human text per workout type from zones.training_targets()."""
    out = {}
    for r in tt.get("rows", []):
        parts = []
        if r["primary"] == "心率":
            if any(v is not None for v in r["hr"]):
                parts.append("心率 " + _fmt_range(r["hr"], "bpm"))
            if any(v is not None for v in r["power"]):
                parts.append("功率 " + _fmt_range(r["power"], "W"))
        else:
            if any(v is not None for v in r["power"]):
                parts.append("功率 " + _fmt_range(r["power"], "W"))
            if any(v is not None for v in r["hr"]):
                parts.append("心率 " + _fmt_range(r["hr"], "bpm"))
        out[r["id"]] = " · ".join(parts)
    return out


def _hard_seconds(ds: Dataset, ws: list[Workout], b: int, e: int) -> dict[int, float]:
    if not ws:
        return {}
    ev = Evaluator(ds, b, e)
    best: dict[int, float] = {}
    keep = {w.idx for w in ws}
    for expr in HARD_EXPRS.values():
        r = ev.evaluate(f"athleterange({b}, {e}, {expr})")
        if isinstance(r, WS):
            for i, v in r.items():
                if i in keep and v == v:
                    best[i] = max(best.get(i, 0.0), float(v))
    return best


def _gate_session(gate: dict, dec: dict, th: dict, hours: Optional[float]) -> dict:
    """The base-phase interval for the gate's decision (engine/quality_gate.py)."""
    from backend.engine import quality_gate as QG
    spec = dec["spec"]
    pre = "" if spec is QG.RECOVERY else QG.prefix(gate)
    if (gate.get("dose") or {}).get("faded") and spec not in (QG.RECOVERY, QG.SUB):
        pre = "上次間歇後段掉了：退一步；" + pre
    s = QG.session(spec, {"cp": th.get("cp"), "lthr": th.get("lthr"), "aet": th.get("aet")}, pre, hours,
                   bool((gate.get("lthr") or {}).get("default")))
    s["source"] = QG.source(gate, spec)
    return s


def week_plan(ds: Dataset, status, today: Optional[dt.date] = None, prefs=None, blackouts=None) -> dict:
    """Target volume and sessions for the current Monday–Sunday week.

    `status` is a computed `backend.engine.status.Status` (phase, goals and the
    indicators steer the plan). `prefs`: engine.plan_prefs.Prefs (課表偏好);
    None or the defaults keep the original rules untouched. `blackouts`:
    engine.blackouts ranges (不排課日期); None / empty = none."""
    from backend.engine import blackouts as BL
    from backend.engine import plan_prefs as PP
    PR = prefs if prefs is not None and prefs.active else None
    bmap = BL.blocked(blackouts or ())
    allowed_fn = PR.allowed if PR is not None else None
    today = today or day_to_date(ds.today)
    monday = period_start(today, "week")
    sunday = monday + dt.timedelta(days=6)
    kind = status.kind or "base"
    by = {i.id: i for i in status.indicators}
    goals = status.goals
    notes: list[dict] = []

    # ---- history ---------------------------------------------------------
    hist = [(monday - dt.timedelta(weeks=i), *_week_hours(ds, monday - dt.timedelta(weeks=i)))
            for i in range(8, 0, -1)]                      # oldest → last complete week
    hours4 = [h for _, h, _ in hist[-4:]]
    base4 = statistics.mean(hours4) if hours4 else 0.0
    last_h = hist[-1][1] if hist else 0.0
    ref = max(base4, last_h)
    tph = _tss_per_hour(ds, today)
    tot_h = sum(h for _, h, _ in hist[-6:])
    r_all = (sum(t for _, _, t in hist[-6:]) / tot_h) if tot_h > 1 else 50.0

    cc, ac = ds.athlete.ctlconstant, ds.athlete.atlconstant
    ev = Evaluator(ds, int(date_to_day(monday)) - 1, int(date_to_day(sunday)))
    ctl_s, atl_s = ev.evaluate("ctl"), ev.evaluate("atl")
    d_prev_sun = int(date_to_day(monday)) - 1
    ctl0 = _n(ctl_s.at(d_prev_sun)) or 0.0
    tday = int(date_to_day(today))
    ctl_today, atl_today = _n(ctl_s.at(tday)) or 0.0, _n(atl_s.at(tday)) or 0.0
    tsb_today = _n(ctl_s.at(tday - 1) - atl_s.at(tday - 1)) or 0.0

    # ---- volume target ---------------------------------------------------
    f7 = 1.0 - (1.0 - 1.0 / cc) ** 7
    mode = kind
    hours = ref
    why: list[str] = []
    ramp_goal = RAMP_GOAL.get(kind)
    build3 = len(hist) >= 4 and all(hist[i][1] >= 0.95 * hist[i - 1][1] and hist[i][1] > 0.5
                                    for i in range(len(hist) - 3, len(hist)))
    if kind in ("base", "specific"):
        need_tss = 7.0 * (ctl0 + ramp_goal / f7)
        need_h = need_tss / r_all
        cap = max(1.10 * ref, ref + 0.5)
        hours = min(max(need_h, base4), cap)
        why.append(f"CTL {ctl0:.0f} 要每週 +{ramp_goal:.0f}，需要約 {need_tss:.0f} TSS（≈ {need_h:.1f} h）")
        if need_h > cap:
            why.append(f"但週量上限 = 近 4 週 {base4:.1f} h / 上週 {last_h:.1f} h 的 +10%（至少 +0.5 h）→ {cap:.1f} h")
        if tsb_today < -30:
            mode, hours = "recovery_week", 0.6 * base4
            why.append(f"TSB {tsb_today:+.0f} < −30：改成恢復週（近 4 週的 60%）")
        elif tsb_today < -20:
            hours = min(hours, base4)
            why.append(f"TSB {tsb_today:+.0f} < −20：先維持量，不加")
        elif build3:
            mode, hours = "recovery_week", 0.65 * statistics.mean(h for _, h, _ in hist[-3:])
            why.append("已連續 3 週加量：這週是恢復週（前 3 週平均的 65%）")
    elif kind == "taper":
        base6 = statistics.mean(h for _, h, _ in hist[-6:]) if hist else 0.0
        days_to = goals.get("days_to_next_a")
        share = 0.4 if days_to is not None and days_to <= 7 else 0.5
        hours = base6 * share
        why.append(f"減量期：平常 {base6:.1f} h × {share:.0%}")
    elif kind == "event":
        hours = 0.3 * base4
        why.append("比賽週：短、輕鬆")
    elif kind in ("recovery", "transition"):
        hours = (0.5 if kind == "recovery" else 0.65) * base4
        why.append(f"{'恢復' if kind == 'recovery' else '轉換'}期：近 4 週的 {0.5 if kind == 'recovery' else 0.65:.0%}")
    hours = max(hours, 0.0)
    if PR is not None and PR.weekly_hours is not None and hours > PR.weekly_hours:
        hours = PR.weekly_hours
        why.append(f"你的每週時數上限 {PR.weekly_hours:g} h")
    lost: list[dt.date] = []
    if bmap:
        # 不排課日期 (engine/blackouts.py): after a week that lost days, the <= 10 %
        # step is taken from what was actually done; this week's lost days shrink it
        def trained(m: dt.date) -> set:
            return {wdate(w) for w in workouts_between(ds, m, m + dt.timedelta(days=7))}
        prev_m = monday - dt.timedelta(days=7)
        prev_lost = BL.lost_days(bmap, prev_m, allowed_fn, trained(prev_m))
        if prev_lost:
            cap_b = BL.step_cap(last_h)
            if hours > cap_b + 1e-9:
                hours = cap_b
                why.append(f"上週 {BL.range_text(prev_lost)} 不排課、實際 {last_h:.1f} h：本週從實際量 +10%（至少 +0.5 h）→ {cap_b:.1f} h")
                notes.append(BL.step_note(prev_lost, last_h, cap_b))
        lost = BL.lost_days(bmap, monday, allowed_fn, trained(monday))
        if lost:
            f = BL.factor(monday, lost, allowed_fn)
            lost_h = hours * (1.0 - f)
            hours *= f
            why.append(f"不排課 {BL.range_text(lost)}：少 {len(lost)} 個可練日，週量 × {f:.0%}")
            notes.append(BL.week_note(bmap, lost, lost_h))
    tss_target = hours * r_all

    # ---- what is done already -------------------------------------------
    week_ws = workouts_between(ds, monday, sunday + dt.timedelta(days=1))
    done_h = sum(moving_s(w) for w in week_ws) / 3600.0
    done_tss = sum(_n(w.metrics.get("tss")) or 0.0 for w in week_ws)
    hard = _hard_seconds(ds, [w for w in week_ws if category(w) in ENDURANCE],
                         int(date_to_day(monday)), int(date_to_day(sunday)))

    # ---- session template ------------------------------------------------
    from backend.engine.zones import training_targets
    est = {}
    if not ds.config.parity:
        try:
            from backend.engine.thresholds import estimate
            est = estimate(ds, today)
        except Exception:
            est = {}
    tt = training_targets(ds, tday, (est.get("lthr") or {}).get("value"),
                          (est.get("aethr") or {}).get("value"))
    tgt = _targets(tt)
    aet = tt.get("aet")
    lvl = lambda iid: getattr(by.get(iid), "level", "na")
    days_to = goals.get("days_to_next_a")
    goal_h = (goals["targets"].get("est_hours") or {}).get("value")
    goal_d = (goals["targets"].get("climb_per_km") or {}).get("value")
    mountain_goal = bool(goal_d) or any(e.kind in ("race", "baiyue") for e in status.plan.events
                                        if e.end >= today)
    longest28 = max((moving_s(w) for w in workouts_between(ds, today - dt.timedelta(days=28),
                                                          today + dt.timedelta(days=1))
                     if category(w) in ENDURANCE), default=0.0) / 60.0
    minutes_total = hours * 60.0
    sessions: list[Session] = []
    # 間歇門檻 (engine/quality_gate.py): status.i_gate's result; the method, this
    # week's guardrails and the dose step
    from backend.engine import aet_test as AT
    from backend.engine import quality_gate as QG
    gate = dict(getattr(by.get("gate"), "extra", None) or {})
    if not gate:                          # a status without i_gate: no method, guardrails only
        gate = {"state": "none", "mode": "auto", "resolved": "none", "levels": {i: lvl(i) for i in ("intensity", "drift")},
                "guard": QG.guard(), "dose": {"done": 0, "step": 0, "faded": False}}
    gate["levels"] = {i: lvl(i) for i in ("intensity", "drift")}
    gate_levels = gate["levels"]
    dec = QG.week_decision(gate, kind, mode, monday)
    allow_quality = dec["allow"]
    tx = getattr(by.get("testing"), "extra", None) or {}
    cp_due = tx.get("cp_due", lvl("testing") in ("bad", "watch"))      # the CP test measures CP only
    test_due = cp_due and lvl("testing") in ("bad", "watch") and (days_to is None or days_to > 10)
    # 課表偏好 CP 測試方式 (engine/cp_protocols.py) — read even when the other
    # preferences are the defaults (it is not part of Prefs.active)
    from backend.engine import cp_protocols as CPP
    protocol = CPP.norm(getattr(prefs, "cp_test_protocol", None))
    test_s = CPP.session_for(protocol) if test_due else None      # race: nothing scheduled
    # the AeT drift test: base phase, no (fresh) measured AeT, never the CP-test week
    aet_due = test_s is None and (days_to is None or days_to > 10) and AT.due(
        today, kind, gate.get("base_start"), tx.get("aet_date"), tx.get("aet_last_test"))
    strength_n = 2 if kind in ("base", "transition", "recovery") or lvl("strength") in ("bad", "watch") else 1

    def add(**kw):
        sessions.append(Session(**kw))

    if kind in ("base", "specific") and mode != "recovery_week":
        if kind == "specific" and goal_h:
            long_min = max(90.0, min(goal_h * 0.7 * 60.0, max(longest28, 60.0) * 1.15))
        else:
            long_min = max(60.0, min(0.30 * minutes_total, max(longest28, 60.0) * 1.15))
        long_min = min(long_min, 0.5 * minutes_total) if minutes_total >= 120 else long_min
        terrain = (f"挑每公里爬升 ≥ {goal_d * 0.7:.0f} m 的路線" if goal_d else
                   "有山路就走山路，陡坡用走的" if mountain_goal else "平路或緩坡")
        add(id="long", kind="long", title="長時間輕鬆" + ("（山路）" if mountain_goal else ""),
            minutes=int(round(long_min / 5) * 5), target=tgt.get("long", ""),
            detail=f"{terrain}；全程心率壓在 AeT{f' {aet:.0f} bpm' if aet else ''} 以下，爬坡可以走",
            source=SRC_KOOP if kind == "specific" else SRC_UA,
            tss=long_min / 60.0 * tph["trail" if mountain_goal else "road"])
        if test_s is not None:
            add(**{**test_s, "detail": test_s["detail"] + "。門檻過期或沒測過：區間、TSS、賽事功率都靠它"})
        elif aet_due and kind == "base":
            # AeT drift test in place of this week's interval (engine/aet_test.py); its
            # length by the 課表偏好 weekday cap: 80′ standard, or UA's 50′ minimum
            add(**AT.session(tt, AT.start_hr((est.get("aethr") or {}).get("value"), tt.get("lthr")),
                             AT.start_power(tt.get("cp")), getattr(prefs, "cap_weekday", None)))
        elif allow_quality and kind == "specific":
            add(id="quality", kind="quality", title="爬坡間歇 5×4 分", minutes=60,
                target=tgt.get("supra", ""), detail="上坡 4 分鐘（6–10% 坡），慢跑或走下來恢復；暖身 15 分、緩和 10 分",
                source=SRC_PALLADINO + "（Supra-threshold）", tss=60 / 60 * 75)
        elif allow_quality and kind == "base" and dec["spec"] is not None:
            # the gate's dose step (engine/quality_gate.py §4.5)
            add(**_gate_session(gate, dec, tt, hours))
    elif kind == "base" and mode == "recovery_week" and allow_quality and dec["spec"] is not None:
        # 3:1 recovery week: a short fartlek instead of intervals (Palladino)
        add(**_gate_session(gate, dec, tt, hours))
    elif kind == "taper":
        add(id="quality", kind="quality", title="短強度 4×3 分", minutes=45,
            target=tgt.get("threshold", ""), detail="保留強度、不累積疲勞（98–102% CP）", source=SRC_BOSQUET,
            tss=45 / 60 * 65)
    elif kind == "event":
        add(id="race", kind="race", title="比賽", minutes=0, detail="賽前 2 天 20–30 分輕鬆跑＋幾趟加速",
            source="你的筆記")
    for i in range(strength_n):
        add(id=f"strength{i + 1}", kind="strength", title="肌力（下肢單腳＋核心）", minutes=35,
            detail="膝主導＋臀中肌；安排在輕鬆日或跑完後", source=SRC_UA,
            tss=35 / 60 * tph["strength"])
    used = sum(s.minutes for s in sessions if s.kind not in ("strength",))
    left = max(0.0, minutes_total - used)
    n_easy = 0 if left < 25 else max(1, min(5, int(round(left / 50.0))))
    for i in range(n_easy):
        m = left / n_easy
        strides = kind == "base" and i == 0 and mode != "recovery_week"
        add(id=f"easy{i + 1}", kind="easy", title="輕鬆跑" + ("＋坡道衝刺 8×10 秒" if strides else ""),
            minutes=int(round(m / 5) * 5), target=tgt.get("z2", ""),
            detail="心率不超過 AeT" + ("；最後 8 趟 10 秒上坡衝刺，走下來恢復" if strides else ""),
            source=SRC_UA + ("；Palladino 基礎中期坡衝刺" if strides else ""), tss=m / 60.0 * tph["road"])
    if PR is not None:
        # 課表偏好: counts, caps, terrain, interval target (engine/plan_prefs.py)
        ctx = PP.Ctx(kind=kind, mode=mode, allow_quality=allow_quality, rates=tph, aet=aet,
                     slots=max(1, sum(bool(x) for x in PR.days) - len(lost)), notes=notes,
                     quality_cap=1 if kind == "base" and QG.guardrail_mode(gate) else None)
        shaped = PP.shape([asdict(s) for s in sessions], minutes_total, PR, ctx)
        sessions = []
        for d in shaped:
            flag = d.pop("long_day", False)
            sessions.append(Session(**d))
            sessions[-1]._long_day = flag          # soft cap: this easy run carries the excess

    # ---- mark what is done ----------------------------------------------
    pool = sorted(week_ws, key=lambda w: w.day)
    used_idx: set[int] = set()

    def take(pred) -> Optional[Workout]:
        for w in pool:
            if w.idx not in used_idx and pred(w):
                used_idx.add(w.idx)
                return w
        return None

    for s in sessions:
        w = None
        if s.kind == "strength":
            w = take(lambda w: category(w) == "strength")
        elif s.id == "long":                      # kind long, or hike (課表偏好 登山)
            w = take(lambda w: category(w) in ENDURANCE and moving_s(w) / 60 >= 0.8 * s.minutes)
        elif s.id == "test_aet":
            # the 50-min test: a ≥ 48-min road run (2′ slack) titled AeT — the COROS
            # workout's name; untitled only from 55 min (workout_review.TEST_AET_MIN_S),
            # so the athlete's ordinary 41–52′ road runs are not taken for the test (自組)
            from backend.engine import workout_review as WR
            w = take(lambda w: category(w) == "road" and moving_s(w) >= AT.WARM_S + AT.MAIN_MIN_S - 2 * 60
                     and (bool(WR.AET_TITLE.search(WR._title(w))) or moving_s(w) >= WR.TEST_AET_MIN_S))
        elif s.kind in ("quality", "test"):
            need = QG.hard_need(s.title, HARD_SESSION_S)       # 5×1′ never reaches 10 min at threshold
            w = take(lambda w: hard.get(w.idx, 0) >= need)
        elif s.kind == "easy":
            w = take(lambda w: category(w) in ENDURANCE)
        if w is not None:
            s.done = True
            s.done_by = activity_row(w)
            s.day = wdate(w).isoformat()

    # ---- place the rest on the remaining days ----------------------------
    done_today = any(wdate(w) == today for w in week_ws)
    first = today + dt.timedelta(days=1) if done_today else today
    free = [first + dt.timedelta(days=i) for i in range((sunday - first).days + 1)] if first <= sunday else []
    free = [d for d in free if d.isoformat() not in bmap]          # 不排課日期: never placed there
    todo = [s for s in sessions if not s.done and s.kind not in ("race",)]
    main_todo = [s for s in todo if s.kind != "strength"]
    long_wd = _long_weekday(ds, today)
    plan_days: dict[str, list[str]] = {}

    def put(s: Session, d: dt.date):
        s.day = d.isoformat()
        plan_days.setdefault(s.day, []).append(s.id)

    long_done = next((dt.date.fromisoformat(s.day) for s in sessions if s.kind == "long" and s.done and s.day), None)
    if PR is not None:
        long_wd = PP.long_weekday(PR, long_wd)
        ds_ = [{**asdict(s), "long_day": getattr(s, "_long_day", False)} for s in todo]
        left_out = PP.place(ds_, free, long_wd, PR, notes=notes, long_done=long_done)
        for s, d in zip(todo, ds_):
            if d["day"]:
                put(s, dt.date.fromisoformat(d["day"]))
        if left_out:
            drop_min = sum(s["minutes"] for s in left_out)
            n_ok = len([d for d in free if PR.allowed(d)])
            notes.append({"level": "info", "src": "prefs", "text": f"本週可練的日子只剩 {n_ok} 天，{len(left_out)} 堂課（約 {drop_min} 分鐘）排不進去——不用補，下週照常"})
        avail, keep_rest, main_todo = [], True, []
    else:
        avail = list(free)
        # keep one rest day when there is room
        keep_rest = len(avail) > len(main_todo) + 0
    aet_days = AT.test_days(prefs)            # 課表偏好 aet_test_days; Mon–Fri without prefs too
    for s in sorted(main_todo, key=lambda s: {"long": 0, "test": 1, "quality": 1}.get(s.kind, 2)):
        if not avail:
            break
        if s.kind == "long":
            pick = next((d for d in avail if d.weekday() == long_wd), avail[-1])
        elif s.kind == "test" and aet_days is not None and AT.is_aet_session(asdict(s)):
            long_day = next((dt.date.fromisoformat(x.day) for x in sessions if x.kind == "long" and x.day), None)
            hard_days = [dt.date.fromisoformat(x.day) for x in sessions
                         if x.kind in ("quality", "test") and x.day and x is not s]
            r = AT.pick_day(avail, long_day, hard_days, aet_days, weekend_ok=not AT.is_short(asdict(s)))
            if r["day"] is None:
                notes.append({"level": "info", "text": r["note"]})
                continue
            put(s, r["day"])
            avail.remove(r["day"])
            continue
        elif s.kind in ("quality", "test"):
            long_day = next((dt.date.fromisoformat(x.day) for x in sessions if x.kind == "long" and x.day), None)
            cands = [d for d in avail if long_day is None or abs((d - long_day).days) >= 2]
            if not cands and lost and any(abs((d - long_day).days) <= 1 for d in avail):
                continue          # 不排課日期 left no room: drop it rather than stack two hard days
            pick = (cands or avail)[0]
        else:
            pick = avail[0]
        put(s, pick)
        avail.remove(pick)
    unplaced = [s for s in main_todo if s.day is None]
    if unplaced:
        drop_min = sum(s.minutes for s in unplaced)
        what = "次輕鬆跑" if all(s.kind == "easy" for s in unplaced) else "堂課"
        notes.append({"level": "info", **({"src": "blackout"} if lost else {}),
                      "text": f"本週只剩 {len(free)} 天，{len(unplaced)} {what}（約 {drop_min} 分鐘）排不進去——不用補，下週照常"})
    # strength on easy days (or free days), never the day before the long session
    easy_days = [dt.date.fromisoformat(s.day) for s in main_todo if s.kind == "easy" and s.day]
    long_day = next((dt.date.fromisoformat(s.day) for s in main_todo if s.kind == "long" and s.day), None)
    for s in [s for s in todo if s.kind == "strength"]:
        cands = [d for d in avail + easy_days if long_day is None or d != long_day - dt.timedelta(days=1)]
        cands = [d for d in cands if d.isoformat() not in [x.day for x in sessions if x.kind == "strength" and x.day]]
        if cands:
            d = sorted(cands)[0]
            put(s, d)
            if d in avail:
                avail.remove(d)
    if not keep_rest and free:
        notes.append({"level": "info", "text": "剩下的每一天都排了東西；覺得累就把一次輕鬆跑換成休息"})

    # ---- 熱適應課 (engine/heat_plan.py): only before a hot A/B race ---------
    heat_info = {"active": False}
    try:
        from backend.engine import heat_plan as HP
        sd = [asdict(s) for s in sessions]
        heat_info = HP.apply(sd, events=status.plan.events, today=today, prefs=prefs, aet=aet, mode=mode,
                             kind=kind, notes=notes)
        if heat_info.get("active"):
            sessions = [Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__}) for d in sd]
    except Exception as e:                  # noqa: BLE001 — heat sessions never break the plan
        heat_info = {"active": False, "reason": f"熱適應資料讀取失敗（{type(e).__name__}）"}

    # ---- projection to Sunday -------------------------------------------
    planned_by_day = {}
    for s in sessions:
        if not s.done and s.day:
            planned_by_day[s.day] = planned_by_day.get(s.day, 0.0) + s.tss
    days_after = [(today + dt.timedelta(days=i)) for i in range(1, (sunday - today).days + 1)]
    # today's planned load is added to today's already-computed CTL when nothing is logged yet
    ctl_start, atl_start = ctl_today, atl_today
    if not done_today and today.isoformat() in planned_by_day:
        x = planned_by_day[today.isoformat()]
        c_prev, a_prev = _n(ctl_s.at(tday - 1)) or 0.0, _n(atl_s.at(tday - 1)) or 0.0
        ctl_start = c_prev + (x - c_prev) / cc
        atl_start = a_prev + (x - a_prev) / ac
    proj = project(ctl_start, atl_start, [planned_by_day.get(d.isoformat(), 0.0) for d in days_after], cc, ac)
    proj_rows = [{"date": d.isoformat(), **p} for d, p in zip(days_after, proj)]
    ctl_end = proj_rows[-1]["ctl"] if proj_rows else ctl_start
    atl_end = proj_rows[-1]["atl"] if proj_rows else atl_start

    # ---- non-session to-dos from the indicators --------------------------
    for iid in ("data", "testing"):
        i = by.get(iid)
        if i is not None and i.level in ("bad", "watch") and i.action and not (iid == "testing" and test_s is not None):
            notes.append({"level": i.level, "text": f"{i.title}：{i.action}"})

    mode_label = {"base": "基礎期", "specific": "專項期", "taper": "減量期", "event": "比賽週",
                  "recovery": "恢復期", "transition": "轉換期", "recovery_week": "恢復週"}[mode]
    return {
        "week": {"start": monday.isoformat(), "end": sunday.isoformat(), "today": today.isoformat(),
                 "days_left": len(free)},
        "phase": kind, "mode": mode, "mode_label": mode_label,
        "target": {"hours": hours, "tss": tss_target, "tss_per_hour": r_all},
        "done": {"hours": done_h, "tss": done_tss, "sessions": len(week_ws),
                 "activities": [activity_row(w) for w in sorted(week_ws, key=lambda w: w.day)]},
        "remaining": {"hours": max(0.0, hours - done_h), "tss": max(0.0, tss_target - done_tss)},
        "why": why,
        "rules": [SRC_RAMP, SRC_TEN, SRC_31] if kind in ("base", "specific") else [SRC_BOSQUET if kind == "taper" else SRC_UA],
        "history": [{"start": s.isoformat(), "hours": h, "tss": t} for s, h, t in hist],
        "load": {"ctl_week_start": ctl0, "ctl_today": ctl_today, "atl_today": atl_today,
                 "tsb_today": tsb_today, "ctl_end": ctl_end, "atl_end": atl_end,
                 "tsb_next": ctl_end - atl_end, "ramp": ctl_end - ctl0},
        "projection": proj_rows,
        "sessions": [asdict(s) for s in sorted(sessions, key=lambda s: (s.day or "9999", s.kind))],
        "long_weekday": WEEKDAYS[long_wd],
        "thresholds": {"cp": tt.get("cp"), "cp_source": tt.get("cp_source"), "lthr": tt.get("lthr"),
                       "lthr_source": tt.get("lthr_source"), "aet": aet, "aet_source": tt.get("aet_source")},
        "notes": notes,
        # the quality gate (engine/quality_gate.py), so projection.project_weeks can
        # re-evaluate it for each projected week instead of copying this week's answer
        "quality_gate": {**gate, "levels": gate_levels, "allowed": allow_quality,
                         "this_week": dec["spec"][1] if allow_quality and dec["spec"] and kind == "base"
                         and not (test_s is not None or aet_due) else None,
                         # a suggested test counts as the last one, so the projection waits ≥ 4 weeks
                         "aet_test": {"due": aet_due, "last": today.isoformat() if aet_due else tx.get("aet_last_test")}},
        # per-category TSS / h (projection shapes projected weeks with the same rates)
        "tss_per_category": tph,
        "prefs": PR.to_dict() if PR is not None else None,
        "blackout_days": [d.isoformat() for d in lost],
        "heat": heat_info,
    }
