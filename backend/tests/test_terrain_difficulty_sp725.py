"""
SP-725: 地形難度 (一般 / 技術路段多 / 需要手腳並用) on the race calculator and on past activities, the
multipliers fitted from the user's own marked runs (engine/terrain_difficulty.py, trailhr.fit_terrain).
Synthetic runs only (no WKO5 folder, no app DB); the 10/18 case is rebuilt from its published shape
(SP-716: 15–25 % slower at the same grade from the first hour on), not from the real record.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest
from pytest import approx

from backend.engine import activity_tags as AT
from backend.engine import terrain_difficulty as TD
from backend.engine.racepower import trailhr as TH


# ---- the mark ------------------------------------------------------------------------

def test_terrain_mark_is_one_tag_and_three_levels():
    assert AT.terrain_of(["x", "技術路段多"]) == "technical"
    t = AT.with_terrain(["x", "技術路段多"], "scramble")
    assert t == ["x", "需要手腳並用"] and AT.terrain_of(t) == "scramble"
    assert AT.with_terrain(t, None) == ["x"] and AT.terrain_of(["x"]) is None
    assert AT.validate(terrain_difficulty="hard") == "INVALID_TERRAIN_DIFFICULTY"
    assert AT.validate(terrain_difficulty="normal") is None
    assert set(AT.TERRAINS.values()) <= AT.MARK_TAGS


def test_upsert_sets_and_clears_the_mark_without_touching_other_tags(tmp_path):
    db = tmp_path / "t.db"
    r = AT.upsert(db, start_local="2026-10-18T07:00", tags=["北插"], poles="with", terrain_difficulty="technical")
    assert AT.terrain_of(r["tags"]) == "technical" and "北插" in r["tags"] and AT.poles_of(r["tags"]) == "with"
    r = AT.upsert(db, start_local="2026-10-18T07:00", terrain_difficulty="scramble")
    assert [t for t in r["tags"] if t in AT.TERRAINS.values()] == ["需要手腳並用"]
    # a tag list typed with two marks keeps the last one
    r = AT.upsert(db, start_local="2026-10-18T07:00", tags=["地形一般", "技術路段多"])
    assert AT.terrain_of(r["tags"]) == "technical" and "地形一般" not in r["tags"]
    r = AT.upsert(db, start_local="2026-10-18T07:00", terrain_difficulty=None)
    assert AT.terrain_of(r["tags"]) is None
    with pytest.raises(ValueError):
        AT.upsert(db, start_local="2026-10-18T07:00", terrain_difficulty="extreme")
    assert AT.merge({}, {"tags_json": '["技術路段多"]'})["terrain_difficulty"] == "technical"


# ---- synthetic runs from a known model ------------------------------------------------

A_, B_ = 2.0, 6.0          # v₀(x) = 2 + 6x effort-km/h


def _run(x, T_h, mult=1.0, delta=0.05, noise=0.0, rnd=None, idx=None, level=None):
    """A run of T_h moving hours at HR level x on terrain needing `mult` × the effort km: it covers
    E = v₀(x)·dist_nodecay(T) / mult effort km of the course."""
    v0 = A_ + B_ * x
    e = v0 * TH.dist_nodecay(T_h, delta) / mult
    if noise and rnd:
        e *= math.exp(rnd.gauss(0.0, noise))
    p = {"x": x, "x_raw": x, "v": e / T_h, "T_h": T_h, "eff_km": e, "idx": idx, "date": f"2026-01-{(idx or 0) % 28 + 1:02d}"}
    if level:
        p["terrain"] = level
    return p


def _population(n_plain=24, tech=(), scr=(), noise=0.03, seed=5):
    rnd = random.Random(seed)
    pts, i = [], 0
    for _ in range(n_plain):
        pts.append(_run(rnd.uniform(0.8, 1.05), rnd.uniform(1.0, 4.0), noise=noise, rnd=rnd, idx=i))
        i += 1
    for m in tech:
        pts.append(_run(rnd.uniform(0.85, 1.0), rnd.uniform(2.0, 5.0), mult=m, noise=noise, rnd=rnd, idx=i,
                        level="technical"))
        i += 1
    for m in scr:
        pts.append(_run(rnd.uniform(0.85, 1.0), rnd.uniform(2.0, 5.0), mult=m, noise=noise, rnd=rnd, idx=i,
                        level="scramble"))
        i += 1
    return pts


def test_no_marks_every_level_is_its_prior_labelled_estimate():
    f = TH.fit_terrain(_population(), 0.05)
    assert f["mult"] == {"normal": 1.0, "technical": TD.PRIOR["technical"], "scramble": TD.PRIOR["scramble"]}
    assert f["levels"]["technical"]["label"] == "推估" and f["levels"]["technical"]["n"] == 0
    assert f["levels"]["scramble"]["label"] == "推估"


def test_marked_runs_move_their_level_toward_the_data_by_n_over_n_plus_k():
    pts = _population(tech=[1.30] * 6, noise=0.0)
    f = TH.fit_terrain(pts, 0.05)
    lv = f["levels"]["technical"]
    assert lv["n"] == 6 and lv["label"] == "你的紀錄"
    assert lv["raw"] == approx(1.30, abs=0.02)                    # each run's own ratio
    want = (6 * 1.30 + TD.SHRINK_K * TD.PRIOR["technical"]) / (6 + TD.SHRINK_K)
    assert lv["mult"] == approx(want, abs=0.02)
    # the level nobody marked keeps its prior
    assert f["levels"]["scramble"] == {**f["levels"]["scramble"], "mult": TD.PRIOR["scramble"], "label": "推估"}
    assert f["mult"]["normal"] == 1.0


def test_the_v0_fit_puts_marked_runs_back_on_normal_terrain():
    pts = _population(tech=[1.4] * 8, noise=0.0)
    plain = TH.fit([{**p, "terrain_mult": 1.0} for p in pts], 0.05)
    mult = TH.fit_terrain(pts, 0.05)["mult"]
    back = TH.fit([{**p, "terrain_mult": mult.get(p.get("terrain") or "normal", 1.0)} for p in pts], 0.05)
    # the marked runs drag a plain fit's speed down; put back, the fit is close to the true line
    assert TH.v0_at(plain, 0.95) < TH.v0_at(back, 0.95)
    assert TH.v0_at(back, 0.95) == approx(A_ + B_ * 0.95, rel=0.03)


# ---- acceptance 3: leave-one-out, the marked runs' whole moving time ---------------------

def test_leave_one_out_marked_runs_are_closer_with_the_multiplier():
    rnd = random.Random(11)
    pts = _population(tech=[rnd.uniform(1.15, 1.30) for _ in range(6)],
                      scr=[rnd.uniform(1.30, 1.50) for _ in range(4)], noise=0.04, seed=11)
    r = TH.terrain_loo(pts, 0.05)
    assert r["n"] == 10
    assert r["median_abs_terrain"] < r["median_abs_plain"]
    assert r["median_abs_terrain"] < 0.08 < r["median_abs_plain"]
    # every marked run was underestimated without terrain (the model thought it was a normal trail)
    assert all(x["err_plain"] < 0 for x in r["rows"])


def test_the_1018_case_from_its_shape():
    """10/18 北插天山 (SP-716): 5.2 h moving, 15–25 % slower than the athlete's usual trails at the same
    grade from the first hour on (the scramble 40 %): ≈ 1.22 × the effort km. Marked 技術路段多, with two
    earlier technical runs of the athlete — predicted without itself, it lands much closer."""
    pts = _population(tech=[1.20, 1.25], noise=0.02, seed=3)
    day = _run(0.97, 5.2, mult=1.22, idx=99, level="technical")
    r = TH.terrain_loo(pts + [day], 0.05)
    row = next(x for x in r["rows"] if x["idx"] == 99)
    assert row["err_plain"] < -0.12                                # the plain model: far too fast
    assert abs(row["err_terrain"]) < abs(row["err_plain"]) / 2
    # with no technical run marked before it, the prior alone (1.15) still helps
    r0 = TH.terrain_loo(_population(noise=0.02, seed=3) + [day], 0.05)
    row0 = r0["rows"][0]
    assert row0["mult"] == TD.PRIOR["technical"] and abs(row0["err_terrain"]) < abs(row0["err_plain"])


def test_backtest_summary_reports_marked_cases_with_and_without_terrain():
    from backend.engine.racepower import backtest as BT
    rows = [{"category": "trail", "th": {"moving_s": 1}, "terrain": "technical", "err_th_given": -0.20, "err_th_given_terrain": -0.04,
             "err_th_race": -0.18, "err_th_race_terrain": -0.03},
            {"category": "trail", "th": {"moving_s": 1}, "terrain": "scramble", "err_th_given": -0.30, "err_th_given_terrain": 0.05,
             "err_th_race": -0.25, "err_th_race_terrain": 0.02},
            {"category": "trail", "th": {"moving_s": 1}, "terrain": None, "err_th_given": 0.02, "err_th_given_terrain": 0.02}]
    s = BT.summarise_terrain_difficulty(rows)
    assert s["n"] == 2 and s["by_level"] == {"technical": 1, "scramble": 1}
    assert s["given_terrain"]["median_abs"] < s["given"]["median_abs"] and s["better"] is True
    assert BT.summarise_trail_hr(rows)["terrain"]["n"] == 2
    assert BT.summarise_terrain_difficulty([])["better"] is None


def test_evaluate_trail_hr_scores_the_case_with_its_level():
    from backend.engine.racepower import backtest as BT
    pts = _population(tech=[1.25] * 4, noise=0.0)
    m = TH.fit(pts, 0.05)
    m["terrain"] = TH.fit_terrain(pts, 0.05)
    m["xstar"] = TH.fit_xstar([])
    case = {"trail_pt": _run(0.95, 3.0, mult=1.25, level="technical"), "terrain": "technical"}
    r = BT.evaluate_trail_hr(case, {"trail_hr": m})
    assert r["terrain"] == "technical" and r["terrain_mult"] > 1.15
    assert abs(r["err_th_given_terrain"]) < abs(r["err_th_given"])
    plain = BT.evaluate_trail_hr({**case, "terrain": None}, {"trail_hr": m})
    assert plain["err_th_given_terrain"] == plain["err_th_given"]


# ---- acceptance 1: the calculator ----------------------------------------------------

def _model(terrain=None):
    from backend.engine.racepower import trailhr as TH_
    pts = [{"x": x, "v": 2 + 4 * x, "T_h": 2.0, "eff_km": 10.0} for x in np.linspace(0.8, 1.2, 8)]
    m = TH_.fit(pts, 0.05)
    m["xstar"] = TH_.fit_xstar([])
    if terrain:
        m["terrain"] = terrain
    return m


def test_trail_estimate_scales_the_effort_km_by_the_level():
    from backend.engine.racepower import planner as PL
    m = _model()
    base = PL.trail_hr_estimate(m, 20.0, 1500.0, 1.0)
    same = PL.trail_hr_estimate(m, 20.0, 1500.0, 1.0, terrain="normal")
    tech = PL.trail_hr_estimate(m, 20.0, 1500.0, 1.0, terrain="technical")
    scr = PL.trail_hr_estimate(m, 20.0, 1500.0, 1.0, terrain="scramble")
    assert same["time_s"] == approx(base["time_s"]) and base["terrain"]["level"] == "normal"
    assert tech["eff_km"] == approx(base["eff_km"] * 1.15) and tech["eff_km_course"] == approx(base["eff_km"])
    assert base["time_s"] < tech["time_s"] < scr["time_s"]
    # a model stored before SP-725 (no fitted multipliers): the priors, labelled 推估
    assert tech["terrain"]["label"] == "推估" and tech["terrain"]["mult"] == TD.PRIOR["technical"]
    assert set(tech["terrain"]["levels"]) == {"normal", "technical", "scramble"}
    # the user's fitted multiplier and its count reach the plan
    fitted = {"mult": {"normal": 1.0, "technical": 1.22, "scramble": 1.35},
              "levels": {"normal": {"mult": 1.0, "n": 0, "label": "定義"},
                         "technical": {"mult": 1.22, "n": 3, "label": "你的紀錄"},
                         "scramble": {"mult": 1.35, "n": 0, "label": "推估"}}}
    t2 = PL.trail_hr_estimate(_model(fitted), 20.0, 1500.0, 1.0, terrain="technical")
    assert t2["terrain"]["mult"] == 1.22 and t2["terrain"]["n"] == 3 and t2["terrain"]["label"] == "你的紀錄"
    assert "你標過的 3 筆" in PL.terrain_text(t2) and PL.terrain_text(base) == ""


def test_planin_takes_the_three_levels_default_normal():
    from pydantic import ValidationError
    from backend.engine.racepower.calc import PlanIn
    assert PlanIn(type="trail").terrain_difficulty is None
    assert PlanIn(type="trail", terrain_difficulty="scramble").terrain_difficulty == "scramble"
    with pytest.raises(ValidationError):
        PlanIn(type="trail", terrain_difficulty="hard")


def _gpx_of(tr) -> bytes:
    pts = "".join(f'<trkpt lat="{a:.7f}" lon="{b:.7f}"><ele>{z:.1f}</ele></trkpt>' for a, b, z in zip(tr.lat, tr.lon, tr.ele))
    return f'<?xml version="1.0"?><gpx version="1.1"><trk><trkseg>{pts}</trkseg></trk></gpx>'.encode()


def test_plan_segments_and_the_cairn_curve_follow_the_terrain():
    """The segment times share the HR model's total (now × the level), so the 時間曲線 pushed to Cairn
    (its cumulative moving time) grows with it — nothing in cairn_curve changes."""
    from backend.engine.racepower import cairn_curve as CC
    from backend.engine.racepower import course as CO
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import planner as PL
    from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track
    tr = synthetic_track({"len": 20000, "z": lambda x: 300 + 200 * math.sin(x / 3000.0)})
    c = CO.build_course(tr)
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9}

    def plan(level):
        return PL.plan_run(v1=fake_v1("trail", 20.0, c["totals"]["gain_m"]), course=c, grade_re=GM.GradeRE(RE0),
                           effort_validated=False, opts={"mode": "auto", "terrain_difficulty": level},
                           validated={"trail": True}, trail_hr=m)
    p0, p1 = plan(None), plan("technical")
    assert p1["summary"]["time_s"] > p0["summary"]["time_s"] * 1.10
    assert sum(s["t"] for s in p1["segments"]) == approx(p1["summary"]["time_s"], abs=1.0)
    assert any("地形難度" in w for w in p1["warnings"]) and not any("地形難度" in w for w in p0["warnings"])
    gpx = _gpx_of(tr)
    c0 = CC.build(p0, gpx, start_times=[None])
    c1 = CC.build(p1, gpx, start_times=[None])
    assert c1["points"][-1][1] == approx(p1["summary"]["time_s"], abs=1.0)
    assert c1["points"][-1][1] > c0["points"][-1][1] * 1.10


# ---- acceptance 2: past activities (the user's mark, else the route's default) feed the model ----

def _ds(n=4):
    import datetime as dt
    from types import SimpleNamespace as NS
    t0 = dt.datetime(2026, 9, 1, 7, 0)
    ws = [NS(idx=i, entry=NS(start=t0 + dt.timedelta(days=i), file=f"2026/{i}.fit")) for i in range(n)]
    return NS(workouts=ws, memo={})


def test_levels_user_mark_wins_over_the_route_default():
    ds = _ds()
    tags = [{"start_local": "2026-09-01T07:00", "file": "2026/0.fit", "tags_json": '["需要手腳並用"]'},
            {"start_local": "2026-09-02T07:00", "file": "2026/1.fit", "tags_json": '["地形一般"]'}]
    index = {"routes": [{"id": "r1", "members": ["2026/1.fit", "2026/2.fit"],
                         "efforts": [{"file": "2026/1.fit", "start": "2026-09-02T07:00:00"},
                                     {"file": "x/other.fit", "start": "2026-09-04T07:01:00"}]}]}
    lv = TD.levels(ds, tags, index=index, defaults={"r1": "technical"})
    assert lv[0] == {"level": "scramble", "src": "user"}
    assert lv[1] == {"level": "normal", "src": "user"}             # the user's own 一般 beats the route
    assert lv[2] == {"level": "technical", "src": "route"}
    assert lv[3] == {"level": "technical", "src": "route"}         # the same trip from another source, by start
    assert TD.levels(_ds(), [], index=index, defaults={}) == {}


def test_trail_hr_model_fits_the_marked_runs(monkeypatch, tmp_path):
    import datetime as dt
    from types import SimpleNamespace as NS
    from backend.engine.racepower import athlete as A
    monkeypatch.setattr(A, "SOLO_HIKES", str(tmp_path / "none.json"))
    rnd = random.Random(2)
    # all under 2 h (no long-run durability rows to look up in this fake dataset)
    pts = [_run(rnd.uniform(0.8, 1.05), rnd.uniform(1.0, 1.9), idx=i) for i in range(20)]
    pts += [_run(rnd.uniform(0.85, 1.0), rnd.uniform(1.0, 1.9), mult=1.3, idx=20 + i, level="technical") for i in range(5)]
    marks ={p["idx"]: {"level": p.pop("terrain"), "src": "user"} for p in pts if p.get("terrain")}
    monkeypatch.setattr(A, "trail_hr_points", lambda ds, runs, exclude=None, heat=True: [dict(p) for p in pts] if heat else [])
    monkeypatch.setattr(A, "inside_acts", lambda ds, today, exclude=None, tags=None: [])
    monkeypatch.setattr(TD, "levels", lambda ds, tags=None, **kw: marks)
    m = A.trail_hr_model(NS(workouts=[], flush_series=lambda: None), dt.date(2026, 10, 8), race_idx=set(), tags=[])
    t = m["terrain"]["levels"]["technical"]
    assert t["n"] == 5 and t["label"] == "你的紀錄" and t["mult"] > 1.2
    marked = [p for p in m["points"] if p.get("terrain") == "technical"]
    assert len(marked) == 5 and all(p["terrain_src"] == "user" and p["terrain_mult"] == t["mult"] for p in marked)
    assert TH.v0_at(m, 0.95) == approx(A_ + B_ * 0.95, rel=0.05)    # the marked runs did not drag v₀ down
