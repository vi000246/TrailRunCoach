"""
冷啟動排課 (SP-288; docs/research/cold-start.md §1.1, §3 G1, §4.2, §4.5, §5 T1).

Before: a runner without history got a self-contradicting first week — a weekly target of 0.5 h
(the +10 % / +0.5 h cap of 0 h) next to a 60-min long run (the long-run floor, the 50 % share only
applied from 120 min), no easy run at all (reproduced on an empty dataset:
test_cold_start.test_empty_dataset_reproduces_the_old_first_week).

Now, for the week the data starts (COLD WEEK: no run or hike in the LEVEL0_DAYS before this week's
Monday — 等級 0 of §4.1, read on the Monday so a first run mid-week doesn't flip the plan — and no
停訓後的恢復期 with a previous volume, engine/reentry.py, which already covers a runner back after a
break):

  * the weekly volume = the questionnaire's (engine/experience.py) runs × minutes, AS ENTERED
    (owner 2026-10-06: not discounted; the doc's × 0.75 is not applied); with 「能連續跑 30 分鐘」
    at least RUN30_FLOOR_H
  * no questionnaire (or no volume in it): DEFAULT_HOURS, DEFAULT_RUNS runs, no long run, and a
    note pointing to the questionnaire (owner 2026-10-06)
  * 「還不能連續跑 30 分鐘」: no run-walk sessions (the doc's T7 is not built, owner 2026-10-06) — the
    no-questionnaire default, and a note that the automatic plan is only meant for runners who can
    run 30 minutes
  * DEFAULT_RUNS runs a week, not on two days in a row (課表偏好 每週跑步次數 / the preferences'
    placement win when set)
  * the self-reported longest run stands for `longest28` (the Frandsen cap reads it)

The RAMP (the first RAMP_WEEKS weeks from the week the data started, the cold week included):
the start level stands for the weeks before the data — the weekly base is max(start level,
actual) (§4.2 「實際紀錄」) — and the run count stays ≥ DEFAULT_RUNS. Everything else is the
existing rule (+10 %, at least +0.5 h, 3:1; §4.2: no stricter step for beginners — GRONORUN [C5]
found none helped). projection.project_weeks reads week_plan's `cold_start` and applies the same.

For every runner, not only new ones: a week under SHORT_WEEK_MIN gets a long run of at most
LONG_SHARE of the week and no 60-min floor (SP-288 驗收). A runner with history (data in the 28 days
before this Monday, or a re-entry block) gets none of the rest: `week_context` is None.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _

DEFAULT_HOURS = 1.5        # owner 2026-10-06; 推估 (NHS Couch to 5K's end point: 3 × 30 min a week [C10])
DEFAULT_RUNS = 3           # NHS Couch to 5K [C10], TrainingPeaks' novice trail plan [C12]: 3 runs, a day apart
RUN30_FLOOR_H = 1.5        # 「能連續跑 30 分鐘」 → ≥ 3 × 30 min (NHS end point [C10]; as a floor 推估, §4.2)
LONG_SHARE = 0.40          # 推估 (cold-start.md §4.2): a week < SHORT_WEEK_MIN — long run ≤ 40 % of it
SHORT_WEEK_MIN = 120       # the existing line: the 50 % share of the long run applies from 120 min
LONG_MIN_WEEK = 90         # 推估: a week under 3 × 30 min has no separate long run
LEVEL0_DAYS = 28           # cold-start.md §4.1 等級 0: no run or hike in the last 28 days (= load_guard.SEED_DAYS)
RAMP_WEEKS = 4             # 推估 (§4.2): the start level stands in until 4 weeks of data (= the Zone 3 gate's 4 weeks)

NOTE_NO_SURVEY = N_("還沒填跑步經驗：這週先排每週 {h:g} 小時、{n} 次輕鬆跑，不排長跑。到「設定 → 個人資料 → 跑步經驗」"
                      "填最近 4 週的量，課表會照你平常的量排")
NOTE_NO_30 = N_("還不能連續跑 30 分鐘：app 的自動排課要等你能連續跑 30 分鐘之後才準。這週先照預設排每週 {h:g} 小時、"
                  "{n} 次輕鬆跑，不排長跑；每次以能完整講一句話的強度為準")
NOTE_SURVEY = N_("資料還在累積：這週的量照你填的問卷（每週 {h:.1f} 小時，照原數字用），排 {n} 次")
NOTE_RAMP = N_("資料還在累積（第 {i}/{n} 週）：週量的基準取起步量 {h:.1f} 小時和實際紀錄的較大值，每週至少 {runs} 次")


def start_level(exp: Optional[dict]) -> dict:
    """The cold week from the questionnaire: {hours, runs, long (a long run allowed), longest
    (self-reported, min; None), source: survey | default | no_run30}."""
    from backend.engine import experience as EX
    base = {"hours": DEFAULT_HOURS, "runs": DEFAULT_RUNS, "long": False, "longest": None, "source": "default"}
    if not isinstance(exp, dict):
        return base
    if exp.get("can_run_30") is False:
        return {**base, "source": "no_run30"}          # the run-walk plan isn't built: the default (owner)
    h = EX.weekly_hours(exp)
    if h is None or h <= 0:
        return base
    if exp.get("can_run_30") is True:
        h = max(h, RUN30_FLOOR_H)
    longest = exp.get("longest_min")
    # more runs a week than DEFAULT_RUNS reported: kept (≤ rest_days.AUTO_MAX_RUNS) — fewer, longer runs
    # would make each one longer than the runner's usual (推估; Frandsen: single-run spikes, not weekly
    # volume, carry the risk — downhill-recovery.md [D17])
    from backend.engine.rest_days import AUTO_MAX_RUNS
    runs = max(DEFAULT_RUNS, min(int(exp.get("runs_per_week") or 0), AUTO_MAX_RUNS))
    return {"hours": h, "runs": runs, "long": h * 60.0 >= LONG_MIN_WEEK,
            "longest": float(longest) if longest else None, "source": "survey"}


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


def week_context(ds, today: dt.date, exp: Optional[dict] = None, reentry: Optional[dict] = None,
                 load_exp: bool = True) -> Optional[dict]:
    """This week's cold-start / ramp context, None for a runner with history (nothing changes).
    {level: 0 (cold week) | 1 (ramp), week (1…RAMP_WEEKS), since, until (the Monday the ramp ends),
    hours, runs, long, longest, source}. `exp`: the questionnaire (None + load_exp = the stored one);
    `reentry`: week_plan's 停訓後的恢復期 block — with a previous volume it wins (no cold start)."""
    if reentry and reentry.get("prev_hours"):
        return None
    from backend.engine import experience as EX
    monday = _monday(today)
    foot = EX.foot_days(ds, monday + dt.timedelta(days=6))
    since = data_start(foot, monday)
    if since is None or since + dt.timedelta(weeks=RAMP_WEEKS) <= monday:
        return None
    if exp is None and load_exp:
        exp = EX.load()
    lv = start_level(exp)
    week = (monday - since).days // 7 + 1
    return {**lv, "level": 0 if since == monday else 1, "week": week, "since": since.isoformat(),
            "until": (since + dt.timedelta(weeks=RAMP_WEEKS)).isoformat()}


