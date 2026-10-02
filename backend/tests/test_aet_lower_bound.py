"""The temporary AeT lower bound (threshold_estimate.aet_lower_bound, drift_agg.aet_validity):
when the regression finds no 5 % crossing, AeT ≥ X counts as valid with safeguards; the
8-week reminder (zone_events) is low priority. Synthetic data only."""
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import drift_agg as DA
from backend.engine import zone_events as ZE
from backend.engine.algorithms import threshold_estimate as TE
from backend.engine.planning import Plan, Threshold
from backend.tests.test_quality_gate import TODAY, _ds
from backend.tests.test_workout_review import _run


def _pts(hrs, drift=0.01, se=0.015, tier="ref"):
    return [(h, drift, se, tier) for h in hrs]


FLAT = [140, 143, 146, 148, 149, 150, 151, 152]


def test_a_flat_low_drift_set_gives_a_lower_bound():
    agg = TE.aet_aggregate([p[:3] for p in _pts(FLAT)])
    assert not agg.valid and agg.code in TE.AET_BOUND_NO_CROSSING
    lb = TE.aet_lower_bound(_pts(FLAT))
    assert lb["ok"] and lb["value"] == 152 and lb["n"] == 8 and lb["top_n"] >= 3 and lb["upper"] < 0.05
    # never above LTHR − 3
    lb = TE.aet_lower_bound(_pts(FLAT), lthr=153)
    assert lb["ok"] and lb["value"] == 150


def test_noisy_or_low_grade_runs_do_not_count():
    assert not TE.aet_lower_bound(_pts(FLAT, se=0.06))["ok"]               # SE > 5 pp
    assert not TE.aet_lower_bound(_pts(FLAT, tier="other"))["ok"]
    assert "要 ≥ 6 次" in TE.aet_lower_bound(_pts(FLAT[:5]))["reason"]


def test_a_clear_high_drift_at_or_below_x_breaks_the_bound():
    pts = _pts(FLAT) + [(145.0, 0.12, 0.01, "ref")]
    lb = TE.aet_lower_bound(pts)
    assert not lb["ok"] and lb["broken"]["hr1"] == 145.0 and "下限不成立" in lb["reason"]
    # a high drift inside the (doubled) noise does not
    assert TE.aet_lower_bound(_pts(FLAT) + [(145.0, 0.07, 0.04, "ref")])["ok"]


def test_the_top_runs_must_be_clearly_below_5_percent_and_dense_near_x():
    assert not TE.aet_lower_bound(_pts(FLAT, drift=0.04))["ok"]            # mean + 2·SE ≥ 5 %
    spread = [120, 125, 130, 135, 140, 145, 152]
    lb = TE.aet_lower_bound(_pts(spread))
    assert not lb["ok"] and lb["top_n"] < 3


def test_aet_validity_reports_the_bound_as_valid_with_its_label():
    ws = [_run(TODAY - dt.timedelta(days=2 * i + 1), minutes=52, hr=h, hr_end=h) for i, h in enumerate(FLAT)]
    ds = _ds(ws)
    v = DA.aet_validity(ds, TODAY)
    assert v["lower_bound"] and v["valid"] and v["se"] is None and v["shift_bpm"] is None
    assert v["value"] == pytest.approx(max(p["hr1"] for p in DA.aet_points(ds, TODAY)))
    assert v["reason"].startswith(f"AeT ≥ {v['value']:.0f} bpm（下限，推估）")
    assert "暫時規則" in v["reason"] and "保守" in v["reason"] and "8 週" in v["reason"]


def test_a_bound_never_fires_moved_or_shift_and_needs_no_test(monkeypatch):
    from backend.engine import quality_gate as QG
    from backend.engine import plan_prefs as PP
    from backend.tests.test_quality_gate import GOOD_BY, BASE, _plan
    monkeypatch.setattr(DA, "aet_points", lambda ds, today, days=180, **kw: [{"hr1": 150, "drift": 0.01, "se": 0.02}])
    monkeypatch.setattr(DA, "aet_validity", lambda ds, today, lthr=None: {
        "valid": True, "value": 152.0, "se": None, "n": 8, "shift_bpm": None, "lower_bound": True,
        "reason": "AeT ≥ 152 bpm（下限，推估）：…", "points": 8})
    plan = _plan(aethr=135, lthr=165)                                       # far below the bound
    g = QG.evaluate(_ds([], plan), plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    assert g["aet_test_reason"] is None and g["aet"]["valid"]
    assert "AeT ≥ 152 bpm（下限，推估）" in QG.indicator(g)["why"]


# ---------------------------------------------------------------------------
# the 8-week reminder
# ---------------------------------------------------------------------------

DAY = dt.date(2026, 11, 20)
BOUND = {"valid": True, "lower_bound": True, "value": 150.0, "bound": {"ok": True}}


def test_reminder_cycles_every_8_weeks_from_the_last_test():
    assert ZE.bound_reminder_due(Plan(), DAY) is not None                 # never tested: due now
    recent = Plan(thresholds=[Threshold("2026-10-30", aethr=140, aethr_method="test")])
    assert ZE.bound_reminder_due(recent, DAY) is None                     # 21 days ago
    old = Plan(thresholds=[Threshold("2026-09-01", aethr=140, aethr_method="test")])
    assert ZE.bound_reminder_due(old, DAY) == "2026-10-27"                # 8 weeks after the test
    assert ZE.bound_reminder_due(old, dt.date(2026, 12, 25)) == "2026-12-22"   # the next cycle: a new id


def test_reminder_is_a_low_priority_box_suggestion_only_while_bound():
    ds = SimpleNamespace(workouts=[])
    out = ZE.suggestions(ds, Plan(), DAY, acts=[], brk={}, points=[], aet_validity=BOUND)
    s = next(x for x in out["suggestions"] if x["id"].startswith("aet_bound:"))
    assert s["priority"] == "low" and s["tests"] == ["aet"] and "下限" in s["title"] and "可以關掉" in s["text"]
    assert not [x for x in ZE.suggestions(ds, Plan(), DAY, acts=[], brk={}, points=[],
                                          aet_validity={**BOUND, "lower_bound": False})["suggestions"]
                if x["id"].startswith("aet_bound")]
    # the box: dismissible by its id (one per 8-week cycle)
    from backend.engine import suggestions as SG
    rows = SG.zone_rows({"suggestions": [s]}, set(), lambda k, since: False, lambda k, e: [])
    assert rows and rows[0]["id"] == f"zone:{s['id']}"
    assert not SG.visible(rows, {rows[0]["id"]: {"action": "dismissed"}})
