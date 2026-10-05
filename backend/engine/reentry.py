"""
停訓後的恢復期 (re-entry) — docs/research/detraining.md §4.1, §4.2, §6.

A break = consecutive days without a run (Daniels' tables use it; the impact
load has to be re-learnt, so hikes / rides don't shorten it — detraining.md
§5.2). The re-entry block by break length (Daniels table 9.2, coach; the
period lasts as long as the break):

  1–5 days    back to 100 %, no make-up (Daniels cat. 1; Friel ≤ 3 days)
  6–13 days   first half 50 %, second half 75 % of the previous volume; no
              Zone 3 / Zone 5 inside it; then 1 Zone 3 session before Zone 5
              (台灣教練: Zone 3 first; 1 session 推估)
  14–28 days  as above; Zone 3 / Zone 5 targets × FVDOT (0.973–0.931); the
              last long run of the block is a drift check (UA: re-read after
              a layoff); 2 Zone 3 sessions before Zone 5 (推估)
  29–56 days  three stages 33 / 50 / 75 % (Daniels cat. 3); Zone 5 only after
              the aerobic base is re-confirmed after the break (one of the
              three tests: the 90-min drift, UA gap or Friel drift; Mujika &
              Padilla 2000: recent gains are lost
              after > 4 weeks); AeT counts as stale (UA)
  > 56 days   restart: 15 weeks, 3-week steps 33 → 50 → 70 → 85 → 100 %
              (Daniels cat. 4); Zone 3 from week 13 (推估 mapping of his T at
              step 5); CP and AeT retests

FVDOT (VDOT O2, "VDOT Adjustments For Time Off From Running", 2018 — coach):
FVDOT-1 without cross-training, FVDOT-2 with it (≥ half the break's days
with ≥ 45 min of hiking / riding / walking — 推估, Daniels doesn't define it).
The FVDOT-2 42-day cell reads 0.994 on the web page — a typo for 0.944 by
its neighbours (未驗證, to check against the book). Between rows: linear
(推估). Power targets × FVDOT too (推估: VDOT ↔ CP not verified).

"The previous volume" = the mean weekly endurance time of the 4 weeks before
the break (推估). Planned breaks come from 不排課日期 (engine/blackouts.py)
≥ 6 days with no run inside; unplanned ones from the activity data (the
current gap counts as a break returning today).

Post-race phases (SP-73, owner 2026-10-05): days inside an A race's 恢復期 (7–14
days) or the 轉換期 after it (auto or manual, planning.post_race_days) are not
break days — both are planned rest / easy / cross-training blocks, so a 恢復期
without a run or weeks of only cross-training / strength in the transition
don't start a re-entry block when base resumes. A break counts its days outside
those phases only (≥ 6 still makes a block, as long as those days, e.g. a
transition + 10 more days off = a 10-day 6–13 block; the block text says how
many post-race days were left out).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from backend.i18n import _

MIN_BREAK = 6                         # Daniels cat. 2 starts at 6 days
CAT2_MAX, CAT3_MAX = 28, 56           # days
CROSS_MIN = 45                        # 推估: minutes of hiking / riding / walking that count as cross-training
CROSS_SHARE = 0.5                     # 推估: on ≥ half the break's days
LONG_CAP_MIN = 90                     # 推估: 6–13-day block long-run cap (徐國峰's 90-min check length)
TARGETS_AFTER_DAYS = 14               # 推估: Zone 3 targets × FVDOT also for 2 weeks after the block
CAT4_STEPS = (0.33, 0.50, 0.70, 0.85, 1.00)
CAT4_Z3_WEEKS = 12                    # 推估: Zone 3 from week 13 (Daniels' T at step 5)
SRC = ("Daniels 表 9.2（停練後回來的調整）；VDOT O2 FVDOT 表（2018）；"
       "Mujika & Padilla 2000；Uphill Athlete（中斷後用飄移測試重新讀）；台灣教練（先 3 區後 5 區）")

FVDOT1 = ((5, 1.000), (6, 0.997), (7, 0.994), (10, 0.985), (14, 0.973), (21, 0.952), (28, 0.931), (35, 0.910),
          (42, 0.889), (49, 0.868), (56, 0.847), (63, 0.826), (70, 0.805), (72, 0.800))
FVDOT2 = ((5, 1.000), (6, 0.998), (7, 0.997), (10, 0.992), (14, 0.986), (21, 0.976), (28, 0.965), (35, 0.955),
          (42, 0.944), (49, 0.934), (56, 0.923), (63, 0.913), (70, 0.902), (72, 0.900))


def fvdot(days: int, cross: bool = False) -> float:
    tab = FVDOT2 if cross else FVDOT1
    if days <= tab[0][0]:
        return 1.0
    if days >= tab[-1][0]:
        return tab[-1][1]
    for (a, fa), (b, fb) in zip(tab, tab[1:]):
        if a <= days <= b:
            return fa + (fb - fa) * (days - a) / (b - a)
    return tab[-1][1]


def category(days: int) -> str:
    """"short" (1–5), "6-13", "14-28", "29-56", "long" (> 56)."""
    if days < MIN_BREAK:
        return "short"
    if days <= 13:
        return "6-13"
    if days <= CAT2_MAX:
        return "14-28"
    if days <= CAT3_MAX:
        return "29-56"
    return "long"


STEP_UP_MIN = {"6-13": 14, "14-28": 29, "29-56": 57}   # 推估: an injury layoff uses the next block


def plan(last: dt.date, ret: dt.date, cross: bool = False, planned: bool = False,
         prev_hours: Optional[float] = None, prev_long_min: Optional[float] = None,
         ongoing: bool = False, injury: Optional[dict] = None, step_up: bool = False,
         days: Optional[int] = None, transition_days: int = 0) -> Optional[dict]:
    """The re-entry block for a break from the day after `last` (the last run)
    to the day before `ret` (the first run back, or the day after a blackout).
    None for a break < 6 days. `injury` (engine/injuries.py): the 傷病紀錄 the
    break overlaps — the text says 傷停; `step_up` (injury.reentry_step_up,
    推估): the block of the next-longer break (停 10 天 → the 14–28-day rules
    and length) — after an injury the tissue, not only the fitness, has to
    re-adapt. FVDOT stays the one of the real break (it is a fitness loss). `days`: the break's
    length when not every day between counts (find_all: the post-race phase days, `transition_days` of
    them, are left out); None = all of them."""
    if days is None:
        days = (ret - last).days - 1
    cat = category(days)
    if cat == "short":
        return None
    real_days = days
    stepped = bool(injury) and step_up and cat in STEP_UP_MIN
    if stepped:
        days = STEP_UP_MIN[cat]
        cat = category(days)
    segs: list[tuple[str, str, float]] = []

    def seg(a: dt.date, n: int, f: float) -> dt.date:
        b = a + dt.timedelta(days=n)
        segs.append((a.isoformat(), b.isoformat(), f))
        return b

    if cat in ("6-13", "14-28"):
        h = math.ceil(days / 2)
        end = seg(seg(ret, h, 0.50), days - h, 0.75)
        q_from = end
    elif cat == "29-56":
        a = days // 3
        end = seg(seg(seg(ret, a, 0.33), a, 0.50), days - 2 * a, 0.75)
        q_from = end
    else:
        d = ret
        for f in CAT4_STEPS:
            d = seg(d, 21, f)
        end = d
        q_from = ret + dt.timedelta(weeks=CAT4_Z3_WEEKS)
    inj = None
    if injury:
        from backend.engine import injuries as INJ
        inj = {"id": injury.get("id"), "label": INJ.full_label(injury.get("area"), injury.get("side"))}
    return {"last_run": last.isoformat(), "return": ret.isoformat(), "days": real_days, "category": cat,
            "end": end.isoformat(), "quality_from": q_from.isoformat(), "segments": segs,
            "fvdot": round(fvdot(real_days, cross), 4), "cross": cross, "planned": planned, "ongoing": ongoing,
            "prev_hours": prev_hours, "prev_long_min": prev_long_min,
            "z3_before_z5": 1 if cat == "6-13" else 2,
            "drift_check": cat == "14-28", "reconfirm": cat in ("29-56", "long"),
            "aet_stale": cat in ("29-56", "long"), "cp_retest": cat == "long" or real_days >= 50,
            "restart_base": cat == "long", "injury": inj, "stepped_up": stepped, "days_effective": days,
            "transition_days": int(transition_days),
            "text": text_of(real_days, cat, ret, end, inj, stepped, transition_days)}


def text_of(days: int, cat: str, ret: dt.date, end: dt.date, injury: Optional[dict] = None,
            stepped: bool = False, transition_days: int = 0) -> str:
    how = {"6-13": _("前半 50%、後半 75%"), "14-28": _("前半 50%、後半 75%，強度目標打折"),
           "29-56": _("三段 33／50／75%，5 區要重新確認有氧基礎"), "long": _("15 週重新打底（33→50→70→85→100%）")}[cat]
    head = (_("傷停 {days} 天（{label}，傷病紀錄 #{id}）", days=days, label=injury["label"], id=injury["id"])
            if injury else _("停跑 {days} 天", days=days))
    up = _("；傷後往上一級排（推估）") if stepped else ""
    if transition_days:
        head += _("（不含賽後恢復期／轉換期 {n} 天）", n=transition_days)
    return _("{head}：{start} 起恢復期到 {end}（{how}；Daniels 表 9.2，恢復期＝停訓天數{up}）",
             head=head, start=ret.isoformat(), end=(end - dt.timedelta(days=1)).isoformat(), how=how, up=up)


def frac_on(p: Optional[dict], day: dt.date) -> Optional[float]:
    """The block's volume fraction on `day`; None outside the block."""
    if not p:
        return None
    iso = day.isoformat()
    for a, b, f in p["segments"]:
        if a <= iso < b:
            return f
    return None


