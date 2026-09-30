"""
Evaluator for WKO5 chart expressions at the athlete level (season charts),
with per-workout evaluation for sample-level sub-expressions.

Value kinds:
    scalar      float | None (None = WKO5 "invalid"/na) | str
    WS          per-workout series  {workout idx: value}
    DS          daily series        Daily(start_day, np.ndarray) — NaN = na
    samples     np.ndarray of one workout's samples (inside workout context)
    ListV       {a, b, ...}  (gauge values)
    RangeV      {lo:hi[:step]}  (bands / axes)
    PairV       (x, y) or (,y)  (explicit points / horizontal line)

Semantics chosen to match WKO5 (see docs/wko5-internals/ when confirmed):
  * Aggregations (sum/avg/max/min/count) over sample-level arguments are
    evaluated once per workout -> WS. Over WS / DS they reduce to a scalar
    within the current athleterange.
  * athleterange(a, b, e) limits aggregation and output to days [a, b];
    tl() always integrates the full history so CTL doesn't restart.
  * tl() EWMA recurrence, rampconstant, ewma() factor and tss are marked
    PROVISIONAL until verified against WKO5.
"""
from __future__ import annotations

import datetime as dt
import math
import operator
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from backend.engine.algorithms.wko5_meanmax import meanmax_time
from backend.engine.algorithms.wko5_pdmodel import (
    aerobic as pd_aerobic, anaerobic as pd_anaerobic, fit as pdfit, model as pdmodel,
)
from backend.engine.algorithms.wko5_power import rapower
from backend.engine.wko5expr import parser as P
from backend.engine.wko5expr.dataset import (
    SPORT_SETTING_PREFIX, Dataset, Workout, day_to_date, date_to_day,
)
from backend.files.wko5_athlete import text_field


class EvalError(Exception):
    pass


# ---------------------------------------------------------------------------
# value types
# ---------------------------------------------------------------------------

class WS(dict):
    """Per-workout series: {workout idx -> value}."""


@dataclass
class Daily:
    start: int                 # first day number
    values: np.ndarray         # float, NaN = na

    def at(self, day: float) -> float:
        i = int(math.floor(day)) - self.start
        return float(self.values[i]) if 0 <= i < len(self.values) else math.nan

    def clip(self, lo: Optional[int], hi: Optional[int]) -> "Daily":
        s = self.start if lo is None else max(self.start, lo)
        e = self.start + len(self.values) - 1 if hi is None else min(self.start + len(self.values) - 1, hi)
        if e < s:
            return Daily(s, np.array([], dtype=float))
        return Daily(s, self.values[s - self.start:e - self.start + 1].copy())


@dataclass
class ListV:
    items: list


@dataclass
class RangeV:
    lo: Any
    hi: Any
    step: Any = None


@dataclass
class PairV:
    x: Any
    y: Any


@dataclass
class Curve:
    """An (x, y) set. Mostly mean-max / power-duration curves (xs = durations
    in s), but also any other explicit pair set — a histogram from bin(), a
    regression line, `(xlist, ylist)` — in which case `xkind` says what x is
    ("duration" | "value" | "date" = day number)."""
    xs: list
    ys: list
    fit: Optional[dict] = None       # cached wko5_pdmodel.fit() result
    xkind: str = "duration"
    # display only: where if() dropped points inside the curve, as
    # (index of the first point after the hole, x of the first dropped point);
    # the renderer draws a break there instead of bridging the hole
    gaps: Optional[list] = None

    def at(self, x: float) -> float:
        """Linear interpolation, clamped to the ends (WKO5's li())."""
        if not self.xs:
            return math.nan
        if x <= self.xs[0]:
            return self.ys[0] if self.ys[0] is not None else math.nan
        if x >= self.xs[-1]:
            return self.ys[-1] if self.ys[-1] is not None else math.nan
        for i in range(1, len(self.xs)):
            if self.xs[i] >= x:
                x0, x1 = self.xs[i - 1], self.xs[i]
                y0, y1 = self.ys[i - 1], self.ys[i]
                if y0 is None or y1 is None:
                    return math.nan
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y0
        return math.nan


def _is_na(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _num(v) -> float:
    if _is_na(v):
        return math.nan
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, np.floating, np.integer)):
        return float(v)
    if isinstance(v, PairV) and v.x is None:
        return _num(v.y)
    return math.nan


def _unwrap(v):
    """`(,y)` — a pair without x — acts as its y value in arithmetic."""
    while isinstance(v, PairV) and v.x is None and v.y is not None:
        v = v.y
    return v


def _is_set(v) -> bool:
    return isinstance(v, (np.ndarray, WS, Daily, ListV, Curve))


def _values(v) -> np.ndarray:
    """Every y value of any value kind as a flat float array (na = NaN)."""
    v = _unwrap(v)
    if isinstance(v, np.ndarray):
        return v.astype(float)
    if isinstance(v, WS):
        return np.array([_num(x) for x in v.values()], dtype=float)
    if isinstance(v, Daily):
        return v.values.astype(float)
    if isinstance(v, Curve):
        return np.array([_num(y) for y in v.ys], dtype=float)
    if isinstance(v, ListV):
        parts = [_values(x.y if isinstance(x, PairV) else x) for x in v.items]
        return np.concatenate(parts) if parts else np.array([], dtype=float)
    if isinstance(v, PairV):
        return _values(v.y) if v.y is not None else np.array([], dtype=float)
    return np.array([_num(v)], dtype=float)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

CHANNELS = {"power", "runpower", "bikepower", "heartrate", "runheartrate", "speed", "runspeed",
            "cadence", "runcadence", "elevation", "_elevation", "deltatime", "_rapower4",
            "temperature", "stancetime", "verticaloscillation", "elapsedtime", "_rapower",
            "elapseddistance", "ecpower", "rngp", "fmax", "kleg", "rgrade"}
RUN_ONLY = {"runpower": "power", "runheartrate": "heartrate", "runspeed": "speed", "runcadence": "cadence"}
BIKE_ONLY = {"bikepower": "power"}

ATHLETE_CONSTANTS = {"ctlconstant", "atlconstant", "rampconstant"}
# Expression Reference "constants". g = 9.80665 (double @0x86c220, loaded for the
# name "g" at 0x71b013, unit METERSPERSECOND), e = @0x86c180 (0x71b0aa) — DISASSEMBLY.
CONSTANTS = {"na": math.nan, "e": math.e, "pi": math.pi, "g": 9.80665}
# Workout text variables -> athlete index workout record (3202) field ids.
# DISASSEMBLY (summary-level resolver 0x4f93e6 + index serializer 0x4fb517):
# title = 3213 (WKO5 fills it with the workout type on import, e.g. "Trail
# Running"), desc/description = 3206, notes = 3207, code = 3210. 3206/3207 may be
# gzip-compressed (backend/files/wko5_athlete.text_field).
TEXT_FIELDS = {"title": 3213, "description": 3206, "desc": 3206, "notes": 3207, "code": 3210}
# Derived channels WKO5 defines as expression strings: the channel-definition
# function 0x7242ac creates each one for the listed sport groups only (DISASSEMBLY;
# the strings are quoted verbatim). They see identifiers in WKO5's display units
# (EXPR_UNIT_SCALE: stancetime ms, height cm), like every other expression.
# A sport set of None = every sport.
CHANNEL_EXPRS = {
    # "Elevation Corrected Power" (@0x860d18, 0x724bcc; Bike or Run): 25 s EWMA of
    # power divided by f(h) = -6.74e-9 h^2 - 2.74e-5 h + 0.997 (h = elevation, m);
    # above sftp only the part below sftp is scaled up: (p/f - sftp)*f + sftp.
    "ecpower": ({"bike", "run"},
                "if(ewma(power,25)/(-0.00000000674*(metric(elevation))^2-0.0000274*(metric(elevation))+.997)<=sftp,"
                "ewma(power,25)/(-0.00000000674*(metric(elevation))^2-0.0000274*(metric(elevation))+.997),"
                "((ewma(power,25)/(-0.00000000674*(metric(elevation))^2-0.0000274*(metric(elevation))+.997)-sftp)"
                "*(-0.00000000674*(metric(elevation))^2-0.0000274*(metric(elevation))+.997)+sftp))"),
    # "Maximum Force" in N (@0x860e88, 0x7251a1; Run): Morin et al. (2005) sine-wave
    # model, contact time tc = stancetime, flight time = 60000/cadence/2 - tc.
    "fmax": ({"run"}, "(metric(weight)*g)*(pi/2)*((60*1000/cadence/2-stancetime)/stancetime+1)"),
    # leg stiffness in kN/m (0x725058; Run): Fmax / leg compression (Morin 2005, leg
    # length 0.53 * height).
    "kleg": ({"run"},
             "if(stancetime>0,((metric(weight)*g)*(pi/2)*((60*1000/cadence/2-stancetime)/stancetime+1))"
             "/(metric(height)/100*0.53-((metric(height)/100*0.53)^2-(metric(speed)/3.6*stancetime/2000)^2)^0.5"
             "+((metric(weight)*g)*(pi/2)*((60*1000/cadence/2-stancetime)/stancetime+1)*(stancetime/1000)^2"
             "/(metric(weight)*pi^2)+g*(stancetime/1000)^2/8))/1000)"),
}
# "rgrade" (0x724cfd; all sports, PERCENT), WKO5's channel string verbatim. The
# evaluator computes it step by step in Evaluator._rgrade (same arithmetic), with
# one guard the string does not have: a horizontal run below 1 cm is na.
RGRADE_EXPR = ("filter(metric(_elevation-shift(_elevation,1))/sqrt((metric(elapseddistance-"
               "shift(elapsedDistance,1))*1000)^2-metric(_elevation-shift(_elevation,1))^2),"
               "gaussian(3,17),2)")
RGRADE_MIN_RUN_M = 0.01
# WKO5 hands identifiers to expressions in display units (the Palladino report's
# note; the fmax/kleg strings divide stancetime by 1000 and height by 100): the
# .wko4 files store stancetime in s, verticaloscillation and the height setting
# in m, so they are scaled on read — stancetime ms, verticaloscillation cm,
# height cm. Everything else is metric storage = metric display already.
EXPR_UNIT_SCALE = {"stancetime": 1000.0, "verticaloscillation": 100.0}
SETTING_UNIT_SCALE = {"height": 100.0}
# rngp = "1000/_ragpace" (0x724e46; Run only), evaluated by _rngp().
RUN_ONLY_DERIVED = {"rngp"}
# Built-in variables that WKO5.exe defines as expression strings
# (docs/wko5-internals/formulas.md §6.10 / §6.12 — string literals in WKO5.exe).
BUILTIN_EXPRS = {
    "tisaerobic": (
        "workoutrange(begintime,endtime,@lookback:=90,"
        "@ftp:=sport(sport).athleterange(date-@lookback+1,date,ftp(meanmax(power))),"
        "@height:=@ftp*1.3,@width:=0.105,@center:=@ftp*1,@ewmapower:=ewma(power,18),"
        "@weighting:=-(@width*(@ewmapower-@center))^2+@height,"
        "@weighting:=if(@weighting>0,@weighting,0),"
        "@weightedwork:=@ewmapower*@weighting*deltatime/1000,"
        "@score:=sum(@weightedwork)/(@ftp*3.6)/85,"
        "if(count(@ewmapower)>0,clamp(round(@score)+1,1,10)))"),
    "tisanaerobic": (
        "workoutrange(begintime,endtime,@lookback:=90,@weighting:=1.379,"
        "@threshold:=sport(sport).athleterange(date-@lookback+1,date,ftp(meanmax(power)))*.85,"
        "@frc:=sport(sport).athleterange(date-@lookback+1,date,frc(meanmax(power))),"
        "@ewmapower:=ewma(power,18),"
        "@powerabove:=if(@ewmapower>@threshold,@ewmapower-@threshold,0),"
        "@weightedpowerabove:=@powerabove^@weighting,"
        "@work:=(@weightedpowerabove*deltatime/1000),"
        "@score:=sum(@work)/@frc/3.6,"
        "if(count(@ewmapower)>0,clamp(round(@score)+1,1,10)))"),
    "stamina": (
        "clamp(1+(s(meanmax(_rapower4)^.25)*(1+ln(3600/Dmax(meanmax(_rapower4)^0.25))))"
        "/ftp(meanmax(_rapower4)^0.25),0,100)"),
}
# Built-ins that are evaluated once per workout (they need the workout's samples).
PER_WORKOUT_BUILTINS = {"tisaerobic", "tisanaerobic"}
# Identifiers the evaluator resolves itself (besides channels / metrics / settings).
BUILTIN_IDENTS = (set(CONSTANTS) | set(TEXT_FIELDS) | set(BUILTIN_EXPRS) | set(CHANNEL_EXPRS)
                  | {"begintime", "endtime", "rngp"})
# Setting aliases: the settings resolver 0x71dba0 rewrites "sftp" to
# lower("Bike") + "ftp" (0x71dbe9..0x71dc20) — for every sport (DISASSEMBLY).
SETTING_ALIASES = {"sftp": "bikeftp"}
# season-plan targets (not WKO5 identifiers) -> planning.GOAL_FIELDS key
GOAL_IDENTS = {"goaldistance": "distance_km", "goalclimbing": "climbing_m",
               "goalclimbperkm": "climb_per_km", "goalhours": "est_hours", "goaldays": "days"}
WORKOUT_METRICS = {"sport", "date", "title", "tss", "if", "np", "distance", "climbing", "descending",
                   "duration", "movingduration", "plannedtss", "work", "tssduration", "ngp", "vam",
                   "grade", "elevationchange", "pwhr", "pahr", "ef", "vi", "hrtss", "hrif"}


@dataclass
class Ctx:
    ds: Dataset
    begin: int                          # chart begindate (day)
    end: int                            # chart enddate (day)
    rng: Optional[tuple[int, int]]      # active athleterange (days, inclusive)
    vars: dict
    workout: Optional[Workout] = None   # set inside per-workout evaluation
    window: Optional[tuple[float, float]] = None   # workoutrange: elapsed seconds [lo, hi]
    full: bool = False                  # tl(): whole history, not the chart range
    sportf: Optional[str] = None        # sport(x).athleterange(...): only this sport group

    def child(self, **kw) -> "Ctx":
        c = Ctx(self.ds, self.begin, self.end, self.rng, self.vars, self.workout, self.window,
                self.full, self.sportf)
        for k, v in kw.items():
            setattr(c, k, v)
        return c


# Calls that consume sample data themselves (lift per workout internally).
SAMPLE_CONSUMERS = {"sum", "avg", "max", "min", "count", "meanmax", "athleterange", "workoutrange",
                    "pdcurve", "ftp", "frc", "pmax", "vo2max", "tte", "stamina", "pdprofile",
                    "stddev", "pstddev", "variance", "pvariance", "slrm", "slrb", "slrrsq", "bin",
                    "ftpcurve", "frccurve", "levelfrom", "levelto", "targetpower", "targetduration"}

# reductions handled by _reduce (sum/avg/... over a set)
REDUCE_KINDS = {"sum", "avg", "max", "min", "count", "stddev", "pstddev", "variance", "pvariance",
                "slrm", "slrb", "slrrsq"}


def needs_samples(node: P.Node) -> bool:
    """True if `node` references channel data that is NOT already consumed by
    a nested aggregation / athleterange (those lift per workout themselves)."""
    if isinstance(node, P.Ident):
        return node.name in CHANNELS
    if isinstance(node, P.Call) and node.name in SAMPLE_CONSUMERS:
        return False
    for v in vars(node).values():
        if isinstance(v, P.Node) and needs_samples(v):
            return True
        if isinstance(v, list) and any(isinstance(x, P.Node) and needs_samples(x) for x in v):
            return True
    return False


