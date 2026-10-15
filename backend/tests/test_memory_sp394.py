"""SP-394 第 6 條: ten tenants rebuilding at once stay under the container's 3 GB.

Measured (backend/scripts/perf_sp394.py memory): every tenant's Dataset stayed in memory after
the Dataset cache's memory budget (wko5views._DatasetCache, WKO5COACH_DATASET_MEM_MB) had dropped
it — the overview status kept per tenant (api/overview._status_cache) held its Dataset and the
400-day evaluator. A kept Status now lets the Dataset go (Status.detach). Synthetic only."""
from __future__ import annotations

import gc
import weakref

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine.status import Status
from backend.tests.test_quality_gate import TODAY, _daily, _ds, _plan


def _week_ds():
    from backend.engine.planning import Threshold
    plan = _plan(lthr=165, day="2026-09-01")            # as test_aet_weekday._build_week_ds
    plan.thresholds.append(Threshold("2026-09-20", cp=250.0))
    return _ds(_daily(), plan), plan


def test_a_detached_status_keeps_its_values_and_lets_the_dataset_go():
    ds, plan = _week_ds()
    attached = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute().detach()
    assert st.to_dict() == attached.to_dict()
    # while the caller holds the Dataset, everything a cached Status is used for still works
    assert O.week_plan(ds, st, TODAY, prefs=PP.Prefs()) == O.week_plan(ds, attached, TODAY, prefs=PP.Prefs())
    assert st.ev is not None and st.since(28) == attached.since(28)        # the evaluator made again
    ref = weakref.ref(ds)
    del ds, attached
    st.detach()
    gc.collect()
    assert ref() is None                                                    # nothing of the Status holds it
    assert st.to_dict()["indicators"] and st.plan is plan and st.goals == attached_goals(plan)


def attached_goals(plan):
    from backend.engine.planning import goals
    return goals(plan, TODAY)


def test_the_overview_keeps_a_detached_status(monkeypatch):
    from backend.api import overview as OV
    ds, plan = _week_ds()
    monkeypatch.setattr(OV, "_status_cache", {})
    monkeypatch.setattr(OV, "_remember_status", lambda key, st: None)
    monkeypatch.setattr(OV, "_status_key", lambda d, today: (("u1", id(d), today), PP.Prefs()))
    st = OV._status(ds, TODAY)
    assert OV._status(ds, TODAY) is st                                      # cached
    ref = weakref.ref(ds)
    assert isinstance(st.ds, weakref.ProxyType) and st.ds.workouts is ds.workouts
    del ds
    gc.collect()
    assert ref() is None                                                    # the cache does not keep it
    assert list(OV._status_cache.values()) == [st] and st.today == TODAY and st.to_dict()["indicators"]


# ---------------------------------------------------------------------------- the classification job

@pytest.fixture
def auto(monkeypatch):
    from backend import jobs
    from backend.api import activity_auto as AA
    from backend.engine import activity_tags as AT
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: [])
    jobs.reset()
    yield AA
    jobs.reset()


def test_a_done_classification_job_lets_its_dataset_go(tmp_path, auto):
    """The job is the value of a WeakKeyDictionary keyed on its Dataset (activity_auto._JOBS): a
    done callback over the Dataset (the warm-up's series sweep) left in it kept the Dataset alive."""
    from backend.tests.test_activity_auto import _fit_ds
    AA = auto
    ds = _fit_ds(tmp_path, n=3)
    ran = []
    job = AA.job_for(ds)
    job.when_done(lambda d=ds: ran.append(len(d.workouts)), key="sweep")
    assert AA.wait(ds)["state"] == "ready"
    job.done.wait(5)
    assert ran == [3] and job._callbacks == {}
    ref = weakref.ref(ds)
    del ds
    gc.collect()
    assert ref() is None


def test_the_warm_ups_sweep_callback_holds_the_dataset_weakly(tmp_path, monkeypatch):
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import coldstore
    from backend.tests.test_activity_auto import _fit_ds
    swept, cbs = [], {}
    monkeypatch.setattr(coldstore, "schedule_sweep", lambda reason, d: swept.append(d))

    class Job:
        def when_done(self, fn, key=None):
            cbs[key] = fn
    ds = _fit_ds(tmp_path, n=2)
    WV._sweep_when_done(Job(), ds)
    cbs["sweep"]()                                        # done while the Dataset is kept: swept
    assert swept == [ds]
    swept.clear()
    ref = weakref.ref(ds)
    del ds
    gc.collect()
    assert ref() is None                                  # the pending callback does not keep it
    cbs["sweep"]()                                        # gone by then: nothing to sweep
    assert swept == []


# ---------------------------------------------------------------------------- the warm-up's thread

def test_the_warm_ups_low_priority_thread_does_not_hold_the_last_dataset_while_it_waits_again(monkeypatch):
    """A warm-up asked for meanwhile runs the low-priority part once more (`low_again`): its
    thread waited in the queue again with the previous Dataset in a local variable — one Dataset
    per tenant kept alive past the Dataset cache's budget."""
    from backend import jobs
    from backend.api import activity_auto as AA
    from backend.api import racepower as RP
    from backend.api import wko5views as WV

    class DS:
        pass
    made, freed_while_waiting = [], []

    def wait_idle():
        if made:                                          # the second round's wait
            gc.collect()
            freed_while_waiting.append(made[0]() is None)

    def call(fn, **kw):
        d = DS()
        made.append(weakref.ref(d))
        return d
    monkeypatch.setattr(WV, "_wait_plan_idle", wait_idle)
    monkeypatch.setattr(jobs, "call", call)
    monkeypatch.setattr(AA, "job_for", lambda d, priority=None: object())
    monkeypatch.setattr(WV, "_sweep_when_done", lambda job, d: None)
    monkeypatch.setattr(RP, "warm_charts", lambda job: None)
    slot = {"low": "me", "low_again": True}
    WV._low_priority("deploy", slot)
    assert len(made) == 2 and freed_while_waiting == [True] and slot["low"] is None


def test_a_job_waiting_for_its_next_tier_holds_its_dataset_weakly(tmp_path, auto, monkeypatch):
    """Between two tiers the job keeps its key builder (own / code / probes / context for the
    later tiers): the builder held the Dataset, so every tenant's job waiting for its warm / cold
    tier kept a Dataset alive past the Dataset cache's budget."""
    from backend.tests.test_activity_auto import _fit_ds
    AA = auto
    ds = _fit_ds(tmp_path, n=3)
    _keys, kb = AA.activity_keys(ds, [], {})
    assert kb.detach() is kb and kb.context is not None
    ref = weakref.ref(ds)
    del ds
    gc.collect()
    assert ref() is None                                  # the builder alone does not keep it
    seen = []
    real = AA._Keys.detach
    monkeypatch.setattr(AA._Keys, "detach", lambda self: seen.append(1) or real(self))
    ds2 = _fit_ds(tmp_path / "b", n=3)
    assert AA.wait(ds2)["state"] == "ready" and seen == [1]   # the job keeps a detached builder
