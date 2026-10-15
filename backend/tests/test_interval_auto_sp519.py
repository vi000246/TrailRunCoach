"""輕鬆跑或長跑裡有一組像樣的用力段時，自動顯示間歇圖表 (SP-519).

The user (2026-10-14): ≥ 3 efforts of about the same length at threshold or above → the 間歇 page
shows its charts without 「當作間歇判讀」; a trail climb counts like any other effort. The set
(interval_reps.auto_reps): ≥ 3 reps at ≥ 95 % CP, the longest ≤ 1.5 × the shortest, an easier stretch
between each two (≤ 80 % of their power, ≤ 3 min or 3 × the rep) — the watch's laps first, else the
power pattern. Fewer reps, unequal lengths, under the line or no rest between → SP-513's one sentence.
Display only: the classification, quality_gate.dose_history (the ladder) and the plan match are not
touched. The real page: e2e/test_interval_auto_sp519_e2e.py."""
import datetime as dt

import numpy as np

from backend.engine import interval_eval as IE
from backend.engine import interval_reps as IR
from backend.engine import quality_gate as QG
from backend.engine import workout_review as WR
from backend.i18n import use_locale
from backend.tests.test_interval_eval import _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds
from backend.tests.test_workout_review import _run
from backend.tests.wko5_fakes import FakeWorkout

CP = 250.0                               # test_quality_gate._ds
OTHERS = ("interval_reps", "interval_power", "interval_tiz", "interval_hr", "intervals")
EASY = "這次不是間歇課（輕鬆跑，課表也沒有對應的間歇）"


def _set_run(day, works, on, rest_s=120, rest_p=120.0, base=175.0, trail=False, hr=140.0, warm_s=900):
    """`warm_s` (15′) easy @ `base`, the reps (`works` seconds each, `on` W — a number or one per rep)
    with `rest_s` at `rest_p` between, 5′ easy. A trail run (runningtrail, climbing) when `trail`."""
    ons = on if isinstance(on, (list, tuple)) else [on] * len(works)
    p = [base] * warm_s
    for k, (n, x) in enumerate(zip(works, ons)):
        p += [x] * n
        if k < len(works) - 1:
            p += [rest_p] * rest_s
    p += [base] * 300
    t = np.arange(len(p), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": [hr] * len(t), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    tags = ["running", "runningtrail"] if trail else ["running"]
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=tags,
                       sport_type="trail running" if trail else "running", channels=ch,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 250.0 if trail else 5.0})


def _page(ds, w) -> dict:
    return {sec: WR.review(ds, w, sec) for sec in ("interval_verdict",) + OTHERS}


def _shown(page: dict) -> list:
    return [sec for sec, r in page.items() if not r.get("hide")]


def _one(fw):
    ds = _ds([fw])
    return ds, ds.workouts[0]


# ---- 1. a set of ≥ 3 → the charts, without the button ----------------------------------------

def test_an_easy_run_with_three_reps_shows_the_interval_charts():
    ds, w = _one(_v1a_run(TODAY - dt.timedelta(days=3), reps=3))       # 3×2′ @ 109 % CP, 2′ walks
    assert WR.classify(ds, w)["type"] == "easy"
    e = IE.card_cached(ds, w)
    assert e["ok"] and e["auto"] and not e["flagged"] and e["kind"] == "detected" and not e.get("not_interval")
    assert len(e["reps"]) == 3 and e["rep_source"] == "auto"
    page = _page(ds, w)
    assert _shown(page) == ["interval_verdict"] + list(OTHERS)
    v = page["interval_verdict"]
    assert "action" not in v                                         # no 「當作間歇判讀」 to press
    assert v["badge"]["sub"].endswith("（自動判讀：這次算輕鬆跑，有 3 趟差不多長、≥ 95% CP 的用力段）")
    found = next(c for c in v["chip_rows"] if c["label"] == "找趟")
    assert "自動判讀只改這一頁的顯示" in found["tip"]
    assert len(page["interval_power"]["iv_trace"]["reps"]) == 3
    with use_locale("en"):
        en = WR.review(_ds([_v1a_run(TODAY - dt.timedelta(days=3), reps=3)]), w, "interval_verdict")
    assert en["badge"]["sub"].endswith(
        " (reviewed automatically: Easy run, with 3 efforts of about the same length at ≥ 95% CP)")


def test_a_one_minute_fartlek_is_found_by_its_power():
    # the recovery week's 4×1′ at 98–101 % CP (quality_gate.RECOVERY) with 2′ jogs, no laps
    ds, w = _one(_set_run(TODAY - dt.timedelta(days=2), [60] * 4, 250.0, rest_s=120, rest_p=150.0))
    assert WR.classify(ds, w)["type"] == "easy"
    e = IE.card_cached(ds, w)
    assert e["ok"] and e["auto"] and len(e["reps"]) == 4 and e["rep_source"] == "auto"


