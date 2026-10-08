"""
What one chart depends on — the per-chart parts of its render-cache key (SP-336, SP-320 ⑤;
docs/research/cache-tiering.md §7.2 items 8–9).

The render cache (render_cache.py) used to key every chart on the whole dataset (every
activity, today) and on every engine file: one new activity, a new day or any deploy dropped
all of them. Here each chart gets three narrower parts instead:

    s   = scope_of(chart, ds, begin, end, workout)   # the days [lo, hi] it reads, + today?
    fp  = fingerprint(ds, s)                         # the activities in [lo, hi] (+ today)
    sig = code_signature(chart, ds)                  # the code its chart type runs

**Scope** — a static reading of the chart's expressions (parser AST), never a run:

* an activity metric, a channel aggregation, a per-period total: the chart's own range;
* `tl(x, c)` and the builtins `ctl` / `atl` / `tsb` (exponentially weighted loads): the range
  plus WARMUP_TAU (6) time constants before it — 6 × 42 = 252 days for CTL. An older activity
  still moves the value by at most e^-6 ≈ 0.25 % of its own share, which the owner accepted
  (SP-336: 「PMC 類預熱期 6 × 42 天」);
* `shift(x, k)`, `athleterange(a, b, …)`, `ftp(curve, lookback)` (and frc / pmax / vo2max /
  tte), the TIS builtins (`@lookback:=90`), `drift_avg()` (8 weeks), the training levels
  (90-day fit) and the settings the chart may estimate (runtpace: the as-of estimate reads
  up to 180 days back): their own reach, from the literal numbers, `begindate` / `enddate` /
  `today` / `date` and `@vars` (interval arithmetic, min / max, trunc, startof…);
* anything it cannot bound (a computed shift, an unknown function inside a date) and every
  chart kind that is not an expression chart (zones, targets, 5 區流程, period zones, VAM,
  poles, the review card, activity panels): the whole history, with today.

`today` is part of the key only when the chart reads it (`today` / `now`, the season goals,
a race reference line) or its range reaches today — so a day change only drops the charts
that end today (their range moves anyway) and the few that read the date.

**Fingerprint** — global inputs that every chart reads (engine config, the season plan and
corrections stamps, the WKO5 athlete file, the manual PMC start, the watch-power and
bad-file settings), plus one digest per activity in [lo, hi]: its file and stamp, day, sport,
tags, platform, metrics (TSS etc. as computed with the thresholds in effect), the thresholds
in effect that day (`Dataset._settings_sig`) and its power source; plus the dated settings
at both ends of the scope, the stored test / done interval sessions of those days, and the
activity weather stamp when the chart reads drift(). A digest per activity is memoised on
the Dataset (one pass per Dataset / plan / corrections), the window's hash per (lo, hi).

**Code signature** — engine/codehash.py (SP-332) over the code a chart type reaches: the
Dataset build (its class's `__init__`: metrics, thresholds, estimates), the expression
evaluator's dispatch (`ev_*`) plus each `fn_<call>` the chart's expressions use (the
evaluator looks them up by name, which codehash cannot see), the renderer of its kind and
its post-processing (period, window, basis, variant, race references, drift bars), plus the
endpoint's own glue code. `render_cache.CACHE_VERSION` stays as the manual version for
what codehash cannot see (a registry, a getattr). A deploy that changes workout_review.py
drops the drift and review charts, not the TSS or volume charts.
"""
from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import importlib
import json
import math
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from backend.engine.wko5expr import parser as P

WARMUP_TAU = 6                 # time constants of warm-up for an EWMA load (owner: 6 × 42 days)
DRIFT_AVG_DAYS = 56            # drift_avg(): drift_agg.AGG_DAYS (the last runs within 8 weeks)
SETTING_EST_DAYS = 180         # runtpace / runftp estimates: thresholds.WINDOWS[-1] (as-of, earlier runs)
LEVEL_DAYS = SETTING_EST_DAYS  # levelfrom / bin("ilevels"): a 90-day power fit + the threshold estimate
PD_LOOKBACK_FNS = {"ftp", "frc", "pmax", "vo2max", "tte"}     # (curve, lookback): Evaluator._daily_pd
LEVEL_FNS = {"levelfrom", "levelto", "levelname", "levelcount", "bin", "targetpower", "targetduration",
             "targetname"}
