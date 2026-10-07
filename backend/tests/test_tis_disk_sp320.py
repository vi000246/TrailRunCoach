"""SP-320 ②: the per-workout TIS values (the tisaerobic / tisanaerobic built-ins) are kept on
disk, so a new Dataset object (a sync, a restart) reads them instead of recomputing; a new
run recomputes only the workouts whose 90-day FTP / FRC window holds it, and the values
equal a cold computation's. Synthetic FIT files only."""
import datetime as dt
import math
from datetime import datetime, timezone

from backend.engine.wko5expr import evaluator as EV
from backend.engine.wko5expr import fitcache
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)


def _profile(k: float) -> list:
    """A run with efforts from 15 s to 10 min: a curve the PD model can fit."""
    seg = [(300, 200), (15, 560), (120, 200), (60, 410), (120, 200), (180, 340), (120, 200), (600, 290),
           (300, 210)]
    return [int(w * k) for d, w in seg for _ in range(d)]


def _folder(tmp_path):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    start = datetime(2026, 3, 1, 8, tzinfo=timezone.utc)
    for i in range(12):                       # every 15 days: windows overlap partly
        (d / f"{i}.fit").write_bytes(build_run(start + dt.timedelta(days=15 * i), power=_profile(1 + 0.02 * (i % 4)),
                                               hr=150, speed_m_s=3.0, stryd=True))
    return tmp_path / "fit" / "coros", d


def _mk(root):
    return FitFolderDataset(root, config=EngineConfig(parity=False), today=TODAY, classifications={},
                            athlete_settings=[], estimate_thresholds=False)


def _tis(ds):
    ev = Evaluator(ds, ds.first_day, ds.today)
    out = {}
    for name in ("tisaerobic", "tisanaerobic"):
        r = ev.evaluate(name)
        out[name] = {ds.workouts[i].entry.file: v for i, v in r.items()}
    ds.flush_series()
    return out


def _count(monkeypatch):
    calls = []
    real = Evaluator._has_channel            # runs only when a TIS is computed

    def counting(self, w, name):
        calls.append(w.entry.file)
        return real(self, w, name)
    monkeypatch.setattr(Evaluator, "_has_channel", counting)
    return calls


def test_lookback_matches_the_expressions():
    for name in EV.PER_WORKOUT_BUILTINS:
        assert "@lookback:=90" in EV.BUILTIN_EXPRS[name]
    assert EV.BUILTIN_DISK_LOOKBACK_DAYS >= 90


def _fake_pdfit(pts):
    """A stand-in PD fit (the real one rejects these synthetic curves): FTP / FRC follow the
    window's best 10-min and 1-min powers, so a TIS still depends on its window."""
    p = dict(pts)
    if not p:
        return None
    ftp = max(y for x, y in pts if x >= 300) * 0.95
    return {"FTP": ftp, "FRC": 60 * (max(p.values()) - ftp) + 9000, "valid": True}


def test_new_dataset_reads_tis_from_disk(tmp_path, monkeypatch):
    from backend.engine.racepower import weather as WX
    monkeypatch.setattr(WX, "HOME", tmp_path / "home")
    monkeypatch.setattr(EV, "pdfit", _fake_pdfit)
    root, d = _folder(tmp_path)
    calls = _count(monkeypatch)
    a = _tis(_mk(root))
    assert len(calls) == 24 and any(not math.isnan(v) for v in a["tisaerobic"].values())

    calls.clear()
    b = _tis(_mk(root))                        # a new Dataset object: no recomputation
    assert calls == [] and b == a

    # a new run on 2026-06-10: only the workouts of the next 91 days recompute
    (d / "new.fit").write_bytes(build_run(datetime(2026, 6, 10, 8, tzinfo=timezone.utc), power=_profile(1.1),
                                          hr=165, speed_m_s=3.4, stryd=True))
    calls.clear()
    c = _tis(_mk(root))
    ds_c = _mk(root)
    days = {w.entry.file: w.entry.start.date() for w in ds_c.workouts}
    assert calls and len(calls) < 2 * len(days)
    assert all(dt.date(2026, 6, 9) <= days[f] <= dt.date(2026, 9, 10) for f in calls)
    # equal to a cold computation
    for p in fitcache.home_of(root).glob("series_builtin*"):
        p.unlink()
    assert _tis(_mk(root)) == c
