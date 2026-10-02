"""AeT heat covariate (drift_agg module doc): warm runs (25–28 °C) enter the
AeT regression with their first-half HR moved to 25 °C by β (bpm/°C) — the
literature default (Jenkins 2023, 1 bpm/°C) shrunk toward the athlete's own
fit with n / (n + 20). Synthetic data only — never the WKO5 folder or the DB."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import drift_agg as DA
from backend.tests.test_heat_bands import _temps
from backend.tests.test_quality_gate import TODAY, _ds
from backend.tests.test_workout_review import _run


def _rows(n, beta, seed=1, sd_c=4.0):
    """n runs: HR = 60 + 0.4·P + β·T + noise, P 180–220 W, T around 24 °C."""
    rng = np.random.default_rng(seed)
    p = rng.uniform(180, 220, n)
    t = 24.0 + rng.normal(0, sd_c, n)
    hr = 60 + 0.4 * p + beta * t + rng.normal(0, 1.0, n)
    return [{"hr": float(h), "x": float(x), "temp_c": float(c)} for h, x, c in zip(hr, p, t)]


# ---------------------------------------------------------------------------
# β: default, personal, in between
# ---------------------------------------------------------------------------

def test_no_personal_data_uses_the_literature_default():
    b = DA.shrink_beta(DA.fit_heat_beta([]))
    assert b["beta"] == DA.BETA_DEFAULT == 1.0 and b["src"] == "default" and b["w"] == 0.0
    # below the minimum n, or one temperature only: still the default
    assert DA.shrink_beta(DA.fit_heat_beta(_rows(DA.BETA_MIN_N - 1, 0.3)))["src"] == "default"
    assert DA.shrink_beta(DA.fit_heat_beta(_rows(40, 0.3, sd_c=0.5)))["src"] == "default"
    assert "文獻預設" in DA.beta_text(b)


def test_lots_of_data_is_mostly_personal():
    b = DA.shrink_beta(DA.fit_heat_beta(_rows(400, 0.4)))
    assert b["src"] == "personal" and b["personal"] == pytest.approx(0.4, abs=0.05)
    w = 400 / (400 + DA.BETA_K)
    assert b["w"] == pytest.approx(w)
    assert b["beta"] == pytest.approx(w * b["personal"] + (1 - w) * 1.0)
    assert b["beta"] < 0.5
    assert "本人 400 次" in DA.beta_text(b)


def test_few_runs_land_between_personal_and_default():
    b = DA.shrink_beta(DA.fit_heat_beta(_rows(20, 0.4, seed=3)))
    assert b["src"] == "personal" and b["w"] == pytest.approx(0.5)
    assert min(b["personal"], 1.0) < b["beta"] < max(b["personal"], 1.0)
    assert b["beta"] == pytest.approx(0.5 * b["personal"] + 0.5, abs=1e-9)


def test_beta_is_clipped_to_sane_bounds():
    assert DA.shrink_beta(DA.fit_heat_beta(_rows(400, 6.0)))["beta"] == DA.BETA_BOUNDS[1]
    assert DA.shrink_beta(DA.fit_heat_beta(_rows(400, -3.0)))["beta"] == DA.BETA_BOUNDS[0]


def test_power_that_never_varies_is_dropped_from_the_fit():
    rows = [{**r, "x": 200.0} for r in _rows(60, 0.6, seed=5)]
    f = DA.fit_heat_beta(rows)
    assert f["personal"] is not None and f["n"] == 60


def test_heat_beta_without_any_temperature_is_the_default():
    ds = _ds([_run(TODAY - dt.timedelta(days=d), minutes=52) for d in (6, 4, 2)])
    b = DA.heat_beta(ds, TODAY)
    assert b["src"] == "default" and b["beta"] == 1.0 and b["n"] == 0


# ---------------------------------------------------------------------------
# the AeT points
# ---------------------------------------------------------------------------

def test_warm_runs_enter_heat_adjusted_hot_runs_stay_out():
    ws = [_run(TODAY - dt.timedelta(days=d), minutes=52, hr=135.0, hr_end=137.0) for d in (8, 6, 4, 2)]
    ds = _ds(ws)
    order = _temps(ds, [22.0, 27.0, 31.0, None])
    pts = DA.aet_points(ds, TODAY, beta={"beta": 1.0})
    assert [p["idx"] for p in pts] == [order[0].idx, order[1].idx, order[3].idx]   # cool, warm, none
    cool, warm, none = pts
    assert warm["band"] == "warm" and warm["heat_bpm"] == pytest.approx(2.0)     # 1 bpm/°C × (27 − 25)
    assert warm["hr1"] == pytest.approx(warm["hr1_raw"] - 2.0)
    assert cool["hr1"] == cool["hr1_raw"] and cool["heat_bpm"] == 0.0            # cool: not moved
    assert none["hr1"] == none["hr1_raw"] and none["temp_c"] is None             # no weather: not moved
    # the drift itself is not adjusted
    raw = DA.aet_points(ds, TODAY, beta={"beta": 0.0})
    assert [p["drift"] for p in raw] == [p["drift"] for p in pts]
    # the old reading (cool + no temperature) is still available
    assert [p["idx"] for p in DA.aet_points(ds, TODAY, bands=("cool", "none"))] == [order[0].idx, order[3].idx]


def test_aet_validity_says_warm_runs_were_heat_adjusted():
    ws = [_run(TODAY - dt.timedelta(days=d), minutes=52) for d in (6, 4, 2)]
    ds = _ds(ws)
    _temps(ds, [22.0, 27.0, 28.0])
    v = DA.aet_validity(ds, TODAY)
    assert v["points"] == 3 and not v["valid"]
    assert "25–28 °C" in v["reason"] and "推估" in v["reason"] and "飄移本身不校正" in v["reason"]
    assert v["heat"]["warm"] == 2 and v["heat"]["src"] == "default" and v["heat"]["ref_c"] == 25.0
    # without weather: no adjustment, no note
    ds = _ds([_run(TODAY - dt.timedelta(days=d), minutes=52) for d in (6, 4, 2)])
    v = DA.aet_validity(ds, TODAY)
    assert v["points"] == 3 and v["heat"] is None and "25–28" not in v["reason"]
