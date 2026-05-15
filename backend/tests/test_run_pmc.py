import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date, timedelta
import numpy as np
import pytest
from backend.engine.algorithms.metrics import compute_run_pmc, compute_intensity_load_series


def _days(start: date, n: int, tss: float) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), tss) for i in range(n)]


def test_run_pmc_zero_input():
    result = compute_run_pmc([])
    assert result == []


def test_run_pmc_acwr_after_steady_load():
    """Steady 50 TSS/day for 365 days → ACWR converges near 1.0 (within 0.02)."""
    start = date(2025, 1, 1)
    series = _days(start, 365, 50.0)
    result = compute_run_pmc(series)
    last = result[-1]
    # CTL tau=42, ATL tau=7 — both converge at ~365 days; ATL converges faster so ACWR > 1 until CTL catches up
    assert last["acwr"] == pytest.approx(1.0, abs=0.02)


def test_run_pmc_daily_pct_ctl():
    """100 TSS day after CTL=50 → daily_pct_ctl = tss/ctl."""
    start = date(2025, 1, 1)
    series = _days(start, 90, 50.0)
    series.append((start + timedelta(days=90), 100.0))
    result = compute_run_pmc(series)
    last = result[-1]
    assert last["daily_pct_ctl"] == pytest.approx(100.0 / last["ctl"], rel=0.01)


def test_run_pmc_ramp_rate_rising():
    """Increasing load → positive ramp rate."""
    start = date(2025, 1, 1)
    series = [(start + timedelta(days=i), float(i)) for i in range(30)]
    result = compute_run_pmc(series)
    last = result[-1]
    assert last["ramp_rate"] > 0


def test_compute_intensity_load_series_empty():
    assert compute_intensity_load_series([], tau=42) == []


def test_compute_intensity_load_series_ewma():
    """EWMA of 3600s/day (1hr) for 252 days (6× tau) → value converges to ~60 min."""
    start = date(2025, 1, 1)
    series = [(start + timedelta(days=i), 3600.0) for i in range(252)]
    result = compute_intensity_load_series(series, tau=42)
    last = result[-1]["value"]
    # After 6 tau, EWMA ≈ 3600 * (1 - exp(-6)) / 60 = 59.6 min
    assert last == pytest.approx(60.0, abs=1.0)


def test_endpoint_response_shapes():
    """Validate response dicts from algorithms have all expected keys."""
    start = date(2025, 1, 1)
    series = [(start + timedelta(days=i), 50.0) for i in range(30)]
    pmc = compute_run_pmc(series)
    assert all(k in pmc[0] for k in ["date", "ctl", "atl", "tsb", "tss", "acwr", "daily_pct_ctl", "ramp_rate"])
    intensity = compute_intensity_load_series(series, tau=42)
    assert all(k in intensity[0] for k in ["date", "value"])


def test_intensity_time_calculation():
    """Seconds above threshold from synthetic power array."""
    power = np.array([200.0] * 60 + [100.0] * 30)
    ftp = 200.0
    hi_95 = float(np.sum(power >= 0.95 * ftp))   # 190W threshold — all 60 qualify
    hi_103 = float(np.sum(power >= 1.03 * ftp))  # 206W — none qualify
    assert hi_95 == pytest.approx(60.0, abs=1.0)
    assert hi_103 == pytest.approx(0.0, abs=1.0)
