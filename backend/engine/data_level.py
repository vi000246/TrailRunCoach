"""
資料等級 (SP-291; docs/research/cold-start.md §3 G5, §4.1, §5 T4) — the one answer to 「how much of
the runner's own data is there」 that the plan (cold_start.week_context → overview.week_plan, the
projection), the race feasibility (race_feasibility.races) and the status page (status.Status)
read, instead of each deciding 「not enough data」 on its own (§1.4: the weekly volume, the
thresholds and the feasibility each fell back to their own default).

  0 沒有資料  no run or hike (overview.FOOT) in the LEVEL0_DAYS before this week's Monday
  1 累積中    records, but not the level-2 condition yet
  2 正常      the Zone 3 gate's consistency path (quality_gate.z3_consistency with z3_rule — the
              進階設定 values, default 4 complete weeks with ≥ 3 sessions each, no 7-day gap, a
              ≥ 21-day break starts over; no new numbers), counted on the run AND hike days like
              level 0 (the questionnaire asks about both)

Read on this week's Monday, the plan's week: a first run mid-week doesn't change this week's level
(cold_start's reading, SP-288) — the plan, the feasibility (its weeks are the full weeks before this
one) and the status page see the same level all week.

  week    the week of the current stretch of data, 1 = the week it started (data_start: the week of
          the first run / hike after the last gap of more than LEVEL0_DAYS)
  survey  the questionnaire's start level stands in (cold_start.start_level: the answers as entered —
          not discounted, owner 2026-10-06 — or the default): level 0, and level 1 up to week `need`
          (§4.2 「不足 4 週的部分用問卷值補；滿 4 週後問卷不再使用」 — a level-1 runner past that week, e.g.
          2 runs a week, is planned on their own records, 推估). Level 2: never (owner: 「滿 4 週自動升到
          等級 2，問卷值不再使用」).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _

LEVEL0_DAYS = 28           # cold-start.md §4.1 等級 0: no run or hike in the last 28 days (= load_guard.SEED_DAYS)
LEVELS = (0, 1, 2)
LABEL = {0: N_("沒有資料"), 1: N_("累積中"), 2: N_("正常")}

# the plan page's line (§4.1; SP-291 驗收: 「你的資料還在累積（第 n 週／4）：週量依你填的問卷，心率區間是推估」)
LINE_SURVEY = N_("你的資料還在累積（第 {i} 週／{n}）：週量依你填的問卷{hr}")
LINE_DEFAULT = N_("你的資料還在累積（第 {i} 週／{n}）：週量從預設的每週 {h:g} 小時起算{hr}")
LINE_OWN = N_("你的資料還在累積：要連續 {n} 週、每週跑步或健行 ≥ {r} 次（現在 {k}/{n} 週）；週量照你的紀錄{hr}")
HR_PRIOR = N_("，心率區間是推估")


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def data_start(foot: list, monday: dt.date) -> Optional[dt.date]:
    """The Monday the current stretch of data started: the week of the first run / hike after the
    last gap of ≥ LEVEL0_DAYS before `monday` (sessions of this week count), this Monday when the
    LEVEL0_DAYS before it have none; None = no stretch (nothing at all up to this week)."""
    past = [d for d in foot if d < monday + dt.timedelta(days=7)]
    if not [d for d in past if monday - dt.timedelta(days=LEVEL0_DAYS) <= d < monday]:
        return monday                                   # 等級 0 on this Monday: the data starts this week
    start = past[0]
    for a, b in zip(past, past[1:]):
        if (b - a).days > LEVEL0_DAYS:
            start = b
    return _monday(start)


def _rule(rule: Optional[dict]) -> dict:
    if rule is not None:
        return rule
    from backend.engine import quality_gate as QG
    return QG.z3_rule()


def level(ds, today: dt.date, rule: Optional[dict] = None) -> dict:
    """This week's data level: {level 0 | 1 | 2, week, since (ISO Monday the stretch started), monday,
    need / runs (the Zone 3 rule's weeks and sessions a week), weeks_ok (the trailing complete weeks
    that meet it, ≤ need), survey (the questionnaire's start level stands in)}. `rule` = quality_gate.
    z3_rule() (None = read it). Memoised on the Dataset per Monday and rule."""
    from backend.engine import experience as EX
    from backend.engine import quality_gate as QG
    if not isinstance(getattr(ds, "workouts", None), (list, tuple)):
        raise ValueError("no workout list: the level can't be told")       # never 「level 0」 by mistake
    rule = _rule(rule)
    monday = _monday(today)
    need, runs = int(rule["weeks"]), int(rule["runs"])
    memo = getattr(ds, "memo", None)
    mk = ("data_level", monday.isoformat(), tuple(int(rule[k]) for k in ("weeks", "runs", "gap", "relock")))
    if isinstance(memo, dict) and mk in memo:
        return dict(memo[mk])
    foot = EX.foot_days(ds, monday + dt.timedelta(days=6))
    before = [d for d in foot if d < monday]
    if not [d for d in before if monday - dt.timedelta(days=LEVEL0_DAYS) <= d]:
        lv, since, ok = 0, monday, 0
    else:
        try:
            skip = QG._transition_skip(ds, before, monday)
        except Exception:                   # noqa: BLE001 — no plan: no post-race days
            skip = set()
        # the gate's own path on the days before this Monday (z3_gate reads it on the run days)
        cons = QG.z3_consistency(before, monday, need, skip, rule)
        lv = 2 if cons["open"] else 1
        ok = min(int(cons.get("weeks") or 0), need)
        since = data_start(foot, monday) or monday
    week = (monday - since).days // 7 + 1
    out = {"level": lv, "week": week, "since": since.isoformat(), "monday": monday.isoformat(),
           "need": need, "runs": runs, "weeks_ok": ok, "survey": lv == 0 or (lv == 1 and week <= need)}
    if isinstance(memo, dict):
        memo[mk] = dict(out)
    return out


def safe_level(ds, today: dt.date, rule: Optional[dict] = None) -> Optional[dict]:
    """level(), None on any error — a reader then keeps its level-2 (today's) behaviour."""
    try:
        return level(ds, today, rule)
    except Exception:                       # noqa: BLE001 — the plan / card / page must still build
        return None


def line(lv: Optional[dict], source: str = "survey", hours: Optional[float] = None,
         hr_prior: bool = False) -> Optional[str]:
    """The one line on the 課表 page (and the status card): what is still a default while the data
    builds up; None at level 2. `source` = cold_start.start_level's (survey / default / no_run30),
    `hours` its weekly hours, `hr_prior` = the HR zones come from the 0.90 × max-HR prior (SP-289)."""
    if not lv or lv.get("level") == 2:
        return None
    hr = _(HR_PRIOR) if hr_prior else ""
    if not lv.get("survey"):
        return _(LINE_OWN, n=lv["need"], r=lv["runs"], k=lv.get("weeks_ok") or 0, hr=hr)
    if source == "survey":
        return _(LINE_SURVEY, i=lv["week"], n=lv["need"], hr=hr)
    return _(LINE_DEFAULT, i=lv["week"], n=lv["need"], h=float(hours or 0.0), hr=hr)


def public(lv: Optional[dict]) -> Optional[dict]:
    """The API shape (week_plan / status / feasibility): the level with its label."""
    if not lv:
        return None
    return {**lv, "label": _(LABEL[lv["level"]])}