def test_thirty_second_reps_need_the_watch_laps():
    # 4×30″ @ 100 % CP / 30″ easy: under count_reps' 40 s, so only the laps (one per pushed step) find them
    fw = _set_run(TODAY - dt.timedelta(days=2), [30] * 4, 250.0, rest_s=30, rest_p=140.0)
    ds, w = _one(fw)
    assert IR.auto_reps(ds, w, WR._samples(ds, w), CP) is None
    assert IE.card_cached(ds, w)["not_interval"]
    ds, w = _one(fw)
    laps = [{"start_s": 0.0, "duration_s": 900.0, "power": 175.0}]
    for k in range(4):
        laps.append({"start_s": 900.0 + 60 * k, "duration_s": 30.0, "power": 250.0})
        if k < 3:
            laps.append({"start_s": 930.0 + 60 * k, "duration_s": 30.0, "power": 140.0})
    laps.append({"start_s": 1110.0, "duration_s": 300.0, "power": 175.0})
    ds.laps = lambda idx: laps
    e = IE.card_cached(ds, w)
    assert e["ok"] and e["auto"] and e["rep_source"] == "auto_lap" and [r["duration_s"] for r in e["reps"]] == [30.0] * 4
    v = WR.review(ds, w, "interval_verdict")
    assert next(c for c in v["chip_rows"] if c["label"] == "找趟")["chips"] == ["手錶分段（自動判讀）"]


# ---- 2. trail climbs count -----------------------------------------------------------------

def test_trail_climbs_at_the_intensity_count():
    # a trail run: 3 climbs of ~2.5′ at 104–108 % CP, the way down between (50 % CP)
    ds, w = _one(_set_run(TODAY - dt.timedelta(days=2), [150, 140, 160], [265.0, 260.0, 270.0], rest_s=150,
                          rest_p=125.0, trail=True))
    c = WR.classify(ds, w)
    assert c["type"] == "easy" and c["terrain"] == "trail"
    e = IE.card_cached(ds, w)
    assert e["ok"] and e["auto"] and len(e["reps"]) == 3
    assert "越野" not in IE.card_cached(ds, w).get("why", "")
    assert _shown(_page(ds, w)) == ["interval_verdict"] + list(OTHERS)


# ---- 3. not a set → SP-513's one sentence ----------------------------------------------------

def _not_auto(fw) -> dict:
    ds, w = _one(fw)
    assert WR.classify(ds, w)["type"] in ("easy", "long", "hard_long")
    e = IE.card_cached(ds, w)
    assert not e.get("ok") and e["not_interval"] and not e.get("auto")
    page = _page(ds, w)
    assert _shown(page) == ["interval_verdict"]
    return page["interval_verdict"]


def test_two_reps_are_not_a_set():
    v = _not_auto(_v1a_run(TODAY - dt.timedelta(days=3), reps=2))
    assert v["badge"]["text"] == "這次不是間歇課" and v["action"]["label"] == "當作間歇判讀"


def test_reps_of_very_different_length_are_not_a_set():
    # 1′, 2′ and 4′ at 109 % CP: no three of them within 1.5 ×
    v = _not_auto(_set_run(TODAY - dt.timedelta(days=2), [60, 120, 240], 272.0))
    assert "偵測到 3 趟" in v["series"][0]["data"]["value"]          # still offered by hand


def test_reps_under_the_threshold_are_not_a_set():
    # 3×3′ at 92 % CP (Zone 3, under 95 % CP)
    v = _not_auto(_set_run(TODAY - dt.timedelta(days=2), [180] * 3, 230.0))
    assert v.get("empty") == EASY or v["badge"]["text"] == "這次不是間歇課"


def test_surges_in_a_steady_run_are_not_a_set():
    # a steady run at 90 % CP with three 1′ surges at 104 % CP: nothing easier between them
    v = _not_auto(_set_run(TODAY - dt.timedelta(days=2), [60] * 3, 260.0, rest_s=150, rest_p=225.0, base=200.0,
                           hr=138.0))
    assert v["badge"]["text"] == "這次不是間歇課"


def test_climbs_far_apart_are_not_a_set():
    # three 2′ efforts with 15′ of easy running between: separate climbs, not reps
    _not_auto(_set_run(TODAY - dt.timedelta(days=2), [120] * 3, 272.0, rest_s=900, rest_p=150.0))


def test_a_run_without_power_keeps_its_one_sentence():
    ds, w = _one(_run(TODAY - dt.timedelta(days=2)))
    assert IR.auto_reps(ds, w, WR._samples(ds, w), CP) is None
    assert WR.review(ds, w, "interval_verdict")["empty"] == EASY


# ---- R1: the first 10 minutes do not count -----------------------------------------------

