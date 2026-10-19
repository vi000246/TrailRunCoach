"""SP-414: a runner who never entered a body weight (plan.json `weights` empty, no weight in the
DB's athlete settings). GET /racepower/inputs answered 500: derive() put the season plan's fresh
CP test beside the W′ prior, and the prior's J/kg band multiplied None.

The W′ prior itself is Ruiz-Alias 2025's amateur mean in kJ — no body weight in it; only the
workbook's J/kg band beside it needs one, so that band is None without a weight (never a default
weight, owner 2026-10-13).

Synthetic only: the demo athlete (backend/demo/build.py, small mode) built into tmp_path with its
weights cleared; network calls refused."""
from __future__ import annotations

import datetime as dt
import json
import socket

import pytest

from backend.engine.racepower import cptest as T
from backend.tests import demo_fixtures as F


def test_w_prime_prior_without_weight_keeps_the_kj_prior():
    with_w = T.w_prime_prior(62.0, "male")
    none = T.w_prime_prior(None, "male")
    assert none["mid"] == with_w["mid"] == 13100.0
    assert (none["lo"], none["hi"]) == (with_w["lo"], with_w["hi"])
    assert none["rwc_band_j"] is None
    assert with_w["rwc_band_j"] and all(v > 0 for v in with_w["rwc_band_j"])