# ---------------------------------------------------------------------------
# elementwise lifting
# ---------------------------------------------------------------------------

def _scalar_op(op: Callable, a, b):
    if isinstance(a, str) or isinstance(b, str):
        if op is operator.add and isinstance(a, str) and isinstance(b, str):
            return a + b                      # string concatenation (Reference: string())
        if op in (operator.eq, operator.ne):
            sa = a.lower() if isinstance(a, str) else a
            sb = b.lower() if isinstance(b, str) else b
            return 1.0 if op(sa, sb) else 0.0
        return math.nan
    x, y = _num(a), _num(b)
    if math.isnan(x) or math.isnan(y):
        return math.nan
    try:
        r = op(x, y)
    except ZeroDivisionError:
        return math.nan
    except (OverflowError, ValueError):
        return math.nan
    return float(r) if not isinstance(r, bool) else (1.0 if r else 0.0)


def _curve_operand(v, base: Curve) -> np.ndarray:
    """`v` as an array aligned with `base.xs` (for Curve arithmetic)."""
    n = len(base.xs)
    if isinstance(v, Curve):
        if list(v.xs) == list(base.xs):
            return np.array([_num(y) for y in v.ys], dtype=float)
        m = {x: _num(y) for x, y in zip(v.xs, v.ys)}
        return np.array([m.get(x, math.nan) for x in base.xs], dtype=float)
    if isinstance(v, (np.ndarray, ListV)):
        arr = _values(v)
        out = np.full(n, np.nan)
        out[:min(n, len(arr))] = arr[:n]
        return out
    return np.full(n, _num(v))


def lift2(op: Callable, a, b):
    """Apply a binary op with WKO5 broadcasting rules."""
    a, b = _unwrap(a), _unwrap(b)
    if isinstance(a, ListV) or isinstance(b, ListV):
        # {..} op {..} pairs up elementwise; {..} op x broadcasts x. A one-item
        # list against another set acts as its single value.
        if isinstance(a, ListV) and isinstance(b, ListV):
            return ListV([lift2(op, x, y) for x, y in zip(a.items, b.items)])
        if isinstance(a, ListV):
            if len(a.items) == 1 and _is_set(b):
                return lift2(op, a.items[0], b)
            return ListV([lift2(op, x, b) for x in a.items])
        if len(b.items) == 1 and _is_set(a):
            return lift2(op, a, b.items[0])
        return ListV([lift2(op, a, y) for y in b.items])
    if isinstance(a, Curve) or isinstance(b, Curve):
        base = a if isinstance(a, Curve) else b
        A, B = _curve_operand(a, base), _curve_operand(b, base)
        with np.errstate(all="ignore"):
            r = op(A, B)
        if r.dtype == bool:
            r = r.astype(float)
        r = np.where(np.isnan(A) | np.isnan(B), np.nan, r).astype(float)
        return Curve(list(base.xs), [float(y) for y in r], xkind=base.xkind)
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        with np.errstate(all="ignore"):
            A = a if isinstance(a, np.ndarray) else np.full(len(b), _num(a))
            B = b if isinstance(b, np.ndarray) else np.full(len(a), _num(b))
            n = min(len(A), len(B))
            r = op(A[:n], B[:n])
            if r.dtype == bool:
                r = r.astype(float)
            bad = np.isnan(A[:n]) | np.isnan(B[:n])
            if op is operator.truediv:
                bad |= B[:n] == 0            # x/0 = na, as for single values (PROVISIONAL)
            r = np.where(bad, np.nan, r)
            return r.astype(float)
    if isinstance(a, WS) or isinstance(b, WS):
        keys = a.keys() if isinstance(a, WS) else b.keys()
        if isinstance(a, WS) and isinstance(b, WS):
            keys = a.keys() & b.keys()
        out = WS()
        for k in keys:
            av = a[k] if isinstance(a, WS) else _at_workout(a, k)
            bv = b[k] if isinstance(b, WS) else _at_workout(b, k)
            out[k] = _scalar_op(op, av, bv)
        return out
    if isinstance(a, Daily) or isinstance(b, Daily):
        if isinstance(a, Daily) and isinstance(b, Daily):
            s = max(a.start, b.start)
            e = min(a.start + len(a.values), b.start + len(b.values))
            A = a.values[s - a.start:e - a.start]
            B = b.values[s - b.start:e - b.start]
        elif isinstance(a, Daily):
            s, A, B = a.start, a.values, np.full(len(a.values), _num(b))
        else:
            s, A, B = b.start, np.full(len(b.values), _num(a)), b.values
        with np.errstate(all="ignore"):
            r = op(A, B)
        if r.dtype == bool:
            r = r.astype(float)
        r = np.where(np.isnan(A) | np.isnan(B), np.nan, r)
        return Daily(s, r.astype(float))
    return _scalar_op(op, a, b)


_CUR_CTX: list[Ctx] = []


def _at_workout(v, idx):
    """Value of a non-WS operand at a workout: Daily -> that day's value."""
    if isinstance(v, Daily):
        return v.at(_CUR_CTX[-1].ds.workouts[idx].day)
    return v


def lift1(fn: Callable[[float], float], a):
    a = _unwrap(a)
    if isinstance(a, Curve):
        with np.errstate(all="ignore"):
            ys = np.asarray(fn(np.array([_num(y) for y in a.ys], dtype=float)), dtype=float)
        return Curve(list(a.xs), [float(y) for y in ys], xkind=a.xkind)
    if isinstance(a, np.ndarray):
        with np.errstate(all="ignore"):
            return fn(a).astype(float) if callable(fn) else a
    if isinstance(a, WS):
        return WS({k: _safe1(fn, v) for k, v in a.items()})
    if isinstance(a, Daily):
        with np.errstate(all="ignore"):
            return Daily(a.start, np.asarray(fn(a.values), dtype=float))
    if isinstance(a, ListV):
        return ListV([lift1(fn, x) for x in a.items])
    return _safe1(fn, a)


def _safe1(fn, v):
    x = _num(v)
    if math.isnan(x):
        return math.nan
    try:
        return float(fn(np.float64(x)))
    except (ValueError, OverflowError, ZeroDivisionError):
        return math.nan


def _json_num(v):
    """NaN has no JSON form; store it as None so a cached miss round-trips."""
    x = _num(v)
    return None if math.isnan(x) else x


def _na_map(v, f: Callable[[float], float]):
    """Elementwise map that also sees na (NaN) values, e.g. logical not / isvalid."""
    v = _unwrap(v)
    if isinstance(v, Curve):
        return Curve(list(v.xs), [f(_num(y)) for y in v.ys], xkind=v.xkind)
    if isinstance(v, PairV) and v.y is not None:
        return PairV(v.x, _na_map(v.y, f))
    if isinstance(v, np.ndarray):
        return np.array([f(float(x)) for x in v], dtype=float)
    if isinstance(v, WS):
        return WS({k: f(_num(x)) for k, x in v.items()})
    if isinstance(v, Daily):
        return Daily(v.start, np.array([f(float(x)) for x in v.values], dtype=float))
    if isinstance(v, ListV):
        return ListV([_na_map(x, f) for x in v.items])
    return f(_num(v))


def truthy(v) -> bool:
    x = _num(v)
    return not math.isnan(x) and x != 0


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------

BINOPS = {
    "+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv,
    "^": operator.pow, "=": operator.eq, "<>": operator.ne, "<": operator.lt,
    ">": operator.gt, "<=": operator.le, ">=": operator.ge,
}