def in_block(p: Optional[dict], day: dt.date) -> bool:
    return bool(p) and p["return"] <= day.isoformat() < p["end"]


def quality_ok(p: Optional[dict], day: dt.date) -> bool:
    """No Zone 3 / Zone 5 in the break or inside the block (Daniels: E days only)."""
    return not p or not (p["last_run"] < day.isoformat() < p["quality_from"])


def week_factor(p: dict, monday: dt.date, last_break_day: Optional[dt.date] = None) -> Optional[float]:
    """Σ fractions over the week's 7 days ÷ 7: break days count 0, block days
    their %, days after the block 1. None when the week doesn't touch the block."""
    days = [monday + dt.timedelta(days=i) for i in range(7)]
    if not any(in_block(p, d) for d in days):
        return None
    ret = dt.date.fromisoformat(p["return"])
    tot = 0.0
    for d in days:
        f = frac_on(p, d)
        if f is not None:
            tot += f
        elif d >= ret:
            tot += 1.0
    return tot / 7.0


def _run_days(ds) -> list[dt.date]:
    from backend.engine import workout_review as WR
    return sorted({WR._wdate(w) for w in ds.workouts if w.sport == "run"})


def _cross(ds, a: dt.date, b: dt.date) -> bool:
    """≥ half the break's days [a, b] with ≥ 45 min of hiking / riding / walking (推估)."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category as cat_of, moving_s
    if b < a:
        return False
    days = {}
    for w in ds.workouts:
        d = WR._wdate(w)
        if a <= d <= b and w.sport != "run" and cat_of(w) in ("hike", "bike", "walk", "trail", "road"):
            days[d] = days.get(d, 0.0) + moving_s(w) / 60.0
    n = (b - a).days + 1
    return sum(1 for v in days.values() if v >= CROSS_MIN) >= CROSS_SHARE * n


def prev_volume(ds, last: dt.date) -> tuple[float, float]:
    """(mean weekly endurance hours, longest session minutes) of the 4 weeks before `last` (推估)."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import ENDURANCE, category as cat_of, moving_s
    a = last - dt.timedelta(days=27)
    tot, longest = 0.0, 0.0
    for w in ds.workouts:
        d = WR._wdate(w)
        if a <= d <= last and cat_of(w) in ENDURANCE:
            tot += moving_s(w)
            longest = max(longest, moving_s(w) / 60.0)
    return tot / 3600.0 / 4.0, longest


