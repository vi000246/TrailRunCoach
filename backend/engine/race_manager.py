"""
賽事管理 (SP-675, static/races.html): every race in one list, one race's day, and what was
predicted against what happened.

The list = the season plan's events (賽事周期 — the same events, added / edited / deleted from either
page; 百岳 trips are listed too, owner 2026-10-16) + the race activities no plan
event claims (the user's own 比賽 mark, a race word in the title — engine/race_activities.py). One row:

  {id: "e:<event id>" | "a:<workout idx>", event: the event (planning.event_json) | None, name, date,
   kind, priority, distance_km, climbing_m (the event's; else the activity's), past, status
   (upcoming | matched | partial | unmatched | done = an activity-only race), why, activities
   [{idx, date, title, km, gain_m, duration_s, how}], finish_s (the matched activities' duration
   summed), result (the user's 名次或成績), compare_idx (the activity two races are overlaid by: the
   longest one), calc {saved, predicted_s, saved_at} (the race calculator's saved calculation)}

detail(): the row + per activity its totals, 1-km splits and the overlay (activity_compare: pace /
HR / power every 0.5 % of the distance) with the elevation at the same points; `estimates` =
[{key: own (the event's 預估時間 est_hours) | calc (the saved calculation), predicted_s, actual_s,
diff_s}] when the race has an actual time.

readiness(): the 賽前完備程度 board (engine/race_readiness.py) as of the day before a past A race —
the board only follows an A race; any other race says why there is none.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from backend.i18n import _

ELEV_NONE = None


def predicted_s(result: Optional[dict]) -> Optional[float]:
    """The finish time of a saved /plan result (the calculator's summary: clock incl. stops)."""
    s = (result or {}).get("summary") if isinstance(result, dict) else None
    if not isinstance(s, dict):
        return None
    tot = s.get("time_total_s") or s.get("clock_s")
    if not tot and s.get("time_s"):
        tot = float(s["time_s"]) + float(s.get("stops_s") or 0.0)
    return round(float(tot)) if tot else None


def _calc(calcs: dict, eid: str) -> dict:
    row = (calcs or {}).get(eid)
    if not row:
        return {"saved": False, "predicted_s": None, "saved_at": None}
    return {"saved": True, "predicted_s": predicted_s(row.get("result")), "saved_at": row.get("saved_at")}


def _duration(ds, idx: int) -> Optional[float]:
    for w in getattr(ds, "workouts", None) or []:
        if w.idx == idx:
            v = (w.metrics or {}).get("duration")
            return float(v) if v else None
    return None


def _acts(ds, acts: list[dict]) -> list[dict]:
    return [{"idx": a["idx"], "date": a["date"], "key": a.get("key"), "title": a.get("title") or "",
             "km": a.get("km"), "gain_m": a.get("gain_m"), "duration_s": _duration(ds, a["idx"]),
             "how": a.get("how"), "why": a.get("why")} for a in acts]


def _finish(acts: list[dict]) -> Optional[float]:
    ds_ = [a["duration_s"] for a in acts if a.get("duration_s")]
    return round(sum(ds_)) if ds_ and len(ds_) == len(acts) else None


def _longest(acts: list[dict]) -> Optional[int]:
    return max(acts, key=lambda a: a.get("km") or 0)["idx"] if acts else None


def rows(ds, today: dt.date, tags: Optional[list] = None, calcs: Optional[dict] = None, plan=None) -> list[dict]:
    """Every race, newest first (see the module doc)."""
    from backend.engine import activity_tags as AT
    from backend.engine import planning as P
    from backend.engine import race_activities as RA
    tags = AT.load() if tags is None else tags
    plan = plan if plan is not None else getattr(ds, "plan", None)
    events = list(getattr(plan, "events", None) or [])      # 百岳 trips too (owner 2026-10-16)
    res = RA.results(ds, today, tags=tags, plan=SimpleEvents(events))
    out = []
    claimed: set[int] = set()
    for e in events:
        r = res.get(e.id) or {"status": "upcoming", "why": "", "activities": []}
        acts = _acts(ds, r["activities"])
        claimed.update(a["idx"] for a in acts)
        out.append({"id": f"e:{e.id}", "event": P.event_json(e, today), "event_id": e.id, "name": e.name,
                    "date": e.start.isoformat(), "end": e.end.isoformat(), "kind": e.kind,
                    "priority": (e.priority or "").upper() or None,
                    "distance_km": e.distance_km, "climbing_m": e.climbing_m,
                    "past": e.start <= today, "status": r["status"], "why": r.get("why") or "",
                    "activities": acts, "finish_s": _finish(acts), "result": getattr(e, "result", "") or "",
                    "compare_idx": _longest(acts), "calc": _calc(calcs, e.id), "source": "plan"})
    first = min((dt.date.fromisoformat(a["date"]) for a in RA.candidates(ds, tags)), default=today)
    for a in RA.race_activities(ds, first, today, tags=tags, today=today, plan=SimpleEvents(events)):
        if a["idx"] in claimed or a["source"] == "plan":
            continue
        acts = _acts(ds, [a])
        out.append({"id": f"a:{a['idx']}", "event": None, "event_id": None, "name": a.get("title") or "",
                    "date": a["date"], "end": a["date"], "kind": None, "priority": None,
                    "distance_km": a.get("km"), "climbing_m": a.get("gain_m"), "past": True, "status": "done",
                    "why": a.get("why") or "", "activities": acts, "finish_s": _finish(acts), "result": "",
                    "compare_idx": a["idx"], "calc": _calc({}, ""), "source": a["source"]})
    return sorted(out, key=lambda r: (r["date"], r["id"]), reverse=True)


class SimpleEvents:
    """A plan holding only these events (race_activities reads `.events`)."""

    def __init__(self, events):
        self.events = list(events)


def _elev_at(s, pct: list) -> list:
    import numpy as np
    if not s.ok or s.elev is None:
        return [ELEV_NONE for _p in pct]
    good = np.isfinite(s.elev)
    if good.sum() < 2:
        return [ELEV_NONE for _p in pct]
    x = s.d[0] + np.asarray(pct, dtype=float) / 100.0 * s.dist_km
    return [round(float(v), 1) for v in np.interp(x, s.d[good], s.elev[good])]


def _activity_view(ds, a: dict, series: Callable) -> dict:
    from backend.engine import activity_compare as AC
    try:
        s = series(a["idx"])
    except Exception:                       # noqa: BLE001 — no samples (an archived file): totals only
        s = None
    w = next((x for x in getattr(ds, "workouts", None) or [] if x.idx == a["idx"]), None)
    m = (w.metrics or {}) if w is not None else {}
    meta = {"duration": m.get("duration"), "distance": m.get("distance"), "climbing": m.get("climbing"),
            "load": m.get("tss")}
    if s is None:
        return {**a, "summary": AC.summary(AC.Series(None, None), meta), "splits": [], "overlay": None}
    ov = AC.overlay(s)
    ov["elev"] = _elev_at(s, ov["pct"])
    return {**a, "summary": AC.summary(s, meta), "splits": AC.splits(s), "overlay": ov}


def _estimates(row: dict, event) -> list[dict]:
    actual = row.get("finish_s")
    if not actual:
        return []
    out = []
    own = getattr(event, "est_hours", None) if event is not None else None
    if own:
        p = round(float(own) * 3600)
        out.append({"key": "own", "predicted_s": p, "actual_s": actual, "diff_s": round(actual - p)})
    p = (row.get("calc") or {}).get("predicted_s")
    if p:
        out.append({"key": "calc", "predicted_s": p, "actual_s": actual, "diff_s": round(actual - p),
                    "saved_at": row["calc"].get("saved_at")})
    return out


def detail(ds, today: dt.date, tags: Optional[list] = None, calcs: Optional[dict] = None, *,
           eid: Optional[str] = None, idx: Optional[int] = None, series: Optional[Callable] = None,
           plan=None) -> dict:
    """One race (the event `eid`, or the activity-only race `idx`): KeyError when there is none."""
    want = f"e:{eid}" if eid is not None else f"a:{idx}"
    all_rows = rows(ds, today, tags=tags, calcs=calcs, plan=plan)
    row = next((r for r in all_rows if r["id"] == want), None)
    if row is None:
        raise KeyError(want)
    if series is None:
        from backend.api.activity_compare import _series
        series = lambda i: _series(ds, i)    # noqa: E731
    plan = plan if plan is not None else getattr(ds, "plan", None)
    event = next((e for e in getattr(plan, "events", None) or [] if e.id == eid), None) if eid else None
    return {"row": row, "activities": [_activity_view(ds, a, series) for a in row["activities"]],
            "estimates": _estimates(row, event),
            "calc_url": f"/api/v1/racepower/page?event={eid}" if eid else None}


def readiness(plan, ds, eid: str, today: dt.date) -> dict:
    """{available, text, stages [{name, done, total, passed, label}], why} as of the day before the race."""
    from backend.engine import race_readiness as RR
    e = next((x for x in getattr(plan, "events", None) or [] if x.id == eid), None)
    if e is None:
        raise KeyError(eid)
    if (e.priority or "").upper() != "A":
        return {"available": False, "why": _("賽前完備程度只追蹤 A 賽事"), "stages": [], "text": ""}
    day = e.start - dt.timedelta(days=1)
    if day >= today:
        return {"available": False, "why": _("比賽還沒到：現在的完備程度在圖表分析的專項期／減量期卡片"),
                "stages": [], "text": ""}
    bd = RR.compute(plan, ds, day)
    if ((bd.get("race") or {}).get("id")) != eid:
        return {"available": False, "why": _("比賽前一天的下一場 A 賽事不是這場"), "stages": [], "text": ""}
    return {"available": True, "why": "", "text": RR.line(bd).get("text") or "",
            "stages": [{"name": s.get("name"), "done": s.get("done"), "total": s.get("total"),
                        "passed": bool(s.get("passed")), "unlocked": bool(s.get("unlocked")),
                        "label": s.get("label")} for s in bd.get("stages") or ()]}
