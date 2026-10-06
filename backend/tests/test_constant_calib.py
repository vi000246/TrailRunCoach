"""SP-69: the interval verdict, the LTHR-test reminder and the easy-run HR margin per athlete
(engine/interval_calib.py, engine/threshold_calib.py; calibrate.py items). Synthetic data only."""
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
from backend.engine import threshold_confidence as TC
from backend.tests.calib_fixtures import assert_self_consistent

TODAY = dt.date(2026, 10, 1)
NAMES = (IC.TOL, IC.FADE, IC.TIZ, TCAL.AGE, TCAL.MARGIN)


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
    assert reg[IC.TOL].default == QG.IN_BAND_TOL and reg[IC.FADE].default == QG.LAST_FADE
    assert reg[IC.TIZ].default == QG.TIZ_GOAL
    assert reg[TCAL.AGE].default == TC.TEST_AGE_DAYS and reg[TCAL.MARGIN].default == A.OVER_HR_BPM
    for n in NAMES:                          # nothing stored: the default is in effect
        assert CAL.value(n) == reg[n].default and CAL.basis(n).startswith("預設")
        lo, hi = reg[n].bounds
        assert lo <= reg[n].default <= hi


def test_basis_says_manual_or_personal(monkeypatch):
    _manual(monkeypatch, interval_last_fade=0.07)
    assert CAL.basis(IC.FADE) == "手動"
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: {"value": 0.06, "n": 24, "w": 0.5,
                                                                        "personal": 0.07, "source": "fitted"})
    assert CAL.basis(IC.FADE) == "本人 n=24"


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


# ---- interval_last_fade / interval_tiz_goal: the planned sessions --------------------

def _sessions(n=40, seed=3, fade_mu=0.02, fade_sd=0.02, tiz_mu=0.93, tiz_sd=0.05):
    rng = np.random.default_rng(seed)
    out = []
    for _i in range(n):
        first = float(rng.uniform(260, 280))
        drop = float(rng.normal(fade_mu, fade_sd))
        ps = [first, first * 0.995, first * 0.99, first * (1 - drop)]
        out.append({"powers": ps, "planned": 4, "floor": 0.98 * 0.95 * 270.0,
                    "tiz_ratio": float(min(1.0, rng.normal(tiz_mu, tiz_sd)))})
    return out


def test_author_like_sessions_fit_back_to_the_defaults(monkeypatch):
    rows = _sessions()
    monkeypatch.setattr(IC, "_sessions_of", lambda ds, today: rows)
    reg = CAL._registry()
    assert_self_consistent(reg[IC.FADE], None, 0.05, se=0.01, today=TODAY)
    assert_self_consistent(reg[IC.TIZ], None, 0.85, se=0.02, today=TODAY)


def test_the_selections():
    good = {"powers": [270, 268, 266, 240], "planned": 4, "floor": 250.0, "tiz_ratio": 0.9}
    early_miss = {**good, "powers": [270, 240, 266, 260]}                     # rep 2 out of the band
    short = {**good, "powers": [270, 268, 266]}                                # stopped early
    assert IC.last_drops([good, early_miss, short]) == [pytest.approx(1 - 240 / 270)]
    every = {**good, "powers": [270, 268, 266, 262], "tiz_ratio": 1.2}
    assert IC.tiz_ratios([good, every, early_miss]) == [1.0]                   # good's last rep is out; capped at 1
    assert IC.fit_fade_rows([good] * 19) is None and IC.fit_fade_rows([good] * 20).n == 20


# ---- the verdict reads the values in effect ------------------------------------------

SPEC = ("x", "4×4 分", 4, 4, 3, 1.04, 1.08, False, "")


def test_interval_outcome_uses_the_last_fade_in_effect(monkeypatch):
    bouts = [{"power": 280}, {"power": 279}, {"power": 278}, {"power": 262}]   # last −6.4 %, under 0.98 × 1.04 × 260
    assert QG.interval_outcome(bouts, SPEC, 260.0)["outcome"] == "border"
    _manual(monkeypatch, interval_last_fade=0.08)
    o = QG.interval_outcome(bouts, SPEC, 260.0)
    assert o["outcome"] == "met" and "≤ 8%" in o["why"] and "手動" in o["why"]


