"""間歇判讀 (engine/interval_eval.py) and its single-workout cards — synthetic streams only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import interval_eval as IE
from backend.engine import interval_library as IL
from backend.engine import quality_gate as QG
from backend.engine import workout_review as WR
from backend.tests.test_quality_gate import TODAY, _ds, _z3_run
from backend.tests.test_workout_review import _run
from backend.tests.wko5_fakes import FakeWorkout

CP = 250.0


def _v1a_run(day, reps=5, on=272.0, rest_p=120.0):
    """V1 5×2′ @ 109 % CP with 2′ walks, std blocks: 15′ warm-up @ 70 %, 5′ cool-down."""
    p = [175.0] * 900
    for k in range(reps):
        p += [on] * 120
        if k < reps - 1:
            p += [rest_p] * 120
    p += [150.0] * 300
    t = np.arange(len(p), dtype=float)
    hr = np.full(len(t), 140.0)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 5.0})


def _planned(ds, key, reps=None):
    ds.plan_rows = {w.idx: {"title": IL.title(IL.get(key)), "variant_key": key, "rung_key": IL.get(key).rung,
                            "equiv": True, "variant_reps": reps, "state": "done"} for w in ds.workouts}
    return ds


def test_a_full_session_meets_its_goal():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    e = IE.evaluate(ds, ds.workouts[0])
    assert e["ok"] and len(e["reps"]) == 5 and e["hit"] == 5 and e["outcome"] == "met"
    assert e["tiz_plan_s"] == 600 and e["tiz_ratio"] == pytest.approx(1.0, abs=0.05)
    assert e["verdict"] == "met" and e["verdict_label"] == "達到訓練目標"
    # dFRC: 5 × 120 s × 22 W above CP drains it; the battery never goes up past 100 %
    assert 0.5 < e["dfrc_min_pct"] < 1.0 and max(e["series"]["dfrc_pct"]) <= 1.0 + 1e-9
    assert e["wprime_used_j"] == pytest.approx(5 * 120 * 22, rel=0.02)
    assert e["sdec"] == pytest.approx(0.0, abs=0.01)


def test_stopping_early_is_judged_against_the_chosen_variant():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3), reps=3)]), "v1a")
    e = IE.evaluate(ds, ds.workouts[0])
    assert e["outcome"] == "unadapted" and e["verdict"] == "missed" and e["tiz_ratio"] == pytest.approx(0.6, abs=0.05)
    # a shorter equivalent planned as such (v1b 4×2:30) is judged against its own 10′, not v1a's
    assert IE.verdict_of("met", 0.9) == "met" and IE.verdict_of("met", 0.7) == "partial"
    assert IE.verdict_of("border", 1.0) == "partial" and IE.verdict_of("met", 0.5) == "missed"


def test_the_verdict_moves_the_ladder_only_with_enough_time_in_zone():
    good = [{"power": 240.0}] * 3
    h = {"bouts": good, "cp": CP, "variant_key": "t1a", "rung_key": "z3a", "equiv": True, "tiz_ratio": 0.7}
    d = QG.dose_step([h])
    assert h["outcome"] == "border" and d["step"] == 0 and "目標區時間只有計畫的 70%" in d["note"]
    assert QG.dose_step([{**h, "tiz_ratio": 0.95}])["step"] == 1


def test_the_cards_render_and_hide_on_easy_runs():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    w = ds.workouts[0]
    v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["text"] == "達到訓練目標" and v["badge"]["level"] == "good"
    reps = WR.review(ds, w, "interval_reps")
    names = [s["name"] for s in reps["series"]]
    assert names == ["目標帶", "✓ 達標", "✕ 沒到"] and reps["series"][1]["labels"][0].startswith("✓ ")
    pw = WR.review(ds, w, "interval_power")
    hl = [s for s in pw["series"] if s["data"]["kind"] in ("hline", "band")]
    assert len(hl) == 2                                       # CP and the target band only
    bat = WR.review(ds, w, "interval_battery")
    assert bat["series"][-1]["labels"][0].startswith("最低 ")
    tiz = WR.review(ds, w, "interval_tiz")
    assert [s["data"]["points"][0][1] for s in tiz["series"]][0] == 600
    easy = _ds([_run(TODAY - dt.timedelta(days=2), power=150.0)])
    assert WR.review(easy, easy.workouts[0], "interval_reps").get("hide") is True


def test_hr_at_matched_power_compares_like_with_like():
    days = [TODAY - dt.timedelta(days=d) for d in (20, 13, 6)]
    ds = _planned(_ds([_z3_run(d) for d in days]), "t2a")
    e = IE.evaluate(ds, ds.workouts[-1])
    assert [p["current"] for p in e["peers"]] == [False, False, True]
    r = WR.review(ds, ds.workouts[-1], "interval_hr")
    assert [s["name"] for s in r["series"]][:2] == ["之前的同類間歇", "這次"]
