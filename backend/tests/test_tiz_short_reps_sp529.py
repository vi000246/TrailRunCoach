"""短趟（30 秒以下）的目標區時間算法 (SP-529).

The time in the target zone (interval_eval.tiz_seconds) counted stretches of 10-s power in the band of
≥ 30 s (TIZ_RUN_S). The centred 10-s mean is in the band for only ~20 s of a 30-s rep, so 4×30″ on
target read 0 % and 未達到 (SP-519 圖 1; the 30/15 Zone 5 session too). Now a stretch counts from half the
shortest planned rep when the reps are under 1 min (tiz_run_s); reps of 1 min and more keep 30 s, and the
verdict's thresholds (TIZ goal 85 %, the in-band floor) are unchanged."""
import datetime as dt

import numpy as np

from backend.engine import interval_eval as IE
from backend.engine import interval_library as IL
from backend.engine import workout_review as WR
from backend.tests.test_interval_auto_sp519 import CP, _one, _set_run
from backend.tests.test_quality_gate import TODAY


def _run(works, on, rest_s, rest_p=130.0, warm=600, cool=300, sets=1, set_rest=0):
    """warm-up @ 70 %, `sets` × the reps (`works` s at `on` × CP, `rest_s` at `rest_p` W between), cool-down."""
    p = [0.7 * CP] * warm
    for k in range(sets):
        for i, n in enumerate(works):
            p += [on * CP] * n
            if i < len(works) - 1:
                p += [rest_p] * rest_s
        if k < sets - 1:
            p += [rest_p] * set_rest
    p += [0.62 * CP] * cool
    return np.arange(len(p), dtype=float), p


def test_thirty_second_reps_on_target_count_their_time():
    # the recovery week's fartlek, 4×30″ at 100 % CP (band 98–101 % CP), 30″ easy between
    t, p = _run([30] * 4, 1.0, 30)
    assert IE.tiz_seconds(t, p, CP, 0.98, 1.01) == 0.0                 # the old rule: no 30-s stretch
    got = IE.tiz_seconds(t, p, CP, 0.98, 1.01, rep_s=30)
    assert got >= 0.85 * 120, got                                      # ≈ the 2 minutes run in the band
    assert got <= 4 * 30 + 4 * 8, got                                  # never more than the reps (+ the edges' give-back)
    assert IE.tiz_run_s(30) == 15.0


def test_the_thirty_fifteen_session_counts_its_reps():
    v = IL.get("x3015")
    assert v is not None and min(v.works) == 30
    t, p = _run([30] * v.reps, 1.15, 15, sets=v.sets, set_rest=v.set_rest_s)
    assert IE.tiz_seconds(t, p, CP, v.lo, v.hi, True) == 0.0
    assert IE.tiz_seconds(t, p, CP, v.lo, v.hi, True, rep_s=min(v.works)) >= 0.85 * IL.tiz_s(v)


def test_reps_of_a_minute_or_more_are_unchanged():
    for works, rest in (([60] * 4, 120), ([120] * 5, 120), ([300] * 3, 180), ([90, 120, 90], 60)):
        t, p = _run(works, 1.0, rest)
        assert IE.tiz_run_s(min(works)) == IE.TIZ_RUN_S
        assert IE.tiz_seconds(t, p, CP, 0.98, 1.01, rep_s=min(works)) == IE.tiz_seconds(t, p, CP, 0.98, 1.01)
    assert IE.tiz_run_s(None) == IE.tiz_run_s(0) == IE.TIZ_RUN_S


def test_short_rests_inside_a_long_rep_still_need_thirty_seconds():
    # a 2′ rep: a 20-s stretch of it in the band (the rest under it) still does not count
    t, p = _run([120], 1.0, 0)
    p = list(p)
    for i in range(600, 700):
        p[i] = 0.9 * CP                                                 # the first 100 s under the band
    assert IE.tiz_seconds(t, p, CP, 0.98, 1.01, rep_s=120) == 0.0


def test_the_card_judges_thirty_second_reps_by_their_time_in_the_zone():
    # SP-519 圖 1: an easy run with 4×30″ watch laps at 100 % CP — 目標區時間 was 0 % → 未達到
    fw = _set_run(TODAY - dt.timedelta(days=2), [30] * 4, 250.0, rest_s=30, rest_p=140.0)
    ds, w = _one(fw)
    laps = [{"start_s": 0.0, "duration_s": 900.0, "power": 175.0}]
    for k in range(4):
        laps.append({"start_s": 900.0 + 60 * k, "duration_s": 30.0, "power": 250.0})
        if k < 3:
            laps.append({"start_s": 930.0 + 60 * k, "duration_s": 30.0, "power": 140.0})
    laps.append({"start_s": 1110.0, "duration_s": 300.0, "power": 175.0})
    ds.laps = lambda idx: laps
    e = IE.card_cached(ds, w)
    assert e["ok"] and e["auto"] and e["tiz_run_s"] == 15.0
    assert e["tiz_ratio"] >= 0.85, e["tiz_ratio"]
    assert e["verdict"] != "missed", e.get("why")
    v = WR.review(ds, w, "interval_verdict")
    row = next(r for r in v["chip_rows"] if r["label"] == "目標區時間")
    assert "連續 ≥ 15 秒才算" in row["tip"], row["tip"]
