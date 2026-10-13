"""SP-461: one run's RE always at ONE body weight — the weight of the run's own day.

Before: the per-run metrics row (athlete.run_metrics, series key `racepower_v1`) was keyed on the
run's own-day weight (Dataset._settings_sig) but computed with TODAY's weight, so a new weight
entry left every older run cached at the old "today" (a warm cache and a cold one gave different
REs), and derive's RE (today's weight) and the grade windows (athlete.grade_samples: each run's
own-day weight) rested on two weights.

Now (racepower.spec.md → Derived inputs → Runs): per kg, every run's RE — the cached row, the trail RE in
derive, the grade windows — is at the run's own-day weight, and the row's key carries that weight.
Without a weight nothing changes (speed ÷ watts, SP-444).

Synthetic only: the demo athlete (backend/demo/build.py, small mode) built into tmp_path; its
weights already change (63.5 → 62.5 kg 150 days ago → 62.0 kg 30 days ago)."""
from __future__ import annotations

import datetime as dt
import json
import shutil

import pytest
from pytest import approx

from backend.tests import demo_fixtures as F


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    from backend.demo import build as B
    r = tmp_path_factory.mktemp("demo-sp461")
    B.build(r, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return r


def _copy(root, tmp_path):
    from backend.demo import build as B
    dst = tmp_path / "root"
    shutil.copytree(root, dst, symlinks=True)
    base = dst / "base" / (dst / "base" / "current").read_text("utf-8").strip()
    B.relocate_paths(base)
    return dst, base


def _fresh_dataset(root, monkeypatch):
    F.demo_env(monkeypatch, root)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    from backend.engine.wko5expr import dataset as D
    ds = wko5views._dataset()
    assert isinstance(ds, D.Dataset)
    return ds


def _runs(ds):
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(dt.date.today())
    runs = [w for w in ds.workouts if w.sport == "run" and tday - A.RIEGEL_WINDOW_DAYS < w.day <= tday + 1]
    return runs, ds.setting("weight", tday)


def _implied_weight(re, speed, power):
    return re * power / speed


def test_each_run_re_is_at_its_own_day_weight(root, tmp_path, monkeypatch):
    from backend.engine.racepower import athlete as A
    r, _ = _copy(root, tmp_path)
    ds = _fresh_dataset(r, monkeypatch)
    runs, today_w = _runs(ds)
    by_idx = {w.idx: w for w in runs}
    m = A.run_metrics(ds, runs, today_w)
    assert m, "the demo athlete has runs with power"
    days = {ds.setting("weight", by_idx[i].day) for i in m}
    assert len(days) > 1, "the demo's runs should span a weight change"
    for i, row in m.items():
        wd = ds.setting("weight", by_idx[i].day)
        assert _implied_weight(row["re"], row["avg_speed_ms"], row["avg_power"]) == approx(wd, rel=1e-12), i


def test_a_new_weight_gives_a_warm_cache_the_cold_cache_numbers(root, tmp_path, monkeypatch):
    """The reproduction: compute (rows cached), add a weight today, compute again. The warm rows must
    be what a cold cache computes — the row's key and its value at the same weight."""
    from backend.engine.racepower import athlete as A
    r, base = _copy(root, tmp_path)
    ds = _fresh_dataset(r, monkeypatch)
    runs, _ = _runs(ds)
    A.run_metrics(ds, runs, ds.setting("weight", runs[-1].day))     # warm the series store
    p = base / "plan.json"
    raw = json.loads(p.read_text("utf-8"))
    raw["weights"].append({"date": dt.date.today().isoformat(), "kg": 58.0})
    p.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
    ds = _fresh_dataset(r, monkeypatch)
    runs, today_w = _runs(ds)
    assert today_w == 58.0
    warm = A.run_metrics(ds, runs, today_w)
    ds2 = _fresh_dataset(r, monkeypatch)
    monkeypatch.setattr(ds2, "_series_store", lambda: None)          # no disk rows: a cold cache
    ds2.__dict__.pop("_series_db", None)
    ds2._series.clear()
    runs2, _ = _runs(ds2)
    cold = A.run_metrics(ds2, runs2, today_w)
    assert set(warm) == set(cold)
    for i in warm:
        assert warm[i]["re"] == cold[i]["re"], i
        assert warm[i]["lss_kg"] == cold[i]["lss_kg"], i


def test_derive_and_the_grade_windows_share_each_runs_weight(root, tmp_path, monkeypatch):
    """The per-run RE, the trail RE in derive and the grade windows of one run: the same weight."""
    from backend.engine.racepower import athlete as A
    from backend.engine.algorithms.effort import divisor_of
    from backend.engine.racepower import re as RE
    r, _ = _copy(root, tmp_path)
    ds = _fresh_dataset(r, monkeypatch)
    runs, today_w = _runs(ds)
    by_idx = {w.idx: w for w in runs}
    m = A.run_metrics(ds, runs, today_w)
    gs = A.grade_samples(ds, runs)
    seen = 0
    for s in gs:
        row = m.get(s["a"])
        if row is None or not s.get("re") or not s["v"]:
            continue
        assert _implied_weight(s["re"], s["v"], s["p"]) == \
            approx(_implied_weight(row["re"], row["avg_speed_ms"], row["avg_power"]), rel=1e-9), s["a"]
        seen += 1
    assert seen
    d = A.derive(ds, dt.date.today(), fetch_weather=False, hiking=False)
    trail = d["re"]["trail_runs"]
    assert trail, "the demo athlete has trail runs"
    for row in trail:
        w = by_idx[row["idx"]]
        wd = ds.setting("weight", w.day)
        assert row["re_trail"]["fitted_run"] == approx(
            RE.trail_re(row["km"], row["climb_m"], row["moving_s"], row["avg_power"], wd, divisor_of("fitted_run")),
            rel=1e-12), row["idx"]