class Evaluator:
    def __init__(self, ds: Dataset, begin: float, end: float,
                 sports: Optional[set[str]] = None):
        """`sports`: RHE sport filter (sport groups, lower-case); None = all."""
        self.ds = ds
        self.begin, self.end = int(math.floor(begin)), int(math.floor(end))
        self.unsupported: set[str] = set()
        self.full_span = (min(ds.first_day, self.begin),
                          max(ds.last_day, int(ds.today), self.end) + 1)
        self.wlist = [w for w in ds.workouts if sports is None or w.sport in sports]

    # entry point
    def evaluate(self, expr: str, workout: Optional[Workout] = None):
        """Evaluate at the athlete level, or at the workout level if `workout`."""
        node = P.parse(expr)
        ctx = Ctx(self.ds, self.begin, self.end, None, {}, workout)
        _CUR_CTX.append(ctx)
        try:
            return self.ev(node, ctx)
        finally:
            _CUR_CTX.pop()

    # dispatcher
    def ev(self, n: P.Node, ctx: Ctx):
        m = getattr(self, "ev_" + type(n).__name__)
        return m(n, ctx)

    def ev_Empty(self, n, ctx):
        return None

    def ev_Num(self, n, ctx):
        return n.value

    def ev_Str(self, n, ctx):
        return n.value

    def ev_Unit(self, n, ctx):
        return self.ev(n.value, ctx)

    def ev_Seq(self, n, ctx):
        r = None
        for it in n.items:
            r = self.ev(it, ctx)
        return r

    def ev_Assign(self, n, ctx):
        v = self.ev(n.value, ctx)
        ctx.vars[n.name] = v
        return v

    def ev_Var(self, n, ctx):
        if n.name in ctx.vars:
            return ctx.vars[n.name]
        # "@name" that isn't a user variable is a developer channel (@form_power ...)
        if ctx.workout is not None and n.name in self._workout_channel_names(ctx.workout):
            return self._channel(n.name, ctx.workout)
        if ctx.workout is not None:
            return self._na_samples(ctx.workout)
        raise EvalError(f"undefined variable {n.name}")

    def _na_samples(self, w: Workout) -> np.ndarray:
        t = self.ds.channel(w.idx, "elapsedtime")
        return np.full(0 if t is None else len(t), np.nan)

    def ev_Pair(self, n, ctx):
        x = None if n.x is None else self.ev(n.x, ctx)
        y = None if n.y is None else self.ev(n.y, ctx)
        if x is None:
            # (,set) drops the x of every value; one value left is a plain number
            if isinstance(y, (WS, Curve, ListV)):
                vals = (list(y.values()) if isinstance(y, WS) else
                        list(y.ys) if isinstance(y, Curve) else
                        [i.y if isinstance(i, PairV) else i for i in y.items])
                if len(vals) == 1:
                    return PairV(None, vals[0])
                return ListV(vals)
            return PairV(None, y)
        if y is not None and (isinstance(x, (ListV, Curve)) or isinstance(y, (ListV, Curve))):
            return _pair_set(x, y)
        return PairV(x, y)

    def ev_ListLit(self, n, ctx):
        items = []
        for i in n.items:
            v = self.ev(i, ctx)
            if isinstance(v, RangeV) and v.step is not None:
                items.extend(_expand_range(v))      # {5:0:-1}, {0:max(power):10}
            else:
                items.append(v)
        return ListV(items)

    def ev_RangeLit(self, n, ctx):
        return RangeV(self.ev(n.lo, ctx), self.ev(n.hi, ctx),
                      None if n.step is None else self.ev(n.step, ctx))

    def ev_Unary(self, n, ctx):
        v = self.ev(n.operand, ctx)
        if n.op == "-":
            return lift1(lambda x: -x, v)
        if n.op == "+":
            return v
        return _na_map(v, lambda x: 1.0 if (math.isnan(x) or x == 0) else 0.0)  # logical not

    def ev_BinOp(self, n, ctx):
        if n.op in ("and", "or"):
            a, b = self.ev(n.left, ctx), self.ev(n.right, ctx)
            if n.op == "and":
                f = lambda x, y: (x != 0) & (y != 0) if isinstance(x, np.ndarray) else (x != 0 and y != 0)
            else:
                f = lambda x, y: (x != 0) | (y != 0) if isinstance(x, np.ndarray) else (x != 0 or y != 0)
            return lift2(f, a, b)
        if n.op == "in":
            # PROVISIONAL (not in the Expression Reference): elementwise
            # membership of the left values in the set of right values.
            pool = _values(self.ev(n.right, ctx))
            pool = pool[~np.isnan(pool)]
            def member(x):
                if math.isnan(x) or not len(pool):
                    return 0.0
                return 1.0 if np.any(np.abs(pool - x) <= 1e-9 * max(1.0, abs(x))) else 0.0
            return _na_map(self.ev(n.left, ctx), member)
        return lift2(BINOPS[n.op], self.ev(n.left, ctx), self.ev(n.right, ctx))

    # ---- identifiers ----------------------------------------------------
    def ev_Ident(self, n, ctx):
        name = n.name
        ds = self.ds
        if name == "today":
            return float(math.floor(ds.today))
        if name == "now":
            return ds.today
        if name == "begindate":
            return float(ctx.begin)
        if name == "enddate":
            return float(ctx.end)
        if name == "ctlconstant":
            return ds.athlete.ctlconstant
        if name == "atlconstant":
            return ds.athlete.atlconstant
        if name == "rampconstant":
            return 7.0  # PROVISIONAL default
        if name in CONSTANTS:
            return CONSTANTS[name]
        if name in ("begintime", "endtime"):
            # workout level: the selected range (workoutrange window) in elapsed s
            if ctx.workout is None:
                return WS({w.idx: self._range_time(name, ctx.child(workout=w))
                           for w in self._workouts_in(ctx)})
            return self._range_time(name, ctx)
        if name in TEXT_FIELDS:
            return self._ws_metric(name, ctx)
        if name in BUILTIN_EXPRS:
            return self._builtin(name, ctx)
        if name in ("ctl", "atl", "tsb"):
            # inside a workout chart these are the athlete's values on that day,
            # not a load built from the single workout
            base = ctx.child(workout=None) if ctx.workout is not None else ctx
            ctl = self._tl(self._ws_metric("tss", base), ds.athlete.ctlconstant)
            atl = self._tl(self._ws_metric("tss", base), ds.athlete.atlconstant)
            if name == "ctl":
                r = ctl
            elif name == "atl":
                r = atl
            else:
                r = self._shift(lift2(operator.sub, ctl, atl), 1)
            return r.at(ctx.workout.day) if ctx.workout is not None else r
        if name == "cp":
            # running critical power: the plan's dated CP test, else the FTP
            # WKO5 stored with the workout (index 3010), else the run setting
            if ctx.workout is not None:
                return ds.cp(ctx.workout)
            return WS({w.idx: ds.cp(w) for w in self.wlist})
        if name in ("aethr", "lthr"):
            # per-workout HR thresholds for the workout's own sport (not WKO5
            # identifiers): lthr = WKO5 <sport>thr unless the plan has a test
            get = ds.aethr if name == "aethr" else (lambda w: ds.sport_setting("thr", w))
            if ctx.workout is not None:
                return get(ctx.workout)
            return WS({w.idx: get(w) for w in self.wlist})
        if name in GOAL_IDENTS:
            # combined target of the upcoming A/B events (season plan), NaN if unset
            from backend.engine.planning import goals
            from backend.files.wko5_athlete import day_to_date
            g = goals(ds.plan, day_to_date(ds.today))["targets"].get(GOAL_IDENTS[name])
            return math.nan if g is None else float(g["value"])
        if name in CHANNELS or name == "rgrade":
            if ctx.workout is None:
                raise EvalError(f"channel {name} used outside a workout aggregation")
            if name in CHANNEL_EXPRS:
                return self._windowed(self._channel_expr(name, ctx), ctx)
            return self._windowed(self._channel(name, ctx.workout), ctx)
        if name in WORKOUT_METRICS:
            return self._ws_metric(name, ctx)
        name = SETTING_ALIASES.get(name, name)
        # dated settings: runftp, runthr, weight, ...
        if ds.athlete.settings.get(name) is not None or name.endswith(("ftp", "thr", "mhr", "tpace")):
            k = SETTING_UNIT_SCALE.get(name)
            get = ds.setting if k is None else (
                lambda nm, d: None if (v := ds.setting(nm, d)) is None else v * k)
            if ctx.workout is not None:
                return get(name, ctx.workout.day)
            return WS({w.idx: get(name, w.day) for w in self.wlist})
        # any other channel recorded in this workout's file
        if ctx.workout is not None and name in self._workout_channel_names(ctx.workout):
            return self._windowed(self._channel(name, ctx.workout), ctx)
        self.unsupported.add(name)
        raise EvalError(f"unsupported identifier {name}")

    def _ws_metric(self, name, ctx):
        if ctx.workout is not None:
            return self._workout_value(name, ctx.workout)
        return WS({w.idx: self._workout_value(name, w) for w in self.wlist
                   if ctx.sportf is None or w.sport == ctx.sportf})

    def _workout_value(self, name, w: Workout):
        if name == "sport":
            return w.sport
        if name == "date":
            return w.day
        if name in TEXT_FIELDS:
            return self._text(name, w)
        v = w.metrics.get(name)
        return math.nan if v is None else v

    def _text(self, name, w: Workout) -> str:
        """Workout title / description / notes / code ("" when absent), from the
        athlete index record (TEXT_FIELDS, DISASSEMBLY). WKO5's title (3213) is
        the workout type ("Trail Running") unless the athlete renamed it, which
        is why the charts can select trail runs with has(title,"Trail"). The
        workout-level getter 0x553760 falls back from the own title (.wko4 4020)
        to 4006 and then 4005 (the sport group); that fallback is kept for index
        entries without a 3213."""
        rec = w.entry.record
        if rec is not None:
            v = text_field(rec.get(TEXT_FIELDS[name]))
        else:
            v = getattr(w.entry, "description" if name == "desc" else name, None)
            v = v if isinstance(v, str) else ""
        if name == "title" and not v.strip():
            v = getattr(w.entry, "sport", None) or w.sport_type or ""
        return v

    def _range_time(self, name, ctx) -> float:
        if ctx.window is not None:
            return float(ctx.window[0] if name == "begintime" else ctx.window[1])
        if name == "begintime":
            return 0.0
        t = self.ds.channel(ctx.workout.idx, "elapsedtime")
        if t is not None and len(t) and np.any(~np.isnan(t)):
            return float(np.nanmax(t))
        d = ctx.workout.metrics.get("duration")
        return math.nan if d is None else float(d)

    def _builtin(self, name, ctx):
        """A built-in variable WKO5 defines as an expression (formulas.md §6.10,
        §6.12), evaluated in a scope of its own. TIS is per workout."""
        cache = self.ds.memo.setdefault(("builtin-ast",), {})
        node = cache.get(name)
        if node is None:
            node = cache[name] = P.parse(BUILTIN_EXPRS[name])
        if name not in PER_WORKOUT_BUILTINS:
            return self.ev(node, ctx.child(vars={}))
        if ctx.workout is not None:
            return self._builtin_workout(name, node, ctx.child(window=None), ctx.workout)
        out = WS()
        for w in self._workouts_in(ctx):
            v = self._builtin_workout(name, node, ctx.child(workout=w, window=None, rng=None,
                                                             sportf=None), w)
            if not _is_na(v):
                out[w.idx] = v
        return out

    def _builtin_workout(self, name, node, ctx, w: Workout):
        memo = self.ds.memo.setdefault(("builtin", name), {})
        if w.idx not in memo:
            if not self._has_channel(w, "power"):
                memo[w.idx] = math.nan            # count(ewma(power)) = 0 -> na
            else:
                try:
                    memo[w.idx] = _num(self.ev(node, ctx.child(vars={})))
                except EvalError:
                    memo[w.idx] = math.nan
        return memo[w.idx]

    def _has_channel(self, w: Workout, name: str) -> bool:
        """Channel present? Uses the athlete index channel list (3062) when
        there is one, so no .wko4 has to be parsed to find out."""
        rec = w.entry.record
        lst = rec.get(3062) if rec is not None else None
        if lst is not None and hasattr(lst, "all"):
            return name in {kv.get(101) for kv in lst.all(108)}
        return name in self._workout_channel_names(w)

    def _workout_channel_names(self, w: Workout) -> set[str]:
        f = self.ds.wko4(w.idx)
        return set(f.channels) if f is not None else set()

    def _windowed(self, arr: np.ndarray, ctx) -> np.ndarray:
        """Inside workoutrange(): samples outside [lo, hi] elapsed seconds
        become NA, so every reducer sees only the window."""
        if ctx.window is None or ctx.workout is None:
            return arr
        t = self.ds.channel(ctx.workout.idx, "elapsedtime")
        if t is None or len(t) != len(arr):
            return arr
        lo, hi = ctx.window
        out = arr.astype(float, copy=True)
        out[(t < lo) | (t > hi)] = np.nan
        return out

    def _channel(self, name, w: Workout) -> np.ndarray:
        ds = self.ds
        n_samples = None
        t = ds.channel(w.idx, "elapsedtime")
        n_samples = 0 if t is None else len(t)
        base = RUN_ONLY.get(name) or BIKE_ONLY.get(name)
        if base:
            want = "run" if name in RUN_ONLY else "bike"
            if w.sport != want:
                return np.full(n_samples, np.nan)
            name = base
        if name == "_elevation":
            return self._smoothed_elevation(w, n_samples)
        if name == "rgrade":
            return self._rgrade(w, n_samples)
        if name in ("_rapower", "_rapower4"):
            # NOTE: on WKO5's own 1 s grid (not the sample grid) — only valid
            # inside grid-agnostic reducers such as meanmax()/avg of itself.
            f = ds.wko4(w.idx)
            pc, tc = (f.channels.get("power"), f.channels.get("elapsedtime")) if f else (None, None)
            if pc is None or tc is None:
                return np.full(n_samples, np.nan)
            ra = np.array([a for _, a in rapower(tc.values, pc.values)], dtype=float)
            return ra ** 4 if name == "_rapower4" else ra
        if name == "rngp":
            if w.sport != "run":
                return np.full(n_samples, np.nan)      # WKO5 defines rngp for Run only
            return self._rngp(w, t, n_samples)
        arr = ds.channel(w.idx, name)
        if arr is None:
            return np.full(n_samples, np.nan)
        k = EXPR_UNIT_SCALE.get(name)
        return arr if k is None else arr * k

    def _rgrade(self, w: Workout, n_samples: int) -> np.ndarray:
        """WKO5's rgrade channel (RGRADE_EXPR, 0x724cfd), a fraction per sample:
            dElev = _elevation[i] − _elevation[i−1]                 (m)
            dRun  = (elapseddistance[i] − elapseddistance[i−1])·1000  (km → m)
            g     = dElev / sqrt(dRun² − dElev²)       (rise over horizontal run)
            rgrade = filter(g, gaussian(3,17), 2)      (centred, ±8 samples)
        g is na for the first sample (shift lag), where either channel is na,
        where dRun < |dElev| (sqrt of a negative: no movement, e.g. standing
        with the barometer drifting) and — the one guard WKO5's string lacks,
        PROVISIONAL — where the horizontal run is below 1 cm: distance is stored
        in 1 cm steps, so a smaller run is the float residue of dRun = |dElev|
        (0.5 m vs 0.500000000000167 m gave g = 1.2e6 on a trail run). filter
        renormalises over the valid samples, so gaps are bridged by neighbours.
        A file's own `rgrade` channel wins."""
        memo = self.ds.memo.setdefault(("rgrade",), {})
        if w.idx in memo:
            return memo[w.idx]
        own = self.ds.channel(w.idx, "rgrade")
        if own is not None:
            memo[w.idx] = own
            return own
        e = self._smoothed_elevation(w, n_samples)
        d = self.ds.channel(w.idx, "elapseddistance")
        out = np.full(n_samples, np.nan)
        if d is not None and len(d) == len(e) == n_samples and n_samples:
            de = np.full(n_samples, np.nan)
            dr = np.full(n_samples, np.nan)
            de[1:] = e[1:] - e[:-1]
            dr[1:] = (d[1:] - d[:-1]) * 1000.0
            with np.errstate(all="ignore"):
                run = np.sqrt(dr ** 2 - de ** 2)
                g = np.where(run >= RGRADE_MIN_RUN_M, de / run, np.nan)
            kernel = _values(self.ev(P.parse("gaussian(3,17)"),
                                     Ctx(self.ds, self.begin, self.end, None, {}, w)))
            out = _kernel_filter(g, kernel, 2)
        memo[w.idx] = out
        return out

    def _smoothed_elevation(self, w: Workout, n_samples: int) -> np.ndarray:
        """`_elevation`: the file's own channel (WKO5 writes it on import), else
        WKO5's smoothing of `elevation` recomputed (algorithms/wko5_elevation,
        VERIFIED bit-exact) — files this app imported from FIT have no
        `_elevation`."""
        arr = self.ds.channel(w.idx, "_elevation")
        if arr is not None:
            return arr
        memo = self.ds.memo.setdefault(("_elevation",), {})
        if w.idx not in memo:
            from backend.engine.algorithms.wko5_elevation import smooth_elevation
            e = self.ds.channel(w.idx, "elevation")
            t = self.ds.channel(w.idx, "elapsedtime")
            out = np.full(n_samples, np.nan)
            if e is not None and t is not None and len(e) == len(t) == n_samples:
                tl = [None if np.isnan(v) else float(v) for v in t]
                el = [None if np.isnan(v) else float(v) for v in e]
                out = np.array([np.nan if v is None else v for v in smooth_elevation(tl, el)],
                               dtype=float)
            memo[w.idx] = out
        return memo[w.idx]

    def _channel_expr(self, name: str, ctx) -> np.ndarray:
        """A derived channel WKO5 defines as an expression string (CHANNEL_EXPRS),
        evaluated once per workout over the whole workout (the caller windows
        it). A channel of the same name recorded in the file wins. Outside the
        channel's sport groups it does not exist (all na). Non-finite samples
        (cadence or stancetime 0) become na — PROVISIONAL, WKO5's x/0 not decoded."""
        w = ctx.workout
        memo = self.ds.memo.setdefault(("chexpr", name), {})
        if w.idx in memo:
            return memo[w.idx]
        t = self.ds.channel(w.idx, "elapsedtime")
        n = 0 if t is None else len(t)
        out = np.full(n, np.nan)
        sports, expr = CHANNEL_EXPRS[name]
        if name in self._workout_channel_names(w):
            arr = self.ds.channel(w.idx, name)
            out = out if arr is None else arr
        elif sports is None or SPORT_SETTING_PREFIX.get(w.sport, w.sport) in sports:
            cache = self.ds.memo.setdefault(("chexpr-ast",), {})
            node = cache.get(name)
            if node is None:
                node = cache[name] = P.parse(expr)
            try:
                v = self.ev(node, ctx.child(window=None, vars={}))
            except EvalError:
                v = None
            if isinstance(v, np.ndarray) and len(v) == n:
                out = np.where(np.isfinite(v), v, np.nan)
        memo[w.idx] = out
        return out

    def _rngp(self, w: Workout, t, n_samples: int) -> np.ndarray:
        """Rolling normalized graded pace (min/km) per sample: WKO5's "1000/_ragpace"
        (0x724e46) with `_ragpace` (VERIFIED channel, algorithms/wko5_pace.py)
        taken from its 1 s grid at each sample's time. VERIFIED against Cache5
        (formulas: see functions.md §7b): WKO5 hands it to expressions in the
        athlete's pace unit (min/mi there, x1.609344); this evaluator keeps min/km."""
        from backend.engine.algorithms.wko5_pace import ragpace
        memo = self.ds.memo.setdefault(("rngp",), {})
        if w.idx not in memo:
            f = self.ds.wko4(w.idx)
            ch = f.channels if f is not None else {}
            tc, ec, sc = ch.get("elapsedtime"), ch.get("_elevation"), ch.get("speed")
            out = np.full(n_samples, np.nan)
            if tc is not None and ec is not None and sc is not None and t is not None:
                gt, gv = ragpace(tc.values, ec.values, sc.values)
                if gt:
                    gt_a = np.array(gt, dtype=float)
                    gv_a = np.array([np.nan if v is None or v == 0 else 1000.0 / v for v in gv])
                    idx = np.searchsorted(gt_a, t, side="right") - 1
                    ok = (idx >= 0) & ~np.isnan(t)
                    out[ok] = gv_a[idx[ok]]
            memo[w.idx] = out
        return memo[w.idx]

    # ---- calls ----------------------------------------------------------
    def ev_Call(self, n, ctx):
        fn = getattr(self, "fn_" + n.name, None)
        if fn is None:
            self.unsupported.add(n.name + "()")
            raise EvalError(f"unsupported function {n.name}()")
        return fn(n, ctx)

    def arg(self, n, i, ctx):
        return self.ev(n.args[i], ctx)

    # conditionals
    def fn_if(self, n, ctx):
        cond = self.arg(n, 0, ctx)
        a = self.arg(n, 1, ctx)
        b = self.arg(n, 2, ctx) if len(n.args) > 2 else None
        return _where(cond, a, b)

    def fn_isvalid(self, n, ctx):
        return _na_map(self.arg(n, 0, ctx), lambda x: 0.0 if math.isnan(x) else 1.0)

    def fn_hastag(self, n, ctx):
        tag = str(self.arg(n, 0, ctx)).lower()
        if ctx.workout is not None:
            return 1.0 if tag in ctx.workout.tags else 0.0
        return WS({w.idx: (1.0 if tag in w.tags else 0.0) for w in self.wlist})

    def fn_has(self, n, ctx):
        s, sub = self.arg(n, 0, ctx), str(self.arg(n, 1, ctx)).lower()
        if isinstance(s, WS):
            return WS({k: (1.0 if sub in str(v).lower() else 0.0) for k, v in s.items()})
        return 1.0 if sub in str(s).lower() else 0.0

    # aggregations
    def _grouped(self, n, ctx, kind, values=None, keys=None):
        """sum(numbers, groupby) — aggregate within each group instead of over
        everything. The usual use is a date bucket, e.g.
        `sum(climbing, startofweek(date))` for a weekly total, or the period
        name form `sum(tss, "week")` (Reference). Week / month / year values
        (weekval / monthval / yearval keys) are plotted at the period's first day."""
        if values is None:
            vnode = n.args[0]
            if ctx.workout is None and needs_samples(vnode):
                # sum(samples, "week"): reduce each workout first, then its period
                # (exact for sum/max/min/count; avg = mean of workout means, PROVISIONAL)
                values = self._aggregate(P.Call(kind, [vnode]), ctx, kind)
                kind = "sum" if kind == "count" else kind
            else:
                values = self.ev(vnode, ctx)
            keys = self.ev(n.args[1], ctx)
        values, keys = _unwrap(values), _unwrap(keys)
        if isinstance(keys, str):
            period = PERIOD_START.get(keys.strip().lower())
            if period is None or not isinstance(values, WS):
                raise EvalError(f'groupby period must be "day", "week", "month" or "year", got {keys!r}')
            keys = WS({k: period(self.ds.workouts[k].day) for k in values})
        if isinstance(values, WS) and isinstance(keys, WS):
            keymap = _period_keymap(n.args[1])
            buckets: dict = {}
            for idx, v in values.items():
                k = keys.get(idx)
                if _is_na(k) or _is_na(v):
                    continue
                if keymap is not None and not isinstance(k, str):
                    k = keymap(_num(k))
                buckets.setdefault(k, []).append(_num(v))
            out = {k: _group_agg(xs, kind) for k, xs in buckets.items()}
            if not out:
                return math.nan
            if all(isinstance(k, (int, float)) and not math.isnan(k) for k in out):
                start = int(math.floor(min(out)))
                end = int(math.floor(max(out)))
                arr = np.full(end - start + 1, np.nan)
                for k, v in out.items():
                    arr[int(math.floor(k)) - start] = v
                return Daily(start, arr)
            return ListV([PairV(k, v) for k, v in sorted(out.items(), key=lambda kv: str(kv[0]))])
        if _is_set(values) and _is_set(keys):
            # sets without dates (samples, lists): one (key, aggregate) point per key.
            # PROVISIONAL: sample groups are plain (not time-weighted) means.
            V, K = _values(values), _values(keys)
            m = min(len(V), len(K))
            buckets = {}
            for k, v in zip(K[:m], V[:m]):
                if not (math.isnan(k) or math.isnan(v)):
                    buckets.setdefault(float(k), []).append(float(v))
            xs = sorted(buckets)
            return Curve(xs, [_group_agg(buckets[x], kind) for x in xs], xkind="value")
        return self._aggregate(n, ctx, kind)     # not a grouping: plain reduce

    def _aggregate(self, n, ctx, kind):
        node = n.args[0]
        if ctx.workout is None and needs_samples(node):
            # A per-workout result depends only on (sub-expression, workout), not
            # on the chart's date range — so it is memoised in the Dataset and on
            # disk. The disk half matters because reducing samples means parsing
            # the .wko4, and a year-long range touches hundreds of them; without
            # it every fresh Dataset (a parity toggle makes one) pays that again.
            # Expressions using @vars are position-dependent, so they are skipped.
            cacheable = not any(isinstance(x, P.Var) for x in P.walk(node))
            key = (kind, repr(node)) if cacheable else None
            memo = self.ds.memo.setdefault(key, {}) if key else {}
            cached_series = getattr(self.ds, "cached_series", None) if cacheable else None
            disk_key = f"agg:{kind}:{repr(node)}"
            out = WS()
            for w in self._workouts_in(ctx):
                if w.idx in memo:
                    r = memo[w.idx]
                else:
                    def compute(w=w):
                        return _reduce(self.ev(node, ctx.child(workout=w)), kind, self.ds, w)
                    if cached_series is not None:
                        hit = cached_series(disk_key, w, lambda w=w: _json_num(compute(w)))
                        r = math.nan if hit is None else hit
                    else:
                        r = compute()
                    memo[w.idx] = r
                if not _is_na(r):
                    out[w.idx] = r
            flush = getattr(self.ds, "flush_series", None)
            if flush is not None:
                flush()
            return out
        v = self.ev(node, ctx)
        if ctx.workout is not None:
            return _reduce(v, kind, self.ds, ctx.workout)
        if isinstance(v, WS):
            v = WS({k: x for k, x in v.items() if self._in_rng(self.ds.workouts[k].day, ctx)})
        elif isinstance(v, Daily):
            r = self._eff_rng(ctx)
            if r is not None:
                v = v.clip(*r)
        return _reduce(v, kind, self.ds, None)

    def fn_sum(self, n, ctx):
        if len(n.args) == 2:
            return self._grouped(n, ctx, "sum")
        return self._aggregate(n, ctx, "sum")

    def fn_avg(self, n, ctx):
        if len(n.args) == 2:
            return self._grouped(n, ctx, "avg")
        return self._aggregate(n, ctx, "avg")

    def _max_min(self, n, ctx, kind):
        if isinstance(n.args[1], P.Str):
            return self._grouped(n, ctx, kind)      # max(x, "week")
        a, b = self.arg(n, 0, ctx), self.arg(n, 1, ctx)
        ua, ub = _unwrap(a), _unwrap(b)
        # max(values, groupby) (Reference) for a period name or two dated /
        # listed sets; two sample series or a set and a number stay
        # elementwise (docs: max/2 @0x6f0c90). PROVISIONAL split.
        grouped = isinstance(ub, str) or (
            _is_set(ua) and _is_set(ub)
            and not (isinstance(ua, np.ndarray) and isinstance(ub, np.ndarray))
            and not (isinstance(ua, Daily) or isinstance(ub, Daily)))
        if grouped:
            return self._grouped(n, ctx, kind, a, b)
        f = np.fmax if kind == "max" else np.fmin
        g = max if kind == "max" else min
        return lift2(lambda x, y: f(x, y) if isinstance(x, np.ndarray) else g(x, y), a, b)

    def fn_max(self, n, ctx):
        if len(n.args) == 2:
            return self._max_min(n, ctx, "max")
        return self._aggregate(n, ctx, "max")

    def fn_min(self, n, ctx):
        if len(n.args) == 2:
            return self._max_min(n, ctx, "min")
        return self._aggregate(n, ctx, "min")

    def fn_count(self, n, ctx):
        """Valid, non-zero values (Reference + DISASSEMBLY 0x6d2910)."""
        if len(n.args) == 2:
            return self._grouped(n, ctx, "count")
        return self._aggregate(n, ctx, "count")

    def fn_length(self, n, ctx):
        """All values, zeros and na included (Reference)."""
        v = _unwrap(self.arg(n, 0, ctx))
        if isinstance(v, WS):
            v = WS({k: x for k, x in v.items() if self._in_rng(self.ds.workouts[k].day, ctx)})
        return float(len(_values(v))) if _is_set(v) else 1.0

    def fn_stddev(self, n, ctx):
        """Sample standard deviation, n-1 (Reference: stddev(1..10) = 3.0276504).
        PROVISIONAL on samples: unweighted, like the Reference wording."""
        return self._aggregate(n, ctx, "stddev")

    def fn_pstddev(self, n, ctx):
        return self._aggregate(n, ctx, "pstddev")

    def fn_variance(self, n, ctx):
        return self._aggregate(n, ctx, "variance")

    def fn_pvariance(self, n, ctx):
        return self._aggregate(n, ctx, "pvariance")

    # simple linear regression (functions.md §7, DISASSEMBLY)
    def fn_slrm(self, n, ctx):
        return self._aggregate(n, ctx, "slrm")

    def fn_slrb(self, n, ctx):
        return self._aggregate(n, ctx, "slrb")

    def fn_slrrsq(self, n, ctx):
        return self._aggregate(n, ctx, "slrrsq")

    def fn_slr(self, n, ctx):
        """The regression line from (xmin, m·xmin+b) to (xmax, m·xmax+b).
        Dated input gives a daily line over those days, workout samples a
        value per sample; other sets a two-point curve."""
        if ctx.workout is None and needs_samples(n.args[0]):
            raise EvalError("slr() of sample data needs a workout chart")
        v = _unwrap(self.arg(n, 0, ctx))
        if isinstance(v, WS):
            v = WS({k: x for k, x in v.items() if self._in_rng(self.ds.workouts[k].day, ctx)})
        elif isinstance(v, Daily) and self._eff_rng(ctx) is not None:
            v = v.clip(*self._eff_rng(ctx))
        x, y = _xy_of(v, self.ds, ctx.workout)
        fit = _slr_fit(x, y)
        if fit is None:
            return math.nan
        m, b, _ = fit
        ok = ~np.isnan(x) & ~np.isnan(y)
        lo, hi = float(np.min(x[ok])), float(np.max(x[ok]))
        if isinstance(v, (WS, Daily)):
            days = np.arange(int(math.floor(lo)), int(math.floor(hi)) + 1, dtype=float)
            return Daily(int(days[0]), m * days + b)
        if isinstance(v, np.ndarray):
            with np.errstate(all="ignore"):
                return m * x + b
        return Curve([lo, hi], [m * lo + b, m * hi + b],
                     xkind=v.xkind if isinstance(v, Curve) else "value")

    # set transforms (Reference "sorting and reshaping" / "selection")
    def fn_cumsum(self, n, ctx):
        """Running total of the valid values; na positions stay na (DOC)."""
        return _seq_map(self.arg(n, 0, ctx), _cumsum)

    def fn_delta(self, n, ctx):
        """Each value minus the previous one; the first is na (DOC)."""
        return _seq_map(self.arg(n, 0, ctx), _delta)

    def fn_rev(self, n, ctx):
        """Reverse order (DOC). Dated / timed sets keep each value's x, so
        reversing them changes nothing that is plotted."""
        v = _unwrap(self.arg(n, 0, ctx))
        if isinstance(v, ListV):
            return ListV(list(reversed(v.items)))
        if isinstance(v, WS):
            return WS(reversed(list(v.items())))
        if isinstance(v, Curve):
            return Curve(list(reversed(v.xs)), list(reversed(v.ys)), xkind=v.xkind)
        return v

    def _sort(self, n, ctx, by_x: bool, desc: bool):
        v = _unwrap(self.arg(n, 0, ctx))
        if by_x:
            c = _as_curve(v, self.ds, ctx.workout)
            if c is None:
                return v
            key = lambda i: (math.isnan(_num(c.xs[i])), _num(c.xs[i]) * (-1 if desc else 1))
            order = sorted(range(len(c.xs)), key=key)
            return Curve([c.xs[i] for i in order], [c.ys[i] for i in order], xkind=c.xkind)
        key = lambda kv: (math.isnan(_num(kv[1])), _num(kv[1]) * (-1 if desc else 1))
        if isinstance(v, WS):
            return WS(sorted(v.items(), key=key))
        if isinstance(v, ListV):
            if all(isinstance(i, str) for i in v.items):
                return ListV(sorted(v.items, reverse=desc))
            return ListV([i for _, i in sorted(enumerate(v.items), key=key)])
        if isinstance(v, np.ndarray):
            s = np.sort(v)                  # NaN last
            if desc:
                good = s[~np.isnan(s)][::-1]
                s = np.concatenate([good, s[np.isnan(s)]])
            return s
        return v

    def fn_sort(self, n, ctx):
        return self._sort(n, ctx, False, False)

    def fn_sortd(self, n, ctx):
        return self._sort(n, ctx, False, True)

    def fn_sortx(self, n, ctx):
        return self._sort(n, ctx, True, False)

    def fn_sortxd(self, n, ctx):
        return self._sort(n, ctx, True, True)

    def fn_noinvalid(self, n, ctx):
        """Drop na (DOC). Sample series keep their length (na = no point), so
        they stay aligned with elapsedtime."""
        v = _unwrap(self.arg(n, 0, ctx))
        if isinstance(v, ListV):
            return ListV([i for i in v.items if isinstance(i, (str, PairV, ListV, Curve))
                          or not _is_na(_num(i))])
        if isinstance(v, WS):
            return WS({k: x for k, x in v.items() if isinstance(x, str) or not _is_na(_num(x))})
        if isinstance(v, Curve):
            keep = [i for i, y in enumerate(v.ys) if not math.isnan(_num(y))]
            return Curve([v.xs[i] for i in keep], [v.ys[i] for i in keep], xkind=v.xkind)
        return v

    def fn_unique(self, n, ctx):
        v = _unwrap(self.arg(n, 0, ctx))
        items = (list(v.items) if isinstance(v, ListV) else list(v.values()) if isinstance(v, WS)
                 else list(_values(v)) if _is_set(v) else [v])
        out, seen = [], set()
        for i in items:
            k = i.lower() if isinstance(i, str) else _num(i)
            if isinstance(k, float) and math.isnan(k):
                continue
            if k not in seen:
                seen.add(k)
                out.append(i)
        return ListV(out)

    def _select(self, n, ctx, mode):
        """greatest / least / first / last (Reference): the `count` chosen
        values, each keeping its x (date / time / duration); lists keep their
        original order, e.g. greatest({5,7,3,2,4,9,8},3) = {7,9,8}."""
        v = _unwrap(self.arg(n, 0, ctx))
        k = _num(self.arg(n, 1, ctx))
        if math.isnan(k):
            return math.nan
        k = int(k)
        if k < 0:
            raise EvalError("Count of elements must be 0 or greater.")
        if isinstance(v, WS):
            v = WS({i: x for i, x in v.items() if self._in_rng(self.ds.workouts[i].day, ctx)})
            order = sorted(v, key=lambda i: self.ds.workouts[i].day)
            vals = [_num(v[i]) for i in order]
            keep = set(_pick(vals, k, mode))
            return WS({order[j]: v[order[j]] for j in range(len(order)) if j in keep})
        if isinstance(v, Daily):
            r = self._eff_rng(ctx)
            if r is not None:
                v = v.clip(*r)
            keep = _pick(list(v.values), k, mode)
            out = np.full(len(v.values), np.nan)
            out[keep] = v.values[keep]
            return Daily(v.start, out)
        if isinstance(v, np.ndarray):
            keep = _pick(list(v), k, mode)
            out = np.full(len(v), np.nan)
            out[keep] = v[keep]
            return out
        if isinstance(v, Curve):
            keep = _pick([_num(y) for y in v.ys], k, mode)
            return Curve([v.xs[j] for j in keep], [v.ys[j] for j in keep], xkind=v.xkind)
        if isinstance(v, ListV):
            vals = [_num(i.y if isinstance(i, PairV) else i) for i in v.items]
            if mode in ("first", "last"):
                vals = [0.0] * len(vals)          # position only; strings count too
            keep = _pick(vals, k, mode)
            return ListV([v.items[j] for j in keep])
        return v if k >= 1 else ListV([])

    def fn_greatest(self, n, ctx):
        return self._select(n, ctx, "greatest")

    def fn_least(self, n, ctx):
        return self._select(n, ctx, "least")

    def fn_first(self, n, ctx):
        return self._select(n, ctx, "first")

    def fn_last(self, n, ctx):
        return self._select(n, ctx, "last")

    def fn_string(self, n, ctx):
        """Numbers to strings, for concatenation with + (Reference).
        PROVISIONAL formatting: up to 10 significant digits, na -> ""."""
        def s(x):
            if isinstance(x, str):
                return x
            f = _num(x)
            if math.isnan(f):
                return ""
            return str(int(f)) if f == int(f) and abs(f) < 1e15 else f"{f:.10g}"
        v = _unwrap(self.arg(n, 0, ctx))
        if isinstance(v, ListV):
            return ListV([s(i) for i in v.items])
        if isinstance(v, WS):
            return WS({k: s(x) for k, x in v.items()})
        if isinstance(v, (np.ndarray, Daily, Curve)):
            return ListV([s(x) for x in _values(v)])
        return s(v)

    def fn_clamp(self, n, ctx):
        """clamp(values, min, max) — the argument order WKO5's own built-in
        TIS / stamina expressions use. The Reference documents
        clamp(min, max, values); that order is taken when the first order
        would give min > max."""
        a, b, c = (self.arg(n, i, ctx) for i in range(3))
        vals, lo, hi = a, _num(b), _num(c)
        if not _is_set(_unwrap(a)) and (_is_set(_unwrap(c)) or lo > hi):
            vals, lo, hi = c, _num(a), _num(b)
        return lift1(lambda x: np.clip(x, lo, hi), vals)

    def fn_sign(self, n, ctx):
        return lift1(np.sign, self.arg(n, 0, ctx))

    # smoothing (functions.md §5, DISASSEMBLY 0x6e7590 / 0x6e20a0 / 0x6dcf10)
    def _kernel_length(self, n, ctx) -> int:
        """The length argument of isef / gaussian: 1..1000, rounded half up
        (0x4a5a60), then made odd (+1 when even)."""
        v = _unwrap(self.arg(n, 1, ctx)) if len(n.args) > 1 else 1.0
        length = _num(v)
        if math.isnan(length):
            return 1
        if length < 1.0 or length > 1000.0:
            raise EvalError("Length must be between 1 and 1000.")
        m = int(math.floor(length + 0.5))
        return m if m % 2 else m + 1

    @staticmethod
    def _normalised(ws: list) -> ListV:
        s = 0.0
        for w in ws:
            s = w + s
        return ListV([w / s for w in ws])

    def fn_isef(self, n, ctx):
        """isef(factor, length) (0x6e7590): symmetric kernel of odd length
        N = length (+1 if even), w_j = exp(|j|·ln(1−factor)) = (1−factor)^|j|
        for j = −N//2 … N//2, normalised to sum 1. factor must be 0…<1; a
        factor ≤ 0.005 gives the identity kernel {1}."""
        f = _num(self.arg(n, 0, ctx))
        if math.isnan(f):
            f = 1.0
        elif f < 0.0 or f >= 1.0:
            raise EvalError("Smoothing factor must be greater than or equal to 0.005 "
                            "and less than 1.0.")
        elif f <= 0.005:
            return ListV([1.0])
        size = self._kernel_length(n, ctx)
        lnf = math.log(1.0 - f)
        h = size >> 1
        return self._normalised([math.exp(float(abs(i - h)) * lnf) for i in range(size)])

    def fn_gaussian(self, n, ctx):
        """gaussian(sigma, length) (0x6e20a0): symmetric kernel of odd length N,
        w_j = exp(−j²/(2σ²)) / (σ·√(2π)) for j = −N//2 … N//2, normalised to
        sum 1. sigma must be 0…100; sigma ≤ 1 gives the identity kernel {1}."""
        sigma = _num(self.arg(n, 0, ctx))
        if math.isnan(sigma):
            return ListV([1.0])
        if sigma < 0.0 or sigma > 100.0:
            raise EvalError("Sigma must be between 1 and 100.")
        if sigma <= 1.0:
            return ListV([1.0])
        size = self._kernel_length(n, ctx)
        c = 1.0 / (math.sqrt(6.283185307179586) * sigma)
        two_s2 = (sigma + sigma) * sigma
        h = size >> 1
        return self._normalised([math.exp(((float(j) * -1.0) * float(j)) / two_s2) * c
                                 for j in range(-h, size - h)])

    def fn_filter(self, n, ctx):
        """filter(numbers, kernel, sides) (0x6dcf10): out[i] =
        Σ k[m]·x[i−off+m] / Σ k[m] over the in-range valid samples, with
        off = K−1 (causal) or (K−1)//2 when sides = 2 (centred); na when no
        sample is valid. Works on the values in order (not time-weighted)."""
        x = _unwrap(self.arg(n, 0, ctx))
        k = _values(self.arg(n, 1, ctx))
        s = _unwrap(self.arg(n, 2, ctx))
        if _is_set(s) and len(_values(s)) != 1:
            raise EvalError("Expected 1 or 2 sides in third argument.")
        sides = _num(s if not _is_set(s) else _values(s)[0])
        return _seq_map(x, lambda a: _kernel_filter(a, k, 2 if sides == 2.0 else 1))

    def fn_dfrc(self, n, ctx):
        """dfrc(power, frc, ftp) (0x6d7ae0): FRC balance in kJ per sample.
        Above FTP the depletion D grows by (p−ftp)·dt, starting from what was
        left after the last recovery; at or below FTP the recovered part is
        R = D·(0.3·(1−e^(−t/25)) + 0.7·(1−e^(−t/300))), t = time since the
        effort ended. Output (FRC·1000 − D + R) / 1000. na power counts as 0 W;
        samples with dt < 0.001 s or no time give no point."""
        if ctx.workout is None:
            raise EvalError("Internal error: Missing power.")
        p = _unwrap(self.arg(n, 0, ctx))
        frc, ftp = _num(self.arg(n, 1, ctx)), _num(self.arg(n, 2, ctx))
        if not isinstance(p, np.ndarray):
            raise EvalError("Expecting power in watts.")
        if math.isnan(frc):
            raise EvalError("Invalid FRC.")
        if math.isnan(ftp):
            raise EvalError("Invalid FTP.")
        frc_j = frc * 1000.0
        if not 0.0 < frc_j <= 50000.0:
            raise EvalError("Invalid FRC value.")
        if not 10.0 <= ftp <= 600.0:
            raise EvalError("Invalid FTP value.")
        dt = self.ds.channel(ctx.workout.idx, "deltatime")
        if dt is None or len(dt) != len(p):
            dt = np.ones(len(p))
        return _dfrc(p.astype(float), dt, frc_j, ftp)

    # math
    def fn_trunc(self, n, ctx):
        return lift1(np.trunc, self.arg(n, 0, ctx))

    def fn_round(self, n, ctx):
        """round(x[, places]) (0x6fe4f0 / 0x6feaa0, helper 0x4a5bb0): places
        counts powers of ten LEFT of the point (round(pi,-1) = 3.1,
        round(1234.567,2) = 1200). With m = 10^−places from WKO5's table,
        x > 0 → floor(x·m + 0.5)/m, else ceil(x·m − 0.5)/m (halves away from
        zero); a result within DBL_MIN of 0 is +0. places is itself rounded
        half away from zero (0x4a59f0) and must be −7…7."""
        v = self.arg(n, 0, ctx)
        places = 0
        if len(n.args) > 1:
            pv = _num(self.arg(n, 1, ctx))
            if not math.isnan(pv):
                places = int(math.floor(pv + 0.5) if pv > 0 else math.ceil(pv - 0.5))
        if not -7 <= places <= 7:
            raise EvalError("Places must be between -7 and 7.")
        m = _ROUND_SCALE[places + 7]
        return lift1(lambda x: _wko_round(x, m), v)

    def fn_floor(self, n, ctx):
        return lift1(np.floor, self.arg(n, 0, ctx))

    def fn_frac(self, n, ctx):
        return lift1(lambda x: x - np.trunc(x), self.arg(n, 0, ctx))

    def fn_ln(self, n, ctx):
        return lift1(np.log, self.arg(n, 0, ctx))

    def fn_log(self, n, ctx):
        base = _num(self.arg(n, 1, ctx)) if len(n.args) > 1 else 10.0
        return lift1(lambda x: np.log(x) / np.log(base), self.arg(n, 0, ctx))

    def fn_ceil(self, n, ctx):
        return lift1(np.ceil, self.arg(n, 0, ctx))

    def fn_sqrt(self, n, ctx):
        return lift1(np.sqrt, self.arg(n, 0, ctx))

    def fn_log10(self, n, ctx):
        return lift1(np.log10, self.arg(n, 0, ctx))

    def fn_abs(self, n, ctx):
        return lift1(np.abs, self.arg(n, 0, ctx))

    # dates (day numbers since 1901-01-01; weeks start on Monday — see §6)
    def _date_fn(self, n, ctx, f):
        g = lambda d: math.nan if math.isnan(d) else f(d)
        return lift1(lambda x: np.vectorize(g, otypes=[float])(x) if isinstance(x, np.ndarray)
                     else g(float(x)), self.arg(n, 0, ctx))

    def fn_startofweek(self, n, ctx):
        return self._date_fn(n, ctx, _start_of_week)

    def fn_startofmonth(self, n, ctx):
        """First day of the month (DOC)."""
        return self._date_fn(n, ctx, _start_of_month)

    def fn_startofyear(self, n, ctx):
        """January 1st of the year (DOC)."""
        return self._date_fn(n, ctx, _start_of_year)

    def fn_startofquarter(self, n, ctx):
        """First day of the calendar quarter: Jan / Apr / Jul / Oct 1. Not a
        WKO5 function — the chart page's 季 period bucket."""
        return self._date_fn(n, ctx, _start_of_quarter)

    def fn_weekval(self, n, ctx):
        """Fractional weeks: weekval(2015-11-08) = 5993.857, trunc -> the
        Monday 2015-11-02 (DOC, Reference example reproduced exactly)."""
        return self._date_fn(n, ctx, lambda d: (d + 8.0) / 7.0)

    def fn_monthval(self, n, ctx):
        """Fractional months since January 1901: monthval(2015-11-03) =
        1378.0666667 (DOC, Reference example reproduced exactly)."""
        def mv(d):
            x = day_to_date(d)
            ndays = (_first_of_next_month(x) - x.replace(day=1)).days
            return (x.year - 1901) * 12 + (x.month - 1) + (d - _start_of_month(d)) / ndays
        return self._date_fn(n, ctx, mv)

    def fn_yearval(self, n, ctx):
        """Fractional years since 1901: yearval(2015-11-08) = 114.852 (DOC)."""
        def yv(d):
            x = day_to_date(d)
            ndays = (dt.date(x.year + 1, 1, 1) - dt.date(x.year, 1, 1)).days
            return (x.year - 1901) + (d - _start_of_year(d)) / ndays
        return self._date_fn(n, ctx, yv)

    def fn_week(self, n, ctx):
        """ISO-8601 week number (DOC)."""
        return self._date_fn(n, ctx, lambda d: float(day_to_date(d).isocalendar()[1]))

    def fn_month(self, n, ctx):
        return self._date_fn(n, ctx, lambda d: float(day_to_date(d).month))

    def fn_year(self, n, ctx):
        return self._date_fn(n, ctx, lambda d: float(day_to_date(d).year))

    def fn_day(self, n, ctx):
        return self._date_fn(n, ctx, lambda d: float(day_to_date(d).day))

    def fn_dayofweek(self, n, ctx):
        """Days after the first day of the week (Monday here, see §6)."""
        return self._date_fn(n, ctx, lambda d: float(day_to_date(d).weekday()))

    def fn_date(self, n, ctx):
        """date(value) is the value as a date; date(year, month, day) builds one
        (DOC). A week / month / year value (weekval, monthval, yearval) becomes
        the first day of its period: date(trunc(monthval(today))) = first of the
        month (Reference). The evaluator has no unit types, so this is read off
        the expression (PROVISIONAL)."""
        if len(n.args) >= 3:
            y, m, d = (int(_num(self.arg(n, i, ctx))) for i in range(3))
            return date_to_day(dt.date(y, m, d))
        v = self.arg(n, 0, ctx)
        keymap = _period_keymap(n.args[0])
        if keymap is not None:
            return _map_scalar(_unwrap(v), lambda k: math.nan if math.isnan(k) else keymap(k))
        return v

    # ranges
    def fn_sport(self, n, ctx):
        return self.arg(n, 0, ctx)

    def fn_athleterange(self, n, ctx):
        lo = int(math.floor(_num(self.arg(n, 0, ctx))))
        hi = int(math.floor(_num(self.arg(n, 1, ctx))))
        sub = ctx.child(rng=(lo, hi), workout=None, window=None)
        rec = n.receiver
        if isinstance(rec, P.Call) and rec.name == "sport" and rec.args:
            # sport(x).athleterange(...): only workouts of sport group x (TIS)
            s = self.ev(rec.args[0], ctx)
            sub.sportf = s.lower() if isinstance(s, str) else None
        body = n.args[2:]
        r = None
        for b in body:
            r = self.ev(b, sub)
        return _clip(r, lo, hi, self.ds)

    def fn_workoutrange(self, n, ctx):
        """workoutrange(start_s, end_s, expr): `expr` over one stretch of the
        workout, e.g. first vs second half (Palladino's half-index report).
        At the athlete level it runs once per workout (TIS, W'bal tau): a
        number per workout, or the (x, y) sets of all workouts pooled."""
        if ctx.workout is None:
            out, xs, ys = WS(), [], []
            for w in self._workouts_in(ctx):
                r = self.fn_workoutrange(n, ctx.child(workout=w, vars=dict(ctx.vars)))
                r = _unwrap(r)
                if isinstance(r, Curve):
                    xs.extend(r.xs)
                    ys.extend(r.ys)
                elif not _is_set(r) and not _is_na(r) and not isinstance(r, (RangeV, PairV)):
                    out[w.idx] = r
            return Curve(xs, ys, xkind="value") if xs else out
        lo = _num(self.arg(n, 0, ctx))
        hi = _num(self.arg(n, 1, ctx))
        if math.isnan(lo) or math.isnan(hi):
            return math.nan
        sub = ctx.child(window=(lo, hi))
        r = None
        for b in n.args[2:]:
            r = self.ev(b, sub)
        return r

    # training load
    def fn_tl(self, n, ctx):
        full = ctx.child(rng=None, full=True)
        x = self.ev(n.args[0], full)
        const = _num(self.arg(n, 1, ctx))
        return self._tl(x, const)

    def fn_shift(self, n, ctx):
        return self._shift(self.arg(n, 0, ctx), int(_num(self.arg(n, 1, ctx))))

    def fn_ewma(self, n, ctx):
        x = self.arg(n, 0, ctx)
        k = _num(self.arg(n, 1, ctx))
        if not isinstance(x, np.ndarray):
            raise EvalError("ewma() needs sample data")
        return _ewma(x, k)

    def fn_meanmax(self, n, ctx):
        if len(n.args) < 2:
            return self._meanmax_curve(n, ctx)
        if ctx.workout is None:
            memo = self.ds.memo.setdefault(("meanmax", repr(n)), {})
            out = WS()
            for w in self._workouts_in(ctx):
                if w.idx not in memo:
                    memo[w.idx] = self.fn_meanmax(n, ctx.child(workout=w))
                v = memo[w.idx]
                if not _is_na(v):
                    out[w.idx] = v
            return out
        x = self.arg(n, 0, ctx)
        secs = _num(self.arg(n, 1, ctx))
        if not isinstance(x, np.ndarray) or math.isnan(secs):
            return math.nan
        t = self.ds.channel(ctx.workout.idx, "elapsedtime")
        if t is None or len(t) != len(x):
            # grid-agnostic series (e.g. _rapower4 on WKO5's own 1 s grid)
            t = np.arange(1, len(x) + 1, dtype=float)
        vals = [None if np.isnan(v) else float(v) for v in x]
        _, best = meanmax_time([float(v) for v in t], vals, durations=[secs])
        return math.nan if best[0] is None else float(best[0])

    def fn_nozero(self, n, ctx):
        return lift1(lambda x: np.where(x == 0, np.nan, x), self.arg(n, 0, ctx))

    def fn_metric(self, n, ctx):
        return self.arg(n, 0, ctx)  # data is stored metric already

    # english(): metric -> imperial for the quantities the charts use
    _ENGLISH = {"distance": 0.621371, "speed": 0.621371, "climbing": 3.28084,
                "descending": 3.28084, "elevation": 3.28084}

    def fn_english(self, n, ctx):
        v = self.arg(n, 0, ctx)
        a = n.args[0]
        k = self._ENGLISH.get(a.name) if isinstance(a, P.Ident) else None
        if k is None:
            self.unsupported.add("english() of expression")
            return v
        return lift1(lambda x: x * k, v)

    # channel -> the WKO5 Cache5 expression holding the same per-workout curve
    _CURVE_CACHE_EXPR = {"power": "meanmax(power)", "runpower": "meanmax(power)",
                         "bikepower": "meanmax(power)", "_rapower4": "meanmax(_rapower4)"}

    def _cached_curve(self, node, w: Workout) -> Optional[Curve]:
        """Reuse WKO5's own cached mean-max curve when the argument is a plain
        channel it caches (verified bit-exact against our meanmax)."""
        if not isinstance(node, P.Ident):
            return None
        expr = self._CURVE_CACHE_EXPR.get(node.name)
        if expr is None:
            return None
        if node.name == "runpower" and w.sport != "run":
            return Curve([], [])
        if node.name == "bikepower" and w.sport != "bike":
            return Curve([], [])
        # WKO5's cache holds the curve of the raw samples: once the athlete has
        # approved a correction for this file (a blanked power spike), it would
        # bring the spike right back, so build the curve from corrected samples.
        corr_sig = getattr(self.ds, "_corr_sig", None)
        if corr_sig is not None and corr_sig(w.entry.file):
            return None
        hit = self.ds.curve_cache(expr).get(w.entry.file)
        return None if hit is None else Curve(list(hit[0]), list(hit[1]))

    def _build_curve(self, node, w: Workout) -> Optional[list]:
        """Mean-max curve for one workout as [xs, ys] (JSON-friendly), or None."""
        ctx = Ctx(self.ds, self.begin, self.end, None, {}, w)
        try:
            x = self.ev(node, ctx)
        except EvalError:
            return None
        if not isinstance(x, np.ndarray) or not len(x) or not np.any(~np.isnan(x)):
            return None       # channel absent for this workout — no curve to build
        t = self.ds.channel(w.idx, "elapsedtime")
        tv = ([float(v) for v in t] if t is not None and len(t) == len(x)
              else [float(i) for i in range(1, len(x) + 1)])
        xs, ys = meanmax_time(tv, [None if np.isnan(v) else float(v) for v in x])
        return [list(xs), list(ys)]

    def _workout_curve(self, node, w: Workout) -> Optional[Curve]:
        """WKO5's own cached curve if it has one, else ours from disk, else built.

        Building is the expensive step (seconds per workout for a derived series
        like `_rapower4`), so the result is memoised on disk as well as in
        memory — otherwise every chart request pays it again.
        """
        memo = self.ds.memo.setdefault(("mmcurve", repr(node)), {})
        if w.idx not in memo:
            hit = self._cached_curve(node, w)
            if hit is None:
                cached_series = getattr(self.ds, "cached_series", None)
                built = (cached_series(f"meanmax:{repr(node)}", w,
                                       lambda: self._build_curve(node, w))
                         if cached_series is not None else self._build_curve(node, w))
                hit = None if built is None else Curve(list(built[0]), list(built[1]))
            memo[w.idx] = hit
        return memo[w.idx]

    def _meanmax_curve(self, n, ctx) -> Curve:
        """meanmax(channel) with no duration: the curve for one workout, or the
        athlete envelope (best of each duration) over the active range."""
        node = n.args[0]
        flush = getattr(self.ds, "flush_series", lambda: None)
        if ctx.workout is not None:
            c = self._workout_curve(node, ctx.workout) or Curve([], [])
            flush()
            return c
        c = self._envelope(node, self._workouts_in(ctx))
        flush()
        return c

    def _envelope(self, node, workouts) -> Curve:
        """Best value of each duration over `workouts`, memoised per set of
        workouts — so the per-workout TIS / lookback fits that share a 90-day
        window share one curve (and its cached fit)."""
        key = tuple(w.idx for w in workouts)
        memo = self.ds.memo.setdefault(("mmenv", repr(node)), {})
        if key in memo:
            return memo[key]
        best: dict[float, float] = {}
        for w in workouts:
            c = self._workout_curve(node, w)
            if c is None:
                continue
            for x, y in zip(c.xs, c.ys):
                if y is not None and (x not in best or y > best[x]):
                    best[x] = y
        xs = sorted(best)
        memo[key] = Curve(xs, [best[x] for x in xs])
        return memo[key]

    # ---- power-duration curve family ------------------------------------
    def _curve_arg(self, n, ctx) -> Optional[Curve]:
        c = _unwrap(self.arg(n, 0, ctx))
        return c if isinstance(c, Curve) else None

    def _fit(self, c: Optional[Curve]) -> Optional[dict]:
        if c is None:
            return None
        if c.fit is None:
            c.fit = pdfit([(x, y) for x, y in zip(c.xs, c.ys)
                           if y is not None and not math.isnan(y)]) or {}
        return c.fit or None

    def _pd_scalar(self, n, ctx, key, scale=1.0):
        if len(n.args) >= 2:
            return self._daily_pd(n, ctx, lambda f: f[key] * scale)
        f = self._fit(self._curve_arg(n, ctx))
        if not f or f.get(key) is None:
            return math.nan
        return f[key] * scale

    def _daily_pd(self, n, ctx, getter):
        """ftp / frc / pmax / vo2max / tte (meanmaxcurve, lookback): one value
        per day of the range, each from the model fitted to the mean-max
        envelope of the previous `lookback` days (Reference; formulas.md §6.9c).
        Every day is fitted (a fit is ~10 ms and windows with the same
        workouts share it). A fit failing WKO5's validity gate (§6.5) is na."""
        node = n.args[0]
        if not (isinstance(node, P.Call) and node.name == "meanmax" and len(node.args) == 1):
            raise EvalError(f"{n.name}(curve, lookback) needs meanmax(channel) as the curve")
        lb = _num(self.arg(n, 1, ctx))
        if math.isnan(lb) or lb < 1:
            raise EvalError(f"{n.name}(): lookback must be >= 1 day")
        lb = int(lb)
        chan = node.args[0]
        if ctx.workout is not None:
            lo = hi = int(math.floor(ctx.workout.day))
        else:
            lo, hi = self._eff_rng(ctx) or (self.begin, self.end)
        pool = [w for w in self.wlist if ctx.sportf is None or w.sport == ctx.sportf]
        days = np.array([math.floor(w.day) for w in pool], dtype=float)
        vals = np.full(hi - lo + 1, np.nan)
        for d in range(lo, hi + 1):
            sel = np.nonzero((days >= d - lb + 1) & (days <= d))[0]
            if not len(sel):
                continue
            f = self._fit(self._envelope(chan, [pool[i] for i in sel]))
            if f and f.get("valid"):
                try:
                    vals[d - lo] = float(getter(f))
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    pass
        flush = getattr(self.ds, "flush_series", None)
        if flush is not None:
            flush()
        if ctx.workout is not None:
            return float(vals[0])
        return Daily(lo, vals)

    def fn_ftp(self, n, ctx):
        return self._pd_scalar(n, ctx, "FTP")

    def fn_frc(self, n, ctx):
        return self._pd_scalar(n, ctx, "FRC", 1 / 1000.0)   # frc() reports kJ

    def fn_pmax(self, n, ctx):
        return self._pd_scalar(n, ctx, "Pmax")

    def fn_vo2max(self, n, ctx):
        return self._pd_scalar(n, ctx, "vo2max")

    def fn_tte(self, n, ctx):
        return self._pd_scalar(n, ctx, "tte")

    # model parameters by their expression names (formulas.md §6.6)
    def _pd_param(self, n, ctx, i):
        f = self._fit(self._curve_arg(n, ctx))
        return math.nan if not f else float(f["params"][i])

    def fn_tau1(self, n, ctx):
        return self._pd_param(n, ctx, 1)

    def fn_tau2(self, n, ctx):
        return self._pd_param(n, ctx, 3)

    def fn_dmax(self, n, ctx):
        return self._pd_param(n, ctx, 4)

    def fn_s(self, n, ctx):
        return self._pd_param(n, ctx, 5)

    def _component_curve(self, n, ctx, part):
        c = self._curve_arg(n, ctx)
        f = self._fit(c)
        if c is None or not f:
            return Curve([], [])
        p = f["params"]
        return Curve(list(c.xs), [part(p, float(x)) for x in c.xs])

    def fn_ftpcurve(self, n, ctx):
        """The model's FTP (aerobic) component over the curve's durations
        (DOC; model §6.1 DISASSEMBLY)."""
        return self._component_curve(n, ctx, pd_aerobic)

    def fn_frccurve(self, n, ctx):
        """The model's FRC (anaerobic) component (DOC; model §6.1)."""
        return self._component_curve(n, ctx, pd_anaerobic)

    # optimized interval targets (formulas.md §6.9b, DISASSEMBLY)
    def _target(self, n, ctx, kind):
        lv = _unwrap(self.arg(n, 0, ctx))
        c = _unwrap(self.arg(n, 1, ctx)) if kind != "name" else None
        f = self._fit(c) if isinstance(c, Curve) else None

        def dur(i):
            if not f:
                return math.nan
            FRC, t1, FTP = f["params"][0], f["params"][1], f["params"][2]
            return {0: f["tte"], 1: 0.9625 * FRC / (0.0375 * FTP), 2: 0.1625 * FRC / (0.032 * FTP),
                    3: 5 * math.log(2) * t1, 4: 3 * math.log(2) * t1,
                    5: math.log(2) * t1}.get(i, math.nan)

        def one(x):
            if math.isnan(x):
                return math.nan
            i = int(x)
            if kind == "name":
                return TARGET_NAMES[i] if 0 <= i < len(TARGET_NAMES) else ""
            if kind == "duration":
                return dur(i)
            if not f:
                return math.nan
            FTP = f["params"][2]
            if i in (0, 1, 2):
                return FTP * (1.0, 1.02, 1.2)[i]
            d = dur(i)
            return pdmodel(f["params"], d) if not math.isnan(d) else math.nan
        return _map_scalar(lv, one)

    def fn_targetname(self, n, ctx):
        """Level names (Reference)."""
        return self._target(n, ctx, "name")

    def fn_targetduration(self, n, ctx):
        return self._target(n, ctx, "duration")

    def fn_targetpower(self, n, ctx):
        return self._target(n, ctx, "power")

    # training levels (functions.md §4 tables, formulas.md §6.9 iLevels)
    def _ilevels(self, c: Optional[Curve]):
        """iLevels (name, from, to) from the PD model of a mean-max curve;
        the last level is open-ended (to = na). DISASSEMBLY."""
        f = self._fit(c)
        if not f:
            return None
        p = f["params"]
        F, t1 = p[2], p[1]
        tA, tB = math.log(2) * t1, 3 * math.log(2) * t1
        lo, hi = 1.0, max(float(p[4]), 1.0)
        for _ in range(60):                       # aerobic / P = 0.632 (log10 bisection)
            mid = 10 ** ((math.log10(lo) + math.log10(hi)) / 2)
            if pd_aerobic(p, mid) / pdmodel(p, mid) < 0.632:
                lo = mid
            else:
                hi = mid
        tC = hi
        PA, PB, PC = pdmodel(p, tA), pdmodel(p, tB), pdmodel(p, tC)
        return [("Recovery", 0.0, 0.56 * F), ("Endurance", 0.56 * F, 0.76 * F),
                ("Tempo", 0.76 * F, 0.88 * F), ("Sweetspot", 0.88 * F, 0.95 * F),
                ("FTP", 0.95 * F, 1.05 * F), ("FRC/FTP", 1.05 * F, PC), ("FRC", PC, PB),
                ("Pmax/FRC", PB, PA), ("Pmax", PA, math.nan)]

    def _level_threshold(self, kind, ctx) -> float:
        """T of a level table: the threshold setting of the workout's sport on
        its date; at the athlete level the Run setting at the range end
        (PROVISIONAL — the athlete's own sport)."""
        if ctx.workout is not None:
            v = self.ds.sport_setting(kind, ctx.workout)
        else:
            v = self.ds.setting("run" + kind, float(min(ctx.end, math.floor(self.ds.today))))
        return math.nan if v is None else float(v)

    def _levels_for(self, spec, ctx):
        """[(name, from, to)] for a level system name or a mean-max curve."""
        spec = _unwrap(spec)
        if isinstance(spec, Curve):
            return self._ilevels(spec)
        if not isinstance(spec, str):
            raise EvalError("levels need a level system name or a mean-max curve")
        name = spec.strip().lower()
        if name in ("ilevels", "cogganoptimized"):
            d = int(math.floor(ctx.workout.day)) if ctx.workout is not None else ctx.end
            ws = [w for w in self.wlist if d - 89 <= math.floor(w.day) <= d]
            return self._ilevels(self._envelope(P.Ident("power"), ws))
        table = LEVEL_TABLES.get(LEVEL_ALIASES.get(name, name))
        if table is None:
            self.unsupported.add(f'levels "{name}"')
            raise EvalError(f'unsupported training levels "{name}"')
        kind, rows = table
        T = self._level_threshold(kind, ctx)
        return [(nm, 0.0 if lo is None else lo * T, math.nan if hi is None else hi * T)
                for nm, lo, hi in rows]

    def _level_value(self, n, ctx, what):
        levels = self._levels_for(self.arg(n, 0, ctx), ctx)
        if what == "count":
            # None = no model (ilevels); [] = a system with no levels (ctspower)
            return float(len(levels)) if levels is not None else math.nan

        def one(x):
            if not levels or math.isnan(x) or not 0 <= int(x) < len(levels):
                return "" if what == "name" else math.nan
            nm, lo, hi = levels[int(x)]
            return {"from": lo, "to": hi, "name": nm}[what]
        return _map_scalar(_unwrap(self.arg(n, 1, ctx)), one)

    def fn_levelfrom(self, n, ctx):
        return self._level_value(n, ctx, "from")

    def fn_levelto(self, n, ctx):
        return self._level_value(n, ctx, "to")

    def fn_levelname(self, n, ctx):
        return self._level_value(n, ctx, "name")

    def fn_levelcount(self, n, ctx):
        return self._level_value(n, ctx, "count")

    # histograms (functions.md §3, DISASSEMBLY)
    def _bin_spec(self, v, ctx):
        v = _unwrap(v)
        if isinstance(v, str):
            levels = self._levels_for(v, ctx)       # names only; T is per workout
            return ("levels", v, [nm for nm, _, _ in levels])
        if isinstance(v, ListV):
            cuts, labels = [], []
            for i in v.items:
                lab, c = (i.x, _num(i.y)) if isinstance(i, PairV) else (None, _num(i))
                labels.append(lab)
                if not math.isnan(c):
                    cuts.append(c)
            return ("cuts", tuple(cuts), labels if any(l is not None for l in labels) else None)
        if isinstance(v, (np.ndarray, Curve)):
            vals = _values(v)
            return ("cuts", tuple(float(c) for c in vals if not math.isnan(c)), None)
        s = _num(v)
        if math.isnan(s) or s == 0:
            raise EvalError("bin size must be a single non-zero number")
        return ("size", s, None)

    def _bin_one(self, v, spec, ctx) -> dict:
        """{bin key: accumulated weight} for one set of values."""
        v = _unwrap(v)
        vals = _values(v)
        wts = np.ones(len(vals))
        if isinstance(v, np.ndarray) and ctx.workout is not None:
            dtv = self.ds.channel(ctx.workout.idx, "deltatime")
            if dtv is not None and len(dtv) == len(vals):
                wts = np.nan_to_num(dtv)
        ok = ~np.isnan(vals)
        vals, wts = vals[ok], wts[ok]
        acc: dict = {}
        kind, arg, _ = spec
        if not len(vals):
            return acc
        if kind == "size":
            keys = [_tolfloor(x / arg) for x in vals]
        elif kind == "cuts":
            cuts = np.array(arg, dtype=float)
            if len(cuts) and np.all(np.diff(cuts) >= 0):
                keys = np.searchsorted(cuts, vals, side="right").tolist()
            else:
                keys = [next((j for j, c in enumerate(cuts) if c > x), len(cuts)) for x in vals]
        else:
            levels = self._levels_for(arg, ctx)
            keys = []
            for x in vals:
                k = None
                for j, (_, lo, hi) in enumerate(levels):
                    if x >= lo and (math.isnan(hi) or x < hi):
                        k = j
                        break
                keys.append(k)
        for k, w in zip(keys, wts):
            if k is not None:
                acc[k] = acc.get(k, 0.0) + float(w)
        return acc

    def fn_bin(self, n, ctx):
        """bin(values, binsize | {cuts} | "levels"): weight per bin — seconds
        (deltatime) for workout samples, else a count. Athlete level: the
        bins of every workout in the range added up."""
        spec = self._bin_spec(self.arg(n, 1, ctx), ctx)
        node = n.args[0]
        if ctx.workout is None and needs_samples(node):
            memo_key = ("bin", repr(node), spec[0], spec[1]) \
                if not any(isinstance(x, P.Var) for x in P.walk(node)) else None
            memo = self.ds.memo.setdefault(memo_key, {}) if memo_key else {}
            acc: dict = {}
            for w in self._workouts_in(ctx):
                if w.idx not in memo:
                    wctx = ctx.child(workout=w)
                    try:
                        memo[w.idx] = self._bin_one(self.ev(node, wctx), spec, wctx)
                    except EvalError:
                        memo[w.idx] = {}
                for k, v in memo[w.idx].items():
                    acc[k] = acc.get(k, 0.0) + v
        else:
            acc = self._bin_one(self.ev(node, ctx), spec, ctx)
        kind, arg, labels = spec
        if kind == "size":
            if not acc:
                return Curve([], [], xkind="value")
            ks = range(min(acc), max(acc) + 1)
            return Curve([k * arg for k in ks], [acc.get(k, 0.0) for k in ks], xkind="value")
        nb = len(arg) + 1 if kind == "cuts" else len(labels)
        ys = [acc.get(i, 0.0) for i in range(nb)]
        if labels:
            return ListV([PairV(labels[i] if i < len(labels) and labels[i] is not None else float(i), y)
                          for i, y in enumerate(ys)])
        return ListV(ys)

    def fn_stamina(self, n, ctx):
        f = self._fit(self._curve_arg(n, ctx))
        if not f or not f.get("FTP"):
            return math.nan
        s = 1.0 + f["D"] * (1.0 + math.log(3600.0 / f["TTE"])) / f["FTP"]
        return min(max(s, 0.0), 100.0)

    def fn_pdprofile(self, n, ctx):
        f = self._fit(self._curve_arg(n, ctx))
        return (f or {}).get("phenotype") or math.nan

    def fn_pdcurve(self, n, ctx):
        """Modelled power-duration curve over the source curve's durations."""
        c = self._curve_arg(n, ctx)
        f = self._fit(c)
        if c is None or not f:
            return Curve([], [])
        p = f["params"]
        return Curve(list(c.xs), [pdmodel(p, float(x)) for x in c.xs], fit=f)

    def _is_setting_ident(self, node) -> bool:
        if not isinstance(node, P.Ident):
            return False
        nm = node.name
        if nm in CHANNELS or nm in WORKOUT_METRICS or nm in BUILTIN_IDENTS:
            return False
        return (self.ds.athlete.settings.get(nm) is not None or nm == "weight"
                or nm.endswith(("ftp", "thr", "mhr", "tpace")))

    def _interp(self, n, ctx, step: bool):
        q = self.arg(n, 1, ctx)
        node = n.args[0]
        if self._is_setting_ident(node):
            # a dated setting is a (date, value) set: the value in effect on q
            return _map_scalar(_unwrap(q), lambda x: _num(self.ds.setting(node.name, x)))
        c = _as_curve(_unwrap(self.arg(n, 0, ctx)), self.ds, ctx.workout)
        if c is None:
            return math.nan
        xs = np.array([_num(x) for x in c.xs], dtype=float)
        ys = np.array([_num(y) for y in c.ys], dtype=float)
        ok = ~np.isnan(xs) & ~np.isnan(ys)
        xs, ys = xs[ok], ys[ok]
        if not len(xs):
            return _map_scalar(_unwrap(q), lambda x: math.nan)
        order = np.argsort(xs, kind="stable")
        xs, ys = xs[order], ys[order]
        if step:
            def f(x):
                j = int(np.searchsorted(xs, x, side="right")) - 1
                return math.nan if j < 0 else float(ys[j])
        else:
            def f(x):
                return float(np.interp(x, xs, ys))
        return _map_scalar(_unwrap(q), f)

    def fn_li(self, n, ctx):
        """Linear interpolation, clamped at the ends (DISASSEMBLY 0x6ed370)."""
        return self._interp(n, ctx, False)

    def fn_lookup(self, n, ctx):
        """y of the last point with X <= x (DOC)."""
        return self._interp(n, ctx, True)

    def fn_xx(self, n, ctx):
        """(X,Y) -> (X,X) (Reference): the x values, each still at its x."""
        v = _unwrap(self.arg(n, 0, ctx))
        if isinstance(v, Curve):
            return Curve(list(v.xs), [float(_num(x)) for x in v.xs], xkind=v.xkind)
        if isinstance(v, PairV):
            return v.x
        if isinstance(v, WS):
            return WS({k: self.ds.workouts[k].day for k in v})
        if isinstance(v, Daily):
            return Daily(v.start, np.arange(v.start, v.start + len(v.values), dtype=float))
        if isinstance(v, np.ndarray) and ctx.workout is not None:
            t = self.ds.channel(ctx.workout.idx, "elapsedtime")
            if t is not None and len(t) == len(v):
                return t.astype(float)
            return np.arange(len(v), dtype=float)
        return v

    def fn_yx(self, n, ctx):
        """(X,Y) -> (Y,X) (Reference), e.g. li(yx(pdcurve(mm)), watts) is the
        duration at which the model reaches `watts`."""
        v = _unwrap(self.arg(n, 0, ctx))
        if isinstance(v, Curve):
            return Curve([float(_num(y)) for y in v.ys], [float(_num(x)) for x in v.xs], xkind="value")
        if isinstance(v, PairV):
            return v.y if v.x is None else PairV(v.y, v.x)
        c = _as_curve(v, self.ds, ctx.workout) if _is_set(v) else None
        if c is not None and not isinstance(v, np.ndarray):
            return Curve([float(_num(y)) for y in c.ys], [float(_num(x)) for x in c.xs], xkind="value")
        return v

    # ---- helpers --------------------------------------------------------
    def _eff_rng(self, ctx) -> Optional[tuple[int, int]]:
        """Days an athlete-level set covers: the athleterange, else the chart
        (RHE) range — WKO5 works on the selected range — except inside tl(),
        which integrates the whole history."""
        if ctx.rng is not None:
            return ctx.rng
        if ctx.full:
            return None
        return (ctx.begin, ctx.end)

    def _workouts_in(self, ctx):
        return [w for w in self.wlist if self._in_rng(w.day, ctx)
                and (ctx.sportf is None or w.sport == ctx.sportf)]

    def _in_rng(self, day, ctx):
        r = self._eff_rng(ctx)
        if r is None:
            return True
        d = math.floor(day)
        return r[0] <= d <= r[1]

    def _tl(self, x, const) -> Daily:
        """WKO5 tl() (VERIFIED by disassembly, WKO5.exe @0x70c0c0):
        per calendar day x_d = sum of that day's valid inputs with
        0 <= x <= 5000 (others ignored); v = v + (x_d - v) / const, v = 0
        before the first input."""
        s, e = self.full_span
        daily = np.zeros(e - s, dtype=float)

        def ok(fv):
            return not math.isnan(fv) and 0.0 <= fv <= 5000.0

        if isinstance(x, WS):
            for k, v in x.items():
                fv = _num(v)
                if ok(fv):
                    daily[int(math.floor(self.ds.workouts[k].day)) - s] += fv
        elif isinstance(x, Daily):
            for i, v in enumerate(x.values):
                d = x.start + i
                if s <= d < e and ok(float(v)):
                    daily[d - s] += v
        else:
            fv = _num(x)
            daily[:] = 0 if math.isnan(fv) else fv
        out = np.empty_like(daily)
        v = 0.0
        for i, xv in enumerate(daily):
            v = v + (xv - v) / const
            out[i] = v
        return Daily(s, out)

    def _shift(self, v, k: int):
        v = _unwrap(v)
        if isinstance(v, ListV):
            items = list(v.items)
            if k > 0:
                return ListV([math.nan] * min(k, len(items)) + items[:max(len(items) - k, 0)])
            if k < 0:
                return ListV(items[-k:] + [math.nan] * min(-k, len(items)))
            return v
        if isinstance(v, Daily):
            return Daily(v.start + k, v.values.copy())
        if isinstance(v, np.ndarray):
            if k == 0:
                return v
            r = np.full_like(v, np.nan)
            if k > 0:
                r[k:] = v[:-k]
            else:
                r[:k] = v[-k:]
            return r
        return v


