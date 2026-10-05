"""
Season plan: target events (races / 百岳), training phases and personal
heart-rate thresholds.

Stored as a user overlay at ~/.wko5coach/plan.json — nothing here touches the
WKO5 files. Phases are generated backwards from each A event unless the user
has saved manual phases:

    base  →  specific (8 wk)  →  taper (2 wk)  →  event  →  recovery  →  transition (3 wk)

Sources (docs/research/periodization-phase-metrics.md):
  * taper 14 days — Bosquet et al. 2007 meta-analysis (8–14 days most effective)
  * specific block before the taper, general → specific — Koop; Uphill Athlete
  * recovery / transition after the goal event — Uphill Athlete (2–4 weeks);
    shortened to 1 week for events shorter than ~6 h (a heuristic, not a finding)
  * transition after the recovery (SP-73) — Friel (Transition 1–8 weeks, usually 3–4)
    and Canova (4 weeks of easy running ≤ 1 h), coach-schools-zones-periodization.md R5:
    `transition_weeks` (課表偏好, default 3, 0 = off). It never takes days from the next A
    race's backward-planned 專項期: too close → shortened, < 7 days → skipped, with a note
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

from backend.i18n import _

PLAN_PATH: Optional[Path] = None      # fixed file (tests); None = the tenant's plan.json


def plan_path() -> Path:
    """The current tenant's plan.json (backend/tenancy.py)."""
    if PLAN_PATH is not None:
        return Path(PLAN_PATH)
    from backend import tenancy
    return tenancy.private_path("plan.json")

TAPER_DAYS = 14
SPECIFIC_WEEKS = 8
MINI_TAPER_DAYS = 5          # B event
LONG_EVENT_HOURS = 6.0       # recovery: 14 d at/above this, else 7 d
B_RECOVERY_DAYS = 3
TRANSITION_WEEKS = 3          # 轉換期 after an A race's recovery (SP-73; Friel 3–4, Canova 4 — the low end, 推估)
TRANSITION_WEEKS_RANGE = (0, 4)   # 0 = off; 4 = Friel's / Canova's upper end
TRANSITION_MIN_DAYS = 7       # less room before the next race's 專項期 → no 轉換期 (推估)
TRANSITION_SETTING = "plan.prefs.transition_weeks"

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
EVENT_HEAT = ("auto", "hot", "cool")      # Event.heat (heat-acclimation.md §5.4)
PACK_MAX_KG = 40.0                         # Event.pack_kg: as athlete.set_hike_meta's 0–40 kg check
CUTOFF_MAX_H = 240.0                       # Event.cutoff_hours: 10 days, past the longest stage race's cutoff


class EventError(ValueError):
    """A bad event field (the message is for the user, translated); a bad date stays a plain ValueError."""


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
    # is this a hot race? auto = the race-day forecast / climatology decides
    # (Hadley > 150, heat.race_is_hot); hot / cool = the user says so
    heat: str = "auto"
    # the trip pack (kg, day 1 = the heaviest) — loaded-carry-training.md §5.1;
    # None = capacity.PACK_DEFAULT_MULTI / _SINGLE (9 kg), see pack()
    pack_kg: Optional[float] = None
    # SP-105 (engine/race_feasibility.py): a race's 關門時間 (h); a 百岳's 撤退時間 = hours from the
    # summit day's start with no summit yet → turn back. None = not set
    cutoff_hours: Optional[float] = None
    # 百岳: km of the summit along the whole trip; None = the GPX's highest point
    summit_km: Optional[float] = None

    @property
    def pack(self) -> float:
        """pack_kg, else the 9 kg default (capacity.PACK_DEFAULT_MULTI / _SINGLE)."""
        if self.pack_kg is not None:
            return float(self.pack_kg)
        from backend.engine.racepower.capacity import PACK_DEFAULT_MULTI, PACK_DEFAULT_SINGLE
        return PACK_DEFAULT_MULTI if (self.days or 1) > 1 else PACK_DEFAULT_SINGLE

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
    # why an automatic phase is not the standard length (SP-73: a shortened / skipped 轉換期)
    note: str = ""

    @property
    def label(self) -> str:
        return PHASES.get(self.kind, self.kind)


