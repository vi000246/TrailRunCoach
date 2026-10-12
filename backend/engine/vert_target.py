"""
每週爬升量目標 (SP-87; docs/research/trail-terrain-and-climb-sessions.md §7, owner 2026-10-05 / 10-12):
before the next A race with a climb, each week of the plan gets a climb target — Uphill Athlete's
absolute rule (Big Vert 20-week plan; Steve House 2024: 「from 50 % to the biggest week at 100 %」), no
Koop density cap. R = the race's single-day climb (the biggest day: race_refs.course_of — the stored
GPX, else the trip's own per-day numbers, else the event's climb; a 連續 race is one day).

    賽前第 20 週 before        none (only the actual climb is shown) — the plans start 20 weeks out
    賽前第 20 → peak week     50 % of R, straight up to the peak: long race 95 %, short 110 %
                             (UA: 90–100 % / over 100 %; 95 % the middle, 110 % 推估)
    ≤ +10 % a week           over the last normal week's target; the first week of the chain over the
                             mean of the last 4 complete weeks' actual climb (when there was any) — a
                             late start or a low base lowers the peak (「上不去就照 10 % 走」)
    a recovery week          50 % of the week before (UA: 「at most −50 %」, the limit); the next week
                             goes on from the last normal week
    減量期 week 1             75 % of the peak (UA: the first taper week −25 %)
    the race week            40 % of the peak (推估; UA gives none)
    100 mi and more          a 3-week taper 75 → 55 → 40 % (55 % 推估), the peak a week earlier

Long race: a predicted finish ≥ LONG_HOURS (planning.event_hours: the race calculator's time, else the
plan's 預估移動時間); without a time ≥ LONG_KM km (both 推估, §7.3). 100 mi: ≥ HUNDRED_KM km.

Display only (owner): the 課表's week rows show 目標 / 已排 / 已完成 and how much is missing
(api/plan_sessions._week_rows); nothing is added to the plan when the planned sessions fall short.
Not for 主要訓練項目 路跑 or a road race. The weeks come from week_plan (this week) and the projection
(their mode says the recovery weeks), so the target follows the same plan the 課表 shows.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

START_WEEKS = 20                    # Uphill Athlete's 20-week plan: the first week with a target
START_SHARE = 0.50                  # of R at 賽前第 20 週
PEAK_LONG, PEAK_SHORT = 0.95, 1.10  # the peak's share of R (UA 90–100 % / > 100 %; 推估 within / above)
STEP_MAX = 0.10                     # ≤ +10 % a week (UA)
RECOVERY_SHARE = 0.50               # a recovery week: half the week before (UA's limit)
TAPER = {2: (0.75, 0.40), 3: (0.75, 0.55, 0.40)}   # taper weeks → share of the peak, first week first
LONG_HOURS = 6.0                    # 推估 (§7.3): a predicted ≥ 6 h finish is a long race
LONG_KM = 50.0                      # 推估: without a time, ≥ 50 km
HUNDRED_KM = 160.0                  # ≈ 100 mi: the 3-week taper
FOOT = ("road", "trail", "hike", "walk")


def _d(x) -> Optional[dt.date]:
    if not x:
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def weeks_out(start: dt.date, monday: dt.date) -> int:
    """賽前第 n 週 of the week starting `monday` (1 = the race week)."""
    return max(1, -(-(start - monday).days // 7))


def race_climb(e) -> Optional[float]:
    """R: the race's biggest single-day climb (m), None without one."""
    from backend.engine.panels import race_refs as RR
    try:
        days = RR.course_of(e, RR.stored_course).get("days") or []
        r = max((float(x.get("gain_m") or 0.0) for x in days), default=0.0)
    except Exception:                       # noqa: BLE001 — no distance / a broken GPX: the event's own
        r = float(getattr(e, "climbing_m", None) or 0.0) / max(1, int(getattr(e, "split_days", 1) or 1))
    return r if r >= 1.0 else None


def is_long(e) -> bool:
    """A long race (the peak at 95 % of R): a predicted ≥ LONG_HOURS, else ≥ LONG_KM km (推估)."""
    from backend.engine import planning as PL
    try:
        h = PL.event_hours(e)
    except Exception:                       # noqa: BLE001
        h = None
    if h:
        return float(h) >= LONG_HOURS
    return float(e.distance_km or 0.0) >= LONG_KM


