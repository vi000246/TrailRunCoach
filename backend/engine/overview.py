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

from backend.engine import load_guard as LG
from backend.engine.hr_profile import EASY_CAP_TIP, below, easy_cap_label
from backend.engine.wko5expr.dataset import Dataset, Workout, date_to_day, day_to_date
from backend.engine.wko5expr.evaluator import WS, Evaluator
from backend.i18n import N_, _

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
# a planned Zone 3 variant (90–95 % CP) is done by time at ≥ 85 % CP (≈ 0.95 × its 90 % floor,
# interval-prescription.md §B3) — it never reaches 95 % CP
Z3_EXPR = "sum(if(runpower >= 0.85*cp, deltatime))"


def category(w: Workout) -> str:
    tags, st = set(w.tags), (w.sport_type or "")
    from backend.engine.algorithms.classify import is_trail
    if is_trail(w):
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


def session_of(ds: Dataset, w: Workout) -> dict:
    """The session classifier for one activity (workout_review.classify): {type,
    type_label, stimulus, icon, z3_s, t_vo2_eq_s}; {} when it can't be measured. The two
    doses (Zone 3 seconds, equivalent T@VO2max) grade a planned session's intensity
    (plan_match.dose, SP-216)."""
    from backend.engine import workout_review as WR
    try:
        c = WR.classify(ds, w)
    except Exception:                       # noqa: BLE001 — a row never breaks the overview
        return {}
    out = {k: c.get(k) for k in ("type", "type_label", "stimulus", "icon", "moderate")}
    st = c.get("stim") or {}
    for k in ("z3_s", "t_vo2_eq_s"):
        v = st.get(k)
        out[k] = None if v is None else round(float(v), 1)
    return out