@dataclass
class Threshold:
    """Personal HR thresholds from a dated test. Any field may be blank."""
    date: str
    lthr: Optional[float] = None    # lactate-threshold HR (WKO5 `thr`)
    aethr: Optional[float] = None   # aerobic-threshold HR (Uphill Athlete AeT test)
    mhr: Optional[float] = None     # maximum HR (設定 → 心率; engine/hr_profile.py)
    rhr: Optional[float] = None     # resting HR (設定 → 心率; engine/hr_profile.py)
    cp: Optional[float] = None      # running critical power, W (a CP test, engine/cp_protocols.py)
    note: str = ""
    # how `cp` was measured (cp_protocols.METHOD_LABEL): 2pt / 1pt_prior / tt20 /
    # race; None = entered by hand or a legacy 3'/12' row. `wprime` J, only
    # when measured (two-point) — a prior is not the athlete's W′.
    wprime: Optional[float] = None
    cp_method: Optional[str] = None
    # how `lthr` / `aethr` were obtained (LTHR_METHODS / AETHR_METHODS); None =
    # a legacy row, read from its note (threshold_method). docs/research/
    # zones-and-thresholds.md §3.4 change 1: an applied estimate is not a test.
    lthr_method: Optional[str] = None
    aethr_method: Optional[str] = None
    # how `mhr` was obtained (MHR_METHODS; SP-64): test = a max-HR test read by
    # engine/threshold_confidence.py, estimate = its sustained-peak candidate,
    # manual = 設定 → 心率; None = a legacy row (= manual)
    mhr_method: Optional[str] = None

    THRESHOLD_FIELDS = ("lthr", "aethr", "mhr", "rhr", "cp")


_THRESHOLD_KEYS = ("date", "lthr", "aethr", "mhr", "rhr", "cp", "note", "wprime", "cp_method",
                   "lthr_method", "aethr_method", "mhr_method")

# estimate = thresholds.estimate applied with 「套用估計」; friel30 = Friel's
# 30-min solo TT (last 20 min HR); test = an AeT drift test (engine/aet_test.py);
# race / lab / manual = entered from a race, a lab test, by hand
LTHR_METHODS = ("estimate", "friel30", "race", "lab", "manual")
AETHR_METHODS = ("estimate", "test", "lab", "manual")
MHR_METHODS = ("test", "race", "lab", "estimate", "manual")
METHOD_LABEL = {"estimate": "自動估算", "friel30": "30 分鐘測試", "test": "AeT 測試", "race": "比賽",
                "lab": "實驗室測試", "manual": "手動輸入"}
_FIELD_NAME = {"lthr": "LTHR", "aethr": "AeT"}


def threshold_method(t: "Threshold", name: str) -> Optional[str]:
    """How the row's `name` (lthr / aethr) was obtained. Rows written before
    the *_method fields existed are read from their note: 「LTHR 自動估算」 /
    「AeT 自動估算」 / 「由活動資料自動估算」 (the season-plan page's and
    apply-estimate's notes) = estimate, 「AeT … 測試」 (apply_body of an AeT
    drift test) = test, anything else = manual."""
    if getattr(t, name, None) is None:
        return None
    m = getattr(t, f"{name}_method", None)
    if m:
        return m
    note = t.note or ""
    label = _FIELD_NAME.get(name, name)
    if f"{label} 自動估算" in note or "由活動資料自動估算" in note:
        return "estimate"
    if name == "aethr" and "AeT" in note and "測試" in note:
        return "test"
    return "manual"


