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
from backend.engine.algorithms.wko5_pdmodel import fit as pdfit, model as pdmodel
from backend.engine.algorithms.wko5_power import rapower
from backend.engine.wko5expr import parser as P
from backend.engine.wko5expr.dataset import Dataset, Workout, day_to_date, date_to_day


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
    """A mean-max / power-duration curve: xs = durations (s), ys = values."""
    xs: list
    ys: list
    fit: Optional[dict] = None       # cached wko5_pdmodel.fit() result

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
    if isinstance(v, (int, float, np.floating)):
        return float(v)
    return math.nan


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

CHANNELS = {"power", "runpower", "bikepower", "heartrate", "runheartrate", "speed", "runspeed",
            "cadence", "runcadence", "elevation", "_elevation", "deltatime", "_rapower4",
            "temperature", "stancetime", "verticaloscillation", "elapsedtime", "_rapower"}
RUN_ONLY = {"runpower": "power", "runheartrate": "heartrate", "runspeed": "speed", "runcadence": "cadence"}
BIKE_ONLY = {"bikepower": "power"}

ATHLETE_CONSTANTS = {"ctlconstant", "atlconstant", "rampconstant"}
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

    def child(self, **kw) -> "Ctx":
        c = Ctx(self.ds, self.begin, self.end, self.rng, self.vars, self.workout, self.window)
        for k, v in kw.items():
            setattr(c, k, v)
        return c


# Calls that consume sample data themselves (lift per workout internally).
SAMPLE_CONSUMERS = {"sum", "avg", "max", "min", "count", "meanmax", "athleterange", "workoutrange",
                    "pdcurve", "ftp", "frc", "pmax", "vo2max", "tte", "stamina", "pdprofile"}


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


