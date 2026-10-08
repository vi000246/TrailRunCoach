"""本次重點's 「刺激 TIS」 tile (workout_review._tis_card, SP-81): this activity's aerobic /
anaerobic TIS from the evaluator's built-ins, hidden without power, 「算不出」 without a
PD model, and hidden by the viewer when 使用功率 is off. Synthetic data only."""
import datetime as dt
import math
from pathlib import Path

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.engine.wko5expr import evaluator as E
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.test_wko5expr_functions import model_curve
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 9, 30)
STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(autouse=True)
def pd_curve(monkeypatch):
    # the PD fit sees a known curve (the synthetic samples are too short to fit one), as test_tis_charts
    monkeypatch.setattr(E.Evaluator, "_workout_curve", lambda self, node, w: model_curve())


def _cards(ds, i=0):
    return {c.get("id"): c for c in R.review(ds, ds.workouts[i], "summary")["cards"]}


def _intervals(minutes=50):
    n = minutes * 60 + 1
    return np.where((np.arange(n) // 120) % 2 == 0, 380.0, 150.0)       # 2′ on / 2′ off


def test_tis_tile_with_power_matches_the_evaluator():
    ds = FakeDataset([_run(TODAY - dt.timedelta(days=2), minutes=52, power=200.0),
                      _run(TODAY, minutes=50, power=_intervals())], TODAY, settings=SETTINGS)
    for i in (0, 1):
        w = ds.workouts[i]
        c = _cards(ds, i)["tis"]
        d = math.floor(w.day)
        a = E.Evaluator(ds, d, d).evaluate("tisaerobic", w)
        an = E.Evaluator(ds, d, d).evaluate("tisanaerobic", w)
        assert 1 <= a <= 10 and 1 <= an <= 10
        assert R.tis_scores(ds, w) == (a, an)
        assert c["kind"] == "stat" and c["value"] == f"{a:.0f}／{an:.0f}" and c["power"] is True
        assert c["sub"] == "有氧／無氧（1–10）"
        assert f"有氧 TIS {a:.0f}、無氧 TIS {an:.0f}" in c["tip"] and "怎麼看：" in c["tip"] and "方法：" in c["tip"]
    # the steady run below threshold has no anaerobic stimulus; the intervals do
    assert R.tis_scores(ds, ds.workouts[0])[1] == 1
    assert R.tis_scores(ds, ds.workouts[1])[1] > 1


def test_no_tis_tile_without_power():
    ds = FakeDataset([_run(TODAY, minutes=52)], TODAY, settings=SETTINGS)
    assert R.tis_scores(ds, ds.workouts[0]) is None
    C = _cards(ds)
    assert "tis" not in C and "power" not in C            # like 平均功率: no power, no tile
    assert "time" in C and "hr" in C


def test_tis_tile_without_a_pd_model_says_so(monkeypatch):
    ds = FakeDataset([_run(TODAY, minutes=52, power=200.0)], TODAY, settings=SETTINGS)
    monkeypatch.setattr(R, "tis_scores", lambda ds, w: (None, None))
    c = _cards(ds)["tis"]
    assert c["value"] == "–" and c["sub"] == "算不出" and "90 天" in c["tip"]


def test_tis_tile_no_garmin_style_level_names():
    ds = FakeDataset([_run(TODAY, minutes=52, power=200.0)], TODAY, settings=SETTINGS)
    c = _cards(ds)["tis"]
    for word in ("Recovery", "Maintaining", "Improving", "Highly", "Overreaching", "恢復", "維持", "提升", "過量"):
        assert word not in c["value"] + c["sub"] + c["tip"], word


def test_tis_tile_english():
    from backend.i18n import use_locale
    ds = FakeDataset([_run(TODAY, minutes=52, power=200.0)], TODAY, settings=SETTINGS)
    with use_locale("en"):
        c = _cards(ds)["tis"]
    assert c["label"] == "TIS stimulus" and c["sub"] == "aerobic / anaerobic (1–10)"
    assert "How to read:" in c["tip"] and not any("一" <= ch <= "鿿" for ch in c["tip"])


def test_avg_power_tile_is_a_power_tile_too():
    # SP-81 follow-up (2026-10-07): 「使用功率」 off hides 平均功率 like 刺激 TIS — both carry `power`
    ds = FakeDataset([_run(TODAY, minutes=52, power=200.0)], TODAY, settings=SETTINGS)
    C = _cards(ds)
    assert C["power"]["power"] is True and C["tis"]["power"] is True
    # the other stat tiles are not power tiles: they stay when 使用功率 is off
    for k in ("time", "hr"):
        assert "power" not in C[k], k
    hidden = [c["id"] for c in C.values() if c.get("power")]
    assert sorted(hidden) == ["power", "tis"]


def test_tis_tile_has_no_typical_value_comparison():
    # SP-81 follow-up (2026-10-07, 「先不要」): no comparison with the 90-day typical value
    ds = FakeDataset([_run(TODAY, minutes=52, power=200.0)], TODAY, settings=SETTINGS)
    c = _cards(ds)["tis"]
    for word in ("中位數", "典型", "平常值", "median", "typical"):
        assert word not in c["value"] + c["sub"] + c["tip"], word


def test_viewer_hides_power_tiles_when_power_is_off():
    html = (STATIC / "wko5_viewer.html").read_text(encoding="utf-8")
    body = html.split("function drawReviewCards(", 1)[1].split("\n}", 1)[0]
    assert "S.usePower !== false || !c.power" in body