def threshold_row(plan: "Plan", name: str, day: dt.date) -> Optional[dict]:
    """The plan row whose `name` (lthr / aethr) is in effect on `day`:
    {"value", "date", "method", "measured", "label"}; None without one.
    measured = obtained by a test / race / lab / by hand, not an applied
    estimate. label: 「自動估算（已套用 2026-01-15）」, 「30 分鐘測試 2026-…」 …"""
    rows = sorted((t for t in plan.thresholds if getattr(t, name, None) is not None and _d(t.date) <= day),
                  key=lambda t: t.date)
    if not rows:
        return None
    t = rows[-1]
    m = threshold_method(t, name)
    d = t.date[:10]
    label = f"自動估算（已套用 {d}）" if m == "estimate" else f"{METHOD_LABEL.get(m, '手動輸入')} {d}"
    return {"value": float(getattr(t, name)), "date": d, "method": m, "measured": m != "estimate",
            "label": label}


@dataclass
class Weight:
    date: str
    kg: float


PROFILE_FIELDS = {
    "sex": ("male", "female"),
    "power_meter": ("stryd", "coros", "garmin", "other"),
}


@dataclass
class Plan:
    events: list[Event] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)       # manual; empty = auto
    thresholds: list[Threshold] = field(default_factory=list)
    weights: list[Weight] = field(default_factory=list)     # dated body weight
    profile: dict = field(default_factory=dict)             # sex, height_cm, power_meter

    def weight_on(self, day: dt.date) -> Optional[float]:
        """Body weight in effect on `day` (earliest entry before the first)."""
        ws = sorted((_d(w.date), w.kg) for w in self.weights if w.kg)
        val = None
        for d, kg in ws:
            if d <= day:
                val = kg
        return val if val is not None else (ws[0][1] if ws else None)

    # ---- persistence ------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Plan":
        path = path or plan_path()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(
            events=[Event(**e) for e in raw.get("events", [])],
            phases=[Phase(**{**p, "auto": False}) for p in raw.get("phases", [])],
            thresholds=[Threshold(**{k: v for k, v in t.items() if k in _THRESHOLD_KEYS})
                        for t in raw.get("thresholds", [])],
            weights=[Weight(**w) for w in raw.get("weights", [])],
            profile=dict(raw.get("profile", {})),
        )

    def save(self, path: Optional[Path] = None) -> None:
        path = path or plan_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "events": [asdict(e) for e in sorted(self.events, key=lambda e: e.date)],
            "phases": [{k: v for k, v in asdict(p).items() if k != "auto"} for p in self.phases],
            "thresholds": [asdict(t) for t in sorted(self.thresholds, key=lambda t: t.date)],
            "weights": [asdict(w) for w in sorted(self.weights, key=lambda w: w.date)],
            "profile": self.profile,
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
        if data.get("heat") not in EVENT_HEAT:
            data["heat"] = "auto"
        if data.get("pack_kg") in ("",):
            data["pack_kg"] = None
        if data.get("pack_kg") is not None:
            data["pack_kg"] = float(data["pack_kg"])
            if not 0 <= data["pack_kg"] <= PACK_MAX_KG:
                raise EventError(_("行程背包要在 0–{max:g} kg", max=PACK_MAX_KG))
        for k, hi in (("cutoff_hours", CUTOFF_MAX_H), ("summit_km", None)):
            if data.get(k) in ("",):
                data[k] = None
            if data.get(k) is not None:
                data[k] = float(data[k])
                if data[k] <= 0 or (hi is not None and data[k] > hi):
                    raise EventError(_("關門／撤退時間要在 0–{max:g} 小時", max=hi) if hi else _("山頂公里數要大於 0"))
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
        """Latest non-blank `name` (lthr / aethr / mhr / cp) dated on or before `day`,
        else None. A test never applies to the days before it was done (fixed
        2026-10-01: the earliest row used to apply backwards, so a recent test's
        CP / LTHR row leaked into every earlier date). Callers then fall
        back to what existed on `day`: WKO5's dated setting history
        (Dataset.setting / cp / aethr) or an estimate as of that date
        (racepower.athlete.thresholds_as_of)."""
        val = None
        for d, v in sorted((_d(t.date), getattr(t, name)) for t in self.thresholds
                           if getattr(t, name) is not None):
            if d <= day:
                val = v
        return val


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------