def hundred(e) -> bool:
    return float(e.distance_km or 0.0) >= HUNDRED_KM


def _next_a(events, monday: dt.date):
    return min((e for e in events or () if getattr(e, "priority", None) == "A" and e.start >= monday),
               key=lambda e: e.start, default=None)


def avg_before(acts: list, monday: dt.date, weeks: int = 4) -> Optional[float]:
    """The mean weekly climb (m) of the foot activities in the `weeks` complete weeks before `monday`
    (activity rows: date, category, climbing_m); None when none of them climbed."""
    a, b = (monday - dt.timedelta(weeks=weeks)).isoformat(), monday.isoformat()
    tot = sum(float(x.get("climbing_m") or 0.0) for x in acts or ()
              if a <= str(x.get("date") or "") < b and x.get("category") in FOOT)
    return tot / weeks if tot > 0 else None


def plan(weeks: list[dict], events, avg4: Optional[float] = None, road: bool = False) -> dict:
    """{monday iso: info} for `weeks` ([{"start", "mode"}], in order — this week first). info: {"target"
    (m, to 10; None before 賽前第 20 週), "race", "r", "share" (of R), "weeks_out", "long", "recovery",
    "capped" (under the straight line because of the 10 % rule), "taper"}; a week with no A race
    ahead with a climb is left out. `avg4`: the mean weekly climb of the 4 complete weeks before the
    first week (avg_before), the first step's base. `road` (主要訓練項目 路跑): {}."""
    if road:
        return {}
    out: dict = {}
    chain: dict = {}                        # race id → {"last", "peak"} (the last normal week, the top)
    for w in weeks or ():
        monday = _d(w.get("start"))
        if monday is None:
            continue
        e = _next_a(events, monday)
        if e is None or e.kind == "road":
            continue
        r = race_climb(e)
        if r is None:
            continue
        n = weeks_out(e.start, monday)
        lg, taper_n = is_long(e), 3 if hundred(e) else 2
        info = {"race": {"id": e.id, "name": e.name, "start": e.start.isoformat()}, "r": round(r),
                "weeks_out": n, "long": lg, "recovery": w.get("mode") == "recovery_week", "target": None,
                "capped": False, "taper": n <= taper_n}
        if n > START_WEEKS:
            out[monday.isoformat()] = info
            continue
        c = chain.setdefault(e.id, {"last": None, "peak": None})
        peak_share = PEAK_LONG if lg else PEAK_SHORT
        peak_w = taper_n + 1

        def line(k: int) -> float:
            return r * (START_SHARE + (peak_share - START_SHARE) * (START_WEEKS - k) / (START_WEEKS - peak_w))

        def capped(v: float) -> tuple[float, bool]:
            base = c["last"] if c["last"] is not None else avg4
            if base and base > 0 and v > base * (1 + STEP_MAX):
                return base * (1 + STEP_MAX), True
            return v, False

        if n > taper_n:
            t, info["capped"] = capped(line(n))
            if info["recovery"]:
                t = RECOVERY_SHARE * (c["last"] if c["last"] is not None else t)
            else:
                c["last"] = t
                c["peak"] = max(c["peak"] or 0.0, t)
        else:
            top = c["peak"]
            if top is None:                 # the plan starts inside the taper: the peak it would have had
                top, info["capped"] = capped(line(peak_w))
            t = top * TAPER[taper_n][taper_n - n]
        info.update(target=round(t, -1), share=round(t / r, 3))
        out[monday.isoformat()] = info
    return out


def annotate(cur: dict, weeks: list[dict], events, acts: list) -> dict:
    """plan() over week_plan's week (`cur`) and the projected `weeks`, with the 4 weeks before this one
    from the activity rows `acts`; {} for 主要訓練項目 路跑. Never raises (the 課表 must still load)."""
    try:
        monday = _d((cur.get("week") or {}).get("start"))
        rows = [{"start": monday.isoformat(), "mode": cur.get("mode")}] + \
            [{"start": w.get("start"), "mode": w.get("mode")} for w in weeks or ()]
        return plan(rows, events, avg_before(acts, monday), road=cur.get("primary_sport") == "road")
    except Exception:                       # noqa: BLE001 — display only
        return {}
