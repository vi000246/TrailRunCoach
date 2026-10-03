"""The demo athlete's year: events, phases, thresholds and the day-by-day
training schedule (docs/plans/auth-and-demo.plan.md §3.2). Everything is
counted back from `anchor` (the last day of data) and drawn from one seeded
generator, so a seed + anchor always gives the same season.

Targets: 半程馬拉松 (~30 weeks ago), 百岳 3 日 (~16 weeks ago, with two 2-day
warm-up trips), 越野 50K (A race, ~4 weeks after the anchor, so the plan is
live: the anchor falls in its 專項期) with a B race (貓空越野 17K, a real course) three
weeks before it. 4–6 sessions a week, every 4th week
easier, a 10-day 感冒 gap, a few missed sessions. The last three weeks hold
the showcase activities (one each): 間歇課, 越野長爬坡, 輕鬆跑, LSD, CP 測試,
AeT 測試, 百岳多日.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from backend.demo import athlete as A

SHOWCASE_DAYS = 21


@dataclass
class Planned:
    date: dt.date
    kind: str                 # easy | recovery | tempo | interval | interval_short | hill | long | lsd |
                              # trail | trail_long | hike | baiyue | race_half | cp_test | aet_test
    minutes: float
    sport: str                # run | trail | hike
    name: str
    hour: float = 6.0         # local start
    showcase: bool = False
    stryd: bool = True
    day: int = 0              # day of a multi-day trip
    trip: Optional[str] = None
    params: dict = field(default_factory=dict)


@dataclass
class Season:
    anchor: dt.date
    start: dt.date
    activities: list
    events: list              # planning.Event
    phases: list              # planning.Phase
    thresholds: list          # planning.Threshold
    weights: list             # planning.Weight
    weeks: list               # [{"monday", "type", "n"}]
    cp_on: dict               # date -> the athlete's true CP that day (the simulation's)


SPORT = {"trail": "trail", "trail_long": "trail", "hike": "hike", "baiyue": "hike"}
NAMES = {
    "easy": "輕鬆跑", "recovery": "恢復跑", "tempo": "節奏跑 2×12 分", "interval": "間歇 6×4 分",
    "interval_short": "間歇 8×2 分", "hill": "坡道反覆", "long": "長跑", "lsd": "LSD 長距離慢跑",
    "trail": "越野跑", "trail_long": "越野長跑", "hike": "負重健行", "race_half": "半程馬拉松（賽事）",
    "cp_test": "CP 測試 12 分 + 3 分", "aet_test": "AeT 測試",
}
MINUTES = {"easy": (40, 60), "recovery": (30, 40), "tempo": (55, 58), "interval": (61, 63),
           "interval_short": (50, 52), "hill": (55, 65), "long": (90, 120), "lsd": (120, 150),
           "trail": (70, 100), "trail_long": (150, 230), "hike": (240, 330), "cp_test": (70, 70),
           "aet_test": (80, 80), "race_half": (90, 90), "baiyue": (420, 420)}


def _wd(d: dt.date, weekday: int) -> dt.date:
    """The day of d's week (Mon = 0) with that weekday."""
    return d + dt.timedelta(days=weekday - d.weekday())


def make_events(anchor: dt.date) -> list:
    from backend.engine.planning import Event
    a = anchor
    half = _wd(a - dt.timedelta(days=7 * 30), 6)
    baiyue = _wd(a - dt.timedelta(days=7 * 16), 4)
    warm1 = _wd(a - dt.timedelta(days=7 * 21), 5)
    warm2 = _wd(a - dt.timedelta(days=7 * 19), 5)
    show = a - dt.timedelta(days=15)
    race = _wd(a + dt.timedelta(days=26), 5)
    return [
        Event(id="demo-half", name="示範城市半程馬拉松", date=half.isoformat(), kind="road", priority="A",
              distance_km=21.1, climbing_m=60, est_hours=1.45, note="虛構賽事"),
        Event(id="demo-warm1", name="百岳暖身 2 日（一）", date=warm1.isoformat(), kind="baiyue", priority="C",
              days=2, distance_km=22, climbing_m=2100, est_hours=13, pack_kg=9, note="虛構行程"),
        Event(id="demo-warm2", name="百岳暖身 2 日（二）", date=warm2.isoformat(), kind="baiyue", priority="C",
              days=2, distance_km=24, climbing_m=2200, est_hours=14, pack_kg=9, note="虛構行程"),
        Event(id="demo-baiyue", name="示範百岳 3 日縱走", date=baiyue.isoformat(), kind="baiyue", priority="A",
              days=3, distance_km=38, climbing_m=3800, est_hours=24, pack_kg=10, note="虛構行程"),
        Event(id="demo-trip", name="百岳 3 日（訓練行程）", date=show.isoformat(), kind="baiyue", priority="C",
              days=3, distance_km=36, climbing_m=3700, est_hours=23, pack_kg=9, note="虛構行程"),
        Event(id="demo-50k", name="示範山徑越野 50K", date=race.isoformat(), kind="race", priority="A",
              distance_km=50, climbing_m=2800, est_hours=7.5, note="虛構賽事"),
        maokong_event(race),
    ]


