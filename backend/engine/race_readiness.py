"""
賽前完備程度 (SP-650, the integrated version of SP-304 Q3: three gates + a week calendar) for the next
A race. 圖表分析's 專項期 / 減量期 card (SP-651) draws it.

Three stages, each a set of items; a stage counts as on schedule once 2/3 of its items are met
(PASS_SHARE; 計畫完成 60–70 % 就算成功 — the coaching rule of thumb in the SP-304 notes), and every item
also has one cell per week (賽前第 10 → 1 週: target / actual / state) with the target the plan engine sets:
  ① volume (專項量, weeks 10 → 3):
     long         the week's best foot session as a share of the long-day aim (trail: the hardest day's
                  コース定数 capped at 6 h — race_feasibility.readiness' line; road: km ≤ 35); weekly
                  target specific_phase.FRAC / ROAD_FRAC; a cell done within LONG_TOL (推估); met at LONG_MET
                  (70 % — owner's choice, SP-304 Q3-3).
     weekly_long  one long run every week: a session ≥ WEEKLY_LONG_SHARE of the week's long-day target
                  (the plan's recovery-week long day, overview: 65 %); met: each of the last 4 weeks.
     long_climb   the longest continuous climb of an activity (longest_climb: specific_phase.legs on its
                  elevation) ÷ the race GPX's longest climb; cells CLIMB_MILESTONES (推估); met at LONG_MET.
                  No race GPX → gray. Only for races whose longest climb ≥ specific_phase.CLIMB_MIN_GAIN.
     terrain      a session climbing ≥ CLIMB_MIN_GAIN m at the race's metres per km ± TERRAIN_TOL (推估);
                  met: TERRAIN_N in the last 4 weeks (推估). Trail only.
     b2b          b2b.qualifies races: the weeks the b2b rules allow and the accepted ones; none →
                  race_feasibility's B2B_OK by the last week ending ≥ 21 days out.
     race_sim     specific_phase.SIM_WEEKS: one long day at race pace (is_race_sim) in the window.
  ② validation (表現驗證, weeks 6 → 3; open from week 6 or once ① passes). Needs ≥ HISTORY_DAYS of data,
     else gray (high_test excepted):
     repeat       best performances repeat: with power the 5 / 20 / 60-min bests of the last REPEAT_DAYS,
                  the top REPEAT_N's mean ÷ the best ≥ REPEAT_OK on each; without power the same on the
                  climbing speed (VAM) of the mountain sessions.
     ctl_top      chronic load in the top CTL_TOP of the athlete's own last 365 days.
     uphill       climbing speed of the last 8 weeks vs the 8 before (status.VAM_TREND).
     high_test    one hard check (a B / C race or a test day) in the window.
  ③ race state (比賽狀態): unlocks when the taper starts (planning.TAPER_DAYS before the race):
     taper_volume the taper weeks' hours at status.TAPER_BAND of the usual (the median of weeks 10 → 3).
     climb_early  賽前第 2 週's climb ≤ the band's top of the usual.
     ctl_down_days chronic load falling ≤ CTL_DOWN_MAX days in a row before the race.
     race_tsb     race-day load balance in load_guard.TSB_RACE["A"]: projected from the plan before the
                  last 3 days, the actual one in them.
Watched every week (no HRV / sleep): the chronic-load ramp (load_guard.ramp_level), injuries (the injury
log), and with self-ratings the RPE of a session against the last one of the same kind (+RPE_RISE).
Cell states: done / due (this week, not yet) / miss (a past week) / fut / tap (③ before it unlocks) /
unk (gray: no data, with the reason — never counted). The overall level: done ÷ due cells ≥ 2/3.
More than 10 weeks out: a preview (「專項期從 M/D 開始」). Display text names no source or person (owner
2026-10-16). Every threshold is a share of the race or of the athlete's own values.
"""
from __future__ import annotations

import datetime as dt
import math
from statistics import median
from typing import Iterable, Optional

from backend.engine import race_feasibility as F
from backend.engine import specific_phase as SP
from backend.engine.algorithms.chart_metrics import course_constant
from backend.i18n import N_, _

BOARD_WEEKS = 10
TAPER_FROM = 2
PASS_SHARE = 2.0 / 3.0
LONG_TOL = 0.90                      # 推估: a long day ≥ 90 % of the week's target counts
LONG_MET = 0.70                      # the long day / longest climb at 70 % of the race's (SP-304 Q3-3)
WEEKLY_LONG_SHARE = 0.65             # the plan's recovery-week long day (overview.recovery_long_minutes: 65 %)
WEEKLY_LONG_WEEKS = 4
CLIMB_MILESTONES = {9: 0.50, 7: 0.70, 6: 0.80, 4: 0.90}   # 推估
# SP-672: the race's m/km from 2/3 up to 2.5× (was ± 25 %): a steeper session trains the race's climbs at
# least as specifically, and a session a third flatter is still a hilly one (the demo's hill days sat at
# −27…−34 % and +38…+139 % of its race and were all judged 「not like the race」)
TERRAIN_LO = 2.0 / 3.0
TERRAIN_HI = 2.5
TERRAIN_N = 3                        # 推估: 3 in the last 4 weeks
SIM_PACE_TOL = 0.15                  # 推估
HISTORY_DAYS = 90
REPEAT_DAYS = 42                     # 推估: the last 6 weeks
REPEAT_N = 5
REPEAT_OK = 0.95                     # SP-672: was 0.97 — inside one ordinary day-to-day spread (~5 %) of the best
REPEAT_KEYS = ("300", "1200", "3600")
REPEAT_MAIN = ("1200", "3600", "300")   # SP-672: the 20-min best decides (was the worst of the three)
CTL_TOP = 0.20
CTL_DOWN_MAX = 7
RPE_RISE = 2                         # 推估
VALIDATION_FROM = 6

