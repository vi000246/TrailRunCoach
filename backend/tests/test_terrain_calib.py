"""速度與地形 per athlete (generalize-athlete plan B6): the climb divisor
(engine/terrain_calib.py), the flat-speed fallback from threshold pace, one
trail test for every module. Synthetic data only."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import calibrate as CAL
from backend.engine import terrain_calib as TC
from backend.tests.calib_fixtures import assert_self_consistent

TODAY = dt.date(2026, 10, 1)


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)


def _ds(n_flat=40, n_climb=40, divisor=153.0, v=10.0, seed=5):
    """Runs following t = (km + gain/divisor) / v with noise."""
    from backend.engine.wko5expr.dataset import date_to_day
    rng = np.random.default_rng(seed)
    ws = []
    for i in range(n_flat + n_climb):
        km = float(rng.uniform(6, 25))
        gain = float(rng.uniform(0, 5) * km) if i < n_flat else float(rng.uniform(25, 90) * km)
        h = (km + gain / divisor) / v * float(rng.normal(1.0, 0.03))
        ws.append(SimpleNamespace(sport="run", sport_type="running", tags=[],
                                  day=date_to_day(TODAY - dt.timedelta(days=i % 360 + 1)),
                                  metrics={"distance": km, "climbing": gain, "movingduration": h * 3600}))
    return SimpleNamespace(workouts=ws)


def test_default_is_itra_until_fitted():
    from backend.engine.algorithms.effort import divisor_of, effort_distance
    assert TC.divisor() == 100.0 and divisor_of("fitted_run") == 100.0 and divisor_of("scarf") == 126.0
    assert effort_distance(10, 500, formula="fitted_run") == pytest.approx(15.0)


def test_author_like_runs_fit_back_to_153():
    """Self-consistency (calib_fixtures): runs built on 153 m per effort km."""
    item = CAL._registry()["climb_divisor_run"]
    e = assert_self_consistent(item, _ds(n_flat=280, n_climb=150), 153.0, se=15.0, today=TODAY)   # 430 runs like the author
    assert e["n"] == 150 and abs(e["personal"] - 153.0) < 10


def test_needs_eight_climbing_runs():
    assert TC.fit_divisor(_ds(n_climb=5), TODAY) is None


def test_the_value_in_effect_reaches_trailhr(monkeypatch):
    from backend.engine.racepower import trailhr as TH
    monkeypatch.setattr(CAL, "value", lambda name, user_id=1: 140.0 if name == "climb_divisor_run" else 1.0)
    assert TH.effort_divisor() == 140.0
    monkeypatch.setitem(TH.TRAILHR, "divisor", 120.0)
    assert TH.effort_divisor() == 120.0                                 # an explicit override wins


def test_flat_speed_from_threshold_pace():
    from backend.engine import equivalence as EQ
    v, src, n = EQ._flat_speed([], None, tpace_min_per_km=5.0)        # 12 km/h at threshold
    assert v == pytest.approx(9.0) and "閾值配速" in src and n == 0
    v, src, _ = EQ._flat_speed([], None)
    assert v == EQ.DEFAULT_V_FLAT and "推估" in src


def test_one_trail_test():
    from backend.engine.algorithms.classify import is_trail
    from backend.engine import overview as OV
    from backend.engine.panels import climb_vam as PC
    from backend.engine.racepower import athlete as A
    tagged = SimpleNamespace(sport="run", sport_type="running", tags=["runningtrail"])
    typed = SimpleNamespace(sport="run", sport_type="trail running", tags=[])
    road = SimpleNamespace(sport="run", sport_type="running", tags=[])
    for w in (tagged, typed):
        assert is_trail(w) and A.is_trail(w) and PC.is_trail_run(w) and OV.category(w) == "trail"
    assert not is_trail(road) and OV.category(road) == "road"