# 貓空越野 17K: a real course (backend/demo/data/maokong_17k.gpx, the public Xtrail Maokong 17K
# route), the race calculator's example (demo.html). A B race three weeks before the 50K, inside
# its 專項期: make_phases builds on the A events only, so the phases (and the history) stay as
# they were. km / climb as the app reads the GPX (racepower.course: 16.6 km, ↑893 m ↓653 m); est_hours ≈ the
# calculator's 穩定 finish for the demo athlete (1:57).
MAOKONG_ID = "demo-maokong"


def maokong_event(race: dt.date):
    from backend.engine.planning import Event
    return Event(id=MAOKONG_ID, name="貓空越野 17K", date=(race - dt.timedelta(days=21)).isoformat(), kind="race",
                 priority="B", distance_km=16.6, climbing_m=890, est_hours=2.0, note="示範賽事")


def make_phases(events: list, start: dt.date) -> list:
    """Manual phases around the A events: 基礎期 → 專項期 (8 wk) → 減量期 (14 d)
    → 賽事 → 恢復期 (7 d, 14 d after a long event); as planning.py names them."""
    from backend.engine.planning import Phase
    out = []
    aev = sorted((e for e in events if e.priority == "A"), key=lambda e: e.date)
    # the plan covers the whole season even when the data is shorter (tests' 8 weeks)
    first = min([start] + [e.start - dt.timedelta(days=70) for e in aev])
    prev_end = first - dt.timedelta(days=1)
    for e in aev:
        spec = e.start - dt.timedelta(days=56)
        taper = e.start - dt.timedelta(days=14)
        if spec - prev_end > dt.timedelta(days=7):
            out.append(Phase(kind="base", start=(prev_end + dt.timedelta(days=1)).isoformat(),
                             end=(spec - dt.timedelta(days=1)).isoformat(), auto=False))
        s0 = max(spec, prev_end + dt.timedelta(days=1))
        out.append(Phase(kind="specific", start=s0.isoformat(), end=(taper - dt.timedelta(days=1)).isoformat(),
                         event_id=e.id, auto=False))
        out.append(Phase(kind="taper", start=taper.isoformat(), end=(e.start - dt.timedelta(days=1)).isoformat(),
                         event_id=e.id, auto=False))
        out.append(Phase(kind="event", start=e.start.isoformat(), end=e.end.isoformat(), event_id=e.id, auto=False))
        rec = 14 if e.is_long else 7
        out.append(Phase(kind="recovery", start=(e.end + dt.timedelta(days=1)).isoformat(),
                         end=(e.end + dt.timedelta(days=rec)).isoformat(), event_id=e.id, auto=False))
        prev_end = e.end + dt.timedelta(days=rec)
    return out


def cp_true(d: dt.date, anchor: dt.date) -> float:
    """The simulated athlete's CP: 240 W a year ago → 255 W by ~5 months ago
    → 260 W at the last CP test (3 weeks ago)."""
    a1, a2 = anchor - dt.timedelta(days=330), anchor - dt.timedelta(days=150)
    a3 = anchor - dt.timedelta(days=20)
    if d <= a1:
        return A.CP_HISTORY[0]
    if d <= a2:
        return A.CP_HISTORY[0] + (A.CP_HISTORY[1] - A.CP_HISTORY[0]) * (d - a1).days / (a2 - a1).days
    if d <= a3:
        return A.CP_HISTORY[1] + (A.CP_HISTORY[2] - A.CP_HISTORY[1]) * (d - a2).days / (a3 - a2).days
    return A.CP_HISTORY[2]


