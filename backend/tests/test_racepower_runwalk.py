"""
賽事計算機: walk or run from grade × speed (engine/racepower/runwalk.py, SP-226;
docs/research/run-walk-threshold.md §3.1, §5.1, §5.3).
"""
from __future__ import annotations

import math

import pytest

from backend.engine.racepower import fuel as FU
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import runwalk as RW
from backend.engine.racepower import seg_targets as ST
from backend.tests.test_racepower_export import client  # noqa: F401  (fixture)
from backend.tests.test_racepower_v2 import synthetic_track

approx = pytest.approx


def g_of(deg: float) -> float:
    return math.tan(math.radians(deg))


@pytest.mark.parametrize("deg,p,e", [(0, 1.95, 2.14), (5, 1.78, 1.99), (10, 1.62, 1.78), (15, 1.47, 1.51)])
def test_brill_kram_2021_eight_numbers(deg, p, e):
    """The four grades' preferred and energetically optimal speeds (m/s, belt), ≤ 0.05 m/s."""
    assert RW.pts(g_of(deg)) == approx(p, abs=0.05)
    assert RW.eots(g_of(deg)) == approx(e, abs=0.05)
    assert RW.pts(g_of(deg)) <= RW.eots(g_of(deg))


def test_finiel_2026_and_ortiz_2017():
    """Finiel: the preferred transition at 7.2° is 1.76 m/s; Ortiz: walk and run cost the same at 0.8 m/s on 30°."""
    assert RW.pts(g_of(7.2)) == approx(1.76, abs=0.05)
    assert RW.pts(g_of(30)) == approx(0.80, abs=0.05) and RW.eots(g_of(30)) == approx(0.80, abs=0.05)
    # beyond 30°: the same vertical speed (0.4 m/s = 1,440 m/h)
    assert RW.pts(g_of(40)) * math.sin(math.radians(40)) == approx(0.40, abs=1e-9)


def test_the_curves_fall_with_the_grade():
    gs = [x / 100 for x in range(3, 120)]
    for a, b in zip(gs, gs[1:]):
        assert RW.pts(b) <= RW.pts(a) + 1e-12 and RW.eots(b) <= RW.eots(a) + 1e-12


@pytest.mark.parametrize("vam,pts_pct,eots_pct", [(500, 8.0, 7.0), (700, 11.0, 10.0), (900, 15.0, 13.5), (1400, 28.0, 26.5)])
def test_research_table_climbing_speed_to_grade(vam, pts_pct, eots_pct):
    """§3.2: at this climbing rate, walking pays from about this grade on (± 1 %)."""
    gs = [x / 1000 for x in range(30, 600)]
    walk_from = next(g for g in gs if RW.gait(g, vam / 3600 / g) == "walk")
    either_from = next(g for g in gs if RW.gait(g, vam / 3600 / g) != "run")
    assert walk_from * 100 == approx(pts_pct, abs=1.0)
    assert either_from * 100 == approx(eots_pct, abs=1.0)


def test_three_answers_and_only_on_climbs():
    g = g_of(10)
    h = lambda belt: RW.horizontal(g, belt)            # noqa: E731
    assert RW.gait(g, h(1.50)) == "walk"
    assert RW.gait(g, h(1.70)) == "either"
    assert RW.gait(g, h(1.90)) == "run"
    # 15–30°: the band narrows to nothing at 30°
    assert RW.gait(g_of(30), RW.horizontal(g_of(30), 0.79)) == "walk"
    assert RW.gait(g_of(30), RW.horizontal(g_of(30), 0.81)) == "run"
    assert RW.gait(0.029, 0.5) is None and RW.gait(-0.10, 0.5) is None and RW.gait(0.0, 0.5) is None
    assert RW.gait(0.10, None) is None and RW.gait(0.10, 0.0) is None and RW.gait(None, 1.0) is None
    assert RW.belt_speed(0.2, RW.horizontal(0.2, 1.3)) == approx(1.3)