def _injuries(injuries, step_up) -> tuple[list, bool]:
    """The 傷病紀錄 events and the injury.reentry_step_up setting (default on)."""
    if injuries is None:
        try:
            from backend.engine import injuries as INJ
            injuries = INJ.load_events()
        except Exception:                   # noqa: BLE001
            injuries = []
    if step_up is None:
        try:
            from backend.engine.wko5expr.datasource import read_setting
            step_up = read_setting("injury.reentry_step_up", True) is not False
        except Exception:                   # noqa: BLE001
            step_up = True
    return list(injuries or []), bool(step_up)


def _transition_set(ds, runs: list[dt.date], today: dt.date, horizon_days: int) -> set[dt.date]:
    """The post-race 恢復期 / 轉換期 days (planning.post_race_days) a break within reach can
    touch; empty without a plan or on any error (re-entry must still work)."""
    plan_ = getattr(ds, "plan", None)
    if plan_ is None:
        return set()
    try:
        from backend.engine.planning import post_race_days
        lo = min([today - dt.timedelta(days=horizon_days)] + runs[:1])
        return post_race_days(plan_, lo, today + dt.timedelta(weeks=27))
    except Exception:                       # noqa: BLE001
        return set()


def find_all(ds, today: dt.date, blackouts=(), horizon_days: int = 182, injuries=None,
             step_up: Optional[bool] = None) -> list[dict]:
    """Every re-entry block within reach, oldest first: breaks ≥ 6 days whose
    return is ≤ `horizon_days` ago, planned (a 不排課日期 range ≥ 6 days with
    no run inside, now or ahead) or unplanned (from the runs; the current gap
    counts, returning today). A break that overlaps a 傷病紀錄 event is a 傷停
    (`injuries`: the events, default the app DB's)."""
    injuries, step_up = _injuries(injuries, step_up)
    from backend.engine import injuries as INJ
    runs = _run_days(ds)
    tset = _transition_set(ds, runs, today, horizon_days)

    def off(last: dt.date, ret: dt.date) -> tuple[int, int]:
        """(break days outside a post-race phase, post-race 恢復期／轉換期 days) between `last` and `ret`."""
        n = (ret - last).days - 1
        t = sum(1 for k in range(1, n + 1) if last + dt.timedelta(days=k) in tset) if tset else 0
        return n - t, t
    runs_past = [d for d in runs if d <= today]
    cands = []
    for a, b in zip(runs_past, runs_past[1:]):
        if (b - a).days - 1 >= MIN_BREAK and (today - b).days <= horizon_days and off(a, b)[0] >= MIN_BREAK:
            cands.append((a, b, False, False))
    if runs_past and (today - runs_past[-1]).days - 1 >= MIN_BREAK and off(runs_past[-1], today)[0] >= MIN_BREAK:
        cands.append((runs_past[-1], today, False, True))         # still off: as if back today
    from backend.engine import blackouts as BL
    for lo, hi in BL._runs(sorted(dt.date.fromisoformat(k) for k in BL.blocked(blackouts or ()))):
        n = (hi - lo).days + 1
        if n < MIN_BREAK or any(lo <= d <= hi for d in runs):
            continue
        ret = hi + dt.timedelta(days=1)
        if lo > today:
            last = lo - dt.timedelta(days=1)          # ahead: assume running up to the range
        else:
            last = max([d for d in runs if d < lo] or [lo - dt.timedelta(days=1)])
        if (ret - today).days <= 7 * 26 and (today - ret).days <= horizon_days and off(last, ret)[0] >= MIN_BREAK:
            cands.append((last, ret, True, False))
    out = []
    for last, ret, planned, ongoing in sorted(set(cands), key=lambda c: c[1]):
        ph, pl = prev_volume(ds, last)
        inj = INJ.overlapping(injuries, last + dt.timedelta(days=1), ret - dt.timedelta(days=1), today) \
            if injuries else None
        n, t = off(last, ret)
        p = plan(last, ret, _cross(ds, last + dt.timedelta(days=1), ret - dt.timedelta(days=1)), planned, ph, pl,
                 ongoing, injury=inj, step_up=step_up, days=n if t else None, transition_days=t)
        if p is not None:
            out.append(p)
    return out