def note(ctx: dict, runs: Optional[int] = None) -> dict:
    """The week's note (課表 / 總覽): what the cold week / the ramp used. `runs`: the runs actually
    planned (a small volume makes fewer than ctx["runs"]); None = ctx["runs"]."""
    n = ctx["runs"] if runs is None else runs
    if ctx["level"] == 0 and ctx["source"] == "default":
        t = _(NOTE_NO_SURVEY, h=ctx["hours"], n=n)
    elif ctx["level"] == 0 and ctx["source"] == "no_run30":
        t = _(NOTE_NO_30, h=ctx["hours"], n=n)
    elif ctx["level"] == 0:
        t = _(NOTE_SURVEY, h=ctx["hours"], n=n)
    else:
        t = _(NOTE_RAMP, i=ctx["week"], n=RAMP_WEEKS, h=ctx["hours"], runs=ctx["runs"])
    return {"level": "info", "src": "cold_start", "text": t}


def why(ctx: dict) -> str:
    """The volume line of the week's 「為什麼」."""
    if ctx["source"] == "survey":
        return _("新使用者第一週：週量 = 你填的每週 {h:.1f} 小時（不打折）", h=ctx["hours"])
    return _("新使用者第一週：起步量每週 {h:g} 小時（{n} 次 × 30 分，推估）", h=ctx["hours"], n=ctx["runs"])


def long_share_cap(long_min: float, total_min: float) -> float:
    """The long run's share of the week: ≤ 50 % from SHORT_WEEK_MIN, below it ≤ LONG_SHARE and no
    60-min floor (SP-288; the old rule kept a 60-min long run in a 30-min week)."""
    return min(long_min, (0.5 if total_min >= SHORT_WEEK_MIN else LONG_SHARE) * total_min)


def easy_runs(n_easy: int, left: float, ctx: dict, other_runs: int) -> int:
    """The easy runs of a cold / ramp week: the cold week exactly ctx["runs"] runs in all, a ramp week at
    least that many; never one shorter than plan_prefs.MIN_EASY."""
    from backend.engine.plan_prefs import MIN_EASY
    want = ctx["runs"] - other_runs
    if ctx["level"] != 0:
        want = max(n_easy, want)
    return max(0, min(want, int(left // MIN_EASY)))


def keep_long(long_min: float, total_min: float, runs: int) -> bool:
    """A long run only when it is longer than the other runs would be (else they are all easy runs)."""
    others = max(1, runs - 1)
    return long_min > (total_min - long_min) / others + 1e-9


def no_long(ctx: Optional[dict], long_min: float, total_min: float, recovery_week: bool = False) -> bool:
    """No long run this week (SP-288): the cold week without the questionnaire's volume (owner
    2026-10-06), and any cold / ramp week where the long run (after its caps) would not be longer
    than the other runs — they are all easy runs then. A recovery week keeps its long run."""
    if ctx is None or recovery_week:
        return False
    if ctx["level"] == 0 and not ctx["long"]:
        return True
    return not keep_long(long_min, total_min, ctx["runs"])


def prefs_for(prefs, ctx: Optional[dict], slots: int):
    """課表偏好 with the cold / ramp week's run count when 每週跑步次數 is not set (it wins when set:
    「使用者設了偏好就照偏好」); `slots` = the week's training days. `prefs` itself otherwise."""
    if ctx is None or prefs is None or getattr(prefs, "runs", None) is not None:
        return prefs
    from dataclasses import replace
    return replace(prefs, runs=max(1, min(ctx["runs"], slots)))


def apart(ctx: Optional[dict]) -> bool:
    """Runs a day apart (NHS [C10]) — possible up to 4 runs a week."""
    return ctx is not None and ctx["runs"] <= 4
