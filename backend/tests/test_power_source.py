"""Per-workout power source (backend/engine/power_source.py) and the default
that the power-based models skip watch-estimated power. Synthetic FITs only
(backend/tests/fit_builder.py), with and without the Stryd developer fields."""
import datetime as dt
from datetime import datetime, timezone

import pytest

from backend.engine import power_source as PS
from backend.engine.wko5expr.config import EngineConfig
from backend.files.fit_to_channels import fit_to_channels
from backend.tests.fit_builder import build_run

T0 = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)


# ---- classification ---------------------------------------------------------

def test_classify_channels():
    assert PS.classify({}) == PS.NONE
    assert PS.classify({"power": [0, 0, None]}) == PS.NONE
    assert PS.classify({"power": [None, 210.0]}) == PS.WATCH
    assert PS.classify({"power": [210.0], "@form_power": [0, 61.0]}) == PS.STRYD
    assert PS.classify({"power": [210.0], "@air_power": [3.0]}) == PS.STRYD
    assert PS.classify({"power": [210.0], "@leg_spring_stiffness": [9.1]}) == PS.STRYD
    assert PS.classify({"power": [210.0]}, stryd_device=True) == PS.STRYD
    # Stryd fields without power: still no power
    assert PS.classify({"@form_power": [60.0]}) == PS.NONE


def test_stryd_device_names():
    assert PS.is_stryd_device("stryd")
    assert PS.is_stryd_device(95)
    assert PS.is_stryd_device("garmin", "Stryd Footpod")
    assert not PS.is_stryd_device("coros", "COROS APEX 2 Pro")
    assert not PS.is_stryd_device(None, None)


def test_usable_and_labels():
    assert PS.usable(PS.STRYD, False) and not PS.usable(PS.WATCH, False) and PS.usable(PS.WATCH, True)
    assert not PS.usable(PS.NONE, True)
    assert PS.label(PS.WATCH, False) == "手錶推估功率（未採用）"
    assert PS.label(PS.WATCH, True) == "手錶推估功率"
    assert PS.label(PS.STRYD, False) == "Stryd"


def test_fit_files_with_and_without_stryd_fields():
    stryd = fit_to_channels(build_run(T0, seconds=120, power=220, stryd=True))
    assert stryd.channels.get("@form_power") and stryd.power_source == PS.STRYD
    watch = fit_to_channels(build_run(T0, seconds=120, power=220))
    assert "@form_power" not in watch.channels and watch.power_source == PS.WATCH
    assert fit_to_channels(build_run(T0, seconds=120)).power_source == PS.NONE
    pod = fit_to_channels(build_run(T0, seconds=120, power=220, stryd_device=True))
    assert pod.stryd_device and pod.power_source == PS.STRYD


def test_setting_defaults_to_false_without_a_db():
    assert PS.read_setting() is False


# ---- the FIT dataset --------------------------------------------------------

@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def _ds(tmp_path, runs, accept=None, parity=False):
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    for i, kw in enumerate(runs):
        (d / f"{i}.fit").write_bytes(build_run(**kw))
    return FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=parity), today=dt.date(2026, 9, 30),
                            corrections=CorrectionStore(tmp_path / "corr.json"), classifications={},
                            athlete_settings=[], estimate_thresholds=False, accept_watch_power=accept)


RUNS = [dict(start=T0, seconds=1800, power=200, speed_m_s=3.0, stryd=True),            # 0: Stryd
        dict(start=T0 + dt.timedelta(days=1), seconds=1800, power=400, speed_m_s=3.0),  # 1: watch power
        dict(start=T0 + dt.timedelta(days=2), seconds=1800, speed_m_s=3.0)]             # 2: no power


def _set_thresholds(ds):
    ds.athlete.settings["runftp"] = [(dt.date(2020, 1, 1), 200.0)]
    ds.athlete.settings["runthr"] = [(dt.date(2020, 1, 1), 150.0)]
    for w in ds.workouts:
        ds._refresh_hr_fields(w)
        w.metrics = ds._metrics(w)