def make_thresholds(anchor: dt.date, start: dt.date) -> list:
    from backend.engine.planning import Threshold
    cp_day = anchor - dt.timedelta(days=20)
    return [
        Threshold(date=(min(start, anchor - dt.timedelta(days=364)) + dt.timedelta(days=6)).isoformat(), lthr=165, aethr=146, mhr=A.HRMAX,
                  cp=A.CP_HISTORY[0], cp_method="tt20", lthr_method="manual", aethr_method="manual",
                  note="示範資料"),
        Threshold(date=(anchor - dt.timedelta(days=150)).isoformat(), lthr=167, aethr=149, mhr=A.HRMAX,
                  cp=A.CP_HISTORY[1], cp_method="2pt", wprime=13800, lthr_method="manual", aethr_method="manual",
                  note="示範資料"),
        Threshold(date=cp_day.isoformat(), lthr=A.LTHR, aethr=A.AETHR, mhr=A.HRMAX, cp=A.CP_HISTORY[2],
                  cp_method="2pt", wprime=13500, lthr_method="manual", aethr_method="test", note="示範資料"),
    ]


def make_weights(anchor: dt.date, start: dt.date) -> list:
    from backend.engine.planning import Weight
    return [Weight(date=min(start, anchor - dt.timedelta(days=364)).isoformat(), kg=63.5),
            Weight(date=(anchor - dt.timedelta(days=150)).isoformat(), kg=62.5),
            Weight(date=(anchor - dt.timedelta(days=30)).isoformat(), kg=A.WEIGHT_KG)]


def _phase_on(phases: list, events: dict, d: dt.date):
    """(phase kind, its event's kind, the Phase) on day d."""
    for p in phases:
        if p.start <= d.isoformat() <= p.end:
            ev = events.get(p.event_id)
            return p.kind, (ev.kind if ev is not None else None), p
    return "base", None, None


# the minutes a session kind may stretch to when a week's hours are shared out
FLEX = {"easy": (35, 75), "recovery": (25, 45), "long": (75, 160), "lsd": (110, 170),
        "trail": (60, 165), "trail_long": (120, 260)}


def week_hours(phase: str, wk_in: int, wk_left: int, base_idx: int) -> tuple[float, bool]:
    """(target hours, recovery week?) of a week: base blocks ramp ~5 → 7.5 h
    over 3 weeks then ease to ~60 % (each block and each later base phase a
    little higher); 專項期 8–10 h; 減量期 −40 % then −60 %; a dip after a race."""
    if phase == "base":
        meso, pos = wk_in // 4, wk_in % 4
        if base_idx >= 2:
            # the long base before the 50K: from a low start after the 百岳 trip
            # to ~9.5 h, so CTL climbs ~50 → 70+ into the 專項期
            ramp, lift = (4.5, 5.5, 6.5), 1.5 * meso
        else:
            ramp, lift = (5.0, 6.25, 7.5), 0.5 * meso + 0.2 * base_idx
        peak = min(9.5, ramp[2] + lift)
        if pos == 3:
            return round(0.6 * peak, 2), True
        return min(9.5, ramp[pos] + lift), False
    if phase == "specific":
        if wk_left <= 2:                       # the last specific weeks: the biggest
            return 10.0, False
        pos = wk_in % 4
        if pos == 3:
            return 6.0, True
        return (8.0, 9.0, 9.5)[pos], False
    if phase == "taper":
        return (6.0 if wk_in == 0 else 4.0), False
    if phase == "event":
        return 4.0, False
    if phase == "recovery":
        return (3.0 if wk_in == 0 else 4.5), False
    return 5.0, False


def _share(kinds: list, hours: float) -> list:
    """Minutes per session so the week sums to `hours`: structured sessions
    keep their length, the FLEX kinds stretch within their bounds."""
    mins = [None] * len(kinds)
    fixed = 0.0
    for i, k in enumerate(kinds):
        if k not in FLEX:
            lo, hi = MINUTES[k]
            mins[i] = (lo + hi) / 2.0
            fixed += mins[i]
    flex = [i for i, k in enumerate(kinds) if k in FLEX]
    left = hours * 60.0 - fixed
    free = list(flex)
    for _ in range(4):
        if not free:
            break
        w = sum(sum(FLEX[kinds[i]]) / 2.0 for i in free)
        s = max(0.0, left) / w if w else 0.0
        clamped = []
        for i in free:
            lo, hi = FLEX[kinds[i]]
            v = s * sum(FLEX[kinds[i]]) / 2.0
            if v < lo or v > hi:
                mins[i] = min(hi, max(lo, v))
                clamped.append(i)
            else:
                mins[i] = v
        if not clamped:
            break
        left -= sum(mins[i] for i in clamped)
        free = [i for i in free if i not in clamped]
    return mins