# ---------------------------------------------------------------------------
# free helpers
# ---------------------------------------------------------------------------

def _where(cond, a, b):
    """if(cond, a[, b]) elementwise; missing b -> na."""
    cond, a, b = _unwrap(cond), _unwrap(a), (None if b is None else _unwrap(b))
    if any(isinstance(v, Curve) for v in (cond, a, b)):
        # (x, y) sets: keep the points whose condition holds (x from the value)
        base = next(v for v in (a, cond, b) if isinstance(v, Curve))
        C, A = _curve_operand(cond, base), _curve_operand(a, base)
        B = _curve_operand(b, base) if b is not None else None
        xs, ys, gaps, hole = [], [], [], None
        for i, x in enumerate(base.xs):
            if not math.isnan(C[i]) and C[i] != 0:
                ys.append(float(A[i]))
            elif B is not None:
                ys.append(float(B[i]))
            else:
                if xs and hole is None:
                    hole = x
                continue
            if hole is not None:
                gaps.append((len(xs), hole))
                hole = None
            xs.append(x)
        # a partial curve (`if(@recent > @historic, @recent)`): the dropped
        # stretches are remembered so the chart shows separate segments
        return Curve(xs, ys, xkind=base.xkind, gaps=gaps or None)
    if isinstance(cond, ListV) and not isinstance(a, (np.ndarray, WS, Daily)):
        out = []
        for i, c in enumerate(cond.items):
            ai = a.items[i] if isinstance(a, ListV) and i < len(a.items) else (
                math.nan if isinstance(a, ListV) else a)
            bi = (b.items[i] if isinstance(b, ListV) and i < len(b.items) else (
                math.nan if isinstance(b, ListV) or b is None else b))
            out.append(ai if truthy(c) else bi)
        return ListV(out)
    if isinstance(cond, np.ndarray):
        n = len(cond)
        A = a if isinstance(a, np.ndarray) else np.full(n, _num(a))
        B = b if isinstance(b, np.ndarray) else np.full(n, _num(b) if b is not None else np.nan)
        c = np.where(np.isnan(cond), False, cond != 0)
        return np.where(c, A[:n], B[:n]).astype(float)
    if isinstance(cond, WS) or isinstance(a, WS) or isinstance(b, WS):
        keys = set()
        for v in (cond, a, b):
            if isinstance(v, WS):
                keys |= set(v.keys())
        out = WS()
        for k in keys:
            c = cond.get(k, math.nan) if isinstance(cond, WS) else _at_workout(cond, k)
            if truthy(c):
                val = a.get(k, math.nan) if isinstance(a, WS) else _at_workout(a, k)
            else:
                if b is None:
                    continue
                val = b.get(k, math.nan) if isinstance(b, WS) else _at_workout(b, k)
            if not _is_na(val):
                out[k] = val
        return out
    if isinstance(cond, Daily):
        n = len(cond.values)
        A = a.clip(cond.start, cond.start + n - 1).values if isinstance(a, Daily) else np.full(n, _num(a))
        B = (b.clip(cond.start, cond.start + n - 1).values if isinstance(b, Daily)
             else np.full(n, _num(b) if b is not None else np.nan))
        c = np.where(np.isnan(cond.values), False, cond.values != 0)
        return Daily(cond.start, np.where(c, A[:n], B[:n]))
    if truthy(cond):
        return a
    return b if b is not None else math.nan


