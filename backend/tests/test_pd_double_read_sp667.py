"""
SP-667 (from SP-665): the PD model fitted ~2 % low (mFTP 191 vs WKO5's 196 W on the same runs).

1. **No value before the first reading, after the last one, or across a long gap**
   (files/fit_reader._build_raw_workout): np.interp carried the first power over the seconds
   before the Stryd connected, so a start spike became an 8–12 s plateau (09-18: 10 s best
   523 W vs WKO5's 243 W). Power there is 0 (as the FIT dataset / WKO5 read it), the other
   channels NaN.
2. **A FIT-folder Dataset reads each run once**: the synced-FIT curves (cptest.curves) are the
   same files as the Dataset's own, so pd_model / pd_model_params add them only for another
   Dataset (WKO5), and a Stryd-only fit adds only Stryd files.
3. **The window is 90 calendar days** (WKO5's and cptest.curves'): `tday - 90 < w.day` let
   in a run from the 91st day (w.day has a time of day).
4. **The new CP takes effect**: a CP held by the old fit code (cp_hold.json without this
   code's version) is dropped, so the corrected fit applies even within CP_HOLD_W.

Synthetic FITs only — never the WKO5 folder, the real app DB or ~/.wko5coach.
"""
import datetime as dt
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import planning as PL
from backend.engine.racepower import athlete as A
from backend.engine.racepower import cptest as T
from backend.engine.racepower import weather as WX
from backend.engine.wko5expr.dataset import date_to_day
from backend.files import fit_reader as FR
from backend.tests.fit_builder import build_run

D = dt.date
UTC = timezone.utc
NO_POWER = 65535                     # FIT's invalid UINT16: the pod not connected yet


def _spike_start(n=1800, lead=7, spike=529, rest=240):
    """09-18's shape: no power for `lead` s, a start spike for 1 s, then steady running."""
    return [NO_POWER] * lead + [spike] + [rest] * (n - lead - 1)


# ---------------------------------------------------------------------------
# 1. the 1-s resample neither extends nor bridges
# ---------------------------------------------------------------------------

def test_no_power_before_the_first_reading():
    raw = FR.parse_fit_bytes(build_run(datetime(2026, 9, 18, 0, tzinfo=UTC), power=_spike_start(), stryd=True))
    assert list(raw.power_w[:7]) == [0.0] * 7               # was 529 W × 7 (the spike carried back)
    assert raw.power_w[7] == 529 and raw.power_w[8] == 240
    assert raw.has_power and raw.heart_rate_bpm[0] == 140


def _rec(t, **kw):
    return {"timestamp": datetime(2026, 9, 18, 0, tzinfo=UTC) + dt.timedelta(seconds=t), **kw}


def test_no_value_after_the_last_reading_or_across_a_long_gap():
    recs = ([_rec(t, heart_rate=150, altitude=100.0 + t, distance=3.0 * t) for t in range(0, 3)] +
            [_rec(t, power=300, heart_rate=150, altitude=100.0 + t, distance=3.0 * t) for t in range(3, 10)] +
            # a 4-s smart-recording step: bridged; a 60-s pause: not
            [_rec(t, power=200, heart_rate=150, altitude=100.0 + t, distance=3.0 * t) for t in (14, 15, 75, 76)] +
            [_rec(t, heart_rate=150, altitude=100.0 + t, distance=3.0 * t) for t in (77, 78)])
    r = FR._build_raw_workout(recs, "x.fit", "running", None, None, [], {})
    p = r.power_w
    assert len(p) == 79 and list(p[:3]) == [0.0, 0.0, 0.0]          # before the first power
    assert list(p[77:]) == [0.0, 0.0]                                # after the last power
    assert p[11] == pytest.approx(300 - 100 * 2 / 5)                 # a short gap is bridged
    assert np.all(p[16:75] == 0.0) and p[75] == 200                  # a pause is no power
    # the other channels: NaN where there is no reading, not 0 (an altitude of 0 is a 100-m drop)
    hr = r.heart_rate_bpm
    assert hr[0] == 150 and np.all(np.isnan(hr[16:75])) and hr[75] == 150
    assert r.total_distance_m == pytest.approx(3.0 * 78)
    assert not r.has_cadence and not np.isnan(r.cadence_rpm).any()   # no reading at all: zeros, as before


