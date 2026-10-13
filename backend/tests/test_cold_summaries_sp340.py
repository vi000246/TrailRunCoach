"""SP-340 (docs/research/cache-tiering.md §7.1 冷, §7.2 items 6–7, §10 ⑨): the activities older
than a year keep a summary per activity and per month (wko5expr/coldstore.py); their per-second
channels go after a year and come back from the FIT when a page needs them; SP-317's monthly
archive freezes the summaries first (prepare_archive), and after it deletes the FIT files the PMC,
the best efforts, TIS and the achievements are the same and nothing hot or warm is recomputed.

Synthetic FIT files only (four years of runs, trail runs, rides), today fixed."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from backend import jobs
from backend.engine.wko5expr import buildstate, chartscope, coldstore, fitcache, seriesdb
from backend.engine.wko5expr import fitdataset as FD
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.files import fitstore
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)
T = int(date_to_day(TODAY))
CUTOFF = dt.date(2023, 9, 30)                 # SP-317: activities more than 3 years old
FIRST = datetime(2022, 6, 3, 7, tzinfo=timezone.utc)
STEP_DAYS = 13
N = 122                                       # 2022-06-03 … 2026-09-24
SETTINGS = [{"effective_date": "2020-01-01", "weight_kg": 60, "run_ftp_w": 250,
             "threshold_pace_s_per_km": 300}]


def _power(i: int, n: int = 900) -> list:
    """A power profile (a sprint, 1-minute, 5-minute and 20-minute efforts) that moves over the
    years: the oldest activities hold the best sprints, the newest the best long efforts — so the
    all-time best curve needs the archived activities."""
    ftp = 210 + i * 0.4
    sprint = 560 - 2 * i
    out = []
    for k in range(n):
        if k < 10:
            p = sprint
        elif 60 <= k < 120:
            p = ftp * 1.6
        elif 300 <= k < 600:
            p = ftp * 1.2
        elif 900 <= k < 2100:
            p = ftp * 1.03
        else:
            p = ftp * 0.75 + k % 7
        out.append(int(p))
    return out


def _fit(i: int) -> bytes:
    start = FIRST + timedelta(days=STEP_DAYS * i)
    n = 2700 if i % 4 == 0 else 900                       # every 4th a 45-minute one (the PD fit needs 40)
    if i % 7 == 3:                                         # a ride (power, no TSS setting)
        return build_run(start, power=_power(i, n), hr=130 + i % 9, speed_m_s=7.0, sport=2)
    if i % 11 == 5:                                        # a run without power
        return build_run(start, seconds=600, hr=145 + i % 9, speed_m_s=3.2)
    return build_run(start, power=_power(i, n), hr=140 + i % 12, speed_m_s=3.0, stryd=True,
                     sub_sport=3 if i % 3 == 0 else None)


def _start(i: int) -> datetime:
    return FIRST + timedelta(days=STEP_DAYS * i)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(coldstore, "ROOT", tmp_path / "cold")
    root = tmp_path / "fit" / "coros"
    for i in range(N):
        p = root / str(_start(i).year) / f"{i:03d}.fit"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_fit(i))
    return root


def _build(root: Path, today: dt.date = TODAY, **kw) -> FitFolderDataset:
    return FitFolderDataset(root, config=EngineConfig(parity=False), today=today, classifications={},
                            athlete_settings=SETTINGS, estimate_thresholds=False, source="coros", **kw)


def _archive(root: Path, files: list) -> None:
    """SP-317's part, simulated: the FIT files of the archived activities are deleted."""
    for f in files:
        Path(f).unlink()


def _cold_before(ds, cutoff=CUTOFF):
    c = date_to_day(cutoff)
    return [w for w in ds.workouts if w.day < c]


def _env(ds, lo, hi, expr="meanmax(runpower)"):
    c = Evaluator(ds, lo, hi).evaluate(expr)
    return {float(x): y for x, y in zip(c.xs, c.ys) if y is not None}


def _tl(ds, expr):
    d = Evaluator(ds, T - 365 * 5, T).evaluate(expr)
    return {d.start + i: v for i, v in enumerate(d.values)}


def _same_series(a: dict, b: dict, tol=1e-9):
    keys = set(a) | set(b)
    for k in keys:
        x, y = a.get(k, 0.0), b.get(k, 0.0)
        if (x is None or (isinstance(x, float) and math.isnan(x))) and \
                (y is None or (isinstance(y, float) and math.isnan(y))):
            continue
        assert x == pytest.approx(y, abs=tol, rel=tol), k


