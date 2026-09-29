"""WKO5 grade-adjusted pace / NGP / rTSS (backend/engine/algorithms/wko5_pace.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.engine.algorithms.wko5_pace import ngp, ragpace, run_tss

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))


def _flat(n, kmh=12.0, alt=100.0):
    t = [float(i) for i in range(1, n + 1)]
    return t, [alt] * n, [kmh] * n


def test_flat_running_ngp_equals_actual_pace():
    t, e, s = _flat(600, kmh=12.0)          # 12 km/h = 5:00 min/km
    g, dur, _ = ngp(t, e, s)
    assert g == pytest.approx(5.0, rel=1e-6)
    assert dur == pytest.approx(600.0, abs=1.0)


def test_uphill_makes_ngp_faster_than_actual_pace():
    n = 600
    t = [float(i) for i in range(1, n + 1)]
    e = [100.0 + i * 0.05 for i in range(n)]    # 5% climb
    s = [10.0] * n                              # 6:00 min/km actual
    g, _, _ = ngp(t, e, s)
    assert g < 6.0                              # grade-adjusted pace is quicker


def test_downhill_makes_ngp_slower_than_actual_pace():
    n = 600
    t = [float(i) for i in range(1, n + 1)]
    e = [100.0 - i * 0.05 for i in range(n)]
    g, _, _ = ngp(t, e, [10.0] * n)
    assert g > 6.0


def test_ragpace_runs_on_its_own_one_second_grid():
    t = [2.0, 4.0, 6.0]                        # 2 s samples
    gt, gv = ragpace(t, [100.0] * 3, [12.0] * 3)
    assert gt == pytest.approx([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert all(v is not None for v in gv[1:])


def test_no_speed_or_elevation_gives_no_ngp():
    t = [float(i) for i in range(1, 11)]
    assert ngp(t, [None] * 10, [None] * 10)[0] is None


def test_rtss_formula_as_disassembled():
    """UNVERIFIED against WKO5's UI: (d/60)^1.025 * IF^2 / 60 * 100.

    Note this does NOT give 100 for an hour at threshold pace (power TSS
    does) — the 1.025 exponent makes it ~110.8. Confirm against WKO5 before
    trusting rTSS absolutely; it only affects runs with no power meter.
    """
    assert run_tss(3600, 5.0, 5.0) == pytest.approx(60 ** 1.025 / 60 * 100)
    assert run_tss(3600, 5.0, 5.0) == pytest.approx(110.778, abs=1e-3)
    # IF scales quadratically
    assert run_tss(3600, 5.0, 10.0) == pytest.approx(run_tss(3600, 5.0, 5.0) / 4)
    assert run_tss(3600, None, 5.0) is None
    assert run_tss(0, 5.0, 5.0) is None


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_ngp_and_rtss_duration_match_wko5_stored_values():
    """Golden: NGP (4230) and rTSS duration (4249) recomputed from samples."""
    from backend.files.wko5_athlete import read_athlete
    from backend.files.wko4_file import read_wko4
    a = read_athlete(next(ATHLETE_DIR.glob("*.wko5athlete")))
    checked = 0
    for w in a.workouts[::9]:
        if 4230 not in w.metrics:
            continue
        f = read_wko4(ATHLETE_DIR / w.file)
        t, e, s = (f.channels.get(k) for k in ("elapsedtime", "_elevation", "speed"))
        if not (t and e and s):
            continue
        g, dur, _ = ngp(t.values, e.values, s.values)
        assert g == pytest.approx(w.metrics[4230], rel=1e-5), w.file
        if 4249 in w.metrics:
            assert dur == pytest.approx(w.metrics[4249], abs=2.0), w.file
        checked += 1
        if checked >= 20:
            break
    assert checked >= 10
