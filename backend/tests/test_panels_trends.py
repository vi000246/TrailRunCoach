"""backend/engine/panels/trends.py."""
import numpy as np
import pytest

from backend.engine.panels import trends as T


def test_efficiency_no_drift():
    t = np.arange(0, 4200.0)
    r = T.efficiency(t, np.full(len(t), 180.0), np.full(len(t), 140.0))
    assert r["ef"] == pytest.approx(180 / 140)
    assert r["decoupling_pct"] == pytest.approx(0)


def test_efficiency_detects_decoupling():
    t = np.arange(0, 4200.0)
    hr = np.where(t < 2400, 140.0, 150.0)  # second half (after warm-up) higher HR
    r = T.efficiency(t, np.full(len(t), 180.0), hr)
    assert r["decoupling_pct"] == pytest.approx((150 / 140 - 1) * 100, rel=0.05)


def test_efficiency_short_run_is_none():
    t = np.arange(0, 650.0)
    assert T.efficiency(t, np.ones(len(t)), np.full(len(t), 140.0)) is None


@pytest.mark.parametrize("moving,iff,hr,aet,lthr,ok", [
    (3000, 0.7, None, None, None, False),     # too short
    (4000, 0.75, None, None, None, True),
    (4000, 0.9, None, None, None, False),     # too hard
    (4000, None, 145, 150, None, True),       # HR under AeT
    (4000, None, 160, None, 170, False),      # 0.89 * 170 = 151
    (4000, None, None, None, None, False),
])
def test_qualifies_easy(moving, iff, hr, aet, lthr, ok):
    assert T.qualifies_easy(moving, iff, hr, aet, lthr) is ok


def test_rolling_trend_median():
    pts = [("a", 1.0), ("b", 10.0), ("c", 2.0)]
    assert T.rolling_trend(pts, n=3)[-1] == ["c", 2.0]