# ---------------------------------------------------------------------------- tiers / layout

def test_tiers_are_hot_90_days_warm_a_year_cold_older():
    """One definition (backend/jobs.py, SP-394): hot ≤ 90 days, warm ≤ 365, cold older."""
    assert (jobs.HOT_DAYS, jobs.WARM_DAYS, coldstore.NPZ_KEEP_DAYS) == (90, 365, 365)
    assert not hasattr(coldstore, "HOT_DAYS") and not hasattr(coldstore, "WARM_DAYS")
    assert coldstore.tier(T, T) == "hot" and coldstore.tier(T - 90, T) == "hot"
    assert coldstore.tier(T - 91, T) == "warm" and coldstore.tier(T - 365, T) == "warm"
    assert coldstore.tier(T - 366, T) == "cold" and coldstore.is_cold(T - 366, T)


# ---------------------------------------------------------------------------- prepare_archive

def test_prepare_archive_freezes_month_summaries_before_anything_is_deleted(env):
    ds = _build(env)
    old = _cold_before(ds)
    assert len(old) > 20
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    # nothing deleted by it: SP-317 deletes; every file it names is an activity before the cutoff
    assert all(Path(f).exists() for f in out["files"])
    assert sorted(Path(f).name for f in out["files"]) == sorted(Path(w.entry.file).name for w in old)
    assert out["frozen"] == len(old)
    store = coldstore.ColdStore(env)
    months = sorted({w.entry.start.strftime("%Y-%m") for w in old})
    assert store.months() == months and out["months"] == months
    seen = 0
    for m in months:
        doc = store.load(m)
        acts = doc["activities"]
        mine = [w for w in old if w.entry.start.strftime("%Y-%m") == m]
        assert len(acts) == len(mine)
        for w in mine:
            r = next(r for r in acts.values() if r["file"] == w.entry.file)
            assert r["frozen"] and r["metrics"]["tss"] == w.metrics["tss"]
            assert r["metrics"]["duration"] == w.metrics["duration"]
            assert set(r["tis"]) == {"tisaerobic", "tisanaerobic"}
            if w.sport == "run" and w.metrics.get("np"):
                assert r["curves"]["power"][0][:3] == [1, 2, 3]       # WKO5's durations
            seen += 1
        seg = doc["segment"]
        assert seg["frozen"] and seg["n"] == len(mine)
        # each day's TSS (tl()'s rule) = the sum of its activities' TSS
        want: dict = {}
        for w in mine:
            if w.metrics.get("tss") is not None:
                d = str(int(math.floor(w.day)))
                want[d] = want.get(d, 0.0) + w.metrics["tss"]
        assert seg["daily_tss"] == pytest.approx(want)
        # the month's envelope = the best of each duration over its runs
        runs = [w for w in mine if w.sport == "run" and w.metrics.get("np")]
        if runs:
            best: dict = {}
            for w in runs:
                c = Evaluator(ds, ds.first_day, T).evaluate("meanmax(runpower)", workout=w)
                for x, y in zip(c.xs, c.ys):
                    if y is not None and (x not in best or y > best[x]):
                        best[x] = y
            env_ = seg["envelopes"]["runpower"]
            assert sorted(env_["members"]) == sorted(
                fid for fid, r in acts.items() if r["sport"] == "run" and r["curves"].get("power"))
            got = dict(zip(env_["curve"][0], env_["curve"][1]))
            assert got == pytest.approx({x: best[x] for x in best})
    assert seen == len(old)


def test_prepare_archive_is_idempotent(env):
    ds = _build(env)
    a = coldstore.prepare_archive(CUTOFF, ds=ds)
    store = coldstore.ColdStore(env)
    stamps = {m: store.path(m).stat().st_mtime_ns for m in store.months()}
    b = coldstore.prepare_archive(CUTOFF, ds=_build(env))
    assert (b["files"], b["frozen"], b["months"]) == (a["files"], a["frozen"], a["months"])
    assert b["written"] == 0
    assert {m: store.path(m).stat().st_mtime_ns for m in store.months()} == stamps


# ---------------------------------------------------------------------------- after the archive

