"""WKO5 expression functions added from functions.md / formulas.md / the WKO5
Expression Reference — synthetic checks, mostly the Reference's own examples."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
import math

import numpy as np
import pytest

from backend.engine.algorithms.wko5_pdmodel import aerobic, anaerobic, model as pdmodel
from backend.engine.wko5expr import parser as P
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr import evaluator as E
from backend.engine.wko5expr.evaluator import (
    Ctx, Curve, Daily, EvalError, Evaluator, ListV, PairV, WS,
)
from backend.engine.wko5expr.render import result_to_json, workout_result_to_json
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 29)          # a Tuesday


def _ds(workouts, **kw):
    return FakeDataset(workouts, TODAY, settings={"runftp": 250.0, "runthr": 160.0,
                                                  "runtpace": 5.0, "runmhr": 190.0}, **kw)


def _ev(ds, days=30, **kw):
    return Evaluator(ds, ds.today - days, ds.today, **kw)


def _day(d):
    return date_to_day(d)


def ev(expr, ds=None, workout=None):
    ds = ds or _ds([])
    return _ev(ds).evaluate(expr, workout)


def ev_with(e: Evaluator, expr, workout=None, **vars):
    """Evaluate with pre-set @variables (e.g. a synthetic curve)."""
    ctx = Ctx(e.ds, e.begin, e.end, None, {"@" + k: v for k, v in vars.items()}, workout)
    E._CUR_CTX.append(ctx)
    try:
        return e.ev(P.parse(expr), ctx)
    finally:
        E._CUR_CTX.pop()


def items(v):
    assert isinstance(v, ListV), v
    return [x if isinstance(x, str) else (None if math.isnan(x) else x) for x in v.items]


# a mean-max-like curve generated from known model parameters
PARAMS = [15000.0, 20.0, 250.0, 25.0, 2400.0, -30.0]      # FRC, tau1, FTP, tau2, TTE, D
GRID = sorted({int(math.floor(1.05 ** i + 0.5)) for i in range(0, 200) if 1.05 ** i <= 5400}
              | {5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 2400, 3600})


def model_curve(scale=1.0) -> Curve:
    return Curve([float(x) for x in GRID], [pdmodel(PARAMS, float(x)) * scale for x in GRID])


def run_workout(day: dt.datetime, n=60, **ch):
    t = [float(i) for i in range(1, n + 1)]
    return FakeWorkout(day, sport="run", channels={"elapsedtime": t, **ch})


# ---------------------------------------------------------------------------
# constants, counting, simple set functions (Reference examples)
# ---------------------------------------------------------------------------

def test_constants():
    assert math.isnan(ev("na"))
    assert ev("e") == pytest.approx(2.7182818284590452)
    assert ev("g") == pytest.approx(9.80665)
    assert ev("pi") == pytest.approx(3.1415926535897932)
    assert ev("isvalid(na)") == 0.0


def test_count_is_valid_non_zero_and_length_counts_everything():
    assert ev("count({3,6,0,9,na,12})") == 4.0
    assert ev("length({3,6,0,9,na,12})") == 6.0


def test_count_of_samples_skips_zeros():
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=5, heartrate=[0.0, 120.0, None, 130.0, 0.0])])
    assert ev("count(heartrate)", ds, ds.workouts[0]) == 2.0


def test_delta():
    assert items(ev("delta({3,5,7,11,13})")) == [None, 2.0, 2.0, 4.0, 2.0]


def test_cumsum():
    assert items(ev("cumsum({1,2,3,4,5,6,7,8,9,10})")) == [1, 3, 6, 10, 15, 21, 28, 36, 45, 55]
    assert items(ev("cumsum({1,na,2})")) == [1.0, None, 3.0]


def test_cumsum_of_samples_gives_cumulative_work():
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=4, power=[100.0, 200.0, 300.0, 400.0])])
    r = ev("cumsum(power*deltatime)/1000", ds, ds.workouts[0])
    assert list(r) == pytest.approx([0.1, 0.3, 0.6, 1.0])


def test_stddev_family():
    assert ev("stddev({1,2,3,4,5,6,7,8,9,10})") == pytest.approx(3.0276504, abs=1e-7)
    assert ev("pstddev({1,2,3,4,5,6,7,8,9,10})") == pytest.approx(2.8722813, abs=1e-7)
    assert ev("variance({1,2,3})") == pytest.approx(1.0)
    assert math.isnan(ev("stddev({5})"))


def test_stddev_of_samples_per_workout_at_athlete_level():
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=4, power=[1.0, 2.0, 3.0, 4.0])])
    assert ev("stddev(power)", ds, ds.workouts[0]) == pytest.approx(np.std([1, 2, 3, 4], ddof=1))
    r = ev("stddev(power)", ds)
    assert isinstance(r, WS) and r[0] == pytest.approx(np.std([1, 2, 3, 4], ddof=1))


def test_greatest_least_first_last_on_lists_keep_order():
    assert items(ev("greatest({5,7,3,2,4,9,8}, 3)")) == [7, 9, 8]
    assert items(ev("least({5,7,3,2,4,9,8}, 3)")) == [3, 2, 4]
    assert items(ev("first({5,7,3,2,4,9,8}, 3)")) == [5, 7, 3]
    assert items(ev("last({5,7,3,2,4,9,8}, 3)")) == [4, 9, 8]
    assert items(ev('first({"Yankee","Bravo","Zulu","Alpha","Foxtrot"},3)')) == ["Yankee", "Bravo", "Zulu"]
    with pytest.raises(EvalError):
        ev("greatest({1,2}, -1)")


def test_greatest_keeps_each_workouts_date():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, d, 8), metrics={"tss": v})
              for d, v in [(10, 50.0), (12, 90.0), (15, 70.0), (20, 30.0)]])
    e = _ev(ds)
    r = e.evaluate("greatest(tss, 2)")
    assert isinstance(r, WS) and set(r) == {1, 2}
    assert e.evaluate("sum(greatest(tss, 2))/2") == pytest.approx(80.0)
    js = result_to_json(r, ds, e.begin, e.end)
    assert [p[0][:10] for p in js["points"]] == ["2026-09-12", "2026-09-15"]


def test_greatest_ignores_workouts_outside_the_chart_range():
    ds = _ds([FakeWorkout(dt.datetime(2026, 6, 1, 8), metrics={"tss": 500.0}),
              FakeWorkout(dt.datetime(2026, 9, 20, 8), metrics={"tss": 40.0})])
    assert _ev(ds).evaluate("sum(greatest(tss, 1))") == pytest.approx(40.0)
    assert _ev(ds).evaluate("max(tss)") == pytest.approx(40.0)   # reductions use the range too


def test_noinvalid_rev_sort_unique():
    assert items(ev("noinvalid({13,0,7,na,0,21})")) == [13, 0, 7, 0, 21]
    assert items(ev("rev({3,5,7,9})")) == [9, 7, 5, 3]
    assert items(ev("sort({5,3,8,0,na,7})")) == [0, 3, 5, 7, 8, None]
    assert items(ev("sortd({5,3,8})")) == [8, 5, 3]
    assert items(ev("unique({1,2,2,3,1})")) == [1, 2, 3]


def test_sortx_sorts_pairs_by_x():
    r = ev("sortx({(3,1),(1,2),(2,3)})")
    assert isinstance(r, Curve) and r.xs == [1.0, 2.0, 3.0] and r.ys == [2.0, 3.0, 1.0]


def test_sign_clamp_round():
    assert ev("sign(3)") == 1.0 and ev("sign(-9)") == -1.0 and ev("sign(0)") == 0.0
    assert items(ev("sign({-7,5,11})*abs({-7,5,11})")) == [-7, 5, 11]
    assert ev("clamp(round(12.4)+1,1,10)") == 10.0          # WKO5's own TIS order
    assert ev("clamp(4,8,5)") == 5.0                        # Reference order
    assert ev("round(pi,-1)") == pytest.approx(3.1)
    assert ev("round(pi,-3)") == pytest.approx(3.142)
    assert ev("round(1234.567,2)") == pytest.approx(1200.0)
    assert ev("round(2.5)") == 3.0 and ev("round(-2.5)") == -3.0


def test_string_and_concatenation():
    r = ev('string({134,80.1,na}) + " to " + string({142,90,na})')
    assert items(r) == ["134 to 142", "80.1 to 90", " to "]


def test_list_arithmetic_and_ranges():
    assert items(ev("{1,2}*{3,4}")) == [3, 8]
    assert items(ev("round({0.84,0.89,NA}*100)")) == [84, 89, None]
    assert items(ev("{5:0:-1}")) == [5, 4, 3, 2, 1, 0]
    assert items(ev("shift({1,2,3},-1)")) == [2, 3, None]
    assert ev("(,last({1,2,3},1))*2") == 6.0


def test_in_operator():
    assert items(ev("{1,2,3} in {2,3,4}")) == [0, 1, 1]


# ---------------------------------------------------------------------------
# regression
# ---------------------------------------------------------------------------

def test_slr_on_workout_samples():
    t = [float(i) for i in range(1, 11)]
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=10, power=[2 * x + 5 for x in t],
                          heartrate=[0.5 * (2 * x + 5) + 60 for x in t])])
    w = ds.workouts[0]
    assert ev("slrm(power)", ds, w) == pytest.approx(2.0)
    assert ev("slrb(power)", ds, w) == pytest.approx(5.0)
    assert ev("slrrsq(power)", ds, w) == pytest.approx(1.0)
    line = ev("slr(power)", ds, w)
    assert list(line) == pytest.approx([2 * x + 5 for x in t])
    assert ev("slrm((power, heartrate))", ds, w) == pytest.approx(0.5)


def test_slr_on_dated_values_is_a_daily_line():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 10), metrics={"tss": 10.0}),
              FakeWorkout(dt.datetime(2026, 9, 20), metrics={"tss": 30.0})])
    e = _ev(ds)
    assert e.evaluate("slrm(tss)") == pytest.approx(2.0)
    r = e.evaluate("slr(tss)")
    assert isinstance(r, Daily)
    assert r.at(_day(dt.date(2026, 9, 10))) == pytest.approx(10.0)
    assert r.at(_day(dt.date(2026, 9, 20))) == pytest.approx(30.0)
    assert len(r.values) == 11


def test_slr_two_point_line_for_pairs():
    r = ev("slr({(0,1),(10,21),(5,11)})")
    assert isinstance(r, Curve) and r.xs == [0.0, 10.0] and r.ys == pytest.approx([1.0, 21.0])


# ---------------------------------------------------------------------------
# bin
# ---------------------------------------------------------------------------

def _hist_ds():
    # samples at t = 1, 2, 4, 5 -> deltatime 1, 1, 2, 1
    return _ds([FakeWorkout(dt.datetime(2026, 9, 20, 8), sport="run", channels={
        "elapsedtime": [1.0, 2.0, 4.0, 5.0], "power": [100.0, 104.0, 112.0, 125.0],
        "heartrate": [100.0, 130.0, 150.0, 175.0]})])


def test_bin_numeric_size_accumulates_seconds():
    ds = _hist_ds()
    r = ev("bin(power, 10)", ds, ds.workouts[0])
    assert isinstance(r, Curve) and r.xs == [100.0, 110.0, 120.0] and r.ys == [2.0, 2.0, 1.0]


def test_bin_cut_list_is_up_to_but_not_equal():
    ds = _hist_ds()
    assert items(ev("bin(power, {104,120})", ds, ds.workouts[0])) == [1.0, 3.0, 1.0]
    assert items(ev("bin({1,2,3,4}, {3})")) == [2.0, 2.0]          # count without samples


def test_bin_by_level_name():
    ds = _hist_ds()      # runthr 160: classichr AR <110.4, E <134.4, TE <152, TH <169.6, VM
    r = ev('bin(heartrate, "classichr")', ds, ds.workouts[0])
    assert [(p.x, p.y) for p in r.items] == [("Active Recovery", 1.0), ("Endurance", 1.0),
                                              ("Tempo", 2.0), ("Threshold", 0.0), ("VO2max", 1.0)]
    js = workout_result_to_json(r, ds, ds.workouts[0])
    assert js["kind"] == "points" and js["points"][0] == ["Active Recovery", 1.0]


def test_bin_labelled_cuts_skip_open_ended_level():
    ds = _hist_ds()
    r = ev('bin(power, {("Low",105),("Mid",120),("Top",na)})', ds, ds.workouts[0])
    assert [(p.x, p.y) for p in r.items] == [("Low", 2.0), ("Mid", 2.0), ("Top", 1.0)]


def test_bin_at_athlete_level_adds_workouts():
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=3, heartrate=[121.0, 122.0, 131.0]),
              run_workout(dt.datetime(2026, 9, 21, 8), n=2, heartrate=[124.0, 139.0])])
    r = _ev(ds).evaluate("bin(runheartrate, 5)")
    assert r.xs == [120.0, 125.0, 130.0, 135.0] and r.ys == [3.0, 0.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# dates and grouping
# ---------------------------------------------------------------------------

def test_date_functions_match_the_reference_examples():
    assert ev("startofmonth(date(2026,9,17))") == _day(dt.date(2026, 9, 1))
    assert ev("startofyear(date(2026,9,17))") == _day(dt.date(2026, 1, 1))
    assert ev("weekval(date(2015,11,8))") == pytest.approx(5993.857, abs=1e-3)
    assert ev("trunc(weekval(date(2015,11,8)))") == 5993.0
    assert ev("monthval(date(2015,11,3))") == pytest.approx(1378.0666667, abs=1e-6)
    assert ev("yearval(date(2015,11,8))") == pytest.approx(114.852, abs=1e-3)
    assert ev("week(date(2026,9,29))") == 40.0
    assert ev("month(date(2016,8,21))") == 8.0 and ev("year(date(2017,8,21))") == 2017.0
    # date() of a period value is the period's first day (Reference monthval examples)
    assert ev("date(trunc(weekval(today)))") == ev("startofweek(today)") == _day(dt.date(2026, 9, 28))
    assert ev("date(trunc(monthval(today)))") == _day(dt.date(2026, 9, 1))
    assert ev("date(trunc(monthval(today))+1)-1") == _day(dt.date(2026, 9, 30))
    assert ev("date(trunc(yearval(today)))") == _day(dt.date(2026, 1, 1))


def test_groupby_period_names_and_month_keys():
    ds = _ds([FakeWorkout(dt.datetime(2026, 8, 30, 8), metrics={"tss": 10.0}),
              FakeWorkout(dt.datetime(2026, 9, 1, 8), metrics={"tss": 20.0}),
              FakeWorkout(dt.datetime(2026, 9, 2, 8), metrics={"tss": 30.0}),
              FakeWorkout(dt.datetime(2026, 9, 3, 8), metrics={"tss": 0.0})])
    e = _ev(ds)
    wk = e.evaluate('sum(tss, "week")')
    assert wk.at(_day(dt.date(2026, 8, 24))) == pytest.approx(10.0)
    assert wk.at(_day(dt.date(2026, 8, 31))) == pytest.approx(50.0)
    assert e.evaluate('count(tss, "week")').at(_day(dt.date(2026, 8, 31))) == 2.0   # 0 not counted
    sep = _day(dt.date(2026, 9, 1))
    for expr in ('sum(tss, "month")', "sum(tss, startofmonth(date))",
                 "sum(tss, trunc(monthval(date)))"):
        assert e.evaluate(expr).at(sep) == pytest.approx(50.0), expr
    assert e.evaluate("max(tss, startofmonth(date))").at(sep) == pytest.approx(30.0)
    assert e.evaluate("sum(tss, trunc(yearval(date)))").at(_day(dt.date(2026, 1, 1))) == 60.0
    assert e.evaluate("sum(tss, trunc(weekval(date)))").at(_day(dt.date(2026, 8, 31))) == 50.0


def test_groupby_period_of_sample_data():
    ds = _ds([run_workout(dt.datetime(2026, 9, 21, 8), n=4, power=[300.0, 300.0, 100.0, 0.0]),
              run_workout(dt.datetime(2026, 9, 23, 8), n=2, power=[300.0, 100.0])])
    e = _ev(ds)
    mon = _day(dt.date(2026, 9, 21))
    assert e.evaluate('sum(if(runpower >= runftp, deltatime), "week")').at(mon) == 3.0
    assert e.evaluate('count(power, "week")').at(mon) == 5.0          # zero sample not counted
    assert e.evaluate('max(power, "week")').at(mon) == 300.0


def test_groupby_of_lists():
    r = ev("max({150,160,155}, {5,6,5})")
    assert isinstance(r, Curve) and r.xs == [5.0, 6.0] and r.ys == [155.0, 160.0]


# ---------------------------------------------------------------------------
# power-duration family
# ---------------------------------------------------------------------------

def test_targets_follow_the_disassembled_formulas():
    e = _ev(_ds([]))
    c = model_curve()
    f = e._fit(c)
    FRC, t1, FTP = f["params"][0], f["params"][1], f["params"][2]
    assert FTP == pytest.approx(250, rel=0.02)
    got = ev_with(e, "targetduration({0,1,2,3,4,5}, @c)", c=c)
    want = [f["tte"], 0.9625 * FRC / (0.0375 * FTP), 0.1625 * FRC / (0.032 * FTP),
            5 * math.log(2) * t1, 3 * math.log(2) * t1, math.log(2) * t1]
    assert items(got) == pytest.approx(want)
    tp = items(ev_with(e, "targetpower({5:0:-1}, @c)", c=c))
    assert tp[5] == pytest.approx(FTP) and tp[4] == pytest.approx(1.02 * FTP)
    assert tp[3] == pytest.approx(1.2 * FTP)
    assert tp[0] == pytest.approx(pdmodel(f["params"], math.log(2) * t1))
    assert ev("targetname(4)") == "Intensive Anaerobic (FRC)"
    assert items(ev("targetname({0,5})")) == ["Extensive Aerobic (FTP)", "Max"]


def test_ftpcurve_plus_frccurve_is_pdcurve():
    e = _ev(_ds([]))
    c = model_curve()
    a = ev_with(e, "ftpcurve(@c)", c=c)
    b = ev_with(e, "frccurve(@c)", c=c)
    pd = ev_with(e, "pdcurve(@c)", c=c)
    p = e._fit(c)["params"]
    assert a.ys[10] == pytest.approx(aerobic(p, a.xs[10]))
    assert b.ys[10] == pytest.approx(anaerobic(p, b.xs[10]))
    assert np.allclose(np.array(a.ys) + np.array(b.ys), pd.ys)
    ratio = ev_with(e, "ftpcurve(@c)/pdcurve(@c)", c=c)
    assert isinstance(ratio, Curve) and 0 < ratio.ys[0] < ratio.ys[-1] <= 1.0
    kj = ev_with(e, "ftpcurve(@c)*xx(ftpcurve(@c))/1000", c=c)
    assert kj.ys[-1] == pytest.approx(a.ys[-1] * a.xs[-1] / 1000)


def test_ilevels_from_a_meanmax_curve():
    e = _ev(_ds([]))
    c = model_curve()
    FTP = e._fit(c)["params"][2]
    lv = items(ev_with(e, "levelfrom(@c, {0,1,4,5})", c=c))
    assert lv == pytest.approx([0.0, 0.56 * FTP, 0.95 * FTP, 1.05 * FTP])
    assert ev_with(e, "levelto(@c, 4)", c=c) == pytest.approx(1.05 * FTP)
    assert math.isnan(ev_with(e, "levelto(@c, 8)", c=c))         # open-ended Pmax level
    f6, f7, f8 = (ev_with(e, f"levelfrom(@c, {i})", c=c) for i in (6, 7, 8))
    assert 1.05 * FTP < f6 < f7 < f8
    assert ev_with(e, "levelto(@c, 5)", c=c) == pytest.approx(f6)


def test_level_tables_by_name():
    assert ev('levelfrom("classichr", 1)') == pytest.approx(0.69 * 160)
    assert ev('levelto("frielhr", 0)') == pytest.approx(0.85 * 160)
    assert ev('levelname("frielhr", 3)') == "Sub-Threshold"
    assert ev('levelcount("frielhr")') == 7.0
    assert ev('levelname("classicpower", 1)') == "Endurance"
    assert math.isnan(ev('levelto("classichr", 4)'))


def test_li_lookup_xx_yx():
    e = _ev(_ds([]))
    c = Curve([10.0, 20.0, 30.0], [300.0, 250.0, 200.0])
    assert ev_with(e, "li(@c, 15)", c=c) == pytest.approx(275.0)
    assert ev_with(e, "li(@c, 5)", c=c) == pytest.approx(300.0)       # clamped
    assert ev_with(e, "lookup(@c, 25)", c=c) == pytest.approx(250.0)  # step
    assert math.isnan(ev_with(e, "lookup(@c, 5)", c=c))
    assert ev_with(e, "li(yx(@c), 225)", c=c) == pytest.approx(25.0)  # duration at 225 W
    assert items(ev_with(e, "li(@c, {10,30})", c=c)) == [300.0, 200.0]
    xx = ev_with(e, "xx(@c)", c=c)
    assert xx.xs == c.xs and xx.ys == c.xs
    assert ev("lookup(runthr, today)") == 160.0
    assert ev("sum(xx(({1,2},{5,6})))") == 3.0


def test_best_times_style_selection_on_curves():
    e = _ev(_ds([]))
    mm = model_curve(0.95)
    pd = model_curve()
    r = ev_with(e, "xx(greatest(if(xx(@mm)<=30,(@pd-@mm)/@pd),1))", mm=mm, pd=pd)
    assert isinstance(r, Curve) and len(r.xs) == 1 and r.xs[0] <= 30


def test_ftp_with_lookback_is_a_daily_series(monkeypatch):
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 10, 8), sport="run", metrics={"tss": 1.0})])
    e = _ev(ds)
    monkeypatch.setattr(e, "_workout_curve", lambda node, w: model_curve())
    r = e.evaluate("ftp(meanmax(power), 90)")
    assert isinstance(r, Daily) and r.start == e.begin and len(r.values) == 31
    assert math.isnan(r.at(_day(dt.date(2026, 9, 9))))            # before the workout
    ftp = e._fit(model_curve())["FTP"]
    assert r.at(_day(dt.date(2026, 9, 10))) == pytest.approx(ftp)
    assert r.at(_day(dt.date(2026, 9, 29))) == pytest.approx(ftp)
    trend = e.evaluate("slrm(ftp(meanmax(power), 90))")
    assert trend == pytest.approx(0.0, abs=1e-9)
    frc = e.evaluate("frc(meanmax(power), 30)")
    assert frc.at(_day(dt.date(2026, 9, 20))) == pytest.approx(e._fit(model_curve())["FRC"] / 1000)


def test_stamina_identifier_expands_the_builtin(monkeypatch):
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 10, 8), sport="run")])
    e = _ev(ds)
    c4 = Curve(list(model_curve().xs), [y ** 4 for y in model_curve().ys])
    monkeypatch.setattr(e, "_workout_curve", lambda node, w: c4)
    s = e.evaluate("stamina")
    f = e._fit(model_curve())
    want = min(max(1 + f["D"] * (1 + math.log(3600 / f["TTE"])) / f["FTP"], 0), 100)
    assert s == pytest.approx(want, rel=1e-6)


def test_tis_builtins_on_a_steady_ftp_hour(monkeypatch):
    n = 3600
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=n, power=[250.0] * n)])
    e = _ev(ds)
    monkeypatch.setattr(e, "_workout_curve", lambda node, w: model_curve())
    f = e._fit(model_curve())
    FTP, FRC = f["FTP"], f["FRC"] / 1000
    w = ds.workouts[0]
    weighting = max(-(0.105 * (250 - FTP)) ** 2 + FTP * 1.3, 0)
    aer = sum([250 * weighting / 1000] * n) / (FTP * 3.6) / 85
    assert e.evaluate("tisaerobic", w) == min(max(math.floor(aer + 0.5) + 1, 1), 10)
    above = max(250 - 0.85 * FTP, 0) ** 1.379
    ana = sum([above / 1000] * n) / FRC / 3.6
    assert e.evaluate("tisanaerobic", w) == min(max(math.floor(ana + 0.5) + 1, 1), 10)
    r = e.evaluate("tl((tisaerobic), ctlconstant)")          # athlete level: per workout
    assert isinstance(r, Daily) and r.at(_day(dt.date(2026, 9, 20))) > 0


# ---------------------------------------------------------------------------
# smoothing
# ---------------------------------------------------------------------------

def test_filter_window_offsets():
    """0x6dcf10: off = K−1 (causal) or (K−1)//2 (sides = 2)."""
    assert items(ev("filter({1,2,3,4,5},{1},1)")) == [1, 2, 3, 4, 5]
    # K = 2, centred: off = 0, so the window is [i, i+1] (looks ahead)
    assert items(ev("filter({0,0,3,0,0},{1,1},2)")) == [0, 1.5, 1.5, 0, 0]
    # causal: [i−1, i]; edges renormalise over the in-range samples
    assert items(ev("filter({0,0,3,0,0},{1,1},1)")) == [0, 0, 1.5, 1.5, 0]
    # k[K−1] weights the current sample in the causal case
    assert items(ev("filter({0,0,4},{1,3},1)")) == [0, 0, 3]
    # K = 3 centred: k[0] on i−1, k[2] on i+1
    # (i = 0 has weight sum 0 → the undivided sum 0 is returned)
    assert items(ev("filter({0,4,0},{1,0,0},2)")) == [0, 0, 4]
    # a sides value other than 2 is causal (only the value count is checked)
    assert items(ev("filter({1,2},{1},3)")) == [1, 2]
    with pytest.raises(EvalError):
        ev("filter({1,2},{1},{1,2})")