def test_shift_moves_both_curves():
    g = g_of(10)
    assert RW.pts(g, 0.2) == approx(RW.pts(g) + 0.2) and RW.eots(g, -0.2) == approx(RW.eots(g) - 0.2)
    v = RW.horizontal(g, 1.70)
    assert RW.gait(g, v) == "either" and RW.gait(g, v, shift=0.2) == "walk" and RW.gait(g, v, shift=-0.2) == "run"
    assert RW.pts(g_of(60), -5.0) == RW.MIN_SPEED


def test_labels():
    assert [RW.label(x) for x in ("walk", "either", "run", None)] == ["走", "走跑皆可", "跑", None]
    assert [RW.walk_label(x) for x in ("walk", "either", "run", None)] == ["走", "走跑皆可", None, None]


def seg(g, **kw):
    return {"i": 1, "grade": g, "t": 1200.0, "gain_m": max(0.0, g) * 2000, "loss_m": 0.0,
            "power": 250.0, "pace_s_per_km": 600.0, "dist_m": 2000.0, **kw}


def test_steep_climb_run_keeps_the_hr_target_and_says_run():
    """> 8 % run by the function: 陡坡（跑）, HR cap + VAM, no power target (SP-226)."""
    segs = [seg(0.12, i=1, gait="run"), seg(0.12, i=2, gait="walk"), seg(0.12, i=3, gait="either"),
            seg(0.05, i=4, gait="walk"), seg(0.05, i=5, gait="either"), seg(0.05, i=6, gait="run")]
    plan = {"type": "trail", "segments": segs, "used": {"cp": {"value": 300.0}}, "summary": {"time_s": 7200.0}}
    t = ST.plan_targets(plan, aet=145.0, lthr=165.0)
    assert [x["label"] for x in t] == ["陡坡（跑）", "陡坡（走）", "陡坡（走跑皆可）", "爬坡（走）", "可跑的爬坡", "可跑的爬坡"]
    assert [x["kind"] for x in t] == ["steep_climb"] * 4 + ["run_climb"] * 2
    run = t[0]
    assert run["basis"] == "hr" and run["chips"][0]["text"] == "心率 ≤ 165" and "VAM" in run["text"] and "W" not in run["text"]
    assert t[4]["basis"] == "power"
    rows = ST.chart_rows(plan, aet=145.0, lthr=165.0)
    assert [r["walk"] for r in rows] == [False, True, False, True, False, False]
    assert [r["gait"] for r in rows] == ["run", "walk", "either", "walk", "either", "run"]
    assert rows[0]["power"] is None and rows[0]["power_ref"] == 250.0 and rows[0]["label"] == "陡坡（跑）"


def test_no_gait_keeps_the_grade_rule():
    """百岳 and manual courses carry no gait: a steep climb is walked, as before."""
    plan = {"type": "baiyue", "segments": [seg(0.12, i=1), seg(0.05, i=2)], "summary": {"hr_cap": 140.0}}
    t = ST.plan_targets(plan, aet=140.0)
    assert [x["label"] for x in t] == ["陡坡（走）", "可跑的爬坡"]
    assert [r["walk"] for r in ST.chart_rows(plan, aet=140.0)] == [True, False]


def test_fuel_walking_cost_only_when_walked():
    base = {"grade": 0.12, "power": 255.0, "t": 900.0, "dist_m": 1500.0}
    e = FU.run_energy([dict(base, gait="walk"), dict(base, gait="either"), dict(base, gait="run"),
                       dict(base, grade=0.06, gait="either"), dict(base, grade=0.06, gait="walk")], 65.0, 2.0)
    assert [x["method"] for x in e] == ["minetti_walk", "minetti", "minetti", "power", "minetti_walk"]
    assert e[0]["kcal"] < e[1]["kcal"]                 # walking costs less on the same climb (Minetti 2002)