def test_pmc_and_best_efforts_are_the_same_after_the_archive(env):
    ds = _build(env)
    before = {
        "ctl": _tl(ds, "ctl"), "atl": _tl(ds, "atl"),
        "tl": _tl(ds, "tl(tss, ctlconstant)"), "moving": _tl(ds, "tl(movingduration, 28)*7"),
        "tis": _tl(ds, "tl((tisaerobic), ctlconstant)"),
        "all": _env(ds, ds.first_day, T),                                   # the whole history
        "edge": _env(ds, date_to_day(CUTOFF) - 50, date_to_day(CUTOFF) + 40),   # partial months
        "power": _env(ds, ds.first_day, T, "meanmax(power)"),
        "pd": Evaluator(ds, date_to_day(CUTOFF) - 30, date_to_day(CUTOFF) + 30)
        .evaluate("ftp(meanmax(runpower), 90)").values.tolist(),
    }
    from backend.engine import overview as O
    pmc_before = O.pmc(ds, FIRST.date(), TODAY)
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    _archive(env, out["files"])
    after_ds = _build(env)
    assert len(after_ds.workouts) == len(ds.workouts) - out["frozen"]
    assert len(after_ds.cold.archived) == out["frozen"]
    assert after_ds.first_day == ds.first_day
    after = {
        "ctl": _tl(after_ds, "ctl"), "atl": _tl(after_ds, "atl"),
        "tl": _tl(after_ds, "tl(tss, ctlconstant)"), "moving": _tl(after_ds, "tl(movingduration, 28)*7"),
        "tis": _tl(after_ds, "tl((tisaerobic), ctlconstant)"),
        "all": _env(after_ds, after_ds.first_day, T),
        "edge": _env(after_ds, date_to_day(CUTOFF) - 50, date_to_day(CUTOFF) + 40),
        "power": _env(after_ds, after_ds.first_day, T, "meanmax(power)"),
        "pd": Evaluator(after_ds, date_to_day(CUTOFF) - 30, date_to_day(CUTOFF) + 30)
        .evaluate("ftp(meanmax(runpower), 90)").values.tolist(),
    }
    # the data is worth comparing: TIS values, a PMC, and all-time bests held by archived activities
    assert any(v and not math.isnan(v) for v in before["tis"].values())
    assert max(before["ctl"].values()) > 1
    assert before["all"][1] >= 500 > max(_power(i)[0] for i in range(N) if _start(i).date() >= CUTOFF)
    for k in ("ctl", "atl", "tl", "moving", "tis"):
        _same_series(before[k], after[k])
    for k in ("all", "edge", "power"):
        assert before[k] and after[k] == pytest.approx(before[k]), k
    assert np.allclose(np.array(before["pd"], float), np.array(after["pd"], float), equal_nan=True)
    pmc_after = O.pmc(after_ds, FIRST.date(), TODAY)
    assert pmc_after["start"] == pmc_before["start"]
    assert [(r["date"], round(r["tss"], 9), round(r["ctl"] or 0, 9), round(r["atl"] or 0, 9))
            for r in pmc_after["series"]] == \
        [(r["date"], round(r["tss"], 9), round(r["ctl"] or 0, 9), round(r["atl"] or 0, 9))
         for r in pmc_before["series"]]


def test_whole_months_come_from_the_month_envelope(env):
    """最佳成績查窗 = 完整月份取最大 + 頭尾不完整月份的逐筆曲線: with the archived activities' own
    curves taken away, a range of whole months is still right (their segments), a range cutting
    months is not (its edge months read the activity curves)."""
    ds = _build(env)
    lo, hi = date_to_day(dt.date(2022, 7, 1)), date_to_day(dt.date(2023, 6, 30))       # whole months
    elo, ehi = date_to_day(dt.date(2022, 7, 10)), date_to_day(dt.date(2023, 6, 20))    # cutting two
    whole, edge = _env(ds, lo, hi), _env(ds, elo, ehi)
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    _archive(env, out["files"])
    after = _build(env)
    assert _env(after, lo, hi) == pytest.approx(whole) and _env(after, elo, ehi) == pytest.approx(edge)
    after = _build(env)
    for a in after.cold.archived:
        a.cold["curves"] = {}
    assert _env(after, lo, hi) == pytest.approx(whole)
    assert _env(after, elo, ehi) != pytest.approx(edge)