def activity_row(w: Workout, ds: Optional[Dataset] = None) -> dict:
    """One activity for the week / calendar views; with `ds` also its session class
    (`session`: Z5 間歇 / Z3 閾值 / 高強度長跑 / 中強度跑 … and the dashicon)."""
    m = w.metrics
    cat = category(w)
    row = {
        "index": w.idx, "start": w.entry.start.isoformat(), "date": wdate(w).isoformat(),
        "category": cat, "category_label": CATEGORIES[cat][0], "sport_type": w.sport_type,
        "moving_s": moving_s(w), "duration_s": _n(m.get("duration")),
        "distance_km": _n(m.get("distance")), "climbing_m": _n(m.get("climbing")),
        "descending_m": _n(m.get("descending")), "tss": _n(m.get("tss")),
        "if": _n(m.get("if")), "np": _n(m.get("np")), "ep_km": ep_km(w),
    }
    if ds is not None and cat in ("road", "trail", "hike"):
        row["session"] = session_of(ds, w)
    return row


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
    """CTL / ATL / TSB per day over [begin, end] — the chart expressions `ctl`, `atl`,
    `tsb` (TSB = yesterday's CTL − ATL), so the same started PMC (SP-68: manual start,
    else the first 4 weeks' mean, else 0; load_guard.pmc_start) as status / week_plan.
    `start`: where it started {source, date (ISO), ctl, atl}."""
    b, e = int(date_to_day(begin)), int(date_to_day(end))
    ev = Evaluator(ds, b, e)
    ctl, atl, st = ev.pmc()
    tsb = ev.evaluate("tsb")
    tss = daily_tss(ds)
    rows = []
    for d in range(b, e + 1):
        rows.append({"date": day_to_date(d).isoformat(), "tss": tss.get(d, 0.0),
                     "ctl": _n(ctl.at(d)), "atl": _n(atl.at(d)), "tsb": _n(tsb.at(d))})
    return {"begin": begin.isoformat(), "end": end.isoformat(),
            "ctlconstant": ds.athlete.ctlconstant, "atlconstant": ds.athlete.atlconstant,
            "start": {"source": st["source"], "ctl": st["ctl"], "atl": st["atl"],
                      "date": None if st["day"] is None else day_to_date(st["day"]).isoformat()},
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

SRC_RAMP = ("每週 CTL 目標：基礎期 max(2, CTL 的 5%)、專項期 max(2.5, CTL 的 7%)（推估；"
            "Palladino 每週 +1～3、約 2～5% 可長期維持）")
SRC_TEN = ("週量增幅 ≤ 10%：保守做法（推估；系統性回顧找不到「10% 法則」的證據）；受傷風險線 > 20–30%："
           "Nielsen 2014、Damsted 2019（同儕審查）")
SRC_31 = "3:1 週期（三週加量、一週恢復，恢復週減 35%，教練建議減 20–35%；Friel / Uphill Athlete 常見做法，沒有試驗比較過 3:1 和 2:1）"
SRC_RECOVERY_WEEK = ("恢復週保留次數和強度、每堂縮短（挪威教練）；一段加量最多 6 週（Koop）；"
                     "長跑縮到平常的 65%、專項期恢復週排在賽前第 5、3 週為推估")

# ---- 恢復週 (SP-97; periodization-cross-sport.md §4.3, §6.1 SP-97) -------------------------------
# * 專項期: the recovery weeks are counted back from the race (specific_phase.EASY_WEEKS, FRAC's low
#   points 賽前第 5、3 週), so one never lands on the biggest long day (week 4, 90 %); the
#   history-triggered 3:1 (three weeks each ≥ 0.95 × the one before) stays the base phase's.
# * Both phases: after RECOVERY_MAX_GAP weeks without a recovery week the next one is one (Koop: a
#   block is ≤ 6 weeks — 教練級 [211]); a 專項期 week whose next week is a countdown one waits for it,
#   and a countdown week right after a recovery-like week is a normal one (no two easy weeks in a row).
#   A past week counts as a recovery week when it touches a 減量期 / race / 恢復期 / 轉換期 / 回量期 phase
#   (load_guard.STEP_SKIP_KINDS, SP-98's 回量期 included) or its
#   hours are ≤ RECOVERY_DROP × the (up to 3) weeks before it (runners cut 20–35 % [194]: 80 % is
#   the edge of that range, 推估).
# * The TSB < −30 protection is unchanged.
# * The week keeps a shorter long run (RECOVERY_LONG_SHARE of the usual, the ticket's 60–70 %, 推估)
#   and the short fartlek (quality_gate.RECOVERY 4×1′, Palladino) in both phases — the Norwegian
#   coaches keep the sessions and their intensity and shorten each [30]; tapering keeps the
#   intensity too (Wang 2023 [105], indirect).
RECOVERY_MAX_GAP = 6
RECOVERY_DROP = 0.80
RECOVERY_LONG_SHARE = 0.65
RECOVERY_LONG_MIN = 45              # 推估: shorter is just another easy run


def weeks_since_recovery(hours: list, skip: list) -> int:
    """Complete weeks (newest last in `hours`) since the last recovery-like one (SP-97): a week in
    `skip` (it touches a 減量期 / race / 恢復期 / 轉換期 / 回量期 phase, load_guard.skip_mondays) or one ≤ RECOVERY_DROP × the mean of the
    (up to 3) weeks before it. len(hours) when none is found."""
    n = 0
    for i in range(len(hours) - 1, -1, -1):
        prev = [float(h) for h in hours[max(0, i - 3):i]]
        if (i < len(skip) and skip[i]) or (prev and float(hours[i]) <= RECOVERY_DROP * statistics.mean(prev) + 1e-9):
            return n
        n += 1
    return n


def recovery_reason(kind: str, monday: dt.date, race_start: Optional[dt.date], since: int) -> Optional[str]:
    """Why this base / 專項期 week is a recovery week by the calendar (SP-97): the 專項期 countdown
    (specific_phase.EASY_WEEKS) or RECOVERY_MAX_GAP weeks without one (`since`: weeks_since_recovery);
    None = neither (the base phase's 3:1 trigger is checked by the caller). `race_start` = the next A
    race's first day."""
    from backend.engine import specific_phase as SP
    if kind not in ("base", "specific"):
        return None
    w = SP.easy_week(race_start, monday) if kind == "specific" else None
    if w is not None and since >= 1:        # (not two easy weeks in a row: last week was already light)
        return _("專項期賽前第 {w} 週是恢復週（從比賽往回數，對齊長天逐週表的低點；前 3 週平均的 65%）", w=w)
    nxt = kind == "specific" and SP.easy_week(race_start, monday + dt.timedelta(weeks=1)) is not None
    if since >= RECOVERY_MAX_GAP and not nxt:
        return _("已經連續 {n} 週沒有恢復週（Koop：一段加量最多 6 週）：這週是恢復週（前 3 週平均的 65%）", n=since)
    return None


def recovery_long_minutes(usual: float, total_min: float, spec_min: Optional[float] = None) -> float:
    """A recovery week's long run (SP-97): RECOVERY_LONG_SHARE × the usual long run (`usual`, ≥ 60 min),
    ≥ RECOVERY_LONG_MIN; in the 專項期 ≤ `spec_min` (specific_phase.long_minutes at FRAC's low point);
    ≤ half the week. The callers then apply load_guard.cap_long (SP-66), which wins over the floor."""
    m = max(float(RECOVERY_LONG_MIN), RECOVERY_LONG_SHARE * max(float(usual or 0.0), 60.0))
    if spec_min:
        m = max(float(RECOVERY_LONG_MIN), min(m, float(spec_min)))
    return min(m, 0.5 * total_min) if total_min >= 120 else m


def recovery_long(s) -> None:
    """Mark a long run (Session or dict, in place) as the recovery week's shorter one (SP-97)."""
    is_d = isinstance(s, dict)
    det = s.get("detail") if is_d else s.detail
    src = s.get("source") if is_d else s.source
    det = _("恢復週：長跑縮短到平常的 {share:.0%}、全程輕鬆", share=RECOVERY_LONG_SHARE) + ("；" + det if det else "")
    src = (src + "；" if src else "") + SRC_RECOVERY_WEEK
    if is_d:
        s["detail"], s["source"] = det, src
    else:
        s.detail, s.source = det, src


def next_a_start(phases: list, monday: dt.date) -> Optional[dt.date]:
    """The first day of the next A race on / after `monday` (its planning 「event」 phase)."""
    out = None
    for p in phases or ():
        kind = p["kind"] if isinstance(p, dict) else p.kind
        s = dt.date.fromisoformat(str(p["start"] if isinstance(p, dict) else p.start)[:10])
        if kind == "event" and s >= monday and (out is None or s < out):
            out = s
    return out
SRC_BOSQUET = "Bosquet 2007、Wang 2023 統合分析：減量 2 週、量減 41–60%、強度與次數維持"
SRC_UA = "Uphill Athlete"
SRC_KOOP = "Koop《Training Essentials for Ultrarunning》"
SRC_PALLADINO = "Palladino 功率區間"
SRC_PFITZ = "Pfitzinger & Douglas《Advanced Marathoning》"
SRC_DANIELS = "Daniels《Daniels' Running Formula》"
SRC_TRANSITION = "Friel（Transition 3–4 週，for fun rather than fitness）；Canova（4 週輕鬆跑 ≤ 1 小時）"

# ---- 轉換期 (SP-73; coach-schools-zones-periodization.md R5) ---------------------------------
# The volume is a share of the training level before the race (the 4 complete weeks before its
# taper, planning.pre_race_mondays) — not of the last 4 weeks, which hold the taper, the race and
# the recovery and would shrink the 轉換期 week after week. 50 % = the 恢復期's share (推估: no
# school gives a %; Friel 「for fun rather than fitness」). Reduced, not stopped: kayakers who
# stopped lost 10.1 % VO2max vs 4.8 % on reduced training (Garcia-Pallares 2009,
# periodization-cross-sport.md §4.7 [412]) — the direction has a source, the 50 % does not.
# Each easy run ≤ 60 min: Canova's 轉換
# 4 週「輕鬆跑 ≤ 1 小時」. No long run, no interval, no strides; strength ×2 (as before).
TRANSITION_SHARE = 0.5
TRANSITION_RUN_MAX = 60
TRANSITION_OLD_SHARE = 0.65           # no pre-race weeks known (a manual 轉換期 with no race): 65 % of the 4-week mean
TRANSITION_NOTE = N_("轉換期：只排輕鬆跑（每次 ≤ 60 分）和肌力，沒有長跑、強度課；"
                     "想做交叉訓練（騎車、游泳、健行）可以拿來取代輕鬆跑")


def transition_hours(ref_h: Optional[float], base4: float) -> tuple[float, str]:
    """(hours, why) of a 轉換期 week: TRANSITION_SHARE × the pre-race level `ref_h`, else
    (no pre-race weeks) the old TRANSITION_OLD_SHARE × the 4-week mean."""
    if ref_h and ref_h > 0:
        return (TRANSITION_SHARE * ref_h,
                _("轉換期：賽前 4 週平均 {h:.1f} h × {share:.0%}（推估），每次輕鬆跑 ≤ {max} 分",
                  h=ref_h, share=TRANSITION_SHARE, max=TRANSITION_RUN_MAX))
    return TRANSITION_OLD_SHARE * base4, _("轉換期：近 4 週的 {share:.0%}", share=TRANSITION_OLD_SHARE)


def auto_easy_cap(n_easy: int, other_runs: int) -> int:
    """The easy runs that keep the week at ≤ rest_days.AUTO_MAX_RUNS runs (SP-82: at least one rest day; the
    minutes go to the others), never below 1 when there were any. 課表偏好 每週跑步次數 is plan_prefs.shape's."""
    from backend.engine.rest_days import AUTO_MAX_RUNS
    return min(n_easy, max(1, AUTO_MAX_RUNS - other_runs)) if n_easy > 0 else 0


def easy_count(left: float, kind: str) -> int:
    """How many easy runs fill `left` minutes: ~50 min each (1–5); in the 轉換期 / 回量期 each ≤
    TRANSITION_RUN_MAX (1–6); in the 恢復期 each ~REC_SHORT_MIN (1–5, SP-98)."""
    if left < 25:
        return 0
    if kind in ("transition", "rebuild"):
        return max(1, min(6, math.ceil(left / TRANSITION_RUN_MAX - 1e-9)))
    if kind == "recovery":
        from backend.engine.planning import REC_SHORT_MIN
        return max(1, min(5, math.ceil(left / REC_SHORT_MIN - 1e-9)))
    return max(1, min(5, int(round(left / 50.0))))


def cap_transition_runs(ss: list, notes: Optional[list] = None) -> int:
    """The 轉換期's Canova cap after the 課表偏好 shaping (few runs a week make long easy runs):
    every run > TRANSITION_RUN_MAX is cut to it (TSS pro rata); a note says how much went.
    `ss`: Session objects or dicts. Returns the minutes cut."""
    cut = 0
    for s in ss:
        get = (lambda k, s=s: s.get(k)) if isinstance(s, dict) else (lambda k, s=s: getattr(s, k))
        if get("kind") not in ("easy", "long", "hike") or get("done") or (get("minutes") or 0) <= TRANSITION_RUN_MAX:
            continue
        m = int(get("minutes"))
        tss = float(get("tss") or 0.0) * TRANSITION_RUN_MAX / m
        if isinstance(s, dict):
            s["minutes"], s["tss"] = TRANSITION_RUN_MAX, round(tss, 1)
        else:
            s.minutes, s.tss = TRANSITION_RUN_MAX, round(tss, 1)
        cut += m - TRANSITION_RUN_MAX
    if cut and notes is not None:
        notes.append({"level": "info", "src": "transition",
                      "text": _("轉換期每次跑步 ≤ {max} 分（Canova）：本週少排 {cut} 分，不用補",
                                max=TRANSITION_RUN_MAX, cut=cut)})
    return cut


# ---- 恢復期 / 回量期 (SP-98; planning.recovery_plan) -----------------------------------------------
# The 恢復期 REC_SHARE and the 回量期 REBUILD_SHARES of the same pre-race level as the 轉換期; the
# 回量期 like the 轉換期 (easy runs ≤ TRANSITION_RUN_MAX and strength, no long run, no intensity —
# Koop [434], Uphill Athlete [441], 教練級).
REBUILD_NOTE = N_("回量期：只排輕鬆跑（每次 ≤ 60 分）和肌力，不排長跑、強度課；量照減量期倒過來慢慢加回去"
                  "（Higdon 反向減量；不排強度：Koop、Uphill Athlete，教練級）")


def recovery_hours(ref_h: Optional[float], base4: float) -> tuple[float, str]:
    """(hours, why) of a 恢復期 week (SP-98): REC_SHARE × the pre-race level `ref_h`, else (no
    pre-race weeks) the old 50 % of the 4-week mean."""
    from backend.engine.planning import REC_SHARE
    if ref_h and ref_h > 0:
        return (REC_SHARE * ref_h,
                _("恢復期：賽前 4 週平均 {h:.1f} h × {share:.0%}（反向減量的第一段，比例推估）", h=ref_h, share=REC_SHARE))
    return 0.5 * base4, _("恢復期：近 4 週的 {share:.0%}", share=0.5)


def rebuild_hours(ref_h: Optional[float], base4: float, share: float, i: int, n: int) -> tuple[float, str]:
    """(hours, why) of the 回量期's week `i` of `n` (SP-98): `share` × the pre-race level, else × the
    4-week mean."""
    if ref_h and ref_h > 0:
        return (share * ref_h,
                _("回量期第 {i}/{n} 週：賽前 4 週平均 {h:.1f} h × {share:.0%}（照減量期倒過來，比例推估），不排強度課",
                  i=i + 1, n=n, h=ref_h, share=share))
    return share * base4, _("回量期第 {i}/{n} 週：近 4 週的 {share:.0%}，不排強度課", i=i + 1, n=n, share=share)


def inter_cap(phases: list, monday: dt.date, hours: float, why: list, hours_of) -> float:
    """A 專項期 week between two A races < 12 weeks apart (planning.intermediate, SP-95): `hours` capped
    at the share × the first race's pre-taper level (transition_ref), with a `why` line."""
    from backend.engine import planning as P
    it = P.intermediate(phases, monday)
    if not it:
        return hours
    ref = transition_ref(phases, monday, hours_of).get("hours")
    if not ref or hours <= it["share"] * ref:
        return hours
    why.append(_("中間訓練：兩場 A 賽相隔 {span}，這週最多練到上一場賽前 4 週平均 {h:.1f} h 的 {share:.0%}"
                 "（Higdon 等教練級，比例推估）", span=P._span(it["gap"]), h=ref, share=it["share"]))
    return it["share"] * ref


def transition_ref(phases: list, day: dt.date, hours_of) -> dict:
    """The pre-race level of the 轉換期 around `day`: {"mondays": [ISO], "hours": mean or None}.
    `hours_of(monday)` → the week's hours, None when not known (the week isn't past / projected)."""
    from backend.engine.planning import pre_race_mondays
    mons = pre_race_mondays(phases, day)
    hs = [hours_of(m) for m in mons]
    known = [h for h in hs if h is not None]
    return {"mondays": [m.isoformat() for m in mons],
            "hours": statistics.mean(known) if known and len(known) == len(hs) else None}
# 主要訓練項目 = 路跑 (engine/primary_sport.py): the 專項期 long run carries a marathon-pace (MP)
# segment — Pfitzinger's MP long runs (8–18 mi at MP) and Daniels' M runs. Its share of the
# long run and its bounds are 推估 (no published single rule).
MP_SHARE, MP_MIN, MP_MAX = 0.40, 20, 75
# SP-75 (specific-phase-progression.md §4.2, owner 2026-10-05): the MP segment grows week by week and
# comes every other week — share of the long run by 賽前第 n 週, None = an all-easy long run. Pfitzinger's
# MP long runs lengthen (8 → 10 → 14 mi, 二手); Cairess' longest MP block 3 → 6 → 10 km (已驗證, elite);
# Haugen 2022: race-pace volume grows toward the race. The easy weeks sit on the long run's step-back
# weeks (賽前第 5、3 週, the 專項期's recovery weeks) and week 7; the last MP long run is week 4, with the
# race simulation. The shares are 推估. A road race ≤ MP_SHORT_KM keeps its long runs easy.
MP_PLAN = {10: 0.20, 9: 0.25, 8: 0.30, 7: None, 6: 0.35, 5: None, 4: 0.40, 3: None}
MP_SHORT_KM = 10.0
MP_TSS_PER_HOUR = 70.0                 # 推估: MP sits around 80–85 % of threshold
MP_GOAL_MIN_KM = 30.0                  # the A race's goal pace is the MP target only for a marathon-like race


def mp_goal_pace(events, today: dt.date) -> Optional[float]:
    """The next A 路跑賽's goal pace (s/km = 預估移動時間 ÷ distance) when it is ≥ MP_GOAL_MIN_KM;
    None = no goal (the MP segment then follows the threshold pace)."""
    ahead = sorted((e for e in events or () if getattr(e, "priority", "A") == "A" and e.kind == "road"
                    and e.start >= today), key=lambda e: e.start)
    e = ahead[0] if ahead else None
    if e is None or not e.est_hours or not e.distance_km or e.distance_km < MP_GOAL_MIN_KM:
        return None
    return e.est_hours * 3600.0 / e.distance_km


def _mmss(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"

# the weekly CTL goal: load_guard.ramp_goal(kind, CTL) = base max(2, 5 %), specific max(2.5, 7 %) (SP-63, 推估)
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
    # the interval library (engine/interval_library.py; interval-prescription.md §C5.4): the
    # variant, the ladder step it serves, whether it counts for progression, who chose it
    # (auto / user / cap) and why; fewer reps, the warm-up level, the state machine's tweak
    variant_key: Optional[str] = None
    rung_key: Optional[str] = None
    equiv: Optional[bool] = None
    swap: Optional[str] = None
    swap_reason: Optional[str] = None
    variant_reps: Optional[int] = None
    variant_blocks: Optional[str] = None
    variant_adj: Optional[dict] = None
    progress: Optional[bool] = None     # not stored: this week's pick moves the projected ladder
    prefer_days: Optional[list] = None  # not stored: weekdays the cap rule moved it to (plan_prefs.place)
    # a generated structure (engine/workout_steps.py doc; SP-74 技術地形: time + climb + RPE) —
    # reconcile stores it on the unedited auto row, the push uses it instead of the text
    steps: Optional[dict] = None


def _week_hours(ds: Dataset, monday: dt.date) -> tuple[float, float]:
    ws = workouts_between(ds, monday, monday + dt.timedelta(days=7))
    return (sum(moving_s(w) for w in ws) / 3600.0,
            sum(_n(w.metrics.get("tss")) or 0.0 for w in ws))


def _normal_weeks(ds: Dataset, status, monday: dt.date, hours4: list) -> list[float]:
    """The hours of the last 4 normal weeks before `monday` (load_guard.normal_weeks: no 減量期 /
    race week / post-race 恢復期 / 轉換期, up to STEP_LOOKBACK_WEEKS back; SP-73) — `hours4`
    (the last 4 weeks) when none of them is skipped or without a plan."""
    mons = [monday - dt.timedelta(weeks=i) for i in range(LG.STEP_LOOKBACK_WEEKS, 0, -1)]
    skip = LG.skip_mondays(getattr(status, "plan", None), mons)
    if not skip & set(mons[-LG.STEP_AVG_WEEKS:]):
        return list(hours4)
    return LG.normal_weeks([(m, _week_hours(ds, m)[0]) for m in mons], skip)


def _transition_ref(ds: Dataset, status, today: dt.date, monday: dt.date) -> dict:
    """transition_ref for the 轉換期 that holds `today` or comes next (within the planning
    window): its race's pre-race weeks read from the activities when they are all past, else
    hours None (the projection fills them from its projected weeks). {} without a 轉換期."""
    plan = getattr(status, "plan", None)
    if plan is None:
        return {}
    try:
        from backend.engine import planning as P
        phs = P.phases(plan, today - dt.timedelta(days=400), today + dt.timedelta(days=400))
    except Exception:                       # noqa: BLE001 — the plan must still build
        return {}
    # any post-race phase (恢復期 / 轉換期 / 回量期, SP-98): they share the race's pre-race weeks
    tr = next((p for p in phs if p.kind in P.POST_RACE_KINDS and P._d(p.end) >= monday), None)
    if tr is None:
        return {}
    return transition_ref(phs, P._d(tr.start), lambda m: _week_hours(ds, m)[0] if m < monday else None)


RUN_KINDS = ("easy", "long", "quality", "test", "hike")     # the sessions that are runs (not strength)


def _plan_phases(status, monday: dt.date) -> list:
    """planning.phases around `monday` for a status with a plan; [] without one / on any error."""
    plan = getattr(status, "plan", None)
    if plan is None:
        return []
    try:
        from backend.engine import planning as P
        return P.phases(plan, monday - dt.timedelta(days=400), monday + dt.timedelta(days=400))
    except Exception:                       # noqa: BLE001 — the plan must still build
        return []


def _pre_taper(ds: Dataset, t0: dt.date, monday: dt.date) -> dict:
    """The level before a 減量期 starting `t0` (SP-96): the mean runs, climb (m) and hours a week over
    the 4 complete weeks before the taper's week, of those already past `monday`; {} without any."""
    m0 = t0 - dt.timedelta(days=t0.weekday())
    mons = [m0 - dt.timedelta(weeks=k) for k in range(4, 0, -1) if m0 - dt.timedelta(weeks=k) < monday]
    if not mons:
        return {}
    runs, climb, hours = [], [], []
    for m in mons:
        ws = [w for w in workouts_between(ds, m, m + dt.timedelta(days=7)) if category(w) in ("road", "trail")]
        runs.append(len(ws))
        climb.append(sum(_n(w.metrics.get("climbing")) or 0.0 for w in ws))
        hours.append(_week_hours(ds, m)[0])
    return {"runs": statistics.mean(runs), "climb": statistics.mean(climb), "hours": statistics.mean(hours)}


def _week_phase_notes(status, monday: dt.date) -> list[tuple[str, str]]:
    """planning.week_phase_notes of the week (the phase holding Monday and the ones starting later
    that week); a status without a plan: the current phase's own note."""
    from backend.engine import planning as P
    plan = getattr(status, "plan", None)
    if plan is not None:
        try:
            return P.week_phase_notes(P.phases(plan, monday - dt.timedelta(days=400), monday + dt.timedelta(days=400)),
                                      monday)
        except Exception:                   # noqa: BLE001 — the plan must still build
            pass
    ph = getattr(status, "phase", None)
    note = getattr(ph, "note", "") or ""
    return [(ph.kind, t) for t in note.split("；") if t] if ph is not None else []


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


def _hard_seconds(ds: Dataset, ws: list[Workout], b: int, e: int, exprs=None) -> dict[int, float]:
    if not ws:
        return {}
    ev = Evaluator(ds, b, e)
    best: dict[int, float] = {}
    keep = {w.idx for w in ws}
    for expr in exprs or HARD_EXPRS.values():
        r = ev.evaluate(f"athleterange({b}, {e}, {expr})")
        if isinstance(r, WS):
            for i, v in r.items():
                if i in keep and v == v:
                    best[i] = max(best.get(i, 0.0), float(v))
    return best


def _gate_session(gate: dict, dec: dict, th: dict, hours: Optional[float], prefs=None,
                  history=None, mountain: bool = False, cap: Optional[float] = None,
                  alt_caps: Optional[list] = None, notes: Optional[list] = None) -> dict:
    """The base-phase interval for one of the gate's decision items (engine/quality_gate.py
    week_decision: {"track", "spec", "adjust", "first"}). A ladder step is a library variant
    (engine/interval_library.fit): the standard full-length session when the day's `cap` (課表偏好
    weekday cap; None = no cap) allows it, else an equivalent shorter one / fewer reps / another
    day / the step before (§C5.3). A Zone 3 track rung whose time in zone is over the week's
    Zone 3 budget (quality_gate.z3_budget_min: 10 % of `hours`, 5 % for the first session) becomes
    the 巡航版 T1–T3 (quality_gate.cruise_for) — it still counts as the rung's session, and a note
    says why (`notes`). `history`: the stored variants done before this week (rotation).
    `dec["hill"]` (SP-75, the 越野 專項期): the rung's uphill version (interval_library.fit `hill`).
    Recovery fartlek / sub / Zone 3 keep the old builder."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    spec = dec["spec"]
    track = dec.get("track")
    pre = "" if spec is QG.RECOVERY else QG.prefix(gate)
    dose = gate.get("dose") or {}
    if isinstance(dose.get(track), dict):
        dose = dose[track]                      # the track's own state machine (SP-31)
    if dose.get("faded") and spec not in (QG.RECOVERY, QG.SUB):
        # the progression state machine's verdict (quality_gate.dose_step)
        pre = (dose.get("note") or "上次間歇沒有達標：") + pre
    lthr_default = bool((gate.get("lthr") or {}).get("default"))
    tth = {"cp": th.get("cp"), "lthr": th.get("lthr"), "aet": th.get("aet")}
    if spec is QG.SUB:
        # a ramp week (CTL ramp at load_guard's watch line): T1's content, never a ladder step (neutral)
        f = IL.fit("z3a", cap, (), prefs, mountain)
        s = IL.session_for({**f, "equiv": False, "progress": False, "rung": "sub",
                            "reason": "CTL 每週 ≥ +5（Friel）：本週只排閾值，不算進階"}, tth, pre, lthr_default, prefs)
        s["title"] = QG.SUB[1]
        s["source"] = QG.source(gate, spec)
        return s
    rung = spec[0]
    budget = QG.z3_budget_min(hours, bool(dec.get("first"))) if rung in IL.Z3_TRACK else None
    what = f"週量的 {'5%，3 區第一堂' if dec.get('first') else '10%'}"
    if budget is not None and dec.get("budget") is not None and dec["budget"] < budget:
        # the week's interval total (quality_sessions: Zone 3 + Zone 5 ≤ 20 %) leaves less
        budget, what = float(dec["budget"]), f"間歇總量 ≤ 週量 {QG.QUALITY_SHARE_MAX:.0%} 扣掉 5 區"
    canon = IL.canonical(rung) if rung in IL.LIBRARY else None
    if budget is not None and canon is not None and IL.tiz_s(canon) / 60.0 > budget + 1e-6:
        # the week's Zone 3 cap (Daniels ≤ 10 %; UA ~5 % to start): the 巡航版 of the same position
        cr = QG.cruise_for(rung, budget)
        f = IL.fit(cr, cap, history or (), prefs, mountain, alt_caps, hill=bool(dec.get("hill")))
        counts = f["action"] in ("ok", "move") and bool(f["equiv"])
        why = (f"本週 {hours:.1f} h：3 區上限 {budget:.0f} 分（{what}）"
               f"< {IL.RUNG_NAME[rung]} {IL.structure(canon)} 的 {IL.tiz_s(canon) / 60:.0f} 分 → 巡航版 "
               f"{IL.structure(f['variant'])}" + ("（算這一階）" if counts else ""))
        s = IL.session_for({**f, "rung": rung if counts else f.get("rung"), "equiv": counts, "progress": counts,
                            "reason": f"{why}；{f['reason']}"}, tth, pre, lthr_default, prefs, swap="cap")
        if f["action"] == "move" and f.get("move_wd") is not None:
            s["prefer_days"] = [f["move_wd"]]
        s["source"] = QG.source(gate, (f"{s['source']}；{QG.SRC_Z3['volume']}",))
        if notes is not None:
            notes.append({"level": "info", "src": "z3", "text": why + "（Daniels：T ≤ 週量 10%；UA：起步約 5%）"})
        return s
    if rung in IL.LIBRARY:
        f = IL.fit(rung, cap, history or (), prefs, mountain, alt_caps, dec.get("adjust"), hill=bool(dec.get("hill")))
        if rung in IL.Z3_TRACK and cap is not None and (f["action"] == "back" or not f["equiv"]):
            # the day's cap can't fit the Zone 3 rung (no equivalent, no other day): the 巡航版 of the
            # same position that fits — it counts as the rung when 達標, the same rule as the volume
            # cap above (owner 2026-10-04: symmetric); else fit's own fallback (縮量版 / the step before)
            fc = _cruise_for_cap(rung, cap, history, prefs, mountain)
            if fc is not None:
                why = (f"平日上限 {cap:.0f} 分放不下 {IL.RUNG_NAME[rung]} {IL.structure(canon)}"
                       f"（需要 {f.get('need_min') or 0:.0f} 分）→ 巡航版 {IL.structure(fc['variant'])}（算這一階）")
                s = IL.session_for({**fc, "rung": rung, "equiv": True, "progress": True, "action": "ok",
                                    "reason": f"{why}；{fc['reason']}"}, tth, pre, lthr_default, prefs, swap="cap")
                s["source"] = QG.source(gate, (s["source"],))
                if notes is not None:
                    notes.append({"level": "info", "src": "z3", "text": why})
                return s
        by_cap = f["level"] != "full" or f["action"] != "ok" or f.get("reps") is not None
        s = IL.session_for(f, tth, pre, lthr_default, prefs, swap="cap" if by_cap else "auto")
        if f["action"] == "move" and f.get("move_wd") is not None:
            s["prefer_days"] = [f["move_wd"]]
        s["source"] = QG.source(gate, (s["source"],))
        return s
    s = QG.session(spec, tth, pre, hours, lthr_default)
    s["source"] = QG.source(gate, spec)
    return s


def _cruise_for_cap(rung: str, cap: Optional[float], history=None, prefs=None, mountain: bool = False) -> Optional[dict]:
    """The 巡航版 for a Zone 3 track rung the day's cap can't fit: the cruise rung of the same position
    (quality_gate.cruise_for's order: A1 → T1, A2 → T2, A3 / A4 → T3), stepping down until
    interval_library.fit gives a standard or equivalent session within `cap`; None when none fits."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    i = min(IL.Z3_TRACK.index(rung) if rung in IL.Z3_TRACK else 0, len(QG.CRUISE) - 1)
    for j in range(i, -1, -1):
        f = IL.fit(QG.CRUISE[j][0], cap, history or (), prefs, mountain)
        if f["action"] == "ok" and f["equiv"]:
            return f
    return None


def quality_sessions(gate: dict, dec: dict, kind: str, th: dict, tgt: dict, hours: Optional[float], prefs=None,
                     history=None, mountain: bool = False, road: bool = False, cap: Optional[float] = None,
                     alt_caps: Optional[list] = None, notes: Optional[list] = None,
                     reserved: float = 0.0) -> list[dict]:
    """The week's interval sessions (Session kwargs, ids quality / quality2) for week_decision's
    items (SP-31: one per track). Base: the track's ladder rung (_gate_session). 專項期 (SP-75): the
    ladder too — the old fixed sessions (road 2×15′, trail 5×4′ uphill) stopped the ladder (a road
    runner at 2×20′ went back to 2×15′, a trail runner at V1 jumped to 20′ of Zone 5, over the
    10–16′ of a Zone 5 session) — 越野 the rung's uphill version (Koop: ~80 % of intervals uphill;
    specific-phase-progression.md §4.4), 路跑 on the flat; the race's ratio by week_decision. 減量期: Zone 3 =
    TAPER_Z3, Zone 5 = TAPER_Q 有氧間歇（巡航）4×3′ (98–102 % CP) — which a taper week also keeps
    when neither track is open (the session predates the gates). Shared by week_plan and projection.
    `reserved`: minutes of the week's 20 % already taken by the user's own RPE ≥ 7 技術地形
    sessions (technical.user_quality, SP-74): the intervals get the rest — shortened, or left out
    when nothing is left (a note says so)."""
    from backend.engine import quality_gate as QG
    items = dec.get("items") or []
    if kind == "taper" and not items:
        items = [{"track": None}]
    # the week's interval total (SP-31, 推估: Seiler 80/20, Koop): Zone 3 + Zone 5 time in zone ≤
    # QUALITY_SHARE_MAX of the planned running time — Zone 5 first, Zone 3 gets what is left
    total = QG.QUALITY_SHARE_MAX * hours * 60.0 if hours and kind in ("base", "specific") else None
    if total is not None and reserved:
        total = max(0.0, total - reserved)          # the user's own RPE ≥ 7 sessions first (SP-74)
    built: dict = {}
    for i in sorted(range(len(items)), key=lambda k: 0 if items[k].get("track") == "z5" else 1):
        it = items[i]
        t = it.get("track")
        used = sum(session_tiz_min(x) for x in built.values())
        left = None if total is None else max(0.0, total - used)
        if reserved and left is not None and left < 1.0:
            _reserved_out(notes, reserved, left, t)
            continue
        if it.get("cruise"):
            s = _second_z3(gate, built, it, th, hours, prefs, history, mountain, cap, left, notes)
            if s is None:
                continue
        elif kind == "specific" and it.get("spec") is QG.SUB:
            # 專項期 under a CTL ramp at the watch line (week_decision): the threshold-only session, as in the base phase
            s = _gate_session(gate, it, th, hours, prefs, history, mountain, cap, alt_caps, notes)
        elif kind == "taper":
            s = dict(TAPER_Z3 if t == "z3" else TAPER_Q, target=tgt.get("threshold", ""))
        else:
            it = {**it, "hill": kind == "specific" and not road}        # SP-75: 越野 專項期 = the uphill version
            s = _gate_session(gate, {**it, "budget": left} if t == "z3" else it, th, hours, prefs, history, mountain,
                              cap, alt_caps, notes)
        if left is not None and session_tiz_min(s) > left + 1e-6:
            if reserved:
                # the user's own sessions came first: even the 縮量版 floor must fit, else none
                short = _shorten(s, left, total, hours, th, gate, prefs, None)
                if session_tiz_min(short) > left + 1e-6:
                    _reserved_out(notes, reserved, left, t)
                    continue
            s = _shorten(s, left, total, hours, th, gate, prefs, notes)
        built[i] = s
    out = []
    for i in range(len(items)):
        if i not in built:
            continue                            # the second Zone 3 session didn't fit (a note says so)
        s = built[i]
        s["id"] = "quality" if not out else f"quality{len(out) + 1}"
        out.append(s)
    return out


def _reserved_out(notes: Optional[list], reserved: float, left: float, track: Optional[str]) -> None:
    """The note for an interval left out because the user's own RPE ≥ 7 技術地形 sessions took
    the week's 20 % (quality_sessions `reserved`)."""
    from backend.engine import quality_gate as QG
    if notes is not None:
        notes.append({"level": "info", "src": "quality_share",
                      "text": _("你排的技術地形課（RPE ≥ 7）主課 {reserved:.0f} 分算進本週強度預算"
                                "（週量 {share:.0%}，80/20；推估），只剩 {left:.0f} 分：這週不排{zone}間歇",
                                reserved=reserved, share=QG.QUALITY_SHARE_MAX, left=max(0.0, left),
                                zone=_(" 5 區") if track == "z5" else _(" 3 區") if track == "z3" else "")})


CRUISE_REP_MIN_S = 6 * 60   # 巡航 = reps of 6–15 min (coach-schools-zones-periodization.md R1: Friel 6–12′, Daniels
                            # cruise 3–15′, the Norwegian 5×6′) — shorter reps aren't the second Zone 3 session


def _second_z3(gate: dict, built: dict, it: dict, th: dict, hours: Optional[float], prefs=None, history=None,
               mountain: bool = False, cap: Optional[float] = None, left: Optional[float] = None,
               notes: Optional[list] = None) -> Optional[dict]:
    """The week's second Zone 3 session while Zone 5 is closed (課表偏好 2 a week; owner 2026-10-04):
    a 巡航版 interval (a T1–T3 library row or fewer of its reps, floor interval_library.MIN_REPS;
    quality_gate.week_decision's `cruise` item) as close to the first one's time in zone as the
    Zone 3 cap (quality_gate.z3_budget_min: 10 % of the week for both, Daniels), the week's
    interval total (`left`) and the day's cap allow, never the first one's structure. None — with
    a note — when nothing fits. It doesn't move the rung (its 達標 counts in `met`)."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    first = next((x for x in built.values()), None)
    want = session_tiz_min(first) if first else IL.tiz_s(IL.canonical(it["spec"][0])) / 60.0
    room = [want]
    z3cap = QG.z3_budget_min(hours)
    if z3cap is not None:
        room.append(z3cap - sum(session_tiz_min(x) for x in built.values()))
    if left is not None:
        room.append(left)
    budget = min(room)
    # the 巡航版 intervals (T1–T3 rows of the library with ≥ 2 reps of ≥ 6 min, and fewer of their reps down to
    # MIN_REPS): the largest time in zone within the budget and the day's cap, not the first session's
    # structure; on a tie the rung of the first one's position (quality_gate.cruise_for)
    pos = QG.cruise_for(first.get("rung_key") if first and first.get("rung_key") in IL.Z3_TRACK else "a1", None)
    mine = (IL.structure(IL.resolve(first["variant_key"], first.get("variant_reps"))) if first and
            first.get("variant_key") else None)
    best = None
    for r in IL.CRUISE_RUNGS:
        for base in IL.LIBRARY.get(r, ()):
            if base.n < 2 or base.sets > 1 or min(base.works) < CRUISE_REP_MIN_S:
                continue
            for n in range(base.n, IL.MIN_REPS.get(base.cls, 2) - 1, -1):
                v = IL.with_reps(base, n)
                t = IL.tiz_s(v) / 60.0
                if t > budget + 1e-6 or IL.structure(v) == mine or \
                        (cap is not None and IL.total_min(v, "min", prefs) > cap + 1e-6):
                    continue
                key = (t, r == pos, base.canonical, n == base.n)
                if best is None or key > best[0]:
                    best = (key, r, base, n, v)
    fits = best is not None
    if fits:
        _, rung, canon, n, v = best
    head = f"每週 2 堂、5 區還沒開：第二堂排不同的 3 區課（巡航版），不重複「{first['title']}」" if first else ""
    if not fits:
        if notes is not None:
            notes.append({"level": "info", "src": "z3",
                          "text": f"{head}——但 3 區每週上限（週量 10%，Daniels）／間歇總量（20%）或平日上限放不下"
                                  f"（剩 {max(0.0, budget):.0f} 分）：本週排 1 堂"})
        return None
    why = (f"{head}：{IL.RUNG_NAME[rung]} {IL.structure(v)}，目標區 {IL.tiz_s(v) / 60:.0f} 分"
           f"（第一堂 {want:.0f} 分；3 區合計 ≤ 週量 10%）")
    f = {"variant": v, "level": "min" if cap is not None else "std", "reps": n if n != canon.n else None,
         "equiv": True, "progress": False, "reason": why, "action": "ok", "rung": rung, "base": canon}
    lthr_default = bool((gate.get("lthr") or {}).get("default"))
    s = IL.session_for(f, {"cp": th.get("cp"), "lthr": th.get("lthr"), "aet": th.get("aet")}, QG.prefix(gate),
                       lthr_default, prefs, swap="cap")
    s["source"] = QG.source(gate, (f"{s['source']}；{QG.SRC_Z3['volume']}",))
    if notes is not None:
        notes.append({"level": "info", "src": "z3", "text": why})
    return s


def session_tiz_min(s: dict) -> float:
    """Planned time in zone (minutes) of an interval session: its library variant's Σ work, else
    the title's 「N×M 分」 / 「連續 M 分」; 0 when unknown (the recovery fartlek, a test)."""
    import re
    from backend.engine import interval_library as IL
    if s.get("variant_key"):
        v = IL.resolve(s["variant_key"], s.get("variant_reps"), s.get("variant_adj"))
        if v is not None:
            return IL.tiz_s(v) / 60.0
    m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*分", s.get("title") or "")
    if m:
        return int(m.group(1)) * int(m.group(2)) * 1.0
    m = re.search(r"連續\s*(\d+)\s*分", s.get("title") or "")
    return float(m.group(1)) if m else 0.0


def _shorten(s: dict, left: float, total: float, hours: float, th: dict, gate: dict, prefs=None,
             notes: Optional[list] = None) -> dict:
    """An interval over the week's interval total (QUALITY_SHARE_MAX): fewer reps of its variant
    (floor interval_library.MIN_REPS) as a 縮量版 — it doesn't move the rung — or, when it can't be
    cut (a fixed / continuous session), kept as it is; a note says so either way (never dropped)."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    v = IL.resolve(s.get("variant_key"), s.get("variant_reps"), s.get("variant_adj")) if s.get("variant_key") else None
    head = (f"本週 {hours:.1f} h：間歇總量上限 {total:.0f} 分（週量 {QG.QUALITY_SHARE_MAX:.0%}，80/20；推估）"
            f"，{s['title']} 的 {session_tiz_min(s):.0f} 分放不下")
    if v is None or v.n <= 1 or v.sets > 1:
        if notes is not None:
            notes.append({"level": "info", "src": "quality_share", "text": head + "：照排，這週其他輕鬆跑別加速"})
        return s
    floor = IL.MIN_REPS.get(v.cls, 2)
    n = max(floor, min(v.n - 1, int(left * 60 // max(1, max(v.works)))))
    r = IL.with_reps(v, n)
    why = f"{head} → 減成 {n} 趟（縮量版，不算進階）"
    out = IL.session_for({"variant": r, "level": s.get("variant_blocks") or "std", "reps": n, "equiv": False,
                          "progress": False, "reason": why, "rung": s.get("rung_key")},
                         {"cp": th.get("cp"), "lthr": th.get("lthr"), "aet": th.get("aet")}, "",
                         bool((gate.get("lthr") or {}).get("default")), prefs, swap="cap")
    out["source"] = f"{s.get('source') or ''}；{QG.SRC_Z3['share']}"
    if notes is not None:
        notes.append({"level": "info", "src": "quality_share", "text": why})
    return out


def variant_history(before: dt.date, gate: Optional[dict] = None) -> list[dict]:
    """The stored quality sessions with a library variant done before `before`
    (plan_store.variant_rows, read-only), each with its outcome from the gate's
    dose history (the activity it was done by) — interval_library.fit's rotation."""
    try:
        from backend.engine.plan_store import variant_rows
        rows = variant_rows()
    except Exception:                       # noqa: BLE001
        return []
    out_by_idx = {h.get("idx"): h.get("outcome") for h in ((gate or {}).get("dose") or {}).get("history") or []}
    out = []
    for r in rows:
        if r.get("state") != "done" or not r.get("day") or r["day"] >= before.isoformat():
            continue
        idx = (r.get("done_by") or {}).get("index")
        out.append({"day": r["day"], "rung_key": r.get("rung_key"), "variant_key": r.get("variant_key"),
                    "state": "done", "outcome": out_by_idx.get(idx), "swap": r.get("swap")})
    return out


def quality_per_week(PR, kind: str, mode: str, gate: Optional[dict]) -> int:
    """Intervals a week for week_decision: 2 when 課表偏好 每週品質課 = 2 (base / 專項期, not a recovery
    week, not the base phase's guardrail mode — quality_gate.guardrail_mode caps it at 1), else 1."""
    from backend.engine import quality_gate as QG
    if PR is None or getattr(PR, "quality", None) != 2 or mode == "recovery_week" or kind not in ("base", "specific"):
        return 1
    return 1 if kind == "base" and QG.guardrail_mode(gate) else 2


def quality_caps(prefs, long_wd: int) -> tuple[Optional[float], list]:
    """(the weekday cap, [(label, cap, weekday)] of other days with a bigger cap) for the
    interval session (§C5.3-4). 平日 = Mon–Fri; a weekend day takes the long-day cap
    and is a candidate only ≥ 2 days from the long run (48 h)."""
    if prefs is None or not getattr(prefs, "active", False) or prefs.cap_weekday is None:
        return None, []
    alt = []
    for wd, label in ((5, "週六"), (6, "週日")):
        if wd == long_wd or not prefs.days[wd] or min(abs(wd - long_wd), 7 - abs(wd - long_wd)) < 2:
            continue
        c = prefs.long_cap
        if c is None or c > prefs.cap_weekday:
            alt.append((label, c, wd))
    return float(prefs.cap_weekday), alt


def mp_minutes(long_min: float, share: float = MP_SHARE) -> int:
    """The marathon-pace segment (minutes) of a road 專項期 long run (`share`, 推估)."""
    return int(round(max(MP_MIN, min(MP_MAX, share * long_min)) / 5.0) * 5)


def mp_race(events, today: dt.date) -> Optional[dict]:
    """The next A race the MP plan counts back from: {"start", "short" (a road race ≤ MP_SHORT_KM)};
    None without one (the MP segment then stays MP_SHARE every week, as before SP-75)."""
    ahead = sorted((e for e in events or () if getattr(e, "priority", "A") == "A"
                    and getattr(e, "kind", "") in ("race", "road", "baiyue") and e.start >= today), key=lambda e: e.start)
    if not ahead:
        return None
    e = ahead[0]
    return {"start": e.start.isoformat(),
            "short": e.kind == "road" and bool(e.distance_km) and float(e.distance_km) <= MP_SHORT_KM}


def mp_week(race: Optional[dict], monday: dt.date) -> Optional[dict]:
    """{"weeks_out", "share" (None = all easy), "short"} of the week of `monday` (MP_PLAN, SP-75); None
    without a race. Weeks before 賽前第 10 週 take its share, weeks after 3 none."""
    if not race or not race.get("start"):
        return None
    w = -(-(dt.date.fromisoformat(str(race["start"])[:10]) - monday).days // 7)
    share = None if race.get("short") or w < min(MP_PLAN) else MP_PLAN.get(w, MP_PLAN[max(MP_PLAN)])
    return {"weeks_out": w, "share": share, "short": bool(race.get("short"))}


def road_long_session(long_min: float, kind: str, aet: Optional[float], road_rate: float,
                      goal_pace: Optional[float] = None, aet_measured: bool = False,
                      mp: Optional[dict] = None) -> dict:
    """The long run for 主要訓練項目 = 路跑: flat, easy; in the 專項期 with a marathon-pace segment
    near the end (Pfitzinger / Daniels). `goal_pace` (s/km, mp_goal_pace): the race's goal pace, written
    as 「目標配速 m:ss/km」 (the step builders read it); None = threshold pace × 1.04–1.08.
    `aet` = the easy-run cap (hr_profile; `aet_measured`: a measured AeT). `mp` (mp_week, SP-75): this
    week's MP share — growing, every other week, none before a road race ≤ 10 km; None = MP_SHARE.
    Session kwargs (id long)."""
    cap = easy_cap_label(None, aet, aet_measured)
    m = int(round(long_min / 5) * 5)
    if kind == "specific" and mp is not None and mp.get("share") is None:
        why = (_("比賽 10 公里以內：長跑全程輕鬆，不加馬拉松配速段") if mp.get("short") else
               _("這週長跑全程輕鬆：馬拉松配速段隔週排，逐週加長（推估）"))
        return dict(id="long", kind="long", title="LSD（路跑）", minutes=m, terrain="road",
                    detail=f"{why}；平路或緩坡；全程心率壓在{below(cap)}",
                    source=SRC_PFITZ, tss=long_min / 60.0 * road_rate)
    if kind == "specific" and m >= 60:
        share = mp["share"] if mp is not None else MP_SHARE
        mp = min(mp_minutes(m, share), m - 25)
        easy = m - mp
        return dict(id="long", kind="long", title=f"長跑＋馬拉松配速 {mp} 分", minutes=m, terrain="road",
                    detail=f"平路；前 {easy - 10} 分輕鬆（心率 ≤ {cap}），接著 {mp} 分馬拉松配速"
                           + (f"（目標配速 {_mmss(goal_pace)}/km）" if goal_pace else "（約閾值配速 × 1.06，推估）")
                           + "，最後 10 分輕鬆收操",
                    source=f"{SRC_PFITZ}（馬拉松配速長跑）；{SRC_DANIELS}（M 配速）",
                    tss=easy / 60.0 * road_rate + mp / 60.0 * max(road_rate, MP_TSS_PER_HOUR))
    return dict(id="long", kind="long", title="LSD（路跑）", minutes=m, terrain="road",
                detail=f"平路或緩坡；全程心率壓在{below(cap)}",
                source=SRC_PFITZ if kind == "specific" else SRC_UA, tss=long_min / 60.0 * road_rate)


# the old fixed 專項期 sessions: not generated any more (SP-75 — the 專項期 climbs the ladders); kept for
# the stored rows they name and their title tests
ROAD_SPECIFIC_Q = dict(id="quality", kind="quality", title="有氧間歇 2×15 分（平路）", minutes=60, terrain="road",
                       detail="平路或跑步機；休 3 分慢跑；暖身 15 分、緩和 10 分",
                       source=f"{SRC_PFITZ}（乳酸閾值跑）；{SRC_DANIELS}（T 配速）", tss=70.0)
# the 專項期 Zone 5 session for 越野 (the Zone 5 track's trail variant, SP-31)
TRAIL_SPECIFIC_Z5 = dict(id="quality", kind="quality", title="VO2max 間歇 5×4 分上坡", minutes=60,
                         detail="上坡 4 分鐘（6–10% 坡），慢跑或走下來恢復；暖身 15 分、緩和 10 分",
                         source=SRC_PALLADINO + "（Supra-threshold）", tss=60 / 60 * 75)
# 減量期: TAPER_Q (Zone 5 / no track open; 98–102 % CP short reps = 有氧間歇・巡航, ex-「短強度 4×3 分」) and a
# Zone 3 one — the volume about halved, the intensity kept (Bosquet 2007; Daniels Phase IV keeps T running)
TAPER_Q = dict(id="quality", kind="quality", title="有氧間歇（巡航）4×3 分", minutes=45,
               detail="保留強度、不累積疲勞（98–102% CP）", source=SRC_BOSQUET, tss=45 / 60 * 65)
TAPER_Z3 = dict(id="quality", kind="quality", title="有氧間歇（巡航）2×8 分", minutes=45,
                detail="保留強度、量減半（88–95% CP）；休 2 分慢跑；暖身 15 分、緩和 10 分",
                source=f"{SRC_BOSQUET}；{SRC_DANIELS}（Phase IV 保留 T）", tss=45 / 60 * 62)
# the base-phase strides for 路跑 (instead of hill sprints): title, detail, source suffixes
ROAD_STRIDES = ("＋加速跑 6×20 秒", "；最後 6 趟 20 秒平路加速跑（快而放鬆，不是衝刺），慢跑回來",
                f"；{SRC_DANIELS} strides")
HILL_STRIDES = ("＋坡道衝刺 8×10 秒", "；最後 8 趟 10 秒上坡衝刺，走下來恢復", "；Palladino 基礎中期坡衝刺")
# 轉換期 (SP-103; periodization-cross-sport.md §4.7, §4.7.1): from its 2nd week, the week's first easy
# run ends with 4 × 15 s strides at about 5K pace (Jay Johnson: 3–5 × 15 s after 20–30 min easy from
# week 2, 教練級; most coaches run once or not at all in week 1). Once a week as in the cyclists' trials
# (one short-sprint session a week in the transition kept the 20-min power, +7.3 % 6 weeks into the
# next preparation — Almquist 2020, Taylor 2021); carrying it over to running is 推估. Not in the 恢復期.
TRANSITION_STRIDES = ("＋加速跑 4×15 秒",
                      "；跑完 20–30 分輕鬆跑後 4 趟 15 秒加速（約 5K 比賽配速，快而放鬆，不是衝刺），每趟之間走或慢跑到呼吸平順",
                      "；Jay Johnson（轉換期第 2 週起每次輕鬆跑後 3–5×15 秒，教練級）；Almquist 2020、Taylor 2021"
                      "（自行車選手轉換期每週一次短衝刺）；套到跑步為推估")
TRANSITION_STRIDES_WEEK = 2


def transition_week(phases: list, monday: dt.date) -> Optional[int]:
    """Which week of its 轉換期 the week of `monday` is (1 = the week the phase starts); None when no
    轉換期 touches the week."""
    sunday = monday + dt.timedelta(days=6)
    for p in phases or ():
        kind = p["kind"] if isinstance(p, dict) else p.kind
        s = dt.date.fromisoformat(str(p["start"] if isinstance(p, dict) else p.start)[:10])
        e = dt.date.fromisoformat(str(p["end"] if isinstance(p, dict) else p.end)[:10])
        if kind == "transition" and s <= sunday and e >= monday:
            return (monday - (s - dt.timedelta(days=s.weekday()))).days // 7 + 1
    return None


def transition_strides_note(kind: str = "transition") -> dict:
    """The week note of the 轉換期's strides (SP-103); `kind` "rebuild": the 回量期 keeps them."""
    if kind == "rebuild":
        return {"level": "info", "src": "transition",
                "text": _("回量期照轉換期，每週第一次輕鬆跑後加 4 趟 15 秒加速（約 5K 配速）：量很小，保留一點速度"
                          "（Jay Johnson，教練級；自行車選手的對照試驗，套到跑步為推估）")}
    return {"level": "info", "src": "transition",
            "text": _("轉換期第 {w} 週起，每週第一次輕鬆跑後加 4 趟 15 秒加速（約 5K 配速）：量很小，保留一點速度"
                      "（Jay Johnson，教練級；自行車選手的對照試驗，套到跑步為推估）", w=TRANSITION_STRIDES_WEEK)}


def strides_for(kind: str, mode: str, i: int, road: bool, tr_week: Optional[int]) -> Optional[tuple]:
    """The strides (title, detail, source suffixes) the i-th easy run of the week carries, None = none:
    base (not a recovery / re-entry week) — the hill sprints / road strides; 轉換期 from its 2nd week —
    TRANSITION_STRIDES (SP-103); the 回量期 (SP-98: after the ≥ 7-day 恢復期 and the 轉換期, so always
    past week 2 after the race) — TRANSITION_STRIDES every week (not a re-entry week)."""
    if i != 0:
        return None
    if kind == "base" and mode not in ("recovery_week", "reentry"):
        return ROAD_STRIDES if road else HILL_STRIDES
    if kind == "transition" and (tr_week or 0) >= TRANSITION_STRIDES_WEEK:
        return TRANSITION_STRIDES
    if kind == "rebuild" and mode != "reentry":
        return TRANSITION_STRIDES
    return None


def week_plan(ds: Dataset, status, today: Optional[dt.date] = None, prefs=None, blackouts=None,
              b2b_accepted: Optional[list] = None, race_predict=None, sport: Optional[str] = None) -> dict:
    """Target volume and sessions for the current Monday–Sunday week.

    `status` is a computed `backend.engine.status.Status` (phase, goals and the
    indicators steer the plan). `prefs`: engine.plan_prefs.Prefs (課表偏好);
    None or the defaults keep the original rules untouched. `blackouts`:
    engine.blackouts ranges (不排課日期); None / empty = none. `b2b_accepted`:
    the accepted B2B entries (engine/b2b.load_accepted); None = none — a due
    B2B is then only a suggestion (`b2b_suggestion`). `race_predict`: the race
    calculator for the 專項期's コース定數 target (engine/specific_phase.py;
    race_refs.calculator_hours); None = the plan's 預估移動時間.
    `sport`: 主要訓練項目 (engine/primary_sport.py,
    trail | road); None = the setting (auto = the suggestion from the data / the next A race).
    Road: no B2B, no steep-hill walk, no mountain long run or uphill interval versions; the
    專項期 long run carries a marathon-pace segment and its interval is a flat threshold run."""
    from backend.engine import b2b as B2B
    from backend.engine import blackouts as BL
    from backend.engine import plan_prefs as PP
    PR = prefs if prefs is not None and prefs.active else None
    b2b: dict = {}
    bmap = BL.blocked(blackouts or ())
    allowed_fn = PR.allowed if PR is not None else None
    today = today or day_to_date(ds.today)
    monday = period_start(today, "week")
    sunday = monday + dt.timedelta(days=6)
    kind = status.kind or "base"
    by = {i.id: i for i in status.indicators}
    goals = status.goals
    notes: list[dict] = []
    if sport not in ("trail", "road"):
        try:
            from backend.engine import primary_sport as PSP
            sport = PSP.effective(ds, getattr(getattr(status, "plan", None), "events", None) or (), today)
        except Exception:                   # noqa: BLE001 — the plan must still build
            sport = "trail"
    road = sport == "road"
    mp_goal = mp_goal_pace(getattr(getattr(status, "plan", None), "events", None) or (), today) if road else None
    mp_rc = mp_race(getattr(getattr(status, "plan", None), "events", None) or (), today) if road else None

    # ---- history ---------------------------------------------------------
    hist = [(monday - dt.timedelta(weeks=i), *_week_hours(ds, monday - dt.timedelta(weeks=i)))
            for i in range(8, 0, -1)]                      # oldest → last complete week
    hours4 = [h for _, h, _ in hist[-4:]]
    base4 = statistics.mean(hours4) if hours4 else 0.0
    last_h = hist[-1][1] if hist else 0.0
    ref = max(base4, last_h)
    # the +10 % cap reads normal weeks only, like the volume-step guardrail (SP-73, load_guard.normal_ref)
    norm_wk = _normal_weeks(ds, status, monday, hours4)
    cap_ref = LG.step_base(norm_wk) or ref
    tph = _tss_per_hour(ds, today)
    # 轉換期 (SP-73): the pre-race level of the current / next 轉換期 (projection reads it too)
    tr_ref = _transition_ref(ds, status, today, monday)
    # 減量期 (SP-96): the next A race's taper touching this week, the pre-taper level (runs, climb)
    phs = _plan_phases(status, monday)
    t_ctx = taper_context(phs, getattr(getattr(status, "plan", None), "events", None) or (), monday)
    t_ref = _pre_taper(ds, dt.date.fromisoformat(t_ctx["taper_start"]), monday) if t_ctx else {}
    tot_h = sum(h for _, h, _ in hist[-6:])
    r_all = (sum(t for _, _, t in hist[-6:]) / tot_h) if tot_h > 1 else 50.0
    if not r_all > 0:
        r_all = 50.0        # hours but no TSS yet (HR-only runs before any LTHR / CP is known)

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
    ramp_goal = LG.ramp_goal(kind, ctl0)
    # 3:1 (SRC_31): three build weeks, each ≥ 0.95 × the one before (0.95 推估: a small dip still
    # counts as building), → a recovery week at 65 % of them (−35 %: Norwegian coaches −25–35 %,
    # runners' coaches −20–35 %, periodization-cross-sport.md §4.3 [30][194]; no trial of 3:1 [198])
    # 3:1 from the history: the base phase only (SP-97 — the 專項期 counts back from the race)
    build3 = kind == "base" and len(hist) >= 4 and all(hist[i][1] >= 0.95 * hist[i - 1][1] and hist[i][1] > 0.5
                                                       for i in range(len(hist) - 3, len(hist)))
    rec_skip = LG.skip_mondays(getattr(status, "plan", None), [m for m, _, _ in hist])
    rec_since = weeks_since_recovery([h for _, h, _ in hist], [m in rec_skip for m, _, _ in hist])
    rec_why = None if build3 else recovery_reason(kind, monday, next_a_start(phs, monday), rec_since)
    if kind in ("base", "specific"):
        need_tss = 7.0 * (ctl0 + ramp_goal / f7)
        need_h = need_tss / r_all
        cap = max(1.10 * cap_ref, cap_ref + 0.5)
        hours = min(max(need_h, base4), cap)
        why.append(_("CTL {ctl:.0f} 要每週 +{ramp:.1f}，需要約 {tss:.0f} TSS（≈ {h:.1f} h）",
                      ctl=ctl0, ramp=ramp_goal, tss=need_tss, h=need_h))
        if need_h > cap and cap_ref != ref:
            why.append(_("但週量上限 = 最近的正常訓練週 {ref:.1f} h（不含減量期／比賽週／賽後恢復期／轉換期）"
                         "的 +10%（至少 +0.5 h）→ {cap:.1f} h", ref=cap_ref, cap=cap))
        elif need_h > cap:
            why.append(_("但週量上限 = 近 4 週 {base4:.1f} h / 上週 {last:.1f} h 的 +10%（至少 +0.5 h）→ {cap:.1f} h",
                          base4=base4, last=last_h, cap=cap))
        # B2B (engine/b2b.py): a planned B2B's TSB drop doesn't make this / next week a recovery week
        # (主要訓練項目 = 路跑: no B2B weekend — an ultra / mountain tool, Koop; Uphill Athlete)
        b2b = {} if road else B2B.plan_context(ds, status, today, monday, [h for _, h, _ in hist], ctl_s, atl_s,
                                               d_prev_sun, by, "recovery_week" if build3 or rec_why else kind,
                                               accepted=b2b_accepted)
        b2b_exempt = B2B.tsb_exempt(b2b, tsb_today, b2b.get("ramp"), b2b.get("ramp_base"))
        if b2b_exempt:
            why.append(b2b_exempt)
        norm_hours = hours                  # a normal week's volume (SP-97: the recovery week's usual long run)
        if tsb_today < -30 and not b2b_exempt:
            mode, hours = "recovery_week", 0.6 * base4
            why.append(_("TSB {tsb:+.0f} < −30：改成恢復週（近 4 週的 60%）", tsb=tsb_today))
        elif rec_why:
            # SP-97: the 專項期 countdown / the 6-week cap — planned, so a TSB of −20…−30 doesn't skip it
            mode, hours = "recovery_week", 0.65 * statistics.mean(h for _, h, _ in hist[-3:])
            why.append(rec_why)
        elif tsb_today < -20 and not b2b_exempt:
            hours = min(hours, base4)
            why.append(_("TSB {tsb:+.0f} < −20：先維持量，不加", tsb=tsb_today))
        elif build3:
            mode, hours = "recovery_week", 0.65 * statistics.mean(h for _, h, _ in hist[-3:])
            why.append(_("已連續 3 週加量：這週是恢復週（前 3 週平均的 65%）"))
    elif kind == "taper":
        base6 = statistics.mean(h for _, h, _ in hist[-6:]) if hist else 0.0
        days_to = goals.get("days_to_next_a")
        # 50 % → 40 % in the last 7 days (SRC_BOSQUET): Bosquet 2007 / Wang 2023 −41–60 % (a cut ≤ 40 %
        # is too small), intensity kept [206][105]; Pfitzinger (中譯本) race week −60 % [459]; 徐國峰
        # 8–14 days [453] (periodization-cross-sport.md §4.5, §4.10)
        share = 0.4 if days_to is not None and days_to <= 7 else 0.5
        hours = base6 * share
        why.append(_("減量期：平常 {h:.1f} h × {share:.0%}", h=base6, share=share))
    elif kind == "event":
        hours = 0.3 * base4
        why.append(_("比賽週：短、輕鬆"))
    elif kind == "recovery":
        # SP-98: a share of the level before the race (planning.pre_race_mondays), not of the taper weeks
        hours, w = recovery_hours(tr_ref.get("hours"), base4)
        why.append(w)
    elif kind == "rebuild":
        from backend.engine import planning as _PL
        rb_ph = next((p for p in phs if p.kind == "rebuild" and _PL._d(p.start) <= monday + dt.timedelta(days=6)
                      and _PL._d(p.end) >= monday), None)
        share, i, n = _PL.rebuild_share(rb_ph, monday) if rb_ph is not None else (_PL.REBUILD_SHARES[0], 0, 1)
        hours, w = rebuild_hours(tr_ref.get("hours"), base4, share, i, n)
        why.append(w)
    elif kind == "transition":
        # 轉換期 (SP-73): a share of the level before the race, not of the taper / race / recovery weeks
        hours, w = transition_hours(tr_ref.get("hours"), base4)
        why.append(w)
    hours = max(hours, 0.0)
    if kind == "specific":
        # 中間訓練 between two A races < 12 weeks apart (SP-95): ≤ a share of the first one's pre-taper level
        hours = inter_cap(phs, monday, hours, why, lambda m: _week_hours(ds, m)[0] if m < monday else None)
    from backend.engine import post_race as PR_
    plan_events = getattr(getattr(status, "plan", None), "events", None) or ()
    b_f, b_why = PR_.b_week_factor(plan_events, monday, kind)       # a B race's week: 75 % (SP-95)
    if b_why:
        hours *= b_f
        why.append(b_why)
    if PR is not None and PR.weekly_hours is not None and hours > PR.weekly_hours:
        hours = PR.weekly_hours
        why.append(_("你的每週時數上限 {h:g} h", h=PR.weekly_hours))
    lost: list[dt.date] = []
    # 停訓後的恢復期 (engine/reentry.py; detraining.md §6): a break ≥ 6 days without running —
    # planned (不排課日期) or not — gets Daniels' block (table 9.2). It replaces the old
    # 「不排課後週量 +10%（至少 +0.5 h）」 step (blackouts.step_cap), which after a fully blocked
    # week capped the next one at 0.5 h — far slower than Daniels' 50 % → 75 % → 100 %.
    from backend.engine import reentry as RE
    try:
        rp = RE.find(ds, today, blackouts or ())
    except Exception:                       # noqa: BLE001 — the plan must still build
        rp = None
    try:                                    # 傷病紀錄 (engine/injuries.py): 「右膝進行中（第 5 天）」
        from backend.engine import injuries as INJ
        notes.extend(INJ.week_notes(INJ.load_events(), monday, today))
    except Exception:                       # noqa: BLE001
        pass
    re_f = RE.week_factor(rp, monday) if rp and kind in ("base", "specific") else None
    if re_f is not None and rp.get("prev_hours"):
        hours = rp["prev_hours"] * re_f
        mode = "reentry"
        why.append(_("{text}：本週 = 停訓前 4 週平均 {prev:.1f} h × {f:.0%} → {h:.1f} h",
                      text=rp["text"], prev=rp["prev_hours"], f=re_f, h=hours))
        notes.append({"level": "info", "src": "reentry", "text": rp["text"]})
    elif rp and kind in ("base", "specific") and rp.get("prev_hours") and \
            rp["end"] <= monday.isoformat() < (dt.date.fromisoformat(rp["end"]) + dt.timedelta(days=7)).isoformat():
        hours = max(hours, rp["prev_hours"])            # Daniels: back to 100 % after the block
        why.append(_("恢復期結束：回到停訓前的量 {h:.1f} h（Daniels 表 9.2）", h=rp["prev_hours"]))
    if bmap:
        def trained(m: dt.date) -> set:
            return {wdate(w) for w in workouts_between(ds, m, m + dt.timedelta(days=7))}
        lost = BL.lost_days(bmap, monday, allowed_fn, trained(monday))
        if lost and mode == "reentry":
            lost = []                                    # the block already counts the break days as 0
        if lost:
            f = BL.factor(monday, lost, allowed_fn)
            lost_h = hours * (1.0 - f)
            hours *= f
            why.append(_("不排課 {range}：少 {n} 個可練日，週量 × {f:.0%}", range=BL.range_text(lost), n=len(lost), f=f))
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
                          (est.get("aethr") or {}).get("value"), (est.get("aethr") or {}).get("below"))
    if rp and rp["return"] <= sunday.isoformat() and monday.isoformat() < (
            dt.date.fromisoformat(rp["end"]) + dt.timedelta(days=RE.TARGETS_AFTER_DAYS)).isoformat() \
            and rp.get("fvdot", 1.0) < 1.0:
        # re-entry: HR zones stay (they follow the body); power / pace targets × FVDOT
        # (Daniels / VDOT O2; power × FVDOT is 推估 — VDOT ↔ CP not verified)
        f = float(rp["fvdot"])
        tt = {**tt, "cp": (tt.get("cp") or 0) * f or tt.get("cp"),
              "rows": [{**r, "power": [None if x is None else x * f for x in (r.get("power") or [])]}
                       for r in tt.get("rows") or []]}
        notes.append({"level": "info", "src": "reentry",
                      "text": _("恢復期：心率區間為主；功率、配速目標 × {f:.3f}（停跑 {days} 天的 FVDOT{cross}）",
                                f=f, days=rp["days"], cross=_("，有交叉訓練") if rp.get("cross") else "")})
    tgt = _targets(tt)
    # the easy-run cap of every session: 課表心率區間 (設定; engine/hr_profile.py) — a measured AeT,
    # else the chosen COROS model's Z2 top; an estimated AeT no longer caps (owner 2026-10-03).
    # The session texts call it 「輕鬆跑上限」 (hr_profile.easy_cap_label; 「（實測 AeT）」 only when
    # measured); a note says what it is when the model isn't LTHR.
    hrz = tt.get("hr_model")
    aet = (tt.get("easy_cap") or {}).get("value") or tt.get("aet")
    aet_src = (tt.get("easy_cap") or {}).get("source") or tt.get("aet_source")
    aet_meas = bool(hrz.get("aet_measured")) if hrz else bool(tt.get("aet_measured"))
    cap_txt = easy_cap_label(None, aet, aet_meas)
    # the walking sessions' uphill cap (SP-115: 75 % HRmax, never below the easy-run cap)
    from backend.engine.hr_profile import walk_cap_for
    walk = walk_cap_for(ds, today, aet, getattr(status.plan, "profile", None))
    if hrz and (hrz.get("fallback") or (hrz["model"] != "lthr" and not hrz.get("aet_measured"))):
        notes.append({"level": "info", "src": "hr_zones",
                      "text": (hrz["fallback"] + "。" if hrz.get("fallback") else "")
                      + _("課表心率用{label}：輕鬆跑上限 {aet:.0f} bpm（{src}）", label=hrz["label"], aet=aet, src=aet_src)})
    try:
        from backend.engine import base_check as BC
        et = BC.easy_targets(ds, today, aet) if not ds.config.parity else None
    except Exception:                       # noqa: BLE001
        et = None
    if et and tgt.get("z2") is not None:
        tgt = {**tgt, "z2": (tgt["z2"] + " · " if tgt["z2"] else "") + et["text"]}
    lvl = lambda iid: getattr(by.get(iid), "level", "na")
    days_to = goals.get("days_to_next_a")
    goal_h = (goals["targets"].get("est_hours") or {}).get("value")
    goal_d = (goals["targets"].get("climb_per_km") or {}).get("value")
    mountain_goal = not road and (bool(goal_d) or any(e.kind in ("race", "baiyue") for e in status.plan.events
                                                      if e.end >= today))
    # the longest foot session of the last LG.LONG_DAYS (SP-66, Frandsen 2025: 30 days; was 28 days
    # incl. rides — a ride is no reference for a run); the name is kept (specific_phase's "longest28")
    longest28 = max((moving_s(w) for w in workouts_between(ds, today - dt.timedelta(days=LG.LONG_DAYS),
                                                          today + dt.timedelta(days=1))
                     if category(w) in FOOT), default=0.0) / 60.0
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
    # 課表偏好 每週品質課 2 → one Zone 3 + one Zone 5 when both tracks are open (SP-31); the
    # guardrail mode keeps the base phase at one
    q_n = quality_per_week(PR, kind, mode, gate)
    dec = QG.week_decision(gate, kind, mode, monday, n=q_n)
    allow_quality = dec["allow"]
    in_reentry = mode == "reentry"
    # a week touching the block gets no quality (推估: the whole week after it, so nothing lands inside)
    if in_reentry and not RE.quality_ok(rp, monday):
        # Daniels: E days only inside the block — no Zone 3, no Zone 5, no test
        allow_quality = False
        dec = {**dec, "allow": False, "spec": None, "advance": False}
    tx = getattr(by.get("testing"), "extra", None) or {}
    cp_due = tx.get("cp_due", lvl("testing") in ("bad", "watch"))      # the CP test measures CP only
    test_due = cp_due and lvl("testing") in ("bad", "watch") and (days_to is None or days_to > 10) \
        and not (in_reentry and not RE.quality_ok(rp, monday)) and kind not in ("transition", "rebuild", "recovery")   # 轉換期 / 回量期: no hard session (SP-73, SP-98)
    # 課表偏好 CP 測試方式 (engine/cp_protocols.py) — read even when the other
    # preferences are the defaults (it is not part of Prefs.active)
    from backend.engine import cp_protocols as CPP
    protocol = CPP.norm(getattr(prefs, "cp_test_protocol", None))
    test_s = CPP.session_for(protocol) if test_due else None      # race: nothing scheduled
    # the AeT test: base phase, a reason (quality_gate.aet_test_reason — B3 and the Z5
    # lifecycle; no fixed cadence), never the CP-test week; its protocol from 課表偏好
    # A 賽後重新打底 (SP-116): a test from before the rebuild doesn't hold it back, and the test is
    # the 間歇門檻's own (xu_drift → 徐國峰 90 分, friel_drift → Friel 60 分)
    aet_reason = gate.get("aet_test_reason") or {}
    last_aet = tx.get("aet_last_test")
    if aet_reason.get("code") == "rebase" and last_aet and str(last_aet)[:10] < aet_reason["from"]:
        last_aet = None
    aet_due = test_s is None and (days_to is None or days_to > 10) and not in_reentry \
        and mode != "recovery_week" and AT.due(
        today, kind, gate.get("base_start"), gate.get("aet_test_reason"), last_aet)
    aet_pref = getattr(prefs, "aet_test_protocol", None) or "auto"
    if aet_reason.get("code") == "rebase":
        aet_pref = {"xu_drift": "xu90", "friel_drift": "friel"}.get(gate.get("mode"), aet_pref)
    aet_proto = AT.resolve_protocol(aet_pref, getattr(prefs, "cap_weekday", None),
                                    getattr(prefs, "long_cap", None) if prefs is not None else None)
    # strength: 2 a week outside the season, 1 in it — once a week kept cyclists' strength for 13 weeks
    # (Rønnestad 2010, periodization-cross-sport.md §4.7 [407])
    strength_n = 2 if kind in ("base", "transition", "recovery", "rebuild") or lvl("strength") in ("bad", "watch") else 1

    def add(**kw):
        sessions.append(Session(**kw))

    # 專項期 (engine/specific_phase.py): the long day follows the next A race's コース定數
    from backend.engine import specific_phase as SP
    sp = SP.plan_context(status, today, monday, mode, _n(ctl_s.at(d_prev_sun) - atl_s.at(d_prev_sun)), longest28,
                         race_predict, sport=sport) if kind == "specific" else {"active": False}
    if (sp.get("race") or {}).get("split_hint"):
        # SP-114: an old multi-day event without its per-day numbers — the 專項期 targets an equal split
        notes.append({"level": "watch", "src": "specific", "text": sp["race"]["split_hint"]})

    # the user's own RPE ≥ 7 技術地形 sessions this week (engine/technical.py, SP-74): in the 20 % first
    from backend.engine import technical as TECH
    user_q = TECH.user_quality(TECH.load_user(), monday) \
        if kind in ("base", "specific") and mode != "recovery_week" else []
    if user_q:
        notes.append(TECH.user_note(user_q, hours, kind))

    rec_wk = mode == "recovery_week"
    if kind in ("base", "specific"):
        if kind == "specific" and sp.get("active"):
            long_min = SP.long_minutes(sp, longest28)
        elif kind == "specific" and goal_h:
            long_min = max(90.0, min(goal_h * 0.7 * 60.0, max(longest28, 60.0) * LG.LONG_CAP))
        else:
            long_min = max(60.0, min(0.30 * minutes_total, max(longest28, 60.0) * LG.LONG_CAP))
        long_min = min(long_min, 0.5 * minutes_total) if minutes_total >= 120 else long_min
        if rec_wk:
            # SP-97: a recovery week keeps a shorter long run (the usual one × 65 %; the 專項期's ≤ FRAC's low point)
            usual = longest28 if kind == "specific" else min(longest28, 0.30 * norm_hours * 60.0)
            long_min = recovery_long_minutes(usual, minutes_total, long_min if kind == "specific" and sp.get("active")
                                             else None)
        # SP-66: ≤ +10 % over the longest of 30 days wins over the 60 / 90-min floors (Frandsen 2025) —
        # and over a recovery week's RECOVERY_LONG_MIN floor (SP-97), so it comes last
        capped, cut = LG.cap_long(long_min, longest28)
        if cut:
            notes.append({"level": "info", "src": "long_cap", "text": LG.cap_note(long_min, longest28)})
            long_min = capped
        if in_reentry:
            # the longest run before the break × the block's % (6–13 days: ≤ 90 min) — detraining.md §6.2
            fr = max((RE.frac_on(rp, monday + dt.timedelta(days=i)) or 0.0) for i in range(7)) or 1.0
            long_min = min(long_min, max(30.0, (rp.get("prev_long_min") or long_min) * fr))
            if rp["category"] == "6-13":
                long_min = min(long_min, float(RE.LONG_CAP_MIN))
        terrain = (f"挑每公里爬升 ≥ {goal_d * 0.7:.0f} m 的路線" if goal_d else
                   "有山路就走山路，陡坡用走的" if mountain_goal else "平路或緩坡")
        # a due CP / AeT test is SUGGESTED, never put into the plan (the user, 2026-10-01): the
        # athlete picks the day (test_suggestions below → 「排入」 on the overview / 課表 page)
        if road:
            # (a recovery week's long run is easy: no marathon-pace segment)
            add(**road_long_session(long_min, "base" if rec_wk else kind, aet, tph["road"], mp_goal, aet_meas,
                                    mp_week(mp_rc, monday)),
                target=tgt.get("long", ""))
        else:
            add(id="long", kind="long", title="LSD" + ("（山路）" if mountain_goal else ""),
                minutes=int(round(long_min / 5) * 5), target=tgt.get("long", ""),
                detail=f"{terrain}；全程心率壓在{below(cap_txt)}，爬坡可以走",
                source=SRC_KOOP if kind == "specific" else SRC_UA,
                tss=long_min / 60.0 * tph["trail" if mountain_goal else "road"])
        if rec_wk:
            recovery_long(sessions[-1])
        if b2b.get("candidate") and not in_reentry:
            # a due B2B is a SUGGESTION (b2b_suggestion below); only an accepted one is planned
            B2B.finalize(b2b, sessions[-1].minutes, b2b.get("longest_before") or 0.0, minutes_total)
        if b2b.get("due"):            # accepted: day 2 out of the easy minutes below (Koop: total unchanged)
            ls = asdict(sessions[-1])
            fol = B2B.followers(ls, b2b)
            sessions[-1] = Session(**ls)
            for f in fol:
                add(**f)
        if rec_wk:
            if allow_quality and dec["spec"] is not None:
                # a recovery week (3:1, the 專項期 countdown, the 6-week cap — SP-97): the short fartlek
                # instead of intervals (Palladino), in the 專項期 too
                add(**_gate_session(gate, dec, tt, hours))
        elif allow_quality and kind in ("base", "specific") and dec.get("items"):
            # two tracks (engine/quality_gate.py week_decision, SP-31): each item is a ladder rung as a
            # library variant fitted into the weekday cap (engine/interval_library.py); 專項期 越野 the
            # rung's uphill version (SP-75)
            q_cap, q_alt = quality_caps(PR, PP.long_weekday(PR, _long_weekday(ds, today)) if PR is not None else 5)
            for q in quality_sessions(gate, dec, kind, tt, tgt, hours, prefs, variant_history(monday, gate),
                                      mountain_goal if kind == "base" else not road, road, q_cap, q_alt, notes,
                                      reserved=sum(u["work"] for u in user_q)):
                add(**q)
    elif kind == "taper":
        # 減量期: one session, the two-track choice (SP-31) — Zone 3 節奏 2×8′ or the 4×3′ short intensity;
        # its content follows the days to the race after the placement (taper_rules, SP-96)
        for q in quality_sessions(gate, dec, kind, tt, tgt, hours):
            add(**q)
        if t_ctx and (dt.date.fromisoformat(t_ctx["start"]) - monday).days > t_ctx["long_days"]:
            # the last long run: ≤ 90 min easy, ≥ 7 (14 when sore) days out (SP-96)
            lm = taper_long_minutes(minutes_total)
            add(id="long", kind="long", title=_("長跑（減量期，≤ {max} 分）", max=TAPER_LONG_MAX_MIN),
                minutes=int(round(lm / 5) * 5), target=tgt.get("long", ""),
                detail=f"心率不超過{cap_txt}；" + ("平路或緩坡，不跑長下坡" if t_ctx.get("sore") else "輕鬆跑"),
                source=SRC_TAPER_WEEK, tss=lm / 60.0 * tph["road"])
    if kind in ("base", "specific", "taper") and dec.get("z3_note") and not in_reentry:
        # why there is no Zone 3 session this week (SP-31: the gate / a guardrail / the 1-a-week turn)
        notes.append({"level": "info", "src": "z3", "text": dec["z3_note"]})
    if dec.get("seg_note") and not in_reentry and kind == "specific" and not rec_wk:
        # SP-75: what the 專項期's 後段 changed in this week's intervals
        notes.append({"level": "info", "src": "specific", "text": dec["seg_note"]})
    if dec.get("warn") and not in_reentry:
        # the low-intensity share under its floor: a warning for Zone 3, Zone 5 waits (SP-31)
        notes.append({"level": "watch", "src": "intensity", "text": dec["warn"]})
    elif kind == "event":
        add(id="race", kind="race", title="比賽", minutes=0, detail="賽前 2 天 20–30 分輕鬆跑＋幾趟加速",
            source="教練常見做法（推估）")
    if (gate.get("guard") or {}).get("step_note") and kind in ("base", "specific") and not in_reentry:
        # the volume step skipped after a short break (SP-63, load_guard.short_break)
        notes.append({"level": "info", "src": "volume", "text": gate["guard"]["step_note"]})
    if b2b.get("post"):
        # the days after a B2B: easy only (UA / Johnston); the minutes go to the easy runs
        sessions = [s for s in sessions if s.kind not in ("quality", "test")]
        notes.append(B2B.post_note(b2b))
    # 肌力課依期別 (engine/strength_plan.py, SP-119): a 越野賽 / 百岳 A race next — AA / 最大肌力 / 維持
    from backend.engine import strength_plan as STP
    a_evs = getattr(getattr(status, "plan", None), "events", None) or ()
    st_ctx = STP.week_context(a_evs, phs, monday, kind, prefs)       # + the picked moves (SP-191)
    st_s = STP.session(st_ctx, [asdict(s) for s in sessions])
    for i in range(strength_n):
        add(id=f"strength{i + 1}", kind="strength", title=st_s["title"], minutes=st_s["minutes"],
            detail=st_s["detail"], source=st_s["source"] or SRC_UA,
            tss=st_s["minutes"] / 60 * tph["strength"])
    used = sum(s.minutes for s in sessions if s.kind not in ("strength",))
    left = max(0.0, minutes_total - used)
    tr_wk = transition_week(phs, monday) if kind == "transition" else None
    n_easy = easy_count(left, kind)
    if kind == "taper":
        # keep the run count, each run shorter (SP-96)
        n_easy = taper_easy_count(left, t_ref.get("runs"), sum(1 for s in sessions if s.kind in RUN_KINDS))
    n_easy = auto_easy_cap(n_easy, sum(1 for s in sessions if s.kind in RUN_KINDS))   # ≥ 1 rest day (SP-82)
    for i in range(n_easy):
        m = min(left / n_easy, TRANSITION_RUN_MAX) if kind in ("transition", "rebuild") else left / n_easy
        st = strides_for(kind, mode, i, road, tr_wk)       # base; 轉換期 from week 2 (SP-103)
        strides = st is not None
        st_t, st_d, st_s = st or ("", "", "")
        add(id=f"easy{i + 1}", kind="easy", title="輕鬆跑" + (st_t if strides else ""),
            minutes=int(round(m / 5) * 5), target=tgt.get("z2", ""),
            detail=f"心率不超過{cap_txt}" + (st_d if strides else ""),
            source=SRC_UA + (st_s if strides else ""), tss=m / 60.0 * tph["road"])
    if PR is not None:
        # 課表偏好: counts, caps, terrain, interval target (engine/plan_prefs.py)
        ctx = PP.Ctx(kind=kind, mode=mode, allow_quality=allow_quality, rates=tph, aet=aet, aet_measured=aet_meas,
                     slots=max(1, sum(bool(x) for x in PR.days) - len(lost)), notes=notes,
                     quality_cap=1 if kind == "base" and QG.guardrail_mode(gate) else None)
        shaped = PP.shape([asdict(s) for s in sessions], minutes_total, PR, ctx)
        sessions = []
        for d in shaped:
            flag = d.pop("long_day", False)
            sessions.append(Session(**d))
            sessions[-1]._long_day = flag          # soft cap: this easy run carries the excess
    if kind in ("transition", "rebuild"):
        # 轉換期 (SP-73) / 回量期 (SP-98): each run ≤ 60 min (Canova) — also after the 課表偏好 shaping
        cap_transition_runs(sessions, notes)
        notes.append({"level": "info", "src": "transition",
                      "text": _(TRANSITION_NOTE) if kind == "transition" else _(REBUILD_NOTE)})
        if any(TRANSITION_STRIDES[0] in s.title for s in sessions):
            notes.append(transition_strides_note(kind))
    # a 轉換期 shortened / skipped for the next A race's 專項期; two A races close together — a
    # 恢復期 / 專項期 / 減量期 cut short, the 12-week hint (SP-90) (planning.auto_phases)
    for pk, t in _week_phase_notes(status, monday):
        notes.append({"level": "info", "src": "transition" if pk in ("recovery", "transition", "rebuild") else "phase",
                      "text": t})
    if b2b.get("due"):
        # B2B texts / caps after the 課表偏好 shaping (it may rename, re-kind or cap the long run)
        flags = {s.id: getattr(s, "_long_day", False) for s in sessions}
        dd = [asdict(s) for s in sessions]
        B2B.decorate(dd, b2b, aet, PR.long_cap if PR is not None else None, b2b.get("weight"), aet_meas)
        sessions = [Session(**d) for d in dd]
        for s in sessions:
            s._long_day = flags.get(s.id, False)
    if sp.get("active"):
        # 專項期: 「這次目標定數約 N（單日目標的 X%）」 + the route, after the caps above
        flags = {s.id: getattr(s, "_long_day", False) for s in sessions}
        dd = [asdict(s) for s in sessions]
        SP.decorate(dd, sp)
        # SP-114: a multi-day 百岳 — ME instead of the uphill VO2max set (ADS ≤ 10 %), else general strength
        try:
            wkg = status.plan.weight_on(today)
        except Exception:                   # noqa: BLE001
            wkg = None
        SP.apply_me(dd, sp, gate.get("gap"), wkg, allow=allow_quality and not b2b.get("post"),
                    rate=tph["trail"], notes=notes)
        SP.walk_targets(dd, walk, aet, aet_meas)       # 攻頂日模擬 / ME: the 75 % HRmax uphill cap (SP-115)
        STP.refresh(dd, st_ctx)                        # SP-119: a 百岳 維持 week with the ME just added
        sessions = [Session(**d) for d in dd]
        for s in sessions:
            s._long_day = flags.get(s.id, False)

    # ---- mark what is done ----------------------------------------------
    pool = sorted(week_ws, key=lambda w: w.day)
    used_idx: set[int] = set()
    hard_z3: Optional[dict] = None

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
        elif s.id in B2B.FOLLOWERS:               # B2B day 2 / 3: the day after the one before
            w = take(lambda w: category(w) in ENDURANCE and moving_s(w) / 60 >= 0.8 * s.minutes
                     and B2B.done_follow(sessions, s, wdate(w)))
        elif s.id == "me":                        # SP-114 ME: a walk / run that climbed ≥ 80 % of it (推估)
            w = take(lambda w: category(w) in FOOT and (_n(w.metrics.get("climbing")) or 0.0) >= 0.8 * (s.climb_m or 0.0))
        elif s.id == "test_aet":
            # the 50-min test: a ≥ 48-min road run (2′ slack) titled AeT — the COROS
            # workout's name; untitled only from 55 min (workout_review.TEST_AET_MIN_S),
            # so ordinary ~45′ road runs are not taken for the test (推估)
            from backend.engine import workout_review as WR
            if AT.is_xu(asdict(s)):
                # 徐國峰's test = any ≥ 88-min flat road run that week (the LSD itself; 2′ slack)
                w = take(lambda w: category(w) == "road" and moving_s(w) >= AT.XU_MIN * 60 - 2 * 60)
            else:
                w = take(lambda w: category(w) == "road" and moving_s(w) >= AT.WARM_S + AT.MAIN_MIN_S - 2 * 60
                         and (bool(WR.AET_TITLE.search(WR._title(w))) or moving_s(w) >= WR.TEST_AET_MIN_S))
        elif s.kind in ("quality", "test"):
            need = QG.hard_need(s.title, HARD_SESSION_S, s.variant_key, s.variant_reps)
            if s.variant_key and QG.is_z3_variant(s.variant_key):
                # Zone 3 at 90–95 % CP never reaches 95 % CP: its own time (10-s power ≥ 85 % CP)
                if hard_z3 is None:
                    hard_z3 = _hard_seconds(ds, [w for w in week_ws if category(w) in ENDURANCE],
                                            int(date_to_day(monday)), int(date_to_day(sunday)), (Z3_EXPR,))
                w = take(lambda w, h=hard_z3: h.get(w.idx, 0) >= need)
            elif s.kind == "quality" and (QG.is_z5_variant(s.variant_key) or str(s.rung_key or "").startswith("z5")):
                # the Zone 5 slot: only a run classified Z5 (equivalent T@VO2max ≥ 4 min; owner
                # 2026-10-02) — a threshold climb with 10′ ≥ LTHR no longer ticks it
                w = take(lambda w: session_of(ds, w).get("stimulus") == "z5")
            else:
                w = take(lambda w: hard.get(w.idx, 0) >= need)
        elif s.kind in ("easy", "hike"):        # hike = 越野跑 (the 專項期 技術地形 session, SP-74)
            w = take(lambda w: category(w) in ENDURANCE)
        if w is not None:
            s.done = True
            s.done_by = activity_row(w, ds)
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
    # hard days already done this week (Z5 / Z3 / 高強度長跑 / CP test, planned or not): the
    # remaining interval keeps 48 h from them (台灣教練: ≥ 2 days apart)
    from backend.engine import workout_review as _WR
    hard_done = sorted({wdate(w) for w in week_ws if category(w) in ("road", "trail", "hike")
                        and session_of(ds, w).get("type") in _WR.HARD_TYPES})
    if PR is not None:
        long_wd = PP.long_weekday(PR, long_wd)
        ds_ = [{**asdict(s), "long_day": getattr(s, "_long_day", False)} for s in todo]
        notes.extend(PP.blocked_pref_notes(PR, monday, bmap))          # a preferred weekday on a 不排課日期
        left_out = PP.place(ds_, free, long_wd, PR, notes=notes, long_done=long_done, hard_done=hard_done,
                            run_done=[wdate(w) for w in week_ws if category(w) in ENDURANCE])
        for s, d in zip(todo, ds_):
            if d["day"]:
                put(s, dt.date.fromisoformat(d["day"]))
        if left_out:
            drop_min = sum(s["minutes"] for s in left_out)
            n_ok = len([d for d in free if PR.allowed(d)])
            notes.append({"level": "info", "src": "prefs", "text": _("本週可練的日子只剩 {days} 天，{n} 堂課（約 {m} 分鐘）排不進去——不用補，下週照常",
                                                               days=n_ok, n=len(left_out), m=drop_min)})
        avail, keep_rest, main_todo = [], True, []
    else:
        avail = list(free)
        # keep one rest day when there is room
        keep_rest = len(avail) > len(main_todo) + 0
    aet_days = AT.test_days(prefs)            # 課表偏好 aet_test_days; Mon–Fri without prefs too
    easy_q: list = []
    for s in sorted(main_todo, key=lambda s: -1 if AT.is_xu(asdict(s)) else
                    {"long": 0, "test": 1, "quality": 1}.get(s.kind, 2)):
        if not avail:
            break
        if s.kind == "long":
            pick = next((d for d in avail if d.weekday() == long_wd), avail[-1])
        elif s.kind == "test" and AT.is_xu(asdict(s)):
            d = AT.pick_day_xu(avail, long_wd, getattr(prefs, "cap_weekday", None))
            if d is None:
                notes.append({"level": "info", "text": _("徐國峰 90 分鐘測試排在週末長跑日，本週週末沒有可練的日子：這週先不測")})
                continue
            put(s, d)
            avail.remove(d)
            continue
        elif s.kind == "test" and aet_days is not None and AT.is_aet_session(asdict(s)):
            long_day = next((dt.date.fromisoformat(x.day) for x in sessions
                             if (x.kind == "long" or AT.is_xu(asdict(x))) and x.day), None)
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
            long_day = next((dt.date.fromisoformat(x.day) for x in sessions
                             if (x.kind == "long" or AT.is_xu(asdict(x))) and x.day), None)
            cands = [d for d in avail if (long_day is None or abs((d - long_day).days) >= 2)
                     and all(abs((d - h).days) >= 2 for h in hard_done)]
            if not cands and lost and any(abs((d - long_day).days) <= 1 for d in avail):
                continue          # 不排課日期 left no room: drop it rather than stack two hard days
            pick = (cands or avail)[0]
        elif s.kind == "easy":
            easy_q.append(s)            # placed together below: where the rest days fall (SP-82)
            continue
        else:
            pick = avail[0]
        put(s, pick)
        avail.remove(pick)
    if easy_q and avail:
        # 休息日的位置 (engine/rest_days.py, SP-82): the easy runs take the days that leave the rest days
        # next to the long run and between hard days, not the earliest free days
        from backend.engine import rest_days as RD
        ld = next((dt.date.fromisoformat(x.day) for x in main_todo if x.kind == "long" and x.day), long_done)
        runs = [dt.date.fromisoformat(x.day) for x in main_todo if x.day] + \
            [wdate(w) for w in week_ws if category(w) in ENDURANCE]
        qd = [dt.date.fromisoformat(x.day) for x in main_todo if x.kind in ("quality", "test") and x.day] + hard_done
        for s, d in zip(easy_q, RD.pick_days(len(easy_q), avail, [monday + dt.timedelta(days=i) for i in range(7)],
                                             runs, ld, long_wd, qd)):
            put(s, d)
            avail.remove(d)
    unplaced = [s for s in main_todo if s.day is None]
    if unplaced:
        drop_min = sum(s.minutes for s in unplaced)
        what = (_("本週只剩 {days} 天，{n} 次輕鬆跑（約 {m} 分鐘）排不進去——不用補，下週照常")
                if all(s.kind == "easy" for s in unplaced) else
                _("本週只剩 {days} 天，{n} 堂課（約 {m} 分鐘）排不進去——不用補，下週照常"))
        notes.append({"level": "info", **({"src": "blackout"} if lost else {}),
                      "text": what.format(days=len(free), n=len(unplaced), m=drop_min)})
    # strength on easy days (or free days), never the day before the long session
    easy_days = [dt.date.fromisoformat(s.day) for s in main_todo if s.kind == "easy" and s.day]
    long_day = next((dt.date.fromisoformat(s.day) for s in main_todo if s.kind == "long" and s.day), None)
    from backend.engine import rest_days as RD
    for s in [s for s in todo if s.kind == "strength"]:
        # an easy-run day first, a free day only when none fits (SP-82: strength doesn't take the rest day)
        cands = RD.strength_days(easy_days, avail, [long_day - dt.timedelta(days=1)] if long_day else [])
        cands = [d for d in cands if d.isoformat() not in [x.day for x in sessions if x.kind == "strength" and x.day]]
        if cands:
            d = cands[0]
            put(s, d)
            if d in avail:
                avail.remove(d)
    if not keep_rest and free:
        notes.append({"level": "info", "text": _("剩下的每一天都排了東西；覺得累就把一次輕鬆跑換成休息")})
    if b2b.get("due"):
        # the accepted B2B on the user's two days (engine/b2b.py place, fixed): the rest moves around them
        dd = [asdict(s) for s in sessions]
        kept = B2B.place(dd, monday, first, set(bmap), allowed_fn, notes, PR.cap_weekday if PR is not None else None,
                         fixed=b2b.get("pair"))
        sessions = [Session(**d) for d in kept]
        B2B.placed(b2b, kept)
    if sp.get("climb"):
        # 長爬坡反覆 (engine/specific_phase.py): the race GPX's longest climb, one easy run
        try:
            dd = [asdict(s) for s in sessions]
            SP.apply_climb(dd, sp, aet=aet, prefs=prefs, b2b=b2b, notes=notes, rates=tph, aet_measured=aet_meas)
            sessions = [Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__}) for d in dd]
        except Exception as e:              # noqa: BLE001 — the plan must still build
            sp = {**sp, "error": type(e).__name__}
    # ---- 陡坡健走（模擬負重） (engine/steep_hill.py): before a 百岳 / multi-day trip, one weekday
    # easy run of a 專項期 week becomes a steep walk at the grade that costs what the pack would
    from backend.engine import steep_hill as SH
    # (主要訓練項目 = 路跑: no steep walk — it simulates a mountain pack)
    lc = {"active": False, "why": _("主要訓練項目：路跑")} if road else \
        SH.plan_context(ds, status, today, monday, mode, _n(ctl_s.at(d_prev_sun) - atl_s.at(d_prev_sun)), gate)
    if lc.get("active"):
        try:
            dd = [asdict(s) for s in sessions]
            SH.apply(dd, lc, aet=aet, prefs=prefs, b2b=b2b, notes=notes, rates=tph, aet_measured=aet_meas,
                     walk=walk)
            sessions = [Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__}) for d in dd]
        except Exception as e:              # noqa: BLE001 — the plan must still build
            lc = {**lc, "error": type(e).__name__}

    # ---- 熱適應課 (engine/heat_plan.py): only before a hot A/B race ---------
    heat_info = {"active": False}
    try:
        from backend.engine import heat_plan as HP
        sd = [asdict(s) for s in sessions]
        heat_info = HP.apply(sd, events=status.plan.events, today=today, prefs=prefs, aet=aet, mode=mode,
                             kind=kind, notes=notes, aet_measured=aet_meas)
        if heat_info.get("active"):
            sessions = [Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__}) for d in sd]
    except Exception as e:                  # noqa: BLE001 — heat sessions never break the plan
        heat_info = {"active": False, "reason": _("熱適應資料讀取失敗（{err}）", err=type(e).__name__)}

    # ---- 下坡課 (engine/downhill.py, SP-99): 專項期 賽前第 9、6、3 週 before an A race with a clear descent
    from backend.engine import downhill as DH
    dh = DH.week_context(kind=kind, mode=mode, monday=monday, events=getattr(getattr(status, "plan", None),
                                                                              "events", None), phases=phs, road=road)
    if dh.get("active"):
        try:
            dd = [asdict(s) for s in sessions]
            DH.apply(dd, dh, prefs=prefs, rates=tph, notes=notes, hard_done=hard_done, b2b=b2b)
            sessions = [Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__}) for d in dd]
        except Exception as e:              # noqa: BLE001 — the plan must still build
            dh = {**dh, "error": type(e).__name__}

    # ---- 技術地形課 (engine/technical.py, SP-74): 越野跑 only; 基礎期 every other week's LSD,
    # 專項期 one a week out of an easy run (RPE 6–7 = a quality session: spacing + budget)
    tech = TECH.week_context(kind=kind, mode=mode, monday=monday, road=road, b2b=b2b)
    if tech.get("active"):
        try:
            dd = [asdict(s) for s in sessions]
            TECH.apply(dd, tech, hours=hours, rates=tph, prefs=prefs, notes=notes, hard_done=hard_done, user=user_q,
                       walk=walk)
            sessions = [Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__}) for d in dd]
        except Exception as e:              # noqa: BLE001 — the plan must still build
            tech = {**tech, "error": type(e).__name__}

    # ---- the day rules, after every pass that makes or moves a session (as projection.project_weeks):
    # the SP-86 strength stop, the 減量期 rules (SP-96), the post-race days (SP-98) and the B / C races
    # (SP-95) — rest days are placed above already (SP-82); illness last, it wins (SP-117)
    # 賽前停肌力 (SP-86): no strength in an A event's last STRENGTH_STOP_DAYS days (projection: the same)
    s_stops = strength_stops(getattr(getattr(status, "plan", None), "events", None) or (), monday, phs)
    sessions = drop_strength_before_a(sessions, s_stops, monday, notes)
    # 減量期規則 (SP-96): quality by days to the race, the last long run, no hard downhill / climbing
    sessions = taper_rules(sessions, t_ctx, monday, notes, road, today)
    # 賽後的日子 (SP-98): no run the first 2 days, ≤ 40 min the first week, flat after a big downhill
    sessions = PR_.apply(sessions, PR_.a_windows(phs, plan_events, monday), monday, notes, today, bmap)
    # B / C races (SP-95): the mini-taper days, the race as the week's key session, the B recovery,
    # a C race in place of a quality session or the long run; the hints
    sessions = PR_.bc_apply(sessions, plan_events, monday, notes, today, bmap, rate=tph.get("trail") or 60.0,
                            done_days={wdate(w).isoformat() for w in week_ws if category(w) in ENDURANCE},
                            factory=Session)
    b_ctl = None
    if any(getattr(e, "priority", None) == "B" and monday - dt.timedelta(weeks=PR_.B_CTL_WEEKS) <= e.start < monday
           for e in plan_events):
        try:
            b_ctl = [(dt.date.fromisoformat(r["date"]), r["ctl"])
                     for r in pmc(ds, monday - dt.timedelta(weeks=PR_.B_CTL_WEEKS), today)["series"]]
        except Exception:                   # noqa: BLE001 — the hint only
            b_ctl = None
    notes.extend(PR_.b_hints(plan_events, monday, b_ctl))
    # 生病 (SP-117, engine/injuries.illness_rule): a cold = Z1 recovery runs only, a fever = no run
    # until a day after the symptoms, then a recovery-pace first run
    try:
        from backend.engine import injuries as INJ
        sessions = illness_apply(sessions, INJ.load_events(), cap_txt)
    except Exception:                       # noqa: BLE001 — the plan must still build
        pass
    if t_ctx and kind == "taper":
        n = taper_climb_note(t_ctx, t_ref.get("climb"), hours / t_ref["hours"] if t_ref.get("hours") else None)
        if n:
            notes.append(n)
    b2b_suggestion = B2B.suggestion(b2b, monday, next((s.day for s in sessions if s.id == "long"), None),
                                    enabled=getattr(prefs, "b2b", True) is not False)
    race_sim = SP.sim_suggestion(sp, monday, max([longest28] + [s.minutes for s in sessions if s.id == "long"]),
                                 aet, tph["trail"], aet_meas)

    # ---- 平衡／腳踝 (engine/balance_plan.py, SP-120; owner 2026-10-05: part of strength): a 越野賽 /
    # 百岳 A race next — a block at the end of the strength session(s); with the strength gone (SP-86
    # stop, race week) one balance-only strength session. After the day rules, so they keep it.
    from backend.engine import balance_plan as BP
    try:
        bal = BP.week_context(a_evs, monday, kind, prefs)
        if bal.get("active"):
            dd = [asdict(s) for s in sessions]
            BP.attach(dd, bal)
            BP.ensure(dd, bal, [d for d in free if d.isoformat() not in bmap], allowed_fn)
            sessions = [Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__}) for d in dd]
    except Exception:                       # noqa: BLE001 — the plan must still build
        pass

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

    # ---- tests: suggested, not scheduled -----------------------------------
    test_suggestions = []
    if test_s is not None:
        ti = by.get("testing")
        test_suggestions.append({
            "kind": "cp", "protocol": test_s.get("protocol"), "title": test_s["title"], "minutes": test_s["minutes"],
            "reason": _("{why}：區間、TSS、賽事計算機都靠 CP", why=getattr(ti, "verdict", "") or _("門檻過期或沒測過")),
            "session": {k: test_s.get(k) for k in ("kind", "title", "minutes", "target", "detail", "source", "tss",
                                                   "protocol")}})
    if aet_due:
        a_s = AT.session(tt, AT.start_hr((est.get("aethr") or {}).get("value"), tt.get("lthr")),
                         AT.start_power(tt.get("cp")), getattr(prefs, "cap_weekday", None), aet_proto,
                         getattr(prefs, "long_cap", None) if prefs is not None else None)
        test_suggestions.append({
            "kind": "aet", "protocol": aet_proto, "title": a_s["title"], "minutes": a_s["minutes"],
            "reason": (gate.get("aet_test_reason") or {}).get("text") or _("AeT 需要重新確認"),
            "replaces_long": aet_proto == "xu90",
            "session": {k: a_s.get(k) for k in ("kind", "title", "minutes", "target", "detail", "source", "tss",
                                                "protocol")}})

    mode_label = {"base": _("基礎期"), "specific": _("專項期"), "taper": _("減量期"), "event": _("比賽週"),
                  "recovery": _("恢復期"), "transition": _("轉換期"), "rebuild": _("回量期"), "recovery_week": _("恢復週"),
                  "reentry": _("停訓後恢復期")}[mode]
    return {
        "week": {"start": monday.isoformat(), "end": sunday.isoformat(), "today": today.isoformat(),
                 "days_left": len(free)},
        "phase": kind, "mode": mode, "mode_label": mode_label,
        # 主要訓練項目 (engine/primary_sport.py): trail | road — projection.project_weeks follows it
        "primary_sport": sport,
        "mp_goal_pace_s": mp_goal,          # the A marathon's goal pace (s/km) for the MP segment; None = threshold
        "mp_race": mp_rc,                   # SP-75: the A race the MP plan counts back from (projection)
        "target": {"hours": hours, "tss": tss_target, "tss_per_hour": r_all, "ref_weeks": norm_wk},
        "done": {"hours": done_h, "tss": done_tss, "sessions": len(week_ws),
                 "activities": [activity_row(w, ds) for w in sorted(week_ws, key=lambda w: w.day)]},
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
                       # aet = the easy-run cap (not always an AeT: the 課表心率區間 Z2 top unless measured);
                       # its ± badge only when the cap IS the AeT estimate (no 課表心率區間)
                       "lthr_source": tt.get("lthr_source"), "aet": aet, "aet_source": aet_src,
                       "aet_pm": tt.get("aet_pm") if hrz is None else None,
                       "aet_measured": aet_meas, "easy_cap_label": cap_txt, "easy_cap_tip": _(EASY_CAP_TIP),
                       # 課表心率區間 (engine/hr_profile.plan_hr_zones): the push / step builders read it
                       "hr_model": hrz,
                       # 登山爬坡的心率上限 (hr_profile.walk_cap, SP-115): the walking sessions' texts and push
                       "walk_cap": walk},
        "notes": notes,
        # the quality gate (engine/quality_gate.py), so projection.project_weeks can
        # re-evaluate it for each projected week instead of copying this week's answer
        "quality_gate": {**gate, "levels": gate_levels, "allowed": allow_quality,
                         "this_week": dec["spec"][1] if allow_quality and dec["spec"] and kind == "base" else None,
                         "this_week_tracks": [it["track"] for it in dec.get("items") or []] if allow_quality else [],
                         "quality_n": q_n,
                         # a suggested test counts as the last one, so the projection waits ≥ 4 weeks
                         "aet_test": {"due": aet_due, "last": today.isoformat() if aet_due else tx.get("aet_last_test")}},
        # per-category TSS / h (projection shapes projected weeks with the same rates)
        "tss_per_category": tph,
        "prefs": PR.to_dict() if PR is not None else None,
        "blackout_days": [d.isoformat() for d in lost],
        "heat": heat_info,
        # CP / AeT tests that are due: suggestions with a day picker, never scheduled
        "test_suggestions": test_suggestions,
        # 停訓後的恢復期 (engine/reentry.py): the block in effect / ahead, for projection and the log
        "reentry": rp,
        # 轉換期 (SP-73): the pre-race level of the current / next 轉換期, for projection
        "transition_ref": tr_ref,
        # 連續兩天長天 (engine/b2b.py): this week's B2B / post-B2B state, for projection, adapt and the card
        "b2b": B2B.public(b2b),
        # a due B2B, suggested (never scheduled until accepted: api/plan_sessions suggestions)
        "b2b_suggestion": b2b_suggestion,
        # 陡坡健走（模擬負重） (engine/steep_hill.py): this week's stage / session, for the projection
        "steep_hill": SH.public(lc),
        # 專項期 (engine/specific_phase.py): the race target, this week's share, the GPX features
        "specific": SP.public(sp),
        # the race simulation 4–3 weeks out, suggested (api/plan_sessions: the floating box)
        "race_sim_suggestion": race_sim,
        # 技術地形課 (engine/technical.py, SP-74): this week's rule and the session it made
        "technical": TECH.public(tech),
        # 下坡課 (engine/downhill.py, SP-99): this week's countdown rule and the session it made
        "downhill": DH.public(dh),
        # 賽前停肌力 (SP-86): the A events' no-strength windows, for the projection
        "strength_stop": s_stops,
        # the A races ahead (engine/strength_plan.py): the projection's 肌力課依期別 and its 平衡／腳踝 block (SP-119, SP-120)
        "a_races": STP.a_races(a_evs, monday),
        # 減量期 (SP-96): the next A race's taper touching this week and the pre-taper level, for the projection
        "taper": {**t_ctx, **{f"pre_{k}": v for k, v in t_ref.items()}} if t_ctx else None,
        # the last 4 weeks' mean run climb (m): a projected 減量期's pre-taper climb (SP-96)
        "climb4": (_pre_taper(ds, monday + dt.timedelta(weeks=1), monday + dt.timedelta(weeks=1)) or {}).get("climb"),
    }


