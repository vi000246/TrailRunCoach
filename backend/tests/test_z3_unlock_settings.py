"""SP-295: the Zone 3 unlock numbers in 設定 → 進階設定 (engine/advanced_params.py items,
quality_gate.z3_rule). Defaults unchanged; other values drive the unlock and the re-lock; range checks."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi import HTTPException

from backend.engine import advanced_params as AP
from backend.engine import calibrate as CAL
from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.tests.test_calibrate import _run, _session
from backend.tests.test_quality_gate import BASE, GOOD_BY, TODAY, _ds
from backend.tests.test_z5_gate_viz import _easy

MON = TODAY - dt.timedelta(days=TODAY.weekday())


def _wk(i, ks=(0, 2, 4)):
    return [MON - dt.timedelta(weeks=i) + dt.timedelta(days=k) for k in ks]


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)


def _manual(monkeypatch, **vals):
    monkeypatch.setattr(CAL, "stored_entry",
                        lambda name, user_id=1: {"value": vals[name], "source": "user"} if name in vals else None)


def test_defaults_are_todays_constants():
    reg = CAL._registry()
    assert set(AP.Z3_RULE.values()) <= set(reg)
    assert reg["z3_unlock_weeks"].default == QG.Z3_WEEKS_NEED == 4
    assert reg["z3_unlock_runs_per_week"].default == QG.Z3_RUNS_PER_WEEK == 3
    assert reg["z3_unlock_max_gap_days"].default == QG.Z3_MAX_GAP_DAYS == 7
    assert reg["z3_relock_days"].default == QG.Z3_RELOCK_DAYS == 21
    for n in AP.Z3_RULE.values():
        it = reg[n]
        assert it.manual_only and it.integer and it.bounds[0] >= 1 and it.bounds[0] <= it.default <= it.bounds[1]
    assert QG.z3_rule() == {"weeks": 4, "runs": 3, "gap": 7, "relock": 21, "manual": False}
    assert AP.z3_rule_stamp() == (4, 3, 7, 21)


def test_default_rule_behaves_as_before():
    four = sorted(d for i in range(1, 5) for d in _wk(i))
    assert QG.z3_consistency(four, TODAY)["open"]
    assert not QG.z3_consistency(four[3:], TODAY)["open"]                        # 3 weeks


def test_fewer_weeks_and_runs_unlock_sooner(monkeypatch):
    two = sorted(d for i in (1, 2) for d in _wk(i))
    assert not QG.z3_consistency(two, TODAY)["open"]
    _manual(monkeypatch, z3_unlock_weeks=2)
    c = QG.z3_consistency(two, TODAY)
    assert c["open"] and len(c["rows"]) == 2
    two_a_week = sorted(d for i in range(1, 5) for d in _wk(i, (1, 5)))
    assert not QG.z3_consistency(two_a_week, TODAY)["open"]
    _manual(monkeypatch, z3_unlock_runs_per_week=2)
    assert QG.z3_consistency(two_a_week, TODAY)["open"]
    # a stricter gap: Mon / Wed / Fri leaves 2 days off (Sat, Sun) — a 2-day limit breaks it
    four = sorted(d for i in range(1, 5) for d in _wk(i))
    _manual(monkeypatch, z3_unlock_max_gap_days=3)
    assert QG.z3_consistency(four, TODAY)["open"]
    _manual(monkeypatch, z3_unlock_max_gap_days=2)
    assert not QG.z3_consistency(four, TODAY)["open"]


def test_relock_follows_the_setting(monkeypatch):
    # met 4 weeks long ago (last run Fri of week −8), 15 days off, back on Sun of week −6, then
    # 5 regular weeks: the default 21 days keep it open (sticky) …
    old = sorted(d for i in range(8, 12) for d in _wk(i))
    back = old + [MON - dt.timedelta(days=36)] + sorted(d for i in range(1, 6) for d in _wk(i))
    c = QG.z3_consistency(back, TODAY)
    assert c["break"] is None and c["open"]
    # … a 14-day re-lock locks it: only what came after the break counts — 5 complete weeks open it
    # again with the default 4 weeks, not with 6
    _manual(monkeypatch, z3_relock_days=14)
    c = QG.z3_consistency(back, TODAY)
    assert c["break"]["days"] == 15 and c["open"] and c["since"] > c["break"]["return"]
    _manual(monkeypatch, z3_relock_days=14, z3_unlock_weeks=6)
    c = QG.z3_consistency(back, TODAY)
    assert c["break"]["days"] == 15 and not c["open"]


def test_texts_quote_the_values_and_say_default_or_manual(monkeypatch):
    ds = _ds(_easy(range(2, 40, 4)))
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    lab = g["z3"]["tests"][0]["label"]
    assert lab == "連續 4 週，每週跑 ≥ 3 次、沒有 ≥ 7 天沒跑（預設，推估）"
    assert "/4 週每週跑 ≥ 3 次（預設，推估）" in g["z3"]["reason"]
    assert "連續 4 週規律訓練（每週 ≥ 3 次、沒有 ≥ 7 天沒跑；預設，推估，停跑 ≥ 21 天" in QG.option_texts()["auto"]["tip"]
    _manual(monkeypatch, z3_unlock_weeks=6, z3_unlock_runs_per_week=2, z3_unlock_max_gap_days=5, z3_relock_days=30)
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    assert g["z3"]["tests"][0]["label"] == "連續 6 週，每週跑 ≥ 2 次、沒有 ≥ 5 天沒跑（手動）"
    assert g["z3"]["weeks_need"] == 6 and g["z3"]["rule"]["manual"]
    assert "連續 6 週規律訓練（每週 ≥ 2 次、沒有 ≥ 5 天沒跑；手動，停跑 ≥ 30 天" in QG.option_texts()["auto"]["tip"]
    assert AP.z3_rule_stamp() == (6, 2, 5, 30)


def test_api_range_checks_and_replan(monkeypatch):
    from backend.api import calib as API
    from backend.engine import plan_auto as PA
    kicks = []
    monkeypatch.setattr(PA, "after_settings", lambda: kicks.append(1))

    async def go():
        s = await _session()
        for name, bad in (("z3_unlock_weeks", 0), ("z3_unlock_runs_per_week", 8), ("z3_unlock_max_gap_days", 0),
                          ("z3_relock_days", 3)):
            with pytest.raises(HTTPException) as e:
                await API.set_manual(name, API.Manual(value=bad), db=s)
            assert e.value.status_code == 400 and "之間" in e.value.detail
        with pytest.raises(HTTPException) as e:
            await API.set_manual("z3_unlock_weeks", API.Manual(value=2.5), db=s)
        assert "整數" in e.value.detail
        assert kicks == []
        r = await API.set_manual("z3_unlock_weeks", API.Manual(value=6), db=s)
        assert r["source"] == "user" and r["value"] == 6 and r["integer"] and r["chip"]["text"] == "手動"
        assert kicks == [1]
        r = await API.clear_manual("z3_unlock_weeks", db=s)
        assert r["source"] == "default" and r["value"] == 4 and kicks == [1, 1]
        await API.set_manual("heat_partial_hadley", API.Manual(value=120.5), db=s)   # not a plan input
        assert kicks == [1, 1]
        await s.close()
    _run(go())
