"""
SP-726: 位置記憶 — a planned course that goes where you ran before uses how fast you were THERE
(engine/racepower/memory.py): the pieces each earlier run covered, the shrunk per-segment ratio, the
plan's segment times, the back-test (leave-one-out, earlier runs only) and the page. Synthetic tracks
only: a 5 km out-and-along path whose 3rd km is 1.5 × slower than its grade says.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from pytest import approx

from backend.engine.racepower import memory as MEM
from backend.engine.racepower import planner as PL
from backend.engine.racepower import trailhr as TH

ROOT = Path(__file__).resolve().parents[1]
LAT0, LON0 = 24.80, 121.40
M_PER_DEG = 111320.0
PACE = 0.4                         # s per m on the flat (400 s/km)


def _path(n_m: int = 5000, step: float = 5.0, north_m: float = 0.0, reverse: bool = False):
    x = np.arange(0.0, n_m + step, step)
    if reverse:
        x = x[::-1]
    lat = np.full(len(x), LAT0 + north_m / M_PER_DEG)
    lon = LON0 + x / (M_PER_DEG * math.cos(math.radians(LAT0)))
    return x, lat, lon


def _run(day: float = 1.0, slow: float = 1.5, north_m: float = 0.0, reverse: bool = False) -> dict:
    """A run along the path: flat, 400 s/km × the day's form, the 3rd km × `slow`; a compact track."""
    x, lat, lon = _path(north_m=north_m, reverse=reverse)
    d = np.abs(x - x[0])
    step = np.diff(d, prepend=0.0)
    where = x if not reverse else x                     # the slow place is a place, not a distance
    k = np.where((where >= 2000) & (where < 3000), slow, 1.0)
    t = np.cumsum(step * PACE * day * k)
    return MEM.compact(lat, lon, d, np.full(len(d), 500.0), t, np.ones(len(d), bool))


def _segs(n: int = 5) -> list[dict]:
    return [{"i": j + 1, "start_km": float(j), "end_km": float(j + 1), "start_m": j * 1000.0, "end_m": (j + 1) * 1000.0,
             "dist_m": 1000.0, "gain_m": 0.0, "loss_m": 0.0, "grade": 0.0} for j in range(n)]


def _plan():
    x, lat, lon = _path()
    return MEM.plan_points({"lat": lat.tolist(), "lon": lon.tolist(), "km": (x / 1000.0).tolist()})


def _entry(tr: dict, idx: int, rho: float = 1.0, date: str = "2026-09-01") -> dict:
    ce = MEM.cost_effort(tr["d"], tr["z"], lambda g: TH.slope_cost(None, g), TH.effort_divisor())
    return {**tr, "idx": idx, "date": date, "label": f"run {idx}", "rho": rho, "ce": ce, "CE": float(ce[-1]),
            "T": float(tr["cm"][-1])}


# ---- the pieces and the segments ---------------------------------------------------------------

def test_compact_keeps_a_point_every_25_m_with_the_moving_time():
    tr = _run()
    assert len(tr["d"]) == approx(5000 / 25 + 1, abs=1)
    assert np.all(np.diff(tr["d"]) >= 25.0 - 1e-9)
    assert tr["cm"][-1] == approx(5000 * PACE + 1000 * PACE * 0.5, rel=1e-3)
    assert MEM.compact([0] * 5, [0] * 5, [0] * 5, [0] * 5, [0] * 5, [True] * 5) is None   # no GPS


def test_one_earlier_pass_marks_the_slow_km_shrunk_by_half():
    tr = _run()
    # ρ: the run's actual ÷ the model's whole time — here the model expects the flat 400 s/km, so 1.1
    e = _entry(tr, 7, rho=(5000 * PACE + 500 * PACE) / (5000 * PACE))
    mem = MEM.segment_memory(_plan(), _segs(), [e])
    rows = mem["segments"]
    assert set(rows) == {0, 1, 2, 3, 4} and mem["n_activities"] == 1
    assert all(r["f"] == approx(1.0, abs=0.02) for r in rows.values())
    # the slow km: r = ρ × time share ÷ effort share = 1.1 × (600/2200) ÷ (1/5) = 1.5 → one pass: √1.5
    assert rows[2]["R"] == approx(math.sqrt(1.5), rel=0.02)
    assert rows[0]["R"] == approx(1.0, abs=0.02) and rows[4]["R"] == approx(1.0, abs=0.02)
    assert rows[2]["passes"][0]["date"] == "2026-09-01" and rows[2]["passes"][0]["r"] == approx(1.5, rel=0.02)


def test_more_passes_trust_the_place_more():
    rho = 1.1
    es = [_entry(_run(), i, rho=rho, date=f"2026-0{i}-01") for i in range(1, 4)]
    R = MEM.segment_memory(_plan(), _segs(), es)["segments"][2]["R"]
    assert R == approx(math.exp(3 * math.log(1.5) / 4), rel=0.02)          # K = 1 pseudo-pass


def test_the_other_direction_or_another_path_is_no_memory():
    assert MEM.segment_memory(_plan(), _segs(), [_entry(_run(reverse=True), 1)]) is None
    assert MEM.segment_memory(_plan(), _segs(), [_entry(_run(north_m=120.0), 1)]) is None
    assert MEM.segment_memory(None, _segs(), [_entry(_run(), 1)]) is None   # a manual course


