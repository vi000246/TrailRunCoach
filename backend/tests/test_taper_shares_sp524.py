"""SP-524: the 減量期 volume — 75 % of the pre-taper level 15–21 days out (a 17 / 21-day taper only),
50 % 8–14 days out, 40 % in the race week; the base is the level before the taper for every taper
week (the 2nd week's no longer holds the 1st one's cut). overview.week_plan and
projection.week_hours read the same table (overview.TAPER_SHARES)."""
from __future__ import annotations

import datetime as dt
import inspect

import pytest

from backend.engine import overview as O
from backend.engine import projection as P


@pytest.mark.parametrize("days,share", [(0, 0.40), (6, 0.40), (7, 0.40), (8, 0.50), (13, 0.50), (14, 0.50),
                                        (15, 0.75), (20, 0.75), (21, 0.75), (None, 0.50)])
def test_share_by_days_to_the_race(days, share):
    assert O.taper_share(days) == share


def test_first_taper_week_is_the_one_holding_its_monday():
    sun = dt.date(2026, 11, 8)                       # a 21-day taper before Sun 11/29 starts on a Sunday
    assert sun.weekday() == 6
    assert O.taper_first_monday(sun) == dt.date(2026, 11, 9)
    mon = dt.date(2026, 11, 9)
    assert O.taper_first_monday(mon) == mon


def test_base_is_the_six_weeks_before_the_week_the_taper_starts_in():
    """SP-668: the week holding the taper's first day takes a taper share on its taper days
    (straddle_hours), so the base is the 6 weeks before that week (was: before the first whole taper week)."""
    t0 = dt.date(2026, 11, 8)                        # a Sunday: its week starts Mon 11/2
    m1 = dt.date(2026, 11, 2)
    hours = {m1 - dt.timedelta(weeks=k): 10.0 for k in range(1, 7)}
    hours[m1 - dt.timedelta(weeks=7)] = 99.0         # older: not in the base
    hours[m1] = 7.5                                  # the week the taper starts in: not in the base
    hours[m1 + dt.timedelta(weeks=1)] = 5.0
    assert O.taper_base(t0, hours.get) == 10.0
    assert O.taper_base(t0, lambda m: None) is None


def test_21_day_taper_weeks_keep_the_pre_taper_base():
    """Weeks 15–21 / 8–14 / ≤ 7 days out: 75 / 50 / 40 % of the same 10 h, whatever the projected
    taper weeks before them were (the rolling 6-week mean made the 2nd week ≈ 37 %)."""
    hist = [10.0] * 8
    out = []
    for days in (20, 13, 6):
        h, mode, why = P.week_hours("taper", hist, [False] * len(hist), ctl0=40.0, r=50.0, cc=42.0,
                                    days_to_a=days, taper_base=10.0)
        assert mode == "taper" and "10.0 h" in why[0]
        out.append(round(h, 2))
        hist.append(h)
    assert out == [7.5, 5.0, 4.0]
    # the old rolling base: the 2nd week would have been 0.5 × mean(10, 10, 10, 10, 10, 7.5)
    rolled, _, _ = P.week_hours("taper", [10.0] * 7 + [7.5], [False] * 8, ctl0=40.0, r=50.0, cc=42.0, days_to_a=13)
    assert rolled < 5.0


def test_14_day_taper_unchanged():
    for days, share in ((13, 0.5), (6, 0.4)):
        h, _, _ = P.week_hours("taper", [8.0] * 8, [False] * 8, ctl0=40.0, r=50.0, cc=42.0, days_to_a=days,
                               taper_base=8.0)
        assert h == pytest.approx(8.0 * share)


def test_without_a_known_taper_start_the_last_six_weeks():
    h, _, _ = P.week_hours("taper", [6.0, 6.0, 12.0, 12.0, 12.0, 12.0, 12.0, 12.0], [False] * 8, ctl0=40.0, r=50.0,
                           cc=42.0, days_to_a=10)
    assert h == pytest.approx(6.0)


def test_overview_and_projection_read_the_same_rules():
    wp = inspect.getsource(O.week_plan)
    i = wp.index('elif kind == "taper":')
    taper = wp[i:wp.index('elif kind == "event":', i)]
    assert "taper_base(" in taper and "taper_share(days_to)" in taper
    assert '(dt.date.fromisoformat(t_ctx["start"]) - monday).days' in taper      # days from Monday
    assert "0.4 if" not in taper
    pj = inspect.getsource(P.project_weeks) if hasattr(P, "project_weeks") else inspect.getsource(P)
    assert "O.taper_base(" in pj and 'days_to = (_d(tc["start"]) - week).days' in pj
    wh = inspect.getsource(P.week_hours)
    assert "O.taper_share(days_to_a)" in wh and "0.4 if" not in wh
    # the sources sit at the table
    src = inspect.getsource(O)
    k = src.index("TAPER_SHARES = (")
    note = src[src.rindex("# 減量期的量 (SP-524", 0, k):k]
    for who in ("Pfitzinger 75–80 %", "Higdon 74 %", "Gaudette 85–90 %", "Karp 70 %", "Bosquet 2007", "Wang 2023"):
        assert who in note, who
