"""
Body weight from Apple Health (SP-472; docs/research/weight-sync.md): the rules, no I/O.

The weight API (api/weight.py) receives measurements from the iOS Shortcut or Health Auto Export
and stores every one (weight_measurements). Writing a weight into plan.json's `weights` is what
costs: the estimate cache keys on the plan (fitdataset._est_inputs) and a new weight re-pushes the
課表's power targets (api/plan._notify). So nothing is applied when a measurement arrives; once a
night (api/weight.tick, 03:00 local) `choose` decides which days become entries:

  1. one value per local day: the day's LAST measurement (by measured time; a later copy of the
     same sample replaces it);
  2. manual first: the newest entry the user wrote (設定頁, or the COROS weight they accepted) is
     never changed or removed, and no measurement dated on or before that entry's day is applied —
     measurements of the days AFTER it are compared with it (rules 3 and 4) like any other entry;
  3. a day that already has an automatic entry takes the day's last value (same-day correction);
  4. otherwise a new entry when the value differs from the weight in effect that day
     (Plan.weight_on) by ≥ MIN_DIFF_KG, or the previous entry is ≥ MAX_GAP_DAYS older
     (or there is no entry before it at all).

`parse` reads both payloads (the Shortcut's {kg, unit, time} or {measurements: [...]}, and Health
Auto Export's {data: {metrics: [{name: weight_body_mass, units, data: [{qty, date, source}]}]}}),
checking the range and the time of each value; `coros_hint` is the 「要更新嗎？」 of the COROS
account weight (never written by itself).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Optional

KG_MIN, KG_MAX = 25.0, 250.0         # the same range as the 設定頁 (api/plan.put_profile)
LB_PER_KG = 2.2046226218
MIN_DIFF_KG = 0.5                    # owner 2026-10-13
MAX_GAP_DAYS = 7
COROS_DIFF_KG = 1.0
MAX_ITEMS = 1000                     # values in one request
MAX_AGE_DAYS = 3 * 366               # older values are refused (a typo'd year)
FUTURE_SLACK = dt.timedelta(hours=2)  # a phone clock a little ahead
AUTO = "apple_health"                # Weight.source of an applied measurement
MANUAL_SOURCES = (None, "", "manual", "coros")
SOURCES = ("shortcut", "health_auto_export")
_HAE_NAMES = ("weightbodymass",)     # weight_body_mass / weight_&_body_mass, normalised
_UNITS = {"kg": 1.0, "kgs": 1.0, "lb": 1 / LB_PER_KG, "lbs": 1 / LB_PER_KG, "g": 0.001}


@dataclass
class Item:
    kg: float
    measured_at: dt.datetime          # aware
    device: Optional[str] = None


@dataclass
class Parsed:
    source: str
    items: list = field(default_factory=list)
    rejected: list = field(default_factory=list)     # [{"i": index, "reason": code}]
    ignored: int = 0                                  # Health Auto Export metrics other than weight


class PayloadError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _number(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    elif isinstance(v, str):
        s = v.strip().replace(",", ".")       # 72,3 from a comma-decimal locale
        m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*)", s)
        if not m:
            return None
        x = float(m.group(1))
    else:
        return None
    return x if math.isfinite(x) else None


def _unit_of(v, unit) -> Optional[float]:
    """The factor to kg: an explicit unit, else a unit written after the number, else kg."""
    u = unit
    if u is None and isinstance(v, str):
        m = re.search(r"([A-Za-z]+)\s*$", v.strip())
        u = m.group(1) if m else None
    if u is None or str(u).strip() == "":
        return 1.0
    return _UNITS.get(str(u).strip().lower())


_HAE_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)\s*([+-]\d{2}:?\d{2}|Z)?$")


def parse_time(v, tz: dt.tzinfo) -> Optional[dt.datetime]:
    """ISO 8601 (the Shortcut's 「格式化日期 › ISO 8601」), Health Auto Export's
    'yyyy-MM-dd HH:mm:ss Z' or Unix seconds; a time without an offset is the athlete's zone."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        try:
            return dt.datetime.fromtimestamp(float(v), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(v, str) or not v.strip() or len(v) > 40:
        return None
    s = v.strip()
    m = _HAE_TIME.match(s)
    if m:
        off = m.group(3)
        if off and off != "Z" and ":" not in off:
            off = off[:3] + ":" + off[3:]
        s = f"{m.group(1)}T{m.group(2)}{off or ''}"
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.replace(tzinfo=tz) if t.tzinfo is None else t


def _item(kg_raw, unit, time_raw, device, tz, now) -> tuple[Optional[Item], Optional[str]]:
    x = _number(kg_raw)
    if x is None:
        return None, "kg_missing"
    f = _unit_of(kg_raw, unit)
    if f is None:
        return None, "unit_unknown"
    kg = round(x * f, 2)
    if not KG_MIN <= kg <= KG_MAX:
        return None, "kg_out_of_range"
    if time_raw in (None, ""):
        return None, "time_missing"
    t = parse_time(time_raw, tz)
    if t is None:
        return None, "time_unreadable"
    if t > now + FUTURE_SLACK:
        return None, "time_in_future"
    if t < now - dt.timedelta(days=MAX_AGE_DAYS):
        return None, "time_too_old"
    dev = device.strip()[:80] if isinstance(device, str) and device.strip() else None
    return Item(kg, t, dev), None


def parse(body, tz: dt.tzinfo, now: Optional[dt.datetime] = None) -> Parsed:
    """The request body (already JSON-decoded) → the values and what was refused. Raises
    PayloadError(bad_shape | too_many) for a body that is neither payload."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(body, dict):
        raise PayloadError("bad_shape")
    raw: list = []                        # (kg, unit, time, device)
    ignored = 0
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("metrics"), list):
        source = "health_auto_export"
        for m in data["metrics"]:
            if not isinstance(m, dict):
                continue
            name = re.sub(r"[^a-z]", "", str(m.get("name") or "").lower())
            if name not in _HAE_NAMES:
                ignored += 1
                continue
            rows = m.get("data")
            if not isinstance(rows, list):
                continue
            for r in rows:
                if len(raw) > MAX_ITEMS:
                    raise PayloadError("too_many")
                r = r if isinstance(r, dict) else {}
                raw.append((r.get("qty"), r.get("units") or m.get("units"), r.get("date"), r.get("source")))
    else:
        source = "shortcut"
        rows = body.get("measurements")
        if rows is None:
            rows = [body]
        if not isinstance(rows, list):
            raise PayloadError("bad_shape")
        if len(rows) > MAX_ITEMS:
            raise PayloadError("too_many")
        for r in rows:
            r = r if isinstance(r, dict) else {}
            kg = r.get("kg", r.get("weight", r.get("value")))
            raw.append((kg, r.get("unit"), r.get("time", r.get("date")), r.get("device") or r.get("source")))
        if not any(v is not None for v, *_ in raw):
            raise PayloadError("bad_shape")
    if len(raw) > MAX_ITEMS:
        raise PayloadError("too_many")
    out = Parsed(source=source, ignored=ignored)
    for i, (kg, unit, t, dev) in enumerate(raw):
        it, why = _item(kg, unit, t, dev, tz, now)
        if it is None:
            out.rejected.append({"i": i, "reason": why})
        else:
            out.items.append(it)
    return out


# ---------------------------------------------------------------------------- the nightly choice

def is_manual(w) -> bool:
    return getattr(w, "source", None) in MANUAL_SOURCES


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def weight_on(entries: list, day: dt.date) -> Optional[float]:
    """Plan.weight_on over `entries` (the entry on / before `day`; before the first, the first)."""
    ws = sorted(((_d(w.date), w.kg) for w in entries if w.kg), key=lambda x: x[0])
    val = None
    for d, kg in ws:
        if d <= day:
            val = kg
    return val if val is not None else (ws[0][1] if ws else None)


def choose(entries: list, days: dict, make) -> tuple[list, list]:
    """(the new entries, one decision per day). `entries`: plan.json's Weight rows; `days`:
    {local date: kg} — each day's last measurement; `make(date_iso, kg)`: a new Weight.
    Decision: {"day", "kg", "action": added | replaced | skipped, "reason"} with reason
    first | diff | gap | same_day (added / replaced) or manual | small | same (skipped)."""
    out = list(entries)
    manual = [_d(w.date) for w in out if is_manual(w)]
    cut = max(manual) if manual else None
    decisions = []
    for day in sorted(days):
        exact = float(days[day])
        kg = round(exact, 1)                       # the entry keeps 0.1 kg; the threshold sees the value
        rec = {"day": day.isoformat(), "kg": kg}
        if cut is not None and day <= cut:
            decisions.append({**rec, "action": "skipped", "reason": "manual"})
            continue
        same = next((w for w in out if _d(w.date) == day), None)
        if same is not None:                       # an automatic entry (a manual one is ≤ cut)
            if abs(same.kg - kg) < 0.05:
                decisions.append({**rec, "action": "skipped", "reason": "same"})
            else:
                out = [w for w in out if w is not same] + [make(day.isoformat(), kg)]
                decisions.append({**rec, "action": "replaced", "reason": "same_day", "was": same.kg})
            continue
        eff = weight_on(out, day)
        before = [_d(w.date) for w in out if _d(w.date) < day]
        prev = max(before) if before else None
        if eff is None:
            reason = "first"
        elif abs(exact - eff) >= MIN_DIFF_KG - 1e-9:
            reason = "diff"
        elif prev is None or (day - prev).days >= MAX_GAP_DAYS:
            reason = "gap"
        else:
            decisions.append({**rec, "action": "skipped", "reason": "small", "in_effect": eff})
            continue
        out.append(make(day.isoformat(), kg))
        decisions.append({**rec, "action": "added", "reason": reason, "in_effect": eff})
    out.sort(key=lambda w: w.date)
    return out, decisions


def last_per_day(rows: list) -> dict:
    """{day: row} — the last measurement of each local day. rows: objects with .day,
    .measured_at, .received_at, .id."""
    best: dict = {}
    for r in rows:
        k = (r.measured_at, r.received_at or dt.datetime.min, r.id or 0)
        cur = best.get(r.day)
        if cur is None or k > cur[0]:
            best[r.day] = (k, r)
    return {d: v[1] for d, v in best.items()}


# ---------------------------------------------------------------------------- COROS account weight

def coros_hint(entries: list, coros: Optional[tuple], dismissed: Optional[float]) -> Optional[dict]:
    """The 「COROS 帳號的體重是 X kg，要更新嗎？」 hint: the COROS account's weight (kg, date) differs
    from the newest entry by ≥ COROS_DIFF_KG, and the user has not said 「不用」 to that value.
    None without an entry (the first-run 精靈 offers the COROS weight then) or without a COROS one."""
    if not coros or not entries or coros[0] is None:
        return None
    kg = round(float(coros[0]), 1)
    last = max(entries, key=lambda w: w.date)
    if abs(kg - float(last.kg)) < COROS_DIFF_KG - 1e-9:
        return None
    if dismissed is not None and abs(float(dismissed) - kg) < 0.05:
        return None
    return {"kg": kg, "date": str(coros[1])[:10] if coros[1] else None, "current_kg": float(last.kg),
            "current_date": last.date[:10], "diff": round(kg - float(last.kg), 1)}