def test_filter_na_handling():
    # na samples drop out of both sums; an na sample still gets an output
    assert items(ev("filter({2,na,4},{1,1,1},2)")) == [2, 3, 4]
    assert items(ev("filter({na,na,5},{1,1},1)")) == [None, None, 5]
    # weights summing to zero: the sum is returned undivided
    assert items(ev("filter({1,2,3},{1,-1},1)")) == [1, -1, -1]


def test_isef_kernel():
    """0x6e7590: odd length, (1−f)^|j| normalised to sum 1."""
    assert items(ev("isef(0.5,3)")) == pytest.approx([0.25, 0.5, 0.25], rel=1e-15)
    k = items(ev("isef(0.5,4)"))                   # even length → 5
    assert k == pytest.approx([0.1, 0.2, 0.4, 0.2, 0.1], rel=1e-15)
    w = [math.exp(abs(j) * math.log(1 - 1 / 9)) for j in range(-4, 5)]
    s = 0.0
    for x in w:
        s = x + s
    assert items(ev("isef(1/9,9)")) == [x / s for x in w]          # bit-exact recipe
    assert items(ev("isef(0.005,9)")) == [1.0]                     # f <= 0.005 → identity
    with pytest.raises(EvalError):
        ev("isef(1,9)")
    with pytest.raises(EvalError):
        ev("isef(0.5,1001)")


