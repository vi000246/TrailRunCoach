"""WKO5 mean-max (backend/engine/algorithms/wko5_meanmax.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.engine.algorithms.wko5_meanmax import duration_grid, meanmax_time

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))
CACHE = ATHLETE_DIR / "Cache5"


def _t(n):
    return [float(i) for i in range(1, n + 1)]


def test_duration_grid_is_geometric_plus_fixed_points():
    g = duration_grid(3600)
    assert g[:4] == [1, 2, 3, 4]              # round(1.05^i)
    for fixed in (5, 60, 300, 1200, 3600):
        assert fixed in g
    assert max(g) <= 3600
    assert duration_grid(30)[-1] <= 30


def test_best_window_finds_the_hard_segment():
    v = [100.0] * 100 + [400.0] * 60 + [100.0] * 100
    _, best = meanmax_time(_t(260), v, durations=[60, 120])
    assert best[0] == pytest.approx(400.0)
    assert best[1] == pytest.approx((400 * 60 + 100 * 60) / 120)


def test_window_is_continuous_and_prorated_on_irregular_samples():
    # one 10 s sample at 300 W, then 1 s samples at 100 W
    t = [10.0] + [10.0 + i for i in range(1, 11)]
    v = [300.0] + [100.0] * 10
    _, best = meanmax_time(t, v, durations=[10])
    assert best[0] == pytest.approx(300.0)     # the whole 10 s sample
    _, best15 = meanmax_time(t, v, durations=[15])
    assert best15[0] == pytest.approx((300 * 10 + 100 * 5) / 15)


def test_mostly_valid_window_divides_by_valid_time_only():
    v = [200.0] * 100
    v[50] = None                                # 59/60 valid = 98.3% > 98%
    _, best = meanmax_time(_t(100), v, durations=[60])
    assert best[0] == pytest.approx(200.0)


def test_gappy_window_divides_by_total_time():
    v = [200.0] * 30 + [None] * 30 + [200.0] * 30   # only 50% valid in any 60 s window
    _, best = meanmax_time(_t(90), v, durations=[60])
    assert best[0] == pytest.approx(200.0 * 30 / 60)


def test_duration_longer_than_the_workout_gives_nothing():
    _, best = meanmax_time(_t(10), [200.0] * 10, durations=[600])
    assert best[0] is None


@pytest.mark.golden
@pytest.mark.skipif(not CACHE.exists(), reason="no WKO5 athlete folder")
def test_meanmax_matches_wko5_cached_curves():
    """Golden: WKO5's own cached meanmax(power) curve, point for point."""
    from backend.files.wko5chart_reader import decode_file, Record
    from backend.files.wko4_file import read_wko4, decode_channel
    src = next((p for p in CACHE.glob("*.wko5cache")
                if (decode_file(p).fields[0].value.get(461) if decode_file(p).fields else None) == "meanmax(power)"), None)
    if src is None:
        pytest.skip("no cached meanmax(power)")
    root = decode_file(src).fields[0].value
    checked = 0
    for e in root.get(601).all(102):
        chans = {}
        for c in (e.get(116).all(4403) if isinstance(e.get(116), Record) else []):
            body = c.get(102)
            blk = body.get(121) if isinstance(body, Record) else None
            if isinstance(blk, bytes):
                chans[c.get(101)] = decode_channel(c.get(101), blk).values
        xs, ys = chans.get("x"), chans.get("y")
        if not xs:
            continue
        w = read_wko4(ATHLETE_DIR / e.get(117).split(":", 1)[-1])
        pw, t = w.channels.get("power"), w.channels.get("elapsedtime")
        if not pw or not t:
            continue
        grid, mm = meanmax_time(t.values, pw.values)
        assert [int(x) for x in xs] == grid, e.get(117)
        for x, y in zip(xs, ys):
            assert dict(zip(grid, mm))[int(x)] == pytest.approx(y, rel=1e-9), (e.get(117), x)
        checked += 1
        if checked >= 15:
            break
    assert checked >= 5
