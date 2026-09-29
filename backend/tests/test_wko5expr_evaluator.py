"""WKO5 expression evaluator semantics on synthetic data."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
import math

import numpy as np
import pytest

from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.evaluator import Evaluator, EvalError, WS, Daily, ListV, PairV, RangeV
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 29)          # a Tuesday


def _ds(workouts, **kw):
    return FakeDataset(workouts, TODAY, settings={"runftp": 250.0, "runthr": 160.0}, **kw)


def _ev(ds, days=30, **kw):
    return Evaluator(ds, ds.today - days, ds.today, **kw)


def _day(d):
    return date_to_day(d)


def test_tl_is_linear_ewma_from_zero():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 1, 8), metrics={"tss": 42.0})])
    ctl = _ev(ds).evaluate("tl(tss, ctlconstant)")
    d0 = _day(dt.date(2026, 9, 1))
    assert ctl.at(d0 - 1) == 0.0
    assert ctl.at(d0) == pytest.approx(1.0)                # 0 + (42-0)/42
    assert ctl.at(d0 + 1) == pytest.approx(1.0 - 1.0 / 42)  # decays on rest days


def test_tl_sums_a_days_workouts_and_ignores_out_of_range_inputs():
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 1, 8), metrics={"tss": 21.0}),
        FakeWorkout(dt.datetime(2026, 9, 1, 18), metrics={"tss": 21.0}),
        FakeWorkout(dt.datetime(2026, 9, 2, 8), metrics={"tss": 6000.0}),  # > 5000 ignored
        FakeWorkout(dt.datetime(2026, 9, 3, 8), metrics={"tss": -5.0}),    # < 0 ignored
    ])
    ctl = _ev(ds).evaluate("tl(tss, 42)")
    d0 = _day(dt.date(2026, 9, 1))
    assert ctl.at(d0) == pytest.approx(1.0)
    assert ctl.at(d0 + 2) == pytest.approx((1 - 1 / 42) ** 2)


def test_tsb_is_yesterdays_ctl_minus_atl():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 1, 8), metrics={"tss": 70.0})])
    ev = _ev(ds)
    ctl, atl, tsb = ev.evaluate("ctl"), ev.evaluate("atl"), ev.evaluate("tsb")
    d = _day(dt.date(2026, 9, 5))
    assert tsb.at(d) == pytest.approx(ctl.at(d - 1) - atl.at(d - 1))


def test_if_filters_workouts_and_divides_by_daily_series():
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 1, 8), sport="run", metrics={"tss": 42.0}),
        FakeWorkout(dt.datetime(2026, 9, 1, 9), sport="walk", metrics={"tss": 10.0}),
    ])
    r = _ev(ds).evaluate('if(sport="run",tss)/tl(if(sport="run",tss),ctlconstant)')
    assert isinstance(r, WS) and set(r) == {0}
    assert r[0] == pytest.approx(42.0 / 1.0)


def test_string_compare_is_case_insensitive_and_hastag_matches_whole_tag():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 1, 8), sport="run", tags=["Race", "Garmin 3284"])])
    ev = _ev(ds)
    assert ev.evaluate('if(sport="RUN",1)')[0] == 1.0
    assert ev.evaluate('hastag("race")')[0] == 1.0
    assert ev.evaluate('hastag("rac")')[0] == 0.0


def test_athleterange_limits_aggregation_but_tl_keeps_history():
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 1, 8), metrics={"tss": 42.0}),
        FakeWorkout(dt.datetime(2026, 9, 29, 8), metrics={"tss": 10.0}),
    ])
    ev = _ev(ds)
    assert ev.evaluate("athleterange(today,today,sum(tss))") == pytest.approx(10.0)
    ctl_today = ev.evaluate("athleterange(today,today,max(tl(tss,42)))")
    full = ev.evaluate("tl(tss,42)")
    assert ctl_today == pytest.approx(full.at(ds.today))
    assert ctl_today > 10.0 / 42  # history before the range is included


def test_logical_not_of_na_and_zero_is_one():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 1, 8), metrics={"tss": 5.0})])
    ev = _ev(ds)
    assert ev.evaluate("!athleterange(today,today,sum(tss))") == 1.0   # no workout today -> na
    assert ev.evaluate("!0") == 1.0 and ev.evaluate("!3") == 0.0


def test_startofweek_is_monday():
    ds = _ds([])
    v = _ev(ds).evaluate("startofweek(today)")
    assert v == _day(dt.date(2026, 9, 28))


def test_sample_level_aggregation_lifts_per_workout():
    t = [float(i) for i in range(1, 11)]
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 1, 8), sport="run",
                    channels={"elapsedtime": t, "power": [300.0] * 5 + [100.0] * 5}),
        FakeWorkout(dt.datetime(2026, 9, 2, 8), sport="walk",
                    channels={"elapsedtime": t, "power": [300.0] * 10}),
    ])
    r = _ev(ds).evaluate("sum(if(runpower >= 0.95*runftp, deltatime))")
    assert r == WS({0: 5.0})                      # walk has no runpower
    assert _ev(ds).evaluate("sum(athleterange(today-60,today,sum(if(power>250,deltatime,0))))") == 15.0


def test_workout_level_samples_gauges_and_time_weighted_avg():
    t = [1.0, 2.0, 4.0]                           # last sample covers 2 s
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 1, 8), channels={"elapsedtime": t, "power": [100.0, 100.0, 400.0]})])
    ev = _ev(ds)
    w = ds.workouts[0]
    assert isinstance(ev.evaluate("power", w), np.ndarray)
    g = ev.evaluate("{avg(power),max(power)}", w)
    assert isinstance(g, ListV)
    assert g.items[0] == pytest.approx((100 + 100 + 400 * 2) / 4)
    assert g.items[1] == 400.0


def test_lines_bands_and_constants():
    ev = _ev(_ds([]))
    assert ev.evaluate("(,0.8)") == PairV(None, 0.8)
    assert isinstance(ev.evaluate("@indexvalues:={-15:10}").items[0], RangeV)
    assert ev.evaluate("0.03") == 0.03


def test_sport_filter_restricts_workouts():
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 1, 8), sport="run", metrics={"tss": 42.0}),
        FakeWorkout(dt.datetime(2026, 9, 1, 9), sport="walk", metrics={"tss": 42.0}),
    ])
    all_ = _ev(ds).evaluate("tl(tss,42)").at(_day(dt.date(2026, 9, 1)))
    run = _ev(ds, sports={"run"}).evaluate("tl(tss,42)").at(_day(dt.date(2026, 9, 1)))
    assert all_ == pytest.approx(2.0) and run == pytest.approx(1.0)


def test_grouped_sum_buckets_by_the_second_argument():
    """sum(numbers, groupby) — e.g. weekly totals via startofweek(date)."""
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 21, 8), metrics={"climbing": 100.0}),   # Mon
        FakeWorkout(dt.datetime(2026, 9, 23, 8), metrics={"climbing": 250.0}),   # Wed
        FakeWorkout(dt.datetime(2026, 9, 28, 8), metrics={"climbing": 400.0}),   # next Mon
    ])
    r = _ev(ds).evaluate("sum(climbing, startofweek(date))")
    assert isinstance(r, Daily)
    assert r.at(_day(dt.date(2026, 9, 21))) == pytest.approx(350.0)
    assert r.at(_day(dt.date(2026, 9, 28))) == pytest.approx(400.0)
    assert math.isnan(r.at(_day(dt.date(2026, 9, 24))))       # no bucket that day


def test_grouped_avg_and_count():
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 21, 8), metrics={"tss": 10.0}),
        FakeWorkout(dt.datetime(2026, 9, 21, 18), metrics={"tss": 30.0}),
    ])
    ev = _ev(ds)
    day = _day(dt.date(2026, 9, 21))
    assert ev.evaluate("avg(tss, trunc(date))").at(day) == pytest.approx(20.0)
    assert ev.evaluate("count(tss, trunc(date))").at(day) == pytest.approx(2.0)


def test_grouped_sum_ignores_workouts_without_a_value():
    ds = _ds([
        FakeWorkout(dt.datetime(2026, 9, 21, 8), metrics={"climbing": 100.0}),
        FakeWorkout(dt.datetime(2026, 9, 21, 9), metrics={"climbing": None}),
    ])
    r = _ev(ds).evaluate("sum(climbing, trunc(date))")
    assert r.at(_day(dt.date(2026, 9, 21))) == pytest.approx(100.0)


def test_unknown_identifier_raises_and_is_reported():
    ev = _ev(_ds([]))
    with pytest.raises(EvalError):
        ev.evaluate("nosuchthing")
    assert "nosuchthing" in ev.unsupported