EST_SETTING_SUFFIXES = ("tpace", "ftp")                         # Evaluator._setting's estimate fallbacks
EXPR_KINDS = ("athlete", "workout")
# chart kinds drawn by a panel of their own (not the expression engine): whole history + today
PANEL_KINDS = ("zones", "targets", "z5gate", "periodzones", "climbvam", "polecompare", "review", "activity")
_PERIOD_BACK = {"startofweek": 6, "startofmonth": 31, "startofquarter": 92, "startofyear": 366}
_ROUND = {"trunc", "floor", "round", "ceil", "int"}


@dataclass(frozen=True)
class Scope:
    """The days [lo, hi] (day numbers, inclusive) whose activities a chart reads; None = no
    bound on that side (lo None = the whole history). `today`: today's date is an input.
    `weather`: it reads the per-activity weather (drift())."""
    lo: Optional[int]
    hi: Optional[int]
    today: bool = False
    weather: bool = False

    @property
    def full(self) -> bool:
        return self.lo is None

    @classmethod
    def whole(cls) -> "Scope":
        return cls(None, None, True, True)

    def as_json(self) -> list:
        return [self.lo, self.hi, self.today, self.weather]


class _Unbounded(Exception):
    """The analysis cannot bound what an expression reads: the whole history."""


class _Reach:
    """One pass over a chart's expressions. `visit(node, lo, hi, vars)` records the day range
    the node reads in `self.lo` / `self.hi`; `ival(node, …)` is a value's [min, max] when it is
    a day or a number the analysis can follow (None otherwise)."""

    def __init__(self, ds, b: int, e: int):
        self.ds = ds
        self.B, self.E = b, e
        self.T = int(math.floor(ds.today))
        ath = getattr(ds, "athlete", None)
        self.ctlc = float(getattr(ath, "ctlconstant", 42.0) or 42.0)
        self.atlc = float(getattr(ath, "atlconstant", 7.0) or 7.0)
        self.lo, self.hi = b, e
        self.today = False
        self.weather = False
        self._builtin: dict = {}

    def use(self, lo: float, hi: float) -> None:
        self.lo = min(self.lo, int(math.floor(lo)))
        self.hi = max(self.hi, int(math.floor(hi)))

    # ---- day / number intervals -------------------------------------------
    def ival(self, n, lo, hi, vars_) -> Optional[tuple[float, float]]:
        if isinstance(n, P.Num):
            return (float(n.value), float(n.value))
        if isinstance(n, P.Unit):
            return self.ival(n.value, lo, hi, vars_)
        if isinstance(n, P.Ident):
            name = n.name
            if name == "begindate":
                return (self.B, self.B)
            if name == "enddate":
                return (self.E, self.E)
            if name in ("today", "now"):
                self.today = True
                return (self.T, self.T + 1 - 1e-9)
            if name == "date":
                return (lo, hi + 1 - 1e-9)
            if name == "ctlconstant":
                return (self.ctlc, self.ctlc)
            if name == "atlconstant":
                return (self.atlc, self.atlc)
            if name == "rampconstant":
                return (7.0, 7.0)
            return None
        if isinstance(n, P.Var):
            return vars_.get(n.name)
        if isinstance(n, P.Unary) and n.op in ("-", "+"):
            v = self.ival(n.operand, lo, hi, vars_)
            if v is None:
                return None
            return (-v[1], -v[0]) if n.op == "-" else v
        if isinstance(n, P.BinOp) and n.op in ("+", "-", "*", "/"):
            a, b = self.ival(n.left, lo, hi, vars_), self.ival(n.right, lo, hi, vars_)
            if a is None or b is None:
                return None
            if n.op == "+":
                return (a[0] + b[0], a[1] + b[1])
            if n.op == "-":
                return (a[0] - b[1], a[1] - b[0])
            if n.op == "/" and b[0] <= 0 <= b[1]:
                return None
            ops = [a[0] * b[0], a[0] * b[1], a[1] * b[0], a[1] * b[1]] if n.op == "*" else \
                [a[0] / b[0], a[0] / b[1], a[1] / b[0], a[1] / b[1]]
            return (min(ops), max(ops))
        if isinstance(n, P.ListLit) and len(n.items) == 1:
            return self.ival(n.items[0], lo, hi, vars_)
        if isinstance(n, P.Call) and n.receiver is None:
            name = n.name
            if name in ("min", "max", "least", "greatest"):
                items = n.args[0].items if len(n.args) == 1 and isinstance(n.args[0], P.ListLit) else n.args
                vs = [self.ival(x, lo, hi, vars_) for x in items]
                if not vs or any(v is None for v in vs):
                    return None
                pick = min if name in ("min", "least") else max
                return (pick(v[0] for v in vs), pick(v[1] for v in vs))
            if name in _ROUND and len(n.args) >= 1:
                v = self.ival(n.args[0], lo, hi, vars_)
                return None if v is None else (math.floor(v[0]), math.ceil(v[1]))
            if name in _PERIOD_BACK and len(n.args) == 1:
                v = self.ival(n.args[0], lo, hi, vars_)
                return None if v is None else (v[0] - _PERIOD_BACK[name], v[1])
        return None

    def need(self, n, lo, hi, vars_) -> tuple[float, float]:
        v = self.ival(n, lo, hi, vars_)
        if v is None:
            raise _Unbounded(type(n).__name__)
        return v

    # ---- what a node reads --------------------------------------------------
    def visit(self, n, lo, hi, vars_) -> None:
        self.use(lo, hi)
        if n is None or isinstance(n, (P.Num, P.Str, P.Empty, P.Var)):
            return
        if isinstance(n, P.Ident):
            return self._ident(n.name, lo, hi)
        if isinstance(n, P.Assign):
            self.visit(n.value, lo, hi, vars_)
            vars_[n.name] = self.ival(n.value, lo, hi, vars_)
            return
        if isinstance(n, P.Call):
            return self._call(n, lo, hi, vars_)
        for v in vars(n).values():                       # Seq, BinOp, Unary, Pair, ListLit, RangeLit, Unit
            if isinstance(v, P.Node):
                self.visit(v, lo, hi, vars_)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, P.Node):
                        self.visit(x, lo, hi, vars_)

    def _ident(self, name: str, lo, hi) -> None:
        from backend.engine.wko5expr import evaluator as EV
        if name in ("today", "now") or name in EV.GOAL_IDENTS:
            self.today = True
        elif name in ("ctl", "atl", "tsb"):
            # tsb = yesterday's ctl − atl (Evaluator.ev_Ident): one more day
            c = {"ctl": self.ctlc, "atl": self.atlc}.get(name, max(self.ctlc, self.atlc))
            self.use(lo - WARMUP_TAU * c - (1 if name == "tsb" else 0), hi)
        elif name in EV.BUILTIN_EXPRS:
            node = self._builtin.get(name)
            if node is None:
                node = self._builtin[name] = P.parse(EV.BUILTIN_EXPRS[name])
            self.visit(node, lo, hi, {})
        elif name.endswith(EST_SETTING_SUFFIXES) and name not in EV.WORKOUT_METRICS:
            self.use(lo - SETTING_EST_DAYS, hi)

    def _call(self, n: P.Call, lo, hi, vars_) -> None:
        name, args = n.name, n.args
        if n.receiver is not None:
            self.visit(n.receiver, lo, hi, vars_)
        if name == "tl" and len(args) >= 2:
            c = self.need(args[1], lo, hi, vars_)[1]
            self.visit(args[1], lo, hi, vars_)
            return self.visit(args[0], lo - WARMUP_TAU * c, hi, vars_)
        if name == "shift" and len(args) >= 2:
            k = self.need(args[1], lo, hi, vars_)
            return self.visit(args[0], lo - max(k[1], 0), hi + max(-k[0], 0), vars_)
        if name == "athleterange" and len(args) >= 2:
            a = self.need(args[0], lo, hi, vars_)
            z = self.need(args[1], lo, hi, vars_)
            self.visit(args[0], lo, hi, vars_)
            self.visit(args[1], lo, hi, vars_)
            for x in args[2:]:
                self.visit(x, a[0], z[1], vars_)
            return
        if name in PD_LOOKBACK_FNS and len(args) >= 2:
            lb = self.need(args[1], lo, hi, vars_)[1]
            self.visit(args[1], lo, hi, vars_)
            return self.visit(args[0], lo - lb + 1, hi, vars_)
        if name == "drift_avg":
            self.weather = True
            self.use(lo - DRIFT_AVG_DAYS, hi)
        elif name == "drift":
            self.weather = True
        elif name in LEVEL_FNS:
            self.use(lo - LEVEL_DAYS, hi)
        for x in args:
            self.visit(x, lo, hi, vars_)


