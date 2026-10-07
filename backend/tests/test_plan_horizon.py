"""
SP-327: the stored plan's horizon at a phase boundary (api/plan_sessions.horizon_of) and the
uids of the stored sessions across it — the current phase ends within 14 days: the horizon
reaches the next phase's end (still ≤ 8 weeks), so the later weeks are never removed on the
phase's last days and re-added under new uids the day after (calendar feed / COROS: deleted,
then created again). 整個周期 (phase_push_end) stays the current phase. Pure: phases and
generated weeks built by hand.
"""
import datetime as dt
from datetime import date

import pytest

from backend.api import plan_sessions as API
from backend.engine import projection as P
from backend.engine import reconcile as R
from backend.tests.test_plan_store import cur_plan, g

D = dt.timedelta
MON, TODAY = date(2026, 10, 5), date(2026, 10, 7)       # a Wednesday: this week 10/5–10/11
FLOOR, CAP = date(2026, 10, 18), date(2026, 12, 6)       # end of next week / MAX_WEEKS after this week
NEXT_END = date(2026, 11, 22)


def mon_of(d: date) -> date:
    return d - D(days=d.weekday())


def two(cur_end: date, next_end=None) -> list[dict]:
    """基礎期 to `cur_end`, then (optionally) 專項期 to `next_end`."""
    ps = [{"kind": "recovery", "start": "2026-06-01", "end": "2026-07-31"},
          {"kind": "base", "start": "2026-08-01", "end": cur_end.isoformat()}]
    if next_end is not None:
        ps.append({"kind": "specific", "start": (cur_end + D(days=1)).isoformat(), "end": next_end.isoformat()})
    return ps


def horizon(today: date, cur_end: date, next_end=None) -> date:
    return API.horizon_of(mon_of(today), today, cur_end, two(cur_end, next_end))


# ---------------------------------------------------------------------------
# horizon_of
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("left, want", [
    (0, NEXT_END),                  # the phase's last day (was 10/18, the two-week floor)
    (3, NEXT_END),                  # was 10/18
    (14, NEXT_END),                 # was 10/21
    (15, TODAY + D(days=15)),       # more than 14 days left: the phase's own end, as before
    (20, TODAY + D(days=20)),
])
def test_phase_ending_within_14_days_plans_through_the_next_phase(left, want):
    assert horizon(TODAY, TODAY + D(days=left), NEXT_END) == want


@pytest.mark.parametrize("left, want", [(0, FLOOR), (3, FLOOR), (14, TODAY + D(days=14)),
                                        (20, TODAY + D(days=20))])
def test_no_next_phase_keeps_the_old_rule(left, want):
    end = TODAY + D(days=left)
    assert horizon(TODAY, end) == want == min(CAP, max(end, FLOOR))


def test_horizon_cap_floor_and_no_phase():
    assert horizon(TODAY, TODAY + D(days=3), date(2027, 3, 1)) == CAP          # still ≤ MAX_WEEKS
    assert CAP == MON + D(weeks=P.MAX_WEEKS, days=6)
    assert horizon(TODAY, TODAY + D(days=1), TODAY + D(days=3)) == FLOOR        # a short next phase: ≥ 2 weeks
    assert API.horizon_of(MON, TODAY, None, []) == CAP                          # no phase: the cap


SEASON = [{"kind": "base", "start": "2026-08-01", "end": "2026-10-07"},
          {"kind": "specific", "start": "2026-10-08", "end": "2026-11-15"},
          {"kind": "taper", "start": "2026-11-16", "end": "2026-11-28"},
          {"kind": "event", "start": "2026-11-29", "end": "2026-11-29"},
          {"kind": "recovery", "start": "2026-11-30", "end": "2026-12-13"},
          {"kind": "base", "start": "2026-12-14", "end": "2027-06-30"}]


