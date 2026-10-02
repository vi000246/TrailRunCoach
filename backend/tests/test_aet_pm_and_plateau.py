"""The 「± N bpm」 badge on an estimated AeT (zones.aet_uncertainty) and the
optional mFTP plateau line on power-duration charts (render.mftp_plateau).
Synthetic data only."""
import numpy as np
import pytest

from backend.engine.algorithms.threshold_estimate import DriftPoint, estimate_aet
from backend.engine.wko5expr import render as R
from backend.engine.zones import AET_PM_BORROWED, AET_PM_FLOOR, aet_uncertainty


def test_measured_aet_has_no_badge():
    assert aet_uncertainty("measured") is None and aet_uncertainty(None) is None


def test_estimates_without_a_standard_error_borrow_16_bpm_and_say_so():
    for kind in ("friel", "estimate"):
        p = aet_uncertainty(kind)
        assert p["pm"] == AET_PM_BORROWED == 16
        assert "Micheli 2025" in p["tip"] and "推估" in p["tip"]
    assert "0.89 × LTHR" in aet_uncertainty("friel")["tip"]


def test_a_regression_shows_two_standard_errors_never_below_the_day_to_day_floor():
    assert aet_uncertainty("regression", 4.2)["pm"] == 8
    assert aet_uncertainty("regression", 0.5)["pm"] == AET_PM_FLOOR
    assert aet_uncertainty("regression", None)["pm"] == AET_PM_BORROWED     # no SE: the borrowed one


def test_estimate_aet_reports_the_crossing_se():
    rng = np.random.default_rng(1)
    hr = np.linspace(130, 160, 10)
    pts = [DriftPoint(float(h), float(0.05 + 0.004 * (h - 145) + rng.normal(0, 0.005)), 180.0) for h in hr]
    ae = estimate_aet(pts)
    assert ae.value == pytest.approx(145, abs=3) and ae.se is not None and 0 < ae.se < 5
    exact = estimate_aet([DriftPoint(float(h), 0.05 + 0.004 * (h - 145), 180.0) for h in hr])
    assert exact.se == pytest.approx(0.0, abs=1e-6)                       # a perfect line: no spread


class _Ev:
    def __init__(self, value):
        self.value, self.calls = value, []

    def evaluate(self, expr):
        self.calls.append(expr)
        return self.value


def _curve(expr, unit="WATTS", x="duration"):
    return {"expression": expr, "y_axis": "WATTS", "unit": {"id": unit},
            "data": {"kind": "points", "x": x, "points": [[1, 600], [60, 400], [3600, 240]]}}


def test_plateau_from_the_pd_curve_or_the_mean_max_curve():
    ev = _Ev(251.4)
    p = R.mftp_plateau(ev, [_curve("pdcurve(meanmax(runpower))")])
    assert p == {"y": 251.4, "y_axis": "WATTS", "label": "mFTP 251 W", "tip": R.PLATEAU_TIP}
    assert ev.calls == ["ftp(meanmax(runpower))"]
    ev = _Ev(240.0)
    assert R.mftp_plateau(ev, [_curve("meanmax(power)")])["y"] == 240.0 and ev.calls == ["ftp(meanmax(power))"]


def test_no_plateau_off_power_duration_charts_or_without_a_fit():
    assert R.mftp_plateau(_Ev(250.0), [_curve("meanmax(heartrate)", unit="BPM")]) is None
    assert R.mftp_plateau(_Ev(250.0), [_curve("ctl", x="date")]) is None
    assert R.mftp_plateau(_Ev(float("nan")), [_curve("pdcurve(meanmax(runpower))")]) is None
    # the chart already draws ftp() itself: no second line
    assert R.mftp_plateau(_Ev(250.0), [_curve("meanmax(runpower)"),
                                       {"expression": "ftp(meanmax(runpower))", "data": {"kind": "hline", "y": 250}}]) is None