def _exprs(ch: dict) -> list[str]:
    return [s.get("expression") or "" for s in ch.get("series") or []]


def scope_of(ch: dict, ds, b: float, e: float, workout=None) -> Scope:
    """The days a chart over [b, e] reads (module doc). `workout`: a workout chart's activity."""
    b, e = int(math.floor(b)), int(math.floor(e))
    T = int(math.floor(ds.today))
    kind = ch.get("kind")
    if kind == "map" and workout is not None:
        d = int(math.floor(workout.day))
        return Scope(d, d, False, False)
    if kind not in EXPR_KINDS:
        return Scope.whole()
    if workout is not None:
        d = int(math.floor(workout.day))
        b, e = min(b, d), max(e, d)
    r = _Reach(ds, b, e)
    try:
        for x in _exprs(ch):
            if x.strip():
                r.visit(P.parse(x), b, e, {})
    except (_Unbounded, P.ParseError, RecursionError):
        return Scope.whole()
    today = r.today or r.hi >= T or bool(ch.get("race_refs"))
    return Scope(r.lo, r.hi, today, r.weather or bool(ch.get("drift_bars")))


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------

_SETTING_NAMES = ("runthr", "runmhr", "runftp", "runtpace", "weight", "bikethr", "bikeftp")


def _stamp(p) -> list:
    try:
        st = p.stat()
        return [p.name, st.st_size, st.st_mtime_ns]
    except (OSError, AttributeError, TypeError):
        return [getattr(p, "name", None), None, None]