def test_hot_and_warm_keys_do_not_change_after_the_archive(env):
    """The 90-day and 365-day charts (with the PMC warm-up) keep their render-cache data key, and
    the as-of estimate / PD-refit / TIS keys keep theirs: archiving recomputes nothing hot or warm."""
    ds = _build(env)
    charts = [{"kind": "athlete", "id": "pmc", "series": [{"expression": "ctl"}, {"expression": "atl"}]},
              {"kind": "athlete", "id": "mmp", "series": [{"expression": "meanmax(runpower)"}]},
              {"kind": "athlete", "id": "tis", "series": [{"expression": "tl((tisaerobic), ctlconstant)"}]},
              {"kind": "athlete", "id": "tss", "series": [{"expression": "tss"}]}]

    def keys(d):
        out = {}
        for ch in charts:
            for days in (90, 365):
                for end in (T - 1, T):
                    s = chartscope.scope_of(ch, d, end - days + 1, end)
                    out[(ch["id"], days, end)] = chartscope.fingerprint(d, s)
        return out

    def est(d):
        recent = [TODAY - dt.timedelta(days=k) for k in (0, 45, 200, 364, 500)]
        d.pd_memo._prep = None
        return {"grid": d._estimate_grid(), "key": d._estimate_key(),
                "grid_sig": [d._grid_sig(x) for x in recent], "pd": [d.pd_memo.sig(x) for x in recent]}

    def tis(d):
        ev = Evaluator(d, d.first_day, T)
        return [ev._builtin_window_sig(w) for w in d.workouts if w.day > T - 600]

    k0, e0, t0 = keys(ds), est(ds), tis(ds)
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    _archive(env, out["files"])
    after = _build(env)
    assert keys(after) == k0
    assert est(after) == e0
    assert tis(after) == t0


def test_a_bad_file_before_the_cutoff_goes_too_and_old_estimate_days_keep_their_keys(env):
    """A file the Dataset leaves out (a run recorded in a car) is named for deletion with the rest;
    afterwards even the grid days whose windows held the archived runs keep their estimate / PD
    keys — their memo entries hit, nothing is estimated again."""
    (env / "2022" / "car.fit").write_bytes(build_run(datetime(2022, 9, 10, 7, tzinfo=timezone.utc),
                                                     seconds=900, hr=120, speed_m_s=30.0))
    ds = _build(env)
    assert [x["file"] for x in ds.excluded] == ["2022/car.fit"]
    days = [CUTOFF + dt.timedelta(days=k) for k in (-200, -60, 0, 30, 150, 300)]

    def est(d):
        d.pd_memo._prep = None
        return d._estimate_key(), [d._grid_sig(x) for x in days], [d.pd_memo.sig(x) for x in days]
    e0 = est(ds)
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    assert [Path(f).name for f in out["left_out"]] == ["car.fit"]
    _archive(env, out["files"] + out["left_out"])
    after = _build(env)
    assert not after.excluded and after.cold.excluded_files() == ["2022/car.fit"]
    assert est(after) == e0


def _by_file(x, ds):
    """A status as JSON with each activity index (its place in ds.workouts, which archiving the
    oldest ones moves) replaced by the activity's file."""
    if isinstance(x, dict):
        return {k: (ds.workouts[v].entry.file if k in ("idx", "index") and isinstance(v, int) else _by_file(v, ds))
                for k, v in x.items()}
    if isinstance(x, list):
        return [_by_file(v, ds) for v in x]
    return x


def test_status_values_are_the_same_after_the_archive(env):
    """The overview status (CTL, ramp, data level and its weeks, the gates …) reads the same; only
    the activity indexes it links by move, to the same activities."""
    from backend.engine.status import Status
    ds = _build(env)
    a = _by_file(json.loads(json.dumps(Status(ds, today=TODAY).compute().to_dict(), default=str)), ds)
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    _archive(env, out["files"])
    after = _build(env)
    b = _by_file(json.loads(json.dumps(Status(after, today=TODAY).compute().to_dict(), default=str)), after)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_achievements_of_archived_activities_stay(env, tmp_path, monkeypatch):
    from backend.engine import achievements as AC
    monkeypatch.setattr(AC, "CACHE_PATH", tmp_path / "achievements_cache.json")
    ds = _build(env)
    before = [(a.id, a.kind, a.distance_km, a.moving_s) for a in AC.build_achievements(ds, peaks=[])]
    assert before
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    _archive(env, out["files"])
    after = [(a.id, a.kind, a.distance_km, a.moving_s) for a in AC.build_achievements(_build(env), peaks=[])]
    assert after == before


