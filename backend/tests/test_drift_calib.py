"""飄移視窗 per athlete (generalize-athlete plan B7, engine/drift_calib.py):
the warm-up / return windows fitted from the runner's stops, the manual-only
VI / τ / run-walk limits, and workout_review picking the values in effect up
(constants + cache key). Synthetic data only."""
from __future__ import annotations

import numpy as np
import pytest

from backend.engine import calibrate as CAL
from backend.engine import drift_calib as DC
from backend.engine import workout_review as WR


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)
    WR._CAL_MEMO["at"] = -1e9
    yield
    WR._CAL_MEMO["at"] = -1e9
    WR.apply_calibration()                      # back to the defaults for the other tests


def _run(stops, n=3600):
    """1 Hz speed 10 km/h with stops [(start s, length s)]."""
    t = np.arange(n, dtype=float)
    v = np.full(n, 10.0)
    for a, ln in stops:
        v[a:a + ln] = 0.0
    return t, v


def test_early_and_tail_values():
    rel, segs, end = DC.run_stops(*_run([(300, 30), (900, 40), (3300, 60), (3500, 30)]))
    assert DC.early_value(segs) == pytest.approx(900, abs=1)
    assert DC.tail_value(segs, end) == pytest.approx(end - 3300, abs=1)
    rel, segs, end = DC.run_stops(*_run([]))
    assert DC.early_value(segs) is None and DC.tail_value(segs, end) is None


def test_fit_needs_twenty_runs():
    assert DC._p95_fit([800.0] * 19) is None
    f = DC._p95_fit([600.0 + 10 * i for i in range(30)])
    assert f.n == 30 and 850 < f.value < 900
    item = CAL._registry()["drift_early_s"]
    e = CAL.shrink(item, f)                                          # w = 30/50 toward 1200
    assert 950 < e["value"] < 1100


def test_constants_follow_the_values_in_effect(monkeypatch):
    assert WR.apply_calibration() == "" and WR.DRIFT_EARLY_S == 1200 and WR.WALK_MAX_S == 180
    vals = {"drift_early_s": 900.0, "drift_tail_s": 720.0, "drift_max_vi": 1.06, "drift_tau_s": 60.0,
            "walk_max_s": 180.0}
    monkeypatch.setattr(CAL, "value", lambda name, user_id=1: vals[name])
    WR._CAL_MEMO["at"] = -1e9
    sfx = WR.apply_calibration()
    assert WR.DRIFT_EARLY_S == 900 and isinstance(WR.DRIFT_EARLY_S, int) and WR.DRIFT_MAX_VI == 1.06
    assert "DRIFT_EARLY_S=900" in sfx and "DRIFT_MAX_VI=1.06" in sfx and "DRIFT_TAIL_S" not in sfx


def test_manual_only_items_are_listed_but_never_fitted():
    for n in ("drift_max_vi", "drift_tau_s", "walk_max_s"):
        it = CAL._registry()[n]
        assert it.manual_only and CAL.shrink(it, it.fit(None, None)) is None
        assert "手動指定" in CAL.describe(n, None)["chip"]["tip"]