@pytest.fixture(scope="module")
def built_no_weight(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-no-weight")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    base = root / "base" / (root / "base" / "current").read_text("utf-8").strip()
    p = base / "plan.json"
    raw = json.loads(p.read_text("utf-8"))
    assert raw.get("weights"), "the demo athlete should have weights to clear"
    raw["weights"] = []
    p.write_text(json.dumps(raw, ensure_ascii=False), "utf-8")
    return root


@pytest.fixture
def client(built_no_weight, monkeypatch):
    F.demo_env(monkeypatch, built_no_weight)
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from backend.api import racepower as RP
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    for k in ("inputs", "grade"):
        RP._cache.pop(k, None)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        yield c
    wko5views._dataset_cfg.cache_clear()
    for k in ("inputs", "grade"):
        RP._cache.pop(k, None)


def test_inputs_answers_without_any_weight(client):
    """The reproduction: the demo athlete has a fresh season-plan CP test and no detected W′."""
    r = client.get("/api/v1/racepower/inputs")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["weight"]["value"] is None
    plan = next(s for s in d["cp"]["sources"] if s["id"] == "plan")
    assert plan["fresh"]
    short = d["cp"]["short"]
    if short and short["label"].startswith("計畫 CP 測試"):
        assert short["w_prime"] == 13100.0          # the kJ prior, weight-free


# ---- step 2 (owner 2026-10-13, B1): everything that needs a weight says so, nothing answers 500 ----

NO_W = "沒有體重"


def _post(c, path, body):
    return c.post(f"/api/v1/racepower/{path}", json=body, headers={"X-TRC-CSRF": F.csrf(c)})


BAIYUE = {"type": "baiyue", "distance_km": 16, "gain_m": 1200, "loss_m": 1200, "days": 2}
TRAIL = {"type": "trail", "distance_km": 30, "gain_m": 1500, "loss_m": 1500}


def test_walk_capacity_without_weight_is_none_not_an_exception():
    from backend.engine.racepower import athlete as A
    x = {"weight": None, "v_run": {"v": 2.0, "n": 50, "source": "t"}, "trail": [], "hike": [], "flat": [],
         "hike_down": [], "aet": {}, "pack_of": lambda a: 9.0, "packs": {}, "packs_recorded": set()}
    assert A.walk_capacity(None, dt.date(2026, 10, 13), inputs=x) is None


def test_predict_baiyue_without_weight_pack_factor_one_and_no_energy():
    from backend.engine.racepower import predict as PR
    days = PR.split_days(2, 16, 1200, 1200)
    r = PR.predict_baiyue(day_plan=days, eph=3.0, weight=None, pack_kg=None, hist_pack_kg=5.0)
    assert r["pack_factor"] == 1.0
    ep = 16 + 1200 / 100
    assert r["total"]["moving_h"] == pytest.approx(ep / 3.0)        # EP / (EP/h · M · 1)
    assert r["total"]["kcal"] is None and r["total"]["water_ml"] is None
    assert all(d["kcal"] is None and d["water_ml"] is None for d in r["days"])
    with_w = PR.predict_baiyue(day_plan=days, eph=3.0, weight=62.0, pack_kg=9.0, hist_pack_kg=5.0)
    assert with_w["pack_factor"] == pytest.approx(67 / 71) and with_w["total"]["kcal"] > 0


def test_plan_hike_gpx_without_weight_has_times_and_no_energy():
    from backend.engine.racepower import course as CO
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import planner as PL
    from backend.tests.test_racepower_v2 import synthetic_track
    c = CO.build_course(synthetic_track({"len": 8000, "z": lambda x: 1000 + x * 0.1}))
    v1 = {"type": "baiyue", "env": {"M": 1.0, "from": {"altitude_m": 100, "temp_c": 20, "rh_pct": 70},
                                    "to": {"altitude_m": 1800, "temp_c": 15, "rh_pct": 70}},
          "used": {"weight": {"value": None}, "eph": {"value": 3.0}, "aet": {"value": 142}},
          "result": {"pack_factor": 1.0, "pack_kg": None, "M": 1.0, "time_s": 10000,
                     "days": [{"day": 1, "km": 8, "gain_m": 800, "loss_m": 0, "moving_h": 2.8, "kcal": None,
                               "water_ml": None}]}, "warnings": []}
    out = PL.plan_hike(v1=v1, course=c, hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                       opts={"mode": "auto"}, validated={})
    s = out["summary"]
    assert s["time_s"] > 0 and s["kcal"] is None and s["water_ml"] is None
    assert all(d["kcal"] is None and d["water_ml"] is None and d["moving_h"] > 0 for d in out["days"])
    assert any(NO_W in w for w in out["warnings"])
    assert not any("(體重 + 5 kg)" in w for w in out["warnings"])     # the pack-factor rule is not applied


def test_capacity_backtest_without_weight_is_skipped_not_an_error(monkeypatch):
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import backtest as BT
    x = {"weight": None, "v_run": {"v": 2.0, "n": 50, "source": "t"},
         "trail": [{"a": 1, "g": 0.2, "g_raw": 0.2, "v": 0.8, "z": 300, "t": 0.0, "k": 0, "hr": 140, "hr_lag": 140,
                    "seg": ("trail", 1, None, 0), "seg_hr": 140, "src": "trail"}],
         "hike": [], "flat": [], "hike_down": [], "aet": {}, "pack_of": lambda a: 9.0, "packs": {},
         "packs_recorded": set()}
    monkeypatch.setattr(A, "walk_capacity_inputs", lambda ds, today, exclude=None: x)
    r = BT.capacity_backtest(None, dt.date(2026, 10, 13))
    assert r["skipped"] == "no_weight" and r["passed"] is False and "error" not in r
    assert NO_W in r["reason"]


def test_baiyue_predict_without_weight_answers_with_the_reason(client):
    r = _post(client, "predict", BAIYUE)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["result"]["pack_factor"] == 1.0
    assert d["result"]["total"]["kcal"] is None
    assert d["used"]["pack_kg"]["value"] is None and NO_W in d["used"]["pack_kg"]["source"]
    # SP-482: hint objects — no weight is a missing required input (RC28), class 1 in every level
    assert any(NO_W in w["text"] and w["id"] == "RC28" and w["cls"] == 1 for w in d["warnings"])
    assert d["result"]["time_s"] > 0


def test_baiyue_plan_without_weight_answers_with_the_reason(client):
    r = _post(client, "plan", {**BAIYUE, "trip_kind": "group"})
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["summary"]["time_s"] > 0 and d["summary"]["kcal"] is None
    assert NO_W in d["fuel"]["unavailable"]
    assert any(NO_W in w["text"] and w["id"] == "RC28" and w["cls"] == 1 for w in d["warnings"])     # SP-482


@pytest.mark.parametrize("path", ["predict", "plan"])
@pytest.mark.parametrize("extra", [{"re": 1.0}], ids=["manual_re"])
def test_run_without_weight_is_400_with_the_reason(client, path, extra):
    """SP-444: without a weight road / trail are predicted on speed ÷ watts
    (test_re_without_weight_sp444.py); only a typed per-kg RE still needs one."""
    for kind in (TRAIL, {"type": "road", "distance_km": 10, "gain_m": 0}):
        r = _post(client, path, {**kind, **extra})
        assert r.status_code == 400, (kind["type"], r.status_code, r.text[:300])
        assert NO_W in r.json()["detail"] and "RE" in r.json()["detail"]


def test_run_with_a_typed_weight_is_not_blocked_by_the_no_weight_reason(client):
    """A weight typed into 「你的數字」 is a weight: the no-weight 400 is not given (SP-444: the RE is
    measured per W without a weight, so the prediction is made)."""
    r = _post(client, "predict", {**TRAIL, "weight": 62})
    assert r.status_code == 200, r.text[:300]
    r = _post(client, "predict", {**TRAIL, "weight": 62, "re": 1.0})
    assert r.status_code == 200, r.text[:300]


def test_page_says_what_cannot_be_computed_and_where_to_fill_it():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "static"
    page = (static / "racepower.html").read_text("utf-8")
    assert 'id="noweight"' in page and "INP.weight.value" in page
    assert "/api/v1/app/settings#personal" in page
    assert "F?.unavailable" in page and "!PLAN?.fuel?.unavailable" in page     # the 補給 card says why
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert cat.get("noweight.banner") and cat.get("noweight.link"), loc
    zh = json.loads((static / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))["noweight.banner"]
    for item in ("登山能力模型", "背包預設", "熱量", "W/kg"):
        assert item in zh
    assert "速度／瓦" in zh          # SP-444: road / trail are predicted per watt, not 「算不出」
