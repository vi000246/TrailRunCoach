"""NP / tssduration / power TSS (backend/engine/algorithms/wko5_power.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.engine.algorithms.wko5_power import normalized_power, power_tss, rapower

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\WKO5\Athlete"))


def test_constant_power_np_equals_power_and_counts_seconds():
    t = [float(i) for i in range(1, 601)]           # 10 min at 1 Hz
    np_, dur = normalized_power(t, [200.0] * 600)
    assert np_ == pytest.approx(200.0)
    assert dur == 600


def test_np_weights_hard_efforts_above_average():
    t = [float(i) for i in range(1, 1201)]
    p = [100.0] * 600 + [300.0] * 600                # avg 200
    np_, _ = normalized_power(t, p)
    assert np_ > 200.0


def test_rapower_divides_by_valid_coverage_when_at_least_27s():
    t = [float(i) for i in range(1, 61)]
    v = [100.0] * 60
    v[40:43] = [None, None, None]                     # 3 s gap -> 27 s valid in window
    ra = dict(rapower(t, v))
    assert ra[45.0] == pytest.approx(100.0)          # sum / validcov, not / covered


def test_rapower_skips_seconds_without_valid_samples():
    t = [1.0, 2.0, 3.0]
    assert rapower(t, [None, None, None]) == []
    assert normalized_power(t, [None, None, None]) == (None, 0.0)


def test_power_tss_one_hour_at_ftp_is_100():
    assert power_tss(250.0, 3600.0, 250.0) == pytest.approx(100.0)
    assert power_tss(None, 3600.0, 250.0) is None
    assert power_tss(250.0, 0.0, 250.0) is None


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_np_and_tssduration_match_wko5_stored_values():
    """Golden: NP (4219) and tssduration (4248) recomputed from samples."""
    from backend.files.wko5_athlete import read_athlete
    from backend.files.wko4_file import read_wko4
    a = read_athlete(next(ATHLETE_DIR.glob("*.wko5athlete")))
    checked = 0
    for w in a.workouts:
        if w.metrics.get(4219) is None:
            continue
        f = read_wko4(ATHLETE_DIR / w.file)
        pw, t = f.channels.get("power"), f.channels.get("elapsedtime")
        np_, dur = normalized_power(t.values, pw.values)
        assert np_ == pytest.approx(w.metrics[4219], rel=1e-9), w.file
        assert dur == w.metrics[4248], w.file
        checked += 1
        if checked >= 60:  # keep the suite fast; full run verified 359/359
            break
    assert checked >= 30