def auto_phases(events: list[Event], begin: dt.date, end: dt.date,
                transition_weeks: int = TRANSITION_WEEKS) -> list[Phase]:
    """Phases covering [begin, end], built backwards from each A event.
    After each A event's recovery: `transition_weeks` of 轉換期 (0 = none), ending before
    the next A event's 專項期 — shortened when that starts sooner (`note` says so), skipped
    when fewer than TRANSITION_MIN_DAYS are left (the recovery phase's `note` says so)."""
    one = dt.timedelta(days=1)
    a_events = sorted((e for e in events if e.priority == "A"), key=lambda e: e.start)
    out: list[Phase] = []
    cursor = begin                  # first day not yet assigned

    def add(kind, s, e, eid=None, note=""):
        s = max(s, cursor)
        if s <= e:
            out.append(Phase(kind, s.isoformat(), e.isoformat(), eid, note=note))
            return out[-1]
        return None

    def spec_start_of(ev: Event) -> dt.date:
        return ev.start - dt.timedelta(days=TAPER_DAYS) - dt.timedelta(weeks=SPECIFIC_WEEKS)

    for i, ev in enumerate(a_events):
        if ev.end < cursor:
            continue
        taper_start = ev.start - dt.timedelta(days=TAPER_DAYS)
        spec_start = spec_start_of(ev)
        add("base", cursor, spec_start - one)
        add("specific", spec_start, taper_start - one, ev.id)
        add("taper", taper_start, ev.start - one, ev.id)
        add("event", ev.start, ev.end, ev.id)
        cursor = max(cursor, ev.end + one)
        rec_days = 14 if ev.is_long else 7
        rec_end = ev.end + dt.timedelta(days=rec_days)
        rec = add("recovery", cursor, rec_end, ev.id)
        cursor = max(cursor, rec_end + one)
        if transition_weeks and transition_weeks > 0:
            # 轉換期 (SP-73): never into the next A race's 專項期 — that phase is planned backwards
            # from its race and wins
            t_end = rec_end + dt.timedelta(weeks=int(transition_weeks))
            nxt = next((x for x in a_events[i + 1:] if x.start > ev.end), None)
            limit = spec_start_of(nxt) - one if nxt is not None else None
            if limit is not None and limit < t_end:
                days = (limit - cursor).days + 1
                if days >= TRANSITION_MIN_DAYS:
                    note = _("轉換期縮短為 {days} 天：下一場 A 賽事「{name}」的專項期 {start} 開始",
                             days=days, name=nxt.name, start=(limit + one).isoformat())
                    add("transition", cursor, limit, ev.id, note)
                    cursor = max(cursor, limit + one)
                elif rec is not None:
                    rec.note = _("沒有轉換期：下一場 A 賽事「{name}」的專項期 {start} 開始，只剩 {days} 天",
                                 name=nxt.name, start=(limit + one).isoformat(), days=max(0, days))
            else:
                add("transition", cursor, t_end, ev.id)
                cursor = max(cursor, t_end + one)
    if cursor <= end:
        add("base", cursor, end)    # no A event ahead: open-ended base
    return [p for p in out if _d(p.start) <= end]


def transition_weeks_setting(user_id: int = 1) -> int:
    """課表偏好 轉換期週數 (plan.prefs.transition_weeks), read-only like plan_prefs.load;
    TRANSITION_WEEKS when unset or unreadable."""
    try:
        from backend.engine.wko5expr.datasource import read_setting
        v = read_setting(TRANSITION_SETTING, TRANSITION_WEEKS, user_id)
    except Exception:                       # noqa: BLE001 — phases must still build
        return TRANSITION_WEEKS
    if isinstance(v, bool) or not isinstance(v, int):
        return TRANSITION_WEEKS
    return max(TRANSITION_WEEKS_RANGE[0], min(TRANSITION_WEEKS_RANGE[1], v))


