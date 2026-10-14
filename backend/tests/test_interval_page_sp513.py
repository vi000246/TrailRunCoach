"""不是間歇課時，間歇頁整頁一句話 (SP-513).

An easy run's 間歇 page kept three cards (間歇判讀, 功率／無氧儲備與心率, 每組) each saying 「這不是間歇課」
in its own words, with the other three taken off by rules of their own. Now a run that isn't an interval
session (interval_eval.card's `not_interval`: not classified quality / a test, not marked 「當作間歇」, no
planned interval structure) gets one sentence in 間歇判讀 — with the 「當作間歇判讀」 offer when bouts were
found — and every other card of the page is off. An interval session, a test, a marked run, a quality
session without reps and a planned session without power keep what they had. The real page is checked in
e2e/test_interval_page_sp513_e2e.py."""
import datetime as dt

import numpy as np

from backend.engine import interval_eval as IE
from backend.engine import workout_review as WR
from backend.i18n import use_locale
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds, _z3_run
from backend.tests.test_workout_review import _run

# views/workout.json 間歇: the cards besides 間歇判讀
OTHERS = ("interval_reps", "interval_power", "interval_tiz", "interval_hr", "intervals")
EASY = "這次不是間歇課（輕鬆跑，課表也沒有對應的間歇）"


def _page(ds, w) -> dict:
    return {sec: WR.review(ds, w, sec) for sec in ("interval_verdict",) + OTHERS}


def _shown(page: dict) -> list:
    return [sec for sec, r in page.items() if not r.get("hide")]


def test_an_easy_run_is_one_sentence():
    ds = _ds([_run(TODAY - dt.timedelta(days=2), power=150.0)])
    w = ds.workouts[0]
    assert IE.card_cached(ds, w)["not_interval"]
    page = _page(ds, w)
    assert _shown(page) == ["interval_verdict"]
    v = page["interval_verdict"]
    assert v["empty"] == EASY and "badge" not in v and "action" not in v
    # the W′ battery isn't lost: 本次重點's card stays
    assert not WR.review(ds, w, "wprime_battery").get("hide")


def test_an_easy_run_with_bouts_keeps_the_offer():
    # 2×2′ @ 109 % with an easy average HR: not quality, 2 bouts found → 「當作間歇判讀」 (3 such reps
    # show the 間歇 page by themselves since SP-519: test_interval_auto_sp519.py)
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3), reps=2)])
    w = ds.workouts[0]
    page = _page(ds, w)
    assert _shown(page) == ["interval_verdict"]
    v = page["interval_verdict"]
    assert v["badge"] == {"text": "這次不是間歇課", "level": "", "sub": "輕鬆跑"}
    assert len(v["series"]) == 1 and v["series"][0]["data"]["value"] == "偵測到 2 趟用力段，可以當作間歇判讀"
    assert v["action"]["label"] == "當作間歇判讀"


def test_an_easy_run_without_power_says_not_an_interval_session():
    ds = _ds([_run(TODAY - dt.timedelta(days=2))])
    w = ds.workouts[0]
    page = _page(ds, w)
    assert _shown(page) == ["interval_verdict"] and page["interval_verdict"]["empty"] == EASY
    with use_locale("en"):
        en = WR.review(_ds([_run(TODAY - dt.timedelta(days=2))]), w, "interval_verdict")
    assert en["empty"] == "Not an interval session (Easy run, and the plan has no matching intervals)"


def test_a_hard_long_run_says_why_in_the_one_sentence():
    w = _z3_run(TODAY - dt.timedelta(days=4))
    p = [175.0] * 2700 + list(w.channels["power"])
    t = np.arange(len(p), dtype=float)
    w.channels.update({"elapsedtime": list(t), "power": p, "heartrate": [150.0] * len(t), "speed": [10.0] * len(t),
                       "elapseddistance": list(t * 10 / 3600)})
    w.metrics.update({"duration": float(len(t)), "movingduration": float(len(t)), "distance": len(t) / 360.0})
    ds = _ds([w])
    w = ds.workouts[0]
    assert WR.classify(ds, w)["type"] == "hard_long"
    page = _page(ds, w)
    assert _shown(page) == ["interval_verdict"]
    v = page["interval_verdict"]
    why = "這次不是間歇課（高強度長跑：有閾值強度，算硬課、不算間歇次數）"
    # its 3×8′ are found as bouts: the offer, with the hard-day reason behind the row's ?
    assert v["badge"] == {"text": "這次不是間歇課", "level": "", "sub": "高強度長跑"} and not v.get("empty")
    assert len(v["series"]) == 1 and v["series"][0]["data"]["tip"].startswith(why + "\n") and v["action"]


def test_not_a_run_is_one_sentence_too():
    w = _run(TODAY - dt.timedelta(days=2), power=150.0)
    w.sport = "bike"
    ds = _ds([w])
    w = ds.workouts[0]
    page = _page(ds, w)
    assert _shown(page) == ["interval_verdict"] and page["interval_verdict"]["empty"] == "不是跑步：不判讀間歇"


def test_an_interval_session_keeps_every_card():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    w = ds.workouts[0]
    assert not IE.card_cached(ds, w).get("not_interval")
    page = _page(ds, w)
    assert page["interval_verdict"]["badge"]["text"] == "達到訓練目標"
    # 同功率下的心率 has no peers on one activity, but it is there (says so); every card shows
    assert _shown(page) == ["interval_verdict"] + list(OTHERS)
    assert len(page["interval_power"]["iv_trace"]["reps"]) == 5 and page["intervals"]["series"]


def test_an_unplanned_quality_session_keeps_every_card():
    # 5×2′ @ 109 %: Z5 間歇 by its power (classify quality), evaluated on the detected bouts
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3))])
    w = ds.workouts[0]
    assert WR.classify(ds, w)["type"] == "quality" and IE.card_cached(ds, w)["ok"]
    assert "interval_power" in _shown(_page(ds, w)) and "intervals" in _shown(_page(ds, w))


def test_a_planned_session_without_power_keeps_the_power_reason():
    ds = _planned(_ds([_run(TODAY - dt.timedelta(days=2))]), "v1a")
    w = ds.workouts[0]
    e = IE.card_cached(ds, w)
    assert not e.get("not_interval") and e["state"] == "no_power"
    v = WR.review(ds, w, "interval_verdict")
    assert v["empty"].startswith("沒有功率")


def test_a_marked_run_is_not_folded(monkeypatch):
    # 「當作間歇判讀」 pressed: the reader asked for the interval reading, so the page isn't one sentence
    monkeypatch.setattr(IE, "flagged", lambda w: True)
    ds = _ds([_run(TODAY - dt.timedelta(days=2), power=150.0)])
    w = ds.workouts[0]
    e = IE.card(ds, w)
    assert not e["not_interval"] and e["why"].startswith("標了「當作間歇」")