def _plan(client, monkeypatch=None, gait=None):   # noqa: F811
    tr = synthetic_track({"len": 16000, "z": lambda x: 300 + (x * 0.14 if x < 8000 else (16000 - x) * 0.14)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    if gait is not None:
        monkeypatch.setattr(RW, "gait", lambda g, v, shift=0.0: gait if g >= RW.MIN_GRADE else None)
    return client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": cid}}).json()


def test_plan_labels_follow_the_predicted_speed(client):   # noqa: F811
    p = _plan(client)
    climbs = [s for s in p["segments"] if s["grade"] >= 0.03]
    assert climbs
    for s in climbs:
        assert s["gait"] == RW.gait(s["grade"], s["speed_ms"])
        assert s["walk"] == RW.walk_label(s["gait"])
        assert (s["walk"] in s["notes"]) if s["walk"] else not any(n in ("走", "走跑皆可") for n in s["notes"])
    assert all(s["gait"] is None for s in p["segments"] if s["grade"] < 0.03)


def test_the_labels_never_change_the_time(client, monkeypatch):   # noqa: F811
    """SP-226 changes labels, targets and food only: the predicted times are the same whatever the gait."""
    walk = _plan(client, monkeypatch, "walk")
    run = _plan(client, monkeypatch, "run")
    assert walk["summary"]["time_s"] == approx(run["summary"]["time_s"], rel=1e-12)
    assert [s["t"] for s in walk["segments"]] == approx([s["t"] for s in run["segments"]], rel=1e-12)
    assert {s["gait"] for s in walk["segments"] if s["grade"] >= 0.03} == {"walk"}
    # the food does follow the gait: walking the climbs costs less
    assert walk["fuel"]["kcal"] < run["fuel"]["kcal"]
    assert walk["fuel"]["methods"].get("minetti_walk") and not run["fuel"]["methods"].get("minetti_walk")


# ---- SP-230: is 「步頻 < 130 spm 算走」 right? -------------------------------------------

import datetime as dt                                    # noqa: E402
from types import SimpleNamespace                        # noqa: E402

import numpy as np                                       # noqa: E402

from backend.engine.racepower import athlete as A        # noqa: E402

MIDS = (RW.cad_edges()[:-1] + RW.cad_edges()[1:]) / 2


def mix(walk, run, sd=6.0, walk_share=0.4, total_s=3 * 3600.0):
    """Seconds per bin of a walking and a running group of climbing cadence."""
    d = walk_share * np.exp(-0.5 * ((MIDS - walk) / sd) ** 2) + (1 - walk_share) * np.exp(-0.5 * ((MIDS - run) / sd) ** 2)
    return list(d / d.sum() * total_s)


def track(sections, dt_s=1.0):
    """Sample arrays from [(metres, grade, strides/min, m/s)] sections."""
    t, d, z, cad = [0.0], [0.0], [100.0], [sections[0][2]]
    for metres, g, c, v in sections:
        for _ in range(int(metres / (v * dt_s))):
            t.append(t[-1] + dt_s)
            d.append(d[-1] + v * dt_s)
            z.append(z[-1] + v * dt_s * g)
            cad.append(c)
    return {k: np.array(x, float) for k, x in (("t", t), ("d", d), ("z", z), ("cad", cad))}


def test_climb_cadence_histogram_counts_only_climbs():
    """Seconds per 5-spm bin of moving time on ≥ 3 % windows; the cadence channel is
    strides/min (spm = × 2); flats and descents stay out."""
    a = track([(1000, 0.0, 85, 3.0), (1000, 0.10, 55, 1.0), (1000, 0.10, 82, 2.0), (1000, -0.10, 88, 3.5)])
    h = RW.climb_cadence_hist(a["t"], a["d"], a["z"], a["cad"], np.ones(len(a["t"]), bool))
    s = dict(zip(MIDS, h))
    assert s[112.5] == pytest.approx(1000, rel=0.12)          # 55 × 2 = 110 spm, walked at 1 m/s
    assert s[162.5] == pytest.approx(500, rel=0.12)           # 82 × 2 = 164 spm, run at 2 m/s
    assert sum(h) == pytest.approx(1500, rel=0.12)            # the flat 170 spm and the descent 176 spm: none
    assert RW.climb_cadence_hist(a["t"], a["d"], a["z"], None, np.ones(len(a["t"]), bool)) is None


@pytest.mark.parametrize("walk,run,verdict,word", [(110, 165, "ok", "門檻合用"), (100, 140, "ok", "門檻合用"),
                                                   (140, 172, "off", "快走會被算成跑步"),
                                                   (95, 128, "off", "慢跑會被算成走路")])
def test_two_groups_and_where_the_valley_is(walk, run, verdict, word):
    """130 in the valley (or on its floor): fits; the valley above 130: fast hiking counts as
    running; below: slow running counts as walking."""
    c = RW.cadence_check(mix(walk, run, sd=5.0))
    assert c["bimodal"] and c["verdict"] == verdict and word in c["hint"]
    assert c["walk_peak_spm"] == pytest.approx(walk, abs=5) and c["run_peak_spm"] == pytest.approx(run, abs=5)
    assert c["walk_peak_spm"] < c["valley_spm"] < c["run_peak_spm"]
    assert c["below_share"] == pytest.approx(sum(x for m, x in zip(MIDS, mix(walk, run, sd=5.0)) if m < 130) / (3 * 3600))


def test_one_group_or_too_little():
    one = RW.cadence_check(mix(150, 150))
    assert one["bimodal"] is False and one["verdict"] == "unimodal" and "只有一群" in one["hint"]
    few = RW.cadence_check(mix(110, 165, total_s=600.0))
    assert few["enough"] is False and few["verdict"] == "few" and few["bimodal"] is None
    assert RW.cadence_check(None)["verdict"] == "few"


def test_the_check_never_moves_the_line():
    """先不自動改門檻: the 130 spm everywhere stays."""
    from backend.engine import workout_review as WR
    from backend.engine.racepower import intensity as I
    RW.cadence_check(mix(140, 172, sd=5.0))
    assert WR.RUN_CADENCE == 65.0 and I.INTENSITY["run_cadence"] == 65.0 and RW.THRESHOLD_SPM == 130.0


def _ds(arrays):
    """A Dataset stand-in: two outdoor runs with the given arrays, one treadmill run, one hike."""
    def w(idx, sport="run", sport_type="trail running", tags=()):
        return SimpleNamespace(idx=idx, sport=sport, sport_type=sport_type, tags=list(tags), day=0,
                               entry=SimpleNamespace(file=f"{idx}.fit", start=dt.datetime(2026, 9, 1)))
    ws = [w(1), w(2), w(3, tags=("runningtreadmill",)), w(4, sport="walk", sport_type="hiking")]
    ch = {"elapsedtime": arrays["t"], "elapseddistance": arrays["d"] / 1000.0, "elevation": arrays["z"],
          "cadence": arrays["cad"], "speed": np.full(len(arrays["t"]), 7.2)}
    return SimpleNamespace(workouts=ws, channel=lambda i, name: ch.get(name), flush_series=lambda: None,
                           cached_series=lambda key, wk, fn: fn())


def test_athlete_sums_the_outdoor_runs(monkeypatch):
    from backend.engine.wko5expr import dataset as D
    monkeypatch.setattr(D, "date_to_day", lambda d: 10)
    a = track([(1000, 0.10, 55, 1.0), (1000, 0.10, 82, 2.0)])
    secs, n = A.climb_cadence_seconds(_ds(a), dt.date(2026, 10, 6))
    assert n == 2 and sum(secs) == pytest.approx(2 * 1500, rel=0.15)
    out = A.climb_cadence(_ds(a), dt.date(2026, 10, 6))
    assert out["n_runs"] == 2 and out["verdict"] == "ok"               # 110 and 164 spm, 50 min of climbing
    assert out["walk_peak_spm"] == pytest.approx(110, abs=5) and out["run_peak_spm"] == pytest.approx(164, abs=5)


def test_cadence_check_endpoint(client, monkeypatch):   # noqa: F811
    from backend.api import racepower as RP
    monkeypatch.setattr(A, "climb_cadence_seconds", lambda ds, today: (mix(140, 172, sd=5.0), 12))
    monkeypatch.setattr(RP, "_dataset", lambda: object())
    RP._cache.pop("climb_cadence", None)
    r = client.get("/api/v1/racepower/cadence-check").json()
    assert r["verdict"] == "off" and r["n_runs"] == 12 and r["threshold_spm"] == 130 and len(r["bins"]) == len(r["seconds"])
    r_en = client.get("/api/v1/racepower/cadence-check", headers={"Accept-Language": "en"}).json()
    assert r_en["verdict"] == "off"