def test_interval_outcome_uses_the_tolerance_in_effect(monkeypatch):
    bouts = [{"power": 263}] * 4                                               # 0.973 × the lower bound
    assert QG.interval_outcome(bouts, SPEC, 260.0)["outcome"] == "too_high"
    _manual(monkeypatch, interval_in_band_tol=0.97)
    assert QG.interval_outcome(bouts, SPEC, 260.0)["outcome"] == "met"


def test_dose_step_uses_the_tiz_goal_in_effect(monkeypatch):
    h = [{"date": "2026-09-20", "title": QG.Z3[0][1], "bouts": [{"power": 240}] * 2, "cp": 260.0,
          "tiz_ratio": 0.82, "track": "z3"}]
    d = QG.dose_step([dict(x) for x in h])
    assert d["outcome"] == "border" and "< 85%" in d["note"] and "預設" in d["note"]
    _manual(monkeypatch, interval_tiz_goal=0.80)
    assert QG.dose_step([dict(x) for x in h])["outcome"] == "met"


# ---- lthr_test_age_days ----------------------------------------------------------------

def test_age_from_how_fast_the_lthr_moves():
    fast = [("2026-01-01", 160, "friel30"), ("2026-02-26", 165, "friel30"), ("2026-04-23", 160, "friel30")]
    assert TCAL.fit_age_rows(fast, TODAY).value == pytest.approx(56.0)        # 5 bpm in 56 days
    slow = [("2026-01-01", 160, "friel30"), ("2026-04-01", 161, "friel30"), ("2026-07-01", 160, "friel30")]
    assert TCAL.fit_age_rows(slow, TODAY).value == TCAL.AGE_BOUNDS[1]         # clipped at 24 weeks
    mixed = [("2026-01-01", 160, "friel30"), ("2026-03-01", 170, "race"), ("2026-05-01", 162, "friel30")]
    assert TCAL.fit_age_rows(mixed, TODAY) is None                            # one same-kind pair
    close = [("2026-01-01", 160, "friel30"), ("2026-01-10", 165, "friel30"), ("2026-01-20", 160, "friel30")]
    assert TCAL.fit_age_rows(close, TODAY) is None                            # < 4 weeks apart: noise


def test_fit_reads_only_tests_from_the_plan():
    from backend.engine.planning import Threshold
    ths = [Threshold(date="2026-01-01", lthr=160, lthr_method="friel30"),
           Threshold(date="2026-02-26", lthr=165, lthr_method="friel30"),
           Threshold(date="2026-03-15", lthr=150, lthr_method="estimate"),
           Threshold(date="2026-04-23", lthr=160, lthr_method="friel30")]
    f = TCAL.fit_lthr_test_age(SimpleNamespace(plan=SimpleNamespace(thresholds=ths)), TODAY)
    assert f.n == 2 and f.value == pytest.approx(56.0)


def test_the_age_hint_follows_the_value_in_effect(monkeypatch):
    lt = {"value": 160.0, "source_kind": "test", "date": "2026-06-23", "cp_at_date": None, "label": "x"}  # 100 days
    s = TC.event_signals(lt, TODAY, None)
    assert [x["id"] for x in s] == ["age"] and "56 天" in s[0]["text"] and "預設" in s[0]["text"]
    _manual(monkeypatch, lthr_test_age_days=120.0)
    assert TC.event_signals(lt, TODAY, None) == []


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


def test_overhard_reads_the_margin_and_says_whose():
    r = {"avg_hr": 154, "aet": 150, "over_aet_s": 600, "hr_s": 3000}
    assert A.overhard(36, {**r, "aet_margin": 5.0}) is None                   # 154 ≤ AeT + 5
    why = A.overhard(36, {**r, "avg_hr": 156, "aet_margin": 5.0, "aet_margin_basis": "本人 n=12"})
    assert why.startswith("平均心率 156 > AeT+5（155；餘裕 5 bpm，本人 n=12）")
    assert A.overhard(36, r).startswith("平均心率 154 > AeT+3（153），")      # no margin in the row: as before


def test_friel_band_upper_edge_follows_the_margin(monkeypatch):
    assert QG.friel_band() == QG.FRIEL_HR_BAND
    _manual(monkeypatch, easy_hr_margin_bpm=4.5)
    assert QG.friel_band() == (QG.FRIEL_HR_BAND[0], 4.5)
    assert TCAL.margin_fields() == {"aet_margin": 4.5, "aet_margin_basis": "手動"}
