"""Real-data half of backend/tests/test_wko5_pace.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine.algorithms.wko5_pace import ngp
from backend.tests.realdata._paths import ATHLETE_DIR


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