def planned_ahead(blackouts, after: dt.date, prev_hours: Optional[float], prev_long_min: Optional[float]) -> list[dict]:
    """Blocks for 不排課日期 ranges ≥ 6 days that start after `after` (the
    projected weeks; no data yet — the athlete is assumed to run up to them)."""
    from backend.engine import blackouts as BL
    out = []
    for lo, hi in BL._runs(sorted(dt.date.fromisoformat(k) for k in BL.blocked(blackouts or ()))):
        if lo <= after or (hi - lo).days + 1 < MIN_BREAK:
            continue
        p = plan(lo - dt.timedelta(days=1), hi + dt.timedelta(days=1), False, True, prev_hours, prev_long_min)
        if p is not None:
            out.append(p)
    return out


def block_on(blocks: list[dict], monday: dt.date) -> Optional[dict]:
    """The block whose period touches the week of `monday` (the latest)."""
    hit = [p for p in blocks if week_factor(p, monday) is not None]
    return hit[-1] if hit else None


def find(ds, today: dt.date, blackouts=(), horizon_days: int = 182, injuries=None,
         step_up: Optional[bool] = None) -> Optional[dict]:
    """The block that matters on `today`: the one in progress, else the
    latest that started on or before today, else the next planned one."""
    ps = find_all(ds, today, blackouts, horizon_days, injuries, step_up)
    iso = today.isoformat()
    past = [p for p in ps if p["return"] <= iso]
    if past:
        return past[-1]
    return ps[0] if ps else None