def test_dataset_power_source_metadata(tmp_path, no_plan):
    ds = _ds(tmp_path, RUNS)
    assert [ds.power_source(w) for w in ds.workouts] == [PS.STRYD, PS.WATCH, PS.NONE]
    assert ds.accept_watch_power is False
    assert [ds.power_ok(w) for w in ds.workouts] == [True, False, False]
    assert ds.power_label(ds.workouts[1]) == PS.UNUSED_LABEL
    # the channel itself is untouched (charts, the activity view)
    assert ds.channel(1, "power") is not None


def test_watch_power_gives_no_power_tss_by_default(tmp_path, no_plan):
    ds = _ds(tmp_path, RUNS)
    _set_thresholds(ds)
    stryd, watch, _ = ds.workouts
    assert stryd.metrics["tss"] == pytest.approx(50, rel=0.03)          # 30 min at FTP
    assert watch.metrics["power_tss_blocked"] is True
    assert watch.metrics["np"] == pytest.approx(400, rel=0.01)          # NP still reported
    assert watch.metrics["tss"] == pytest.approx(watch.metrics["hrtss"])   # falls back to hrTSS
    assert ds._is_hr_sourced(watch) and not ds._is_hr_sourced(stryd)


def test_accept_watch_power_setting_lets_it_back(tmp_path, no_plan):
    ds = _ds(tmp_path, RUNS, accept=True)
    _set_thresholds(ds)
    watch = ds.workouts[1]
    assert ds.power_ok(watch) and not watch.metrics["power_tss_blocked"]
    assert watch.metrics["tss"] == pytest.approx(200, rel=0.03)         # 30 min at 2 × FTP
    assert ds.power_label(watch) == "手錶推估功率"


def test_parity_mode_reads_every_power_like_wko5(tmp_path):
    ds = _ds(tmp_path, RUNS, parity=True)
    assert ds.accept_watch_power is True and ds.power_ok(ds.workouts[1])


# ---- race-power models ------------------------------------------------------

def test_envelope_and_run_metrics_skip_watch_power(tmp_path, no_plan):
    from backend.engine.racepower import athlete as A
    ds = _ds(tmp_path, RUNS)
    runs = ds.workouts
    env = A.envelope(ds, runs)
    assert env["ys"] and set(env["who"]) == {0}                   # the 400 W watch run sets nothing
    assert max(env["ys"]) == pytest.approx(200, abs=1)
    assert set(A.run_metrics(ds, runs, 66.0)) == {0}
    assert A._curve(ds, runs[1]) is None and A._curve(ds, runs[1], any_power=True) is not None
    st = A.model_stats(ds, runs[1])
    assert st["p_avg"] is None and st["hr_avg"] == pytest.approx(140)   # HR kept
    unused = A.watch_unused(ds, runs)
    assert [u["idx"] for u in unused] == [1] and unused[0]["label_power"] == PS.UNUSED_LABEL
    s = A.power_summary(ds, runs)
    assert s["counts"] == {"stryd": 1, "watch": 1, "none": 1} and not s["accept_watch_power"]
    # accepted: the watch run is back in the envelope
    ds2 = _ds(tmp_path / "b", RUNS, accept=True)
    assert 1 in A.envelope(ds2, ds2.workouts)["who"]


def test_cp_as_of_prefers_stryd_and_falls_back_to_watch_power(tmp_path, no_plan, monkeypatch):
    """The LTHR estimate's CP: the usable power when the 90-day window has
    any (a junk watch file stays out), else every power (pre-Stryd era)."""
    from backend.engine.racepower import athlete as A
    seen = []

    def fake_pd(ds, day, runs, ref_cp, any_power=False):
        seen.append(([w.idx for w in runs], any_power))
        return {"mftp": 200.0}
    monkeypatch.setattr(A, "pd_model", fake_pd)
    ds = _ds(tmp_path, RUNS)
    A._cp_memo.clear()
    assert A._pd_mftp(ds, dt.date(2026, 9, 10)) == 200.0
    assert seen[-1] == ([0], False)                       # Stryd only; the 400 W watch run out
    ds2 = _ds(tmp_path / "w", RUNS[1:])                     # watch power + no power only
    A._pd_mftp(ds2, dt.date(2026, 9, 10))
    assert seen[-1] == ([0, 1], True)                     # every run, watch power read