def test_an_object_the_user_deleted_takes_its_frozen_summary_with_it(tmp_path, monkeypatch):
    monkeypatch.setattr(coldstore, "ROOT", tmp_path / "cold")
    root = tmp_path / "fit" / "coros"
    shas = []
    for i in range(6):
        raw = build_run(_start(i), power=_power(i), hr=150, stryd=True)
        sha = fitstore.sha256_hex(raw)
        p = root / fitstore.object_key("coros", sha).split("/", 1)[1]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(fitstore.gzip_fit(raw))
        shas.append(sha)
    ds = _build(root)
    out = coldstore.prepare_archive(CUTOFF, ds=ds)
    assert out["frozen"] == 6
    _archive(root, out["files"])
    assert len(_build(root).cold.archived) == 6
    monkeypatch.setattr(FD, "_deleted_objects", lambda: frozenset(shas[:2]))      # the user deleted two
    assert len(_build(root).cold.archived) == 4


# ---------------------------------------------------------------------------- the sweep

def test_sweep_keeps_channels_a_year_and_recalls_them_from_the_fit(env, monkeypatch):
    ds = _build(env)
    cold = [w for w in ds.workouts if coldstore.is_cold(w.day, T)]
    warm = [w for w in ds.workouts if not coldstore.is_cold(w.day, T)]
    assert cold and warm
    keep = {w.entry.file: ds._store.arrays(w.entry.file) for w in cold[:3]}
    st = coldstore.sweep(ds)
    assert st["npz_dropped"] == len(cold) and st["records"] == len(cold)
    for w in cold:
        assert not ds._store.npz(w.entry.file).exists()
        assert ds._store.files[w.entry.file]["npz"] == "cold"
    for w in warm:
        assert ds._store.npz(w.entry.file).exists()
    # the next build reads no FIT: the entries stay valid without their channels
    parsed = []
    real = fitcache.parse_file
    monkeypatch.setattr(fitcache, "parse_file", lambda *a: parsed.append(a[0]) or real(*a))
    again = _build(env)
    assert parsed == []
    # a page that needs a cold activity's channels: re-parsed from its FIT, counted as a recall
    seen = []
    state = buildstate.recall("coros")

    def spy(*a):
        seen.append(state.snapshot())
        return real(*a)
    monkeypatch.setattr(fitcache, "parse_file", spy)
    w = next(x for x in again.workouts if x.entry.file in keep)
    f = again.wko4(w.idx)
    assert f is not None and len(seen) == 1
    assert seen[0]["state"] == "building" and seen[0]["phase"] == "recall" and seen[0]["n_total"] == 1
    assert "FIT" in seen[0]["message"]
    assert state.snapshot()["state"] == "ready"
    t, ch = keep[w.entry.file]
    t2, ch2 = again._store.arrays(w.entry.file)
    assert np.array_equal(t, t2) and set(ch) == set(ch2)
    assert all(np.array_equal(ch[k], ch2[k], equal_nan=True) for k in ch)
    # a recalled file's channels go again NPZ_RECALL_DAYS later
    p = again._store.npz(w.entry.file)
    assert p.exists()
    assert coldstore.sweep(again)["npz_dropped"] == 0
    old = p.stat().st_mtime - (coldstore.NPZ_RECALL_DAYS + 1) * 86400
    os.utime(p, (old, old))
    assert coldstore.sweep(again)["npz_dropped"] == 1 and not p.exists()


def test_charts_over_cold_data_read_the_summaries_not_the_fits(env, monkeypatch):
    ds = _build(env)
    coldstore.sweep(ds)
    out = coldstore.prepare_archive(CUTOFF, ds=_build(env))
    _archive(env, out["files"])
    parsed = []
    real = fitcache.parse_file
    monkeypatch.setattr(fitcache, "parse_file", lambda *a: parsed.append(a[0]) or real(*a))
    d = _build(env)
    assert d.cold.archived
    _env(d, d.first_day, T)
    _tl(d, "ctl")
    _tl(d, "tl((tisaerobic), ctlconstant)")
    Evaluator(d, T - 365 * 3, T).evaluate("ftp(meanmax(runpower), 90)")
    assert parsed == []                                  # not one FIT read again