def phases(plan: Plan, begin: dt.date, end: dt.date, transition_weeks: Optional[int] = None) -> list[Phase]:
    """The manual phases when the user saved any (they win), else auto_phases.
    `transition_weeks` None = the 課表偏好 setting (transition_weeks_setting)."""
    if plan.phases:
        return plan.phases
    tw = transition_weeks_setting() if transition_weeks is None else transition_weeks
    return auto_phases(plan.events, begin, end, tw)


def phase_on(plan: Plan, day: dt.date, begin: Optional[dt.date] = None,
             transition_weeks: Optional[int] = None) -> Optional[Phase]:
    begin = begin or day - dt.timedelta(days=400)
    for p in phases(plan, begin, day + dt.timedelta(days=400), transition_weeks):
        if _d(p.start) <= day <= _d(p.end):
            return p
    return None


POST_RACE_KINDS = ("recovery", "transition")   # the planned post-race phases (SP-73)


def phase_days(plan: Plan, begin: dt.date, end: dt.date, kinds: tuple,
               transition_weeks: Optional[int] = None) -> set[dt.date]:
    """The days in [begin, end] inside a phase of one of `kinds` (auto or manual). Empty on any
    plan error."""
    out: set[dt.date] = set()
    try:
        ps = phases(plan, begin - dt.timedelta(days=400), end, transition_weeks)
    except Exception:                       # noqa: BLE001 — the callers must still work
        return out
    for p in ps:
        if p.kind not in kinds:
            continue
        a, b = _d(p.start), _d(p.end)
        if a is None or b is None:
            continue
        d = max(a, begin)
        while d <= min(b, end):
            out.add(d)
            d += dt.timedelta(days=1)
    return out


def transition_days(plan: Plan, begin: dt.date, end: dt.date,
                    transition_weeks: Optional[int] = None) -> set[dt.date]:
    """The days in [begin, end] inside a 轉換期 (auto or manual)."""
    return phase_days(plan, begin, end, ("transition",), transition_weeks)


def post_race_days(plan: Plan, begin: dt.date, end: dt.date,
                   transition_weeks: Optional[int] = None) -> set[dt.date]:
    """The days in [begin, end] inside a planned post-race phase — the A race's 恢復期 (7–14
    days) or the 轉換期 after it (auto or manual). SP-73 (owner 2026-10-05): both are planned
    rest / easy / cross-training blocks, so their days without a run are not a running break
    for the re-entry block (reentry.find_all) or the Zone 3 gate's gap / re-lock
    (quality_gate.z3_consistency), and their weeks are not a volume-step baseline
    (status.i_volume). Empty on any plan error."""
    return phase_days(plan, begin, end, POST_RACE_KINDS, transition_weeks)


def pre_race_mondays(phases_: list, day: dt.date, n: int = 4) -> list[dt.date]:
    """The Mondays of the `n` complete weeks before the taper of the A race whose 轉換期 /
    恢復期 contains `day` (the last `event` phase that ended before `day`; its `taper` phase
    start, else the event start − TAPER_DAYS) — the training level the 轉換期 volume is a share
    of (SP-73). [] without such an event. `phases_`: Phase objects or dicts."""
    def get(p, k):
        return p[k] if isinstance(p, dict) else getattr(p, k)
    evs = [p for p in phases_ if get(p, "kind") == "event" and _d(get(p, "end")) < day]
    if not evs:
        return []
    ev = max(evs, key=lambda p: _d(get(p, "start")))
    ev_start = _d(get(ev, "start"))
    tap = [p for p in phases_ if get(p, "kind") == "taper" and _d(get(p, "end")) == ev_start - dt.timedelta(days=1)]
    t0 = _d(get(tap[0], "start")) if tap else ev_start - dt.timedelta(days=TAPER_DAYS)
    first = t0 - dt.timedelta(days=t0.weekday())        # the taper's own week is not counted
    return [first - dt.timedelta(weeks=k) for k in range(n, 0, -1)]


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
