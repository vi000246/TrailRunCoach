"""SP-728: the calculator's elevation chart marks the stretches where you may need hands too (攀爬段) —
from what a plan has (the terrain's shape, your own earlier passes over the same spot), a model fitted on
your own trail runs, checked by a cross-validation grouped by activity whose numbers go in the ?.
Synthetic runs only (no one's data)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend.engine.racepower import climb as CL
from backend.engine.racepower import share as SH

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


# ---- synthetic courses and runs ----------------------------------------------------------------

def _course(lat0: float, n_km: float = 8.0):
    """A line north from (lat0, 121.5): rolling, with steep −30 % descents at 2, 4 and 6 km."""
    d = np.arange(0.0, n_km * 1000.0, 2.0)                       # a sample every ≤ 15 s even at 0.5 km/h
    z = 600.0 + 40.0 * np.sin(d / 700.0)
    for at in (2000.0, 4000.0, 6000.0):
        z -= np.clip(d - at, 0, 300) * 0.30
        z += np.clip(d - at - 300.0, 0, 300) * 0.30          # back up after
    lat = lat0 + d / 111_195.0
    lon = np.full_like(d, 121.5)
    return d, z, lat, lon


def _run(course, scramble_at=None, seed=0):
    d, z, lat, lon = course
    r = np.random.default_rng(seed)
    g = np.gradient(z, d)
    v = np.clip(9.0 - 20.0 * np.abs(g), 3.0, 12.0) * r.uniform(0.92, 1.08)   # km/h; a −30 % descent ~ 3 km/h
    if scramble_at is not None:
        v = np.where((d > scramble_at) & (d < scramble_at + 300.0), 0.5, v)   # hands and feet: 0.5 km/h
    dt = 2.0 / (v / 3.6)
    t = np.concatenate([[0.0], np.cumsum(dt[:-1])])
    return CL.compact(lat, lon, d, z, t, v)


def _tracks(n_a=5, n_b=5):
    A, B = _course(24.70), _course(24.90)
    out = {}
    for k in range(n_a):
        out[f"a{k}"] = _run(A, scramble_at=4000.0, seed=k)            # route A: the 4 km descent is a scramble
    for k in range(n_b):
        out[f"b{k}"] = _run(B, seed=100 + k)                          # route B: same terrain, never a scramble
    return out, A, B


def _profile(course):
    d, z, lat, lon = course
    return {"km": list(d / 1000.0), "z": list(z), "lat": list(lat), "lon": list(lon)}


# ---- one earlier run ---------------------------------------------------------------------------

def test_a_watch_pause_is_not_time_and_the_slow_part_is_counted():
    n = 400
    d = np.arange(n) * 5.0
    t = np.arange(n) * 2.0
    t[200:] += 600.0                                   # a 10-minute auto-pause
    kmh = np.full(n, 9.0)
    kmh[100:150] = 0.5                                 # 100 s below 1 km/h
    tr = CL.compact(np.full(n, 24.7) + d / 111_195.0, np.full(n, 121.5), d, np.zeros(n), t, kmh)
    assert tr["ce"][-1] == pytest.approx(2.0 * (n - 1), abs=2.5)       # the pause left out
    assert tr["cs"][-1] == pytest.approx(100.0, abs=4.5)


def test_the_label_is_a_slow_steep_window_against_your_own_median():
    tracks, _A, _B = _tracks()
    h = CL.history(tracks)
    acts = np.asarray(h["act"])
    pos = acts[h["y"] > 0]
    assert len(pos) and all(str(a).startswith("a") for a in pos)          # only route A's scramble
    steep = np.abs(h["X"][:, 0]) >= CL.STEEP
    assert (h["y"][~steep] == 0).all()                                   # never a flat window
    # the other steep descents of route A, run at the usual pace, are not scrambles
    assert h["y"].sum() < steep.sum() / 3


# ---- the model and its cross-validation -------------------------------------------------------

def test_the_cross_validation_separates_the_scrambles_and_says_how_well():
    tracks, _A, _B = _tracks()
    f = CL.fit(tracks)
    cv = f["cv"]
    assert f["usable"] and f["reason"] is None
    assert cv["n_acts"] == 10 and cv["n_pos"] >= CL.MIN_POS and cv["top_pct"] == CL.TOP_PCT
    assert cv["auc"] > 0.9 and cv["top_share"] > cv["base_share"] * 3


def test_too_few_runs_or_scrambles_give_no_marks():
    tracks, A, _B = _tracks(n_a=2, n_b=2)
    f = CL.fit(tracks)
    assert not f["usable"] and f["reason"] == "few"
    m = CL.marks(f, _profile(A))
    assert m["marks"] == [] and not m["usable"]
    assert CL.fit({}) == {"usable": False, "reason": "few", "cv": {"n_acts": 0, "n_pos": 0, "n_windows": 0}}


def test_a_run_never_gets_its_own_place_memory():
    tracks, _A, _B = _tracks()
    h = CL.history(tracks)
    acts = list(dict.fromkeys(h["act"].tolist()))
    code = np.array([acts.index(a) for a in h["act"]])
    near = CL._near(h["spot"], h["spot"], CL._grid(h["spot"]))
    only = {"a0": 1}                                    # route A's runs: only a0 allowed as memory
    allowed = np.array([a in only for a in acts])
    mem = CL._mem(near, h["y"], code, allowed, code)
    a0 = np.asarray(h["act"]) == "a0"
    assert (mem[a0][:, 0] == 0).all()                  # a0's own windows: its own passes don't count
    a1 = np.asarray(h["act"]) == "a1"
    assert (mem[a1][:, 0] == 1).any()                  # a1 sees a0 at the same spots


def test_the_plan_marks_the_scramble_from_your_own_passes_not_the_same_terrain_elsewhere():
    tracks, A, B = _tracks()
    f = CL.fit(tracks)
    ma = CL.marks(f, _profile(A))
    assert ma["usable"] and ma["badge"] == "推估"
    assert len(ma["marks"]) == 1
    m = ma["marks"][0]
    assert 3.9 <= m["start_km"] <= 4.1 and 4.2 <= m["end_km"] <= 4.4 and m["p"] >= f["thr"]
    assert (m["end_km"] - m["start_km"]) * 1000 >= CL.MIN_MARK_M
    assert CL.marks(f, _profile(B))["marks"] == []      # route B: the same −30 % descents, never a scramble


def test_the_plan_needs_coordinates():
    tracks, A, _B = _tracks()
    f = CL.fit(tracks)
    p = _profile(A)
    p.pop("lat")
    assert CL.marks(f, p) is None
    assert CL.marks(None, _profile(A)) is None


def test_auc_and_top_share():
    assert CL.auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([0, 0, 1, 1])) == 1.0
    assert CL.auc(np.array([0.5, 0.5]), np.array([0, 1])) == 0.5
    assert CL.auc(np.array([0.5, 0.6]), np.array([1, 1])) is None
    p = np.arange(100) / 100.0
    y = (np.arange(100) >= 97).astype(float)
    assert CL.top_share(p, y, 5.0) == pytest.approx(0.6)


# ---- the plan and the share ---------------------------------------------------------------------

def test_a_share_keeps_the_marks_and_the_shares_not_how_many_runs():
    tracks, A, _B = _tracks()
    c = CL.marks(CL.fit(tracks), _profile(A))
    plan = {"type": "trail", "summary": {"time_s": 3600, "km": 8.0}, "segments": [{"i": 1}], "climb": c}
    snap = SH.snapshot(plan, title="t")
    assert snap["climb"]["marks"] == c["marks"]
    assert set(snap["climb"]["cv"]) == {"auc", "top_share", "base_share", "top_pct"}
    assert "n_acts" not in json.dumps(snap["climb"])
    plan["climb"] = {**c, "marks": []}
    assert "climb" not in SH.snapshot(plan, title="t")       # nothing to draw: not in the snapshot


def test_the_calculator_asks_for_the_marks_on_a_gpx_trail_or_hike_only():
    src = (Path(__file__).resolve().parents[1] / "engine" / "racepower" / "calc.py").read_text("utf-8")
    i = src.index('cm = getattr(ctx, "climb_marks", None)')
    blk = src[i:i + 500]
    assert 'body.type in ("trail", "baiyue") and course.get("source") == "gpx"' in blk
    api = (Path(__file__).resolve().parents[1] / "api" / "racepower.py").read_text("utf-8")
    assert "def climb_marks(self, course: dict)" in api and "CL.fit(A.climb_tracks(ds, model))" in api


# ---- the chart (node) ----------------------------------------------------------------------------

def _run_js(expr: str, **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const el = { style: {}, appendChild() {}, set textContent(v) {} };
const ctx = { DATA: JSON.parse(process.argv[3]), console, isFinite };
ctx.window = ctx;
ctx.document = { getElementById: () => null, createElement: () => ({}), head: el, documentElement: {} };
ctx.getComputedStyle = () => ({ getPropertyValue: () => "#123456" });
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/seg_profile.js", "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
def test_the_chart_the_legend_the_hover_and_the_table_show_the_marks():
    P = SH.with_rows(SH.example("example-trail-50k"))
    P["climb"] = {"marks": [{"start_km": 11.0, "end_km": 12.4, "p": 0.4}],
                  "cv": {"auc": 0.72, "top_share": 0.27, "base_share": 0.05, "top_pct": 5}}
    o = _run_js("(() => { const o = SegProfile.option(DATA.P, { measure: 'pace' }); const s = o.series.find((x) => x.name === 'climb.legend');"
                "const r = SegProfile.rowAt(DATA.P, 11.5);"
                "return { area: s && s.markArea.data, legend: SegProfile.legend(DATA.P, {}), tip: SegProfile.rowTip(DATA.P, r),"
                " table: SegProfile.table(DATA.P, {}) }; })()", P=P)
    assert o["area"] and o["area"][0][0]["xAxis"] == 11.0 and o["area"][0][1]["xAxis"] == 12.4
    assert o["area"][0][0]["label"]["formatter"] == "🧗"
    assert "climb.legend" in o["legend"] and "climb.tip" in o["legend"]
    assert "climb.row" in o["tip"]
    assert o["table"].count("🧗") == sum(1 for r in P["chart_rows"] if r["end_km"] > 11.0 and r["start_km"] < 12.4)


@needs_node
def test_no_marks_no_legend():
    P = SH.with_rows(SH.example("example-trail-50k"))
    o = _run_js("({ legend: SegProfile.legend(DATA.P, {}), n: SegProfile.option(DATA.P, {}).series.length })", P=P)
    assert "climb" not in o["legend"]


def test_the_texts_are_in_both_catalogs():
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        for k in ("climb.legend", "climb.tip", "climb.row"):
            assert cat.get(k), (loc, k)
        assert "{hit}" in cat["climb.tip"] and "{base}" in cat["climb.tip"] and "{top}" in cat["climb.tip"]
