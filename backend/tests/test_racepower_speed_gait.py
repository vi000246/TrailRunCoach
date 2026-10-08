"""
賽事計算機 SP-229: on trail climbs the time model picks the walking or the running curve from
the predicted speed (GaitRE.re_at) instead of the athlete's majority gait per grade bin — only
when the athlete's own trail back-test kept it (backtest.speed_gait_summary → no_worse);
otherwise the planner keeps the majority gait. docs/research/run-walk-threshold.md §5.3.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backend.engine.racepower import backtest as BT
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.engine.racepower import runwalk as RW
from backend.tests.test_racepower_v2 import CP, W, fake_v1, synthetic_track

approx = pytest.approx
FROZEN = Path(__file__).parent / "fixtures" / "sp229_flag_off_plans.json"
NOTE_SPEED = "走（依這段的預估速度，用走的比跑省力）"
NOTE_MAJORITY = "走（你在這個坡度多半走）"

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


def _model(speed_gait: bool, shift: float = 0.0, walk_re=lambda g: 1 - g * 2, walk_from: int = 4):
    """Running windows on every bin, walked windows from `walk_from` % (walking cheaper per watt
    by default: RE 1 − 2g against 1 − 3g) and on the steep descents −20 … −14 % (so the majority
    gait walks those). The same model as the frozen fixture (backend/tests/fixtures)."""
    run = [{"g": g / 100, "re": 1.0 * (1 - g / 100 * 3), "v": 2.5, "a": 0, "run": 1.0}
           for g in range(-20, 40, 2) for _ in range(40)]
    walk = [{"g": g / 100, "re": walk_re(g / 100), "v": 1.0, "a": 0, "run": 0.0}
            for g in range(walk_from, 40, 2) for _ in range(60)]
    walk += [{"g": g / 100, "re": 0.9, "v": 1.0, "a": 0, "run": 0.0} for g in range(-20, -12, 2) for _ in range(80)]
    m = GM.fit_gait_re(run + walk, 1.0)
    m.runwalk = {"shift": shift, "personal": bool(shift)}
    return m.with_speed_gait(speed_gait)


def _plan(model, kind="trail", validated=None, **opts):
    c = _course()
    v1 = fake_v1(kind, c["totals"]["km"], c["totals"]["gain_m"] if kind == "trail" else 0.0)
    return PL.plan_run(v1=v1, course=c, grade_re=model, opts={"mode": "auto", **opts}, validated=validated or {},
                       effort_validated=False)


def _walked(plan) -> int:
    return sum(1 for s in plan["segments"] if s["gait_curve"] == "walk")


def _s_h(g, shift=0.0):
    return RW.horizontal(g, RW.pts(g, shift))


# ---- the model --------------------------------------------------------------------------

def test_speed_gait_is_off_by_default_and_survives_the_planner_copies():
    m = GM.fit_gait_re([], 1.0)
    assert m.speed_gait is False and m.to_json()["speed_gait"] is False
    on = m.with_speed_gait(True)
    assert on.speed_gait and not m.speed_gait                                  # a copy, not in place
    assert on.for_trail("race").speed_gait and on.with_flat(1.1).speed_gait and on.for_surface("wet").speed_gait
    assert on.to_json()["speed_gait"] is True


def test_curve_at_follows_the_speed_or_the_majority():
    m = _model(False)
    g = 0.16
    fast_p = RW.horizontal(g, RW.eots(g) * 1.3) * W / m.run.re(g)
    slow_p = RW.horizontal(g, RW.pts(g) * 0.5) * W / m.run.re(g)
    # majority gait: walked at 16 % whatever the power
    assert m.walked(g) and m.curve_at(g, fast_p, W) == "walk" and m.curve_at(g, slow_p, W) == "walk"
    on = m.with_speed_gait(True)
    assert on.curve_at(g, fast_p, W) == "run" and on.curve_at(g, slow_p, W) == "walk"
    assert on.re_at(g, slow_p, W) == approx(on.walk.re(g))                      # well below S: the walking curve
    assert on.curve_at(0.0, slow_p, W) == "run"                                 # flats: majority (run)
    assert on.data_n_at(g, fast_p, W) == on.run.data_n(g) and on.data_n_at(g, slow_p, W) == on.walk.data_n(g)


@pytest.mark.parametrize("walk_re", [lambda g: 1 - g * 2, lambda g: 1 - g * 4],
                         ids=["walking cheaper", "walking dearer"])
def test_speed_never_falls_as_power_rises(walk_re):
    """The review's bug: walking RE is above running RE on climbs, so a plain switch at S made
    the speed drop when the power crossed it. v(P) = re_at·P/W must be non-decreasing."""
    m = _model(True, walk_re=walk_re)
    for g in [x / 100 for x in range(3, 40)]:
        ps = np.linspace(20.0, 600.0, 1500)
        v = [m.re_at(g, p, W) * p / W for p in ps]
        assert all(b >= a - 1e-12 for a, b in zip(v, v[1:])), g


def test_walking_is_held_at_the_switch_speed():
    """Between 「walking would be faster than S」 and 「running reaches S」 the speed is S: never 走
    above the switch speed."""
    m = _model(True)
    g = 0.16
    s = _s_h(g)
    p_run_at_s = s * W / m.run.re(g)
    p_walk_at_s = s * W / m.walk.re(g)
    assert p_walk_at_s < p_run_at_s
    for p in np.linspace(p_walk_at_s * 1.001, p_run_at_s * 0.999, 20):
        assert m.re_at(g, p, W) * p / W == approx(s) and m.speed_curve(g, p, W) == "walk"
    for p in np.linspace(10.0, 600.0, 300):
        v = m.re_at(g, p, W) * p / W
        if m.speed_curve(g, p, W) == "walk":
            assert v <= s * (1 + 1e-9)


def test_walking_dearer_than_running_never_slows_a_climb():
    """With personal data saying walking costs more per watt, max(v_run, …) keeps the running curve."""
    m = _model(True, walk_re=lambda g: 1 - g * 4)
    g = 0.16
    for p in np.linspace(20.0, 600.0, 100):
        assert m.re_at(g, p, W) == approx(m.run.re(g)) and m.speed_curve(g, p, W) == "run"


def test_descents_and_flats_keep_the_majority_gait():
    """The walked steep-descent bin stays on the walking curve with the switch on (re_at = re)."""
    m = _model(True)
    g = -0.16
    assert m.walked(g)
    for p in (50.0, 150.0, 400.0):
        assert m.re_at(g, p, W) == m.re(g) and m.speed_curve(g, p, W) == "walk" and m.curve_at(g, p, W) == "walk"
    assert m.re_at(0.0, 150.0, W) == m.re(0.0)


def test_no_walking_data_no_walking_curve():
    """A climb bin without walked windows never walks from the prior alone (as the majority gait)."""
    m = _model(True, walk_from=12)
    g = 0.06
    assert m.walk.data_n(g) == 0
    slow = _s_h(g) * 0.4 * W / m.run.re(g)
    assert m.speed_curve(g, slow, W) == "run" and m.re_at(g, slow, W) == approx(m.run.re(g))
    assert m.trusted_at(g, slow, W) == m.trusted(g)


# ---- the planner ------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["trail_auto", "trail_power", "trail_validated", "trail_time", "road_gpx"])
def test_flag_off_plan_equals_the_frozen_pre_change_output(case):
    """Flag off: the plan main produced before SP-229 (backend/tests/fixtures/sp229_flag_off_plans.json,
    written by main f93d74e0's planner on this fixture)."""
    frozen = json.loads(FROZEN.read_text("utf-8"))[case]
    kind, opts, validated = {
        "trail_auto": ("trail", {"mode": "auto"}, {}),
        "trail_power": ("trail", {"mode": "power", "target_power": 200.0}, {}),
        "trail_validated": ("trail", {"mode": "auto", "effort_target": 0.9}, {"trail": True}),
        "trail_time": ("trail", {"mode": "time", "target_time_s": 8000.0}, {"trail": True}),
        "road_gpx": ("road", {"mode": "auto"}, {}),
    }[case]
    p = _plan(_model(False), kind=kind, validated=validated, **opts)
    assert p["summary"]["time_s"] == approx(frozen["time_s"], rel=1e-9)
    assert p["summary"]["power"] == approx(frozen["power"], rel=1e-9)
    assert len(p["segments"]) == len(frozen["segments"])
    for s, f in zip(p["segments"], frozen["segments"]):
        for k in ("t", "power", "speed_ms"):
            assert s[k] == approx(f[k], rel=1e-9), k
        for k in ("gait", "walk", "notes", "trusted", "badge"):
            assert s[k] == f[k], k


def test_speed_gait_plan_uses_the_predicted_speed():
    m = _model(True)
    p = _plan(m, validated={"trail": True})
    assert p["summary"]["runwalk"]["speed_gait"] is True and p["summary"]["total_method"] == "v2"
    tr = m.for_trail("race")
    seen = set()
    for s in p["segments"]:
        cur = tr.curve_at(s["grade"], s["power"], W)
        assert s["gait_curve"] == cur
        seen.add(cur)
        # the label is the speed shown's (SP-226); 走 never above the switch speed
        assert s["gait"] == RW.gait(s["grade"], s["speed_ms"], m.rw_shift)
        climb = s["grade"] >= RW.MIN_GRADE
        assert (NOTE_SPEED in s["notes"]) == (climb and s["gait"] == "walk")
        if NOTE_SPEED in s["notes"]:
            assert s["speed_ms"] < _s_h(s["grade"]) * (1 + 1e-9)
        # descents / flats: the majority gait and its note, as before
        assert (NOTE_MAJORITY in s["notes"]) == (not climb and cur == "walk")
    assert seen == {"walk", "run"}
    assert any(NOTE_MAJORITY in s["notes"] for s in p["segments"])                # the walked −15 % descent


def test_road_gpx_keeps_the_majority_gait():
    """The gate judged trail segments only: a road GPX plan is the same with the switch on."""
    on, off = _plan(_model(True), kind="road"), _plan(_model(False), kind="road")
    assert on["summary"]["runwalk"]["speed_gait"] is False
    assert on["summary"]["time_s"] == off["summary"]["time_s"]
    assert [s["t"] for s in on["segments"]] == [s["t"] for s in off["segments"]]
    assert [s["notes"] for s in on["segments"]] == [s["notes"] for s in off["segments"]]


@pytest.mark.parametrize("mode,key,values", [
    ("auto", "effort_target", (0.70, 0.80, 0.90, 1.00)),
    ("power", "target_power", (0.45 * CP, 0.60 * CP, 0.75 * CP, 0.90 * CP, 1.05 * CP)),
])
@pytest.mark.parametrize("validated", [{}, {"trail": True}], ids=["v1 total", "validated"])
def test_raising_the_intensity_never_adds_walked_segments(mode, key, values, validated):
    """The ticket's test: the same course, a higher target → never more walked segments."""
    m = _model(True)
    plans = [_plan(m, validated=validated, mode=mode, **{key: v}) for v in values]
    counts = [_walked(p) for p in plans]
    assert all(b <= a for a, b in zip(counts, counts[1:])), counts
    assert counts[0] > counts[-1], counts                       # and the switch does move on this course
    labels = [sum(1 for s in p["segments"] if s["gait"] == "walk") for p in plans]
    assert all(b <= a for a, b in zip(labels, labels[1:])), labels
    # the majority gait walks the same segments at every intensity
    off = [_walked(_plan(_model(False), validated=validated, mode=mode, **{key: v})) for v in values]
    assert len(set(off)) == 1


@pytest.mark.parametrize("validated", [{}, {"trail": True}], ids=["v1 total", "validated"])
def test_finish_time_never_rises_with_effort_or_power(validated):
    """The review's reproduction (auto 0.92 → 0.93, power 254 → 258 W gave a slower finish)."""
    m = _model(True)
    ts = [_plan(m, validated=validated, mode="auto", effort_target=f)["summary"]["time_s"]
          for f in np.arange(0.70, 1.0001, 0.01)]
    assert all(b <= a + 1e-6 for a, b in zip(ts, ts[1:])), ts
    plans = [_plan(m, validated=validated, mode="power", target_power=float(pw)) for pw in range(150, 334, 4)]
    ts = [p["summary"]["time_s"] for p in plans]
    assert all(b <= a + 1e-6 for a, b in zip(ts, ts[1:])), ts
    # every power target in the old jump is reachable (no 「平均功率只能到」)
    for pw, p in zip(range(150, 334, 4), plans):
        assert not any("平均功率只能到" in w for w in p["warnings"]), pw
        if validated:
            assert p["summary"]["power"] == approx(pw, abs=0.5)


@pytest.mark.parametrize("validated", [{}, {"trail": True}], ids=["v1 total", "validated"])
def test_time_and_pace_targets_are_met_with_the_switch_on(validated):
    """目標時間 / 目標配速: a faster target gives a faster finish, reached to the second."""
    m = _model(True)
    km = _course()["totals"]["km"]
    prev = None
    for t in range(9000, 5400, -150):
        p = _plan(m, validated=validated, mode="time", target_time_s=float(t))
        assert p["summary"]["time_s"] == approx(t, abs=1.0)
        assert not any("達不到目標時間" in w for w in p["warnings"]), t
        q = _plan(m, validated=validated, mode="time", target_pace_s_per_km=t / km)
        assert q["summary"]["time_s"] == approx(t, abs=1.0)
        if prev is not None:
            assert p["summary"]["time_s"] < prev
        prev = p["summary"]["time_s"]


def test_personal_shift_moves_the_planner_switch():
    """A curve shifted up (SP-228: this athlete walks at faster speeds) walks at least as many segments."""
    assert _walked(_plan(_model(True, 0.4))) >= _walked(_plan(_model(True, 0.0)))


def test_speed_gait_note_is_translated():
    from backend.i18n import use_locale, _
    with use_locale("en"):
        en = _(NOTE_SPEED)
        old = _(NOTE_MAJORITY)
    assert en != NOTE_SPEED and "walk" in en.lower() and old != NOTE_MAJORITY


# ---- the gate ---------------------------------------------------------------------------

def _rows(n_acts, per_act, err, err_speed, gait="run", gait_speed="walk", extra=()):
    """Back-test rows: per activity `per_act` climbs whose curve changed + `extra` unchanged segments."""
    segs = [{"grade": 0.12, "err": err, "err_speed": err_speed, "gait": gait, "gait_speed": gait_speed}] * per_act
    segs += [{"grade": g, "err": e, "err_speed": e, "gait": "run", "gait_speed": "run"} for g, e in extra]
    return [{"category": "trail", "err_v2": 0.0, "segments": segs} for _ in range(n_acts)]


def test_gate_ties_are_not_kept():
    """No changed climb (or the same error): err_speed == err → the old `<=` passed; now 'tie' / 'few'."""
    none = _rows(5, 0, 0.1, 0.1, extra=[(0.12, 0.05), (0.0, 0.02)] * 10)
    s = BT.speed_gait_summary(none)
    assert s["changed"] == 0 and s["reason"] == "few" and not s["no_worse"]
    tie = BT.speed_gait_summary(_rows(5, 5, 0.08, 0.08))
    assert tie["changed"] == 25 and tie["reason"] == "tie" and not tie["no_worse"]


def test_gate_needs_enough_changed_climbs_and_activities():
    s = BT.speed_gait_summary(_rows(4, 4, 0.10, 0.02))                  # 16 < 20
    assert s["reason"] == "few" and not s["no_worse"] and s["changed_activities"] == 4
    s = BT.speed_gait_summary(_rows(2, 15, 0.10, 0.02))                 # 30 climbs, 2 activities < 3
    assert s["reason"] == "few" and not s["no_worse"]


def test_gate_passes_on_a_clear_improvement_and_fails_on_worse():
    ok = BT.speed_gait_summary(_rows(4, 6, 0.10, 0.02, extra=[(0.0, 0.03)] * 5))
    assert ok["reason"] == "ok" and ok["no_worse"] and ok["changed"] == 24 and ok["changed_activities"] == 4
    assert ok["changed_segments"]["gait"]["median_abs"] == approx(0.10)
    assert ok["changed_segments"]["speed_gait"]["median_abs"] == approx(0.02)
    worse = BT.speed_gait_summary(_rows(4, 6, 0.02, 0.10))
    assert worse["reason"] == "worse" and not worse["no_worse"]


def test_gate_class_model_rows():
    """sfx '_cls' reads the race-class model's own columns."""
    rows = [{"category": "trail", "err_v2": 0.0, "segments": [
        {"grade": 0.12, "err": 0.1, "err_speed": 0.1, "gait": "run", "gait_speed": "run",
         "err_cls": 0.10, "err_speed_cls": 0.02, "gait_cls": "run", "gait_speed_cls": "walk"}] * 7}] * 3
    assert BT.speed_gait_summary(rows)["reason"] == "few"
    c = BT.speed_gait_summary(rows, "_cls")
    assert c["changed"] == 21 and c["no_worse"]


def test_speed_gait_flag_reads_the_stored_back_test(tmp_path):
    p = tmp_path / "bt.json"
    assert BT.speed_gait_flag(p) is False                                       # nothing stored
    for ok in (True, False):
        p.write_text(json.dumps({"version": 2, "terrain": {"speed_gait": {"no_worse": ok}}}), encoding="utf-8")
        assert BT.speed_gait_flag(p) is ok
    p.write_text(json.dumps({"version": 2, "terrain": {}}), encoding="utf-8")
    assert BT.speed_gait_flag(p) is False
    # the race-class model in use: both gates must pass
    for pooled, cls, want in ((True, True, True), (True, False, False), (False, True, False)):
        p.write_text(json.dumps({"terrain": {"speed_gait": {"no_worse": pooled}, "speed_gait_cls": {"no_worse": cls}}}),
                     encoding="utf-8")
        assert BT.speed_gait_flag(p, class_model=True) is want
        assert BT.speed_gait_flag(p) is pooled


@pytest.mark.parametrize("flag,race_model", [(True, False), (False, False), (True, True)])
def test_api_sets_the_switch_from_the_back_test(monkeypatch, flag, race_model):
    """/racepower's grade models carry speed_gait = the stored back-test's gate for the model in use."""
    from backend.api import racepower as RP
    from backend.engine.racepower import athlete as A
    m = _model(False)
    seen = {}

    def fake_flag(path=None, class_model=False):
        seen["class_model"] = class_model
        return flag
    monkeypatch.setattr(RP, "_dataset", lambda: object())
    monkeypatch.setattr(RP._tenancy, "ds_key", lambda ds: ("t", 1))
    monkeypatch.setattr(RP, "inputs", lambda *a, **k: {"re": {"road": {"median": 1.0}}})
    monkeypatch.setattr(A, "grade_models", lambda *a, **k: {"grade_re": m, "hike_speed": GM.fit_hike_speed([])})
    monkeypatch.setattr(A, "solo_hikes", lambda: set())
    monkeypatch.setattr(BT, "class_model_flag", lambda *a: race_model)
    monkeypatch.setattr(BT, "surface_split_flag", lambda *a: False)
    monkeypatch.setattr(BT, "speed_gait_flag", fake_flag)
    monkeypatch.setattr(BT, "load", lambda *a: None)
    RP._cache.pop("grade", None)
    try:
        assert RP._grade_models()["grade_re"].speed_gait is flag
        assert seen["class_model"] is race_model
    finally:
        RP._cache.pop("grade", None)