def test_gaussian_kernel():
    """0x6e20a0: exp(−j²/2σ²)/(σ√2π), odd length, normalised."""
    assert items(ev("gaussian(1,5)")) == [1.0]                    # sigma <= 1 → identity
    g = items(ev("gaussian(2,5)"))
    raw = [math.exp(-j * j / 8) for j in range(-2, 3)]
    assert g == pytest.approx([x / sum(raw) for x in raw], rel=1e-15)
    assert len(items(ev("gaussian(2,4)"))) == 5
    assert sum(items(ev("gaussian(3,31)"))) == pytest.approx(1.0)
    with pytest.raises(EvalError):
        ev("gaussian(101,5)")


def test_filter_with_isef_on_samples():
    p = [100.0] * 5 + [300.0] + [100.0] * 5
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=11, power=p)])
    r = ev("filter(power,isef(0.5,3),2)", ds, ds.workouts[0])
    assert isinstance(r, np.ndarray) and len(r) == 11
    assert r[5] == pytest.approx(200.0) and r[4] == pytest.approx(150.0) and r[0] == 100.0


def test_dfrc_depletion_and_biexponential_recovery():
    """0x6d7ae0: kJ balance, (p−ftp)·dt above FTP, 30 % τ=25 s + 70 % τ=300 s back."""
    ftp, frc = 250.0, 15.0
    p = [200.0, 350.0, 350.0, None, 200.0, 400.0]
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=6, power=p)])
    e = _ev(ds)
    r = ev_with(e, "dfrc(power, @frc, @ftp)", ds.workouts[0], frc=frc, ftp=ftp)
    assert isinstance(r, np.ndarray)
    assert r[0] == 15.0                                  # below FTP, nothing used yet
    assert r[1] == pytest.approx(14.9) and r[2] == pytest.approx(14.8)
    D = 200.0

    def rec(t):
        return (1 - math.exp(-t / 25)) * D * 0.3 + (1 - math.exp(-t / 300)) * D * 0.7
    assert r[3] == pytest.approx((15000.0 - D + rec(1.0)) / 1000.0, rel=1e-13)  # na = 0 W
    assert r[4] == pytest.approx((15000.0 - D + rec(2.0)) / 1000.0, rel=1e-13)
    # back above FTP: starts from what is still missing, then adds (400−250)·1
    assert r[5] == pytest.approx((15000.0 - (D - rec(2.0) + 150.0)) / 1000.0)


