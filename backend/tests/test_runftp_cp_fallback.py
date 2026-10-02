"""WKO5 "Weekly Time At or Over FTP" / "Weekly Time in Zones - % Run FTP" on a
source without a Run FTP setting (COROS / FIT: athlete_settings.run_ftp_w is
empty and the account-level ftp_w is ignored on purpose).

Both compare `runpower` with `runftp`; with no runftp every share was na and
the charts said 「這段期間沒有資料」. Outside parity mode the evaluator now
reads runftp as the CP in effect that day (the plan's CP test, else the FIT
dataset's Stryd-only PD fit, 推估) — what Dataset.cp / the `cp` identifier
already use — and the chart notice says so. Synthetic data only."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine.wko5expr.render import render_chart
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 2)
RUN_A = dt.datetime(2026, 9, 2, 7)          # before the CP test: the Stryd fit (250 W) applies
RUN_B = dt.datetime(2026, 9, 30, 18)        # the CP test day: 200 W
TEST_DAY = "2026-09-30"

SHARE_95 = ('sum(if(runpower >= runftp * 0.95, deltatime), "week") '
            '/ sum(if(isvalid(runpower), deltatime), "week")')
CHART = {"kind": "athlete", "title": "Weekly Time At or Over FTP",
         "series": [{"id": "s0", "name": "% of Time Over 95% (FTP)", "type": "bar",
                     "expression": SHARE_95, "y_axis": "PERCENT"}]}


def _run(start, watts):
    n = len(watts)
    return FakeWorkout(start, sport="run", channels={"elapsedtime": [float(i) for i in range(1, n + 1)],
                                                     "power": list(watts)})


def _ds(settings=None, parity=False, fit=True):
    # RUN_A: half at 230 W, half at 245 W; RUN_B: 200 W throughout
    ds = FakeDataset([_run(RUN_A, [230.0] * 50 + [245.0] * 50), _run(RUN_B, [200.0] * 100)],
                     TODAY, settings=settings)
    ds.config = SimpleNamespace(parity=parity)
    ds.plan = SimpleNamespace(thresholds=[SimpleNamespace(date=TEST_DAY, cp=200.0)])
    if fit:
        ds._cp_fit_on = lambda d: {"date": dt.date(2026, 8, 1), "cp": 250.0} if d >= dt.date(2026, 8, 1) else None
    return ds


def _points(res):
    return {d: v for d, v in res["series"][0]["data"]["points"] if v is not None}


def test_runftp_falls_back_to_the_cp_in_effect_each_day():
    ds = _ds()
    res = render_chart(CHART, ds, ds.today - 40, ds.today)
    pts = _points(res)
    # week of RUN_A: only the 245-W half >= 0.95 × 250 W (fit) -> 50 % (CP 200 would give 100 %);
    # week of RUN_B: 200 W >= 0.95 × 200 W (test) -> 100 %
    assert sorted(pts.values()) == [pytest.approx(0.5), pytest.approx(1.0)]
    assert res["empty"] is None
    n = res["notice"]
    assert "Run FTP" in n and "200 W" in n and "CP 測試 2026-09-30" in n and "推估" in n
    assert res["estimates"]["runftp"]["value"] == 200.0


def test_a_run_ftp_setting_wins():
    ds = _ds({"runftp": 240.0})
    res = render_chart(CHART, ds, ds.today - 40, ds.today)
    # 0.95 × 240 = 228 W: all of RUN_A, none of RUN_B (a week with no time over is na, as in WKO5)
    assert list(_points(res).values()) == [pytest.approx(1.0)]
    assert not res["notice"] and res["estimates"] == {}


def test_parity_mode_stays_as_wko5():
    ds = _ds(parity=True)
    res = render_chart(CHART, ds, ds.today - 40, ds.today)
    assert _points(res) == {} and res["empty"]


def test_no_cp_before_the_test_and_no_fit_leaves_that_week_blank():
    ds = _ds(fit=False)
    res = render_chart(CHART, ds, ds.today - 40, ds.today)
    assert list(_points(res).values()) == [pytest.approx(1.0)]       # only the test week
    assert "推估" not in res["notice"]
