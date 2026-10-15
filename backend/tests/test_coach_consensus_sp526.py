"""SP-526: the interval verdict and the LTHR retest reminder use the coaches' consensus, the same
for everyone (SP-518 Q1-1 / Q1-2 A; docs/research/coach-consensus-sp518/E-personalized.md):
  * time in the zone ≥ 80 % of the plan (was 85 %, fitted per athlete);
  * only the last rep missed:達標 while it fell ≤ Hunter Allen's limit for the rep length below the
    3rd rep (≤ 2 min 10 %, 3 min 8 %, 5 min 5 %, 10 min 4 %, ≥ 20 min 3 %; was 5 % below the 1st);
  * the LTHR retest hint after 56 days (was fitted to 8–24 weeks).
The three are no longer calibration items: not listed in 設定 › 自動估算的參數, not fitted, and a value
stored before (fitted or typed in) is not read."""
from __future__ import annotations

import datetime as dt

import pytest

from backend.engine import calibrate as CAL
from backend.engine import interval_calib as IC
from backend.engine import interval_eval as IE
from backend.engine import quality_gate as QG
from backend.engine import threshold_calib as TCAL
from backend.engine import threshold_confidence as TC
from backend.i18n import use_locale
from backend.settings import repository as R

OLD = {IC.FADE: 0.03, IC.TIZ: 0.90, TCAL.AGE: 168.0}        # what a fit / a manual value could have stored


@pytest.fixture(autouse=True)
def _stored_old_values(monkeypatch):
    """The retired keys still hold values (fitted or manual): none of them may change anything."""
    monkeypatch.setattr(CAL, "stored_entry",
                        lambda name, user_id=1: {"value": OLD[name], "source": "user"} if name in OLD else None)


def test_retired_items_are_gone_from_the_calibration_list():
    reg = CAL._registry()
    assert not set(IC.RETIRED) & set(reg) and TCAL.AGE not in reg
    assert IC.TOL in reg and TCAL.MARGIN in reg                     # the others stay per athlete
    for name in (*IC.RETIRED, TCAL.AGE):
        assert not R.known(CAL.key(name))                           # the settings layer: an unknown key
    for gone in ("last_fade", "tiz_goal", "fit_last_fade", "fit_tiz_goal"):
        assert not hasattr(IC, gone)
    assert not hasattr(TCAL, "lthr_test_age") and not hasattr(TCAL, "fit_lthr_test_age")


@pytest.mark.parametrize("rep_min,limit", [(0.5, 0.10), (1, 0.10), (2, 0.10), (3, 0.08), (4, 0.065), (5, 0.05),
                                           (7.5, 0.045), (10, 0.04), (15, 0.035), (20, 0.03), (40, 0.03),
                                           (None, 0.05)])
def test_last_fade_by_rep_length(rep_min, limit):
    assert QG.last_fade_for(rep_min) == pytest.approx(limit)


SPEC4 = ("x", "4×4 分", 4, 4, 3, 1.04, 1.08, False, "")              # floor = 0.98 × 1.04 × 260 = 265 W


def test_last_rep_is_judged_against_the_third():
    # last 255 W: −5.6 % of the 3rd (270), −12 % of the 1st (290); 4-min reps may fall 6.5 %
    o = QG.interval_outcome([{"power": 290}, {"power": 280}, {"power": 270}, {"power": 255}], SPEC4, 260.0)
    assert o["outcome"] == "met" and o["first_miss"] == 4 and o["last_fade"] == pytest.approx(0.065)
    assert "≤ 6%" in o["why"] or "≤ 7%" in o["why"]
    assert "教練共識" in o["why"] and "Allen" in o["why"]
    assert o["fade"] == pytest.approx(255 / 290 - 1)                 # the shown fade stays last ÷ first
    o = QG.interval_outcome([{"power": 290}, {"power": 280}, {"power": 270}, {"power": 250}], SPEC4, 260.0)
    assert o["outcome"] == "border"                                   # −7.4 % of the 3rd


def test_three_reps_use_the_first():
    spec = ("x", "3×10 分", 3, 10, 2, 0.95, 1.0, False, "")          # floor 242 W; 10-min reps: 4 %
    assert QG.interval_outcome([{"power": 250}, {"power": 249}, {"power": 241}], spec, 260.0)["outcome"] == "met"
    assert QG.interval_outcome([{"power": 250}, {"power": 249}, {"power": 238}], spec, 260.0)["outcome"] == "border"


def test_tiz_goal_is_80_percent_for_everyone():
    assert QG.TIZ_GOAL == IE.TIZ_GOAL == 0.80
    assert IE.verdict_of("met", 0.82) == "met" and IE.verdict_of("met", 0.79) == "partial"
    h = {"date": "2026-09-20", "title": QG.Z3[0][1], "bouts": [{"power": 240}] * 2, "cp": 260.0, "track": "z3"}
    assert QG.dose_step([{**h, "tiz_ratio": 0.82}])["outcome"] == "met"          # 85 % made this 部分達到
    d = QG.dose_step([{**h, "tiz_ratio": 0.78}])
    assert d["outcome"] == "border" and "< 80%" in d["note"] and "教練共識" in d["note"]


def test_retest_hint_after_56_days_whatever_was_stored():
    today = dt.date(2026, 10, 1)
    lt = {"value": 160.0, "source_kind": "test", "date": "2026-06-23", "cp_at_date": None, "label": "x"}  # 100 days
    s = TC.event_signals(lt, today, None)
    assert [x["id"] for x in s] == ["age"] and "56 天" in s[0]["text"] and "教練共識" in s[0]["text"]
    assert TC.TEST_AGE_DAYS == 56
    lt50 = {**lt, "date": (today - dt.timedelta(days=50)).isoformat()}
    assert TC.event_signals(lt50, today, None) == []


def test_english():
    with use_locale("en"):
        o = QG.interval_outcome([{"power": 290}, {"power": 280}, {"power": 270}, {"power": 255}], SPEC4, 260.0)
        assert "coach consensus" in o["why"] and "Allen" in o["why"]
        h = {"date": "2026-09-20", "title": QG.Z3[0][1], "bouts": [{"power": 240}] * 2, "cp": 260.0, "track": "z3",
             "tiz_ratio": 0.78}
        assert "80 % completion" in QG.dose_step([h])["note"]
        lt = {"value": 160.0, "source_kind": "test", "date": "2026-06-23", "cp_at_date": None, "label": "x"}
        assert "coach consensus: 8 weeks" in TC.event_signals(lt, dt.date(2026, 10, 1), None)[0]["text"]


def test_review_cards_say_80_not_the_old_85():
    import inspect
    from backend.engine import workout_review as WR
    src = inspect.getsource(WR)
    assert 'e.get("tiz_goal", 0.85)' not in src and "預設 85% 是 §C2" not in src
    assert "TrainingPeaks、intervals.icu、TrainerPlan 的「完成」都從 80% 起" in src