def season_horizon(d: date, ps=SEASON) -> date:
    cur = next(p for p in ps if p["start"] <= d.isoformat() <= p["end"])
    return API.horizon_of(mon_of(d), d, date.fromisoformat(cur["end"]), ps)


def test_horizon_same_the_day_before_and_after_a_boundary_and_never_moves_back():
    assert season_horizon(date(2026, 10, 7)) == season_horizon(date(2026, 10, 8)) == date(2026, 11, 15)
    # the next phase is the one-day race: at least the two-week floor, then the 恢復期's end
    assert season_horizon(date(2026, 11, 28)) == date(2026, 12, 6)
    assert season_horizon(date(2026, 11, 29)) == date(2026, 12, 13)
    prev, d = None, date(2026, 9, 1)
    while d <= date(2027, 1, 31):
        h = season_horizon(d)
        assert prev is None or h >= prev, d
        prev, d = h, d + D(days=1)


def test_phase_push_end_is_the_current_phase_only():
    # 整個周期 (push / unpush): the phase the menu names, also on its last days when the
    # horizon already reaches into the next one
    end = TODAY + D(days=3)
    assert horizon(TODAY, end, NEXT_END) == NEXT_END
    assert API.phase_push_end(TODAY, end) == end
    assert API.phase_push_end(TODAY, date(2027, 3, 1)) == TODAY + D(weeks=P.MAX_WEEKS)
    assert API.phase_push_end(TODAY, None) == TODAY + D(weeks=P.MAX_WEEKS)     # no phase


# ---------------------------------------------------------------------------
# uids across the boundary (reconcile with each day's horizon)
# ---------------------------------------------------------------------------

def gen(today: date, until: date) -> list[dict]:
    """gen_weeks for `today`: this week, then each week up to `until`, the same three sessions
    every week, days after `until` left out (as projection.project_weeks)."""
    out, m = [], mon_of(today)
    while m <= until:
        ss = [g("quality", "quality", "閾值 3×10 分", 60, (m + D(days=1)).isoformat()),
              g("easy1", "easy", "輕鬆跑", 45, (m + D(days=3)).isoformat()),
              g("long", "long", "LSD（山路）", 120, (m + D(days=6)).isoformat())]
        out.append({"start": m.isoformat(), "mode": "base", "provisional": m > mon_of(today) + D(days=7),
                    "sessions": [s for s in ss if s["day"] <= until.isoformat()]})
        m += D(weeks=1)
    return out


def on(stored: list[dict], today: date, ps: list[dict]):
    h = season_horizon(today, ps)
    new, ch = R.reconcile(stored, gen(today, h), [], today.isoformat(), h.isoformat())
    return new, ch, h


def uids(ss: list[dict], since: date) -> dict:
    return {(s["week_start"], s["gen_key"]): s["uid"] for s in ss
            if s["state"] == "active" and s.get("day") and s["day"] >= since.isoformat()}


BOUNDARY = [{"kind": "base", "start": "2026-08-01", "end": "2026-10-07"},
            {"kind": "specific", "start": "2026-10-08", "end": "2026-12-20"},
            {"kind": "taper", "start": "2026-12-21", "end": "2027-01-09"},
            {"kind": "event", "start": "2027-01-10", "end": "2027-01-10"}]


def test_same_uids_the_day_before_and_after_a_phase_boundary():
    s1, _, _ = on([], date(2026, 10, 6), BOUNDARY)            # 基礎期 ends tomorrow
    s2, ch2, h2 = on(s1, date(2026, 10, 7), BOUNDARY)         # its last day
    s3, ch3, h3 = on(s2, date(2026, 10, 8), BOUNDARY)         # 專項期's first day
    assert h2 == h3 == CAP                                    # 12/20 capped at 8 weeks
    assert max(s["day"] for s in s2 if s["state"] == "active") == "2026-12-06"
    day = date(2026, 10, 8)
    assert uids(s3, day) == uids(s2, day) == uids(s1, day)
    assert len(uids(s3, day)) == 26                           # 3 a week to 12/6, but 10/6 (past)
    assert not [c for c in ch2 + ch3 if c["action"] in ("added", "removed")]


