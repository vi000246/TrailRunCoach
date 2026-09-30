"""Dataset built from a folder of FIT files (backend/engine/wko5expr/fitdataset.py)."""
import datetime as dt
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.fitdataset import FitFolderDataset, dataset_for_source, sport_of, workout_fields
from backend.tests.fit_builder import build_run


def _folder(tmp_path, runs):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    for i, kw in enumerate(runs):
        (d / f"{i}.fit").write_bytes(build_run(**kw))
    (d / "broken.fit").write_bytes(b"not a fit")
    return tmp_path / "fit" / "coros"


def _cfg():
    return EngineConfig(parity=True)


def test_workouts_and_metrics_from_fit(tmp_path, monkeypatch):
    monkeypatch.setenv("WKO5COACH_TZ", "Asia/Taipei")
    root = _folder(tmp_path, [
        dict(start=datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc), seconds=1800, power=250, speed_m_s=3.0),
        dict(start=datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc), seconds=1200, hr=150, speed_m_s=2.5,
             climb_m_per_s=0.2),
    ])
    ds = FitFolderDataset(root, config=_cfg(), today=dt.date(2026, 9, 30))
    assert len(ds.workouts) == 2                              # broken file skipped
    w0, w1 = ds.workouts
    assert w0.sport == "run" and w0.entry.start == datetime(2026, 9, 2, 6, 30)   # local wall clock
    m0 = w0.metrics
    assert m0["duration"] == pytest.approx(1800, abs=2)
    assert m0["distance"] == pytest.approx(5.4, rel=0.01)
    assert m0["np"] == pytest.approx(250, rel=0.01)
    assert w1.metrics["climbing"] == pytest.approx(240, rel=0.1)
    assert w1.metrics["np"] is None
    # channels through the normal Dataset API
    p = ds.channel(0, "power")
    assert len(p) == len(ds.channel(0, "elapsedtime")) and np.nanmax(p) == 250


def test_tss_uses_athlete_thresholds_like_wko5(tmp_path):
    root = _folder(tmp_path, [dict(start=datetime(2026, 9, 1, 8, tzinfo=timezone.utc), seconds=3600, power=250)])
    ds = FitFolderDataset(root, config=_cfg(), today=dt.date(2026, 9, 30))
    ds.athlete.settings["runftp"] = [(dt.date(2020, 1, 1), 250.0)]
    w = ds.workouts[0]
    w.entry.metrics  # computed once at load (before the setting): recompute with it
    w.metrics = ds._metrics(w)
    assert w.metrics["tss"] == pytest.approx(100, rel=0.02)   # one hour at FTP


def test_evaluator_runs_on_fit_dataset(tmp_path):
    root = _folder(tmp_path, [
        dict(start=datetime(2026, 9, 1, 8, tzinfo=timezone.utc), seconds=1200, power=240),
        dict(start=datetime(2026, 9, 2, 8, tzinfo=timezone.utc), seconds=1200, power=260),
    ])
    ds = FitFolderDataset(root, config=_cfg(), today=dt.date(2026, 9, 30))
    ev = Evaluator(ds, ds.first_day, ds.last_day)
    w = ds.workouts[1]
    assert ev.evaluate("max(power)", w) == pytest.approx(260)
    assert ev.evaluate("avg(heartrate)", w) == pytest.approx(140)
    tot = ev.evaluate("sum(distance)")
    vals = [v for v in (tot.values if hasattr(tot, "values") else [tot]) if v is not None]
    assert sum(vals) == pytest.approx(7.2, rel=0.02)


def test_workout_fields_hr_and_moving():
    t = np.arange(1, 601, dtype=float)
    ch = {"heartrate": np.full(600, 170.0), "speed": np.r_[np.full(300, 10.0), np.zeros(300)],
          "elapseddistance": np.cumsum(np.r_[np.full(300, 10 / 3600), np.zeros(300)])}
    f = workout_fields(t, ch, "run", lthr=170.0)
    assert f[4213] == pytest.approx(300)
    assert f[4235] == pytest.approx(600 / 3600 * 100, rel=0.02)   # hrTSS at LTHR


def test_sport_mapping():
    assert sport_of("running", "trail") == ("run", "trail running")
    assert sport_of("cycling", None)[0] == "bike"
    assert sport_of("hiking", None) == ("walk", "hiking")
    assert sport_of("kitesurfing", None)[0] == "other"


def test_dataset_for_source_picks_folder(tmp_path, _fit_root_in_tmp):
    (_fit_root_in_tmp / "tp" / "2026").mkdir(parents=True)
    (_fit_root_in_tmp / "tp" / "2026" / "a.fit").write_bytes(
        build_run(datetime(2026, 9, 1, 8, tzinfo=timezone.utc), seconds=300))
    ds = dataset_for_source("tp", tmp_path / "no-wko5", config=_cfg(), today=dt.date(2026, 9, 30))
    assert isinstance(ds, FitFolderDataset) and ds.source == "tp" and len(ds.workouts) == 1
