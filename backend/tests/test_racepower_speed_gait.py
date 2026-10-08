"""
賽事計算機 SP-229: the time model picks the walking or the running curve from the predicted
speed (GaitRE.re_at) instead of the athlete's majority gait per grade bin — only when the
athlete's own trail back-test says it is no worse (backtest.speed_gait_summary → no_worse);
otherwise the planner keeps the majority gait. docs/research/run-walk-threshold.md §5.3.
"""
from __future__ import annotations

import json

import pytest

from backend.engine.racepower import backtest as BT
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.engine.racepower import runwalk as RW
from backend.tests.test_racepower_v2 import CP, W, fake_v1, synthetic_track

approx = pytest.approx

# (length m, grade): climbs from runnable to steep, with descents between
LEGS = [(1500, 0.0), (1500, 0.04), (1000, -0.03), (1500, 0.08), (1000, -0.05), (1500, 0.12), (1000, -0.08),
        (1500, 0.18), (1000, -0.10), (1200, 0.25), (1000, -0.12), (800, 0.35), (2000, -0.15), (1500, 0.0)]


def _z(x: float) -> float:
    z, at = 300.0, 0.0
    for d, g in LEGS:
        if x <= at + d:
            return z + (x - at) * g
        z += d * g
        at += d
    return z


def _course():
    return CO.build_course(synthetic_track({"len": sum(d for d, _ in LEGS), "z": _z}))


def _model(speed_gait: bool, shift: float = 0.0):
    """Running windows on every bin and walked windows from 4 %: walking is cheaper per watt
    (RE 1 − 2g against 1 − 3g), and the walked bins from 4 % have a walked majority."""
    run = [{"g": g / 100, "re": 1.0 * (1 - g / 100 * 3), "v": 2.5, "a": 0, "run": 1.0}
           for g in range(-20, 40, 2) for _ in range(40)]
    walk = [{"g": g / 100, "re": 1.0 * (1 - g / 100 * 2), "v": 1.0, "a": 0, "run": 0.0}
            for g in range(4, 40, 2) for _ in range(60)]
    m = GM.fit_gait_re(run + walk, 1.0)
    m.runwalk = {"shift": shift, "personal": bool(shift)}
    return m.with_speed_gait(speed_gait)


def _plan(model, **opts):
    c = _course()
    v1 = fake_v1("trail", c["totals"]["km"], c["totals"]["gain_m"])
    return PL.plan_run(v1=v1, course=c, grade_re=model, opts={"mode": "auto", **opts}, validated={},
                       effort_validated=False)


def _walked(plan) -> int:
    return sum(1 for s in plan["segments"] if s["gait_curve"] == "walk")


# ---- the model --------------------------------------------------------------------------

def test_speed_gait_is_off_by_default_and_survives_the_planner_copies():
    m = GM.fit_gait_re([], 1.0)
    assert m.speed_gait is False and m.to_json()["speed_gait"] is False
    on = m.with_speed_gait(True)
    assert on.speed_gait and not m.speed_gait                                  # a copy, not in place
    assert on.for_trail("race").speed_gait and on.with_flat(1.1).speed_gait and on.for_surface("wet").speed_gait
    assert on.to_json()["speed_gait"] is True


def test_curve_at_follows_the_switch_or_the_majority():
    m = _model(False)
    g = 0.16
    fast_p = RW.horizontal(g, RW.eots(g) * 1.3) * W / m.run.re(g)
    slow_p = RW.horizontal(g, RW.pts(g) * 0.7) * W / m.run.re(g)
    # majority gait: walked at 16 % whatever the power
    assert m.walked(g) and m.curve_at(g, fast_p, W) == "walk" and m.curve_at(g, slow_p, W) == "walk"
    on = m.with_speed_gait(True)
    assert on.curve_at(g, fast_p, W) == "run" and on.curve_at(g, slow_p, W) == "walk"
    assert on.curve_at(0.0, slow_p, W) == "run"                                 # flats always run
    # trusted / data_n read the curve that predicts the segment
    assert on.data_n_at(g, fast_p, W) == on.run.data_n(g) and on.data_n_at(g, slow_p, W) == on.walk.data_n(g)


# ---- the planner ------------------------------------------------------------------------

def test_majority_gait_plan_is_unchanged():
    """Flag off: the same plan as a model that never heard of SP-229 (re only, no re_at)."""
    m = _model(False)
    a = _plan(m)
    assert a["summary"]["runwalk"]["speed_gait"] is False
    climbs = [s for s in a["segments"] if s["grade"] >= 0.03]
    assert climbs
    for s in a["segments"]:
        assert s["gait_curve"] == ("walk" if m.walked(s["grade"]) else "run")
        majority = "走（你在這個坡度多半走）" in s["notes"]
        assert majority == (s["gait_curve"] == "walk")
        assert not any(n.startswith("走（依") for n in s["notes"])
        # SP-226's label stays the final speed's
        assert s["gait"] == (RW.gait(s["grade"], s["speed_ms"]) if s["grade"] >= 0.03 else None)