LEVEL_LABEL = {"unknown": N_("資料不足"), "on_track": N_("照進度"), "behind": N_("落後")}
NO_DATA = N_("還沒有任何跑步或健行紀錄")
NO_GPX = N_("比賽沒有 GPX：上傳 GPX 才算得出最長一段連續爬升")
SHORT_HISTORY = N_("資料不到 90 天，先不算")
BEFORE_DATA = N_("資料不足：資料從 {d} 開始")
EMPTY_WEEK = N_("資料不足：這週沒有任何活動紀錄")
# SP-672: the chart each non-count item opens (views/*.json chart ids; the card links them with a range);
# the count items (weekly_long, terrain, b2b, race_sim, high_test) have none, long_climb / repeat draw their own
CHART = {"long": "route-difficulty-specific", "long_road": "long-run-km-specific", "ctl_top": "pmc-all",
         "uphill": "uphill-vam", "taper_volume": "weekly-hours-vs-taper", "climb_early": "weekly-volume",
         "ctl_down_days": "pmc-all", "race_tsb": "form-pct"}


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def longest_climb(km: list, z: list) -> Optional[float]:
    """The biggest continuous gain (m) of a distance (km) / elevation (m) profile: specific_phase.legs
    (a dip < CLIMB_TOL_M stays inside the climb). None without a profile; 0 for a flat one."""
    pts = [(float(a), float(b)) for a, b in zip(km or [], z or []) if a is not None and b is not None
           and a == a and b == b]
    if not pts:
        return None
    ups = [x["dz"] for x in SP.legs([p[0] for p in pts], [p[1] for p in pts]) if x["dir"] > 0]
    return round(max(ups, default=0.0))


def _road(race: dict, sport: str) -> bool:
    return sport == "road" or race.get("kind") == "road"


def _aim(race: dict, road: bool) -> Optional[float]:
    """The long-day aim the ratios are shares of: road km (≤ 35), trail コース定数 capped at 6 h."""
    hd = F.hardest_day(race)
    if road:
        return min(hd["km"], F.ROAD_LONG_KM) or None
    cap = min(1.0, F.LONG_CAP_H / hd["hours"]) if hd["hours"] else 1.0
    return hd["cc"] * cap or None


def long_share(a: dict, race: dict, road: bool = False) -> Optional[float]:
    aim = _aim(race, road)
    if not aim:
        return None
    v = a["km"] if road else course_constant(a["hours"], a["km"], a["climb_m"], a.get("descent_m") or 0.0)
    return v / aim


def plan_share(a: dict, race: dict, road: bool = False) -> Optional[float]:
    """The share of the week's long day in the plan's own unit (SP-672): road km of the ≤ 35-km aim; trail
    hours of the hardest day's hours capped at 6 h (specific_phase.trail_aim sets the trail long day in
    minutes: a flat run of that length is that week's long day although its course constant is lower)."""
    if road:
        return long_share(a, race, True)
    h = F.hardest_day(race)["hours"]
    aim = min(h, F.LONG_CAP_H) if h else None
    return a["hours"] / aim if aim else None


def is_race_sim(a: dict, race: dict, target: float, road: bool = False) -> bool:
    """A race simulation: a foot session reaching LONG_TOL of the week's long-day `target` share at the
    race's own pace ± SIM_PACE_TOL (trail: hours per effort-km; road: hours per km)."""
    if not a.get("foot") or not a.get("hours"):
        return False
    r = plan_share(a, race, road)
    if r is None or r < target * LONG_TOL:
        return False
    day = race.get("day") or race
    if road:
        want = day["hours"] / day["km"] if day.get("km") else None
        got = a["hours"] / a["km"] if a.get("km") else None
    else:
        want = race.get("h_per_ekm")
        ekm = (a.get("km") or 0.0) + (a.get("climb_m") or 0.0) / 100.0
        got = a["hours"] / ekm if ekm > 0 else None
    return bool(want and got and abs(got / want - 1.0) <= SIM_PACE_TOL)


# ---------------------------------------------------------------------------
# the board
# ---------------------------------------------------------------------------



def week_mondays(start: dt.date) -> dict[int, dt.date]:
    """{賽前第 n 週: its Monday} for n = BOARD_WEEKS … 1 (specific_phase.weeks_out)."""
    rm = start - dt.timedelta(days=start.weekday())
    out = {}
    for k in range(BOARD_WEEKS + 2):
        m = rm - dt.timedelta(weeks=k)
        w = SP.weeks_out(start, m)
        if 1 <= w <= BOARD_WEEKS:
            out[w] = m
    return out


def b2b_weeks(race: dict, mons: dict[int, dt.date], accepted: Iterable = ()) -> tuple[dict[int, int], str]:
    """({week: B2B count wanted there}, "plan" | "readiness"): the accepted entries and the weeks the b2b
    rules allow; none → race_feasibility's B2B_OK by the last week ending ≥ b2b.LAST_BEFORE_DAYS out."""
    from backend.engine import b2b as B2B
    acc = {_d(a.get("week")) for a in accepted or () if a.get("week")}
    out = {w: 1 for w, m in mons.items() if m in acc}
    ev = {"id": race.get("id"), "name": race.get("name"), "start": race["start"], "days": race.get("days") or 1,
          "is_long": True}
    last = None
    for w in sorted(mons, reverse=True):
        if w < 3 or w in out:
            if w in out:
                last = (mons[w] + dt.timedelta(days=6)).isoformat()
            continue
        info = B2B.week_context(kind="specific", mode="recovery_week" if w in SP.EASY_WEEKS else "normal",
                                monday=mons[w], event=ev, last_recovery=(w + 1) in SP.EASY_WEEKS,
                                state={"count": 0, "last": last})
        if info.get("candidate"):
            out[w] = 1
            last = (mons[w] + dt.timedelta(days=6)).isoformat()
    if out:
        return out, "plan"
    start = _d(race["start"])
    ok = [w for w, m in mons.items() if (start - (m + dt.timedelta(days=6))).days >= B2B.LAST_BEFORE_DAYS]
    return ({min(ok): F.B2B_OK} if ok else {}), "readiness"




# ---------------------------------------------------------------------------
# small judges (pure)
# ---------------------------------------------------------------------------

def passed(done: int, total: int) -> bool:
    return total > 0 and done >= total * PASS_SHARE - 1e-9


def need(done: int, total: int) -> int:
    return max(0, math.ceil(total * PASS_SHARE - 1e-9) - done)