def _reduce(v, kind, ds, w):
    v = _unwrap(v)
    if kind in ("slrm", "slrb", "slrrsq"):
        fit = _slr_fit(*_xy_of(v, ds, w))
        if fit is None:
            return math.nan
        return float(fit[{"slrm": 0, "slrb": 1, "slrrsq": 2}[kind]])
    if isinstance(v, np.ndarray) and kind == "avg" and w is not None:
        dtv = ds.channel(w.idx, "deltatime")
        if dtv is not None and len(dtv) == len(v):
            m = ~np.isnan(v) & ~np.isnan(dtv)
            den = dtv[m].sum()
            return float((v[m] * dtv[m]).sum() / den) if den > 0 else math.nan
    vals = _values(v)
    vals = vals[~np.isnan(vals)]
    if kind == "count":
        # valid AND non-zero (Reference: count({3,6,0,9,na,12}) = 4)
        return float(np.count_nonzero(np.abs(vals) >= DBL_MIN))
    if kind in ("stddev", "variance"):
        if len(vals) < 2:
            return math.nan
        var = float(np.var(vals, ddof=1))
        return math.sqrt(var) if kind == "stddev" else var
    if kind in ("pstddev", "pvariance"):
        if len(vals) == 0:
            return math.nan
        var = float(np.var(vals))
        return math.sqrt(var) if kind == "pstddev" else var
    if len(vals) == 0:
        return math.nan
    return float({"sum": np.sum, "avg": np.mean, "max": np.max, "min": np.min}[kind](vals))