def test_speed_gait_plan_uses_the_predicted_speed():
    m = _model(True)
    p = _plan(m)
    assert p["summary"]["runwalk"]["speed_gait"] is True
    seen = set()
    for s in p["segments"]:
        cur = m.for_trail("race").curve_at(s["grade"], s["power"], W)
        assert s["gait_curve"] == cur
        seen.add(cur)
        # one decision: the label, the note and the curve all come from the running curve's speed
        run_v = m.run.re(s["grade"]) * s["power"] / W
        assert s["gait"] == RW.gait(s["grade"], run_v, m.rw_shift)
        assert ("走（依這段的預估速度，用走的比跑省力）" in s["notes"]) == (cur == "walk")
        assert "走（你在這個坡度多半走）" not in s["notes"]
        if cur == "walk":
            assert s["gait"] == "walk" and s["walk"] == RW.walk_label("walk")
    assert seen == {"walk", "run"}


@pytest.mark.parametrize("mode,key,values", [
    ("auto", "effort_target", (0.70, 0.80, 0.90, 1.00)),
    ("power", "target_power", (0.45 * CP, 0.60 * CP, 0.75 * CP, 0.90 * CP, 1.05 * CP)),
])
def test_raising_the_intensity_never_adds_walked_segments(mode, key, values):
    """The ticket's test: the same course, a higher target → never more walked segments."""
    m = _model(True)
    counts = [_walked(_plan(m, mode=mode, **{key: v})) for v in values]
    assert all(b <= a for a, b in zip(counts, counts[1:])), counts
    assert counts[0] > counts[-1], counts                       # and the switch does move on this course
    labels = [sum(1 for s in _plan(m, mode=mode, **{key: v})["segments"] if s["gait"] == "walk") for v in values]
    assert all(b <= a for a, b in zip(labels, labels[1:])), labels
    # the majority gait walks the same segments at every intensity
    off = [_walked(_plan(_model(False), mode=mode, **{key: v})) for v in values]
    assert len(set(off)) == 1


def test_personal_shift_moves_the_planner_switch():
    """A curve shifted up (SP-228: this athlete walks at faster speeds) walks at least as many segments."""
    assert _walked(_plan(_model(True, 0.4))) >= _walked(_plan(_model(True, 0.0)))


def test_speed_gait_note_is_translated():
    from backend.i18n import use_locale, _
    with use_locale("en"):
        en = _("走（依這段的預估速度，用走的比跑省力）")
    assert en != "走（依這段的預估速度，用走的比跑省力）" and "walk" in en.lower()


# ---- the gate ---------------------------------------------------------------------------

def test_speed_gait_flag_reads_the_stored_back_test(tmp_path):
    p = tmp_path / "bt.json"
    assert BT.speed_gait_flag(p) is False                                       # nothing stored
    for ok in (True, False):
        p.write_text(json.dumps({"version": 2, "terrain": {"speed_gait": {"no_worse": ok}}}), encoding="utf-8")
        assert BT.speed_gait_flag(p) is ok
    p.write_text(json.dumps({"version": 2, "terrain": {}}), encoding="utf-8")
    assert BT.speed_gait_flag(p) is False


def test_summary_reports_the_segments_whose_curve_changed():
    def row(errs):
        return {"category": "trail", "err_v2": 0.0, "segments": [
            {"grade": g, "err": e, "err_speed": es, "gait": gm, "gait_speed": gs} for g, e, es, gm, gs in errs]}
    rows = [row([(0.10, 0.10, 0.02, "run", "walk"), (0.20, -0.08, -0.08, "walk", "walk"), (0.0, 0.03, 0.03, "run", None)])] * 3
    s = BT.speed_gait_summary(rows)
    assert s["changed"] == 3
    assert s["changed_segments"]["gait"]["n"] == 3 and s["changed_segments"]["gait"]["median_abs"] == approx(0.10)
    assert s["changed_segments"]["speed_gait"]["median_abs"] == approx(0.02)


@pytest.mark.parametrize("flag", [True, False])
def test_api_sets_the_switch_from_the_back_test(monkeypatch, flag):
    """/racepower's grade models carry speed_gait = the stored back-test's no_worse."""
    from backend.api import racepower as RP
    from backend.engine.racepower import athlete as A
    m = _model(False)
    monkeypatch.setattr(RP, "_dataset", lambda: object())
    monkeypatch.setattr(RP._tenancy, "ds_key", lambda ds: ("t", 1))
    monkeypatch.setattr(RP, "inputs", lambda *a, **k: {"re": {"road": {"median": 1.0}}})
    monkeypatch.setattr(A, "grade_models", lambda *a, **k: {"grade_re": m, "hike_speed": GM.fit_hike_speed([])})
    monkeypatch.setattr(A, "solo_hikes", lambda: set())
    monkeypatch.setattr(BT, "class_model_flag", lambda *a: False)
    monkeypatch.setattr(BT, "surface_split_flag", lambda *a: False)
    monkeypatch.setattr(BT, "speed_gait_flag", lambda *a: flag)
    monkeypatch.setattr(BT, "load", lambda *a: None)
    RP._cache.pop("grade", None)
    try:
        assert RP._grade_models()["grade_re"].speed_gait is flag
    finally:
        RP._cache.pop("grade", None)
