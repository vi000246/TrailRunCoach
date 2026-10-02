"""Real-data half of backend/tests/test_wko5_meanmax.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine.algorithms.wko5_meanmax import meanmax_time
from backend.tests.realdata._paths import ATHLETE_DIR


CACHE = ATHLETE_DIR / "Cache5"


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