def test_dfrc_argument_checks():
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=3, power=[1.0, 2.0, 3.0])])
    e = _ev(ds)
    for frc, ftp in ((0.0, 250.0), (51.0, 250.0), (15.0, 5.0), (15.0, 601.0), (math.nan, 250.0)):
        with pytest.raises(EvalError):
            ev_with(e, "dfrc(power, @frc, @ftp)", ds.workouts[0], frc=frc, ftp=ftp)


def test_round_uses_wkos_scale_table():
    """0x4a5bb0: x·m then /m with m = 10^−places (not x/10^places)."""
    assert ev("round(1234.567,2)") == math.floor(1234.567 * 0.01 + 0.5) / 0.01
    assert ev("round(-0.4)") == 0.0 and math.copysign(1, ev("round(-0.4)")) == 1.0
    assert ev("round(2.5,0.6)") == 0.0                   # places 0.6 → 1: round(2.5/10)*10
    assert ev("round(-2.45,-1)") == -2.5
    with pytest.raises(EvalError):
        ev("round(1,8)")


def test_cts_and_rst_power_levels_are_empty():
    """0x64d1e0 only reads the power threshold and clears the table; the
    classes' level count (vtable slot 11, 0x457000) returns 0."""
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=3, power=[1.0, 2.0, 3.0])])
    for name in ("ctspower", "rstpower"):
        assert ev(f'levelcount("{name}")', ds, ds.workouts[0]) == 0.0
        assert ev(f'levelname("{name}", 0)', ds, ds.workouts[0]) == ""


