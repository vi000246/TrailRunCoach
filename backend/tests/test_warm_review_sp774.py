"""SP-774: the warm-up's low-priority part computes, before anyone opens a single-activity page, what
the review cards' first view paid for once per process: the code hashes of their disk caches
(codehash.closure walks ~1,000 functions: ~0.5 s local, NAS ~×8.5) and the 門檻信心 check, which reads
every run of the last year (threshold_confidence.check, memoised on the Dataset)."""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.api import wko5views as W
from backend.engine import codehash as C


def test_no_dataset_or_a_broken_one_never_fails_the_warm_up():
    assert W._warm_review(None) is False
    assert W._warm_review(SimpleNamespace(workouts=None)) is False      # not iterable: logged, False


def test_it_warms_the_newest_run_with_hr_the_newest_activity_with_power_and_the_check(monkeypatch):
    from backend.engine import threshold_confidence as TC
    from backend.engine import workout_review as WR
    ws = [SimpleNamespace(idx=0, sport="run"), SimpleNamespace(idx=1, sport="bike"),
          SimpleNamespace(idx=2, sport="run"), SimpleNamespace(idx=3, sport="run")]
    chans = {(0, "heartrate"), (0, "power"), (1, "power"), (2, "heartrate"), (3, "speed")}
    plan = object()
    ds = SimpleNamespace(workouts=ws, today=20000.0, plan=plan, flushed=0,
                         channel=lambda i, k: [1.0] if (i, k) in chans else None)
    ds.flush_series = lambda: setattr(ds, "flushed", ds.flushed + 1)
    seen = []
    monkeypatch.setattr(WR, "measure", lambda d, w: seen.append(("measure", w.idx)))
    monkeypatch.setattr(WR, "tis_scores", lambda d, w: seen.append(("tis", w.idx)))
    monkeypatch.setattr(TC, "auto_choice", lambda d, p, today: seen.append(("check", p is plan, today)))
    from backend.engine.wko5expr.dataset import day_to_date
    assert W._warm_review(ds) is True
    assert seen == [("measure", 2), ("tis", 1), ("check", True, day_to_date(20000))]
    assert ds.flushed == 1


def test_warm_up_low_priority_part_runs_it_on_the_dataset_read_after_the_wait(monkeypatch):
    from backend.api import activity_auto as AA
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import racepower as RP
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    from backend.engine import race_readiness as RD
    order = []
    ds = SimpleNamespace(today=20000.0)
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.setattr(W, "_dataset", lambda: ds)
    monkeypatch.setattr(OV, "_status", lambda d, today: None)
    monkeypatch.setattr(PSA, "_compute_inputs", lambda: None)
    monkeypatch.setattr(plan_auto, "busy", lambda: False)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(AA, "job_for", lambda d, **kw: None)
    monkeypatch.setattr(RP, "warm_charts", lambda job=None: None)
    monkeypatch.setattr(RD, "warm", lambda plan, d, today: order.append("readiness") or True)
    monkeypatch.setattr(W, "_warm_review", lambda d: order.append(("review", d is ds)) or True)
    t = W.warm_up("test")
    t.join(10)
    low = W._warm_slot().get("low")
    low and low.join(10)
    assert order == [("review", True), "readiness"]


@pytest.mark.needs_core
def test_after_it_the_first_card_computes_no_code_hash_and_no_check(tmp_path, monkeypatch):
    """On a real Dataset: after _warm_review, the newest run's measurements and the 門檻信心 check
    neither walk the code (codehash.closure) nor gather the runs again."""
    from backend.engine import threshold_confidence as TC
    from backend.engine import workout_review as WR
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.dataset import day_to_date
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    for i in range(3):
        (d / f"{i}.fit").write_bytes(build_run(datetime(2026, 9, 1 + i, 8, tzinfo=timezone.utc), seconds=600,
                                               power=200 + 10 * i, hr=140 + i, speed_m_s=3.0))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=False), today=dt.date(2026, 9, 30),
                          classifications={}, athlete_settings=[{"effective_date": "2026-01-01", "weight_kg": 60}],
                          estimate_thresholds=False)
    C._CALLABLE.clear()                        # a fresh process
    C._MEMO.clear()
    assert W._warm_review(ds) is True
    walks, gathers = [], []
    real_closure, real_gather = C.closure, TC.gather_runs
    monkeypatch.setattr(C, "closure", lambda *a, **k: walks.append(1) or real_closure(*a, **k))
    monkeypatch.setattr(TC, "gather_runs", lambda *a, **k: gathers.append(1) or real_gather(*a, **k))
    newest = [w for w in ds.workouts if w.sport == "run"][-1]
    WR.measure(ds, newest)
    WR.measure(ds, ds.workouts[0])             # another activity: the same compute, the same code hash
    TC.auto_choice(ds, getattr(ds, "plan", None), day_to_date(int(ds.today)))
    assert walks == [] and gathers == []
