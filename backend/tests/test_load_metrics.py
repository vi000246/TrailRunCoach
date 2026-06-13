import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import numpy as np
import pytest
from backend.engine.algorithms.metrics import compute_load_metrics


def test_load_metrics_hr_and_pace():
    hr = np.array([160.0] * 3600)
    out = compute_load_metrics(
        hr=hr, duration_s=3600, distance_m=12000,
        lthr=160, threshold_pace_s_per_km=300.0,
    )
    assert out["hr_tss"] == pytest.approx(100.0, abs=5.0)
    # threshold pace 300 s/km = 0.30 s/m; avg pace = 3600/12000 = 0.30 → IF=1 → rTSS=100
    assert out["r_tss"] == pytest.approx(100.0, abs=1.0)


def test_load_metrics_missing_inputs_omitted():
    out = compute_load_metrics(hr=None, duration_s=3600, distance_m=None,
                               lthr=None, threshold_pace_s_per_km=None)
    assert "hr_tss" not in out
    assert "r_tss" not in out
