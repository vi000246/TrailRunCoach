"""Real-data half of backend/tests/test_wko5_hr.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine.algorithms.wko5_hr import hr_tss
from backend.tests.realdata._paths import ATHLETE_DIR


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_hrtss_and_hrif_match_wko5_stored_values():
    """Golden: hrTSS (4235) / hrIF (4236) recomputed from samples."""
    from backend.files.wko5_athlete import read_athlete
    from backend.files.wko4_file import read_wko4
    a = read_athlete(next(ATHLETE_DIR.glob("*.wko5athlete")))
    prefix = {"run": "run", "bike": "bike", "road bike": "bike", "swim": "swim"}
    checked = 0
    for w in a.workouts[::5]:  # every 5th workout keeps it fast; full run 1030/1030
        if 4235 not in w.metrics:
            continue
        f = read_wko4(ATHLETE_DIR / w.file)
        hr, t = f.channels.get("heartrate"), f.channels.get("elapsedtime")
        if not hr or not t:
            continue
        lthr = a.setting_on(prefix.get((w.sport_group or "").lower(), "other") + "thr", w.start.date())
        s, iff = hr_tss(t.values, hr.values, lthr)
        assert s == pytest.approx(w.metrics[4235], rel=1e-9), w.file
        assert iff == pytest.approx(w.metrics[4236], rel=1e-9), w.file
        checked += 1
    assert checked >= 100