# ---- 賽前停肌力 (SP-86; Bompa & Buzzichelli p.184 / p.327) ------------------------------------
# No strength session in an A event's 減量期 + the race itself (STRENGTH_STOP_DAYS = the default
# 14-day taper; SP-96: the window follows the actual taper — 7 days before a 2–3 day 百岳, up to 21
# by 課表偏好, shorter when two A races are close, SP-90): the book's 「主要比賽」, so B / C events keep theirs (applying it to them would be 推估).
# Shared by week_plan and projection (week_plan's `strength_stop` → project_weeks), applied after
# the placement: a strength session the week placed (課表偏好 每週肌力 / 肌力日 included) on a day
# in the window is dropped — a reduction, so auto-adjust removes a stored one without asking.
STRENGTH_STOP_DAYS = 14


def strength_stops(events, since: dt.date, phases: Optional[list] = None) -> list[dict]:
    """[{"from", "to", "race", "days"}] (ISO, inclusive) of every A event ending on / after `since`:
    from its taper's first day (planning.taper_start of `phases`; without them taper_days) to its
    last day; `days` = the days before the race."""
    from backend.engine import planning as P
    out = []
    for e in events or ():
        if getattr(e, "priority", None) != "A" or e.end < since:
            continue
        t0 = P.taper_start(phases or (), e)
        out.append({"from": t0.isoformat(), "to": e.end.isoformat(), "race": e.name, "days": (e.start - t0).days})
    return sorted(out, key=lambda x: x["from"])


