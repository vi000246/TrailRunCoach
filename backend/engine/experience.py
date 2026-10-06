"""
跑步經驗問卷 (SP-290; docs/research/cold-start.md §2.1, §3 G3, §4.2, §5 T3).

The first-run 精靈 (static/setup_wizard.js, SP-211) asked sex, age and weight only; what a new
runner can tell about their running was never asked although it is usable: self-reported running
volume has no mean bias (Dideriksen 2016 [C1]: mean difference 1.86 %, limits of agreement −28 % …
+40 %), and a race result plus the weekly volume predicts a finish time (Vickers & Vertosick 2016
[C2]). Four questions, every one skippable, on the same 精靈 — same 「稍後再說」 and 7-day reminder
as SP-211 (athlete_profile.setup_remind), no second wizard:

  1. 最近 4 週，平均一週跑步或健行幾次、每次大約多久？   runs_per_week, minutes_per_run
  2. 最近 4 週最長的一次是多久？                         longest_min
  3. 現在能不能連續跑 30 分鐘不停？                      can_run_30 (True / False / None = 不答)
  4. 最近一年有沒有比賽成績？                            engine/race_results.py (shared, SP-293 / SP-276)

Stored in the settings store `athlete.experience` {runs_per_week, minutes_per_run, longest_min,
can_run_30, at} (`at` = when it was saved: answered, even with every field blank); 設定 → 個人資料
edits it. The self-reported volume is used as entered — NOT discounted (owner decision 2026-10-06;
the research doc's × 0.75 is not applied).

Who is not asked: a runner whose data already has HISTORY_WEEKS complete weeks in a row with
≥ HISTORY_RUNS days of running or hiking each (the Zone 3 gate's consistency numbers,
quality_gate.Z3_WEEKS_NEED / Z3_RUNS_PER_WEEK) — their own data says more than the answers.
A runner who changed watch / platform is better served by importing the old data (the 精靈 says so).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Optional

KEY = "athlete.experience"
FIELDS = ("runs_per_week", "minutes_per_run", "longest_min", "can_run_30")
LIMITS = {"runs_per_week": (0, 14), "minutes_per_run": (5, 600), "longest_min": (5, 1440)}
HISTORY_WEEKS = 4          # = quality_gate.Z3_WEEKS_NEED (推估 there): 4 complete weeks …
HISTORY_RUNS = 3           # = quality_gate.Z3_RUNS_PER_WEEK (推估 there): … of ≥ 3 days with a run / hike


def validate(v: Any) -> None:
    """The settings value: None (never answered) or the answer object."""
    if v is None:
        return
    if not isinstance(v, dict):
        raise ValueError("experience must be an object or null")
    extra = set(v) - set(FIELDS) - {"at"}
    if extra:
        raise ValueError(f"unknown experience fields {sorted(extra)}")
    for k, (lo, hi) in LIMITS.items():
        x = v.get(k)
        if x is not None and (not isinstance(x, int) or isinstance(x, bool) or not lo <= x <= hi):
            raise ValueError(f"{k} must be a whole number {lo}–{hi} or empty")
    if v.get("can_run_30") not in (None, True, False):
        raise ValueError("can_run_30 must be true, false or empty")
    if not isinstance(v.get("at"), str) or not v["at"]:
        raise ValueError("experience needs the time it was answered (at)")


def answer(fields: dict, now: Optional[dt.datetime] = None) -> dict:
    """The stored object for the answered `fields` (blank ones left out), validated."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {k: fields.get(k) for k in FIELDS if fields.get(k) is not None}
    out["at"] = now.isoformat(timespec="seconds")
    validate(out)
    return out


def answered(v: Optional[dict]) -> bool:
    """The questionnaire was saved (blank answers included) — it is not asked again."""
    return isinstance(v, dict) and bool(v.get("at"))


def weekly_hours(v: Optional[dict]) -> Optional[float]:
    """Self-reported weekly hours = runs × minutes, as entered (not discounted, owner 2026-10-06);
    None when either is blank."""
    if not isinstance(v, dict):
        return None
    n, m = v.get("runs_per_week"), v.get("minutes_per_run")
    if n is None or m is None:
        return None
    return n * m / 60.0


def load(user_id: int = 1) -> Optional[dict]:
    """The stored answer (read-only, synchronous); None without one or when it doesn't validate."""
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(KEY, None, user_id)
    try:
        validate(v)
    except ValueError:
        return None
    return v


def foot_days(ds, today: dt.date) -> list[dt.date]:
    """The dates with a run or hike (overview.FOOT) up to `today`, oldest first."""
    from backend.engine import overview as O
    from backend.engine.wko5expr.dataset import date_to_day, day_to_date
    tday = math.floor(date_to_day(today))
    return sorted({day_to_date(math.floor(w.day)) for w in getattr(ds, "workouts", [])
                   if math.floor(w.day) <= tday and O.category(w) in O.FOOT})


def has_history(ds, today: dt.date) -> bool:
    """HISTORY_WEEKS complete Monday–Sunday weeks in a row, each with ≥ HISTORY_RUNS days of
    running or hiking, anywhere in the data before this week (memoised on the Dataset)."""
    memo = getattr(ds, "memo", None)
    mk = ("experience.has_history", today.isoformat())
    if isinstance(memo, dict) and mk in memo:
        return memo[mk]
    mon = today - dt.timedelta(days=today.weekday())
    per: dict[dt.date, int] = {}
    for d in foot_days(ds, today):
        m = d - dt.timedelta(days=d.weekday())
        if m < mon:
            per[m] = per.get(m, 0) + 1
    ok = False
    run = 0
    prev = None
    for m in sorted(per):
        good = per[m] >= HISTORY_RUNS
        run = run + 1 if good and prev is not None and m - prev == dt.timedelta(weeks=1) and run else (1 if good else 0)
        prev = m
        if run >= HISTORY_WEEKS:
            ok = True
            break
    if isinstance(memo, dict):
        memo[mk] = ok
    return ok
