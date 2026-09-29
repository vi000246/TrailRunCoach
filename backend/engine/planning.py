"""
Season plan: target events (races / 百岳), training phases and personal
heart-rate thresholds.

Stored as a user overlay at ~/.wko5coach/plan.json — nothing here touches the
WKO5 files. Phases are generated backwards from each A event unless the user
has saved manual phases:

    base  →  specific (8 wk)  →  taper (2 wk)  →  event  →  recovery

Sources (docs/research/periodization-phase-metrics.md):
  * taper 14 days — Bosquet et al. 2007 meta-analysis (8–14 days most effective)
  * specific block before the taper, general → specific — Koop; Uphill Athlete
  * recovery / transition after the goal event — Uphill Athlete (2–4 weeks);
    shortened to 1 week for events shorter than ~6 h (a heuristic, not a finding)
B events get a short mini-taper and recovery inside the surrounding phase;
C events are training days and don't change the plan.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

PLAN_PATH = Path.home() / ".wko5coach" / "plan.json"

TAPER_DAYS = 14
SPECIFIC_WEEKS = 8
MINI_TAPER_DAYS = 5          # B event
LONG_EVENT_HOURS = 6.0       # recovery: 14 d at/above this, else 7 d
B_RECOVERY_DAYS = 3

PHASES = {
    "transition": "轉換期",
    "base": "基礎期",
    "specific": "專項期",
    "taper": "減量期",
    "event": "賽事",
    "recovery": "恢復期",
}
KINDS = {"race": "越野賽", "baiyue": "百岳", "road": "路跑賽", "other": "其他"}
PRIORITIES = ("A", "B", "C")


def _d(s) -> Optional[dt.date]:
    if s in (None, ""):
        return None
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


@dataclass
class Event:
    id: str
    name: str
    date: str                       # first day, ISO
    kind: str = "race"
    priority: str = "A"
    days: int = 1                   # multi-day 百岳
    distance_km: Optional[float] = None
    climbing_m: Optional[float] = None
    est_hours: Optional[float] = None   # expected moving time
    note: str = ""

    @property
    def start(self) -> dt.date:
        return _d(self.date)

    @property
    def end(self) -> dt.date:
        return self.start + dt.timedelta(days=max(1, int(self.days or 1)) - 1)

    @property
    def climb_per_km(self) -> Optional[float]:
        if self.distance_km and self.climbing_m is not None and self.distance_km > 0:
            return self.climbing_m / self.distance_km
        return None

    @property
    def is_long(self) -> bool:
        if self.est_hours is not None:
            return self.est_hours >= LONG_EVENT_HOURS
        return (self.days or 1) > 1 or (self.distance_km or 0) >= 42


@dataclass
class Phase:
    kind: str
    start: str
    end: str                        # inclusive
    event_id: Optional[str] = None
    auto: bool = True

    @property
    def label(self) -> str:
        return PHASES.get(self.kind, self.kind)


@dataclass
class Threshold:
    """Personal HR thresholds from a dated test. Any field may be blank."""
    date: str
    lthr: Optional[float] = None    # lactate-threshold HR (WKO5 `thr`)
    aethr: Optional[float] = None   # aerobic-threshold HR (Uphill Athlete AeT test)
    mhr: Optional[float] = None
    cp: Optional[float] = None      # running critical power, W (3'/12' test)
    note: str = ""

    THRESHOLD_FIELDS = ("lthr", "aethr", "mhr", "cp")


@dataclass
class Plan:
    events: list[Event] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)       # manual; empty = auto
    thresholds: list[Threshold] = field(default_factory=list)

    # ---- persistence ------------------------------------------------------
    @classmethod
    def load(cls, path: Path = PLAN_PATH) -> "Plan":
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(
            events=[Event(**e) for e in raw.get("events", [])],
            phases=[Phase(**{**p, "auto": False}) for p in raw.get("phases", [])],
            thresholds=[Threshold(**t) for t in raw.get("thresholds", [])],
        )

    def save(self, path: Path = PLAN_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "events": [asdict(e) for e in sorted(self.events, key=lambda e: e.date)],
            "phases": [{k: v for k, v in asdict(p).items() if k != "auto"} for p in self.phases],
            "thresholds": [asdict(t) for t in sorted(self.thresholds, key=lambda t: t.date)],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)

    # ---- edits ------------------------------------------------------------
    def upsert_event(self, data: dict) -> Event:
        data = {k: v for k, v in data.items() if k in Event.__dataclass_fields__}
        if data.get("priority") not in PRIORITIES:
            data["priority"] = "A"
        if data.get("kind") not in KINDS:
            data["kind"] = "other"
        _d(data["date"])  # validate
        eid = data.get("id") or uuid.uuid4().hex[:8]
        ev = Event(**{**data, "id": eid})
        self.events = [e for e in self.events if e.id != eid] + [ev]
        return ev

    def delete_event(self, eid: str) -> bool:
        n = len(self.events)
        self.events = [e for e in self.events if e.id != eid]
        return len(self.events) != n

    # ---- thresholds -------------------------------------------------------
    def threshold_on(self, name: str, day: dt.date) -> Optional[float]:
        """Latest non-blank `name` (lthr / aethr / mhr / cp) dated on or before `day`.
        Before the first test the earliest value applies, as WKO5 does."""
        vals = [(_d(t.date), getattr(t, name)) for t in self.thresholds
                if getattr(t, name) is not None]
        vals.sort()
        val = None
        for d, v in vals:
            if d <= day:
                val = v
        return val if val is not None else (vals[0][1] if vals else None)


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------

def auto_phases(events: list[Event], begin: dt.date, end: dt.date) -> list[Phase]:
    """Phases covering [begin, end], built backwards from each A event."""
    one = dt.timedelta(days=1)
    a_events = sorted((e for e in events if e.priority == "A"), key=lambda e: e.start)
    out: list[Phase] = []
    cursor = begin                  # first day not yet assigned

    def add(kind, s, e, eid=None):
        s = max(s, cursor)
        if s <= e:
            out.append(Phase(kind, s.isoformat(), e.isoformat(), eid))

    for ev in a_events:
        if ev.end < cursor:
            continue
        taper_start = ev.start - dt.timedelta(days=TAPER_DAYS)
        spec_start = taper_start - dt.timedelta(weeks=SPECIFIC_WEEKS)
        add("base", cursor, spec_start - one)
        add("specific", spec_start, taper_start - one, ev.id)
        add("taper", taper_start, ev.start - one, ev.id)
        add("event", ev.start, ev.end, ev.id)
        cursor = max(cursor, ev.end + one)
        rec_days = 14 if ev.is_long else 7
        rec_end = ev.end + dt.timedelta(days=rec_days)
        add("recovery", cursor, rec_end, ev.id)
        cursor = max(cursor, rec_end + one)
    if cursor <= end:
        add("base", cursor, end)    # no A event ahead: open-ended base
    return [p for p in out if _d(p.start) <= end]


def phases(plan: Plan, begin: dt.date, end: dt.date) -> list[Phase]:
    return plan.phases if plan.phases else auto_phases(plan.events, begin, end)


def phase_on(plan: Plan, day: dt.date, begin: Optional[dt.date] = None) -> Optional[Phase]:
    begin = begin or day - dt.timedelta(days=400)
    for p in phases(plan, begin, day + dt.timedelta(days=400)):
        if _d(p.start) <= day <= _d(p.end):
            return p
    return None


def b_event_windows(events: list[Event]) -> list[dict]:
    """Mini-taper / recovery windows around B events (markers, not phases)."""
    out = []
    for e in events:
        if e.priority != "B":
            continue
        out.append({"event_id": e.id, "kind": "mini_taper",
                    "start": (e.start - dt.timedelta(days=MINI_TAPER_DAYS)).isoformat(),
                    "end": (e.start - dt.timedelta(days=1)).isoformat()})
        out.append({"event_id": e.id, "kind": "mini_recovery",
                    "start": (e.end + dt.timedelta(days=1)).isoformat(),
                    "end": (e.end + dt.timedelta(days=B_RECOVERY_DAYS)).isoformat()})
    return out


# ---------------------------------------------------------------------------
# combined targets across several upcoming events
# ---------------------------------------------------------------------------

GOAL_FIELDS = {
    "distance_km": "距離",
    "climbing_m": "爬升",
    "climb_per_km": "每公里爬升",
    "est_hours": "預估時間",
    "days": "天數",
}


def goals(plan: Plan, today: dt.date, horizon_days: int = 182) -> dict:
    """Train for the hardest demand among the upcoming A and B events.

    Each goal is the maximum over events dated within `horizon_days` (A events
    always count, whatever the date, up to and including the next one), with
    the event that sets it, so the page can say which race drives which
    target."""
    upcoming = sorted((e for e in plan.events if e.end >= today and e.priority in ("A", "B")),
                      key=lambda e: e.start)
    horizon = today + dt.timedelta(days=horizon_days)
    next_a = next((e for e in upcoming if e.priority == "A"), None)
    pool = [e for e in upcoming if e.start <= horizon or (next_a and e.start <= next_a.start)]
    out = {}
    for key, label in GOAL_FIELDS.items():
        best = None
        for e in pool:
            v = getattr(e, key)
            if v is None:
                continue
            if best is None or v > best[0]:
                best = (v, e)
        out[key] = None if best is None else {
            "label": label, "value": best[0], "event_id": best[1].id, "event": best[1].name}
    return {
        "events": [e.id for e in pool],
        "next_a": None if next_a is None else next_a.id,
        "days_to_next_a": None if next_a is None else (next_a.start - today).days,
        "targets": out,
    }


def event_json(e: Event, today: dt.date) -> dict:
    return {**asdict(e), "end": e.end.isoformat(), "climb_per_km": e.climb_per_km,
            "kind_label": KINDS.get(e.kind, e.kind), "days_to": (e.start - today).days}


def phase_json(p: Phase) -> dict:
    return {**asdict(p), "label": p.label,
            "days": (_d(p.end) - _d(p.start)).days + 1}