def _monday_of(iso: str) -> str:
    d = dt.date.fromisoformat(iso[:10])
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def illness_apply(ss: list, events: list, cap_txt: str = "") -> list:
    """The week's not-done sessions under a 生病 event of the 傷病紀錄 (SP-117; injuries.illness_rule):
    on an "off" day (fever, or the day after it) no run and no strength; on a "z1" day (cold
    symptoms) every run becomes a Z1 recovery run ≤ ILL_RUN_MAX_MIN (no interval, test, long run
    or strides); the first run on or after a "first" day (back after a fever) is a recovery-pace
    run ≤ FIRST_RUN_MAX_MIN. The week note is injuries.week_notes'. Session objects; `ss` unchanged
    without an illness."""
    from backend.engine import injuries as INJ
    if not any(INJ.is_illness(e) for e in events or ()):
        return ss

    def rule(s):
        return INJ.illness_rule(events, dt.date.fromisoformat(s.day)) if s.day and not s.done else None

    def recover(s, cap: float, title: str, why: str):
        m = int(s.minutes or 0)
        nm = int(min(m, cap) // 5 * 5) or m
        s.tss = round(float(s.tss or 0.0) * (nm / m if m else 1.0) * 0.8, 1)
        s.kind, s.title, s.minutes = "easy", title, nm
        s.target, s.detail, s.source = "", why, ""
        s.terrain = "road" if s.terrain in ("trail", "hike") else s.terrain      # flat, no climbing
        for k in _VARIANT_KEYS:
            setattr(s, k, None)
        s.protocol = None
    out = []
    for s in ss:
        r = rule(s)
        if r is None:
            out.append(s)
        elif r["rule"] == "off" and (s.kind in RUN_KINDS or s.kind == "strength"):
            continue
        elif r["rule"] == "z1" and s.kind in RUN_KINDS:
            recover(s, INJ.ILL_RUN_MAX_MIN, _("恢復跑（心率 1 區）"),
                    _("{label}：心率 1 區{cap}，不加速、不衝坡；{src}", label=r["label"],
                      cap=_("（不超過{cap}）", cap=cap_txt) if cap_txt else "", src=r["src"]))
            out.append(s)
        else:
            out.append(s)
    for e in events:
        # back after a fever: the first run on or after the day after the symptoms (in this week)
        if not (INJ.is_illness(e) and e.get("illness") == "fever" and e.get("status") == "resolved"
                and e.get("resolved_date")):
            continue
        f = (dt.date.fromisoformat(e["resolved_date"][:10]) + dt.timedelta(days=INJ.FEVER_WAIT_DAYS)).isoformat()
        days = sorted(s.day for s in out if s.day)
        if not days:
            continue
        wk = dt.date.fromisoformat(_monday_of(days[0]))
        if not wk.isoformat() <= f <= (wk + dt.timedelta(days=6)).isoformat() or \
                any(s.done and s.kind in RUN_KINDS and s.day and s.day >= f for s in out):
            continue
        nxt = next((s for s in sorted(out, key=lambda x: x.day or "9") if not s.done and s.kind in RUN_KINDS
                    and s.day and s.day >= f), None)
        if nxt is not None:
            recover(nxt, INJ.FIRST_RUN_MAX_MIN, _("恢復跑（發燒後第一次）"),
                    _("發燒好了之後的第一次跑：恢復配速、心率 1 區{cap}，之後照停跑天數排；{src}",
                      cap=_("（不超過{cap}）", cap=cap_txt) if cap_txt else "", src=_(INJ.SRC_FEVER)))
    return out


def drop_strength_before_a(ss: list, stops: list, monday: dt.date, notes: Optional[list] = None) -> list:
    """`ss` without the not-done strength sessions on a day in an A event's no-strength window
    (strength_stops; one with no day is dropped when the window touches the week), and a week
    note when any went. Session objects or dicts."""
    days = [(monday + dt.timedelta(days=i)).isoformat() for i in range(7)]
    hit = {d: x for x in stops or () for d in days if x["from"] <= d <= x["to"]}
    if not hit:
        return ss
    g = (lambda s, k: s.get(k)) if ss and isinstance(ss[0], dict) else getattr
    out, gone = [], []
    for s in ss:
        day = g(s, "day")
        if g(s, "kind") == "strength" and not g(s, "done") and (day in hit or not day):
            gone.append(s)
        else:
            out.append(s)
    if gone and notes is not None:
        x = hit[min(hit)]
        notes.append({"level": "info", "src": "strength",
                      "text": _("A 賽事「{race}」前 {days} 天不排肌力（{start} 起，含比賽週；"
                                "課表偏好的每週肌力／肌力日也一樣）：把體力留給比賽——長距離耐力項目主要比賽前 2 週停肌力，"
                                "停 4 週以上才會退步（Bompa & Buzzichelli）",
                                race=x["race"], days=x.get("days", STRENGTH_STOP_DAYS), start=x["from"][5:])})
    return out


# ---- 減量期規則 (SP-96; periodization-cross-sport.md §4.5.1, §4.5.2, §6.1「SP-96 減量期」) ----------
# Shared by week_plan and projection, applied after the placement (like the strength stop) to every
# not-done session on a day of the next A race's 減量期 (planning.taper_start — its real length:
# 14 days, 7 before a 2–3 day 百岳, up to 21 by 課表偏好, shorter when two A races are close):
#   * the last FULL quality session ≥ TAPER_FULL_Q_DAYS out (10–14 = the taper's first week), a
#     familiar specific one — road: the 專項期 flat threshold 2×15′; trail / 百岳: uphill tempo, not
#     short hard climbing (Gaudette, Koop — 教練級 [420][421]); never a new or maximal session;
#   * after it the same kind, shorter (one segment fewer, Koop [419], 教練級);
#   * the last short intensity TAPER_SHORT_Q_DAYS (3–5) out at half the volume (world-class runners,
#     已驗證 [416]; recreational runners 推估); nothing hard in the last 2 days;
#   * the last long run ≥ TAPER_LONG_DAYS (7) out and ≤ TAPER_LONG_MAX_MIN easy ([421], 教練級);
#     ≥ TAPER_LONG_SORE_DAYS (14) when the long day leaves you clearly sore — detected as an A race
#     descending ≥ SORE_DESCENT_M_PER_KM (its long days copy that descent, specific_phase) ([422]);
#   * no hard downhill, no short hard climbing ([422]; 江晏慶 [454], 教練級): the 長爬坡反覆 (its
#     run-down at race grade) becomes a flat easy run.
# The run count is kept (taper_easy_count): ≥ the pre-taper runs − TAPER_RUN_DROP, each shorter —
# Wang 2023 (keeping the frequency improved, cutting it didn't), Mujika (≤ 20 % fewer), Koop /
# Heward / Gaudette (教練級). The weekly climb follows the minutes (trail / 百岳 A race, 江晏慶
# [454]; the same share is 推估): taper_climb_note.
TAPER_FULL_Q_DAYS = 10
TAPER_SHORT_Q_DAYS = (3, 5)
TAPER_LONG_DAYS = 7
TAPER_LONG_SORE_DAYS = 14
TAPER_LONG_MAX_MIN = 90
SORE_DESCENT_M_PER_KM = 40.0        # 推估: steeper than most trail races' average descent (~25–35 m/km)
TAPER_RUN_DROP = 1
TAPER_RUN_MIN = 20                  # no taper easy run shorter (plan_prefs.MIN_EASY)
SRC_TAPER_WEEK = ("減量期課表：最後一次完整強度課賽前 10–14 天、之後保留種類縮短、最後一次短強度賽前 3–5 天量減半、"
                  "最後一次長跑賽前 ≥ 7 天且 ≤ 90 分（Gaudette、Koop、Mujika；教練級／世界級選手）")
TAPER_FULL = {
    "road": dict(kind="quality", title="有氧間歇 2×15 分（平路）", minutes=60, terrain="road",
                 detail="減量期最後一次完整強度課：專項期熟悉的課，不加量、不做新的課；休 3 分慢跑；暖身 15 分、緩和 10 分",
                 tss=60 / 60 * 70),
    "trail": dict(kind="quality", title="上坡節奏跑 3×10 分（緩坡）", minutes=60,
                  detail="減量期最後一次完整強度課：4–8% 緩坡、3 區（88–95% CP），慢跑下來恢復、下坡放鬆不衝；"
                         "暖身 15 分、緩和 10 分",
                  tss=60 / 60 * 65),
}
TAPER_CUT = {
    "road": dict(kind="quality", title="有氧間歇 2×10 分（平路）", minutes=50, terrain="road",
                 detail="減量期：同一種課、量縮短（少一段、每段變短）；休 3 分慢跑；暖身 15 分、緩和 10 分",
                 tss=50 / 60 * 65),
    "trail": dict(kind="quality", title="上坡節奏跑 2×10 分（緩坡）", minutes=50,
                  detail="減量期：同一種課、量縮短（少一段）；4–8% 緩坡、3 區，下坡放鬆不衝；暖身 15 分、緩和 10 分",
                  tss=50 / 60 * 62),
}
TAPER_SHORT = dict(kind="quality", title="短強度 2×3 分（平路）", minutes=35, terrain="road",
                   detail="賽前 3–5 天最後一次短強度：量減半，98–102% CP，休 2 分慢跑；暖身 15 分、緩和 10 分",
                   tss=35 / 60 * 60)
_VARIANT_KEYS = ("variant_key", "rung_key", "equiv", "swap", "swap_reason", "variant_reps", "variant_blocks",
                 "variant_adj", "progress", "steps", "distance_km", "climb_m")


def taper_context(phases: list, events, monday: dt.date) -> Optional[dict]:
    """The next A race whose 減量期 touches the week of `monday` (planning.taper_start):
    {"race", "id", "start", "taper_start", "days", "trail", "sore", "long_days"}; None without one."""
    from backend.engine import planning as P
    sunday = monday + dt.timedelta(days=6)
    for e in sorted((e for e in events or () if getattr(e, "priority", None) == "A" and e.start > monday),
                    key=lambda e: e.start):
        t0 = P.taper_start(phases or (), e)
        if t0 > sunday:
            return None
        trail = e.kind != "road"
        sore = trail and _descent_per_km(e) >= SORE_DESCENT_M_PER_KM
        return {"race": e.name, "id": e.id, "start": e.start.isoformat(), "taper_start": t0.isoformat(),
                "days": (e.start - t0).days, "trail": trail, "sore": sore,
                "long_days": TAPER_LONG_SORE_DAYS if sore else TAPER_LONG_DAYS}
    return None


def _descent_per_km(e) -> float:
    """The race's descent per km: the stored GPX's, else the climb's (race_refs.plan_course assumes
    descent = climb)."""
    try:
        from backend.engine.panels import race_refs as RR
        t = RR.course_of(e)["totals"]
        return float(t.get("loss_m") or 0.0) / float(t["km"]) if t.get("km") else 0.0
    except Exception:                       # noqa: BLE001 — the plan must still build
        return float(e.climb_per_km or 0.0)


def taper_long_minutes(total_min: float) -> float:
    """A 減量期 week's long run: ≤ TAPER_LONG_MAX_MIN, ~30 % of the week (≥ 45 min, ≤ half the week)."""
    m = min(float(TAPER_LONG_MAX_MIN), max(45.0, 0.30 * total_min))
    return min(m, 0.5 * total_min) if total_min >= 120 else m


def taper_easy_count(left: float, pre_runs: Optional[float], other_runs: int) -> int:
    """The 減量期 week's easy runs: easy_count, raised so the week keeps ≥ the pre-taper run count
    − TAPER_RUN_DROP (each run shorter, ≥ TAPER_RUN_MIN)."""
    n = easy_count(left, "taper")
    if pre_runs:
        want = int(round(pre_runs)) - TAPER_RUN_DROP - int(other_runs)
        n = max(n, min(want, int(left // TAPER_RUN_MIN), 6))
    return n


def taper_climb_note(tc: Optional[dict], pre_climb: Optional[float], share: Optional[float]) -> Optional[dict]:
    """A trail / 百岳 A race's 減量期 week: the weekly climb follows the minutes (江晏慶 [454]; the
    same share is 推估) — a week note; None for a road race or without the pre-taper climb."""
    if not tc or not tc.get("trail") or not pre_climb or not share:
        return None
    return {"level": "info", "src": "taper",
            "text": _("減量期爬升跟著時數一起減：減量前每週約 {pre:.0f} m × {share:.0%} → 這週約 {m:.0f} m（推估）；"
                      "不排短距離高強度爬坡、高強度下坡（江晏慶，教練級）",
                      pre=pre_climb, share=share, m=pre_climb * share)}


def taper_rules(ss: list, tc: Optional[dict], monday: dt.date, notes: Optional[list] = None,
                road: bool = False, today: Optional[dt.date] = None) -> list:
    """`ss` (Session objects or dicts, placed) with the 減量期 rules applied to the not-done sessions on
    a day of `tc`'s taper (taper_context): the quality sessions by days to the race (full ≥ 10, the
    same kind shorter, the half-volume short one 3–5 days out — moved onto an easy day there when it
    landed later, else an easy run), the long run ≤ 90 min and ≥ 7 (14 when sore) days out, the
    長爬坡反覆 a flat easy run. Notes say what changed."""
    if not tc or not ss:
        return ss
    is_d = isinstance(ss[0], dict)

    def g(s, k):
        return s.get(k) if is_d else getattr(s, k, None)

    def put(s, **kw):
        for k, v in kw.items():
            if is_d:
                s[k] = v
            else:
                setattr(s, k, v)

    race, t0 = dt.date.fromisoformat(tc["start"]), dt.date.fromisoformat(tc["taper_start"])
    lo = today or monday
    terr = "road" if road or not tc.get("trail") else "trail"
    said: list[str] = []

    def out_of(s) -> Optional[int]:
        if g(s, "done") or not g(s, "day"):
            return None
        d = dt.date.fromisoformat(g(s, "day"))
        return (race - d).days if t0 <= d < race else None

    def as_easy(s, cap: float, why: str):
        m = int(g(s, "minutes") or 0)
        nm = int(min(m, cap) // 5 * 5) or m
        n_easy = sum(1 for x in ss if g(x, "kind") == "easy")
        put(s, id=f"easy{n_easy + 1}" if g(s, "kind") != "easy" else g(s, "id"), kind="easy", title=_("輕鬆跑"),
            minutes=nm, tss=round(float(g(s, "tss") or 0.0) * (nm / m if m else 1.0) * 0.8, 1),
            detail=why, terrain="road" if tc.get("sore") or terr == "road" else g(s, "terrain"),
            **{k: None for k in _VARIANT_KEYS})

    def as_q(s, tmpl: dict):
        put(s, **{**{k: None for k in _VARIANT_KEYS}, "source": SRC_TAPER_WEEK, "terrain": None, **tmpl})

    touched = False
    for s in sorted(ss, key=lambda x: g(x, "day") or "9"):
        out = out_of(s)
        if out is None:
            continue
        touched = True
        k, sid = g(s, "kind"), g(s, "id")
        if k == "long" or sid in ("long", "long2"):
            if out < tc["long_days"]:
                as_easy(s, 60, _("減量期：賽前 {n} 天內不跑長跑", n=tc["long_days"]))
                said.append(_("賽前 {n} 天內不跑長跑", n=tc["long_days"]))
            elif (g(s, "minutes") or 0) > TAPER_LONG_MAX_MIN or tc.get("sore"):
                m = int(g(s, "minutes") or 0)
                nm = min(m, TAPER_LONG_MAX_MIN)
                put(s, minutes=nm, tss=round(float(g(s, "tss") or 0.0) * nm / m, 1) if m else g(s, "tss"),
                    detail=_("減量期最後一次長跑：≤ {max} 分輕鬆跑", max=TAPER_LONG_MAX_MIN)
                    + (_("，平路或緩坡、不跑長下坡") if tc.get("sore") else ""), climb_m=None, distance_km=None)
                if tc.get("sore"):
                    put(s, terrain="road")
                said.append(_("最後一次長跑 ≤ {max} 分", max=TAPER_LONG_MAX_MIN))
        elif k == "quality":
            if out >= TAPER_FULL_Q_DAYS:
                as_q(s, TAPER_FULL[terr])
            elif out > TAPER_SHORT_Q_DAYS[1] and (race - monday).days > 7:
                as_q(s, TAPER_CUT[terr])
            else:
                if not TAPER_SHORT_Q_DAYS[0] <= out <= TAPER_SHORT_Q_DAYS[1]:
                    # race week: move it onto an easy day 3–5 days out when there is one
                    swap = next((x for x in ss if g(x, "kind") == "easy" and not g(x, "done") and g(x, "day")
                                 and dt.date.fromisoformat(g(x, "day")) >= lo
                                 and TAPER_SHORT_Q_DAYS[0] <= (race - dt.date.fromisoformat(g(x, "day"))).days
                                 <= TAPER_SHORT_Q_DAYS[1]), None)
                    if swap is not None:
                        d1, d2 = g(s, "day"), g(swap, "day")
                        put(s, day=d2)
                        put(swap, day=d1)
                        out = (race - dt.date.fromisoformat(d2)).days
                if TAPER_SHORT_Q_DAYS[0] <= out <= TAPER_SHORT_Q_DAYS[1]:
                    as_q(s, TAPER_SHORT)
                elif out > TAPER_SHORT_Q_DAYS[1]:
                    as_q(s, TAPER_CUT[terr])
                else:
                    as_easy(s, 40, _("賽前 {n} 天內不排強度課：輕鬆跑", n=TAPER_SHORT_Q_DAYS[0]))
                    said.append(_("賽前 {n} 天內不排強度課", n=TAPER_SHORT_Q_DAYS[0]))
        elif sid == "climb":
            # the 長爬坡反覆 runs down at race grade: no hard downhill in the taper
            as_easy(s, g(s, "minutes") or 60, _("減量期不排長爬坡反覆、高強度下坡：平路或緩坡輕鬆跑"))
            said.append(_("不排長爬坡反覆"))
    if notes is not None and touched:
        notes.append({"level": "info", "src": "taper",
                      "text": _("減量期（A 賽事「{race}」前 {days} 天）：跑步次數照舊、每次變短；最後一次完整強度課在賽前 "
                                "10–14 天，之後同一種課縮短，賽前 3–5 天一次量減半的短強度；最後一次長跑賽前 ≥ {long} 天、"
                                "≤ {max} 分", race=tc["race"], days=tc["days"], long=tc["long_days"],
                                max=TAPER_LONG_MAX_MIN)
                      + ("（" + "、".join(dict.fromkeys(said)) + "）" if said else "")})
    return ss