def test_a_phase_change_that_ends_the_current_phase_soon_removes_nothing():
    """The 2026-10-07 case: an open 基礎期 planned to the cap, then a race makes 基礎期 end on
    10/07 — the horizon stays out instead of dropping to 10/18, so the weeks after 10/18 keep
    their uids (before: removed on 10/07, re-added under new uids on 10/08)."""
    open_base = [{"kind": "base", "start": "2026-08-01", "end": "2027-06-30"}]
    s1, _, h1 = on([], date(2026, 10, 5), open_base)
    assert h1 == CAP
    s2, ch2, h2 = on(s1, TODAY, BOUNDARY)
    s3, ch3, h3 = on(s2, date(2026, 10, 8), BOUNDARY)
    assert h2 == h3 == CAP
    assert not [c for c in ch2 + ch3 if c["action"] in ("added", "removed")]
    day = date(2026, 10, 8)
    assert uids(s3, day) == uids(s1, day)
    assert any(k[0] >= "2026-10-19" for k in uids(s3, day))


def cur_week(today: date, phase: str) -> dict:
    """week_plan() of the week 10/5–10/11 on `today` (only its phase changes at the boundary)."""
    c = cur_plan(today=today.isoformat(), mode=phase, sessions=[
        g("quality", "quality", "閾值 3×10 分", 60, "2026-10-09"),
        g("strength1", "strength", "肌力（下肢單腳＋核心）", 35, "2026-10-09"),
        g("easy1", "easy", "輕鬆跑", 45, "2026-10-10"),
        g("long", "long", "LSD（山路）", 120, "2026-10-11")])
    c["week"] = {"start": "2026-10-05", "end": "2026-10-11", "today": today.isoformat(),
                 "days_left": 11 - today.day}
    c["phase"], c["done"] = phase, {"activities": []}
    return c


# a short 專項期 (no A race in the hand-built gate: its weeks stay before the Zone 3 T+ rotation)
SHORT = [{"kind": "base", "start": "2026-08-01", "end": "2026-10-07"},
         {"kind": "specific", "start": "2026-10-08", "end": "2026-11-01"},
         {"kind": "taper", "start": "2026-11-02", "end": "2026-11-14"},
         {"kind": "event", "start": "2026-11-15", "end": "2026-11-15"}]


def test_projected_weeks_keep_their_sessions_and_uids_across_the_boundary():
    """The real projection: the weeks after this one get the same generator ids the day before
    and the day after 基礎期 → 專項期, so reconcile keeps every row (same uid, at most changed)."""
    out = {}
    stored: list[dict] = []
    for today, phase in ((TODAY, "base"), (date(2026, 10, 8), "specific")):
        h = season_horizon(today, SHORT)
        cur = cur_week(today, phase)
        weeks = P.project_weeks(cur, SHORT, h)
        gw = [{"start": "2026-10-05", "mode": phase, "provisional": False, "sessions": cur["sessions"]}] + \
             [{"start": w["start"], "mode": w["mode"], "provisional": w["provisional"], "sessions": w["sessions"]}
              for w in weeks]
        stored, ch = R.reconcile(stored, gw, [], today.isoformat(), h.isoformat())
        out[phase] = (h, [(w["start"], sorted(s["id"] for s in w["sessions"])) for w in weeks], stored, ch)
    (hb, wb, sb, _), (hs, ws, ss, chs) = out["base"], out["specific"]
    assert hb == hs == date(2026, 11, 1) and wb == ws and [w[0] for w in wb] == ["2026-10-12", "2026-10-19",
                                                                                  "2026-10-26"]
    day = date(2026, 10, 8)
    assert uids(ss, day) == uids(sb, day)
    assert not [c for c in chs if c["action"] in ("added", "removed")]