def _global_parts(ds) -> list:
    """What every chart of this Dataset reads besides its activities."""
    from pathlib import Path
    from backend.engine.planning import plan_path
    from backend.engine.wko5expr.corrections import corrections_path
    try:
        athlete = [_stamp(p) for p in sorted(Path(ds.dir).glob("*.wko5athlete"))] if ds.dir else []
    except (OSError, TypeError):
        athlete = []
    cfg = ds.config.to_dict() if hasattr(ds.config, "to_dict") else repr(ds.config)
    try:
        from backend.engine.load_guard import manual_start
        pmc0 = manual_start()
    except Exception:                          # noqa: BLE001 — no settings store: none
        pmc0 = None
    return [athlete, _stamp(plan_path()), _stamp(corrections_path()), cfg, getattr(ds, "source", None),
            getattr(ds, "accept_watch_power", None), getattr(ds, "exclude_bad", None), pmc0]


def _file_stamp(ds, w) -> Any:
    store = getattr(ds, "_store", None)
    files = getattr(store, "files", None)
    if isinstance(files, dict):
        e = files.get(w.entry.file) or {}
        return e.get("sha1") or e.get("stamp")
    try:
        from backend.engine.wko5expr.dataset import _file_stamp as fs
        return fs(ds.dir / w.entry.file) if ds.dir else None
    except Exception:                          # noqa: BLE001 — no file: the rest of the row still counts
        return None


def _digest(ds, w) -> str:
    sig = getattr(ds, "_settings_sig", None)
    try:
        s = sig(w) if sig is not None else ""
    except Exception:                          # noqa: BLE001
        s = ""
    src = (getattr(ds, "_power_src", None) or {}).get(w.idx)
    blocked = w.entry.file in (getattr(ds, "_power_blocked", None) or ())
    row = [w.entry.file, _file_stamp(ds, w), round(float(w.day), 6), w.sport, w.sport_type,
           list(w.tags or []), getattr(w, "platform", None), w.metrics, s, src, blocked]
    return hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


_LOCK = threading.Lock()


def _rows(ds, glob: str):
    """(days, digests) of every activity, memoised on the Dataset per global inputs."""
    key = ("chartscope", "rows")
    hit = ds.memo.get(key)
    if hit is not None and hit[0] == glob:
        return hit[1], hit[2]
    days = [int(math.floor(w.day)) for w in ds.workouts]
    digs = [_digest(ds, w) for w in ds.workouts]
    with _LOCK:
        ds.memo[key] = (glob, days, digs)
        ds.memo[("chartscope", "win")] = {}
    return days, digs