def test_cptest_curves_and_scan_skip_watch_files(tmp_path):
    from backend.engine.racepower import cptest as T
    root = tmp_path / "fit" / "coros" / "2026"
    root.mkdir(parents=True)
    (root / "1_2026-09-01_run.fit").write_bytes(build_run(T0, seconds=900, power=210, stryd=True))
    (root / "2_2026-09-02_run.fit").write_bytes(build_run(T0 + dt.timedelta(days=1), seconds=900, power=500))
    lo, hi = dt.date(2026, 8, 1), dt.date(2026, 9, 30)
    assert len(T.curves(tmp_path, lo, hi)) == 2                               # default: every file (old behaviour)
    kept = T.curves(tmp_path, lo, hi, accept_watch=False)
    assert [c["file"] for c in kept] == ["1_2026-09-01_run.fit"]
    src = T.power_sources(tmp_path, [c["path"] for c in T.curves(tmp_path, lo, hi)])
    assert sorted(src.values()) == [PS.STRYD, PS.WATCH]
    assert (tmp_path / T.POWER_CACHE_NAME).exists()


def test_cptest_cold_folder_prefetch_matches_the_file_by_file_read(tmp_path, monkeypatch):
    """A cold folder is read in one pass (cptest._prefetch: the pool, inline
    here) that also fills the bad-file and power-source caches; the curves,
    the scan and both caches are what the file-by-file reads give."""
    import json
    from backend.engine.racepower import cptest as T
    monkeypatch.setenv("WKO5COACH_FIT_WORKERS", "0")

    def folder(home):
        root = home / "fit" / "coros" / "2026"
        root.mkdir(parents=True)
        (root / "1_2026-09-01_run.fit").write_bytes(build_run(T0, seconds=900, power=210, stryd=True))
        (root / "2_2026-09-02_run.fit").write_bytes(build_run(T0 + dt.timedelta(days=1), seconds=900, power=500))
        (root / "3_2026-09-03_run.fit").write_bytes(build_run(T0 + dt.timedelta(days=2), seconds=600, power=230))
        (root / "4_2026-09-04_run.fit").write_bytes(b"not a fit")
        return home
    lo, hi = dt.date(2026, 8, 1), dt.date(2026, 9, 30)
    out = {}
    for name, min_reads in (("inline", 10_000), ("prefetch", 1)):
        home = folder(tmp_path / name)
        monkeypatch.setattr(T, "POOL_MIN_READS", min_reads)
        c = T.curves(home, lo, hi, accept_watch=False)
        s = T.scan(home, lo, hi)
        # the stamps differ (two folders written at different times): compare what follows them
        out[name] = (c, s, {k: v[2:] for k, v in json.loads((home / T.BAD_CACHE_NAME).read_text("utf-8")).items()},
                     {k: v[2:] for k, v in json.loads((home / T.POWER_CACHE_NAME).read_text("utf-8")).items()})
    a, b = out["inline"], out["prefetch"]
    assert a[0] == b[0] and a[1] == b[1]
    assert a[2] == {k: v for k, v in b[2].items() if k in a[2]}       # the prefetch also fills the others
    assert a[3] == {k: v for k, v in b[3].items() if k in a[3]}
    assert len(b[2]) == 4 and len(b[3]) == 4


def test_settings_key_is_known_and_validated():
    from backend.settings import repository as R
    assert R.DEFAULTS[PS.SETTING_KEY] is False
    with pytest.raises(ValueError):
        R.validate(PS.SETTING_KEY, "yes")
    R.validate(PS.SETTING_KEY, True)
