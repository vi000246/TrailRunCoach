"""Persistent per-file cache of FIT folder datasets (engine/wko5expr/fitcache.py),
the single-flight Dataset factory and the build progress endpoint."""
import asyncio
import datetime as dt
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from backend.engine.wko5expr import buildstate, fitcache
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.files import fit_to_channels as FTC
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)


def _folder(tmp_path, n=3):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"{i}.fit").write_bytes(build_run(datetime(2026, 9, 1 + i, 8, tzinfo=timezone.utc), seconds=900,
                                               power=200 + 10 * i, hr=140 + i, speed_m_s=3.0, climb_m_per_s=0.1))
    (d / "broken.fit").write_bytes(b"not a fit")
    return tmp_path / "fit" / "coros"


def _count_parses(monkeypatch):
    calls = []
    real = FTC.fit_to_channels

    def counting(raw):
        calls.append(1)
        return real(raw)
    monkeypatch.setattr(FTC, "fit_to_channels", counting)
    return calls


def _build(root, **kw):
    return FitFolderDataset(root, config=EngineConfig(parity=False, hr_tss_moving_only=True), today=TODAY,
                            classifications={}, athlete_settings=[{"effective_date": "2026-01-01", "weight_kg": 60}],
                            estimate_thresholds=False, **kw)


def _snapshot(ds):
    return ([(w.entry.file, w.entry.start, w.sport, w.sport_type, sorted(w.metrics.items(), key=str))
             for w in ds.workouts], dict(ds._power_src), [x["file"] for x in ds.excluded])


