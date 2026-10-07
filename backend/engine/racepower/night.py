"""
Night on race day: the segments run in the dark, and an optional night slowdown (SP-254).

docs/research/night-and-sleep.md §2.1 / §4.2 單 1.

  * Sunrise / sunset of the event days come from /weather (`sun`: Open-Meteo's
    daily values, else NOAA's formula); a GPX course without them gets NOAA's
    formula at its start point (weather.sun_times, UTC+8). Dark = before the
    first sunrise, between each sunset and the next sunrise, after the last
    sunset.
  * Each segment's clock span is cold.points() (needs a start time). Its dark
    share = the part of the span in the dark; `night` = dark share ≥ ½ (the
    segment's midpoint is in the dark).
  * Night slowdown (the page offers 0 / 5 / 10 / 15 %, default 0 %, owner
    2026-10-06; 推估): each segment's moving time × (1 + p × its dark share),
    so only the dark part gets longer. The ETAs move, so the dark shares are
    recomputed until the cumulative times move < 1 s (≤ 8 passes). 0 % leaves
    the plan exactly as it was. Target-time mode is not slowed (the target is
    the total). Power, heat and effort stay as solved (推估: the slowdown is
    footing and sight, not a lower power).
  * No study separates how much darkness alone slows a runner; Brager 2020's
    "after sunset, runners slowed down by 35.9%" (100 miles) mixes fatigue and
    sleepiness, so it is not used as a night factor. Not learnt from the
    athlete's own night runs (owner 2026-10-06: not for now).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.engine.racepower import cold as CD
from backend.i18n import _

SLOW_CHOICES = (0.0, 5.0, 10.0, 15.0)   # % (推估, night-and-sleep.md §4.2 單 1)
MAX_PASSES = 8
TOL_S = 1.0


def dark_spans(sun: Optional[list[dict]]) -> list[tuple[dt.datetime, dt.datetime]]:
    """The dark intervals of the sun rows ({date, sunrise, sunset}, naive local)."""
    rows = []
    for r in sun or []:
        try:
            rows.append((dt.datetime.fromisoformat(str(r["sunrise"])[:16]), dt.datetime.fromisoformat(str(r["sunset"])[:16])))
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort()
    if not rows:
        return []
    out = [(dt.datetime.min, rows[0][0])]
    for (_r0, s0), (r1, _s1) in zip(rows, rows[1:]):
        out.append((s0, r1))
    out.append((rows[-1][1], dt.datetime.max))
    return out


def dark_share(begin: dt.datetime, end: dt.datetime, spans) -> float:
    """The share of [begin, end] in the dark (a zero-length span: 1 or 0 by its point)."""
    if end <= begin:
        return 1.0 if any(a <= begin < b for a, b in spans) else 0.0
    tot = (end - begin).total_seconds()
    dark = sum(max(0.0, (min(end, b) - max(begin, a)).total_seconds()) for a, b in spans)
    return min(1.0, dark / tot)


def course_sun(course: dict, date: Optional[str], days: int = 1) -> Optional[list[dict]]:
    """NOAA sunrise / sunset at the GPX course's start point (no /weather `sun`)."""
    from backend.engine.racepower import weather as WX
    pr = course.get("profile") or {}
    lat, lon = (pr.get("lat") or [None])[0], (pr.get("lon") or [None])[0]
    try:
        d0 = dt.date.fromisoformat(str(date)[:10])
    except (TypeError, ValueError):
        return None
    if lat is None or lon is None:
        return None
    return WX.sun_rows(float(lat), float(lon), {d0 + dt.timedelta(days=i) for i in range(max(1, days))})


def _rescale(plan: dict, factors: list[float], start_time: Optional[str], stops) -> None:
    """Segment times × factors (of the unslowed times in plan["_night_t0"]), cumulative times,
    ETAs, speeds and the summary totals rebuilt in place."""
    from backend.engine.racepower import planner as PL
    segs, t0 = plan["segments"], plan["_night_t0"]
    hike = plan.get("type") == "baiyue"
    sm = plan["summary"]
    ratio = float(sm.get("moving_ratio") or 1.0) or 1.0
    cum, day, day_start = 0.0, None, 0.0
    for s, base, f in zip(segs, t0, factors):
        old = s["t"]
        s["t"] = base * f
        if hike and int(s.get("day") or 1) != day:
            day, day_start = int(s.get("day") or 1), cum
        cum += s["t"]
        s["cum_s"] = cum
        k = old / s["t"] if s["t"] else 1.0              # speed scale
        for key in ("pace_s_per_km", "gap_pace_s_per_km"):
            if s.get(key) is not None:
                s[key] = s[key] / k
        for key in ("speed_ms", "speed_kmh", "vert_m_per_h"):
            if s.get(key) is not None:
                s[key] = s[key] * k
        s["eta"] = PL._clock(start_time, ((cum - day_start) / ratio if hike else cum) + PL._stops_before(stops, s["end_km"]))
    added = cum - sum(t0)
    base_T = plan["_night_T0"]
    T = base_T + added
    km = sm.get("km") or 0.0
    sm["time_s"] = T
    if km and sm.get("pace_s_per_km") is not None:
        sm["pace_s_per_km"] = T / km
    if hike:
        sm["capacity_time_s"] = T
        sm["clock_s"] = plan["_night_clock0"] + added / ratio
        if plan.get("_night_ep") and T:
            sm["ep_per_h"] = sm["eph_personal"] = plan["_night_ep"] / (T / 3600.0)
        for d in plan.get("days") or []:
            hh = sum(s["t"] for s in segs if int(s.get("day") or 1) == d["day"]) / 3600.0
            d["moving_h"], d["clock_h"] = hh, hh / ratio
    else:
        sm["finish_eta"] = PL._clock(start_time, T + PL._stops_before(stops, km + 1))
        if sm.get("time_total_s") is not None:
            sm["time_total_s"] = plan["_night_total0"] + added
        if sm.get("time_total_range_s"):
            sm["time_total_range_s"] = [x + added for x in plan["_night_range0"]]