def _showcase(anchor: dt.date) -> list:
    """(days before anchor, kind, showcase?, minutes or None) for the last
    three weeks; the last one is the 50K block's biggest normal week (~11 h,
    back-to-back trail days)."""
    return [(21, "recovery", False, 40), (20, "cp_test", True, None), (19, "easy", False, 60),
            (18, "easy", True, 65), (17, "trail", False, 110), (15, "baiyue", True, None),
            (14, "baiyue", True, None), (13, "baiyue", True, None), (11, "recovery", False, 40),
            (10, "aet_test", True, None), (9, "easy", False, 60), (8, "interval", True, None),
            (7, "easy", False, 70), (6, "trail_long", True, 230), (5, "trail", False, 120),
            (3, "easy", False, 60), (2, "lsd", True, 150), (1, "recovery", False, 40)]


def _templates(phase: str, ev_kind: Optional[str], week_no: int) -> dict:
    """weekday -> kind; "opt" marks the optional 6th session."""
    if phase == "taper":
        return {1: "interval_short", 3: "easy", 5: "easy", 6: "recovery"}
    if phase in ("recovery", "event"):
        return {2: "recovery", 4: "easy", 6: "easy"}
    if phase == "specific" and ev_kind == "race":
        # back-to-back long days (Sat trail_long + Sun trail) late in the block: schedule()
        return {1: "interval", 2: "easy", 3: "hill", 4: "opt", 5: "trail_long", 6: "easy"}
    if phase == "specific" and ev_kind == "baiyue":
        return {1: "tempo", 2: "easy", 3: "easy", 4: "opt", 5: "hike", 6: "trail"}
    if phase == "specific" and ev_kind == "road":
        return {1: "interval", 2: "easy", 3: "tempo", 4: "opt", 5: "lsd", 6: "easy"}
    return {1: "tempo" if week_no % 2 else "hill", 2: "easy", 3: "easy", 4: "opt", 5: "long",
            6: "trail" if week_no % 2 == 0 else "easy"}


