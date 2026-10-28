"""SP-975: the 介紹頁's 賽事周期 example, frozen (backend/demo/intro_cycle.json, served by api/intro.cycle).

The demo athlete's season (backend/demo/season.make_events: the past A 示範百岳 3 日縱走, the A 示範山徑越野 50K
four weeks out, the B 貓空越野 17K three weeks before it) plus one more fictional B race in the base
(示範郊山越野 25K), put through the app's own phase planner — engine/planning.auto_phases, b_event_windows,
recovery_plan — in the 賽事周期 page's window (api/plan.get_plan: today − 120 days … the last event + 42
days). C races make no phase and are left out.

Frozen as whole days from the day it was computed for (REF_TODAY), never a calendar date: the page adds them
to the visitor's own today, so 「今天」 always sits in the 50K's 專項期 about four weeks before the race, as the
介紹頁's 課表 calendar (「比賽前的最後 4 週」). The planner runs without the app's hooks (pure): each race's size
comes from its own est_hours and the ITRA climb divisor, no 下坡升級 (no athlete data) — the 50K is 超馬級 either
way (7.5 h; the calculator's 8.9 h). Run it again when the planner's rules change (test_intro_sp975 compares
the file with a fresh run):

    python -m backend.scripts.intro_cycle            # writes backend/demo/intro_cycle.json
    python -m backend.scripts.intro_cycle --check    # exit 1 when the file is not what the planner gives
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "demo" / "intro_cycle.json"
# the day the example is computed for (a Wednesday: the demo season's race is the Saturday four weeks on)
REF_TODAY = dt.date(2026, 10, 28)
WINDOW_BACK, WINDOW_AFTER = 120, 42       # as api/plan.get_plan
# the example's events as the page names them (i18n intro.cyc.ev.<id>)
EVENT_IDS = {"demo-baiyue": "baiyue", "demo-25k": "trail25k", "demo-maokong": "trail17k", "demo-50k": "trail50k"}


def extra_b(race: dt.date):
    """The second B race: a fictional 25K trail race in the base, the Sunday 12 weeks before the 50K (its
    mini-taper and recovery inside the 基礎期, two weeks before the 專項期)."""
    from backend.engine.planning import Event
    day = race - dt.timedelta(weeks=12)
    day = day + dt.timedelta(days=(6 - day.weekday()))          # that week's Sunday
    return Event(id="demo-25k", name="示範郊山越野 25K", date=day.isoformat(), kind="race", priority="B",
                 distance_km=25.0, climbing_m=1200, est_hours=3.3, note="虛構賽事")


def build(today: dt.date = REF_TODAY) -> dict:
    from backend.demo import season
    from backend.engine import planning as P
    events = [e for e in season.make_events(today) if e.priority in ("A", "B")]
    race = next(e for e in events if e.id == "demo-50k").start
    events.append(extra_b(race))
    begin = today - dt.timedelta(days=WINDOW_BACK)
    end = max(today, max(e.end for e in events)) + dt.timedelta(days=WINDOW_AFTER)
    phases = P.auto_phases(events, begin, end, P.TRANSITION_WEEKS, None)
    off = lambda d: (P._d(d) - today).days                       # noqa: E731
    by_id = {e.id: e for e in events}
    shown = sorted((e for e in events if begin <= e.start <= end), key=lambda e: e.start)

    def phase(p) -> dict:
        ev = by_id.get(p.event_id)
        out = {"kind": p.kind, "start": off(p.start), "end": off(p.end), "event": EVENT_IDS.get(p.event_id)}
        if p.kind == "event" and ev is not None and ev.kind == "baiyue":
            out["trip"] = True                                   # an A 百岳 trip's phase is 行程 (SP-504 R2)
        if p.kind == "recovery" and ev is not None:
            out["size"] = P.SIZES[P.recovery_plan(ev)["rec_size"]]
        return out
    return {
        "_about": "SP-975: the 介紹頁's 賽事周期 example (backend/scripts/intro_cycle.py: the demo season through "
                  "engine/planning.auto_phases; a fictional runner). Days from today, no calendar date.",
        "begin": off(begin), "end": off(end),
        "phases": [phase(p) for p in phases],
        "events": [{"id": EVENT_IDS[e.id], "priority": e.priority, "start": off(e.start), "end": off(e.end)}
                   for e in shown],
        "b_windows": [{"kind": w["kind"], "event": EVENT_IDS[w["event_id"]], "start": off(w["start"]),
                       "end": off(w["end"])} for w in P.b_event_windows(events)
                      if begin <= P._d(w["start"]) and P._d(w["end"]) <= end],
        "rules": {"taper_days": P.TAPER_DAYS, "specific_weeks": P.SPECIFIC_WEEKS,
                  "mini_taper_days": P.MINI_TAPER_DAYS, "transition_weeks": P.TRANSITION_WEEKS},
    }


def dumps(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="compare, do not write")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    text = dumps(build())
    if a.check:
        same = a.out.exists() and a.out.read_text("utf-8") == text
        print("up to date" if same else f"{a.out} differs from the planner's output")
        return 0 if same else 1
    a.out.write_text(text, encoding="utf-8")
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
