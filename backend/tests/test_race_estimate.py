"""用近期比賽成績推完賽時間 (SP-293, engine/racepower/race_estimate.py, POST /racepower/estimate): without a
CP the race calculator estimates from the shared race results — road Riegel k −0.07, trail
unsourced-rules §0.5.5 (effort km × the flat easy pace × 0.85), labelled 推估; with a CP the existing
model; the rows are race_results' one list (the questionnaire's and SP-276's). Results only / results
+ CP / neither. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import re

import pytest

from backend.engine import e_pace as EP
from backend.engine import race_results as RR
from backend.engine.racepower import race_estimate as RX

TODAY = dt.date(2026, 10, 5)
TEN_K = RR.make(10.0, 45 * 60, "2026-05-01", source="survey")               # 10 K 45:00 from the questionnaire


def test_road_from_results_only_is_riegel_and_labelled():
    est = RX.estimate("road", 21.0975, 0.0, [TEN_K], TODAY)
    assert est["available"] and est["method"] == "riegel" and est["k"] == -0.07
    assert est["time_s"] == pytest.approx(2700 * (21.0975 / 10.0) ** (1 / 0.93), abs=1)       # ≈ 1:40:26
    assert est["label"] == "推估" and "Riegel" in est["text"] and "推估" in est["text"]
    assert "跑步經驗問卷填的" in est["text"] and "10 K 45:00（2026-05-01）" in est["text"]
    assert est["note"] is None


def test_trail_keeps_the_effort_km_rule_of_0_5_5():
    est = RX.estimate("trail", 21.0, 1200.0, [TEN_K], TODAY)
    e = EP.of_race({"distance_m": 10000.0, "time_s": 2700, "date": "2026-05-01", "source": "survey"}, TODAY)
    pace = (e["e_fast"] + e["e_slow"]) / 2
    assert est["available"] and est["method"] == "effort_pace" and est["effort_km"] == 33.0
    assert est["time_s"] == pytest.approx(33.0 * pace * 0.85, abs=1)
    assert "等效公里 33.0" in est["text"] and "× 0.85" in est["text"] and "推估" in est["text"]
    assert RX.estimate("baiyue", 10.0, 900.0, [TEN_K], TODAY)["available"] is False


def test_neither_results_nor_cp_says_where_to_add_a_race():
    for rows in ([], None, [RR.make(30.0, 4 * 3600, "2026-05-01", trail=True, source="manual")],      # trail only
                 [RR.make(10.0, 2700, "2025-01-01", source="manual")],                                # > 365 days
                 [RR.make(10.0, 2700, "2026-05-01", source="activity", confirmed=False)]):           # unconfirmed
        est = RX.estimate("road", 21.1, 0.0, rows, TODAY)
        assert est["available"] is False and "跑步經驗" in est["reason"]


def test_one_shared_list_newest_road_race_wins():
    """SP-276 × SP-290: the questionnaire's row and a 設定 / confirmed-activity row live in ONE list
    (race_results); the estimate and the E pace read the same newest road race."""
    rows = RR.with_survey([], TEN_K)
    newer = {"distance_m": 5000.0, "time_s": 21 * 60, "date": "2026-09-01", "source": "manual"}
    rows = EP.with_race(rows, newer)                                   # the 設定 block's 「比賽成績（E 配速）」
    assert len(rows) == 2 and RR.survey_entry(rows) == TEN_K
    est = RX.estimate("road", 10.0, 0.0, rows, TODAY)
    assert est["race"]["date"] == "2026-09-01" and est["race"]["distance_km"] == 5.0
    assert EP.pick(rows)["date"] == est["race"]["date"]                 # the E pace's race
    assert "設定裡填的" in est["text"]


def test_marathon_with_a_low_self_reported_week_is_optimistic():
    assert RX.estimate("road", 42.195, 0.0, [TEN_K], TODAY, survey_h=2.0)["note"].startswith("這個估法對週跑量少的人偏樂觀")
    assert RX.estimate("road", 42.195, 0.0, [TEN_K], TODAY, survey_h=5.0)["note"] is None
    assert RX.estimate("road", 21.1, 0.0, [TEN_K], TODAY, survey_h=2.0)["note"] is None
    assert RX.estimate("road", 42.195, 0.0, [TEN_K], TODAY)["note"] is None           # no self-report


def _api(monkeypatch, cp_default=None, rows=(TEN_K,), survey_h=None):
    from backend.api import racepower as A
    from backend.engine import race_feasibility as F
    monkeypatch.setattr(A, "inputs", lambda refresh=False: {"cp": {"default": cp_default,
                                                                   "sources": [{"id": "test"}] if cp_default else []}})
    monkeypatch.setattr(RR, "load", lambda user_id=1: list(rows))
    monkeypatch.setattr(F, "survey_hours", lambda exp=None, load=True: survey_h)
    monkeypatch.setattr(A, "today_local", lambda: TODAY)
    return A


def test_api_results_only_results_and_cp_and_neither(monkeypatch):
    from backend.engine.racepower.calc import PlanIn
    body = PlanIn(type="road", distance_km=21.0975)
    A = _api(monkeypatch)
    est = A.estimate(body)                                             # results only: the estimate
    assert est["available"] and est["time_s"] == pytest.approx(2700 * 2.10975 ** (1 / 0.93), abs=1)
    A = _api(monkeypatch, cp_default="test")                           # results + CP: the existing model
    assert A.estimate(body) == {"available": False, "model": True}
    A = _api(monkeypatch)
    assert A.estimate(PlanIn(type="road", distance_km=21.0975, cp=250.0))["model"]   # a CP typed in
    A = _api(monkeypatch, rows=())                                     # neither: why, nothing guessed
    got = A.estimate(body)
    assert got["available"] is False and got["reason"]
    A = _api(monkeypatch, survey_h=2.0)
    assert A.estimate(PlanIn(type="road", distance_km=42.195))["note"]


def test_page_falls_back_to_the_estimate_only_without_a_model():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "static" / "racepower.html").read_text("utf-8")
    assert "post(`${API}/estimate`, b)" in page and "function renderEstimate(est)" in page
    assert 'e.status === 400 && b.type !== "baiyue"' in page


def test_texts_are_translated():
    from backend.i18n import use_locale
    with use_locale("en"):
        outs = [RX.estimate("road", 42.195, 0.0, [TEN_K], TODAY, survey_h=2.0),
                RX.estimate("trail", 21.0, 1200.0, [TEN_K], TODAY), RX.estimate("road", 10.0, 0.0, [], TODAY),
                RX.estimate("baiyue", 10.0, 0.0, [TEN_K], TODAY)]
        for o in outs:
            for k in ("title", "tile", "label", "text", "note", "reason"):
                assert not re.search("[一-鿿]", o.get(k) or ""), (k, o.get(k))
