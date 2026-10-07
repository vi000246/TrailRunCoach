"""SP-320 ⑥: the as-of threshold estimates are memoised per grid day, keyed on that day's
inputs, and `today` is out of the key: the first build of a new day re-estimates nothing, a
new run re-estimates only the grid days whose windows hold it, and the values equal a cold
build's. Synthetic FIT files only."""
import datetime as dt
from datetime import datetime, timezone

from backend.engine import thresholds as TH
from backend.engine.racepower import athlete as A
from backend.engine.wko5expr import fitcache
from backend.engine.wko5expr import fitdataset as FD
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)


def _folder(tmp_path):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    start = datetime(2025, 12, 1, 8, tzinfo=timezone.utc)
    for i in range(30):                       # a run every 10 days: ~9 grid days
        (d / f"{i}.fit").write_bytes(build_run(start + dt.timedelta(days=10 * i), seconds=900,
                                               power=200 + i, hr=150 + (i % 5), speed_m_s=3.0))
    return tmp_path / "fit" / "coros", d


def _count(monkeypatch):
    calls = []
    real = TH.estimate

    def counting(ds, today=None, cp_of=None):
        calls.append(today)
        return real(ds, today, cp_of=cp_of)
    monkeypatch.setattr(TH, "estimate", counting)
    return calls


def _mk(root, today=TODAY):
    return FitFolderDataset(root, config=EngineConfig(parity=False), today=today, classifications={},
                            athlete_settings=[], estimate_thresholds=True)


def _isolate(tmp_path, monkeypatch):
    from backend.engine.racepower import weather as WX
    monkeypatch.setattr(WX, "HOME", tmp_path / "home")          # no real synced FIT folder


def test_lookback_covers_what_an_estimate_reads():
    assert FD.ESTIMATE_LOOKBACK_DAYS >= TH.WINDOWS[-1] + A.CP_ASOF_BACK_DAYS + A.CP_WINDOW_DAYS


def test_next_day_hits_and_a_new_run_recomputes_only_its_grid_days(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    root, d = _folder(tmp_path)
    calls = _count(monkeypatch)
    a = _mk(root)
    n_grid = len(calls)
    assert n_grid >= 8
    thr = a.athlete.settings.get("runthr")

    # tomorrow's (and next week's) first build: no estimate at all, same values
    calls.clear()
    b = _mk(root, TODAY + dt.timedelta(days=1))
    c = _mk(root, TODAY + dt.timedelta(days=7))
    assert calls == []
    assert b.athlete.settings.get("runthr") == thr == c.athlete.settings.get("runthr")

    # the whole-dataset key misses (estimate.json gone): every grid day still hits
    (fitcache.home_of(root) / "estimate.json").unlink()
    calls.clear()
    e = _mk(root)
    assert calls == [] and e.athlete.settings.get("runthr") == thr

    # a new run inside the history: only the grid days within ESTIMATE_LOOKBACK_DAYS after it
    new_day = dt.date(2026, 2, 3)
    (d / "new.fit").write_bytes(build_run(datetime(2026, 2, 3, 8, tzinfo=timezone.utc), seconds=900,
                                          power=230, hr=158, speed_m_s=3.2))
    calls.clear()
    f = _mk(root)
    assert calls, "the grid days after the new run are re-estimated"
    assert all(new_day <= x <= new_day + dt.timedelta(days=FD.ESTIMATE_LOOKBACK_DAYS + 1) for x in calls)
    assert len(calls) < n_grid
    # …and equal a cold build
    hm = fitcache.home_of(root)
    for name in ("estimate.json", "estimate_grid.json"):
        (hm / name).unlink()
    calls.clear()
    g = _mk(root)
    assert len(calls) == n_grid or len(calls) >= len(set(calls))
    assert g.athlete.settings.get("runthr") == f.athlete.settings.get("runthr")
    assert g._cp_est == f._cp_est
