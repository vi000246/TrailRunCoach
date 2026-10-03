"""熱與溫度 per athlete (generalize-athlete plan B4, engine/heat_calib.py):
the Hadley β (T1) fitted on route efforts and shrunk toward 0.3, the RH
default (T3), the home training conditions (P5); the users (trailhr,
zone_events, climb_vam) read the value in effect. Synthetic files in tmp."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from backend.engine import calibrate as CAL
from backend.engine import heat_calib as HC
from backend.tests.calib_fixtures import assert_self_consistent


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    from backend.engine import routes as R
    monkeypatch.setattr(R, "HOME", tmp_path)
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)
    HC.clear_memo()
    yield
    HC.clear_memo()


def _index(root, n_routes=8, per_route=12, beta=0.224, seed=3, power=True):
    """Route efforts like the reference runner's: HR = route base + 0.18·P + β·(Hadley − 120) + noise."""
    rng = np.random.default_rng(seed)
    routes = []
    for r in range(n_routes):
        base = 100 + 5 * r
        effs = []
        for i in range(per_route):
            had = float(rng.uniform(95, 165))
            p = float(rng.normal(220, 12))
            hr = base + 0.18 * p + beta * (had - 120) + float(rng.normal(0, 2.0))
            st = dt.datetime(2025, 1 + (i % 12), 1 + r, 6 + (i % 3) * 5)
            effs.append({"sport": "run", "file": f"f{r}_{i}", "start": st.isoformat(), "avg_hr": hr,
                         "avg_power": p if power else None, "moving_s": 1800 + 60 * i, "dist_km": 5.0 + p / 100,
                         "wx": {"hadley": had, "temp_c": 20.0}})
        routes.append({"id": f"r{r}", "efforts": effs})
    (root / "index.json").write_text(json.dumps({"routes": routes, "segments": []}), "utf-8")


# ---- T1 --------------------------------------------------------------------------

def test_default_beta_without_route_data():
    hb = HC.hr_beta()
    assert hb["beta"] == HC.BETA_DEFAULT == 0.3 and hb["source"] == "default" and "推估" in hb["src"]


def test_beta_fit_and_shrinkage_for_the_author(tmp_path):
    """Self-consistency (calib_fixtures): efforts following the reference 0.224
    fit back within ±1 SE (0.036) of it after shrinkage toward 0.3."""
    _index(tmp_path, n_routes=12, per_route=22)                     # 264 efforts, like the reference runner's 271
    item = CAL._registry()["hadley_hr_beta"]
    e = assert_self_consistent(item, None, 0.224, se=0.036)
    assert e["n"] >= 250 and abs(e["personal"] - 0.224) < 0.02 and 0.224 < e["value"] < 0.3
    hb = HC.hr_beta()
    assert hb["source"] == "fitted" and hb["beta"] == pytest.approx(e["value"]) and "本人" in hb["src"]


def test_beta_needs_enough_efforts_and_spread(tmp_path):
    _index(tmp_path, n_routes=2, per_route=10)                      # 20 < 30
    assert HC.fit_hadley_beta() is None and HC.hr_beta()["source"] == "default"
    rows = [{"route": "a", "hr": 150.0, "p": 200.0, "v": 10.0, "mv": 40.0, "hour": 7, "hadley": 120.0 + (i % 5)}
            for i in range(40)]
    assert HC.fit_beta_rows(rows) is None                           # Hadley span 4 < 40


def test_beta_speed_basis_without_power(tmp_path):
    _index(tmp_path, n_routes=10, per_route=10, power=False)
    f = HC.fit_hadley_beta()
    assert f is not None and f.n >= 90


def test_manual_beta_wins(monkeypatch, tmp_path):
    _index(tmp_path, n_routes=12, per_route=22)
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1:
                        {"value": 0.5, "source": "user"} if name == "hadley_hr_beta" else None)
    HC.clear_memo()
    hb = HC.hr_beta()
    assert hb["beta"] == 0.5 and hb["source"] == "user" and "手動" in hb["src"]


def test_users_read_the_beta_in_effect(monkeypatch):
    from backend.engine import heat as HT
    from backend.engine.racepower import trailhr as TH
    monkeypatch.setattr(HC, "hr_beta", lambda: {"beta": 0.4, "se": 0.1, "n": 0, "source": "default", "src": "x"})
    assert HT.hr_heat_adjust(150.0, 130.0) == pytest.approx(146.0)
    assert HT.hr_heat_adjust(150.0, 130.0, beta=0.1) == pytest.approx(149.0)
    assert TH.heat_shift(150.0, 160.0) == pytest.approx(0.4 * 30 / 160)
    from backend.engine import zone_events as ZE
    sh = ZE.hr_shift([], dt.date(2026, 10, 1))
    assert sh["heat"]["beta"] == 0.4 and sh["heat"]["source"] == "x" and sh["heat"]["beta_source"] == "default"
    assert "預設" in ZE.heat_note(sh)


# ---- T3 / P5 --------------------------------------------------------------------

def _weather(root, acts):
    from backend.engine import route_weather as RW
    doc = {"version": RW.ACTIVITY_WX_VERSION, "activities": {f"a{i}": a for i, a in enumerate(acts)}}
    (root / "activity_weather.json").write_text(json.dumps(doc), "utf-8")


def test_rh_default_and_home_conditions(tmp_path):
    from backend.engine import zone_events as ZE
    from backend.engine.racepower import athlete as A
    assert ZE.rh_default() == 60.0                                   # no data: 推估
    fb = A.fallback_training()
    assert (fb["temp_c"], fb["rh_pct"], fb["altitude_m"]) == (12.0, 70.0, 200.0) and "預設" in fb["label"]
    _weather(tmp_path, [{"date": "2026-01-01", "temp_c": 24.0 + i % 3, "rh_pct": 80.0 + i % 5} for i in range(30)])
    HC.clear_memo()
    assert 75.0 <= ZE.rh_default() <= 82.0                           # own median 82, w = 30/40 toward 60
    fb = A.fallback_training()
    assert 20.0 < fb["temp_c"] <= 25.0 and 75.0 < fb["rh_pct"] <= 84.0 and "本人" in fb["label"]


def test_items_are_registered_with_chips():
    names = set(CAL._registry())
    assert {"hadley_hr_beta", "humidity_default", "home_temp_c", "home_rh_pct", "aet_heat_beta"} <= names
    d = CAL.describe("hadley_hr_beta", None)
    assert d["value"] == 0.3 and d["chip"]["text"] == "預設（推估）"
