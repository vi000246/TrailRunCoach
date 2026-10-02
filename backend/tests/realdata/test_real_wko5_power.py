"""Real-data half of backend/tests/test_wko5_power.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine.algorithms.wko5_power import normalized_power
from backend.tests.realdata._paths import ATHLETE_DIR


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
