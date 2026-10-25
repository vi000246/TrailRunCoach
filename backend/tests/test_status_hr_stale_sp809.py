"""
SP-809 (SP-361 Q2 「總覽狀態卡也排進來」; SP-809 Q1 = B, owner 2026-10-21): after switching 設定 → 課表心率區間
the overview's status card answers the previous status at once, marked 「心率區間更新中」 (stale reason
`hr`), and the new model's status is computed in the background — the 課表's SP-361 B for the overview.
The 測試 card's 「最大心率可信度低」 warning (thr_check.warn) is re-applied under the new model at once.

Before: the status key holds HRP.stamp() among the user's own settings and a settings change is never
served stale — GET /status recomputed the whole Status in the request (0.74–0.90 s on the 52-week demo
data, ≈ 6–8 s on the NAS). The HR model stays in the key (Q1: the 輕鬆跑上限 decides the gate's Zone 3
dose), only the wait is gone.

Synthetic only: a fake Status (the SP-394 scaffolding), a stubbed HR stamp, a tmp tenant.
"""
from __future__ import annotations

import dataclasses
import json
import threading
import time
from pathlib import Path

import pytest

from backend import tenancy
from backend.engine import hr_profile as HRP
from backend.engine import threshold_confidence as TC
# the SP-394 scaffolding: a fake Status recording its computations, the module state reset per test
from backend.tests.test_queue_followups_sp394 import DAY, FakeStatus, _ds, _user, _wait, clean, ov  # noqa: F401


def _thr(model, setting, hconf="low", auto="lthr"):
    return {"hrmax": {"value": 190, "confidence": hconf}, "lthr": {"value": 170, "confidence": "high"},
            "warn": {"lthr": {"low": False}, "hrmax": {"low": hconf == "low" and model in ("hrr", "hrmax"),
                                                         "text": "最大心率可信度低"},
                     "model": model, "setting": setting, "auto": auto}}


class HrStatus(FakeStatus):
    """A status whose 測試 card holds the threshold check of the model in effect when computed."""

    def to_dict(self):
        setting = HRP.plan_model()
        model = "lthr" if setting == "auto" else setting
        return {**super().to_dict(), "model_at_compute": setting,
                "indicators": [{"id": "testing", "extra": {"thr_check": _thr(model, setting)}},
                               {"id": "fitness", "extra": {}}]}


@pytest.fixture
def hr(ov, monkeypatch):
    """The overview module with HrStatus and a 課表心率區間 setting the test switches."""
    monkeypatch.setattr(ov, "Status", HrStatus)
    model = {"v": "lthr", "acc": {}}
    monkeypatch.setattr(HRP, "plan_model", lambda *a: model["v"])
    monkeypatch.setattr(HRP, "stamp", lambda *a: json.dumps([model["acc"], model["v"]], sort_keys=True))
    return ov, model


def _warn(v):
    return next(i for i in v["indicators"] if i["id"] == "testing")["extra"]["thr_check"]["warn"]


def test_a_model_switch_answers_the_previous_status_marked_hr_and_recomputes_once(hr, tmp_path):
    OV, model = hr
    with tenancy.use(_user(tmp_path, "u2")):
        ds = _ds(1)
        first = OV.status_answer(ds, DAY)
        assert "stale" not in first and _warn(first)["hrmax"]["low"] is False and len(FakeStatus.runs) == 1
        model["v"] = "hrr"                                     # PUT /plan/hr-profile {"model": "hrr"}
        FakeStatus.gate = threading.Event()                    # the new model's status is slow
        t0 = time.perf_counter()
        v = OV.status_answer(ds, DAY)
        assert time.perf_counter() - t0 < 0.5                  # no wait for the computation
        assert v["stale"]["reason"] == "hr" and v["model_at_compute"] == "lthr"      # the previous status…
        w = _warn(v)                                           # …its warning under the new model
        assert w["hrmax"]["low"] is True and w["model"] == "hrr" and w["setting"] == "hrr"
        assert _wait(lambda: len(FakeStatus.runs) == 2)        # computed in the background
        assert OV.status_updating() is True
        assert OV.status_answer(ds, DAY)["stale"]["reason"] == "hr" and len(FakeStatus.runs) == 2   # not twice
        FakeStatus.gate.set()
        assert _wait(lambda: not OV.status_updating())
        fresh = OV.status_answer(ds, DAY)
        assert "stale" not in fresh and fresh["model_at_compute"] == "hrr" and len(FakeStatus.runs) == 2


