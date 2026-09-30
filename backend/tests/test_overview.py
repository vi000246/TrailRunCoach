import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import overview as O


def W(sport="run", sport_type="running", tags=()):
    return SimpleNamespace(sport=sport, sport_type=sport_type, tags=list(tags))


@pytest.mark.parametrize("w, cat", [
    (W(), "road"),
    (W(sport_type="treadmill running", tags=["runningtreadmill"]), "road"),
    (W(sport_type="trail running", tags=["runningtrail"]), "trail"),
    (W("walk", "hiking", ["hiking"]), "hike"),
    (W("other", "mountaineering", ["mountaineering"]), "hike"),
    (W("road bike", "cycling", ["cycling"]), "bike"),
    (W("strength", "strength"), "strength"),
    (W("other", "strength"), "strength"),
    (W("walk", "walking", ["walking"]), "walk"),
    (W("other", "table tennis"), "other"),
])
def test_category(w, cat):
    assert O.category(w) == cat


def test_periods():
    d = dt.date(2026, 9, 30)                     # a Wednesday
    assert O.period_start(d, "week") == dt.date(2026, 9, 28)
    assert O.period_start(d, "month") == dt.date(2026, 9, 1)
    assert O.period_start(d, "year") == dt.date(2026, 1, 1)
    assert O.period_next(dt.date(2026, 1, 1), "month") == dt.date(2026, 2, 1)
    assert O.period_next(dt.date(2026, 12, 1), "month") == dt.date(2027, 1, 1)
    assert O.period_prev(dt.date(2026, 3, 1), "month") == dt.date(2026, 2, 1)
    assert O.period_prev(dt.date(2026, 1, 1), "year") == dt.date(2025, 1, 1)
    assert O.period_label(dt.date(2026, 9, 28), "week") == "9/28–10/4"
    assert O.period_label(dt.date(2026, 9, 1), "month") == "2026/09"


def test_projection_matches_tl_recurrence():
    rows = O.project(20.0, 10.0, [70.0, 0.0], 42.0, 7.0)
    c1 = 20 + (70 - 20) / 42
    a1 = 10 + (70 - 10) / 7
    assert rows[0]["ctl"] == pytest.approx(c1)
    assert rows[0]["atl"] == pytest.approx(a1)
    assert rows[0]["tsb"] == pytest.approx(10.0)          # yesterday's CTL − ATL
    assert rows[1]["tsb"] == pytest.approx(c1 - a1)
    assert rows[1]["ctl"] == pytest.approx(c1 + (0 - c1) / 42)


def test_ramp_tss_for_three_ctl_points():
    # weekly TSS that raises CTL by exactly 3 in 7 days
    cc, ctl0 = 42.0, 20.0
    f7 = 1 - (1 - 1 / cc) ** 7
    daily = ctl0 + 3 / f7
    rows = O.project(ctl0, 0.0, [daily] * 7, cc, 7.0)
    assert rows[-1]["ctl"] - ctl0 == pytest.approx(3.0)