def test_elevation_gain_fallback_skips_the_gaps():
    from backend.files.file_service import elevation_gain
    recs = [_rec(t, altitude=100.0 + t) for t in range(0, 5)] + [_rec(t, altitude=100.0 + t) for t in range(60, 65)]
    r = FR._build_raw_workout(recs, "x.fit", "running", None, None, [], {})
    assert np.isnan(r.altitude_m[30])
    assert elevation_gain(r) == pytest.approx(64.0)                  # the climb across the pause counts once


def test_the_synced_curve_is_the_datasets_curve(tmp_path):
    """cptest's own read (fit_reader) of a run that starts without power gives the same mean-max
    as the FIT dataset (and WKO5): the start spike is 1 s, not a 529-W plateau."""
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    p = d / "1_2026-09-18_run.fit"
    p.write_bytes(build_run(datetime(2026, 9, 18, 0, tzinfo=UTC), power=_spike_start(), stryd=True))
    c = T._read(p)["curve"]
    at = dict(zip(*c))
    assert at[1.0] == 529.0
    assert at[10.0] == pytest.approx((529 + 9 * 240) / 10, abs=0.1)        # was 523 W
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=D(2026, 9, 30),
                          estimate_thresholds=False, tz=UTC)
    xs, ys = A._curve(ds, ds.workouts[0])
    for t in (1.0, 5.0, 8.0, 10.0, 15.0, 61.0, 598.0):
        assert np.interp(t, *c) == pytest.approx(np.interp(t, xs, ys), abs=0.6), t


# ---------------------------------------------------------------------------
# 2. one read per run; a Stryd-only fit adds Stryd files only
# ---------------------------------------------------------------------------

def _fit_ds(home, days, spike_on=()):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    d = home / "fit" / "coros" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    for i, (day, hour) in enumerate(days):
        p = d / f"{i}_{day.isoformat()}_run.fit"
        power = _spike_start() if day in spike_on else [180 + (i % 5) * 4] * 1800
        p.write_bytes(build_run(datetime(day.year, day.month, day.day, hour, tzinfo=UTC), power=power, stryd=True))
    ds = FitFolderDataset(home / "fit" / "coros", config=EngineConfig(parity=True), today=D(2026, 10, 13),
                          estimate_thresholds=False, tz=UTC)
    ds.plan = PL.Plan()
    return ds


def test_a_fit_folder_dataset_reads_each_run_once(tmp_path, monkeypatch):
    monkeypatch.setattr(WX, "HOME", str(tmp_path))
    days = [(D(2026, 9, 1) + dt.timedelta(days=3 * i), 6) for i in range(8)]
    ds = _fit_ds(tmp_path, days, spike_on={days[3][0]})
    assert ds.adds_synced_fit is False
    # the same folder through cptest.curves holds every run again
    assert len(T.curves(tmp_path, D(2026, 7, 1), D(2026, 10, 8))) == 8
    assert A._synced_curves(ds, D(2026, 10, 8)) == []
    read = []
    monkeypatch.setattr(T, "curves", lambda *a, **k: read.append(a) or [])
    runs = list(ds.workouts)
    r = A.pd_model(ds, D(2026, 10, 8), runs, None)
    A.pd_model_params(ds, D(2026, 10, 8), runs, None)
    ds.stryd_cp_fit(D(2026, 10, 8))
    assert read == []
    assert r is None or r["fit_files"] == []


