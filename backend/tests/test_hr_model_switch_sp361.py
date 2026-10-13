"""SP-361: switching 設定 → 課表心率區間 (PUT /api/v1/plan/hr-profile {model}) made the 課表 load
slowly. The model is a setting read live (hr_profile.plan_model, HP.stamp() in every cache key of
what it changes: the plan inputs, the overview status, the zone / target charts), yet the switch
went through _notify(True), which drops every cached Dataset (wko5views.plan_changed) — so the next
課表 load rebuilt the whole Dataset before re-planning. A model-only switch keeps the Datasets and
still starts the automatic re-plan / re-push (plan_auto.after_thresholds); a manual max / resting HR
(a plan threshold row) rebuilds them as before. What the Dataset's own memo kept and the model
changes follows the model now (threshold_confidence.check's 「最大心率可信度低」 warning)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from backend.api import plan as API
from backend.api import wko5views as WV
from backend.engine import hr_profile as HP
from backend.engine import plan_auto as PA
from backend.engine import planning as P
from backend.engine import threshold_confidence as TC
from backend.settings.repository import SettingsRepository
from backend.tests.test_coros_workouts import make_db, run


@pytest.fixture
def spy(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    load, save = P.Plan.load.__func__, P.Plan.save
    monkeypatch.setattr(P.Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    monkeypatch.setattr(P.Plan, "save", lambda self, p=path: save(self, p))
    monkeypatch.setattr(API, "_estimate_dataset", lambda: None)
    monkeypatch.setattr(API, "hr_profile_view", lambda ds, today: {"ok": True})
    seen = {"cleared": 0, "replans": 0}
    monkeypatch.setattr(WV._dataset_cfg, "cache_clear", lambda: seen.__setitem__("cleared", seen["cleared"] + 1))
    monkeypatch.setattr(PA, "after_thresholds", lambda: seen.__setitem__("replans", seen["replans"] + 1))
    return seen


def test_switching_the_model_keeps_the_datasets_and_still_replans(spy):
    async def go():
        db = await make_db(logged_in=False)
        await API.put_hr_profile(API.HrProfileIn(model="hrr"), db)
        return await SettingsRepository(db, 1).get(HP.MODEL_KEY)
    assert run(go()) == "hrr"
    assert spy == {"cleared": 0, "replans": 1}


def test_a_manual_max_hr_still_rebuilds_the_datasets(spy):
    async def go():
        db = await make_db(logged_in=False)
        await API.put_hr_profile(API.HrProfileIn(max_hr=190, model="hrmax"), db)
    run(go())
    assert spy == {"cleared": 1, "replans": 1}


def test_nothing_changed_nothing_happens(spy):
    async def go():
        db = await make_db(logged_in=False)
        await API.put_hr_profile(API.HrProfileIn(), db)
    run(go())
    assert spy == {"cleared": 0, "replans": 0}


def test_the_threshold_check_memo_follows_the_model(monkeypatch):
    # the check is memoised on the Dataset (ds.memo); with the Dataset kept across a model switch
    # its 「最大心率可信度低」 warning (only under % HRR / % HRmax) must follow the model in use
    model = {"v": "lthr"}
    monkeypatch.setattr(HP, "plan_model", lambda user_id=1: model["v"])
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    monkeypatch.setattr(TC, "lthr_info", lambda ds, plan, today: {"value": 165.0, "source_kind": "test"})
    monkeypatch.setattr(TC, "mhr_info", lambda ds, today, acc=None: {"value": 175.0, "source_kind": "estimate"})
    monkeypatch.setattr(TC, "rhr_info", lambda ds, today, acc=None: {"value": 50.0, "source_kind": "manual"})
    monkeypatch.setattr(TC, "gather_runs", lambda ds, today, acts=None: [])
    monkeypatch.setattr(TC, "_cp_now", lambda ds, plan, today: None)
    monkeypatch.setattr(TC, "test_results", lambda ds, plan, today: [])
    ds = SimpleNamespace(memo={}, workouts=[])
    today = dt.date(2026, 10, 13)
    first = TC.check(ds, P.Plan(), today)
    assert first["warn"]["model"] == "lthr"
    model["v"] = "hrr"
    again = TC.check(ds, P.Plan(), today)
    assert again["warn"]["model"] == "hrr"
    model["v"] = "lthr"
    assert TC.check(ds, P.Plan(), today) is first          # still memoised per model
