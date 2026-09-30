"""
Unit registry for WKO5 axis / series unit ids.

WKO5 names a chart axis (and every series' `y_axis` / `x_axis`) with a unit id:
WATTS, KJ, PERCENT, HHMMSS, METERS, FT, PACEKM, ..., or a free-form
`CUSTOM<label>` such as `CUSTOMkN/m`. This module says, for each id:

* ``label``    what to print next to the number (Traditional-Chinese UI, metric)
* ``kind``     number / duration / pace / percent / date — picks the formatter
* ``decimals`` how many decimals a value deserves, by magnitude: a tuple of
               ``(threshold, decimals)`` pairs, first match on ``|display value|``
* ``scale``    display multiplier: the value WKO5 stores times ``scale`` is what
               the label means. PERCENT stores fractions (x100 -> %). CM and
               MILLISECONDS need none: expressions see stancetime in ms and
               verticaloscillation in cm, as in WKO5 (evaluator EXPR_UNIT_SCALE).
* ``metric``   for imperial ids (FT, MI, MPH, PACEMI, FAHRENHEIT): the metric id
               and the value conversion ``metric = value * to_metric + offset``.

The evaluator keeps every channel metric; only ``english()`` produces imperial
numbers. So outside parity mode an imperial *id* is relabelled to its metric
twin, and ``english(`` in the expression is rewritten to ``metric(`` (see
:func:`metricize_expression`) — after that, every value is metric.

Pace ids are special: WKO5 lets several quantities sit on a PACEKM axis —
``ngp`` (a speed, km/h), ``duration/distance`` (s/km), ``runtpace`` (min/km).
:func:`pace_base` tells them apart; the viewer formats all of them as m:ss /km.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

Decimals = tuple  # ((threshold, decimals), ...), checked in order

AUTO: Decimals = ((100, 0), (10, 1), (0, 2))
D0: Decimals = ((0, 0),)
D1: Decimals = ((0, 1),)
D2: Decimals = ((0, 2),)

KM_PER_MI = 1.609344
M_PER_FT = 0.3048


@dataclass(frozen=True)
class Unit:
    id: str
    label: str
    kind: str = "number"            # number | duration | pace | percent | date
    decimals: Decimals = AUTO
    scale: float = 1.0              # display value = stored value * scale
    metric: Optional[str] = None    # imperial ids: metric twin
    to_metric: float = 1.0          # metric value = value * to_metric + offset
    offset: float = 0.0
    aliases: tuple = field(default=())

    @property
    def imperial(self) -> bool:
        return self.metric is not None

    def decimals_for(self, value: Optional[float]) -> int:
        """Decimals for one stored value (the rule looks at the display value)."""
        if value is None or not isinstance(value, (int, float)) or math.isnan(value):
            return self.decimals[-1][1]
        v = abs(value * self.scale)
        for thr, d in self.decimals:
            if v >= thr:
                return d
        return self.decimals[-1][1]

    def meta(self) -> dict:
        """JSON handed to the viewer."""
        out = {"id": self.id, "label": self.label, "kind": self.kind,
               "dec": [list(p) for p in self.decimals]}
        if self.scale != 1.0:
            out["scale"] = self.scale
        return out


def _u(id_, label, kind="number", decimals=AUTO, **kw) -> Unit:
    return Unit(id_, label, kind, decimals, **kw)


_REGISTRY: dict[str, Unit] = {u.id: u for u in (
    # power / work
    _u("WATTS", "W", decimals=D0),
    _u("WATTSKG", "W/kg", decimals=D2),
    _u("KJ", "kJ", decimals=D0),
    _u("JOULES", "J", decimals=D0),
    _u("KCAL", "kcal", decimals=D0),
    # fractions
    _u("PERCENT", "%", "percent", ((10, 0), (0, 1)), scale=100.0),
    # durations (stored in seconds)
    _u("HHMMSS", "h:mm:ss", "duration", D0),
    _u("HMSLONG", "h:mm:ss", "duration", D0),
    _u("HMSSHORT", "m:ss", "duration", D0),
    _u("HHMM", "h:mm", "duration", D0),
    _u("SECONDS", "s", "duration", D0),
    _u("MILLISECONDS", "ms", decimals=D0),                 # stancetime evaluates in ms
    # training load
    _u("TSS", "TSS", decimals=D0),
    _u("TSSPERDAY", "TSS/天", decimals=((10, 0), (0, 1))),
    _u("TSSPERDAYPERWEEK", "TSS/天/週", decimals=D1),
    # distance / elevation
    _u("METERS", "m", decimals=D0),
    _u("KM", "km", decimals=((100, 0), (10, 1), (0, 2))),
    _u("CM", "cm", decimals=D1),                            # verticaloscillation in cm
    # speed / pace
    _u("KPH", "km/h", decimals=D1),
    _u("METERSPERSECOND", "m/s", decimals=D2),
    _u("METERSPERHOUR", "m/h", decimals=D0),
    _u("PACEKM", "/km", "pace", D0),
    # physiology
    _u("BPM", "bpm", decimals=D0),
    _u("RPM", "spm", decimals=D0),
    _u("steps/min", "spm", decimals=D0),
    _u("L/min", "L/min", decimals=D2),
    _u("mL/min/kg", "mL/min/kg", decimals=D1),
    _u("KG", "kg", decimals=D1),
    _u("CELSIUS", "°C", decimals=D1),
    # misc
    _u("DATE", "日期", "date", D0),
    _u("NONE", "", decimals=AUTO),
    # imperial -> metric twins
    _u("FT", "ft", decimals=D0, metric="METERS", to_metric=M_PER_FT),
    _u("MI", "mi", decimals=D2, metric="KM", to_metric=KM_PER_MI),
    _u("MPH", "mph", decimals=D1, metric="KPH", to_metric=KM_PER_MI),
    # pace min/mi -> min/km: the same time over a longer unit, so divide
    _u("PACEMI", "/mi", "pace", D0, metric="PACEKM", to_metric=1 / KM_PER_MI),
    _u("FAHRENHEIT", "°F", decimals=D0, metric="CELSIUS", to_metric=5 / 9, offset=-32 * 5 / 9),
)}

# CUSTOM<label> ids whose label is a known quantity: nicer label / decimals
_CUSTOM_KNOWN: dict[str, tuple[str, Decimals]] = {
    "w": ("W", D0), "watts": ("W", D0), "j": ("J", D0), "kj": ("kJ", D0),
    "tss/week": ("TSS/週", D0), "tss/day/week": ("TSS/天/週", D1),
    "m/km": ("m/km", D0), "m/min/bpm": ("m/min/bpm", D2),
    "次": ("次", D0), "count": ("次", D0), "minutes": ("分鐘", D0),
    "公里": ("km", ((100, 0), (10, 1), (0, 2))), "km total distance": ("km", ((100, 0), (10, 1), (0, 2))),
    "ep": ("EP", D0), "eph": ("EP/h", D0), "@eph": ("EP/h", D0),
}


def custom_label(uid: str) -> Optional[str]:
    """`CUSTOMkN/m` -> `kN/m`; None for non-custom ids."""
    return uid[6:] if uid.startswith("CUSTOM") else None


def unit(uid: Optional[str], sport: Optional[str] = None) -> Unit:
    """Unit for any id. Unknown / CUSTOM ids get their text as the label and
    decimals by magnitude (|v| >= 100 -> 0, >= 10 -> 1, else 2)."""
    uid = (uid or "NONE").strip() or "NONE"
    u = _REGISTRY.get(uid)
    if u is None and uid.upper() in _REGISTRY:
        u = _REGISTRY[uid.upper()]
    if u is not None:
        if u.id == "RPM" and sport == "bike":
            return Unit("RPM", "rpm", "number", D0)
        return u
    lab = custom_label(uid)
    if lab is not None:
        lab = lab.strip()
        known = _CUSTOM_KNOWN.get(lab.lower())
        if known:
            return Unit(uid, known[0], "number", known[1])
        return Unit(uid, lab, "number", AUTO)
    return Unit(uid, uid, "number", AUTO)


def is_imperial(uid: Optional[str]) -> bool:
    return unit(uid).imperial


def metric_id(uid: Optional[str]) -> str:
    """The metric id for an imperial id; any other id unchanged."""
    u = unit(uid)
    return u.metric if u.metric else (uid or "NONE")


def to_metric_value(uid: Optional[str], v: Optional[float]) -> Optional[float]:
    """Convert one value stored in imperial `uid` units to its metric twin."""
    u = unit(uid)
    if v is None or not u.imperial:
        return v
    return v * u.to_metric + u.offset


_ENGLISH_RE = re.compile(r"\benglish\s*\(", re.IGNORECASE)


def uses_english(expr: Optional[str]) -> bool:
    return bool(expr) and bool(_ENGLISH_RE.search(expr))


def metricize_expression(expr: Optional[str]) -> Optional[str]:
    """`english(x)` -> `metric(x)`: the evaluator stores everything metric and
    `metric()` is the identity, so the result is the same quantity in metric."""
    if not expr:
        return expr
    return _ENGLISH_RE.sub("metric(", expr)


# ---------------------------------------------------------------------------
# pace
# ---------------------------------------------------------------------------

_SPEED_RE = re.compile(r"\b(ngp|gap|speed|runspeed|avgspeed|metric\(speed\))\b", re.IGNORECASE)
_INVERTED_SPEED_RE = re.compile(r"\d\s*/\s*(\w+\()?\s*(ngp|gap|speed|runspeed)", re.IGNORECASE)


def pace_base(values: Iterable[float], expr: Optional[str] = "") -> str:
    """What a PACEKM/PACEMI series' numbers are:
        "s"   seconds per km (e.g. avg(duration/distance) ~ 300-900)
        "kph" a speed in km/h that WKO5 displays as pace (ngp, speed)
        "min" minutes per km (runtpace, 60/runspeed)
    """
    vals = sorted(abs(v) for v in values if v is not None and not math.isnan(v) and v != 0)
    med = vals[len(vals) // 2] if vals else None
    if med is not None and med >= 60:
        return "s"
    e = expr or ""
    if _SPEED_RE.search(e) and not _INVERTED_SPEED_RE.search(e) and "duration" not in e.lower():
        return "kph"
    return "min"


def pace_to_min_per_km(v: Optional[float], base: str) -> Optional[float]:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if base == "s":
        return v / 60.0
    if base == "kph":
        return 60.0 / v if v > 0 else None
    return v


# ---------------------------------------------------------------------------
# formatting (mirrors the viewer; used by the audit / report and tests)
# ---------------------------------------------------------------------------

def fmt_duration(secs: float, style: Optional[str] = None) -> str:
    s = int(round(secs))
    sign = "-" if s < 0 else ""
    s = abs(s)
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    if style == "hm":
        return f"{sign}{h}:{m:02d}"
    if h:
        return f"{sign}{h}:{m:02d}:{x:02d}"
    return f"{sign}{m}:{x:02d}"


def format_value(v: Optional[float], u: Unit, with_label: bool = True, base: str = "min") -> str:
    """Human text for one stored value in unit `u`."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    if u.kind == "duration":
        return fmt_duration(v, "hm" if u.id == "HHMM" else None)
    if u.kind == "pace":
        p = pace_to_min_per_km(v, base)
        if p is None:
            return "—"
        if u.id == "PACEMI":
            return f"{fmt_duration(p * 60)} /mi" if with_label else fmt_duration(p * 60)
        return f"{fmt_duration(p * 60)} /km" if with_label else fmt_duration(p * 60)
    if u.kind == "date":
        from backend.engine.wko5expr.dataset import day_to_date
        try:
            return day_to_date(v).isoformat()
        except (ValueError, OverflowError, TypeError):
            return str(v)
    d = u.decimals_for(v)
    txt = f"{v * u.scale:.{d}f}"
    if u.kind == "percent":
        return txt + "%"
    return f"{txt} {u.label}".rstrip() if with_label and u.label else txt


def meta(uid: Optional[str], sport: Optional[str] = None, **extra) -> dict:
    m = unit(uid, sport).meta()
    m.update({k: v for k, v in extra.items() if v is not None})
    return m