def test_another_dataset_adds_synced_files_filtered_like_its_runs(tmp_path, monkeypatch):
    """WKO5 as the 資料來源: the synced FIT files WKO5 has not imported still count; a Stryd-only
    fit takes the Stryd files only, and a spike before the pod connected is 1 s there too."""
    monkeypatch.setattr(WX, "HOME", str(tmp_path))
    root = tmp_path / "fit" / "coros" / "2026"
    root.mkdir(parents=True)
    (root / "1_2026-09-18_run.fit").write_bytes(build_run(datetime(2026, 9, 18, 0, tzinfo=UTC),
                                                          power=_spike_start(), stryd=True))
    (root / "2_2026-09-19_run.fit").write_bytes(build_run(datetime(2026, 9, 19, 0, tzinfo=UTC),
                                                          seconds=900, power=420))    # watch power
    wko5 = SimpleNamespace(accept_watch_power=True)
    day = D(2026, 10, 8)
    assert sorted(c["file"] for c in A._synced_curves(wko5, day)) == ["1_2026-09-18_run.fit", "2_2026-09-19_run.fit"]
    only = A._synced_curves(wko5, day, stryd_only=True)
    assert [c["file"] for c in only] == ["1_2026-09-18_run.fit"]
    at = dict(zip(only[0]["xs"], only[0]["ys"]))
    assert at[10.0] < 300                                                  # not the 529-W plateau
    assert [c["file"] for c in A._synced_curves(SimpleNamespace(accept_watch_power=False), day)] == \
        ["1_2026-09-18_run.fit"]
    # the window: 90 days up to the day
    assert [c["file"] for c in A._synced_curves(wko5, D(2026, 9, 18) + dt.timedelta(days=90))] == \
        ["2_2026-09-19_run.fit"]
    assert len(A._synced_curves(wko5, D(2026, 9, 18) + dt.timedelta(days=89))) == 2


