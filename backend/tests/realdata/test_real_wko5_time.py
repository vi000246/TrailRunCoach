"""Real-data half of backend/tests/test_wko5_time.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine.algorithms.wko5_time import distance_range, moving_duration, pedaling_duration
from backend.tests.realdata._paths import ATHLETE_DIR


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_time_and_distance_match_wko5_stored_values():
    """Golden: movingduration (4213), pedalingduration (4214), distance (4217)."""
    from backend.files.wko5_athlete import read_athlete
    from backend.files.wko4_file import read_wko4
    a = read_athlete(next(ATHLETE_DIR.glob("*.wko5athlete")))
    checked = 0
    for w in a.workouts[::7]:
        m = w.metrics
        p = ATHLETE_DIR / w.file
        if not p.exists():
            continue
        f = read_wko4(p)
        t = f.channels.get("elapsedtime")
        if not t:
            continue
        sport = (w.sport_group or "").lower()
        sport = {"road bike": "bike"}.get(sport, sport)
        vals = lambda name: (f.channels[name].values if name in f.channels else None)
        if 4213 in m and (vals("speed") or vals("elapseddistance")):
            got = moving_duration(t.values, vals("speed"), sport, distance=vals("elapseddistance"))
            assert got == pytest.approx(m[4213], abs=1e-6), (w.file, "moving")
        if 4214 in m and vals("cadence"):
            assert pedaling_duration(t.values, vals("cadence")) == pytest.approx(m[4214], abs=1e-6), \
                (w.file, "pedaling")
        if 4217 in m and vals("elapseddistance"):
            dc = f.channels["elapseddistance"]
            begin, d = distance_range(dc.values, dc.base)
            assert d == pytest.approx(m[4217], rel=1e-6), (w.file, "distance")
            if 4216 in m:
                assert begin == pytest.approx(m[4216], abs=1e-6), (w.file, "begindistance")
        checked += 1
    assert checked >= 50