def _sessions(ds, lo: Optional[int], hi: Optional[int]) -> list:
    """The stored CP-test sessions and done interval sessions of these days (the review card's
    test / interval judgement reads them)."""
    from backend.engine.plan_store import done_plan, test_sessions
    from backend.engine.wko5expr.dataset import date_to_day

    def inside(day) -> bool:
        return day is not None and (lo is None or day >= lo) and (hi is None or day <= hi)

    def wday(i):
        try:
            return int(math.floor(ds.workouts[int(i)].day))
        except (IndexError, TypeError, ValueError):
            return None

    def sday(s):
        try:
            return int(date_to_day(dt.date.fromisoformat(str(s.get("day"))[:10])))
        except (TypeError, ValueError):
            return None
    out = sorted([s["uid"], s["state"], s.get("day") or "", (s.get("done_by") or {}).get("index") or -1,
                  s.get("protocol") or ""] for s in test_sessions()
                 if inside(sday(s)) or inside(wday((s.get("done_by") or {}).get("index"))))
    out += sorted([i, r.get("uid") or "", r.get("title") or "", r.get("variant_key") or "",
                   json.dumps(r.get("steps"), sort_keys=True, default=str)]
                  for i, r in done_plan().items() if inside(wday(i)))
    return out


def _weather_stamp():
    try:
        from backend.engine import route_weather as RW
        from backend.engine.routes import home as routes_home
        return _stamp(routes_home() / RW.ACTIVITY_WX_FILE)
    except Exception:                          # noqa: BLE001 — no routes module: no archive
        return None


def fingerprint(ds, scope: Scope) -> str:
    """The data part of a chart's key: global inputs + the activities in scope (module doc)."""
    glob = json.dumps(_global_parts(ds), sort_keys=True, default=str)
    days, digs = _rows(ds, glob)
    lo, hi = scope.lo, scope.hi
    i0 = 0 if lo is None else bisect.bisect_left(days, lo)
    i1 = len(days) if hi is None else bisect.bisect_right(days, hi)
    wins = ds.memo.setdefault(("chartscope", "win"), {})
    win = wins.get((i0, i1))
    if win is None:
        h = hashlib.sha1()
        for d in digs[i0:i1]:
            h.update(d.encode())
        win = wins[(i0, i1)] = f"{i1 - i0}:{h.hexdigest()}"
    ends = []
    for d in (lo, hi):
        if d is None:
            continue
        for n in _SETTING_NAMES:
            try:
                ends.append(ds.setting(n, float(d)))
            except Exception:                  # noqa: BLE001 — a Dataset without that setting
                ends.append(None)
    parts = [glob, scope.as_json(), win, ends, _sessions(ds, lo, hi),
             ds.today if scope.today else None, _weather_stamp() if (scope.weather or scope.full) else None]
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------
# code signature
# ---------------------------------------------------------------------------

# the renderer of each panel kind ("module:attribute"); their closure is what changes them
KIND_ROOTS = {
    "athlete": ["backend.engine.wko5expr.render:render_chart", "backend.engine.sport_map:make_filter"],
    "workout": ["backend.engine.wko5expr.render:render_chart", "backend.engine.workout_review:_has"],
    "map": ["backend.engine.wko5expr.render:render_map"],
    "review": ["backend.engine.workout_review:review"],
    "activity": ["backend.engine.panels.activity_charts:render"],
    "periodzones": ["backend.engine.panels.period_zones:render"],
    "climbvam": ["backend.engine.panels.climb_vam:render"],
    "polecompare": ["backend.engine.panels.pole_compare:render"],
    "zones": ["backend.engine.zones:zone_table", "backend.engine.zones:training_targets",
              "backend.engine.thresholds:estimate"],
    "targets": ["backend.engine.zones:zone_table", "backend.engine.zones:training_targets",
                "backend.engine.thresholds:estimate"],
    "z5gate": ["backend.api.wko5views:z5gate_panel"],
}
# post-processing the chart endpoint applies by chart key (wko5views.chart)
POST_ROOTS = {
    "period": ["backend.engine.wko5expr.periods:chart_period", "backend.engine.wko5expr.periods:with_period",
               "backend.engine.wko5expr.periods:bucket_start", "backend.engine.wko5expr.periods:buckets",
               "backend.engine.wko5expr.periods:min_days", "backend.engine.wko5expr.periods:period_locked"],
    "window": ["backend.engine.wko5expr.recentbests:apply_window", "backend.engine.wko5expr.recentbests:summarize"],
    "basis": ["backend.engine.wko5expr.basis:apply_basis", "backend.engine.wko5expr.basis:no_power_note"],
    "variants": ["backend.engine.wko5expr.variants:apply_variant"],
    "race_refs": ["backend.engine.panels.race_refs:apply"],
    "drift_bars": ["backend.engine.panels.drift_bars:apply"],
}
_CODE: dict = {}
_CODE_LOCK = threading.Lock()