def test_the_stryd_fits_ask_for_stryd_files_only(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(WX, "HOME", str(tmp_path))
    real = A._synced_curves

    def spy(ds, today, any_power=False, stryd_only=False):
        seen.append(stryd_only)
        return real(ds, today, any_power, stryd_only)
    monkeypatch.setattr(A, "_synced_curves", spy)
    days = [(D(2026, 9, 1) + dt.timedelta(days=3 * i), 6) for i in range(8)]
    ds = _fit_ds(tmp_path, days)
    ds._estimate_cp(D(2026, 10, 8), D(2026, 10, 8))
    ds.stryd_pd_params(D(2026, 10, 8))
    assert seen and all(seen)


# ---------------------------------------------------------------------------
# 3. 90 calendar days
# ---------------------------------------------------------------------------

def test_the_window_is_90_calendar_days():
    tday = date_to_day(D(2026, 10, 8))
    assert A.in_cp_window(tday - 89 + 0.01, tday)                  # 07-11 (the 90th day back)
    assert not A.in_cp_window(tday - 90 + 0.5, tday)               # 07-10 noon (the old test let it in)
    assert A.in_cp_window(tday + 0.99, tday) and not A.in_cp_window(tday + 1.0, tday)


def test_the_stryd_fit_and_its_params_read_90_days(tmp_path, monkeypatch):
    monkeypatch.setattr(WX, "HOME", str(tmp_path))
    end = D(2026, 10, 8)
    days = [(end - dt.timedelta(days=90), 12), (end - dt.timedelta(days=89), 6)] + \
        [(end - dt.timedelta(days=3 * i), 6) for i in range(6)]
    got = {}

    def fake_pd(ds_, day, runs, ref, any_power=False, stryd_only=False):
        got.setdefault("pd", sorted(w.entry.start.date() for w in runs))
        return {"mftp": 195.0, "frc": 5000.0, "n_points": 40}

    def fake_params(ds_, day, runs, ref=None, any_power=False, stryd_only=False):
        got["params"] = sorted(w.entry.start.date() for w in runs)
        return {"mftp": 195.0, "frc": 5000.0, "pmax": 520.0, "tte": 2000.0, "params": [1, 2, 3, 4, 5, 6]}
    monkeypatch.setattr(A, "pd_model", fake_pd)
    monkeypatch.setattr(A, "pd_model_params", fake_params)
    ds = _fit_ds(tmp_path, days)
    ds._estimate_cp(end, end)
    assert ds._cp_est[-1]["runs"] == 7
    assert ds.stryd_pd_params(end)["mftp"] == 195.0
    first = end - dt.timedelta(days=89)
    assert got["pd"][0] == first and got["params"] == got["pd"] and len(got["pd"]) == 7


# ---------------------------------------------------------------------------
# 4. the corrected CP takes effect
# ---------------------------------------------------------------------------

def test_a_cp_held_by_the_old_fit_code_gives_way(tmp_path, monkeypatch):
    """cp_hold.json from before SP-667 (no "v"): 193.0 W, 2.3 W from the corrected 195.3 W fit —
    within CP_HOLD_W, so it would have stayed. It is dropped: the corrected fit applies and is
    remembered with this code's version, and from then on the ≥ 3 W rule holds as before."""
    from backend.engine.wko5expr import fitdataset as FD
    monkeypatch.setattr(WX, "HOME", str(tmp_path))
    end = D(2026, 10, 8)
    monkeypatch.setattr(A, "pd_model", lambda ds_, day, runs, ref, any_power=False, stryd_only=False:
                        {"mftp": 195.3 if day == end else 175.4, "frc": 5000.0, "n_points": 40})
    days = [(end - dt.timedelta(days=3 * i), 6) for i in range(8)]
    ds = _fit_ds(tmp_path, days)
    hold = ds._store.home / FD.CP_HOLD_FILE
    hold.parent.mkdir(parents=True, exist_ok=True)
    hold.write_text(json.dumps({"date": end.isoformat(), "cp": 193.0, "frc_j": 5650.0, "runs": 49, "n_points": 40}))
    ds._estimate_cp(end, end)
    ds._hold_cp_est()
    assert ds.cp_info(ds.workouts[-1])["cp"] == 195.3
    saved = json.loads(hold.read_text())
    assert saved["cp"] == 195.3 and saved["v"] == FD.CP_HOLD_V
    # remembered by this code: a fit within 3 W keeps it
    monkeypatch.setattr(A, "pd_model", lambda ds_, day, runs, ref, any_power=False, stryd_only=False:
                        {"mftp": 196.5, "frc": 5000.0, "n_points": 40})
    ds._estimate_cp(end, end)
    ds._hold_cp_est()
    assert ds.cp_info(ds.workouts[-1])["cp"] == 195.3


def test_the_pd_memo_and_the_synced_curve_cache_are_refitted():
    """The PD memo's code version and cptest's curve cache version moved with this fix, so every
    stored refit and every synced curve is recomputed (not read back from before the fix)."""
    from backend.engine.wko5expr import fitdataset as FD
    assert FD.ESTIMATE_CODE_V >= 2
    assert T._KEY_VERSION >= 3


def test_derive_race_power_reads_the_same_90_calendar_days(tmp_path, monkeypatch):
    """derive()'s runs_90 (the race-power PD model, envelope, training conditions) is in_cp_window too:
    the run at noon of the 91st day back is out (`w.day > tday - 90` let it in)."""
    monkeypatch.setattr(WX, "HOME", str(tmp_path))
    end = D(2026, 10, 8)
    days = [(end - dt.timedelta(days=90), 12), (end - dt.timedelta(days=89), 6)] + \
        [(end - dt.timedelta(days=3 * i), 6) for i in range(6)]
    got = {}
    real = A.pd_model

    def spy(ds_, day, runs, ref, *a, **kw):
        got["pd"] = sorted(w.entry.start.date() for w in runs)
        return real(ds_, day, runs, ref, *a, **kw)
    monkeypatch.setattr(A, "pd_model", spy)
    ds = _fit_ds(tmp_path, days)
    out = A.derive(ds, end, fetch_weather=False)
    assert got["pd"][0] == end - dt.timedelta(days=89) and len(got["pd"]) == 7, got
    assert out["counts"]["runs_90"] == 7