def test_apply_replaces_the_terrain_multiplier_on_the_known_part():
    mem = {"segments": {1: {"f": 0.5, "R": 1.3}, 3: {"f": 1.0, "R": 0.9}}}
    assert MEM.apply([100.0, 100.0, 100.0, 100.0], mem, 1.0) == approx([100.0, 115.0, 100.0, 90.0])
    # 技術路段多 ×1.15 on the plan: the known part takes your own pace instead of it
    assert MEM.apply([115.0, 115.0], {"segments": {0: {"f": 1.0, "R": 1.3}}}, 1.15) == approx([130.0, 115.0])


# ---- the planner -----------------------------------------------------------------------------------

def _res():
    rows = [{"t": 400.0, "v": 2.5, "P": 200.0} for _ in range(5)]
    return {"rows": rows, "T": 2000.0}


def test_planner_applies_it_only_with_the_back_tests_go():
    mem = MEM.segment_memory(_plan(), _segs(), [_entry(_run(), 7, rho=1.1)])
    off = PL._place_memory(_res(), {**mem, "apply": False}, 1.0)
    assert off["T"] == 2000.0 and [r["t"] for r in off["rows"]] == [400.0] * 5
    assert off["memory"]["applied"] is False and PL._seg_memory(off["memory"], 2)["applied"] is False
    on = PL._place_memory(_res(), {**mem, "apply": True}, 1.0)
    assert on["rows"][2]["t"] == approx(400.0 * math.sqrt(1.5), rel=0.02)
    assert on["T"] == approx(sum(r["t"] for r in on["rows"])) and on["memory"]["delta_s"] == approx(on["T"] - 2000.0)
    assert on["rows"][2]["v"] == approx(2.5 * 400.0 / on["rows"][2]["t"])
    s = PL._seg_memory(on["memory"], 2)
    assert s["applied"] and s["n"] == 1 and s["passes"][0]["label"] == "run 7"
    assert PL._seg_memory(on["memory"], 9) is None
    assert "第 1、2、3、4、5 段你以前跑過" in PL.memory_text(on["memory"]) and "+" in PL.memory_text(on["memory"])
    assert "現在只標出來" in PL.memory_text(off["memory"])
    assert PL.memory_summary(on["memory"])["segments"] == 5 and PL.memory_summary(None) is None
    assert PL._place_memory(_res(), None, 1.0) == _res()


# ---- the back-test ---------------------------------------------------------------------------------

def test_backtest_on_a_route_run_again_and_again(monkeypatch):
    """Five runs of the same path (the 3rd km slow every time, the day's form varying): each predicted
    from the model without it and the memory of the runs before it."""
    forms = [1.00, 1.04, 0.97, 1.02, 0.99]
    tracks = {i: _run(day=f) for i, f in enumerate(forms)}
    pts = [{"idx": i, "date": f"2026-0{i + 1}-01", "label": f"run {i}", "x": 1.0, "eff_km": 5.0,
            "T_h": tracks[i]["cm"][-1] / 3600.0} for i in range(5)]
    model = {"points": pts, "delta": 0.0, "c": 9.0, "slope_cost": None, "terrain": {"mult": {"normal": 1.0}}}
    monkeypatch.setattr(TH, "fit", lambda points, delta: {"c": 9.0, "delta": 0.0})     # 400 s/km on 一般

    def course_of(tr):
        return {"segments": _segs(), "profile": {"lat": tr["lat"], "lon": tr["lon"], "km": [x / 1000.0 for x in tr["d"]]}}
    out = MEM.memory_backtest(model, tracks, course_of)
    assert out["n"] == 4                                     # the first run has nothing before it
    # the model misses the slow km by ~9 % of the run; the memory of the earlier runs brings it back
    assert out["total_plain"] == approx(0.09, abs=0.03)
    assert out["total_memory"] < out["total_plain"] / 2
    assert out["cum_memory"] < out["cum_plain"] and out["better"] is True
    slow = [r for r in out["rows"]]
    assert all(r["known"] == approx(1.0, abs=0.01) for r in slow)

    # a route with nothing special about any place: the memory has little to add and stays close
    flat_tracks = {i: _run(day=f, slow=1.0) for i, f in enumerate(forms)}
    pts2 = [{**p, "T_h": flat_tracks[p["idx"]]["cm"][-1] / 3600.0} for p in pts]
    out2 = MEM.memory_backtest({**model, "points": pts2}, flat_tracks, course_of)
    assert out2["n"] == 4 and abs(out2["total_memory"] - out2["total_plain"]) < 0.02


def test_the_flag_needs_three_runs_not_worse(tmp_path):
    from backend.engine.racepower import backtest as BT
    p = tmp_path / "bt.json"
    assert BT.memory_flag(p) is False
    BT.save({"trail_hr": {"memory": {"n": 2, "better": True}}}, p)
    assert BT.memory_flag(p) is False
    BT.save({"trail_hr": {"memory": {"n": 3, "better": True}}}, p)
    assert BT.memory_flag(p) is True
    BT.save({"trail_hr": {"memory": {"n": 9, "better": False}}}, p)
    assert BT.memory_flag(p) is False


# ---- the page ---------------------------------------------------------------------------------------

def test_the_segment_table_marks_the_known_segments_in_both_languages():
    html = (ROOT / "static" / "racepower.html").read_text("utf-8")
    assert "function memBadge" in html and "${memBadge(s)}" in html
    for loc in ("zh-TW", "en"):
        rp = json.loads((ROOT / "static" / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert "{r}" in rp["mem.badge"] and "{f}" in rp["mem.tip_on"] and "{f}" in rp["mem.tip_off"]
        assert "{date}" in rp["mem.pass"]
    en = json.loads((ROOT / "i18n" / "locales" / "en.json").read_text("utf-8"))
    assert any(k.startswith("位置記憶（推估）：第 {segs} 段") for k in en)