def test_back_to_lthr_and_to_auto_the_warning_follows(hr, tmp_path):
    OV, model = hr
    with tenancy.use(_user(tmp_path, "u2")):
        ds = _ds(1)
        OV.status_answer(ds, DAY)                              # computed under lthr
        model["v"] = "hrmax"
        assert _warn(OV.status_answer(ds, DAY))["hrmax"]["low"] is True
        assert _wait(lambda: not OV.status_updating())
        model["v"] = "auto"                                    # 自動: the pick the check kept (lthr here)
        v = OV.status_answer(ds, DAY)
        assert v["stale"]["reason"] == "hr" and _warn(v)["hrmax"]["low"] is False and _warn(v)["model"] == "lthr"
        assert _wait(lambda: not OV.status_updating())


def test_the_model_with_another_setting_or_a_demo_is_computed_in_the_request(hr, tmp_path, monkeypatch):
    OV, model = hr
    u2 = _user(tmp_path, "u2")
    with tenancy.use(u2):
        OV.status_answer(_ds(1), DAY)
    from backend.engine import plan_prefs as PP
    real = PP.load()
    monkeypatch.setattr(PP, "load", lambda *a, **k: dataclasses.replace(real, weekly_hours=(real.weekly_hours or 0) + 1.5))
    model["v"] = "hrr"                                         # 課表偏好 saved too: they expect its effect
    with tenancy.use(u2):
        v = OV.status_answer(_ds(1), DAY)
        assert "stale" not in v and v["model_at_compute"] == "hrr" and len(FakeStatus.runs) == 2
    demo = dataclasses.replace(u2, id="demo-x", kind=tenancy.DEMO_SANDBOX, root=tmp_path / "demo-x",
                               shared=tmp_path / "demo-x")
    with tenancy.use(demo):
        OV.status_answer(_ds(7), DAY)
        model["v"] = "hrmax"
        assert "stale" not in OV.status_answer(_ds(7), DAY)


def test_a_status_kept_before_sp809_is_computed_in_the_request(hr, tmp_path):
    """The disk record of an older version has no settings_but_hr: no stale answer for a model switch."""
    OV, model = hr
    with tenancy.use(_user(tmp_path, "u2")):
        OV.status_answer(_ds(1), DAY)
        rec = json.loads(OV._last_path().read_text("utf-8"))
        rec.pop("settings_but_hr")
        OV._last_path().write_text(json.dumps(rec), "utf-8")
        OV._status_last.clear()
        OV._status_cache.clear()
        model["v"] = "hrr"
        v = OV.status_answer(_ds(1), DAY)
        assert "stale" not in v and v["model_at_compute"] == "hrr"


def test_rewarn_follows_the_setting_from_what_the_check_kept():
    chk = _thr("lthr", "lthr", hconf="low", auto="hrr")
    assert TC.rewarn(chk, "hrr")["hrmax"]["low"] is True
    assert TC.rewarn(chk, "hrmax")["hrmax"]["low"] is True
    assert TC.rewarn(chk, "lthr")["hrmax"]["low"] is False
    a = TC.rewarn(chk, "auto")                                 # 自動 → the kept pick
    assert a["hrmax"]["low"] is True and a["model"] == "hrr" and a["setting"] == "auto"
    assert a["hrmax"]["text"] == chk["warn"]["hrmax"]["text"] and a["lthr"] == chk["warn"]["lthr"]
    assert TC.rewarn(_thr("hrr", "hrr", hconf="medium"), "hrmax")["hrmax"]["low"] is False
    old = _thr("lthr", "lthr")
    del old["warn"]["auto"]                                    # kept before SP-809
    assert TC.rewarn(old, "auto") is None and TC.rewarn(old, "hrr") is not None
    assert TC.rewarn(None, "hrr") is None and TC.rewarn({"warn": None}, "hrr") is None


@pytest.mark.parametrize("computed", ["hrr", "lthr", "auto"])
def test_rewarn_equals_the_check_computed_under_the_new_setting(computed):
    """assess() (the check's pure core) keeps the 自動 pick in warn; rewarn of a check computed under one
    setting gives the warning the check itself gives under every other one (the 「最大心率可信度低」 runner)."""
    from backend.tests.test_threshold_confidence import users_case
    r = users_case(hr_model=computed)
    assert r["warn"]["auto"] == r["auto"]["model"]
    for setting in ("lthr", "hrr", "hrmax", "auto"):
        want = users_case(hr_model=setting)["warn"]
        got = TC.rewarn(r, setting)
        assert got == want, (computed, setting)


def test_the_overview_badge_says_hr_zones_updating():
    static = Path(__file__).resolve().parents[1] / "static"
    page = (static / "overview.html").read_text("utf-8")
    assert 'st.reason === "hr" ? tO("stale.hr")' in page and '"stale.hr_tip"' in page
    zh = json.loads((static / "i18n" / "zh-TW" / "overview.json").read_text("utf-8"))
    en = json.loads((static / "i18n" / "en" / "overview.json").read_text("utf-8"))
    assert zh["stale.hr"] == "心率區間更新中" and en["stale.hr"] and zh["stale.hr_tip"] and en["stale.hr_tip"]