# ---------------------------------------------------------------------------
# workout identifiers
# ---------------------------------------------------------------------------

def test_text_fields_and_title_falls_back_to_the_sport_name():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 20, 8), sport="run", sport_type="Trail Running",
                          notes="felt good", description="hill reps"),
              FakeWorkout(dt.datetime(2026, 9, 21, 8), sport="run", title="Easy 10k")])
    e = _ev(ds)
    assert e.evaluate('has(title,"Trail")') == WS({0: 1.0, 1: 0.0})
    assert e.evaluate("notes")[0] == "felt good" and e.evaluate("notes")[1] == ""
    assert e.evaluate("desc")[0] == "hill reps" == e.evaluate("description")[0]
    assert e.evaluate("title", ds.workouts[1]) == "Easy 10k"
    js = result_to_json(e.evaluate("rev(notes)"), ds, e.begin, e.end)
    assert [p[1] for p in js["points"]] == ["felt good"]


def test_begintime_endtime_and_athlete_level_workoutrange():
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=10, power=[float(i) for i in range(10)]),
              run_workout(dt.datetime(2026, 9, 21, 8), n=4, power=[5.0] * 4)])
    e = _ev(ds)
    w = ds.workouts[0]
    assert e.evaluate("begintime", w) == 0.0 and e.evaluate("endtime", w) == 10.0
    assert e.evaluate("workoutrange(3, 5, begintime)", w) == 3.0
    r = e.evaluate("workoutrange(begintime, endtime, max(power))")
    assert r == WS({0: 9.0, 1: 5.0})