def level_of(due: int, done: int) -> str:
    if due <= 0:
        return "unknown"
    return "on_track" if done >= due * PASS_SHARE - 1e-9 else "behind"


def repeat_ratio(perf: dict, day: dt.date) -> Optional[tuple[str, float]]:
    """(mode, ratio, key) of the best performances of the REPEAT_DAYS up to `day`: top REPEAT_N mean ÷ best,
    with power the first of REPEAT_MAIN (20 → 60 → 5 min) that has REPEAT_N sessions, else climbing speed
    (key "vam"). None without REPEAT_N sessions."""
    lo = day - dt.timedelta(days=REPEAT_DAYS)

    def ratio(vals: list[float]) -> Optional[float]:
        vals = sorted((v for v in vals if v), reverse=True)
        if len(vals) < REPEAT_N:
            return None
        return sum(vals[:REPEAT_N]) / REPEAT_N / vals[0]
    pw = [r for r in (perf or {}).get("power") or () if lo < _d(r["date"]) <= day]
    for k in REPEAT_MAIN:
        x = ratio([r.get(k) for r in pw])
        if x is not None:
            return "power", x, k
    v = ratio([r["vam"] for r in (perf or {}).get("vam") or () if lo < _d(r["date"]) <= day])
    return ("climb", v, "vam") if v is not None else None


def repeat_points(perf: dict, day: dt.date) -> dict:
    """The repeat chart's points (SP-672): every session of the REPEAT_DAYS up to `day`, {key: [{date, v}]}
    for the 5 / 20 / 60-min power bests, or {"vam": [...]} without power."""
    lo = day - dt.timedelta(days=REPEAT_DAYS)
    pw = sorted((r for r in (perf or {}).get("power") or () if lo < _d(r["date"]) <= day), key=lambda r: _d(r["date"]))
    if any(r.get(k) for r in pw for k in REPEAT_KEYS):
        return {k: [{"date": _d(r["date"]).isoformat(), "v": round(r[k], 1)} for r in pw if r.get(k)] for k in REPEAT_KEYS}
    vm = sorted((r for r in (perf or {}).get("vam") or () if lo < _d(r["date"]) <= day), key=lambda r: _d(r["date"]))
    return {"vam": [{"date": _d(r["date"]).isoformat(), "v": round(r["vam"])} for r in vm if r.get("vam")]}


def ctl_rank(ctl: list, day: dt.date) -> Optional[float]:
    """The share of the athlete's last 365 days' chronic load at or below `day`'s (1 = the year's top)."""
    xs = [(d, v) for d, v in ctl or () if v is not None and day - dt.timedelta(days=365) < _d(d) <= day]
    if len(xs) < HISTORY_DAYS:
        return None
    now = max(xs, key=lambda x: _d(x[0]))[1]
    return sum(1 for _d0, v in xs if v <= now) / len(xs)


def uphill_change(vam: list, day: dt.date) -> Optional[float]:
    """Mean climbing speed of the 8 weeks up to `day` ÷ the 8 weeks before − 1 (≥ 2 sessions each)."""
    a = [r["vam"] for r in vam or () if day - dt.timedelta(days=56) < _d(r["date"]) <= day]
    b = [r["vam"] for r in vam or () if day - dt.timedelta(days=112) < _d(r["date"]) <= day - dt.timedelta(days=56)]
    if len(a) < 2 or len(b) < 2:
        return None
    return (sum(a) / len(a)) / (sum(b) / len(b)) - 1.0


def ctl_down_days(ctl: list, day: dt.date) -> Optional[int]:
    """Days in a row up to `day` with the chronic load lower than the day before."""
    s = sorted(((_d(d), v) for d, v in ctl or () if v is not None and _d(d) <= day), key=lambda x: x[0])
    if len(s) < 2:
        return None
    n = 0
    for (d0, v0), (d1, v1) in zip(reversed(s[:-1]), reversed(s[1:])):
        if v1 < v0:
            n += 1
        else:
            break
    return n


def _pct(x: Optional[float]) -> str:
    return "" if x is None else f"{x * 100:.0f}%"


# ---------------------------------------------------------------------------
# the board
# ---------------------------------------------------------------------------

