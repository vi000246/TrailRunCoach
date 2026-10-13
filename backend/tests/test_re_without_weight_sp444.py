"""SP-444: road / trail predictions without a body weight.

RE is speed ÷ (W/kg) = v·W/P; every power ↔ speed step is v = RE·P/W, so with ONE weight for every
RE and every prediction the weight cancels and RE could be speed ÷ watts. It does not cancel
everywhere for a runner WITH a weight (the grade windows use each run's own-day weight, the CVI
adjustment is +0.01 per-kg RE), so (docs/plans/sp444-re-without-weight.md, plan B):

* with a weight: nothing changes (the numbers below are pinned from before SP-444, exactly);
* without one (weights are all-or-nothing): RE is speed ÷ watts (`unit: "per_w"`) for every run, every
  grade window and every prediction alike — the same formulas at a 1-kg unit mass; what really needs a
  weight (W/kg, LSS/kg, a per-kg typed RE without a typed weight) is None or says so.

Synthetic only: the demo athlete (backend/demo/build.py, small mode) built into tmp_path, one copy
with its weights cleared; network calls refused."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import socket

import pytest
from pytest import approx

from backend.engine.racepower import predict as PR
from backend.engine.racepower import re as RE
from backend.tests import demo_fixtures as F

# ---- with a weight: pinned from the code before SP-444 (exact) ----------------------------------


def test_predict_run_with_weight_is_unchanged():
    r = PR.predict_run(distance_km=30, cp=280, tte=3000, k=-0.07, re=1.18, weight=62.0, gain_m=1500,
                       effort_divisor=153.0, target_time_s=14400, road_re=1.0, train_cvi=40.0)
    assert r["time_s"] == 8000.1049587249645
    assert r["power"] == 261.42060851280115
    assert r["w_per_kg"] == 4.216461427625825
    assert r["cvi_crosscheck"]["time_s"] == 7287.258032366367
    assert r["target"]["cp_required"] == 162.09138701443362
    r = PR.predict_run(distance_km=10, cp=280, tte=3000, k=-0.07, re=0.98, weight=62.0, w_prime=15000)
    assert (r["time_s"], r["power"], r["task17"]["time_s"]) == \
        (2211.7768979883017, 286.03858893553144, 2205.9037900874637)


PLAN_PINNED = [   # (validated, opts) → time_s, power, w/kg, first two segment times
    ({}, {"mode": "auto"}, 3307.865800870217, 294.8816060599856, 4.212594372285508,
     (1898.135996807656, 1409.7298040625603)),
    ({}, {"mode": "time", "target_time_s": 5400}, 5400.0, 180.6349592575875, 2.5804994179655356,
     (3079.2156445677174, 2320.7843554322826)),
    ({}, {"mode": "power", "target_power": 250}, 3901.7151199649697, 249.9999999999308, 3.5714285714275826,
     (2224.8559700078786, 1676.8591499570914)),
    ({"trail": True}, {"mode": "auto"}, 3112.8102078599295, 296.13882579326327, 4.230554654189476,
     (1794.1980060820722, 1318.6122017778575)),
    ({"trail": True}, {"mode": "time", "target_time_s": 5400}, 5400.000000000642, 170.70814071718772,
     2.438687724531253, (3079.2156445680835, 2320.784355432558)),
    ({"trail": True}, {"mode": "power", "target_power": 250}, 3687.295839492715, 249.9999999999308,
     3.5714285714275826, (2102.58871020656, 1584.7071292861551)),
]


def _gpx_case():
    from backend.engine.racepower import course as CO
    from backend.tests.test_racepower_v2 import fake_v1, synthetic_track
    tr = synthetic_track({"len": 12000, "z": lambda x: 200 + (x * 0.05 if x < 6000 else (12000 - x) * 0.05)})
    c = CO.build_course(tr)
    return c, fake_v1("trail", 12.0, c["totals"]["gain_m"])


@pytest.mark.parametrize("val,opts,t,p,wkg,segs", PLAN_PINNED)
def test_plan_run_with_weight_is_unchanged(val, opts, t, p, wkg, segs):
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import planner as PL
    from backend.tests import test_racepower_v2 as T
    c, v1 = _gpx_case()
    out = PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(T.RE0), opts=opts, validated=val, effort_validated=False)
    s = out["summary"]
    assert (s["time_s"], s["power"], s["w_per_kg"]) == (t, p, wkg)
    assert tuple(x["t"] for x in out["segments"][:2]) == segs


# ---- without a weight: speed ÷ watts, the same formulas -----------------------------------------


def test_re_mass_is_the_weight_or_one():
    assert RE.re_mass(62.0) == 62.0
    assert RE.re_mass(None) == 1.0 and RE.re_mass(0) == 1.0


def test_cvi_adjustment_per_kg_is_additive_and_per_watt_relative():
    adj = RE.cvi_adjust(60.0, 0.0)                 # 中丘 (4) vs 平路 (1): 3 categories → +0.03 RE
    assert adj == approx(0.03)
    assert RE.with_cvi(1.0, adj, "per_kg") == 1.0 + adj
    assert RE.with_cvi(0.016, adj, "per_w") == approx(0.016 * 1.03)


def test_predict_run_per_watt_equals_per_kg():
    """RE per kg at weight W ≡ RE per W (= RE/W) at a 1-kg unit mass: the same prediction."""
    kw = dict(distance_km=30, cp=280, tte=3000, k=-0.07, gain_m=1500, effort_divisor=153.0, target_time_s=14400)
    a = PR.predict_run(re=1.18, weight=62.0, **kw)
    b = PR.predict_run(re=1.18 / 62.0, weight=None, re_unit="per_w", **kw)
    assert b["time_s"] == approx(a["time_s"], rel=1e-12) and b["power"] == approx(a["power"], rel=1e-12)
    assert b["target"]["cp_required"] == approx(a["target"]["cp_required"], rel=1e-12)
    assert b["w_per_kg"] is None
    # the CVI cross-check: road RE × (1 + adj) per watt (≈ + adj per kg at RE ≈ 1)
    c = PR.predict_run(re=1.18 / 62.0, weight=None, re_unit="per_w", road_re=1.0 / 62.0, train_cvi=40.0, **kw)
    assert c["cvi_crosscheck"]["re_used"] == approx((1.0 / 62.0) * (1 + c["cvi_crosscheck"]["adjust"]))


def test_plan_run_per_watt_equals_per_kg():
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import planner as PL
    from backend.tests import test_racepower_v2 as T
    c, v1 = _gpx_case()
    w = v1["used"]["weight"]["value"]
    v1w = json.loads(json.dumps(v1))
    v1w["used"]["weight"] = {"value": None}
    v1w["used"]["re"] = {"value": T.RE0 / w, "unit": "per_w"}
    for opts in ({"mode": "auto"}, {"mode": "time", "target_time_s": 5400}, {"mode": "power", "target_power": 250}):
        a = PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(T.RE0), opts=opts, validated={"trail": True},
                        effort_validated=False)
        b = PL.plan_run(v1=v1w, course=c, grade_re=GM.GradeRE(T.RE0 / w), opts=opts, validated={"trail": True},
                        effort_validated=False)
        assert b["summary"]["time_s"] == approx(a["summary"]["time_s"], rel=1e-9)
        assert b["summary"]["power"] == approx(a["summary"]["power"], rel=1e-9)
        assert b["summary"]["w_per_kg"] is None
        assert [x["t"] for x in b["segments"]] == approx([x["t"] for x in a["segments"]], rel=1e-9)


# ---- the demo athlete, with and without a weight ------------------------------------------------


@pytest.fixture(scope="module")
def roots(tmp_path_factory):
    from backend.demo import build as B
    with_w = tmp_path_factory.mktemp("demo-weight")
    B.build(with_w, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    no_w = tmp_path_factory.mktemp("demo-no-weight") / "root"
    shutil.copytree(with_w, no_w, symlinks=True)
    base = no_w / "base" / (no_w / "base" / "current").read_text("utf-8").strip()
    p = base / "plan.json"
    raw = json.loads(p.read_text("utf-8"))
    assert raw.get("weights"), "the demo athlete should have weights to clear"
    raw["weights"] = []
    p.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
    B.relocate_paths(base)          # the DB's file paths: the copy points at its own files
    return {"with": with_w, "none": no_w}


def _client(root, monkeypatch):
    F.demo_env(monkeypatch, root)
    from backend.api import racepower as RP
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    for k in ("inputs", "grade"):
        RP._cache.pop(k, None)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    return TestClient(build_app(demo=True), raise_server_exceptions=False)


@pytest.fixture
def no_net(monkeypatch):
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    yield
    from backend.api import racepower as RP
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    for k in ("inputs", "grade"):
        RP._cache.pop(k, None)


@pytest.fixture
def client(roots, monkeypatch, no_net):
    with _client(roots["none"], monkeypatch) as c:
        yield c


def _post(c, path, body):
    return c.post(f"/api/v1/racepower/{path}", json=body, headers={"X-TRC-CSRF": F.csrf(c)})


TRAIL = {"type": "trail", "distance_km": 30, "gain_m": 1500, "loss_m": 1500}
ROAD = {"type": "road", "distance_km": 21.0975, "gain_m": 0}


def test_inputs_without_weight_measure_re_per_watt(client):
    d = client.get("/api/v1/racepower/inputs").json()
    assert d["weight"]["value"] is None
    assert d["re"]["unit"] == "per_w"
    assert d["re"]["road"] and 0.005 < d["re"]["road"]["median"] < 0.05       # m/s per W (≈ 1 / 62)
    assert d["re"]["trail"]["fitted_run"] and d["re"]["trail"]["fitted_run"]["median"] > 0
    assert d["cp"]["lb_points"], "the runs' power (envelope, lower bound) is back without a weight"
    rows = d["re"]["road_runs"] + d["re"]["trail_runs"]
    assert rows and all(r["lss_kg"] is None and r["re"] > 0 for r in rows)


@pytest.mark.parametrize("path", ["predict", "plan"])
@pytest.mark.parametrize("body", [TRAIL, ROAD], ids=["trail", "road"])
def test_run_without_weight_is_predicted(client, path, body):
    r = _post(client, path, body)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["used"]["re"]["unit"] == "per_w" and d["used"]["weight"]["value"] is None
    res = d["result"] if path == "predict" else d["summary"]
    assert res["time_s"] > 0 and res["power"] > 0
    assert res["w_per_kg"] is None


def test_a_typed_per_kg_re_without_any_weight_says_why(client):
    r = _post(client, "predict", {**TRAIL, "re": 1.0})
    assert r.status_code == 400
    assert "體重" in r.json()["detail"] and "RE" in r.json()["detail"]


def test_a_typed_per_watt_re_is_used_as_is(client):
    r = _post(client, "predict", {**TRAIL, "re": 0.02, "re_unit": "per_w"})
    assert r.status_code == 200, r.text[:300]
    assert r.json()["used"]["re"]["value"] == 0.02 and r.json()["used"]["re"]["unit"] == "per_w"


def test_a_typed_weight_only_adds_w_per_kg(client):
    a = _post(client, "predict", TRAIL).json()["result"]
    r = _post(client, "predict", {**TRAIL, "weight": 62})
    assert r.status_code == 200, r.text[:300]
    b = r.json()["result"]
    assert b["time_s"] == a["time_s"] and b["power"] == a["power"]
    assert b["w_per_kg"] == approx(b["power"] / 62)
    # a per-kg RE with that typed weight: ÷ 62 per watt
    r = _post(client, "predict", {**TRAIL, "weight": 62, "re": 1.24})
    assert r.status_code == 200 and r.json()["used"]["re"]["value"] == approx(1.24 / 62)


def test_trail_without_weight_predicts_what_with_weight_does(roots, monkeypatch, no_net):
    """Weights cancel: the trail RE per W is the per-kg one ÷ today's weight, run by run."""
    out = {}
    for name in ("with", "none"):
        with _client(roots[name], monkeypatch) as c:
            out[name] = {"trail": _post(c, "predict", TRAIL).json(), "road": _post(c, "predict", ROAD).json()}
            from backend.api import racepower as RP
            from backend.api import wko5views
            wko5views._dataset_cfg.cache_clear()
            for k in ("inputs", "grade"):
                RP._cache.pop(k, None)
    a, b = out["with"]["trail"], out["none"]["trail"]
    w = a["used"]["weight"]["value"]
    assert b["used"]["re"]["value"] == approx(a["used"]["re"]["value"] / w, rel=1e-12)
    assert b["result"]["time_s"] == approx(a["result"]["time_s"], rel=1e-9)
    # road: the per-run CVI adjustment is relative per W (×(1 + 0.01) per category, RE ≈ 1 assumed)
    a, b = out["with"]["road"], out["none"]["road"]
    assert b["result"]["time_s"] == approx(a["result"]["time_s"], rel=0.01)


def test_page_shows_and_sends_the_per_watt_re():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "static"
    page = (static / "racepower.html").read_text("utf-8")
    assert 'INP?.re?.unit === "per_w"' in page
    assert 'body.re_unit = "per_w"' in page                   # a typed RE in the unit it was shown in
    assert "u.weight?.value ? u.cp?.value / u.weight.value : null" in page   # no W/kg of CP ÷ 1
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert cat.get("re.per_w_label") and cat.get("re.per_w_note"), loc
    en = json.loads((Path(__file__).resolve().parents[1] / "i18n" / "locales" / "en.json").read_text("utf-8"))
    for zh in ("手動填的 RE 是每公斤，沒有體重換算不了；請填體重，或不要手動填 RE（沒有體重時用你的速度／瓦）",
               "，CVI 調整 {pct:+.0f} %（推估）", "（速度／瓦：沒有體重）"):
        assert en.get(zh), zh
