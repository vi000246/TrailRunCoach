"""SP-69: the interval in-band tolerance and the easy-run HR margin per athlete
(engine/interval_calib.py, engine/threshold_calib.py; calibrate.py items). SP-526 retired the last
rep's fade, the TIZ goal and the LTHR-test age (coach consensus, the same for everyone: see
test_coach_consensus_sp526.py). Synthetic data only."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import adapt as A
from backend.engine import calibrate as CAL
from backend.engine import interval_calib as IC
from backend.engine import quality_gate as QG
from backend.engine import threshold_calib as TCAL
from backend.engine import workout_review as WR
from backend.tests.calib_fixtures import assert_self_consistent

TODAY = dt.date(2026, 10, 1)
NAMES = (IC.TOL, TCAL.MARGIN)


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)


def _manual(monkeypatch, **vals):
    monkeypatch.setattr(CAL, "stored_entry",
                        lambda name, user_id=1: {"value": vals[name], "source": "user"} if name in vals else None)


# ---- registry ----------------------------------------------------------------

def test_items_are_registered_with_todays_constants_as_defaults():
    reg = CAL._registry()
    assert set(NAMES) <= set(reg)
    assert reg[IC.TOL].default == QG.IN_BAND_TOL and reg[TCAL.MARGIN].default == WR.AET_MARGIN
    assert not {IC.FADE, IC.TIZ, TCAL.AGE} & set(reg)                       # SP-526: retired
    for n in NAMES:                          # nothing stored: the default is in effect
        assert CAL.value(n) == reg[n].default and CAL.basis(n).startswith("預設")
        lo, hi = reg[n].bounds
        assert lo <= reg[n].default <= hi


def test_basis_says_manual_or_personal(monkeypatch):
    _manual(monkeypatch, interval_in_band_tol=0.97)
    assert CAL.basis(IC.TOL) == "手動"
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: {"value": 0.97, "n": 24, "w": 0.5,
                                                                        "personal": 0.96, "source": "fitted"})
    assert CAL.basis(IC.TOL) == "本人 n=24"


# ---- interval_in_band_tol: the CP tests ------------------------------------------

def _cp_rows(steps, start=dt.date(2025, 10, 1), every=60, cp=250.0):
    rows, d = [(start.isoformat(), cp)], start
    for s in steps:
        d, cp = d + dt.timedelta(days=every), cp * (1 + s)
        rows.append((d.isoformat(), cp))
    return rows


def test_cp_tests_that_move_4_percent_fit_back_to_0_98():
    item = CAL._registry()[IC.TOL]
    ds = SimpleNamespace(plan=SimpleNamespace(thresholds=[SimpleNamespace(date=d, cp=v) for d, v in
                                                           _cp_rows([0.04, -0.035, 0.045, -0.04, 0.04])]))
    e = assert_self_consistent(item, ds, 0.98, se=0.005, today=TODAY)
    assert e["n"] == 5


def test_steadier_cp_tests_move_the_tolerance_up_and_far_apart_ones_dont_count():
    f = IC.fit_tol_rows(_cp_rows([0.01, -0.01, 0.01]), TODAY)
    assert f.value == pytest.approx(0.995 if 0.995 <= IC.TOL_BOUNDS[1] else IC.TOL_BOUNDS[1])
    assert IC.fit_tol_rows(_cp_rows([0.04, 0.04]), TODAY) is None              # 2 pairs < 3
    assert IC.fit_tol_rows(_cp_rows([0.04, 0.04, 0.04], every=200), TODAY) is None
    assert IC.fit_tol_rows(_cp_rows([0.3, 0.3, 0.3]), TODAY).value == IC.TOL_BOUNDS[0]   # clipped


# ---- the verdict reads the values in effect ------------------------------------------

SPEC = ("x", "4×4 分", 4, 4, 3, 1.04, 1.08, False, "")


def test_interval_outcome_uses_the_tolerance_in_effect(monkeypatch):
    bouts = [{"power": 263}] * 4                                               # 0.973 × the lower bound
    assert QG.interval_outcome(bouts, SPEC, 260.0)["outcome"] == "too_high"
    _manual(monkeypatch, interval_in_band_tol=0.97)
    assert QG.interval_outcome(bouts, SPEC, 260.0)["outcome"] == "met"


# ---- easy_hr_margin_bpm ------------------------------------------------------------------

def test_margin_is_the_aet_se_never_below_3():
    assert TCAL.margin_fit(2.0, 10).value == 3.0
    assert TCAL.margin_fit(4.5, 10).value == 4.5
    assert TCAL.margin_fit(9.0, 10).value == TCAL.MARGIN_BOUNDS[1]
    assert TCAL.margin_fit(None, 10) is None


def test_margin_fit_on_drift_points(monkeypatch):
    from backend.engine import drift_agg as DA
    rng = np.random.default_rng(4)
    hr = rng.uniform(130, 150, 30)
    pts = [{"hr1": float(h), "drift": 0.05 + 0.003 * (h - 140) + float(rng.normal(0, 0.01)), "se": 0.01} for h in hr]
    monkeypatch.setattr(DA, "aet_points", lambda ds, today: pts)
    f = TCAL.fit_easy_margin(SimpleNamespace(plan=None), TODAY)
    assert f.n == 30 and TCAL.MARGIN_BOUNDS[0] <= f.value <= TCAL.MARGIN_BOUNDS[1]


def test_overhard_no_longer_reads_the_margin():
    # SP-301: rule D's 偏強 is power / TSS only — the HR margin changes nothing there
    r = {"avg_hr": 160, "aet": 150, "over_aet_s": 900, "hr_s": 3000}
    assert A.overhard(36, {**r, "aet_margin": 3.0}) is None
    assert A.overhard(36, {**r, "aet_margin": 5.0, "aet_margin_basis": "本人 n=12"}) is None


def test_friel_band_upper_edge_follows_the_margin(monkeypatch):
    assert QG.friel_band() == QG.FRIEL_HR_BAND
    _manual(monkeypatch, easy_hr_margin_bpm=4.5)
    assert QG.friel_band() == (QG.FRIEL_HR_BAND[0], 4.5)
    assert TCAL.margin_fields() == {"aet_margin": 4.5, "aet_margin_basis": "手動"}