def board(race: Optional[dict], today: dt.date, acts: list[dict], *, sport: str = "trail", b2b: bool = False,
          accepted_b2b: Iterable = (), perf: Optional[dict] = None, ctl: Optional[list] = None,
          tests: Iterable = (), race_tsb: Optional[dict] = None, injuries: Iterable = (), rpe: Iterable = (),
          first_day: Optional[dt.date] = None) -> dict:
    """The board of `race` (specific_phase.race_day's dict; None = no next A race). `acts`: activity_rows
    (+ climb_dz) from 賽前第 10 週 on; `perf`: {"power": [{date, "300", "1200", "3600"}], "vam": [{date, vam}]}
    (the last ~4 months); `ctl`: [(date, chronic load)] daily, ≥ a year when there is one; `tests`: dates of
    a hard check (B / C race, test); `race_tsb`: {"share", "source": plan | actual}; `rpe`: [{date, kind,
    rpe}]; `first_day`: the first activity on record."""
    base = {"race": None, "weeks": [], "stages": [], "weekly": [], "due": 0, "done": 0, "level": "unknown",
            "label": _(LEVEL_LABEL["unknown"]), "this_week": [], "preview": False}
    if race is None:
        return {**base, "text": _("沒有下一場 A 賽事")}
    from backend.engine import load_guard as LG
    from backend.engine import planning as P
    from backend.engine.status import TAPER_BAND, VAM_TREND
    start = _d(race["start"])
    road = _road(race, sport)
    mons = week_mondays(start)
    sun = {w: m + dt.timedelta(days=6) for w, m in mons.items()}
    cur = SP.weeks_out(start, today - dt.timedelta(days=today.weekday()))
    preview = cur > BOARD_WEEKS
    foot = [a for a in acts if a.get("foot") and _d(a["date"]) <= today]
    has_data = bool(foot)
    long_hist = first_day is not None and (today - first_day).days >= HISTORY_DAYS
    fr = SP.ROAD_FRAC if road else SP.FRAC
    hd = F.hardest_day(race)
    taper_from = start - dt.timedelta(days=P.TAPER_DAYS)

    def asof(w: int) -> dt.date:
        return min(sun[w], today)

    def wk(w: int) -> list[dict]:
        return [a for a in foot if mons[w] <= _d(a["date"]) <= sun[w]]

    def no_data(w: int) -> Optional[str]:
        """SP-672: a past week before the first activity on record, or without any activity (any sport)."""
        if w <= cur or not has_data:
            return None
        if first_day is not None and sun[w] < first_day:
            return _(BEFORE_DATA, d=f"{first_day.month}/{first_day.day}")
        if not any(mons[w] <= _d(a["date"]) <= sun[w] for a in acts):
            return _(EMPTY_WEEK)
        return None

    def judge(w: int, ok: bool, unknown: Optional[str] = None, lock: bool = False) -> tuple[str, bool, Optional[str]]:
        if preview or w < cur:
            return ("tap" if lock else "fut"), False, None
        if lock:
            return "tap", False, None
        if ok:
            return "done", True, None
        nd = no_data(w)
        if unknown or not has_data or nd:
            return "unk", False, nd
        if w == cur:
            return "due", False, None
        return "miss", True, None

    def mk(w: int, ok: bool, show: str, detail: str, target=None, actual=None, unknown: Optional[str] = None,
           lock: bool = False, aim: Optional[str] = None, **kw) -> dict:
        """A cell; `aim`: what a later / locked cell shows (its target, SP-672: the card no longer works it out)."""
        st, counts, nd = judge(w, ok, unknown, lock)
        if st == "unk":
            detail = nd or unknown or _(NO_DATA)
        if st in ("fut", "tap") and aim is not None:
            show = aim
        return {"state": st, "show": show, "detail": detail, "target": target, "actual": actual, "counts": counts, **kw}

    def span(begin: dt.date, end: dt.date) -> dict:
        return {"begin": begin.isoformat(), "end": max(begin, end).isoformat()}
    board_span = span(mons[BOARD_WEEKS], min(today, start) if not preview else mons[BOARD_WEEKS] + dt.timedelta(days=6))

    def item(iid: str, name: str, crit: str, cells: dict, met: Optional[bool], reason: Optional[str] = None,
             **kw) -> dict:
        if not has_data and reason is None and met is not True:
            met, reason = None, _(NO_DATA)
        return {"id": iid, "name": name, "crit": crit, "met": met, "reason": reason, "cells": cells, **kw}

    # ---- ① volume ----------------------------------------------------------------------------
    vol = []
    spec = [w for w in sorted(mons, reverse=True) if w in fr]
    # SP-672: the week's cell in the plan's own unit (plan_share: trail hours, road km); the item's met stays
    # the difficulty (course constant ≥ 70 % of the hardest day, owner's SP-304 Q3-3)
    shares = {w: max((plan_share(a, race, road) or 0.0 for a in wk(w)), default=None) for w in spec}
    aim_h = min(hd["hours"], F.LONG_CAP_H) if hd["hours"] else 0.0
    aim_km = min(hd["km"], F.ROAD_LONG_KM)

    def amount(x: Optional[float]) -> str:
        if x is None:
            return "—"
        return _("{v} km", v=f"{x * aim_km:.0f}") if road else _("{v} 小時", v=f"{x * aim_h:.1f}")
    cells = {}
    for w in spec:
        t, b = fr[w], shares[w]
        cells[w] = mk(w, bool(b and b >= t * LONG_TOL), _pct(t),
                      _("目標 {t}（{a}），做到 {b}", t=_pct(t), a=amount(t), b=amount(b)), t,
                      None if b is None else round(b, 3))
    best = max((long_share(a, race, road) or 0.0 for w in spec for a in wk(w)), default=0.0)
    vol.append(item("long", _("長天"), _("最硬一次 ≥ 比賽最難那天的 70 %"), cells, best >= LONG_MET, best=round(best, 3),
                    chart={"id": CHART["long_road" if road else "long"], **board_span}))

    cells, hits = {}, {}
    for w in spec:
        hits[w] = any((plan_share(a, race, road) or 0.0) >= fr[w] * WEEKLY_LONG_SHARE for a in wk(w))
        cells[w] = mk(w, hits[w], "1", _("這週一次長距離"), 1, int(hits[w]))
    past = [w for w in spec if w >= cur][-WEEKLY_LONG_WEEKS:] if not preview else []
    done_past = [w for w in past if w > cur or hits[w]]
    vol.append(item("weekly_long", _("每週一次長距離"), _("近 4 週每週 1 次"), cells,
                    bool(done_past) and all(hits[w] for w in done_past)))

    feat = (race.get("features") or {}).get("climb") or {}
    race_dz = float(feat.get("dz") or 0.0)
    if not road and (race.get("features") is None or race_dz >= SP.CLIMB_MIN_GAIN):
        cells, so_far = {}, 0.0
        for w in sorted(CLIMB_MILESTONES, reverse=True):
            t = CLIMB_MILESTONES[w]
            if not race_dz:
                cells[w] = {"state": "unk", "show": _pct(t), "detail": _(NO_GPX), "target": t, "actual": None,
                            "counts": False}
                continue
            m = max((a["climb_dz"] for a in wk(w) if a.get("climb_dz") is not None), default=None)
            so_far = max(so_far, (m or 0.0) / race_dz)
            cells[w] = mk(w, so_far >= t, _pct(t),
                          _("目標 {t} m，做到 {m} m", t=round(t * race_dz), m=round(m) if m is not None else "—"),
                          t, None if m is None else round(m / race_dz, 3), actual_m=m, target_m=round(t * race_dz))
        allm = max((a["climb_dz"] for a in foot if a.get("climb_dz") is not None), default=0.0)
        # SP-672: its own chart — every week's longest continuous climb against the race's and the milestones
        series = [{"week": w, "monday": mons[w].isoformat(),
                   "m": max((a["climb_dz"] for a in wk(w) if a.get("climb_dz") is not None), default=None)}
                  for w in sorted(mons, reverse=True) if not preview and w >= cur]
        own = {"own": "long_climb"}
        if race_dz:
            vol.append(item("long_climb", _("長爬坡（最長一段）"),
                            _("到比賽最長一段 {m} m 的 70 %", m=round(race_dz)), cells, allm / race_dz >= LONG_MET,
                            race_climb_m=round(race_dz), best_m=round(allm), series=series, chart=own,
                            milestones={str(w): round(t * race_dz) for w, t in CLIMB_MILESTONES.items()}))
        else:
            vol.append({"id": "long_climb", "name": _("長爬坡（最長一段）"), "crit": _("到比賽最長一段的 70 %"),
                        "met": None, "reason": _(NO_GPX), "cells": cells, "series": series, "chart": own})

    if not road and hd["km"]:
        mpk = hd["climb_m"] / hd["km"]

        def similar(a: dict) -> bool:
            return (bool(a["km"]) and a["climb_m"] >= SP.CLIMB_MIN_GAIN
                    and TERRAIN_LO - 1e-9 <= a["climb_m"] / a["km"] / mpk <= TERRAIN_HI + 1e-9)
        cells = {}
        for w in spec:
            n = sum(1 for a in wk(w) if similar(a))
            cells[w] = mk(w, n >= 1, "1", _("坡度相近的爬坡課 {n} 次", n=n), 1, n)
        n4 = sum(1 for a in foot if similar(a) and _d(a["date"]) > today - dt.timedelta(days=28))
        vol.append(item("terrain", _("像比賽的地形"), _("近 4 週 {k} 次每公里爬升 {lo}–{hi} m 的爬坡課",
                                                    k=TERRAIN_N, lo=round(mpk * TERRAIN_LO), hi=round(mpk * TERRAIN_HI)),
                        cells, n4 >= TERRAIN_N))

    if b2b and not road:
        from backend.engine import b2b as B2B
        want, src = b2b_weeks(race, mons, accepted_b2b)
        starts = [_d(d["start"]) for d in B2B.detect((a["date"], a["minutes"], a.get("idx")) for a in foot)]
        lo = start - dt.timedelta(weeks=F.B2B_WEEKS)
        cells = {}
        for w in sorted(want, reverse=True):
            n = sum(1 for s in starts if mons[w] <= s <= sun[w])
            got = sum(1 for s in starts if lo <= s <= sun[w]) if src == "readiness" else n
            cells[w] = mk(w, got >= want[w], str(want[w]), _("做到 {n} 次", n=got), want[w], got)
        total = F.B2B_OK if src == "readiness" else sum(want.values())
        vol.append(item("b2b", _("背對背長天（B2B）"),
                        _("賽前 3 週以前做到 {n} 次", n=total), cells,
                        sum(1 for s in starts if s >= lo) >= total if want else None,
                        None if want else _("這場比賽課表沒有排 B2B"), target_source=src))

    sw = sorted(SP.sim_week_numbers("road" if road else "trail"), reverse=True)
    hit = {w: any(is_race_sim(a, race, fr.get(w, 0.0), road) for a in wk(w)) for w in sw}
    cells = {}
    for w in sw:
        c = mk(w, any(hit[x] for x in sw if x >= w), "1", _("照比賽配速的長天"), 1, int(hit[w]))
        if w != sw[-1]:
            c["counts"] = False
            if c["state"] == "miss":
                c["detail"] = _("賽前第 {w} 週還可以做", w=sw[-1])
        cells[w] = c
    vol.append(item("race_sim", _("賽事模擬"), _("第 {a} 或 {b} 週一次照比賽配速的長天", a=sw[0], b=sw[-1]), cells,
                    any(hit.values())))

    # ---- ② validation --------------------------------------------------------------------------
    val = []
    vw = [w for w in sorted(mons, reverse=True) if 3 <= w <= VALIDATION_FROM]
    short = None if long_hist else _(SHORT_HISTORY)
    cells = {}
    for w in vw:
        r = repeat_ratio(perf or {}, asof(w)) if not short else None
        cells[w] = mk(w, bool(r and r[1] >= REPEAT_OK), _pct(r[1]) if r else "",
                      _("前 5 次平均是最高的 {p}", p=_pct(r[1])) if r else (short or _("最近 6 週不到 5 次可比的表現")),
                      REPEAT_OK, None if not r else round(r[1], 3),
                      unknown=None if r else (short or _("最近 6 週不到 5 次可比的表現")), aim="≥" + _pct(REPEAT_OK))
    r = repeat_ratio(perf or {}, today) if not short else None
    val.append(item("repeat", _("最佳表現重複得出來"), _("前 5 次平均 ≥ 最高的 {p}", p=_pct(REPEAT_OK)), cells,
                    None if r is None else r[1] >= REPEAT_OK,
                    None if r else (short or _("最近 6 週不到 5 次可比的表現")), mode=r[0] if r else None,
                    key=r[2] if r else None, ratio=round(r[1], 3) if r else None, ok=REPEAT_OK, top_n=REPEAT_N,
                    points=repeat_points(perf or {}, today), chart={"own": "repeat"}))
    cells = {}

    def top(k: float) -> str:
        return _("最高") if 1 - k < 0.005 else _("前 {p}", p=_pct(1 - k))
    for w in vw:
        k = ctl_rank(ctl, asof(w)) if not short else None
        cells[w] = mk(w, bool(k is not None and k >= 1 - CTL_TOP), "" if k is None else top(k),
                      "" if k is None else _("在自己近 365 天的{p}", p=top(k)), 1 - CTL_TOP,
                      None if k is None else round(k, 3), unknown=None if k is not None else (short or _(SHORT_HISTORY)),
                      aim=_("前 {p}", p=_pct(CTL_TOP)))
    k = ctl_rank(ctl, today) if not short else None
    val.append(item("ctl_top", _("長期負荷到自己的高區"), _("近 365 天的前 20 %"), cells,
                    None if k is None else k >= 1 - CTL_TOP, None if k is not None else (short or _(SHORT_HISTORY)),
                    chart={"id": CHART["ctl_top"], **span(today - dt.timedelta(days=364), today)}))
    cells, vam = {}, (perf or {}).get("vam")
    for w in vw:
        ch = uphill_change(vam, asof(w)) if not short else None
        cells[w] = mk(w, bool(ch is not None and ch >= VAM_TREND), "" if ch is None else f"{ch * 100:+.0f}%",
                      "" if ch is None else _("每小時爬升比 8 週前 {p}", p=f"{ch * 100:+.0f}%"), VAM_TREND,
                      None if ch is None else round(ch, 3),
                      unknown=None if ch is not None else (short or _("8 週內或 8 週前的爬坡紀錄不到 2 次")),
                      aim=f"≥+{VAM_TREND * 100:.0f}%")
    ch = uphill_change(vam, today) if not short else None
    val.append(item("uphill", _("上坡腳程變快"), _("比 8 週前快"), cells, None if ch is None else ch >= VAM_TREND,
                    None if ch is not None else (short or _("8 週內或 8 週前的爬坡紀錄不到 2 次")),
                    chart={"id": CHART["uphill"], **span(today - dt.timedelta(days=111), today)}))
    tds = sorted(_d(t) for t in tests or ())
    cells = {}
    for w in vw:
        got = any(mons[VALIDATION_FROM] <= t <= asof(w) for t in tds) if VALIDATION_FROM in mons else False
        c = mk(w, got, "1", _("模擬賽或測驗一次"), 1, int(got))
        if w != vw[-1]:
            c["counts"] = False
        cells[w] = c
    val.append(item("high_test", _("高區做一次驗證"), _("模擬賽或測驗"), cells,
                    any(mons.get(VALIDATION_FROM, today) <= t <= today for t in tds)))

    # ---- ③ race state --------------------------------------------------------------------------
    lock = today < taper_from
    usual_h = [x for x in (sum(a["hours"] for a in wk(w)) for w in spec) if x > 0]
    usual_c = [x for x in (sum(a["climb_m"] for a in wk(w)) for w in spec) if x > 0]
    nh = median(usual_h) if usual_h else None
    nc = median(usual_c) if usual_c else None
    band_t = f"{TAPER_BAND[0] * 100:.0f}–{TAPER_BAND[1] * 100:.0f}%"
    taper_span = span(mons[BOARD_WEEKS], start)
    rs = []
    cells, oks = {}, []
    for w in (2, 1):
        if w not in mons:
            continue
        sh = sum(a["hours"] for a in wk(w)) / nh if nh else None
        ok = sh is not None and TAPER_BAND[0] <= sh <= TAPER_BAND[1] and w > cur
        cells[w] = mk(w, ok, _pct(sh), _("平常的 {p}", p=_pct(sh)) if sh is not None else "", list(TAPER_BAND),
                      None if sh is None else round(sh, 3), unknown=None if nh else _(NO_DATA), lock=lock, aim=band_t)
        oks.append(ok)
    rs.append(item("taper_volume", _("量減到平常的 {b}", b=band_t), _("強度保留"), cells,
                   None if lock else all(oks), chart={"id": CHART["taper_volume"], **taper_span}))
    cells = {}
    shc = sum(a["climb_m"] for a in wk(2)) / nc if nc and 2 in mons else None
    okc = shc is not None and shc <= TAPER_BAND[1] and (2 > cur)
    if 2 in mons:
        cells[2] = mk(2, okc, _pct(shc), _("爬升是平常的 {p}", p=_pct(shc)) if shc is not None else "",
                      TAPER_BAND[1], None if shc is None else round(shc, 3), unknown=None if nc else _(NO_DATA), lock=lock,
                      aim="≤" + _pct(TAPER_BAND[1]))
    rs.append(item("climb_early", _("爬升量提早減"), _("賽前 2 週開始"), cells, None if lock else okc,
                   chart={"id": CHART["climb_early"], **taper_span}))
    cells = {}
    dd = ctl_down_days(ctl, min(today, start - dt.timedelta(days=1)))
    okd = dd is not None and dd <= CTL_DOWN_MAX and today >= start - dt.timedelta(days=1)
    if 1 in mons:
        cells[1] = mk(1, okd, "" if dd is None else _("{n} 天", n=dd),
                      "" if dd is None else _("長期負荷已經連續下降 {n} 天", n=dd), CTL_DOWN_MAX, dd,
                      unknown=None if dd is not None else _("還沒有長期負荷資料"), lock=lock, aim=f"≤{CTL_DOWN_MAX}")
    rs.append(item("ctl_down_days", _("長期負荷往下不超過 7 天"), "", cells, None if lock else okd,
                   chart={"id": CHART["ctl_down_days"], **span(taper_from - dt.timedelta(days=28), start)}))
    band = LG.TSB_RACE["A"]
    cells = {}
    share = (race_tsb or {}).get("share")
    okt = share is not None and band[0] <= share <= band[1]
    if 1 in mons:
        src_txt = _("課表推算") if (race_tsb or {}).get("source") == "plan" else _("實際")
        cells[1] = mk(1, okt and (race_tsb or {}).get("source") == "actual",
                      "" if share is None else f"{share * 100:+.0f}%",
                      _("賽前 3 天看實際值") if share is None else _("{s}：長期負荷的 {p}", s=src_txt, p=f"{share * 100:+.0f}%"),
                      list(band), share, unknown=None if share is not None else _("課表還沒排到比賽日，推算不出來"),
                      lock=lock, aim=f"{band[0] * 100:+.0f}–{band[1] * 100:+.0f}%")
    rs.append(item("race_tsb", _("比賽日負荷平衡"), _("長期負荷的 +10～+35 %"), cells, None if lock else okt,
                   source=(race_tsb or {}).get("source"), chart={"id": CHART["race_tsb"], **span(taper_from - dt.timedelta(days=28), start)}))

    # ---- stages --------------------------------------------------------------------------------
    def stage(sid: str, name: str, span: str, items: list[dict], unlocked: bool, unlock: dt.date) -> dict:
        tot = sum(1 for i in items if i["met"] is not None)
        dn = sum(1 for i in items if i["met"] is True)
        ok = passed(dn, tot)
        st = "lock" if not unlocked else "passed" if ok else "open"
        label = (_("{d} 解鎖", d=f"{unlock.month}/{unlock.day}") if st == "lock" else _("照進度") if ok
                 else _("還差 {n} 項", n=need(dn, tot)) if tot else _("資料不足"))
        return {"id": sid, "name": name, "span": span, "items": items, "done": dn, "total": tot, "passed": ok,
                "need": need(dn, tot), "unlocked": unlocked, "unlock_date": unlock.isoformat(), "state": st,
                "label": label}
    s1 = stage("volume", _("專項量"), _("賽前 10 → 3 週"), vol, not preview, mons[BOARD_WEEKS])
    s2 = stage("validation", _("表現驗證"), _("賽前 6 → 3 週"), val,
               not preview and (cur <= VALIDATION_FROM or s1["passed"]), mons.get(VALIDATION_FROM, today))
    s3 = stage("race_state", _("比賽狀態"), _("賽前 10 → 0 天"), rs, not lock, taper_from)
    stages = [s1, s2, s3]

    # ---- every week --------------------------------------------------------------------------
    weekly = []
    cs = {_d(d): v for d, v in ctl or () if v is not None}
    cells = {}
    for w in sorted(mons, reverse=True):
        if preview or w < cur:
            continue
        d = asof(w)
        a, b = cs.get(d), cs.get(d - dt.timedelta(days=7))
        if a is None or b is None:
            continue
        ramp = a - b
        lv = LG.ramp_level(ramp, b)
        cells[w] = {"state": "bad" if lv == LG.BLOCK else "watch" if lv == LG.WATCH else "ok",
                    "show": f"{ramp:+.1f}", "detail": _("長期負荷一週 {r}", r=f"{ramp:+.1f}"), "actual": round(ramp, 2)}
    weekly.append({"id": "ramp", "name": _("長期負荷週增量"), "cells": cells})
    from backend.engine import injuries as INJ
    cells, inj = {}, list(injuries or ())
    for w in sorted(mons, reverse=True):
        if preview or w < cur:
            continue
        on = [e for e in inj if any(e in INJ.active_on([e], mons[w] + dt.timedelta(days=k))
                                    for k in range((asof(w) - mons[w]).days + 1))]
        st = ("bad" if any(e.get("severity") in ("moderate", "severe") for e in on) else "watch") if on else "ok"
        cells[w] = {"state": st, "show": "" if not on else str(len(on)),
                    "detail": _("進行中：{x}", x=_("、").join(INJ.full_label(e.get("area"), e.get("side")) for e in on))
                    if on else _("沒有進行中的傷病")}
    weekly.append({"id": "injury", "name": _("傷病"), "cells": cells})
    rated = sorted(({"date": _d(r["date"]), "kind": r.get("kind"), "rpe": float(r["rpe"])} for r in rpe or ()
                    if r.get("rpe") is not None), key=lambda r: r["date"])
    if rated:
        cells = {}
        for i, r in enumerate(rated):
            prev = next((p for p in reversed(rated[:i]) if p["kind"] == r["kind"]), None)
            w = SP.weeks_out(start, r["date"] - dt.timedelta(days=r["date"].weekday()))
            if prev is None or w not in mons or w < cur:
                continue
            up = r["rpe"] - prev["rpe"]
            c = cells.get(w)
            if c is None or up > c["actual"]:
                cells[w] = {"state": "watch" if up >= RPE_RISE else "ok", "show": f"{up:+.0f}",
                            "detail": _("同一種課的自評比上次 {d}", d=f"{up:+.0f}"), "actual": up}
        weekly.append({"id": "rpe", "name": _("自評 RPE"), "cells": cells})

    # ---- totals --------------------------------------------------------------------------------
    allc = [c for s in stages for i in s["items"] for c in i["cells"].values()]
    due = sum(1 for c in allc if c["counts"])
    done = sum(1 for c in allc if c["counts"] and c["state"] == "done")
    lv = "unknown" if preview else level_of(due, done)
    this_week = [] if preview else [
        {"stage": s["id"], "item": i["id"], "name": i["name"], "detail": i["cells"][cur]["detail"],
         "target": i["cells"][cur]["target"]}
        for s in stages for i in s["items"] if (i["cells"].get(cur) or {}).get("state") == "due"]
    weeks = [{"week": w, "monday": mons[w].isoformat(), "phase": "taper" if w <= TAPER_FROM else "specific",
              "current": w == cur} for w in sorted(mons, reverse=True)]
    out = {**base, "race": {"id": race.get("id"), "name": race.get("name"), "start": start.isoformat(),
                            "kind": race.get("kind"), "sport": "road" if road else "trail"},
           "today": today.isoformat(), "weeks_out": cur, "preview": preview,
           "specific_from": mons[BOARD_WEEKS].isoformat(), "taper_from": taper_from.isoformat(),
           "weeks": weeks, "stages": stages, "weekly": weekly, "due": due, "done": done, "level": lv,
           "label": _(LEVEL_LABEL[lv]), "this_week": this_week}
    if preview:
        out["text"] = _("專項期從 {d} 開始", d=f"{mons[BOARD_WEEKS].month}/{mons[BOARD_WEEKS].day}")
    elif due:
        out["text"] = _("到上週為止該達成 {n} 個、做到 {m} 個", n=due, m=done)
    else:
        out["text"] = _(NO_DATA) if not has_data else _("還沒有到期的目標")
    return out