def lift2(op: Callable, a, b):
    """Apply a binary op with WKO5 broadcasting rules."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        with np.errstate(all="ignore"):
            A = a if isinstance(a, np.ndarray) else np.full(len(b), _num(a))
            B = b if isinstance(b, np.ndarray) else np.full(len(a), _num(b))
            n = min(len(A), len(B))
            r = op(A[:n], B[:n])
            if r.dtype == bool:
                r = r.astype(float)
            r = np.where(np.isnan(A[:n]) | np.isnan(B[:n]), np.nan, r)
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
        return PairV(None if n.x is None else self.ev(n.x, ctx),
                     None if n.y is None else self.ev(n.y, ctx))

    def ev_ListLit(self, n, ctx):
        return ListV([self.ev(i, ctx) for i in n.items])

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
            return self._windowed(self._channel(name, ctx.workout), ctx)
        if name in WORKOUT_METRICS:
            return self._ws_metric(name, ctx)
        # dated settings: runftp, runthr, weight, ...
        if ds.athlete.settings.get(name) is not None or name.endswith(("ftp", "thr", "mhr", "tpace")):
            if ctx.workout is not None:
                return ds.setting(name, ctx.workout.day)
            return WS({w.idx: ds.setting(name, w.day) for w in self.wlist})
        # any other channel recorded in this workout's file
        if ctx.workout is not None and name in self._workout_channel_names(ctx.workout):
            return self._windowed(self._channel(name, ctx.workout), ctx)
        self.unsupported.add(name)
        raise EvalError(f"unsupported identifier {name}")

    def _ws_metric(self, name, ctx):
        if ctx.workout is not None:
            return self._workout_value(name, ctx.workout)
        return WS({w.idx: self._workout_value(name, w) for w in self.wlist})

    def _workout_value(self, name, w: Workout):
        if name == "sport":
            return w.sport
        if name == "date":
            return w.day
        if name == "title":
            return w.entry.file
        v = w.metrics.get(name)
        return math.nan if v is None else v

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
        if name == "rgrade":
            return _grade(ds.channel(w.idx, "_elevation") if ds.channel(w.idx, "_elevation") is not None
                          else ds.channel(w.idx, "elevation"),
                          ds.channel(w.idx, "elapseddistance"), n_samples)
        if name in ("_rapower", "_rapower4"):
            # NOTE: on WKO5's own 1 s grid (not the sample grid) — only valid
            # inside grid-agnostic reducers such as meanmax()/avg of itself.
            f = ds.wko4(w.idx)
            pc, tc = (f.channels.get("power"), f.channels.get("elapsedtime")) if f else (None, None)
            if pc is None or tc is None:
                return np.full(n_samples, np.nan)
            ra = np.array([a for _, a in rapower(tc.values, pc.values)], dtype=float)
            return ra ** 4 if name == "_rapower4" else ra
        arr = ds.channel(w.idx, name)
        return np.full(n_samples, np.nan) if arr is None else arr

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
    def _grouped(self, n, ctx, kind):
        """sum(numbers, groupby) — aggregate within each group instead of over
        everything. The usual use is a date bucket, e.g.
        `sum(climbing, startofweek(date))` for a weekly total."""
        values = self.ev(n.args[0], ctx)
        keys = self.ev(n.args[1], ctx)
        if not isinstance(values, WS) or not isinstance(keys, WS):
            return self._aggregate(n, ctx, kind)     # not per-workout: plain reduce
        buckets: dict = {}
        for idx, v in values.items():
            k = keys.get(idx)
            if _is_na(k) or _is_na(v):
                continue
            buckets.setdefault(k, []).append(_num(v))
        if not buckets:
            return math.nan
        agg = {"sum": sum, "count": len,
               "avg": lambda xs: sum(xs) / len(xs),
               "max": max, "min": min}[kind]
        out = {k: float(agg(xs)) for k, xs in buckets.items()}
        if all(isinstance(k, (int, float)) and not math.isnan(k) for k in out):
            start = int(math.floor(min(out)))
            end = int(math.floor(max(out)))
            arr = np.full(end - start + 1, np.nan)
            for k, v in out.items():
                arr[int(math.floor(k)) - start] = v
            return Daily(start, arr)
        return ListV([PairV(k, v) for k, v in sorted(out.items(), key=lambda kv: str(kv[0]))])

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
        elif isinstance(v, Daily) and ctx.rng is not None:
            v = v.clip(*ctx.rng)
        return _reduce(v, kind, self.ds, None)

    def fn_sum(self, n, ctx):
        if len(n.args) == 2:
            return self._grouped(n, ctx, "sum")
        return self._aggregate(n, ctx, "sum")

    def fn_avg(self, n, ctx):
        if len(n.args) == 2:
            return self._grouped(n, ctx, "avg")
        return self._aggregate(n, ctx, "avg")

    def fn_max(self, n, ctx):
        if len(n.args) == 2:
            return lift2(lambda a, b: np.fmax(a, b) if isinstance(a, np.ndarray) else max(a, b),
                         self.arg(n, 0, ctx), self.arg(n, 1, ctx))
        return self._aggregate(n, ctx, "max")

    def fn_min(self, n, ctx):
        if len(n.args) == 2:
            return lift2(lambda a, b: np.fmin(a, b) if isinstance(a, np.ndarray) else min(a, b),
                         self.arg(n, 0, ctx), self.arg(n, 1, ctx))
        return self._aggregate(n, ctx, "min")

    def fn_count(self, n, ctx):
        if len(n.args) == 2:
            return self._grouped(n, ctx, "count")
        return self._aggregate(n, ctx, "count")

    # math
    def fn_trunc(self, n, ctx):
        return lift1(np.trunc, self.arg(n, 0, ctx))

    def fn_round(self, n, ctx):
        v = self.arg(n, 0, ctx)
        digits = int(_num(self.arg(n, 1, ctx))) if len(n.args) > 1 else 0
        return lift1(lambda x: np.round(x, digits), v)

    def fn_ceil(self, n, ctx):
        return lift1(np.ceil, self.arg(n, 0, ctx))

    def fn_sqrt(self, n, ctx):
        return lift1(np.sqrt, self.arg(n, 0, ctx))

    def fn_log10(self, n, ctx):
        return lift1(np.log10, self.arg(n, 0, ctx))

    def fn_abs(self, n, ctx):
        return lift1(np.abs, self.arg(n, 0, ctx))

    # dates
    def fn_startofweek(self, n, ctx):
        def sow(x):
            d = day_to_date(x)
            return date_to_day(d - dt.timedelta(days=d.weekday()))  # Monday
        return lift1(lambda x: np.vectorize(sow)(x) if isinstance(x, np.ndarray) else sow(float(x)),
                     self.arg(n, 0, ctx))

    fn_weekval = fn_startofweek  # PROVISIONAL: week value treated as start of week

    def fn_date(self, n, ctx):
        return self.arg(n, 0, ctx)

    # ranges
    def fn_athleterange(self, n, ctx):
        lo = int(math.floor(_num(self.arg(n, 0, ctx))))
        hi = int(math.floor(_num(self.arg(n, 1, ctx))))
        sub = ctx.child(rng=(lo, hi), workout=None)
        body = n.args[2:]
        r = None
        for b in body:
            r = self.ev(b, sub)
        return _clip(r, lo, hi, self.ds)

    def fn_workoutrange(self, n, ctx):
        """workoutrange(start_s, end_s, expr): `expr` over one stretch of the
        workout, e.g. first vs second half (Palladino's half-index report)."""
        if ctx.workout is None:
            raise EvalError("workoutrange() only works inside a workout chart")
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
        full = ctx.child(rng=None)
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
        best: dict[float, float] = {}
        for w in self._workouts_in(ctx):
            c = self._workout_curve(node, w)
            if c is None:
                continue
            for x, y in zip(c.xs, c.ys):
                if y is not None and (x not in best or y > best[x]):
                    best[x] = y
        flush()
        xs = sorted(best)
        return Curve(xs, [best[x] for x in xs])

    # ---- power-duration curve family ------------------------------------
    def _curve_arg(self, n, ctx) -> Optional[Curve]:
        c = self.arg(n, 0, ctx)
        return c if isinstance(c, Curve) else None

    def _fit(self, c: Optional[Curve]) -> Optional[dict]:
        if c is None:
            return None
        if c.fit is None:
            c.fit = pdfit([(x, y) for x, y in zip(c.xs, c.ys) if y is not None]) or {}
        return c.fit or None

    def _pd_scalar(self, n, ctx, key, scale=1.0):
        f = self._fit(self._curve_arg(n, ctx))
        if not f or f.get(key) is None:
            return math.nan
        return f[key] * scale

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

    def fn_li(self, n, ctx):
        c = self._curve_arg(n, ctx)
        if c is None:
            return math.nan
        return c.at(_num(self.arg(n, 1, ctx)))

    fn_lookup = fn_li

    def fn_xx(self, n, ctx):
        v = self.arg(n, 0, ctx)
        if isinstance(v, Curve):
            return np.array(v.xs, dtype=float)
        return v.x if isinstance(v, PairV) else v

    def fn_yx(self, n, ctx):
        v = self.arg(n, 0, ctx)
        if isinstance(v, Curve):
            return np.array([np.nan if y is None else y for y in v.ys], dtype=float)
        return v.y if isinstance(v, PairV) else v

    def fn_greatest(self, n, ctx):
        # PROVISIONAL: greatest(values, n) — keep values, n ignored
        self.unsupported.add("greatest() semantics unverified")
        return self.arg(n, 0, ctx)

    # ---- helpers --------------------------------------------------------
    def _workouts_in(self, ctx):
        return [w for w in self.wlist if self._in_rng(w.day, ctx)]

    def _in_rng(self, day, ctx):
        if ctx.rng is None:
            return True
        d = math.floor(day)
        return ctx.rng[0] <= d <= ctx.rng[1]

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
    if isinstance(v, np.ndarray):
        vals = v[~np.isnan(v)]
        if kind == "avg" and w is not None:
            dtv = ds.channel(w.idx, "deltatime")
            if dtv is not None and len(dtv) == len(v):
                m = ~np.isnan(v) & ~np.isnan(dtv)
                den = dtv[m].sum()
                return float((v[m] * dtv[m]).sum() / den) if den > 0 else math.nan
    elif isinstance(v, WS):
        vals = np.array([_num(x) for x in v.values()], dtype=float)
        vals = vals[~np.isnan(vals)]
    elif isinstance(v, Daily):
        vals = v.values[~np.isnan(v.values)]
    elif isinstance(v, ListV):
        vals = np.array([_num(x) for x in v.items], dtype=float)
        vals = vals[~np.isnan(vals)]
    else:
        x = _num(v)
        vals = np.array([] if math.isnan(x) else [x])
    if kind == "count":
        return float(len(vals))
    if len(vals) == 0:
        return math.nan
    return float({"sum": np.sum, "avg": np.mean, "max": np.max, "min": np.min}[kind](vals))


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


def _grade(elev: Optional[np.ndarray], dist_km: Optional[np.ndarray], n: int,
           span_m: float = 10.0) -> np.ndarray:
    """PROVISIONAL rgrade: rise / run over a trailing ~span_m metres of distance."""
    if elev is None or dist_km is None or len(elev) != len(dist_km):
        return np.full(n, np.nan)
    d = dist_km * 1000.0
    out = np.full(len(d), np.nan)
    j = 0
    for i in range(len(d)):
        if np.isnan(d[i]) or np.isnan(elev[i]):
            continue
        while j < i and (np.isnan(d[j]) or d[i] - d[j] > span_m):
            j += 1
        k = max(j - 1, 0)
        if not np.isnan(d[k]) and not np.isnan(elev[k]) and d[i] - d[k] > 0:
            out[i] = (elev[i] - elev[k]) / (d[i] - d[k])
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