def test_a_new_old_activity_updates_only_its_month(env):
    """A late import of an old activity: its month's segment (envelope, daily TSS) changes, no other
    month's; only the TIS of the activities whose 91-day window holds it is made again."""
    from backend.engine.wko5expr.evaluator import BUILTIN_DISK_LOOKBACK_DAYS
    ds = _build(env)
    coldstore.sweep(ds)
    store = coldstore.ColdStore(env)
    segs = {m: store.load(m)["segment"] for m in store.months()}
    (env / "2024" / "late.fit").write_bytes(build_run(datetime(2024, 5, 11, 7, tzinfo=timezone.utc),
                                                      power=_power(3), hr=150, stryd=True))
    after = _build(env)
    new = next(w for w in after.workouts if w.entry.file == "2024/late.fit")
    win = [w for w in after.workouts if 0 <= math.floor(w.day) - math.floor(new.day) <= BUILTIN_DISK_LOOKBACK_DAYS]
    st = coldstore.sweep(after)
    assert st["records"] == len(win) and len(win) < 10
    assert st["months"] == sorted({coldstore.month_of(w) for w in win})
    now = {m: store.load(m)["segment"] for m in store.months()}
    changed = {m for m in now if {k: v for k, v in now[m].items() if k != "tis"}
               != {k: v for k, v in (segs.get(m) or {}).items() if k != "tis"}}
    assert changed == {"2024-05"}
    assert any(r["file"] == "2024/late.fit" for r in store.load("2024-05")["activities"].values())


def test_series_rows_of_deleted_activities_go(env):
    ds = _build(env)
    for w in ds.workouts:
        ds.cached_series("probe_v1", w, lambda w=w: w.entry.file)
    ds.flush_series()
    db = ds._store.home / seriesdb.FILE_NAME
    gone = ds.workouts[-1]
    fid = ds._series_fid(gone.entry.file)
    (env / gone.entry.file).unlink()                      # the user deleted it (not an archive)
    after = _build(env)
    st = coldstore.sweep(after)
    assert st["rows_pruned"] == 1
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        fids = {r[0] for r in con.execute("SELECT fid FROM series")}
    finally:
        con.close()
    assert fid not in fids and len(fids) == len(after.workouts)


def test_a_record_of_an_activity_gone_without_the_archive_is_dropped(env):
    ds = _build(env)
    coldstore.sweep(ds)
    store = coldstore.ColdStore(env)
    w = ds.workouts[0]
    m = w.entry.start.strftime("%Y-%m")
    assert any(r["file"] == w.entry.file for r in store.load(m)["activities"].values())
    (env / w.entry.file).unlink()                         # deleted, never frozen: not an archive
    after = _build(env)
    assert not after.cold.archived
    coldstore.sweep(after)
    assert not any(r["file"] == w.entry.file for r in store.load(m)["activities"].values())


# ---------------------------------------------------------------------------- queue / status

def test_the_sweep_is_a_cold_job_of_the_queue(monkeypatch):
    """The warm-up's last step queues the sweep on the Dataset it read: `full` at `cold` (after
    every waiting job but the monthly archive); a Dataset dropped before it runs is skipped."""
    ran = []
    monkeypatch.setattr(coldstore, "sweep", lambda ds, job=None: ran.append((ds, job)))

    class DS:
        _store = object()
    ds = DS()
    jobs._INLINE = True
    try:
        job = coldstore.schedule_sweep("test", ds)
        assert job.priority == jobs.PRIORITIES["cold"] and job.cls == "full"
        assert ran == [(ds, job)] and job.state == "done"
    finally:
        jobs._INLINE = False
    import gc
    import weakref
    ref = weakref.ref(DS())                                   # dropped before the job ran
    gc.collect()
    assert coldstore._sweep_job(ref, None) is None and len(ran) == 1


