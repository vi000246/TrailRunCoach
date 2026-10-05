"""
下坡升級, phase 1 (SP-111 「下坡另外算」; docs/research/downhill-recovery.md): the two numbers
planning.downhill compares, in the same unit — 「下坡衝擊等效 km」 (chart_metrics.DOWNHILL_EXPR /
downhill_weight: the km steeper than −3 %, weighted by grade and speed; Gottschall & Kram 2005,
Keller 1996; status.i_descent's 下坡負荷 uses it too).

  race     the event GPX's per-point grade (racepower.course: 10 m resample + the calculator's
           smoothing) × the race calculator's predicted speed on the segment holding the point
           (CALC.make_plan on the event's course — 「計算機預測的下坡速度」). When the calculator
           cannot predict (no CP / RE …) the race's mean speed (km ÷ planning.event_hours) is used
           for every point (推估, it understates fast descents); without even that, the reference
           3 m/s (weight 1 for the speed). No GPX → None (planning says so; no bump).
  athlete  the biggest single foot activity (run / walk / hike) of the last DOWNHILL_WEEKS[0] weeks
           before the race (today when the race is later), the activities DOWNHILL_WEEKS[0]–[1]
           weeks back linearly down-weighted to 0 (repeated-bout effect: 3 and 6 weeks protect, 9
           don't — periodization-cross-sport.md [350]–[353]); 0 with nothing downhill.

The engine stays pure: install() sets planning.RACE_DOWNHILL_OF / ATHLETE_DOWNHILL_OF (memoised
per tenant / key / day for 10 min); tests set their own values.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional, Sequence

FOOT_SPORTS = ("run", "walk")
HIKE_TAGS = {"hiking", "mountaineering"}       # status.HIKE_TAGS
MEMO_S = 600.0


def race_load(xs_m: Sequence[float], zs_m: Sequence[float], segments: Optional[list] = None,
              speed_kmh: Optional[float] = None) -> float:
    """The downhill impact (等效 km) of a course profile: per step (xs metres, zs metres) the grade,
    the speed of the predicted segment holding it (`segments`: [{start_km, end_km, dist_m, t}]),
    else `speed_kmh`, else 3 m/s; chart_metrics.downhill_load over the steps."""
    from backend.engine.algorithms import chart_metrics as CM
    segs = [s for s in segments or () if s.get("t") and s.get("dist_m")]
    seg_v = [(float(s["start_km"]) * 1000.0, float(s["end_km"]) * 1000.0, float(s["dist_m"]) / float(s["t"]) * 3.6)
             for s in segs]
    ref = speed_kmh if speed_kmh and speed_kmh > 0 else CM.REF_SPEED * 3.6
    grades, dists, speeds = [], [], []
    j = 0
    for i in range(len(xs_m) - 1):
        dx = float(xs_m[i + 1]) - float(xs_m[i])
        if dx <= 0:
            continue
        mid = 0.5 * (float(xs_m[i]) + float(xs_m[i + 1]))
        v = ref
        if seg_v:
            while j < len(seg_v) - 1 and mid > seg_v[j][1]:
                j += 1
            v = seg_v[j][2]
        grades.append((float(zs_m[i + 1]) - float(zs_m[i])) / dx)
        dists.append(dx / 1000.0)
        speeds.append(v)
    return CM.downhill_load(grades, dists, speeds)


def weight(age_days: int) -> float:
    """An activity's weight by its age (planning.DOWNHILL_WEEKS): 1 up to 6 weeks, linear to 0 at 9."""
    from backend.engine.planning import DOWNHILL_WEEKS
    a, b = DOWNHILL_WEEKS[0] * 7, DOWNHILL_WEEKS[1] * 7
    if age_days < 0:
        return 0.0
    if age_days <= a:
        return 1.0
    return max(0.0, (b - age_days) / (b - a))


def athlete_max(rows: Sequence[tuple], day: dt.date) -> float:
    """The weighted max of `rows` [(date, load)] before `day` (weight()); 0.0 with none."""
    return max((float(v) * weight((day - d).days) for d, v in rows if d < day and v and v > 0), default=0.0)


