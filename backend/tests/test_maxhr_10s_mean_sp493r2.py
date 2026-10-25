"""SP-493 R2 (owner 2026-10-20, Q4): the runs' max HR is the best 10-s mean after the shared cleaning
(hr_quality.rolling_mean_peak), no top run dropped — for the max-HR estimate (thresholds.estimate_mhr)
and the watch-account check (threshold_confidence.watch_seen's s10). 「撐 10 秒」 (every second ≥ it)
was too hard. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pytest

from backend.engine import hr_profile as HP
from backend.engine import hr_quality as HQ
from backend.engine import threshold_confidence as TC
from backend.engine import thresholds as TH
from backend.i18n import use_locale
from backend.tests.test_hr_profile import TODAY, _ds, _hr_run
from backend.tests.wko5_fakes import FakeWorkout

CJK = re.compile(r"[㐀-鿿]")


def _finish(n=1800, top=205.0, top_s=6, base=195.0):
    """150 → a 60-s climb to `base` held 30 s → `top_s` s at `top` → `base` 10 s → back to 150."""
    hr = np.full(n, 150.0)
    a = 1200
    hr[a - 60:a] = np.linspace(150.0, base, 60)
    hr[a:a + 30] = base
    hr[a + 30:a + 30 + top_s] = top
    hr[a + 30 + top_s:a + 40 + top_s] = base
    hr[a + 40 + top_s:a + 100 + top_s] = np.linspace(base, 150.0, 60)
    return np.arange(n, dtype=float), hr


def test_rolling_mean_peak_is_the_best_10s_mean_of_full_windows():
    y = np.full(100, 195.0)
    y[50:56] = 205.0                                   # 6 s at 205 inside 195
    assert HQ.rolling_mean_peak(y, 10) == pytest.approx(201.0)     # (6 × 205 + 4 × 195) / 10
    assert HQ.held_peak(y, 10) == pytest.approx(195.0)             # held_peak keeps its meaning
    y[40:60] = np.nan                                  # a window with a gap never counts
    assert HQ.rolling_mean_peak(y, 10) == pytest.approx(195.0)
    assert HQ.rolling_mean_peak(np.full(5, 150.0), 10) is None
    assert HQ.rolling_mean_peak(np.full(30, np.nan), 10) is None


def test_a_runs_peak_is_its_best_10s_mean_after_the_cleaning():
    t, hr = _finish()
    assert TH.peak_sustained_hr(t, hr) == pytest.approx(201.0)
    hr[300:303] = 223.0                                # an optical spike: cleaned away first
    assert TH.peak_sustained_hr(t, hr) == pytest.approx(201.0)


def _run_of(day, hr):
    n = len(hr)
    ch = {"elapsedtime": list(np.arange(n, dtype=float)), "heartrate": list(hr),
          "speed": list(np.full(n, 10.0)), "elapseddistance": list(np.arange(n) * 10.0 / 3600.0)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch, metrics={"duration": float(n)})


def test_the_estimate_reads_the_10s_mean_and_keeps_the_top_run():
    d = TODAY - dt.timedelta(days=10)
    runs = [_hr_run(d - dt.timedelta(days=20 + i * 20), p) for i, p in enumerate((182.0, 183.0, 184.0))]
    runs.append(_run_of(d, _finish()[1]))              # a hard finish: 6 s at 205 — 10-s mean 201
    est = TH.estimate_mhr(_ds(runs), TODAY)
    assert est["value"] == 201                         # 17 above the next: no longer dropped
    assert est["peaks"][0] == [d.isoformat(), 201]
    assert "10 秒平均心率的最高值" in est["reason"] and "誤差" not in est["reason"]


def test_the_estimate_reason_follows_the_language():
    runs = [_hr_run(TODAY - dt.timedelta(days=10 + i * 20), p) for i, p in enumerate((182.0, 183.0, 184.0))]
    ds = _ds(runs)
    zh = TH.estimate_mhr(ds, TODAY)["reason"]
    with use_locale("en"):
        en = TH.estimate_mhr(ds, TODAY)["reason"]
        few = TH.estimate_mhr(_ds(runs[:2]), TODAY)["reason"]
    assert "10 秒平均" in zh
    assert "10-s average" in en and not CJK.search(en)
    assert "2 runs" in few and not CJK.search(few)


def test_the_watch_check_reads_the_10s_mean():
    t, hr = _finish()
    s = TC.run_summary(t, hr)
    assert s["s10"] == pytest.approx(201.0) and s["s5"] == pytest.approx(205.0)
    watch = {"value": 205.0, "source_kind": "watch"}
    row = {**s, "date": (TODAY - dt.timedelta(days=10)).isoformat()}
    seen = TC.watch_seen(watch, [row], TODAY)
    assert seen and seen["value"] == 201 and seen["gap"] == 4    # a 10-s hold (195) was 10 under: not seen
    r = TC.hrmax_result(t, hr)
    assert r["value"] == 205 and r["s10"] == 201


def test_the_watch_source_says_10s_mean_in_both_languages():
    runs = [_hr_run(TODAY - dt.timedelta(days=10 + i * 20), p) for i, p in enumerate((182.0, 183.0, 184.0))]
    acc = {"max_hr": 190.0, "rest_hr": 53.0}
    m = HP.max_hr(_ds(runs), TODAY, acc)
    assert m["kind"] == "coros" and "近 365 天跑步 10 秒平均最高 184" in m["source"]
    with use_locale("en"):
        m = HP.max_hr(_ds(runs), TODAY, acc)
    assert "best 10-s average" in m["source"] and not CJK.search(m["source"])


def test_the_cache_keys_are_bumped():
    # the per-run peaks are cached (cached_series): a new algorithm needs a new key, or old values stay
    assert TH.MHR_KEY == "mhr_peak5_v3" and TC.RUN_KEY == "thr_conf_run_v3"
    assert not hasattr(TH, "MHR_OUTLIER")