def test_sport_receiver_limits_athleterange():
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 20, 8), sport="run", metrics={"tss": 40.0}),
              FakeWorkout(dt.datetime(2026, 9, 20, 9), sport="bike", metrics={"tss": 60.0})])
    e = _ev(ds)
    w = ds.workouts[0]
    assert e.evaluate("athleterange(date-1, date, sum(tss))", w) == pytest.approx(100.0)
    assert e.evaluate("sport(sport).athleterange(date-1, date, sum(tss))", w) == pytest.approx(40.0)


def _ec_factor(h):
    """WKO5 ecpower altitude factor (string @0x860d18), h in metres."""
    return -0.00000000674 * h * h - 0.0000274 * h + 0.997


def test_ecpower_is_wko5s_expression_with_sftp_as_bikeftp():
    n = 40
    power = [200.0] * 20 + [300.0] * 20
    ds = FakeDataset([run_workout(dt.datetime(2026, 9, 20, 8), n=n, power=power,
                                  elevation=[2000.0] * 20 + [0.0] * 20)], TODAY,
                     settings={"bikeftp": 250.0, "runftp": 400.0})
    e = _ev(ds)
    w = ds.workouts[0]
    ew = e.evaluate("ewma(power,25)", w)
    ec = e.evaluate("ecpower", w)
    # below sftp: ewma(power, 25) / f(h)
    assert ec[10] == pytest.approx(ew[10] / _ec_factor(2000.0))
    # above sftp (= bikeftp, not runftp): (p/f - sftp) * f + sftp
    p = ew[-1] / _ec_factor(0.0)
    assert p > 250.0
    assert ec[-1] == pytest.approx((p - 250.0) * _ec_factor(0.0) + 250.0)
    assert e.evaluate("sftp", w) == 250.0
    # windowed like any channel; other sports have no ecpower
    assert e.evaluate("workoutrange(1, 5, max(ecpower))", w) == pytest.approx(np.nanmax(ec[:5]))
    ds2 = FakeDataset([FakeWorkout(dt.datetime(2026, 9, 20, 8), sport="walk",
                                   channels={"elapsedtime": [1.0, 2.0], "power": [100.0, 100.0],
                                             "elevation": [0.0, 0.0]})], TODAY,
                      settings={"bikeftp": 250.0})
    assert np.isnan(_ev(ds2).evaluate("ecpower", ds2.workouts[0])).all()


