"""Real-data half of backend/tests/test_wko5_perf.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import os
import datetime as dt
from pathlib import Path
import pytest
from backend.engine.algorithms.wko5_meanmax import _fast_meanmax, duration_grid, meanmax_curve_reference
from backend.tests.realdata._paths import ATHLETE_DIR
from backend.tests.test_wko5_perf import CHANNELS, _dt


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_fast_path_matches_reference_on_real_workouts():
    from backend.files.wko4_file import read_wko4
    checked = 0
    for p in sorted(ATHLETE_DIR.rglob("*.wko4"))[::37]:
        w = read_wko4(p)
        t = w.channels.get("elapsedtime")
        if not t or not t.values:
            continue
        for ch in CHANNELS:
            c = w.channels.get(ch)
            if not c:
                continue
            dx = _dt(list(t.values))
            grid = duration_grid(t.values[-1])
            if not grid:
                continue
            fast = _fast_meanmax(dx, dx, list(c.values), grid)
            if fast is None:
                continue
            ref = meanmax_curve_reference(dx, dx, list(c.values), grid)
            for a, b in zip(fast, ref):
                assert (a is None) == (b is None), (p.name, ch)
                if a is not None:
                    assert a == pytest.approx(b, rel=1e-11), (p.name, ch)
            checked += 1
    assert checked >= 20


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_channel_peaks_cache_matches_a_direct_scan():
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.dataset import Dataset
    ds = Dataset(ATHLETE_DIR, today=dt.date(2026, 9, 29), config=EngineConfig(parity=True))
    peaks = ds.channel_peaks("power")
    assert len(peaks) > 300
    for w in ds.workouts[::53]:
        f = ds.wko4(w.idx)
        c = f.channels.get("power") if f else None
        good = [v for v in c.values if v is not None] if c else []
        assert peaks.get(w.entry.file) == (max(good) if good else None)


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_cached_series_is_invalidated_by_a_correction():
    """A cache that survived an approved correction would silently show stale data."""
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.corrections import Correction, CorrectionStore
    from backend.engine.wko5expr.dataset import Dataset

    store = CorrectionStore(path=Path(os.devnull + ".json"))   # never written back
    store.items = []
    ds = Dataset(ATHLETE_DIR, today=dt.date(2026, 9, 29),
                 config=EngineConfig(parity=False), corrections=store)
    w = next(w for w in ds.workouts if (ds.wko4(w.idx) or None)
             and ds.wko4(w.idx).channels.get("power"))
    calls = []

    def build():
        calls.append(1)
        return [[1.0], [2.0]]

    ds.cached_series("probe", w, build)
    ds.cached_series("probe", w, build)
    assert len(calls) == 1, "second call should hit the cache"

    store.items = [Correction(file=w.entry.file, channel="power", t_start=0.0, t_end=9.0)]
    ds.cached_series("probe", w, build)
    assert len(calls) == 2, "an approved correction must invalidate the cache"