def test_efforts_in_the_first_ten_minutes_are_not_a_set():
    # the owner's 9/2: three surges in minutes 1–6 (the start, a traffic light), then a steady easy run
    fw = _set_run(TODAY - dt.timedelta(days=2), [50, 45, 60], [285.0, 270.0, 245.0], rest_s=90, rest_p=180.0,
                  warm_s=60)
    v = _not_auto(fw)
    assert v["badge"]["text"] == "這次不是間歇課"
    ds, w = _one(fw)
    assert IR.auto_reps(ds, w, WR._samples(ds, w), CP) is None


def test_the_first_ten_minutes_do_not_count_for_the_watch_laps_either():
    # 4×1′ laps from minute 2: the laps alone would make the set
    fw = _set_run(TODAY - dt.timedelta(days=2), [60] * 4, 260.0, rest_s=60, rest_p=140.0, warm_s=120)
    ds, w = _one(fw)
    laps = [{"start_s": 0.0, "duration_s": 120.0, "power": 175.0}]
    for k in range(4):
        laps.append({"start_s": 120.0 + 120 * k, "duration_s": 60.0, "power": 260.0})
        if k < 3:
            laps.append({"start_s": 180.0 + 120 * k, "duration_s": 60.0, "power": 140.0})
    ds.laps = lambda idx: laps
    assert IR.auto_reps(ds, w, WR._samples(ds, w), CP) is None
    assert IE.card_cached(ds, w)["not_interval"]


def test_a_set_from_minute_ten_counts_and_earlier_surges_are_left_out():
    # efforts from minute 2: 1′, 1′, then 4×2′ starting at 8′, 12′, 16′, 20′ — without R1 the set is
    # the four 2′ reps; the one starting at 8′ is left out now
    ds, w = _one(_set_run(TODAY - dt.timedelta(days=2), [60, 60, 120, 120, 120, 120], 272.0, warm_s=120))
    got = IR.auto_reps(ds, w, WR._samples(ds, w), CP)
    assert got is not None and [round(b["duration_s"] / 60) for b in got["bouts"]] == [2, 2, 2]
    assert all(b["start_s"] >= IR.AUTO_SKIP_START_S for b in got["bouts"])
    ds, w = _one(_set_run(TODAY - dt.timedelta(days=2), [120] * 3, 272.0, warm_s=600))   # first rep at 10:00
    got = IR.auto_reps(ds, w, WR._samples(ds, w), CP)
    assert got is not None and len(got["bouts"]) == 3 and got["bouts"][0]["start_s"] >= 600


# ---- 4. display only ---------------------------------------------------------------------

def test_the_auto_display_changes_no_count(monkeypatch):
    fw = _v1a_run(TODAY - dt.timedelta(days=3), reps=3)
    ds, w = _one(fw)
    assert IE.card_cached(ds, w)["auto"]
    # the ladder's history (dose_history: an unplanned run with < 4 short reps is no interval session)
    assert w.idx not in [h["idx"] for h in QG.dose_history(ds, TODAY)]
    assert WR.classify(ds, w)["type"] == "easy" and WR.classify(ds, w)["stimulus"] is None
    # and the same answers with the auto display off
    monkeypatch.setattr(IR, "auto_reps", lambda *a, **k: None)
    ds2, w2 = _one(_v1a_run(TODAY - dt.timedelta(days=3), reps=3))
    assert IE.card_cached(ds2, w2)["not_interval"]
    assert QG.dose_history(ds2, TODAY) == QG.dose_history(ds, TODAY)
    assert WR.classify(ds2, w2) == WR.classify(ds, w)


def test_the_mark_wins_over_the_auto_display(monkeypatch):
    monkeypatch.setattr(IE, "flagged", lambda w: True)
    ds, w = _one(_v1a_run(TODAY - dt.timedelta(days=3), reps=3))
    e = IE.card(ds, w)
    assert e["ok"] and e["flagged"] and not e["auto"]


def test_similar_set_and_the_rest_rule():
    b = lambda s, d, p=260.0: {"start_s": float(s), "duration_s": float(d), "power": p}     # noqa: E731
    assert [x["duration_s"] for x in IR.similar_set([b(0, 60), b(200, 300), b(600, 70), b(900, 85)])] == [60, 70, 85]
    pg = np.full(2000, 120.0)
    assert len(IR.best_set([b(0, 60), b(180, 60), b(360, 60)], pg)) == 3
    assert len(IR.best_set([b(0, 60), b(500, 60), b(1000, 60)], pg)) == 1      # 440 s apart > 3 min, > 3 × 60 s
    pg[:] = 230.0                                                              # 88 % of the reps: no rest
    assert len(IR.best_set([b(0, 60), b(180, 60), b(360, 60)], pg)) == 1
