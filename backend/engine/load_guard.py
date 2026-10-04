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

Startup: CTL starts at 0 on the first day with TSS, so the first weeks show a
ramp that is only the PMC filling up (≈ 12 % of CTL still at day 42 with steady
training). The guardrail CTL is seeded with the mean daily TSS of the first
SEED_DAYS (Coggan gives CTL / ATL a starting value instead of 0) and the ramp is
not checked during the first STARTUP_DAYS (both 推估: the seed is final once its
4 weeks are in). The weekly volume step still runs in that window.

Weekly volume step: RUNNING time only (road + trail, sport "run"; Nielsen 2014
and Damsted 2019 measured running), last week against
max(the week before, the mean of the 4 weeks before) — the planner's own
reference (overview.week_plan / projection.week_hours: max(4-week mean, last
week)), so a week back to normal after a recovery week is not a 「spike」.
> 20 % block, 10–20 % hold (unchanged classes).

Weekly CTL goal of the planner: base max(2, 5 % CTL), specific max(2.5, 7 % CTL)
(推估: equal to the old +3 / +4 at CTL 55–60; Palladino writes 2–5 %).
"""
from __future__ import annotations

import statistics
from typing import Optional, Sequence

import numpy as np

# ---- CTL ramp ---------------------------------------------------------------
WATCH_PCT, WATCH_MIN = 0.10, 3.0               # 推估 (Friel 5–8 over CTL 60–80, the lower end)
BLOCK_PCT, BLOCK_MIN, BLOCK_MAX = 0.15, 5.0, 10.0   # 推估; BLOCK_MAX = Friel's ceiling 10 (coach)
SRC_RAMP = ("CTL ramp 相對門檻：注意 ≥ max(3, CTL 的 10%)、擋 ≥ min(10, max(5, CTL 的 15%))"
            "（Friel 每週 5–8 適合多數人、10 是上限，換算成比例；推估）")
WATCH, BLOCK = "watch", "block"

# ---- startup -----------------------------------------------------------------
SEED_DAYS = 28                 # 推估: the seed = mean daily TSS of the first 4 weeks (Coggan: give a start value)
STARTUP_DAYS = 28              # 推估: no ramp check before the seed window is complete

# ---- weekly volume step --------------------------------------------------------
STEP_HOLD, STEP_BLOCK = 0.10, 0.20     # > 20 % block: Nielsen 2014, Damsted 2019 (peer-reviewed); 10–20 % hold 推估
STEP_AVG_WEEKS = 4
STEP_SPORTS = ("run",)                 # road + trail runs (sport group "run")

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
        how = {"floor": f"下限 {BLOCK_MIN:.0f}", "cap": f"上限 {BLOCK_MAX:.0f}",
               "pct": f"CTL {c:.0f} 的 {BLOCK_PCT:.0%}"}[term]
    else:
        line = watch_line(c)
        how = f"下限 {WATCH_MIN:.0f}" if WATCH_PCT * c <= WATCH_MIN else f"CTL {c:.0f} 的 {WATCH_PCT:.0%}"
    return f"CTL 每週 +{ramp:.1f}（≥ {line:.1f}＝{how}）"


# ---- startup seed ----------------------------------------------------------------

def daily_load(ctl_values: Sequence[float], const: float) -> np.ndarray:
    """The daily TSS behind a CTL series that starts at 0 (Evaluator._tl: v += (x − v)/c,
    v = 0 before the first day): x_d = c·v_d − (c − 1)·v_{d−1}."""
    v = np.nan_to_num(np.asarray(ctl_values, dtype=float))
    prev = np.concatenate(([0.0], v[:-1]))
    x = const * v - (const - 1.0) * prev
    x[np.abs(x) < 1e-6] = 0.0
    return x


def first_load_index(x: Sequence[float]) -> Optional[int]:
    nz = np.flatnonzero(np.asarray(x, dtype=float) > 0)
    return int(nz[0]) if len(nz) else None


def seeded(x: Sequence[float], const: float, upto: Optional[int] = None) -> tuple[np.ndarray, Optional[int], float]:
    """(load series seeded at the first day with TSS, that index, the seed). The seed
    is the mean daily TSS of the first SEED_DAYS from that day (only days ≤ `upto`,
    the series index of today, count). Before that day the series is 0."""
    x = np.asarray(x, dtype=float)
    out = np.zeros(len(x))
    d0 = first_load_index(x)
    if d0 is None:
        return out, None, 0.0
    hi = d0 + SEED_DAYS
    if upto is not None:
        hi = min(hi, upto + 1)
    seed = float(np.mean(x[d0:max(hi, d0 + 1)]))
    v = seed
    for i in range(d0, len(x)):
        v = v + (x[i] - v) / const
        out[i] = v
    return out, d0, seed


def guard_ramp(ctl_values: Sequence[float], start_day: int, today: int, const: float) -> dict:
    """The guardrail ramp on `today` from a PMC CTL series (Evaluator "ctl": Daily
    starting at `start_day` with v = 0 before it). {"ramp", "ctl_prev", "ctl_now"
    (seeded CTL), "startup" (True = in the first STARTUP_DAYS: no ramp check),
    "day_n" (days since the first TSS), "seed", "level"}."""
    x = daily_load(ctl_values, const)
    i = int(today) - int(start_day)
    out = {"ramp": None, "ctl_prev": None, "ctl_now": None, "startup": False, "day_n": None, "seed": 0.0,
           "level": None}
    if i < 0 or i >= len(x):
        return out
    s, d0, seed = seeded(x, const, upto=i)
    if d0 is None or d0 > i:
        out["startup"] = True
        return out
    n = i - d0
    out.update(day_n=n, seed=seed, ctl_now=float(s[i]))
    if i - 7 >= 0:
        out["ctl_prev"] = float(s[i - 7])
        out["ramp"] = float(s[i] - s[i - 7])
    if n < STARTUP_DAYS:
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


def volume_step(last: float, prev_weeks: Sequence[float]) -> tuple[Optional[float], Optional[float]]:
    """(step, base): last week's running time against step_base(prev_weeks)."""
    b = step_base(prev_weeks)
    return (None, None) if b is None else ((float(last or 0.0) - b) / b, b)


# ---- planner -------------------------------------------------------------------------

def ramp_goal(kind: str, ctl: Optional[float]) -> Optional[float]:
    """The planner's weekly CTL goal (points) for a base / specific week; None otherwise."""
    g = GOAL.get(kind)
    if g is None:
        return None
    return max(g[1], g[0] * _base(ctl))