def line(bd: dict) -> dict:
    """The one sentence of the board for the 賽事評估 card on the season-plan page (SP-652): the stage it
    is in (the first unlocked one not passed; all passed: the last unlocked one) with done / total and its
    label; a preview or no next A race: the board's text. `phase`: which 圖表分析 page has the card
    (taper once the taper started, else build)."""
    rc = bd.get("race")
    today, tf = bd.get("today"), bd.get("taper_from")
    phase = "taper" if today and tf and today >= tf else "build"
    out = {"race_id": (rc or {}).get("id"), "phase": phase, "text": bd.get("text") or ""}
    stages = [s for s in bd.get("stages") or () if s.get("unlocked")]
    if rc is None or bd.get("preview") or not stages:
        return out
    cur = next((s for s in stages if not s.get("passed")), stages[-1])
    n = (bd.get("stages") or []).index(cur) + 1
    out["text"] = _("賽前準備：第 {n} 段〈{name}〉{d}/{t}（{label}）", n=n, name=cur["name"], d=cur["done"],
                    t=cur["total"], label=cur["label"])
    return out


# ---------------------------------------------------------------------------
# the Dataset side
# ---------------------------------------------------------------------------
SERIES_KEY = "readiness_longest_climb_v1"


def activity_rows(ds, since: dt.date, today: dt.date) -> list[dict]:
    """race_feasibility.activity_rows from `since`, the foot ones with `climb_dz` (disk-cached per file)."""
    from backend.engine import overview as O
    rows = F.activity_rows(ds, today, (today - since).days)
    by = {w.idx: w for w in O.workouts_between(ds, since, today + dt.timedelta(days=1))}

    def climb(w) -> Optional[float]:
        d = ds.channel(w.idx, "elapseddistance")
        z = ds.channel(w.idx, "_elevation")
        if z is None:
            z = ds.channel(w.idx, "elevation")
        if d is None or z is None:
            return None
        return longest_climb(list(d), list(z))
    for r in rows:
        w = by.get(r["idx"])
        r["climb_dz"] = None
        if not r["foot"] or w is None or not r["climb_m"]:
            continue
        f = getattr(ds, "cached_series", None)
        try:
            r["climb_dz"] = f(SERIES_KEY, w, lambda w=w: climb(w)) if f else climb(w)
        except Exception:                   # noqa: BLE001 — a broken file = no elevation
            r["climb_dz"] = None
    return rows