def test_warm_rebuild_reads_no_fit_and_matches_the_cold_build(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    calls = _count_parses(monkeypatch)
    cold = _build(root)
    assert len(calls) == 4 and len(cold.workouts) == 3          # broken file parsed once, skipped
    calls.clear()
    warm = _build(root)
    assert calls == []                                            # nothing re-parsed
    assert _snapshot(warm) == _snapshot(cold)
    # channels come back lazily, identical to the parsed ones
    for i in range(3):
        a, b = cold.wko4(i).channels, warm.wko4(i).channels
        assert list(a) == list(b)
        for k in a:
            assert a[k].values == b[k].values
    assert calls == []


def test_a_changed_file_is_the_only_one_parsed_again(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    _build(root)
    calls = _count_parses(monkeypatch)
    f = root / "2026" / "1.fit"
    f.write_bytes(build_run(datetime(2026, 9, 2, 8, tzinfo=timezone.utc), seconds=1200, power=300))
    ds = _build(root)
    assert len(calls) == 1
    assert ds.workouts[1].metrics["duration"] == pytest.approx(1200, abs=2)


def test_a_file_without_samples_is_not_parsed_again(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    monkeypatch.setattr(fitcache, "parse_file", _no_samples(fitcache.parse_file))
    _build(root)
    calls = _count_parses(monkeypatch)
    _build(root)
    assert calls == []


def _no_samples(real):
    """parse_file, but 0.fit comes back as a session without records (start, no samples)."""
    def f(path, npz):
        out = real(path, npz)
        if path.endswith("0.fit"):
            Path(npz).unlink(missing_ok=True)
            out = {"meta": {**out["meta"], "n": 0, "duration": None, "channels": []}, "parse": out["parse"]}
        return out
    return f


def test_a_deleted_file_leaves_the_cache(tmp_path):
    root = _folder(tmp_path)
    a = _build(root)
    npz = a._store.npz("2026/2.fit")
    assert npz.exists()
    (root / "2026" / "2.fit").unlink()
    b = _build(root)
    assert len(b.workouts) == 2 and "2026/2.fit" not in b._store.files and not npz.exists()


def test_a_field_version_change_recomputes_only_that_field(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    _build(root)
    calls = _count_parses(monkeypatch)
    import backend.engine.power_source as PS
    seen = []
    real = PS.classify
    monkeypatch.setattr(PS, "classify", lambda ch, dev=False: seen.append(1) or real(ch, dev))
    monkeypatch.setitem(fitcache._VERSIONS, "power", "changed")
    ds = _build(root)
    assert calls == [] and len(seen) == 3                         # power re-derived from the cached channels
    assert set(ds._power_src.values()) == {"watch"}


def test_hr_fields_follow_the_lthr_without_reparsing(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    a = _build(root)
    assert a.workouts[0].metrics["hrtss"] is None                # no LTHR known
    calls = _count_parses(monkeypatch)
    b = FitFolderDataset(root, config=EngineConfig(parity=False), today=TODAY, classifications={},
                         athlete_settings=[], estimate_thresholds=False)
    b.athlete.settings["runthr"] = [(dt.date(2020, 1, 1), 160.0)]
    for w in b.workouts:
        b._refresh_hr_fields(w)
        w.metrics = b._metrics(w)
    assert calls == [] and b.workouts[0].metrics["hrtss"] > 0
    # the same as computing it from the channels
    from backend.engine.algorithms.wko5_hr import hr_tss
    f = b.wko4(0).channels
    assert b.workouts[0].metrics["hrtss"] == pytest.approx(hr_tss(f["elapsedtime"].values, f["heartrate"].values, 160.0)[0])


def test_process_pool_parses_a_cold_folder(tmp_path, monkeypatch):
    root = _folder(tmp_path, n=4)
    monkeypatch.setenv(fitcache.ENV_WORKERS, "2")
    monkeypatch.setattr(fitcache, "POOL_MIN_FILES", 1)
    st = buildstate.get("pooltest")
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY, source="pooltest")
    assert len(ds.workouts) == 4
    snap = st.snapshot()
    assert snap["phase"] == "finish"
    monkeypatch.setenv(fitcache.ENV_WORKERS, "0")
    inline = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY)
    assert _snapshot(inline)[0] == _snapshot(ds)[0]


def test_lazy_files_keep_a_bounded_number_open(tmp_path, monkeypatch):
    root = _folder(tmp_path, n=3)
    monkeypatch.setenv("WKO5COACH_FIT_OPEN", "2")
    ds = _build(root)
    for i in range(3):
        assert ds.channel(i, "power") is not None
    assert len(ds._files._open) == 2 and len(ds._files) == 3


def test_series_cache_survives_a_rebuild(tmp_path):
    root = _folder(tmp_path)
    a = _build(root)
    n = []
    v = a.cached_series("t_key", a.workouts[0], lambda: n.append(1) or {"x": 1.5})
    a.flush_series()
    b = _build(root)
    assert b.cached_series("t_key", b.workouts[0], lambda: n.append(1) or {"x": 9}) == {"x": 1.5} == v
    assert n == [1]
    # another threshold in effect: computed again, both variants kept
    b.athlete.settings["runthr"] = [(dt.date(2020, 1, 1), 150.0)]
    assert b.cached_series("t_key", b.workouts[0], lambda: {"x": 2}) == {"x": 2}


def test_estimate_memo_restores_the_estimated_settings(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    runs = []

    def fake(self):
        runs.append(1)
        self.athlete.settings["runthr"] = [(dt.date.min, None), (dt.date(2026, 9, 2), 171.0)]
        self._setting_labels["runthr"] = "est"
        return True
    monkeypatch.setattr(FitFolderDataset, "_estimate_settings", fake)
    a = FitFolderDataset(root, config=EngineConfig(parity=False), today=TODAY, classifications={},
                         athlete_settings=[], estimate_thresholds=True)
    b = FitFolderDataset(root, config=EngineConfig(parity=False), today=TODAY, classifications={},
                         athlete_settings=[], estimate_thresholds=True)
    assert runs == [1]
    assert b.athlete.settings["runthr"] == a.athlete.settings["runthr"]
    assert b.setting_label("runthr") == "est"
    assert [w.metrics["hrtss"] for w in b.workouts] == [w.metrics["hrtss"] for w in a.workouts]
    # new data -> estimated again
    (root / "2026" / "9.fit").write_bytes(build_run(datetime(2026, 9, 20, 8, tzinfo=timezone.utc), seconds=600))
    FitFolderDataset(root, config=EngineConfig(parity=False), today=TODAY, classifications={},
                     athlete_settings=[], estimate_thresholds=True)
    assert runs == [1, 1]


def test_pd_refits_are_memoised_per_day_window(tmp_path, monkeypatch):
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import weather as WX
    monkeypatch.setattr(WX, "HOME", tmp_path / "home")          # no real synced FIT folder
    root = _folder(tmp_path)
    fits = []
    monkeypatch.setattr(A, "pd_model", lambda ds, day, runs, ref, any_power=False:
                        fits.append(day) or {"mftp": 200.0 + len(runs)})
    a = _build(root)
    d1, d2 = dt.date(2026, 9, 5), dt.date(2026, 8, 1)
    assert A._pd_mftp(a, d1) == 203.0 and A._pd_mftp(a, d2) == 200.0     # 3 runs / none in the window
    a.flush_series()
    n = len(fits)
    b = _build(root)
    assert A._pd_mftp(b, d1) == 203.0 and len(fits) == n        # from disk, no refit
    # a new run inside d1's window: refit d1 only
    (root / "2026" / "8.fit").write_bytes(build_run(datetime(2026, 9, 4, 8, tzinfo=timezone.utc), seconds=600,
                                                    power=210))
    c = _build(root)
    assert A._pd_mftp(c, d1) == 204.0 and len(fits) == n + 1
    assert A._pd_mftp(c, dt.date(2026, 9, 2)) is not None


# ---- the factory: single flight + progress --------------------------------

def test_concurrent_requests_build_one_dataset(monkeypatch, tmp_path):
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import datasource as DSRC
    WV._dataset_cfg.cache_clear()
    built = []
    gate = threading.Event()

    def slow(source, wko5_dir, config=None, today=None):
        built.append(source)
        st = buildstate.get(source)
        st.phase("parse", total=10)
        st.tick(3)
        gate.wait(5)
        return object.__new__(FitFolderDataset)
    monkeypatch.setattr(WV, "dataset_for_source", slow)
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "coros")
    monkeypatch.setattr(DSRC, "source_stamp", lambda s, d: "stamp-1")
    out = []
    ts = [threading.Thread(target=lambda: out.append(WV._dataset())) for _ in range(5)]
    for t in ts:
        t.start()
    time.sleep(0.3)
    snap = asyncio.run(WV.dataset_status())
    assert snap["state"] == "building" and snap["message"] == "正在處理第 4／10 筆（解析 FIT）…"
    gate.set()
    for t in ts:
        t.join(5)
    assert built == ["coros"] and len(out) == 5 and all(x is out[0] for x in out)
    assert asyncio.run(WV.dataset_status())["state"] == "ready"
    WV._dataset_cfg.cache_clear()


def test_a_failed_build_reports_the_error(monkeypatch):
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import datasource as DSRC
    WV._dataset_cfg.cache_clear()

    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(WV, "dataset_for_source", boom)
    monkeypatch.setattr(DSRC, "current_source", lambda user_id=1: "tp")
    monkeypatch.setattr(DSRC, "source_stamp", lambda s, d: "x")
    with pytest.raises(RuntimeError):
        WV._dataset()
    s = asyncio.run(WV.dataset_status())
    assert s["source"] == "tp" and s["state"] == "error" and "disk gone" in s["error"]
    WV._dataset_cfg.cache_clear()


def test_to_list_turns_nan_into_none():
    assert fitcache.to_list(np.array([1.0, np.nan, 2.5])) == [1.0, None, 2.5]
    assert fitcache.to_list(np.array([1.0, 2.0])) == [1.0, 2.0]