def test_the_warm_up_queues_the_sweep_after_the_classification(monkeypatch, tmp_path):
    """Queued when the classification's last tier is done, right after SP-394's sweep of the old
    series rows (the same moment: 補完新版才刪舊版); at once when there was nothing to classify."""
    from backend.api import activity_auto as AA, racepower as RP, wko5views
    from backend.engine import calibrate as CAL, plan_auto
    from backend.engine.wko5expr import dataset as D
    order = []

    class DS:
        def _series_home(self):
            return tmp_path

    class Job:
        def __init__(self):
            self.cbs = {}

        def when_done(self, fn, key=None):
            self.cbs[key] = fn
    ds, job = DS(), Job()
    monkeypatch.setattr(wko5views, "_dataset", lambda: ds)
    monkeypatch.setattr(plan_auto, "busy", lambda: False)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(AA, "job_for", lambda d, priority=None: order.append("classify") or job)
    monkeypatch.setattr(RP, "warm_charts", lambda j: order.append("charts"))
    monkeypatch.setattr(D, "sweep_series", lambda stores=None, **k: order.append("series"))
    monkeypatch.setattr(coldstore, "schedule_sweep", lambda reason, d=None: order.append(("cold", d)))
    wko5views._low_priority("test", {})
    assert order == ["classify", "charts"]                  # the classification still runs
    job.cbs["sweep"]()                                       # its last tier done
    assert order == ["classify", "charts", "series", ("cold", ds)]
    monkeypatch.setattr(AA, "job_for", lambda d, priority=None: order.append("classify") or None)
    order.clear()
    wko5views._low_priority("test", {})
    assert order == ["classify", ("cold", ds), "charts"]   # nothing to classify: queued at once


def test_nothing_is_frozen_or_dropped_while_a_first_import_is_back_filled(env, monkeypatch):
    """A hot-first Dataset (SP-394 第 4 條: older files deferred, or FitStore.first_import while its
    back-fill runs) holds only part of the history: the sweep skips it, the archive refuses."""
    ds = _build(env)
    ds.deferred = [(dt.date(2023, 1, 1), env / "2023" / "x.fit")]
    assert coldstore.sweep(ds)["skipped"] == "backfill"
    with pytest.raises(coldstore.NotComplete):
        coldstore.prepare_archive(CUTOFF, ds=ds)
    ds.deferred = []
    ds._store.first_import = True
    assert coldstore.sweep(ds)["skipped"] == "backfill"
    assert not coldstore.ColdStore(env).months()
    assert all(ds._store.npz(w.entry.file).exists() for w in ds.workouts)
    ds._store.first_import = False
    assert coldstore.sweep(ds)["npz_dropped"] > 0


def test_dataset_status_shows_a_recall_like_a_build(monkeypatch):
    from backend.api import wko5views as V
    from backend.engine.wko5expr import datasource as DSRC
    monkeypatch.setattr(DSRC, "current_source", lambda *a, **k: "coros")
    buildstate.get("coros").finish()
    st = buildstate.recall("coros")
    st.enter()
    try:
        s = asyncio.run(V.dataset_status())
        assert s["state"] == "building" and s["phase"] == "recall" and s.get("recall") is True
        assert s["message"]
    finally:
        st.leave()
    assert asyncio.run(V.dataset_status())["state"] != "building"


def test_cold_summaries_are_registered_and_backed_up():
    from backend import data_registry as R
    for rel in ("cold/fit_coros/2023-01.json", "cold/abs_0123456789ab/archive.json"):
        e = R.classify(rel)
        assert e is not None and e.cls == R.IMPORTED and e.backup == R.ALWAYS and e.where == R.SHARED, rel


def test_the_store_writes_where_the_registry_says(tmp_path, monkeypatch):
    """A tenant's cold folder for its synced COROS folder lands on the registry's entry (a moved or
    renamed path fails here), and a backup of the tenant carries the month files."""
    from backend import data_registry as R, tenancy
    from backend.engine import backup as B
    from backend.sync import storage
    monkeypatch.setattr(coldstore, "ROOT", None)
    monkeypatch.setattr(storage, "FIT_ROOT", None)
    monkeypatch.delenv(fitcache.ENV_ROOT, raising=False)
    monkeypatch.setenv(tenancy.ENV_HOME, str(tmp_path))
    t = tenancy.Tenant(id="u1", kind=tenancy.USER, root=tmp_path, shared=tmp_path)
    with tenancy.use(t):
        store = coldstore.ColdStore(storage.source_dir("coros"))
        assert store.dir == tmp_path / "cold" / "fit_coros"
        store.save("2023-01", {"v": coldstore.SCHEMA_V, "month": "2023-01",
                               "activities": {"sha1:x": {"file": "a.fit"}}, "segment": None})
        for p in (store.path("2023-01"), store.dir / coldstore.ARCHIVE_FILE):
            e = R.classify(Path(p).relative_to(tmp_path).as_posix())
            assert e is not None and e.cls == R.IMPORTED and e.backup == R.ALWAYS, p
        got = [rel for _p, rel in B._user_files(B.tenant_roots(tmp_path), B.file_entries())]
        assert "cold/fit_coros/2023-01.json" in got
