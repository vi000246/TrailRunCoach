"""WKO5 "Friel Pace Zones for Running" on a source without a threshold-pace
setting (COROS / FIT: athlete_settings.threshold_pace_s_per_km is empty).

Its From / To series are `lookup(runtpace, enddate) * {...}`: with no
`runtpace` setting every bound was na, so the table showed the levels and
percentages but no paces. The evaluator now falls back to the as-of estimate
thresholds.estimate_tpace (推估, the value the zones table already shows) and
the chart says so in its notice. Synthetic data only — no ~/WKO5, no DB."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import thresholds as TH
from backend.engine.wko5expr.render import render_chart
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 2)
TPACE = 6 + 29 / 60                       # 6:29 /km
FROM = (None, 1.29, 1.14, 1.06, 1, 0.97, 0.9)
TO = (1.29, 1.14, 1.06, 1, 0.97, 0.9, 0)

# the series of WKO5's chart (season view), copied so the test needs no WKO5 folder
FRIEL_PACE = {
    "kind": "athlete", "title": "Friel Pace Zones for Running",
    "series": [
        {"id": "series0", "name": "Level", "type": "line",
         "expression": '{"1","2","3","4","5a","5b","5c"}', "y_axis": "NONE"},
        {"id": "series2", "name": "From", "type": "line", "y_axis": "PACEKM",
         "expression": "@tpace:=(,lookup(runtpace,enddate)), @tpace * {na,1.29,1.14,1.06,1,0.97,0.9}"},
        {"id": "series3", "name": "To", "type": "line", "y_axis": "PACEKM",
         "expression": "@tpace:=(,lookup(runtpace,enddate)), @tpace * {1.29,1.14,1.06,1,0.97,0.9,0}"},
    ],
}


def _ds(settings=None, parity=False):
    ds = FakeDataset([FakeWorkout(dt.datetime(2026, 9, 28, 7))], TODAY, settings=settings)
    ds.config = SimpleNamespace(parity=parity)
    return ds


@pytest.fixture
def estimate(monkeypatch):
    calls = []

    def fake(ds, day):
        calls.append(day)
        return {"value": TPACE, "method": "cp", "reason": "推估：CP 204 W × 近 90 天 5 次 Stryd 路跑的速度／功率比"}
    monkeypatch.setattr(TH, "estimate_tpace", fake)
    return calls


def _series(res, name):
    return next(s for s in res["series"] if s["name"] == name)["data"]["values"]


def test_friel_pace_bounds_use_the_estimate_without_a_setting(estimate):
    ds = _ds()
    res = render_chart(FRIEL_PACE, ds, ds.today - 30, ds.today)
    frm, to = _series(res, "From"), _series(res, "To")
    assert frm[0] is None and to[-1] == 0                          # open ends, as in WKO5
    assert frm[1:] == pytest.approx([TPACE * k for k in FROM[1:]])
    assert to[:-1] == pytest.approx([TPACE * k for k in TO[:-1]])
    assert next(s for s in res["series"] if s["name"] == "From")["unit"]["kind"] == "pace"
    assert estimate == [TODAY]                                     # as of the range end
    assert res["empty"] is None
    assert "推估" in res["notice"] and "6:29 /km" in res["notice"]
    assert res["estimates"]["runtpace"]["value"] == pytest.approx(TPACE)


def test_a_threshold_pace_setting_wins_and_no_estimate_notice(estimate):
    ds = _ds({"runtpace": 5.0})
    res = render_chart(FRIEL_PACE, ds, ds.today - 30, ds.today)
    assert _series(res, "From")[4] == pytest.approx(5.0)
    assert estimate == [] and not res["notice"] and res["estimates"] == {}


def test_parity_mode_stays_as_wko5(estimate):
    ds = _ds(parity=True)
    res = render_chart(FRIEL_PACE, ds, ds.today - 30, ds.today)
    assert all(v is None for v in _series(res, "From"))
    assert estimate == []


def test_no_estimate_keeps_the_bounds_blank(monkeypatch):
    monkeypatch.setattr(TH, "estimate_tpace", lambda ds, day: {"value": None, "reason": "近 180 天只有 1 次路跑"})
    ds = _ds()
    res = render_chart(FRIEL_PACE, ds, ds.today - 30, ds.today)
    assert all(v is None for v in _series(res, "From"))
    assert res["estimates"] == {}