def schedule(seed: int, anchor: dt.date, weeks: int = 52) -> Season:
    """The whole season (no files): events, phases, thresholds, activities."""
    rng = np.random.Generator(np.random.PCG64([seed, 1]))
    start = anchor - dt.timedelta(days=7 * weeks)
    events = make_events(anchor)
    phases = make_phases(events, start)
    ev_by_id = {e.id: e for e in events}
    show0 = anchor - dt.timedelta(days=SHOWCASE_DAYS)
    gap0 = anchor - dt.timedelta(days=7 * 40 - 2)
    gap = {gap0 + dt.timedelta(days=i) for i in range(10)} if gap0 > start else set()
    acts: list[Planned] = []
    weeks_meta = []

    def add(d: dt.date, kind: str, *, showcase=False, day=0, trip=None, minutes=None, hour=None):
        lo, hi = MINUTES[kind]
        mins = float(rng.uniform(lo, hi)) if minutes is None else float(minutes) * float(rng.uniform(0.94, 1.06))
        sport = SPORT.get(kind, "run")
        weekend = d.weekday() >= 5
        if hour is None:
            hour = (5.0 if weekend else 5.6) + float(rng.uniform(0, 0.8))
        stryd = bool(rng.random() > 0.10) if sport != "hike" else bool(rng.random() > 0.15)
        if showcase or kind in ("cp_test", "aet_test", "interval"):
            stryd = True
        name = NAMES.get(kind, kind)
        if kind == "baiyue":
            name = f"百岳 第 {day + 1} 天"
        elif showcase and kind == "trail_long":
            name = "越野長爬坡"
        params = {}
        if kind in ("trail", "trail_long"):
            # climbing that goes with the time on trail: ~600 m/h on the long runs
            # (3–4 h → 1500–2500 m), less on the shorter ones
            rate = 600.0 if kind == "trail_long" else 380.0
            params["climb"] = round(min(2500.0, mins / 60.0 * rate * float(rng.uniform(0.9, 1.1))), 0)
        acts.append(Planned(date=d, kind=kind, minutes=round(mins, 1), sport=sport, name=name, hour=hour,
                            showcase=showcase, stryd=stryd, day=day, trip=trip, params=params))

    # trips and the race inside the history: (date, kind, day, trip)
    fixed: dict[dt.date, tuple] = {}
    for e in events:
        if e.start >= show0 or e.start < start:
            continue
        if e.kind == "baiyue":
            for k in range(e.days):
                fixed[e.start + dt.timedelta(days=k)] = ("baiyue", k, e.id)
        elif e.kind == "road":
            fixed[e.start] = ("race_half", 0, e.id)
    monday = _wd(start, 0)
    week_no = 0
    base_seen: list = []                 # the base phases met so far (later ones run a little higher)
    while monday < show0:
        wtype = "normal"
        days = [monday + dt.timedelta(days=i) for i in range(7)]
        ph, evk, phase = _phase_on(phases, ev_by_id, days[3])
        if phase is not None:
            p0, p1 = dt.date.fromisoformat(phase.start), dt.date.fromisoformat(phase.end)
            wk_in, wk_left = max(0, (days[3] - p0).days // 7), max(0, (p1 - days[3]).days // 7)
        else:
            wk_in, wk_left = week_no, 99
        if ph == "base" and phase is not None and phase.start not in base_seen:
            base_seen.append(phase.start)
        hours, recovery_week = week_hours(ph, wk_in, wk_left, max(0, len(base_seen) - 1))
        tmpl = dict(_templates(ph, evk, week_no))
        if ph == "base" and len(base_seen) >= 3 and wk_in % 4 == 2:
            tmpl[5] = "trail_long"           # the base before the 50K: long climbs on trails
        if ph == "specific" and evk == "race" and wk_left <= 2:
            tmpl[6] = "trail"                # back-to-back long days
        if recovery_week:
            wtype = "recovery"
            tmpl = {1: "easy", 3: "easy", 5: "long", 6: "easy"}
        if ph in ("taper", "recovery", "event"):
            wtype = ph
        # the optional 6th session; a missed one now and then (never below 4)
        if tmpl.get(4) == "opt":
            del tmpl[4]
            if rng.random() < 0.6:
                tmpl[4] = "recovery"
        if len(tmpl) >= 5 and rng.random() < 0.12:
            tmpl.pop(int(rng.choice([2, 3])), None)
        if any(d in fixed for d in days):
            wtype = "event"
        if any(d in gap for d in days):
            wtype = "gap"
        if any(d < start or d >= show0 for d in days):
            wtype = "partial"
        n = 0
        todo = []
        for i, d in enumerate(days):
            if d < start or d >= show0 or d in gap:
                continue
            if d in fixed:
                todo.append((d, None))
                continue
            # the days around a trip / race are rest
            if any((d + dt.timedelta(days=j)) in fixed for j in (-1, 1)):
                continue
            if tmpl.get(i) is not None:
                todo.append((d, tmpl[i]))
        # share the week's hours out (the first partial week / a sick week gets
        # its share; the week cut by the showcase window keeps its full load)
        live = sum(1 for d in days if start <= d and d not in gap)
        mins = _share([k for _d, k in todo if k], hours * live / 7.0)
        it = iter(mins)
        for d, kind in todo:
            if kind is None:
                kind, k, trip = fixed[d]
                add(d, kind, day=k, trip=trip, hour=5.0 if kind == "baiyue" else 6.5)
            else:
                add(d, kind, minutes=next(it))
            n += 1
        weeks_meta.append({"monday": monday, "type": wtype, "n": n, "phase": ph, "target_h": hours})
        monday += dt.timedelta(days=7)
        week_no += 1
    for back, kind, show, mins in _showcase(anchor):
        d = anchor - dt.timedelta(days=back)
        if d < start:
            continue
        if kind == "baiyue":
            add(d, kind, showcase=show, day=15 - back, trip="demo-trip", hour=5.0)
        else:
            add(d, kind, showcase=show, minutes=mins)
    # showcase: exactly one per kind (the 3 trip days count as one 百岳多日)
    acts.sort(key=lambda p: (p.date, p.hour))
    cp_on = {p.date: cp_true(p.date, anchor) for p in acts}
    return Season(anchor=anchor, start=start, activities=acts, events=events, phases=phases,
                  thresholds=make_thresholds(anchor, start), weights=make_weights(anchor, start),
                  weeks=weeks_meta, cp_on=cp_on)
