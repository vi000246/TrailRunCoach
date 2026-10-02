"""Real-data half of backend/tests/test_wko5_elevation.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine.algorithms.wko5_elevation import smooth_elevation
from backend.tests.realdata._paths import ATHLETE_DIR


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_elevation_smoothing_matches_wko5_channel():
    """Golden: our _elevation == WKO5's stored _elevation channel."""
    from backend.files.wko4_file import read_wko4
    files = sorted(ATHLETE_DIR.rglob("*.wko4"))
    checked = 0
    for p in files[::9]:
        w = read_wko4(p)
        e, t, we = (w.channels.get("elevation"), w.channels.get("elapsedtime"),
                    w.channels.get("_elevation"))
        if not e or not t or not we:
            continue
        ours = smooth_elevation(t.values, e.values)
        for a, b in zip(ours, we.values):
            if a is None or b is None:
                assert a is None and b is None, p.name
            else:
                assert a == pytest.approx(b, abs=1e-9), p.name
        checked += 1
        if checked >= 25:
            break
    assert checked >= 10