def test_fmax_is_the_morin_force_in_newtons_and_impact_gs():
    n = 10
    ds = FakeDataset([run_workout(dt.datetime(2026, 9, 20, 8), n=n, cadence=[90.0] * (n - 1) + [0.0],
                                  stancetime=[0.25] * n, speed=[12.0] * n)], TODAY,
                     settings={"weight": 70.0, "height": 1.80})
    e = _ev(ds)
    w = ds.workouts[0]
    fm = e.evaluate("fmax", w)
    # stancetime is stored in s but evaluates in ms (as in WKO5): flight = 60000/90/2 - 250
    ratio = (60 * 1000 / 90 / 2 - 250) / 250 + 1
    assert fm[0] == pytest.approx(70 * 9.80665 * math.pi / 2 * ratio)
    assert np.isnan(fm[-1])                          # cadence 0 -> na, not inf
    gs = e.evaluate("avg(metric(fmax/(metric(weight)*g)))", w)
    assert gs == pytest.approx(math.pi / 2 * ratio)
    kl = e.evaluate("kleg", w)
    tc = 0.25
    f = 70 * 9.80665 * math.pi / 2 * ratio
    leg = 1.80 * 0.53
    dl = leg - math.sqrt(leg ** 2 - (12 / 3.6 * tc / 2) ** 2) + f * tc ** 2 / (70 * math.pi ** 2) \
        + 9.80665 * tc ** 2 / 8
    assert kl[0] == pytest.approx(f / dl / 1000)
    bike = FakeDataset([FakeWorkout(dt.datetime(2026, 9, 20, 8), sport="bike",
                                    channels={"elapsedtime": [1.0, 2.0], "cadence": [90.0, 90.0],
                                              "stancetime": [0.25, 0.25]})], TODAY,
                       settings={"weight": 70.0})
    assert np.isnan(_ev(bike).evaluate("fmax", bike.workouts[0])).all()


def test_stancetime_is_ms_and_vertical_oscillation_cm_in_expressions():
    # .wko4 storage: stancetime s, verticaloscillation m; WKO5 hands them to
    # expressions in ms / cm, and the user's charts are written that way
    n = 4
    ds = FakeDataset([run_workout(dt.datetime(2026, 9, 20, 8), n=n, cadence=[85.0] * n,
                                  stancetime=[0.25, 0.25, 0.25, 0.0], power=[250.0] * n,
                                  verticaloscillation=[0.08] * n, speed=[12.0] * n)], TODAY,
                     settings={"weight": 70.0, "height": 1.80})
    e = _ev(ds)
    w = ds.workouts[0]
    assert e.evaluate("stancetime", w)[0] == pytest.approx(250.0)
    assert e.evaluate("verticaloscillation", w)[0] == pytest.approx(8.0)
    assert e.evaluate("metric(verticaloscillation)/100", w)[0] == pytest.approx(0.08)
    assert e.evaluate("height", w) == pytest.approx(180.0)
    # Palladino "Flight Phase": 1 - tc / step time (60000/170 ms)
    fp = e.evaluate("workoutrange(1, 3, avg(1-(stancetime/(60*1000/(cadence*2)))))", w)
    assert fp == pytest.approx(1 - 250 / (60000 / 170))
    # "Pwr-GCT": W per ms; a 0 ms sample is na (x/0), not inf
    pg = e.evaluate("power/stancetime", w)
    assert pg[0] == pytest.approx(1.0) and np.isnan(pg[-1])
    assert e.evaluate("avg(power/stancetime)", w) == pytest.approx(1.0)


def _grade_run(n, rise, run, **extra):
    """A run with a constant rise / horizontal run per sample (m)."""
    step = math.hypot(rise, run) / 1000.0
    return run_workout(dt.datetime(2026, 9, 20, 8), n=n,
                       _elevation=[100.0 + rise * i for i in range(n)],
                       elapseddistance=[step * i for i in range(n)], **extra)