DBL_MIN = 2.2250738585072014e-308

# groupby period name -> first day of the period containing a day number
def _start_of_week(d: float) -> float:
    x = day_to_date(d)
    return date_to_day(x - dt.timedelta(days=x.weekday()))      # Monday (see §6)


def _start_of_month(d: float) -> float:
    return date_to_day(day_to_date(d).replace(day=1))


def _start_of_year(d: float) -> float:
    return date_to_day(day_to_date(d).replace(month=1, day=1))


def _start_of_quarter(d: float) -> float:
    x = day_to_date(d)
    return date_to_day(x.replace(month=(x.month - 1) // 3 * 3 + 1, day=1))


def _first_of_next_month(x: dt.date) -> dt.date:
    return dt.date(x.year + (x.month == 12), x.month % 12 + 1, 1)


PERIOD_START = {"day": lambda d: float(math.floor(d)), "week": _start_of_week,
                "month": _start_of_month, "quarter": _start_of_quarter, "year": _start_of_year}


def _period_keymap(node) -> Optional[Callable[[float], float]]:
    """Group keys made with weekval / monthval / yearval are period numbers,
    not dates: map each back to its period's first day so it plots on a date axis."""
    names = {x.name for x in P.walk(node) if isinstance(x, P.Call)}
    if "monthval" in names:
        def m(k):
            i = int(math.floor(k))
            return date_to_day(dt.date(1901 + i // 12, i % 12 + 1, 1))
        return m
    if "yearval" in names:
        return lambda k: date_to_day(dt.date(1901 + int(math.floor(k)), 1, 1))
    if "weekval" in names:
        return lambda k: float(math.floor(k) * 7 - 8)
    return None


def _group_agg(xs: list, kind: str) -> float:
    if kind == "count":
        return float(sum(1 for x in xs if abs(x) >= DBL_MIN))
    if not xs:
        return math.nan
    return float({"sum": sum, "avg": lambda a: sum(a) / len(a), "max": max, "min": min}[kind](xs))


def _expand_range(r: RangeV) -> list:
    """{lo:hi:step} inside a list -> the numbers lo, lo+step, ... (hi
    inclusive). A bound that is a set (e.g. max(power) per workout) is
    reduced to its max / min (PROVISIONAL)."""
    def bound(v, hi):
        v = _unwrap(v)
        if _is_set(v):
            vals = _values(v)
            vals = vals[~np.isnan(vals)]
            return (float(vals.max()) if hi else float(vals.min())) if len(vals) else math.nan
        return _num(v)
    lo, hi, step = bound(r.lo, False), bound(r.hi, True), _num(r.step)
    if any(math.isnan(x) for x in (lo, hi, step)) or step == 0:
        return []
    n = int(math.floor((hi - lo) / step + 1e-9)) + 1
    return [lo + i * step for i in range(max(n, 0))][:100000]


def _pair_set(x, y):
    """(xs, ys) where at least one side is a list / curve: an (x, y) set.
    Curves contribute their y values; a number is repeated. String x values
    (category labels) give a list of pairs."""
    def vals(v):
        v = _unwrap(v)
        if isinstance(v, Curve):
            return list(v.ys)
        if isinstance(v, ListV):
            return [i.y if isinstance(i, PairV) else i for i in v.items]
        if isinstance(v, np.ndarray):
            return list(v)
        if isinstance(v, WS):
            return list(v.values())
        return None
    X, Y = vals(x), vals(y)
    if X is None and Y is None:
        return PairV(x, y)
    n = len(X) if Y is None else len(Y) if X is None else min(len(X), len(Y))
    X = X[:n] if X is not None else [x] * n
    Y = Y[:n] if Y is not None else [y] * n
    if any(isinstance(i, str) for i in X):
        return ListV([PairV(a, b) for a, b in zip(X, Y)])
    return Curve([_num(a) for a in X], [_num(b) if not isinstance(b, str) else b for b in Y],
                 xkind="value")


def _as_curve(v, ds, w) -> Optional[Curve]:
    """Any set as explicit (x, y) points: dates for athlete-level sets,
    elapsed seconds for samples (index when there is no time axis)."""
    v = _unwrap(v)
    if isinstance(v, Curve):
        return v
    if isinstance(v, WS):
        ks = sorted(v, key=lambda k: ds.workouts[k].day)
        return Curve([ds.workouts[k].day for k in ks], [_num(v[k]) for k in ks], xkind="date")
    if isinstance(v, Daily):
        return Curve(list(np.arange(v.start, v.start + len(v.values), dtype=float)),
                     list(v.values), xkind="date")
    if isinstance(v, np.ndarray):
        t = ds.channel(w.idx, "elapsedtime") if w is not None else None
        xs = t if t is not None and len(t) == len(v) else np.arange(len(v), dtype=float)
        return Curve(list(xs), list(v), xkind="value")
    if isinstance(v, PairV):
        if v.x is None:
            return None
        if isinstance(v.x, np.ndarray) and isinstance(v.y, np.ndarray):
            m = min(len(v.x), len(v.y))
            return Curve(list(v.x[:m]), list(v.y[:m]), xkind="value")
        return Curve([_num(v.x)], [_num(v.y)], xkind="value")
    if isinstance(v, ListV):
        xs, ys = [], []
        for i, it in enumerate(v.items):
            c = _as_curve(it, ds, w) if isinstance(it, (PairV, Curve, np.ndarray)) else None
            if c is not None:
                xs.extend(c.xs)
                ys.extend(c.ys)
            elif not isinstance(it, (str, RangeV, ListV)):
                xs.append(float(i))
                ys.append(_num(it))
        return Curve(xs, ys, xkind="value")
    return None


def _xy_of(v, ds, w) -> tuple[np.ndarray, np.ndarray]:
    c = _as_curve(v, ds, w)
    if c is None:
        return np.array([]), np.array([])
    return (np.array([_num(x) for x in c.xs], dtype=float),
            np.array([_num(y) for y in c.ys], dtype=float))


def _slr_fit(x: np.ndarray, y: np.ndarray):
    """(m, b, r²) of the least-squares line over points with valid x and y
    (functions.md §7): m = (nΣxy − ΣxΣy)/(nΣx² − (Σx)²), b = (Σy − mΣx)/n."""
    ok = ~np.isnan(x) & ~np.isnan(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 2:
        return None
    # centred sums: the same m and b, without cancellation on day numbers ~45000
    mx, my = x.mean(), y.mean()
    sxx = float(((x - mx) ** 2).sum())
    if sxx == 0:
        return None
    sxy = float(((x - mx) * (y - my)).sum())
    m = sxy / sxx
    b = my - m * mx
    syy = float(((y - my) ** 2).sum())
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else math.nan
    return m, b, r2


def _seq_map(v, f: Callable[[np.ndarray], np.ndarray]):
    """Apply an order-dependent array transform (cumsum, delta, filter) to a
    set in its natural order: samples, workouts by date, days, list items."""
    v = _unwrap(v)
    if isinstance(v, np.ndarray):
        return f(v.astype(float))
    if isinstance(v, WS):
        ks = sorted(v, key=lambda k: _CUR_CTX[-1].ds.workouts[k].day if _CUR_CTX else k)
        r = f(np.array([_num(v[k]) for k in ks], dtype=float))
        return WS({k: float(x) for k, x in zip(ks, r)})
    if isinstance(v, Daily):
        return Daily(v.start, f(v.values.astype(float)))
    if isinstance(v, Curve):
        r = f(np.array([_num(y) for y in v.ys], dtype=float))
        return Curve(list(v.xs), [float(x) for x in r], xkind=v.xkind)
    if isinstance(v, ListV):
        r = f(np.array([_num(i) for i in v.items], dtype=float))
        return ListV([float(x) for x in r])
    return float(f(np.array([_num(v)]))[0])


def _cumsum(a: np.ndarray) -> np.ndarray:
    out = np.nancumsum(a)
    out[np.isnan(a)] = np.nan
    return out


def _delta(a: np.ndarray) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) > 1:
        out[1:] = a[1:] - a[:-1]
    return out


def _kernel_filter(a: np.ndarray, k: np.ndarray, sides: int) -> np.ndarray:
    """WKO5 filter loop (0x6dd4bb…0x6dd638). For every i, m = 0…K−1 in order:
    s = i − off + m; if 0 <= s < n and a[s] is valid: wsum = k[m] + wsum,
    acc = k[m]·a[s] + acc. Then acc / wsum unless wsum ≈ 0 (|wsum| < DBL_MIN,
    0x4a59a0), na when no sample was valid. off = K−1 for sides 1 (only past
    samples, k[K−1] on the current one), (K−1)//2 for sides 2. Vectorised
    over i, sequential over m, so the sums round exactly like WKO5's."""
    n, K = len(a), len(k)
    if not n or not K:
        return np.full(n, np.nan)
    off = (K - 1) // 2 if sides == 2 else K - 1
    valid = ~np.isnan(a)
    acc = np.zeros(n)
    wsum = np.zeros(n)
    seen = np.zeros(n, dtype=bool)
    idx = np.arange(n)
    for m in range(K):
        s = idx - off + m
        ok = (s >= 0) & (s < n)
        ok[ok] = valid[s[ok]]
        if not ok.any():
            continue
        w = float(k[m])
        acc[ok] = w * a[s[ok]] + acc[ok]
        wsum[ok] = w + wsum[ok]
        seen |= ok
    out = np.full(n, np.nan)
    div = seen & ~(np.abs(wsum) < DBL_MIN)
    out[div] = acc[div] / wsum[div]
    keep = seen & ~div
    out[keep] = acc[keep]
    return out


def _dfrc(p: np.ndarray, dt: np.ndarray, frc_j: float, ftp: float) -> np.ndarray:
    """WKO5 dfrc loop (0x6d82f0…0x6d8489); see Evaluator.fn_dfrc."""
    out = np.full(len(p), np.nan)
    dep = rec = t_rec = 0.0          # [ebp-0x134], [ebp-0x16c], [ebp-0x158]
    for i in range(len(p)):
        d = float(dt[i])
        if not d >= 0.001:           # na time or dt < 0.001 s: no point
            continue
        x = float(p[i])
        if x != x:
            x = 0.0
        if x > ftp:
            left = dep - rec
            t_rec = 0.0
            dep = (x - ftp) * d + (left if left > 0.0 else 0.0)
            rec = 0.0
        else:
            t_rec += d
            fast = (1.0 - math.exp(t_rec / -25.0)) * (dep * 0.3)
            rec = (1.0 - math.exp(t_rec / -300.0)) * (dep * 0.7) + fast
        out[i] = ((frc_j - dep) + rec) / 1000.0
    return out


# round(): 10^-places for places -7..7, WKO5's own table (0x4a5be8…0x4a5c47)
_ROUND_SCALE = (1e7, 1e6, 1e5, 1e4, 1000.0, 100.0, 10.0, 1.0,
                0.1, 0.01, 0.001, 0.0001, 1e-05, 1e-06, 1e-07)


def _wko_round(x, m: float):
    """0x4a5bb0: x > 0 → floor(x·m + 0.5)/m, else ceil(x·m − 0.5)/m; results
    within DBL_MIN of zero become +0; na stays na."""
    with np.errstate(invalid="ignore"):
        xa = np.asarray(x, dtype=float)
        r = np.where(xa > 0, np.floor(xa * m + 0.5), np.ceil(xa * m - 0.5)) / m
        r = np.where(np.abs(r) < DBL_MIN, 0.0, r)
    return r if isinstance(x, np.ndarray) else float(r)


def _pick(vals: list, k: int, mode: str) -> list:
    """Indices (in original order) of the k greatest / least / first / last
    valid values."""
    idx = [i for i, x in enumerate(vals) if not math.isnan(x)]
    if mode == "greatest":
        chosen = sorted(idx, key=lambda i: -vals[i])[:k]
    elif mode == "least":
        chosen = sorted(idx, key=lambda i: vals[i])[:k]
    elif mode == "first":
        chosen = idx[:k]
    else:
        chosen = idx[-k:] if k else []
    return sorted(chosen)


def _map_scalar(q, f: Callable[[float], Any]):
    """Apply f to every number of q, keeping q's shape."""
    if isinstance(q, ListV):
        return ListV([_map_scalar(_unwrap(i), f) for i in q.items])
    if isinstance(q, np.ndarray):
        return np.array([f(float(x)) for x in q], dtype=float)
    if isinstance(q, Curve):
        return Curve(list(q.xs), [f(_num(y)) for y in q.ys], xkind=q.xkind)
    if isinstance(q, WS):
        return WS({k: f(_num(x)) for k, x in q.items()})
    if isinstance(q, Daily):
        return Daily(q.start, np.array([f(float(x)) for x in q.values], dtype=float))
    if isinstance(q, RangeV):
        return ListV([f(x) for x in _expand_range(q)]) if q.step is not None else f(_num(q.lo))
    return f(_num(q))


def _tolfloor(x: float) -> int:
    """WKO5's tolerant floor (0x4a5b30): 29.9999999999999 -> 30."""
    f = math.floor(x)
    return int(f + 1) if abs(x - (f + 1)) < 1e-15 * abs(x + f + 1) else int(f)


TARGET_NAMES = ["Extensive Aerobic (FTP)", "Intensive Aerobic (FTP)",
                "Max Aerobic (VO2 Max Intensive)", "Extensive Anaerobic (FRC)",
                "Intensive Anaerobic (FRC)", "Max"]

# level systems (functions.md §4): threshold kind, [(name, lo, hi) as fractions of T]
LEVEL_TABLES = {
    "classicpower": ("ftp", [("Active Recovery", None, .56), ("Endurance", .56, .76),
                             ("Tempo", .76, .91), ("Threshold", .91, 1.06),
                             ("VO2max", 1.06, 1.21), ("Anaerobic Capacity", 1.21, None)]),
    "classichr": ("thr", [("Active Recovery", None, .69), ("Endurance", .69, .84),
                          ("Tempo", .84, .95), ("Threshold", .95, 1.06), ("VO2max", 1.06, None)]),
    "frielhr": ("thr", [("Recovery", None, .85), ("Aerobic", .85, .90), ("Tempo", .90, .95),
                        ("Sub-Threshold", .95, 1.00), ("Super-Threshold", 1.00, 1.03),
                        ("Aerobic Capacity", 1.03, 1.06), ("Anaerobic Capacity", 1.06, None)]),
    # names not extracted from WKO5.exe (a format string) — PROVISIONAL
    "usachr": ("mhr", [("Level 1", None, .66), ("Level 2", .66, .73), ("Level 3", .73, .84),
                       ("Level 4", .84, .91), ("Level 5", .91, None)]),
    "bcfhr": ("mhr", [("Level 1", None, .65), ("Level 2", .65, .75), ("Level 3", .75, .82),
                      ("Level 4", .82, .89), ("Level 5", .89, .94), ("Level 6", .94, None)]),
    # pace systems: fractions of threshold pace (min/km), built fastest-first
    # (DISASSEMBLY); compared as pace, not speed — PROVISIONAL (§9 item 1)
    "frielpace": ("tpace", [("Zone 5c", None, .90), ("Zone 5b", .90, .97), ("Zone 5a", .97, 1.00),
                            ("Zone 4", 1.00, 1.06), ("Zone 3", 1.06, 1.14), ("Zone 2", 1.14, 1.29),
                            ("Zone 1", 1.29, None)]),
    "pzipace": ("tpace", [("Speed", None, .86), ("Gray IV", .86, .89), ("VO2max", .89, .91),
                          ("Gray III", .91, .97), ("Thresh", .97, 1.00), ("Gray II", 1.00, 1.05),
                          ("H Aero", 1.05, 1.11), ("M Aero", 1.11, 1.22), ("L Aero", 1.22, 1.35),
                          ("Gray", 1.35, None)]),
    # PKCTSPowerLevels / PKRSTPowerLevels: the shared builder 0x64d1e0 only reads
    # the `power` threshold and clears the table, and their level count (vtable
    # slot 11 = 0x457000) returns 0 — both systems have no levels in 5.0.587.
    "ctspower": ("ftp", []),
    "rstpower": ("ftp", []),
}
LEVEL_ALIASES = {"cogganclassic": "classicpower", "cogganhr": "classichr"}


def _clip(v, lo, hi, ds):
    if isinstance(v, Daily):
        return v.clip(lo, hi)
    if isinstance(v, WS):
        return WS({k: x for k, x in v.items() if lo <= math.floor(ds.workouts[k].day) <= hi})
    return v


def _rolling_time_avg(x: np.ndarray, t: Optional[np.ndarray], window: float) -> np.ndarray:
    """PROVISIONAL: trailing time-window mean (for 30 s NP smoothing)."""
    if t is None or len(t) != len(x):
        k = int(window)
        c = np.convolve(np.nan_to_num(x), np.ones(k) / k, mode="full")[:len(x)]
        return c
    out = np.full(len(x), np.nan)
    j = 0
    acc = cnt = 0.0
    for i in range(len(x)):
        if not np.isnan(x[i]):
            acc += x[i]
            cnt += 1
        while t[i] - t[j] >= window:
            if not np.isnan(x[j]):
                acc -= x[j]
                cnt -= 1
            j += 1
        out[i] = acc / cnt if cnt else np.nan
    return out


def _ewma(x: np.ndarray, k: float) -> np.ndarray:
    """PROVISIONAL: alpha = 1/k per sample."""
    out = np.full(len(x), np.nan)
    v = math.nan
    a = 1.0 / k if k else 1.0
    for i, xv in enumerate(x):
        if np.isnan(xv):
            out[i] = v
            continue
        v = xv if math.isnan(v) else v + a * (xv - v)
        out[i] = v
    return out


def _meanmax(x: np.ndarray, t: Optional[np.ndarray], secs: float) -> float:
    """Best average over any `secs`-second window (assumes ~1 s samples)."""
    if x is None or len(x) == 0:
        return math.nan
    k = int(round(secs))
    v = np.nan_to_num(x)
    if len(v) < k or k <= 0:
        return math.nan
    c = np.cumsum(np.insert(v, 0, 0.0))
    return float(np.max((c[k:] - c[:-k]) / k))