def _safe(f, default):
    try:
        return f()
    except Exception:                       # noqa: BLE001 — a missing input = that item gray
        return default


def inputs(ds, plan, today: dt.date, since: dt.date, start: Optional[dt.date] = None) -> dict:
    """board()'s data inputs from the Dataset: activities (+ longest climb), best power / climbing speed,
    chronic load, hard checks, race-day load balance, injuries, self-ratings, the first activity."""
    from backend.engine import activity_tags as AT
    from backend.engine import injuries as INJ
    from backend.engine import load_guard as LG
    from backend.engine import overview as O
    from backend.engine.status import Status
    from backend.engine.wko5expr.dataset import date_to_day, day_to_date
    out: dict = {"acts": _safe(lambda: activity_rows(ds, since, today), [])}
    st = Status(ds, plan, today)
    ctl_s, atl_s, _st = st.ev.pmc()
    days = range(int(date_to_day(today - dt.timedelta(days=400))), int(date_to_day(today)) + 1)
    out["ctl"] = [(day_to_date(d), v) for d in days for v in (ctl_s.at(d),) if v is not None and v == v]
    perf_lo = today - dt.timedelta(days=120)
    vam, power = [], []
    for w in st.between(120, 0):
        day = day_to_date(math.floor(w.day))
        if st.mountain(w) and (st.m(w, "climbing") or 0) > 200 and st.m(w, "vam"):
            vam.append({"date": day, "vam": st.m(w, "vam")})
        if O.category(w) in O.FOOT and day >= perf_lo:
            cur = _safe(lambda w=w: ds.workout_curve(w.idx, "power"), None)
            if cur and cur[0]:
                xs, ys = cur
                power.append({"date": day, **{k: next((y for x, y in zip(xs, ys) if x >= int(k)), None)
                                              for k in REPEAT_KEYS}})
    out["perf"] = {"power": power, "vam": vam}
    lo = since
    ev_days = {e.start for e in plan.events if e.priority in ("B", "C") and lo <= e.start <= today}
    out["tests"] = [O.wdate(w) for w in O.workouts_between(ds, lo, today + dt.timedelta(days=1))
                    if O.wdate(w) in ev_days or (O.session_of(ds, w).get("type") or "").endswith("test")]
    out["injuries"] = _safe(INJ.load_events, [])
    recorded = _safe(AT.load_recorded, [])
    rpe = []
    for w in O.workouts_between(ds, lo, today + dt.timedelta(days=1)):
        if O.category(w) not in O.FOOT:
            continue
        r = AT.recorded_of(recorded, w.entry.start, getattr(w.entry, "file", None)) if recorded else None
        if r and r.get("rpe") is not None:
            rpe.append({"date": O.wdate(w), "kind": O.session_of(ds, w).get("type") or O.category(w), "rpe": r["rpe"]})
    out["rpe"] = rpe
    out["first_day"] = _safe(lambda: day_to_date(min(w.day for w in ds.workouts)), None)
    if start is not None:
        def race_tsb():
            if (start - today).days <= 3:
                d = int(date_to_day(today))
                tsb, c = ctl_s.at(d - 1) - atl_s.at(d - 1), ctl_s.at(d - 1)
                return {"share": LG.tsb_share(tsb, c), "source": "actual"}
            from backend.engine.overview import project
            from backend.engine.panels import plan_projection as PJ
            pj = PJ.inputs(today, start)
            if not pj or pj["until"] < start.isoformat():
                return None
            d = int(date_to_day(today))
            rows = project(ctl_s.at(d), atl_s.at(d), [x for _d0, x in pj["days"]],
                           float(ds.athlete.ctlconstant), float(ds.athlete.atlconstant))
            i = [x for x, _y in pj["days"]].index(start.isoformat())
            prev = ctl_s.at(d) if i == 0 else rows[i - 1]["ctl"]
            return {"share": LG.tsb_share(rows[i]["tsb"], prev), "source": "plan"}
        out["race_tsb"] = _safe(race_tsb, None)
    return out


def compute(plan, ds, today: dt.date, predict=None) -> dict:
    """The board of the next A race from the Dataset (SP-651's panel calls this)."""
    from backend.engine import b2b as B2B
    from backend.engine import primary_sport as PSP
    from backend.engine.panels import race_refs as RR
    rc = SP.race_day(plan, today, predict or RR.calculator_hours)
    if rc is None:
        return board(None, today, [])
    e = next((x for x in plan.events if x.id == rc["id"]), None)
    start = _d(rc["start"])
    mons = week_mondays(start)
    since = min(min(mons.values()), today)
    data = inputs(ds, plan, today, since, start)
    return board(rc, today, data.pop("acts", []), sport=PSP.effective(ds, plan.events, today),
                 b2b=bool(e and B2B.qualifies(e) and e.kind != "road"), accepted_b2b=B2B.load_accepted(), **data)