def test_rgrade_is_rise_over_horizontal_run_gaussian_filtered():
    ds = _ds([_grade_run(40, 1.0, 10.0)])
    g = ev("rgrade", ds, ds.workouts[0])
    # rise / horizontal run (not / distance), the first sample bridged by the filter
    assert np.allclose(g, 0.1)
    # identical to WKO5's channel string evaluated as an expression
    s = _ev(ds).evaluate(E.RGRADE_EXPR, ds.workouts[0])
    assert np.allclose(g, s)


def test_rgrade_kernel_and_gaps():
    n = 40
    rise = [0.0] * 20 + [2.0] + [0.0] * 19        # one 2 m step at sample 20
    elev = list(np.cumsum([100.0] + rise[1:]))
    dist = [0.01 * i for i in range(n)]            # 10 m per sample
    dist[30] = dist[29]                            # standing: dDist = 0 -> na
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=n, _elevation=elev, elapseddistance=dist)])
    g = ev("rgrade", ds, ds.workouts[0])
    raw = np.full(n, np.nan)
    for i in range(1, n):
        de, dr = elev[i] - elev[i - 1], (dist[i] - dist[i - 1]) * 1000
        if dr * dr - de * de >= 0 and math.sqrt(dr * dr - de * de) >= 0.01:
            raw[i] = de / math.sqrt(dr * dr - de * de)
    k = np.array([math.exp(-j * j / 18.0) for j in range(-8, 9)])
    for i in (0, 12, 20, 25, 30, 39):
        ws = [(k[m], raw[i - 8 + m]) for m in range(17)
              if 0 <= i - 8 + m < n and not np.isnan(raw[i - 8 + m])]
        assert g[i] == pytest.approx(sum(a * b for a, b in ws) / sum(a for a, _ in ws))
    assert np.isnan(raw[30]) and g[20] > 0.02 and g[5] == 0.0


def test_rgrade_ignores_sub_centimetre_runs_and_derives_elevation():
    # the float residue of dDist = |dElev| (0.5 m vs 0.500000000000167 m) would give 1e6
    elev = [100.0, 100.0, 100.5, 100.5, 100.5]
    dist = [0.0, 0.01, 0.0105000000000002, 0.0205, 0.0305]
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=5, _elevation=elev, elapseddistance=dist)])
    g = ev("rgrade", ds, ds.workouts[0])
    assert np.nanmax(np.abs(g)) < 1e-9
    # no _elevation channel (a FIT import): WKO5's smoothing of elevation is used
    from backend.engine.algorithms.wko5_elevation import smooth_elevation
    n = 30
    el = [100.0 + (0.8 * i if i < 15 else 12.0) for i in range(n)]
    ds2 = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=n, elevation=el,
                           elapseddistance=[0.005 * i for i in range(n)])])
    w = ds2.workouts[0]
    sm = smooth_elevation([float(i) for i in range(1, n + 1)], el)
    assert np.allclose(ev("_elevation", ds2, w), sm)
    assert np.nanmax(ev("rgrade", ds2, w)) > 0.1


def test_rngp_is_run_only_min_per_km():
    n = 40
    ds = _ds([run_workout(dt.datetime(2026, 9, 20, 8), n=n, power=[200.0] * n,
                          speed=[12.0] * n, _elevation=[100.0] * n),
              FakeWorkout(dt.datetime(2026, 9, 21, 8), sport="walk",
                          channels={"elapsedtime": [float(i) for i in range(1, n + 1)],
                                    "speed": [12.0] * n, "_elevation": [100.0] * n})])
    pace = ev("rngp", ds, ds.workouts[0])
    assert pace[-1] == pytest.approx(5.0)          # 12 km/h on the flat = 5:00 min/km
    assert np.isnan(ev("rngp", ds, ds.workouts[1])).all()


def test_text_fields_read_wko5s_index_field_ids():
    import gzip
    from backend.files.wko5_athlete import WorkoutEntry, text_field
    from backend.files.wko5chart_reader import Field, Record
    note = "long note " * 20
    rec = Record([Field(3213, 3, "Mountaineering"), Field(3206, 3, "雪主單攻"),
                  Field(3207, 3, gzip.compress(note.encode("utf-8"))), Field(3210, 3, "Z2"),
                  Field(3209, 3, "Other")])
    entry = WorkoutEntry(file="x.wko4", sport="Mountaineering", sport_group="Other", start=None,
                         ftp=None, record=rec)
    assert (entry.title, entry.description, entry.notes, entry.code) == \
        ("Mountaineering", "雪主單攻", note, "Z2")
    assert text_field(None) == "" and text_field(b"abc") == "abc"
    assert E.TEXT_FIELDS == {"title": 3213, "description": 3206, "desc": 3206, "notes": 3207,
                             "code": 3210}
    ds = _ds([FakeWorkout(dt.datetime(2026, 9, 20, 8), sport="other")])
    ds.workouts[0].entry.record = rec
    e = _ev(ds)
    w = ds.workouts[0]
    assert e.evaluate("title", w) == "Mountaineering"
    assert e.evaluate("description", w) == "雪主單攻" == e.evaluate("desc", w)
    assert e.evaluate("notes", w) == note
    assert e.evaluate("code", w) == "Z2"
    assert e.evaluate('if(has(title,"Mountaineering"),description)') == WS({0: "雪主單攻"})


def test_parse_in_operator():
    assert isinstance(P.parse("x in y"), P.BinOp)
    P.parse('noinvalid(if(xx(@a) in xx(@b), @a))')
