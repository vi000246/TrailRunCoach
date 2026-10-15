"""
SP-544: the trail technicality only on the dirt share of the course (engine/racepower/surface.py
apply_tech_dirt, pacing.course_time's re_mult, planner / calc behind TRC_TECH_DIRT_ONLY) and the
back-test's comparison (backtest.GROUND_OF, tech_dirt_summary).
"""
import pytest

from backend.engine import course_surface as CS
from backend.engine.racepower import backtest as BT
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import pacing as PC
from backend.engine.racepower import planner as PL
from backend.engine.racepower import surface as SF


def _gaitre(f: float = 0.80):
    """A trail model whose technicality is f in every bin (no dry / wet split)."""
    g = GM.fit_gait_re([], 1.0)
    g.tech_bins = {lab: {"f": f, "raw": f, "n": 50} for lab in GM.TECH_BIN_LABELS}
    return g


def _eff(*parts):
    """parts = (a_km, b_km, OSM class)."""
    return SF.effective({"segs": [{"a": a, "b": b, "c": c} for a, b, c in parts]})


def test_re_mult_keeps_the_factor_only_on_the_dirt_and_unsure_share():
    g = _gaitre(0.8).for_trail("race")
    segs = [{"grade": 0.0, "start_km": 0.0, "end_km": 2.0, "dist_m": 2000.0},
            {"grade": -0.05, "start_km": 2.0, "end_km": 4.0, "dist_m": 2000.0},
            {"grade": 0.10, "start_km": 4.0, "end_km": 6.0, "dist_m": 2000.0}]
    eff = _eff((0, 1, "paved"), (1, 2, "dirt"), (2, 3, "unknown"), (3, 6, "steps"))
    note = SF.apply_tech_dirt(segs, g, eff)
    # half man-made: RE × (1 + 0.5 (0.8 − 1)) / 0.8 = 0.9 / 0.8 → RE_without · 0.9
    assert segs[0]["re_mult"] == pytest.approx(0.9 / 0.8)
    assert segs[1]["re_mult"] == pytest.approx(0.9 / 0.8)        # half unsure (kept) + half steps
    assert "re_mult" not in segs[2]                              # a climb > +2 %: no technicality anyway
    assert note == {"dirt": 25, "made": 50, "unsure": 25, "applied": 50, "km": 4.0, "manual": False}
    all_dirt = [dict(segs[0])]
    SF.apply_tech_dirt(all_dirt, g, _eff((0, 6, "dirt")))
    assert all_dirt[0]["re_mult"] == pytest.approx(1.0)          # all dirt: exactly as before
    assert SF.apply_tech_dirt([dict(segs[0])], _gaitre(0.8), eff) is None     # not a trail model: nothing


def test_course_time_uses_the_segment_multiplier():
    model = PC.RunModel(60.0, lambda g: 1.0)
    segs = [{"grade": 0.0, "dist_m": 1000.0, "M": 1.0}]
    t0 = PC.course_time(200.0, segs, model)["T"]
    t1 = PC.course_time(200.0, [{**segs[0], "re_mult": 1.25}], model)["T"]
    assert t1 == pytest.approx(t0 / 1.25)


def test_planner_runs_the_man_made_part_without_the_technicality():
    from backend.tests.test_racepower_v2 import fake_v1, synthetic_track
    tr = synthetic_track({"len": 12000, "z": lambda x: 200 + (x * 0.01 if x < 6000 else (12000 - x) * 0.01)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 12.0, c["totals"]["gain_m"])
    g = _gaitre(0.8)

    def plan(ground):
        return PL.plan_run(v1=v1, course=c, grade_re=g, opts={"mode": "power", "target_power": 250, "ground": ground},
                           validated={"trail": True}, effort_validated=False)["summary"]
    base = plan(None)
    assert base["tech_ground"] is None
    km = c["totals"]["km"]
    assert plan(_eff((0, km, "dirt")))["time_s"] == pytest.approx(base["time_s"])
    assert plan(_eff((0, km, "unknown")))["time_s"] == pytest.approx(base["time_s"])      # unsure: as before
    half = plan(_eff((0, km / 2, "paved"), (km / 2, km, "dirt")))
    assert half["time_s"] < base["time_s"] and half["tech_ground"]["made"] == pytest.approx(50, abs=1)
    road = PL.plan_run(v1=fake_v1("road", 12.0, c["totals"]["gain_m"]), course=c, grade_re=g,
                       opts={"mode": "power", "target_power": 250, "ground": _eff((0, km, "paved"))},
                       validated={"road": True}, effort_validated=False)["summary"]
    assert road["tech_ground"] is None                                                   # trail only


def test_calc_reads_the_stored_surface_only_with_the_switch(tmp_path, monkeypatch):
    from backend.engine.racepower import calc as CALC
    cid = "b" * 40
    monkeypatch.setattr(CS, "_default_db", lambda: tmp_path / "app.db")
    CS.save(cid, profile={"segs": [{"a": 0, "b": 1, "c": "paved"}, {"a": 1, "b": 2, "c": "unknown"}]})
    CS.save(cid, overrides={"1": "dirt"})
    body = CALC.PlanIn(type="trail", course=CALC.CourseRef(course_id=cid))
    monkeypatch.delenv(SF.TECH_ENV, raising=False)
    assert CALC.course_ground(None, body) is None
    monkeypatch.setenv(SF.TECH_ENV, "1")
    eff = CALC.course_ground(None, body)
    assert eff["pct"] == {"dirt": 50, "made": 50, "unsure": 0}
    assert CALC.course_ground(None, CALC.PlanIn(type="trail", course=CALC.CourseRef(course_id="c" * 40))) is None


def test_backtest_summary_compares_before_and_after():
    rows = [{"category": "trail", "intensity": "race", "date": "2026-05-01", "label": "a", "err_v2": -0.10,
             "err_v2_ground": -0.06, "cap_sample": True, "err_c": 0.08, "err_c_ground": 0.05},
            {"category": "trail", "intensity": "race", "date": "2026-06-01", "label": "b", "err_v2": 0.04,
             "err_v2_ground": 0.05},
            {"category": "trail", "intensity": "easy", "err_v2": 0.2},          # not compared
            {"category": "road", "err_v2": 0.01, "err_v2_ground": 0.0}]
    s = BT.tech_dirt_summary(rows)
    assert s["cases"] == 2 and s["race_like"] == 2
    assert s["race"]["before"]["median_abs"] == pytest.approx(0.07)
    assert s["race"]["after"]["median_abs"] == pytest.approx(0.055)
    assert s["capacity"]["after"]["n"] == 1 and s["no_worse"] is True
    rows[0]["err_v2_ground"] = -0.20
    assert BT.tech_dirt_summary(rows)["no_worse"] is False
    assert BT.tech_dirt_summary([{"category": "trail", "err_v2": 0.1}]) is None
    assert BT.GROUND_OF is None                                                 # off unless a run sets it
