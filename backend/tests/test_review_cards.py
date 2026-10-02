"""本次重點 / 飄移判讀 as small cards (workout_review `cards`, wko5_viewer drawReviewCards).
Synthetic data only (no WKO5 folder, no real DB)."""
import datetime as dt
from pathlib import Path

import numpy as np

from backend.engine import workout_review as R
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 9, 30)
STATIC = Path(__file__).resolve().parents[1] / "static"


def _ds(*ws):
    return FakeDataset(list(ws), TODAY, settings=SETTINGS)


def _by_id(cards):
    return {c.get("id"): c for c in cards}


def test_summary_cards_easy_run_has_stats_zones_and_verdicts():
    ds = _ds(_run(TODAY, minutes=52, hr=135.0, power=200.0))     # < 55 min: an easy run, not an AeT test
    r = R.review(ds, ds.workouts[0], "summary")
    C = _by_id(r["cards"])
    for k in ("time", "distance", "hr", "power", "zones", "drift", "intensity"):
        assert k in C, k
    assert C["time"]["kind"] == "stat" and C["time"]["value"] == "52:00"
    z = C["zones"]["zones"]
    assert [x["key"] for x in z] == ["low", "mid", "high"]
    assert abs(sum(x["share"] for x in z) - 1.0) < 1e-6 and all(x["seconds"] >= 0 for x in z)
    # constant HR, constant speed: no drift -> ✓, measured ≥ 40 min after the warm-up -> 測試級
    assert C["drift"]["kind"] == "status" and C["drift"]["level"] == "good" and C["drift"]["sub"] == "測試級"
    assert C["intensity"]["level"] == "good"
    # every status card carries its explanation for the ? icon, never inline
    assert all(c.get("tip") for c in r["cards"] if c["kind"] == "status")
    # the 「建議分頁」 row is gone; the text rows stay for the AI
    assert all(s["name"] != "建議分頁" for s in r["series"])
    assert r["suggested_dashboard"] == 1


def test_summary_intensity_card_warns_above_aet_plus_3():
    ds = _ds(_run(TODAY, minutes=50, hr=150.0))          # AeT 142.4: all of it above AeT+3
    C = _by_id(R.review(ds, ds.workouts[0], "summary")["cards"])
    assert C["intensity"]["level"] == "warn" and C["intensity"]["value"] == "100%"


def test_drift_card_refused_run_is_one_short_card():
    ds = _ds(_run(TODAY, minutes=28))                     # 18 min after the warm-up: below both tiers
    r = R.review(ds, ds.workouts[0], "aerobic")
    assert len(r["cards"]) == 1
    c = r["cards"][0]
    assert c["value"] == "不採用" and c["level"] == "na"
    assert c["sub"].startswith("太短") and len(c["sub"]) <= 10
    assert c["tip"]                                        # the full reason behind the ?


def test_drift_card_drifting_run_and_chips():
    ds = _ds(_run(TODAY, minutes=60, hr=130.0, hr_end=150.0, power=200.0))
    r = R.review(ds, ds.workouts[0], "aerobic")
    C = _by_id(r["cards"])
    d = C["drift"]
    assert d["label"] == "Pa:HR 飄移" and d["level"] in ("warn", "bad") and d["value"].endswith("%")
    assert C["other"]["kind"] == "chip" and C["other"]["text"].startswith("Pw:HR")
    # chips are a few words, never a paragraph
    assert all(len(c["text"]) <= 16 for c in r["cards"] if c["kind"] == "chip")
    pw = _by_id(R.review(ds, ds.workouts[0], "aerobic", basis="power")["cards"])
    assert pw["drift"]["label"] == "Pw:HR 飄移" and pw["other"]["text"].startswith("Pa:HR")


def test_short_reason_maps_the_refusals():
    assert R.short_reason({"reason": "有坡（越野或每公里爬升 ≥ 20 m），飄移數字不採用"}) == "有坡（越野）"
    assert R.short_reason({"reason": "中途停了 5:00（> 5%），飄移數字不採用"}) == "停太久"
    assert R.short_reason({"reason": "暖身後只有 12 分鐘（< 30 分，參考值也不採用），飄移不採用",
                           "measured_s": 750.0}) == "太短（12 分）"
    assert R.short_reason({"hot": True, "temp_c": 29.4, "reason": "…"}) == "太熱（29 °C）"


def test_strength_session_cards_only_time_and_hr():
    from backend.tests.wko5_fakes import FakeWorkout
    t = np.arange(0, 1801, 1.0)
    w = FakeWorkout(start=dt.datetime.combine(TODAY, dt.time(7)), sport="strength", tags=["strength"],
                    sport_type="strength_training", channels={"elapsedtime": list(t), "heartrate": [110.0] * len(t)},
                    metrics={"duration": 1800.0, "movingduration": 1800.0})
    ds = _ds(w)
    r = R.review(ds, ds.workouts[0], "summary")
    if r["empty"]:
        return                                              # no per-second speed: nothing to review
    kinds = {c.get("id") for c in r["cards"]}
    assert "drift" not in kinds and "intensity" not in kinds


def test_viewer_draws_cards_and_hides_the_text_rows():
    html = (STATIC / "wko5_viewer.html").read_text(encoding="utf-8")
    assert "function drawReviewCards(" in html and "res.cards?.length" in html
    assert "/api/v1/static/dashicons.js" in html
    assert (STATIC / "dashicons.js").exists()
    # dashboard description behind a ?, not a caption paragraph
    assert 'class="ddesc"' not in html and "ddesc-q" in html


def test_overview_is_a_dashboard():
    html = (STATIC / "overview.html").read_text(encoding="utf-8")
    for needle in ('id="kpis"', "function renderKpis(", "function stype(", 'class="week"', "data-suggest-hook",
                   "suggestions:changed", "/api/v1/static/dashicons.js"):
        assert needle in html, needle
    # every session type the week can show has a label (identity = icon + word)
    for t in ("easy", "z3", "z5", "long", "b2b", "loaded", "test", "rest"):
        assert f"{t}:" in html.split("const TYPES = {", 1)[1].split("};", 1)[0], t