def _athlete_rows(ds, day: dt.date) -> list[tuple]:
    """[(date, downhill load)] of the foot activities in the DOWNHILL_WEEKS[1] weeks before `day`."""
    from backend.engine.algorithms import chart_metrics as CM
    from backend.engine.planning import DOWNHILL_WEEKS
    from backend.engine.wko5expr.dataset import date_to_day, day_to_date
    from backend.engine.wko5expr.evaluator import WS, Evaluator
    d1 = int(math.floor(date_to_day(day))) - 1
    span = DOWNHILL_WEEKS[1] * 7
    r = Evaluator(ds, d1 - span, d1).evaluate(f"athleterange(today-{span}, today, {CM.DOWNHILL_EXPR})")
    if not isinstance(r, WS):
        return []
    out = []
    for i, v in r.items():
        w = ds.workouts[i]
        if w.sport not in FOOT_SPORTS and not HIKE_TAGS & set(w.tags or ()):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            out.append((day_to_date(math.floor(w.day)), fv))
    return out


def _race_value(ev) -> Optional[float]:
    """race_load of the event's stored GPX with the calculator's segment speeds; None = no GPX."""
    from backend.engine import event_gpx as EG
    from backend.engine import planning as P
    from backend.engine.racepower import course as CO
    try:
        got = EG.track(ev.id)
    except EG.EventGpxError:
        return None
    if got is None:
        return None
    track, row = got
    td = CO.track_distance(track)
    xs, raw = CO.resample(td["d"], td["z"], CO.STEP_M)
    zs = CO.smooth(raw, CO.STEP_M, CO.DEFAULT_SIGMA_M)
    segs = None
    try:
        from backend.api import racepower as RP
        from backend.engine.panels.race_refs import CALC_TYPE
        from backend.engine.racepower import calc as CALC
        body = CALC.PlanIn(type=CALC_TYPE.get(ev.kind, "trail"), date=ev.date, days=max(1, int(ev.days or 1)),
                           pack_kg=ev.pack_kg, course=CALC.CourseRef(event_id=ev.id, split="grade"),
                           day_splits_km=EG.splits_for(row, max(1, int(ev.days or 1))))
        segs = CALC.make_plan(RP.LIVE, body).get("segments")
    except Exception:                       # noqa: BLE001 — no prediction: the mean speed below
        segs = None
    mean = None
    if not segs:
        h = P.event_hours(ev)
        mean = float(xs[-1]) / 1000.0 / h if h else None
    return race_load(xs, zs, segs, mean)


def install() -> None:
    """planning.RACE_DOWNHILL_OF / ATHLETE_DOWNHILL_OF for the app (memoised; a recursion guard, since
    the calculator reads the plan)."""
    import contextvars
    import threading
    import time
    from dataclasses import astuple
    from backend.engine import planning as P
    lock, memo = threading.Lock(), {}
    busy = contextvars.ContextVar("downhill_busy", default=False)

    def cached(key, fn):
        with lock:
            hit = memo.get(key)
        if hit and time.time() - hit[0] < MEMO_S:
            return hit[1]
        tok = busy.set(True)
        try:
            v = fn()
        finally:
            busy.reset(tok)
        with lock:
            if len(memo) > 256:
                memo.clear()
            memo[key] = (time.time(), v)
        return v

    def race_of(ev) -> Optional[float]:
        if busy.get():
            raise RuntimeError("downhill: re-entered")     # planning.downhill → no verdict this time
        from backend import tenancy
        return cached(("race", tenancy.current().id, astuple(ev), dt.date.today()), lambda: _race_value(ev))

    def athlete_of(day: dt.date) -> Optional[float]:
        if busy.get():
            raise RuntimeError("downhill: re-entered")
        from backend import tenancy
        from backend.api.wko5views import _dataset
        d = min(day, dt.date.today())
        return cached(("athlete", tenancy.current().id, d, dt.date.today()),
                      lambda: athlete_max(_athlete_rows(_dataset(), d), d))

    P.RACE_DOWNHILL_OF, P.ATHLETE_DOWNHILL_OF = race_of, athlete_of
