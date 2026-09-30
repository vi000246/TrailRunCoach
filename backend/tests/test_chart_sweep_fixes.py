"""Regressions from the full chart sweep (docs/reports/chart-sweep.md): charts
that drew nothing, or drew bare axes, although the data was there — plus the
"why is this empty" sentence for the ones whose data really is absent."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
from pathlib import Path

import numpy as np

from backend.api.wko5views import _panel_kind
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.render import (
    chart_is_empty, render_chart, render_map, result_to_json, workout_result_to_json,
)
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)          # a Wednesday
VIEWER = Path(__file__).resolve().parents[1] / "static" / "wko5_viewer.html"


def _run(day, secs=600, power=None, hr=140.0, cadence=85.0, **metrics):
    t = list(range(1, secs + 1))
    ch = {"elapsedtime": t, "heartrate": [hr] * secs, "speed": [10.0] * secs}
    if power is not None:
        ch["power"] = list(power) if hasattr(power, "__len__") else [power] * secs
    if cadence is not None:
        ch["cadence"] = [cadence] * secs
    return FakeWorkout(dt.datetime.combine(day, dt.time(7)), "run", channels=ch, metrics=metrics)


def _chart(*exprs, kind="athlete", types=None):
    return {"title": "t", "kind": kind, "axes": [],
            "series": [{"id": f"s{i}", "name": f"s{i}", "type": (types or {}).get(i, "line"),
                        "expression": e, "y_axis": "NONE", "x_axis": "NONE"}
                       for i, e in enumerate(exprs)]}


# ---- evaluator: WKO5's cached curve must not undo an approved correction ----

class _CachedDs(FakeDataset):
    """A dataset whose WKO5 Cache5 holds a spiky curve for workout 0."""

    def __init__(self, *a, corrected=False, **k):
        super().__init__(*a, **k)
        self.corrected = corrected

    def curve_cache(self, expr):
        return {"fake/0.wko4": ([1.0, 5.0, 60.0], [1600.0, 1500.0, 1300.0])}

    def _corr_sig(self, file, channel=None):
        return "power:0-5" if self.corrected and file == "fake/0.wko4" else ""


def test_cached_curve_used_without_corrections():
    ds = _CachedDs([_run(TODAY - dt.timedelta(days=3), power=200.0)], TODAY)
    c = Evaluator(ds, ds.today - 30, ds.today).evaluate("meanmax(power)")
    assert max(c.ys) == 1600.0


def test_cached_curve_bypassed_when_file_has_approved_correction():
    """Regression: approving a spike correction had no effect on mean-max /
    PD charts, because the WKO5 cache (raw samples) was read first."""
    ds = _CachedDs([_run(TODAY - dt.timedelta(days=3), power=200.0)], TODAY, corrected=True)
    c = Evaluator(ds, ds.today - 30, ds.today).evaluate("meanmax(power)")
    assert max(c.ys) == 200.0


# ---- render: pair shapes -----------------------------------------------------

def test_pair_of_per_workout_series_is_one_point_per_workout():
    """Regression (EPH by EP): (@ep, @eph) over workouts rendered as a single
    null point."""
    ws = [_run(TODAY - dt.timedelta(days=d), distance=10.0 + d, climbing=100.0 * d, duration=3600.0)
          for d in (1, 2, 3)]
    ds = FakeDataset(ws, TODAY)
    ev = Evaluator(ds, ds.today - 30, ds.today)
    js = result_to_json(ev.evaluate("@ep := climbing/100 + distance, (@ep, @ep/(duration/3600))"),
                        ds, int(ds.today) - 30, int(ds.today))
    assert js["kind"] == "points" and js["x"] == "value"
    assert sorted(p[0] for p in js["points"]) == [12.0, 14.0, 16.0]
    assert all(p[0] == p[1] for p in js["points"])


def test_x_only_pair_is_a_vertical_line():
    ds = FakeDataset([], TODAY)
    ev = Evaluator(ds, ds.today - 30, ds.today)
    assert result_to_json(ev.evaluate("(153,)"), ds, 0, 1) == {"kind": "vline", "x": 153.0}
    assert result_to_json(ev.evaluate("(,5)"), ds, 0, 1) == {"kind": "hline", "y": 5.0}


def test_workout_x_only_pair_and_scalar_pair():
    """Regression: (avg(power),) on HR-vs-power charts came back as an empty
    value instead of a vertical line at the average power."""
    ds = FakeDataset([_run(TODAY, power=200.0)], TODAY)
    w = ds.workouts[0]
    ev = Evaluator(ds, ds.today - 30, ds.today)
    assert workout_result_to_json(ev.evaluate("(avg(power),)", w), ds, w) == {"kind": "vline", "x": 200.0}
    js = workout_result_to_json(ev.evaluate("(avg(power), avg(heartrate))", w), ds, w)
    assert js == {"kind": "points", "x": "value", "points": [[200.0, 140.0]]}


def test_workout_sample_scatter_is_not_a_time_plot():
    """Regression: (ewma(power,30), heartrate) was labelled x="seconds", so the
    viewer dropped it from the power axis of its own chart."""
    ds = FakeDataset([_run(TODAY, power=np.linspace(100, 300, 600))], TODAY)
    w = ds.workouts[0]
    ev = Evaluator(ds, ds.today - 30, ds.today)
    assert workout_result_to_json(ev.evaluate("(power, heartrate)", w), ds, w)["x"] == "value"
    assert workout_result_to_json(ev.evaluate("(elapsedtime, heartrate)", w), ds, w)["x"] == "seconds"


# ---- render: why is the chart empty -----------------------------------------

def test_workout_without_power_says_so():
    ds = FakeDataset([_run(TODAY, power=None, cadence=None)], TODAY)
    res = render_chart(_chart("power", "(avg(power),)", "cadence*2", kind="workout"),
                       ds, ds.today - 30, ds.today, workout=ds.workouts[0])
    assert res["empty"] and "功率" in res["empty"] and "步頻" in res["empty"]


def test_workout_with_data_is_not_empty():
    ds = FakeDataset([_run(TODAY, power=200.0)], TODAY)
    res = render_chart(_chart("power", kind="workout"), ds, ds.today - 30, ds.today,
                       workout=ds.workouts[0])
    assert res["empty"] is None


def test_week_without_activity_says_so():
    """This Week Climbing etc. on a week with no workout yet."""
    ds = FakeDataset([_run(TODAY - dt.timedelta(days=10), climbing=300.0)], TODAY)
    res = render_chart(_chart("athleterange(startofweek(today),startofweek(today)+6,sum(climbing))",
                              types={0: "gauge"}), ds, ds.today - 30, ds.today)
    assert res["empty"] and "本週" in res["empty"]


def test_pd_model_failure_is_explained():
    # 10 minutes of power only: the model needs a curve out to >= 40 minutes
    ds = _CachedDs([_run(TODAY - dt.timedelta(days=2), power=200.0)], TODAY)
    res = render_chart(_chart("pdcurve(meanmax(runpower))", "(153,)"), ds, ds.today - 30, ds.today)
    assert res["empty"] and "PD" in res["empty"]


def test_partial_pd_chart_gets_a_notice():
    """Donny's targeting / PD Curve with Metrics: the mean-max line draws, the
    PD lines don't — the card says why instead of silently missing them."""
    ds = _CachedDs([_run(TODAY - dt.timedelta(days=2), power=200.0)], TODAY)
    res = render_chart(_chart("meanmax(runpower)", "pdcurve(meanmax(runpower))"), ds, ds.today - 30, ds.today)
    assert res["empty"] is None and res["notice"] and "PD" in res["notice"]
    ok = render_chart(_chart("meanmax(runpower)"), ds, ds.today - 30, ds.today)
    assert ok["notice"] is None


