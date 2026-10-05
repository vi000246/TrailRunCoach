"""
休息日的位置 (SP-82; docs/research/rest-day-placement.md, the owner's decisions §4.4).

Before SP-82 a rest day was whatever was left after the placement: the easy runs took the
earliest free days (overview.week_plan, plan_prefs.place, projection._place), so the rest days
piled up late in the week. The number of runs is not changed here — it follows the week's minutes
(easy runs of ~50 min, overview.easy_count; 課表偏好 每週跑步次數 when set), so a week with fewer
runs than 可練日 leaves days free on purpose. What changes is WHICH days stay free.

The long run and the intervals keep their own day rules. The easy runs then take the set of
days with the smallest penalty (every combination, ties → the earliest days; the same input
always gives the same week):

  1. a run the day after the long run (Sunday long → this Monday too)       RULE_AFTER_LONG
  2. a run the day before the long run while the week has a rest day       RULE_BEFORE_LONG
  3. ≤ 4 runs a week: a run the day after an interval                      RULE_AFTER_Q
  4. > 3 training days in a row / > 2 rest days in a row, per extra day    RULE_STREAK
  and the athlete's 休息日偏好 (課表偏好 pref_days["rest"], first / second choice) above all.

Sources (教練級 only; no controlled trial compares rest-day placements): Higdon (rest the day
after the weekend long run and the day before it), Uphill Athlete / Koop (one rest day a week,
the day after the long run), Bompa & Buzzichelli p.165–170 (hard days apart, two heavier days then
a light one), Seiler (hard day – easy day). The order of the rules (the day after the long run
first) is the owner's decision (2026-10-05); the weights and the streak limits are 推估.

Auto mode keeps at least one rest day: AUTO_MAX_RUNS runs a week unless 每週跑步次數 says more
(Bompa's microcycles, UA, Koop: at least one full rest day a week; the 6 itself is 推估).
"""
from __future__ import annotations

import datetime as dt
from itertools import combinations
from typing import Iterable, Optional

AUTO_MAX_RUNS = 6
RULE_AFTER_LONG = 8
RULE_BEFORE_LONG = 4
RULE_AFTER_Q = 2
RULE_STREAK = 1
PREF_WEIGHT = (32, 16)               # 休息日偏好 first / second choice: above every rule
MAX_TRAIN_STREAK = 3
MAX_REST_STREAK = 2
FEW_RUNS = 4
SRC = ("休息日放在長跑隔天（Higdon、Uphill Athlete、Koop）、其次長跑前一天（Higdon）；硬課之間隔開"
       "（Bompa & Buzzichelli、Seiler）；連練不超過 3 天、連休不超過 2 天與權重為推估")


def _streaks(flags: list) -> list[tuple[bool, int]]:
    out: list = []
    for f in flags:
        if out and out[-1][0] == f:
            out[-1] = (f, out[-1][1] + 1)
        else:
            out.append((f, 1))
    return out


def penalty(chosen: Iterable[dt.date], week: list[dt.date], runs: set, long_day: Optional[dt.date],
            long_wd: Optional[int], quality: Iterable[dt.date], rest_pref: tuple = ()) -> int:
    """The penalty of putting the easy runs on `chosen`; `runs` = the week's other run days."""
    ch = set(chosen)
    all_runs = set(runs) | ch
    p = 0
    for rank, wd in enumerate(rest_pref[:len(PREF_WEIGHT)]):
        p += sum(PREF_WEIGHT[rank] for d in ch if d.weekday() == wd)
    after = set()
    if long_day is not None:
        after.add(long_day + dt.timedelta(days=1))
    if long_wd == 6 and week:
        after.add(week[0])                       # last Sunday's long run: this Monday
    p += RULE_AFTER_LONG * len(after & ch)
    if long_day is not None and len(all_runs & set(week)) < len(week):
        if long_day - dt.timedelta(days=1) in ch:
            p += RULE_BEFORE_LONG
    if len(all_runs & set(week)) <= FEW_RUNS:
        p += RULE_AFTER_Q * sum(1 for q in quality if q + dt.timedelta(days=1) in ch)
    for is_run, n in _streaks([d in all_runs for d in week]):
        lim = MAX_TRAIN_STREAK if is_run else MAX_REST_STREAK
        if n > lim:
            p += RULE_STREAK * (n - lim)
    return p


def pick_days(n: int, avail: list[dt.date], week: list[dt.date], runs: Iterable[dt.date],
              long_day: Optional[dt.date] = None, long_wd: Optional[int] = None,
              quality: Iterable[dt.date] = (), rest_pref: tuple = ()) -> list[dt.date]:
    """The `n` days of `avail` for the week's easy runs with the smallest penalty (ties → the
    earliest days), sorted. `runs`: days that already have a run (placed or done)."""
    avail = sorted(set(avail))
    n = max(0, min(n, len(avail)))
    if n == 0:
        return []
    runs, quality = set(runs), list(quality)
    best = None
    for combo in combinations(avail, n):
        key = (penalty(combo, week, runs, long_day, long_wd, quality, rest_pref), combo)
        if best is None or key < best:
            best = key
    return list(best[1])


HARD_KINDS = ("long", "quality", "test", "hike")     # blackouts.HARD: the days that need 48 h around them


def swap_warnings(sessions: list[dict], moved: Iterable[str]) -> list[str]:
    """After a rest day was moved (api POST /rest-days/move): a note per moved hard session that now
    sits < 2 days from another hard one (active or done) — 台灣教練 / Bompa: ≥ 48 h between hard days."""
    from backend.i18n import _
    mv = set(moved)
    hard = [s for s in sessions if s.get("day") and s.get("kind") in HARD_KINDS
            and s.get("state") in ("active", "done")]
    out = []
    for a in (s for s in hard if s.get("uid") in mv):
        da = dt.date.fromisoformat(a["day"])
        for b in hard:
            if b is a or (b.get("uid") in mv and b["uid"] < a["uid"]):
                continue
            n = abs((dt.date.fromisoformat(b["day"]) - da).days)
            if n < 2:
                out.append(_("「{a}」和「{b}」只隔 {n} 天：硬課之間建議隔 ≥ 2 天，可以再拖動調整",
                             a=a.get("title") or "", b=b.get("title") or "", n=n))
    return out


def strength_days(cands_easy: list[dt.date], cands_free: list[dt.date], avoid: Iterable[dt.date] = (),
                  rest_pref: tuple = ()) -> list[dt.date]:
    """Strength candidates in order (SP-83 §4.3, owner 2026-10-05): the easy-run days first, a free
    day only when no easy day fits (a 休息日偏好 day last); never a day in `avoid`."""
    av = set(avoid)
    last = {wd: len(rest_pref) - i for i, wd in enumerate(rest_pref)}     # the first choice last of all
    easy = sorted(d for d in set(cands_easy) if d not in av)
    free = sorted((d for d in set(cands_free) if d not in av and d not in easy),
                  key=lambda d: (last.get(d.weekday(), 0), d))
    return easy + free
