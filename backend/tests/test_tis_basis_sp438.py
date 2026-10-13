"""SP-438: the 「刺激 TIS」 tile's ? names the mFTP, FRC and threshold this activity's TIS was
computed with (the 90-day PD model fit of the same sport, not the settings' CP), so a
high anaerobic score can be told apart from an underestimated FRC (SP-413). Only the
explanation changes — the TIS numbers stay what they were. Synthetic data only."""
import datetime as dt
import math

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.engine.wko5expr import evaluator as E
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.test_wko5expr_functions import model_curve
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 9, 30)
FTP_SUB = "sport(sport).athleterange(date-@lookback+1,date,ftp(meanmax(power)))"
FRC_SUB = "sport(sport).athleterange(date-@lookback+1,date,frc(meanmax(power)))"


@pytest.fixture(autouse=True)
def pd_curve(monkeypatch):
    # the PD fit sees a known curve (the synthetic samples are too short to fit one), as test_review_tis_card
    monkeypatch.setattr(E.Evaluator, "_workout_curve", lambda self, node, w: model_curve())


def _intervals(minutes=50):
    n = minutes * 60 + 1
    return np.where((np.arange(n) // 120) % 2 == 0, 380.0, 150.0)       # 2′ on / 2′ off


def _ds():
    return FakeDataset([_run(TODAY - dt.timedelta(days=4), minutes=52, power=200.0),     # easy, below threshold
                        _run(TODAY - dt.timedelta(days=2), minutes=40, power=230.0),     # steady just above it
                        _run(TODAY, minutes=50, power=_intervals())], TODAY, settings=SETTINGS)


def _tis_card(ds, i):
    return {c.get("id"): c for c in R.review(ds, ds.workouts[i], "summary")["cards"]}["tis"]


# the TIS of these three workouts before SP-438 (integrate/2026-10-12a, dbf816c6) — they must not move
GOLDEN = [(3.0, 1.0), (3.0, 3.0), (3.0, 10.0)]


def test_tis_numbers_are_unchanged():
    ds = _ds()
    assert [R.tis_scores(ds, w) for w in ds.workouts] == GOLDEN
    assert [_tis_card(ds, i)["value"] for i in range(3)] == [f"{a:.0f}／{an:.0f}" for a, an in GOLDEN]


def test_reading_the_basis_first_does_not_move_the_tis():
    # tis_basis shares the evaluator's memo with the score: reading it first must leave the score alone
    ds = _ds()
    for w in ds.workouts:
        R.tis_basis(ds, w)
    assert [R.tis_scores(ds, w) for w in ds.workouts] == GOLDEN


def test_the_basis_expressions_are_the_builtins_own():
    # the mFTP / FRC the tile shows are read with the very sub-expressions WKO5's TIS uses
    assert FTP_SUB in E.BUILTIN_EXPRS["tisaerobic"] and FTP_SUB in E.BUILTIN_EXPRS["tisanaerobic"]
    assert FRC_SUB in E.BUILTIN_EXPRS["tisanaerobic"]
    assert FTP_SUB in R.TIS_BASIS_EXPRS["mftp"] and FRC_SUB in R.TIS_BASIS_EXPRS["frc"]
    assert "*.85" in E.BUILTIN_EXPRS["tisanaerobic"] and R.TIS_THRESHOLD_SHARE == 0.85


def test_the_basis_is_what_the_score_was_computed_with():
    # put the shown numbers into the built-in formula as constants: the same TIS comes out
    ds = _ds()
    for w in ds.workouts:
        b = R.tis_basis(ds, w)
        assert b["mftp"] == pytest.approx(250.0) and b["frc"] == pytest.approx(15.0)
        assert b["threshold"] == pytest.approx(0.85 * b["mftp"])
        d = math.floor(w.day)
        for name in ("tisaerobic", "tisanaerobic"):
            expr = (E.BUILTIN_EXPRS[name].replace(FTP_SUB, repr(b["mftp"])).replace(FRC_SUB, repr(b["frc"])))
            assert "athleterange" not in expr
            assert E.Evaluator(ds, d, d).evaluate(expr, w) == E.Evaluator(ds, d, d).evaluate(name, w), name


def test_tip_names_this_activitys_mftp_frc_and_threshold():
    ds = _ds()
    for i, w in enumerate(ds.workouts):
        b = R.tis_basis(ds, w)
        tip = _tis_card(ds, i)["tip"]
        line = tip.split("\n")[1]
        assert line.startswith("這次用的：")
        assert f"mFTP {b['mftp']:.0f} W" in line and f"FRC {b['frc']:.1f} kJ" in line
        assert f"門檻 {b['threshold']:.0f} W（mFTP 的 85%）" in line
        assert "不是設定裡的 CP" in line and "90 天" in line
        # the old explanation is still there after it
        assert "怎麼看：" in tip and "方法：" in tip and tip.startswith("有氧 TIS ")


def test_tip_english():
    from backend.i18n import use_locale
    ds = _ds()
    with use_locale("en"):
        tip = _tis_card(ds, 0)["tip"]
    line = tip.split("\n")[1]
    assert line.startswith("Used this time:")
    # the fit gives 250.00000000000003 W → threshold 212.50000000000003 W, shown as 213
    assert "mFTP 250 W" in line and "FRC 15.0 kJ" in line and "threshold 213 W (85% of mFTP)" in line
    assert "not the CP in Settings" in line
    assert not any("一" <= ch <= "鿿" for ch in tip)


def test_tip_english_when_the_pd_model_gives_na(monkeypatch):
    from backend.i18n import use_locale
    ds = _ds()
    monkeypatch.setattr(R, "tis_basis", lambda ds, w:
                        {"mftp": None, "frc": 15.0, "threshold": None})
    with use_locale("en"):
        line = _tis_card(ds, 0)["tip"].split("\n")[1]
    assert line.startswith("Used this time: mFTP can't be computed, FRC 15.0 kJ, threshold can't be computed")
    assert not any("一" <= ch <= "鿿" for ch in line)


def test_tip_says_when_frc_cannot_be_fitted(monkeypatch):
    ds = _ds()
    monkeypatch.setattr(R, "tis_basis", lambda ds, w: {"mftp": 250.0, "frc": None, "threshold": 212.5})
    line = _tis_card(ds, 0)["tip"].split("\n")[1]
    assert "mFTP 250 W" in line and "門檻 212 W" in line and "FRC 算不出" in line


def test_no_basis_line_when_the_basis_cannot_be_read(monkeypatch):
    ds = _ds()
    before = _tis_card(ds, 0)["tip"]
    monkeypatch.setattr(R, "tis_basis", lambda ds, w: None)
    tip = _tis_card(_ds(), 0)["tip"]
    assert "這次用的" not in tip and tip.startswith("有氧 TIS 3、無氧 TIS 1")
    assert tip.split("\n", 1)[1] == before.split("\n", 2)[2]          # the rest as before


def test_no_basis_line_on_the_cannot_compute_tile(monkeypatch):
    ds = _ds()
    monkeypatch.setattr(R, "tis_scores", lambda ds, w: (None, None))
    c = _tis_card(ds, 0)
    assert c["value"] == "–" and "這次用的" not in c["tip"]


def test_no_basis_without_power():
    ds = FakeDataset([_run(TODAY, minutes=52)], TODAY, settings=SETTINGS)
    assert R.tis_basis(ds, ds.workouts[0]) is None