def test_power_metric_on_a_workout_without_power_names_the_channel():
    ds = FakeDataset([_run(TODAY, power=None)], TODAY)
    res = render_chart(_chart("np", kind="workout"), ds, ds.today - 30, ds.today, workout=ds.workouts[0])
    assert res["empty"] and "功率" in res["empty"]


def test_report_of_single_values_is_not_empty():
    """MMP Peaks and Clusters Report: only per-range numbers (hlines)."""
    assert not chart_is_empty([{"expression": "max(meanmax(power,5))", "data": {"kind": "hline", "y": 800.0}}])
    # ...but constant lines next to empty data series are an empty chart (EPH=5 on no points)
    assert chart_is_empty([{"expression": "(x, y)", "data": {"kind": "points", "x": "value", "points": []}},
                           {"expression": "5", "data": {"kind": "hline", "y": 5.0}}])


# ---- map panel ----------------------------------------------------------------

def test_map_panel_kind_and_gps_check():
    assert _panel_kind({"kind": "other", "class": "PKMapPanelConfig"}) == "map"
    assert _panel_kind({"kind": "workout"}) == "workout"
    fw = _run(TODAY, secs=10)
    fw.channels.update(latitude=[25.0 + i * 1e-4 for i in range(10)],
                       longitude=[121.5] * 10, elevation=[100.0 + i for i in range(10)])
    ds = FakeDataset([fw, _run(TODAY - dt.timedelta(days=1))], TODAY)
    js = render_map({"kind": "other"}, ds, ds.workouts[1])
    assert js["kind"] == "map" and js["empty"] is None and js["workout"] == ds.workouts[1].idx
    # the Leaflet map draws from /samples; the panel no longer ships a track of its own
    assert "track" not in js
    none = render_map({"kind": "other"}, ds, ds.workouts[0])
    assert "GPS" in none["empty"] and "track" not in none
    zero = _run(TODAY + dt.timedelta(days=1), secs=5)
    zero.channels.update(latitude=[0.0] * 5, longitude=[0.0] * 5)
    ds2 = FakeDataset([zero], TODAY)
    assert "GPS" in render_map({"kind": "other"}, ds2, ds2.workouts[0])["empty"]


# ---- viewer --------------------------------------------------------------------

def test_viewer_draws_the_new_shapes():
    html = VIEWER.read_text(encoding="utf-8")
    # a one-number gauge arrives as an hline (athlete) — it was never drawn
    assert '["values", "value", "hline", "none"].includes(s.data.kind)' in html
    assert 'class="nodata"' in html and "res.empty" in html
    assert "function drawMap(" in html and 'res.kind === "map"' in html
    assert 's.data.kind === "vline"' in html
    assert 's.line_style === "none"' in html and 'type: "scatter"' in html
    # hline-only reports are listed as a table
    assert '!hasPoints && s.data.kind === "hline"' in html
