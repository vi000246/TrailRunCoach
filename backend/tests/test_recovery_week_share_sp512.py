"""SP-512 (SP-69 item 4): the recovery week's volume share. The owner chose option C (2026-10-14):
no personalisation yet; personalising waits for ≥ 4 recovery weeks of results. R1 (2026-10-14): the
owner asked for the number most coaches use — of 11 verified coaches / approaches the mode and the
median cut is 30 %, so the share is 70 % (was 65 %); the TSB < −30 week stays at 60 %. Both are 推估
and their source text says so. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import statistics

import pytest

from backend.engine import load_guard as LG
from backend.engine import overview as O
from backend.engine import projection as P
from backend.tests.test_recovery_week import LIGHT, _week

NOTE_WORDS = ("沒有研究", "推估", "最常見減 30%", "Uphill Athlete", "70%／60%")


def test_the_shares_are_the_coaches_most_common_cut():
    # R1: the most common verified cut is 30 % (mode and median) → 70 % of the 3-week mean (was 0.65);
    # the TSB < −30 guard keeps its deeper 60 % of the 4-week mean
    assert O.RECOVERY_WEEK_SHARE == 0.70
    assert O.RECOVERY_WEEK_TSB_SHARE == 0.60


def test_the_source_line_no_longer_claims_a_coach_range_it_is_outside_of():
    # old SRC_31: "恢復週減 35%，教練建議減 20–35%；Friel / Uphill Athlete 常見做法" — read as if
    # Uphill Athlete backed −20–35 %; it says about half
    assert "推估" in O.SRC_31 and "沒有研究" in O.SRC_31
    assert "前 3 週平均的 70%" in O.SRC_31 and "最常見減 30%" in O.SRC_31 and "約減一半" in O.SRC_31
    assert "65%" not in O.SRC_31


def test_base_31_week_is_the_share_of_the_three_before_and_says_it_is_an_estimate():
    _, _, wp = _week("2027-06-05", {4: LIGHT})
    assert wp["phase"] == "base" and wp["mode"] == "recovery_week"
    h3 = [w["hours"] for w in wp["history"]][-3:]
    assert wp["target"]["hours"] == pytest.approx(O.RECOVERY_WEEK_SHARE * statistics.mean(h3))
    assert "連續 3 週加量" in wp["why"][-2] and "前 3 週平均的 70%" in wp["why"][-2]
    assert all(w in wp["why"][-1] for w in NOTE_WORDS)


def test_a_normal_week_has_no_source_note():
    _, _, wp = _week("2026-10-31", {1: LIGHT})               # 賽前第 5 週 right after a light week
    assert wp["mode"] == "specific"
    assert not any("沒有研究可依" in w for w in wp["why"])


def test_tsb_guard_week_is_the_tsb_share_of_the_4_week_mean(monkeypatch):
    from backend.tests import test_b2b as TB
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: {"date": "2026-08-03", "ctl": 0.0, "atl": 0.0})
    _, _, _, wp = TB._week(today=dt.date(2026, 8, 19), last_week="b2b", tph=1.2, b2b_tph=1.2)
    assert wp["load"]["tsb_today"] < -30 and wp["mode"] == "recovery_week"
    h4 = [w["hours"] for w in wp["history"]][-4:]
    assert wp["target"]["hours"] == pytest.approx(O.RECOVERY_WEEK_TSB_SHARE * statistics.mean(h4))
    assert any("< −30：改成恢復週（近 4 週的 60%）" in w for w in wp["why"])
    assert all(w in wp["why"][-1] for w in NOTE_WORDS)


def test_projection_uses_the_same_share():
    # projection.week_hours reads overview's share; the projected recovery weeks must match
    hist = [5.0, 5.5, 6.0, 6.5]
    h, mode, _ = P.week_hours("base", hist, [True, True, True], ctl0=40.0, r=50.0, cc=42.0, days_to_a=None)
    assert mode == "recovery_week"
    assert h == pytest.approx(O.RECOVERY_WEEK_SHARE * statistics.mean(hist[-3:]))
    assert h == pytest.approx(0.70 * 6.0)
    h, mode, _ = P.week_hours("specific", hist, [False] * 3, ctl0=40.0, r=50.0, cc=42.0, days_to_a=60,
                              recovery="賽前第 5 週")
    assert mode == "recovery_week" and h == pytest.approx(O.RECOVERY_WEEK_SHARE * statistics.mean(hist[-3:]))


def test_the_note_has_english():
    import json
    from pathlib import Path
    en = json.loads((Path(O.__file__).resolve().parents[1] / "i18n" / "locales" / "en.json").read_text("utf-8"))
    from backend.i18n import translate
    key = next(k for k in en if k.startswith("恢復週減多少沒有研究可依"))
    assert en[key] and "estimates" in en[key]
    out = translate(key, "en", share=O.RECOVERY_WEEK_SHARE, tsb_share=O.RECOVERY_WEEK_TSB_SHARE)
    assert "70% / 60%" in out and "30%" in out



def test_race_feasibility_uses_the_same_share():
    # race_feasibility.koop_run's projected recovery week is overview's share (it repeated 0.65 before R1):
    # 6 weeks at 10 h in Koop's window, the 4th a recovery week = 10 h × the share
    from backend.engine import race_feasibility as RF
    race = dt.date(2027, 6, 5)
    mon = RF.monday_of(race)
    weeks = [{"monday": mon - dt.timedelta(weeks=k), "builds": 0, "recovery": k == 5, "in_window": True}
             for k in range(8, 2, -1)]
    rec_h = 10.0 * O.RECOVERY_WEEK_SHARE
    assert rec_h == pytest.approx(7.0)
    assert RF.koop_run(10.0, weeks, race, (0, rec_h - 0.1, 3, 8))["best_run"] == 6    # meets it: no break
    assert RF.koop_run(10.0, weeks, race, (0, rec_h + 0.1, 3, 8))["best_run"] == 3    # below it: breaks the run
