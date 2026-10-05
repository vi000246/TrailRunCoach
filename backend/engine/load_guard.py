"""
Load-progression guardrails shared by status, quality_gate, adapt, b2b and the
planner (SP-63; docs/research/ctl-ramp-calibration.md §4).

CTL ramp — relative lines (one copy; status / quality_gate / adapt / b2b used to
carry their own 5 / 8):

    Δ = CTL(d) − CTL(d − 7), c = CTL(d − 7)
    注意 (watch: threshold only)  Δ ≥ max(3, 10 % × c)
    擋   (block: no interval)     Δ ≥ min(10, max(5, 15 % × c))

Why relative: every TSS-scale change (hrTSS vs power TSS, hikes counted or not,
an LTHR estimate that is off) multiplies CTL and the ramp by the same factor;
only Δ / c survives it. 10 % / 15 % are Friel's 5–8 (suggested) / 10 (ceiling)
over a typical CTL 60–80, the lower end for running (推估). The floors 3 / 5
keep a small CTL from blocking on noise; the cap 10 is Friel's 「> 10 only for
a week」 ceiling, so a large CTL never gets a wider line than that (推估).
Friel, https://joefrieltraining.com/the-ctl-ramp-rate/ (coach).

Who reads which line:
  * status.i_fitness: watch → WATCH, block → BAD
  * quality_gate.guard: watch → threshold-only (SUB), block → no interval
  * adapt rule E: the block line (it was 8 = the old block line since
    2026-10-01; before that 7 — the 7 is not carried over)
  * b2b (no B2B weekend, no TSB exemption): the block line (was 8, the old block)

Startup: a CTL that starts at 0 on the first day with TSS shows, for weeks, a
ramp that is only the PMC filling up (≈ 12 % of CTL still at day 42 with steady
training). The PMC itself (SP-68: the Evaluator builtins ctl / atl / tsb, so the
charts and this guardrail read the same numbers) starts from pmc_start(): the
user's manual CTL / ATL at a date, else CTL = ATL = the mean daily TSS of the first
SEED_DAYS (Coggan gives CTL / ATL a starting value instead of 0), else 0. The ramp
is not checked during the first STARTUP_DAYS of an automatic start (both 推估: the
seed is final once its 4 weeks are in), nor in the first MANUAL_STARTUP_DAYS after
a manual one. The weekly volume step still runs in that window.

Weekly volume step: RUNNING time only (road + trail, sport "run"; Nielsen 2014
and Damsted 2019 measured running), last week against
max(the week before, the mean of the 4 weeks before) — the planner's own
reference (overview.week_plan / projection.week_hours: max(4-week mean, last
week)), so a week back to normal after a recovery week is not a 「spike」. Only normal
weeks make the base (SP-73, owner 2026-10-05): a week touching a 減量期, race week, post-race
恢復期 or 轉換期 (STEP_SKIP_KINDS, planning.phase_days) is left out and the most recent normal
weeks before it are used instead (up to STEP_LOOKBACK_WEEKS back), so the second week after a
transition is not measured against the transition. The planner's +10 % cap reads the same
normal weeks (normal_ref).
> 20 % block, 10–20 % hold (unchanged classes). Exempt: the week after a short
unplanned break — SHORT_BREAK_MIN–5 days without a run (shorter than a re-entry
block, reentry.MIN_BREAK) touching the week before it — since the break pulled
that week and the 4-week mean down, coming back to normal reads as a spike
(owner 2026-10-04; ≥ 6 days is reentry.py's block). The week note says so.
Only UNPLANNED days count toward SHORT_BREAK_MIN (owner 2026-10-05): days of the user's
own 不排課日期 or 休息日 (engine/blackouts.py, both kinds) and the weekdays not ticked as
可練日 in 課表偏好 (plan_prefs.days: a Fri–Sun runner's Mon–Thu gap is their week, not a
break) are a chosen rest, so a gap that is planned, or whose unplanned part is
< SHORT_BREAK_MIN days, is not exempt.

Weekly CTL goal of the planner: base max(2, 5 % CTL), specific max(2.5, 7 % CTL)
(推估: equal to the old +3 / +4 at CTL 55–60; Palladino writes 2–5 %).
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Optional, Sequence

import numpy as np

from backend.engine.reentry import MIN_BREAK
from backend.i18n import _

# ---- CTL ramp ---------------------------------------------------------------
WATCH_PCT, WATCH_MIN = 0.10, 3.0               # 推估 (Friel 5–8 over CTL 60–80, the lower end)
BLOCK_PCT, BLOCK_MIN, BLOCK_MAX = 0.15, 5.0, 10.0   # 推估; BLOCK_MAX = Friel's ceiling 10 (coach)
WATCH, BLOCK = "watch", "block"

# ---- startup -----------------------------------------------------------------
SEED_DAYS = 28                 # 推估: the seed = mean daily TSS of the first 4 weeks (Coggan: give a start value)
STARTUP_DAYS = 28              # 推估: no ramp check before the seed window is complete

# ---- weekly volume step --------------------------------------------------------
STEP_HOLD, STEP_BLOCK = 0.10, 0.20     # > 20 % block: Nielsen 2014, Damsted 2019 (peer-reviewed); 10–20 % hold 推估
STEP_AVG_WEEKS = 4
STEP_SPORTS = ("run",)                 # road + trail runs (sport group "run")
SHORT_BREAK_MIN = 3                    # 推估: ≥ 3 days without a run is a break (routine rest = 1–2 days)
STEP_SKIP_KINDS = ("taper", "event", "recovery", "transition")   # not a baseline week (SP-73)
STEP_LOOKBACK_WEEKS = 26               # 推估: covers taper + race + 恢復期 + a 4-week 轉換期 + 4 normal weeks

# ---- planner's weekly CTL goal -------------------------------------------------
GOAL = {"base": (0.05, 2.0), "specific": (0.07, 2.5)}   # (share of CTL, floor in points) 推估


def _base(ctl_prev: Optional[float]) -> float:
    """CTL₋₇ for the lines; unknown / negative → 0 (the floors, the strict side)."""
    try:
        c = float(ctl_prev)
    except (TypeError, ValueError):
        return 0.0
    return c if c > 0 and c == c else 0.0


def watch_line(ctl_prev: Optional[float]) -> float:
    return max(WATCH_MIN, WATCH_PCT * _base(ctl_prev))


def block_line(ctl_prev: Optional[float]) -> float:
    return min(BLOCK_MAX, max(BLOCK_MIN, BLOCK_PCT * _base(ctl_prev)))


def block_term(ctl_prev: Optional[float]) -> str:
    """Which term sets the block line: "floor" (5), "pct" (15 %) or "cap" (10)."""
    p = BLOCK_PCT * _base(ctl_prev)
    return "floor" if p <= BLOCK_MIN else "cap" if p >= BLOCK_MAX else "pct"


def ramp_level(ramp: Optional[float], ctl_prev: Optional[float]) -> Optional[str]:
    """BLOCK / WATCH / None for a 7-day CTL change against CTL₋₇. A missing
    ramp never fires; a missing CTL₋₇ uses the floors (3 / 5)."""
    if ramp is None:
        return None
    if ramp >= block_line(ctl_prev):
        return BLOCK
    if ramp >= watch_line(ctl_prev):
        return WATCH
    return None


def ramp_text(ramp: float, ctl_prev: Optional[float], level: str) -> str:
    """「CTL 每週 +6.2（≥ 5.3＝CTL 53 的 10%）」 — the line that fired and where it comes from."""
    c = _base(ctl_prev)
    if level == BLOCK:
        line, term = block_line(c), block_term(c)
        how = {"floor": _("下限 {v:.0f}", v=BLOCK_MIN), "cap": _("上限 {v:.0f}", v=BLOCK_MAX),
               "pct": _("CTL {c:.0f} 的 {pct:.0%}", c=c, pct=BLOCK_PCT)}[term]
    else:
        line = watch_line(c)
        how = _("下限 {v:.0f}", v=WATCH_MIN) if WATCH_PCT * c <= WATCH_MIN else _("CTL {c:.0f} 的 {pct:.0%}", c=c, pct=WATCH_PCT)
    return _("CTL 每週 +{ramp:.1f}（≥ {line:.1f}＝{how}）", ramp=ramp, line=line, how=how)


# ---- PMC start values (SP-68) ---------------------------------------------------------
# One source order for the starting CTL / ATL, read by the PMC itself (Evaluator builtins
# ctl / atl / tsb: charts, overview, status, week_plan's TSB < −30, adapt rule E, b2b,
# projection) and so by this guardrail:
#   1. manual — the user's CTL / ATL at the start of a date (設定 → 閾值, user_settings
#      PMC_START_KEY). The series restarts there; days before it keep 2. / 3.
#   2. auto   — SP-63: CTL = ATL = the mean daily TSS of the first SEED_DAYS from the first
#      day with TSS (only days ≤ today count, so it is final once its 4 weeks are in)
#   3. none   — no TSS yet: 0 (WKO5 tl())
PMC_START_KEY = "athlete.pmc_start"     # user_settings: {date: ISO, ctl, atl} | None
MANUAL, AUTO, NONE = "manual", "auto", "none"
START_MAX = 300.0                       # a CTL / ATL above this is a typo, not a start value
MANUAL_STARTUP_DAYS = 7                 # a manual start: no ramp until CTL₋₇ is on / after its date


def parse_manual(v) -> Optional[dict]:
    """{date, ctl, atl} from the stored setting; None when unset or malformed."""
    if not isinstance(v, dict):
        return None
    try:
        d = str(v.get("date") or "")
        dt.date.fromisoformat(d)
        c, a = float(v.get("ctl")), float(v.get("atl"))
    except (TypeError, ValueError):
        return None
    if not (0.0 <= c <= START_MAX and 0.0 <= a <= START_MAX):
        return None
    return {"date": d, "ctl": c, "atl": a}


def manual_start(user_id: int = 1) -> Optional[dict]:
    """The stored manual start (a synchronous read like plan_prefs.load); None = not set."""
    from backend.engine.wko5expr.datasource import read_setting
    return parse_manual(read_setting(PMC_START_KEY, None, user_id))


def first_load_index(x: Sequence[float]) -> Optional[int]:
    nz = np.flatnonzero(np.asarray(x, dtype=float) > 0)
    return int(nz[0]) if len(nz) else None


def auto_seed(x: Sequence[float], upto: Optional[int] = None) -> tuple[Optional[int], float, int]:
    """(index of the first day with TSS, the seed, days in it): the mean daily TSS of the
    first SEED_DAYS from that day, only days ≤ `upto` (the index of today) counting."""
    x = np.asarray(x, dtype=float)
    d0 = first_load_index(x)
    if d0 is None or (upto is not None and d0 > upto):
        return None, 0.0, 0
    hi = d0 + SEED_DAYS
    if upto is not None:
        hi = min(hi, upto + 1)
    hi = max(hi, d0 + 1)
    return d0, float(np.mean(x[d0:hi])), hi - d0


def pmc_start(x: Sequence[float], start_day: int, today: int, manual: Optional[dict] = None) -> dict:
    """The start values for a daily TSS series `x` whose index 0 is day `start_day`
    (`today`: day number). {"source": manual | auto | none, "day" (day number the values
    apply at, the start of that day; None for none), "ctl", "atl", "auto": {"day", "seed",
    "days"} (the automatic seed, also when a manual start wins — the settings card shows it)}.
    A manual start dated after today is ignored."""
    from backend.engine.wko5expr.dataset import date_to_day
    i_today = int(today) - int(start_day)
    d0, seed, n = auto_seed(x, upto=i_today)
    auto = {"day": None if d0 is None else int(start_day) + d0, "seed": seed, "days": n}
    m = parse_manual(manual)
    if m is not None:
        md = int(date_to_day(dt.date.fromisoformat(m["date"])))
        if md <= int(today):
            return {"source": MANUAL, "day": md, "ctl": m["ctl"], "atl": m["atl"], "date": m["date"], "auto": auto}
    if d0 is None:
        return {"source": NONE, "day": None, "ctl": 0.0, "atl": 0.0, "date": None, "auto": auto}
    return {"source": AUTO, "day": auto["day"], "ctl": seed, "atl": seed, "date": None, "auto": auto}


def recur(x: Sequence[float], const: float, start_day: int, at_day: Optional[int], v0: float,
          before: Optional[tuple[int, float]] = None) -> np.ndarray:
    """tl()'s v += (x − v)/const over `x` (index 0 = day `start_day`), with v = v0 at the
    start of day `at_day` (None = WKO5's v = 0 before the first input). `before`: an earlier
    (day, v0) start the series runs from until `at_day` (a manual start keeps the automatic
    seed before its date). A start before the series decays over the rest days in between."""
    x = np.asarray(x, dtype=float)
    out = np.empty(len(x))
    resets = {}
    v = 0.0
    for st in ([before] if before and before[0] is not None else []) + ([(at_day, v0)] if at_day is not None else []):
        k = int(st[0]) - int(start_day)
        if k < 0:
            v = st[1] * (1.0 - 1.0 / const) ** (-k)
        else:
            resets[k] = st[1]
    for i, xv in enumerate(x):
        if i in resets:
            v = resets[i]
        v = v + (xv - v) / const
        out[i] = v
    return out


def pmc_series(x: Sequence[float], const: float, start_day: int, start: dict, which: str) -> np.ndarray:
    """The PMC's CTL (`which` = "ctl") or ATL ("atl") from pmc_start()'s `start`: the
    automatic seed before a manual start's date, then the manual value."""
    a = start.get("auto") or {}
    before = (a.get("day"), a.get("seed", 0.0)) if start.get("source") == MANUAL else None
    if before is not None and (before[0] is None or before[0] >= (start.get("day") or 0)):
        before = None
    return recur(x, const, start_day, start.get("day"), start.get(which, 0.0), before)


def guard_ramp(ctl_values: Sequence[float], start_day: int, today: int, start: Optional[dict]) -> dict:
    """The guardrail ramp on `today` from the PMC CTL series (Evaluator "ctl": Daily starting
    at `start_day`, already started at `start` = pmc_start()). {"ramp", "ctl_prev", "ctl_now",
    "startup" (True = no ramp check: the first STARTUP_DAYS of an automatic start, the first
    MANUAL_STARTUP_DAYS after a manual one), "day_n" (days since the start), "seed", "source",
    "level"}."""
    s = np.nan_to_num(np.asarray(ctl_values, dtype=float))
    st = start or {"source": NONE, "day": None}
    i = int(today) - int(start_day)
    out = {"ramp": None, "ctl_prev": None, "ctl_now": None, "startup": False, "day_n": None,
           "seed": st.get("ctl", 0.0) if st.get("source") == AUTO else 0.0, "source": st.get("source"), "level": None}
    if i < 0 or i >= len(s):
        return out
    if st.get("day") is None or int(st["day"]) > int(today):
        out["startup"] = True
        return out
    n = int(today) - int(st["day"])
    out.update(day_n=n, ctl_now=float(s[i]))
    if i - 7 >= 0:
        out["ctl_prev"] = float(s[i - 7])
        out["ramp"] = float(s[i] - s[i - 7])
    if n < (MANUAL_STARTUP_DAYS if st.get("source") == MANUAL else STARTUP_DAYS):
        out["startup"] = True
        return out
    out["level"] = ramp_level(out["ramp"], out["ctl_prev"])
    return out


# ---- weekly volume step -----------------------------------------------------------

def step_base(prev_weeks: Sequence[float]) -> Optional[float]:
    """max(the week before, the mean of the STEP_AVG_WEEKS before), oldest first; None when 0."""
    xs = [float(h or 0.0) for h in prev_weeks][-STEP_AVG_WEEKS:]
    if not xs:
        return None
    b = max(xs[-1], statistics.mean(xs))
    return b if b > 0 else None


def skip_mondays(plan, mondays: Sequence[dt.date]) -> set[dt.date]:
    """The Mondays whose week (Mon–Sun) touches a STEP_SKIP_KINDS phase of `plan` (auto or
    manual); empty without a plan or on any plan error."""
    ms = sorted(mondays)
    if plan is None or not ms:
        return set()
    try:
        from backend.engine.planning import phase_days
        days = phase_days(plan, ms[0], ms[-1] + dt.timedelta(days=6), STEP_SKIP_KINDS)
    except Exception:                       # noqa: BLE001 — the guardrail must still work
        return set()
    return {m for m in ms if any(m + dt.timedelta(days=k) in days for k in range(7))}


def normal_weeks(weeks: Sequence[tuple], skip=()) -> list[float]:
    """The hours of the last STEP_AVG_WEEKS weeks not in `skip` (Mondays), oldest first;
    `weeks` = [(monday, hours)] oldest first."""
    sk = set(skip or ())
    return [float(h or 0.0) for m, h in weeks if m not in sk][-STEP_AVG_WEEKS:]


def normal_ref(weeks: Sequence[tuple], skip=()) -> Optional[float]:
    """The planner's volume reference on normal weeks: step_base(normal_weeks); None when none."""
    return step_base(normal_weeks(weeks, skip))


def volume_step(last: float, prev_weeks: Sequence[float]) -> tuple[Optional[float], Optional[float]]:
    """(step, base): last week's running time against step_base(prev_weeks)."""
    b = step_base(prev_weeks)
    return (None, None) if b is None else ((float(last or 0.0) - b) / b, b)


def short_break(run_days: Sequence[int], lo: int, hi: int,
                planned: Sequence[int] = ()) -> Optional[tuple[int, int, int]]:
    """(first, last, planned) of the latest short unplanned break — fewer than MIN_BREAK days
    without a run (no re-entry block), between two runs, with ≥ SHORT_BREAK_MIN of them NOT
    `planned` (day indices of the user's own 不排課日期 / 休息日 / unticked 可練日: a rest the
    user chose; owner 2026-10-05) — with a day in [lo, hi] (day indices, the week before the one measured);
    `planned` in the result = how many of its days were planned. Counted, not contiguous:
    2 planned days inside a 5-day gap leave 3 unplanned → exempt. None without one."""
    ds = sorted({int(d) for d in run_days})
    pl = {int(d) for d in planned}
    out = None
    for a, b in zip(ds, ds[1:]):
        n = b - a - 1
        if n < MIN_BREAK and a + 1 <= hi and b - 1 >= lo:
            k = sum(1 for d in range(a + 1, b) if d in pl)
            if n - k >= SHORT_BREAK_MIN:
                out = (a + 1, b - 1, k)
    return out


# ---- planner -------------------------------------------------------------------------

def ramp_goal(kind: str, ctl: Optional[float]) -> Optional[float]:
    """The planner's weekly CTL goal (points) for a base / specific week; None otherwise."""
    g = GOAL.get(kind)
    if g is None:
        return None
    return max(g[1], g[0] * _base(ctl))