def _shift_snow(plan: dict, added: float) -> None:
    """SP-252 × SP-254: the 百岳 積雪 line (summary.snow, computed by the planner before the night
    slowdown) moves with the night's added time, so its moving / clock numbers match the plan's,
    and the snow-free base gets the same addition — the snow difference stays snow only. The high
    end gets the same addition too (推估: a longer trip could be a little more in the dark)."""
    sn = (plan.get("summary") or {}).get("snow")
    if not sn or not added:
        return
    ratio = float(plan["summary"].get("moving_ratio") or 1.0) or 1.0
    for k in ("base_s", "time_s", "time_hi_s"):
        if sn.get(k) is not None:
            sn[k] += added
    for k in ("clock_s", "clock_hi_s"):
        if sn.get(k) is not None:
            sn[k] += added / ratio


def apply(plan: dict, *, date: Optional[str], start_time: Optional[str], stops=None,
          sun: Optional[list[dict]] = None, slow_pct: float = 0.0, mode: str = "auto") -> dict:
    """Marks each segment `night` / `dark_share` and, with slow_pct > 0 (not in target-time
    mode), lengthens the dark part of each segment; sets plan["night"] = {slow_pct, applied,
    added_s, n, ranges, sun, badge}, None without a start time, segments or sun rows."""
    spans = dark_spans(sun)
    segs = plan.get("segments") or []
    if not spans or not segs or CD._start(date, start_time) is None:
        plan["night"] = None
        return plan
    pct = max(0.0, min(15.0, float(slow_pct or 0.0)))
    slow = pct > 0 and mode != "time"
    days = len(plan.get("days") or []) or 1

    def shares():
        return [dark_share(p["begin"], p["end"], spans) for p in CD.points(plan, date, start_time, stops, days)]
    sh = shares()
    added = 0.0
    if slow:
        sm = plan["summary"]
        plan["_night_t0"] = [s["t"] for s in segs]
        plan["_night_T0"] = sm["time_s"]
        plan["_night_clock0"] = sm.get("clock_s")
        plan["_night_total0"] = sm.get("time_total_s")
        plan["_night_range0"] = list(sm.get("time_total_range_s") or [])
        plan["_night_ep"] = sm["ep_per_h"] * sm["time_s"] / 3600.0 if sm.get("ep_per_h") else None
        prev = [s["cum_s"] for s in segs]
        for _n in range(MAX_PASSES):
            _rescale(plan, [1.0 + pct / 100.0 * x for x in sh], start_time, stops)
            sh = shares()
            cur = [s["cum_s"] for s in segs]
            moved = max(abs(a - b) for a, b in zip(cur, prev))
            prev = cur
            if moved < TOL_S:
                break
        added = sum(s["t"] for s in segs) - sum(plan["_night_t0"])
        _shift_snow(plan, added)
        for k in ("_night_t0", "_night_T0", "_night_clock0", "_night_total0", "_night_range0", "_night_ep"):
            plan.pop(k, None)
    for s, x in zip(segs, sh):
        s["dark_share"] = x
        s["night"] = x >= 0.5
    pts = CD.points(plan, date, start_time, stops, days)
    ranges = []
    for n, (s, p) in enumerate(zip(segs, pts)):
        if not s["night"]:
            continue
        if ranges and ranges[-1]["_n"] == n - 1:
            ranges[-1].update(end_km=s["end_km"], to=p["end"], _n=n)
        else:
            ranges.append({"start_km": s["start_km"], "end_km": s["end_km"], "from": p["begin"], "to": p["end"], "_n": n})
    for r in ranges:
        r.pop("_n")
        r["from"], r["to"] = CD.clock(r["from"], date), CD.clock(r["to"], date)
    plan["night"] = {"slow_pct": pct, "applied": slow, "added_s": added, "n": sum(1 for s in segs if s["night"]),
                     "ranges": ranges, "sun": sun, "badge": "推估" if slow else None}
    return plan


def hint(night: Optional[dict], kind: str) -> Optional[dict]:
    """The night part of the attention line (trail / 百岳 only; road races are lit): None
    without night segments."""
    if not night or not night.get("n") or kind not in ("trail", "baiyue"):
        return None
    return night


def where(r: dict) -> str:
    return _("km {a:.1f}–{b:.1f} 天黑（{t0}–{t1}）", a=r["start_km"], b=r["end_km"], t0=r["from"], t1=r["to"])