def _resolve(spec: str):
    mod, attr = spec.split(":")
    try:
        return getattr(importlib.import_module(mod), attr)
    except (ImportError, AttributeError):
        return None


def _calls(ch: dict) -> set[str]:
    from backend.engine.wko5expr import evaluator as EV
    out: set[str] = set()
    todo = []
    for x in _exprs(ch):
        try:
            todo.append(P.parse(x))
        except P.ParseError:
            continue
    seen = set()
    while todo:
        node = todo.pop()
        for n in P.walk(node):
            if isinstance(n, P.Call):
                out.add(n.name)
            elif isinstance(n, P.Ident) and n.name in EV.BUILTIN_EXPRS and n.name not in seen:
                seen.add(n.name)
                todo.append(P.parse(EV.BUILTIN_EXPRS[n.name]))
    return out


def _own_code(fn) -> str:
    """A function's own compiled code (nested code included), without what it calls."""
    from backend.engine import codehash as CH
    h = hashlib.sha1()
    for p in CH._code_parts(fn.__code__):
        h.update(p if isinstance(p, bytes) else CH._stable(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def code_signature(ch: dict, ds, glue: Iterable = ()) -> str:
    """The code part of a chart's key (module doc). `glue`: endpoint functions whose own code
    (not what they call) shapes the JSON. Memoised per (kind, calls, post-processing, Dataset
    class, glue) for the life of the process — it describes the code this process runs."""
    from backend.engine import codehash as CH
    from backend.engine.wko5expr import render_cache as RC
    from backend.engine.wko5expr.evaluator import Evaluator
    kind = ch.get("kind")
    calls = tuple(sorted(_calls(ch))) if kind in EXPR_KINDS else ()
    post = tuple(k for k in POST_ROOTS if ch.get(k))
    glue = tuple(glue)
    dstype = type(ds)
    memo_key = (kind, calls, post, dstype, tuple(id(g) for g in glue), RC.CACHE_VERSION)
    hit = _CODE.get(memo_key)
    if hit is not None:
        return hit
    with _CODE_LOCK:                           # one computation at a time (the first page load asks 28)
        hit = _CODE.get(memo_key)
        if hit is not None:
            return hit
        parts = []
        # the Dataset build: metrics, thresholds, estimates (all charts read them)
        build = [c.__init__ for c in dstype.__mro__ if "__init__" in vars(c) and CH._ours(c)]
        parts.append(("build", CH.code_hash(*build, context=[dstype])))
        if kind in EXPR_KINDS:
            disp = [Evaluator.ev, Evaluator.evaluate] + [v for k, v in vars(Evaluator).items() if k.startswith("ev_")]
            parts.append(("eval", CH.code_hash(*disp, context=[Evaluator, dstype])))
            for c in calls:
                fn = getattr(Evaluator, f"fn_{c}", None)
                if fn is not None:
                    parts.append((c, CH.code_hash(fn, context=[Evaluator, dstype])))
        roots = [r for r in (_resolve(s) for s in KIND_ROOTS.get(kind, [])) if r is not None]
        if roots:
            parts.append(("kind", CH.code_hash(*roots, context=[dstype])))
        elif kind not in KIND_ROOTS:
            parts.append(("unknown kind", RC.code_signature()))     # all engine files (conservative)
        for p in post:
            rs = [r for r in (_resolve(s) for s in POST_ROOTS[p]) if r is not None]
            parts.append((p, CH.code_hash(*rs, context=[dstype])))
        parts.append(("glue", [_own_code(g) for g in glue]))
        parts.append(("version", RC.CACHE_VERSION))
        out = hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()
        _CODE[memo_key] = out
        return out
