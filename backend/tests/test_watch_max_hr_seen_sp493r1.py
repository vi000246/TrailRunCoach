"""SP-493 R1 (owner 2026-10-14, Q2-2): a max HR from the watch account is believable when a run of the
last 90 days came close to it — a 10-s hold ≥ the value − 5 bpm (推估; threshold_confidence.WATCH_SEEN_*);
R2 (owner 2026-10-20, Q4): the row's s10 is the best 10-s mean, and the texts say 「10 秒平均」.
Before, every watch value was a low source, so 自動 almost never picked 儲備心率. Synthetic rows only."""
import datetime as dt

import pytest

from backend.engine import threshold_confidence as TC
from backend.tests.test_threshold_confidence import TODAY, lt, run

WATCH = {"value": 189.0, "source_kind": "watch", "label": "來自手錶（COROS 帳號）"}
REST = {"value": 53.0, "source_kind": "watch", "label": "來自手錶（COROS 帳號）"}
LT_WATCH = lt(168.0, kind="watch", date=None)          # the watch's LTHR: never reliable (auto skips lthr);
#                                                        89 % of 189: no consistency signal on either value
EASY = [run(s10=150.0, s60=149.0, s120=148.0), run(s10=151.0, s60=150.0, s120=149.0),
        run(s10=149.0, s60=148.0, s120=147.0)]


def _days_ago(n: int) -> str:
    return (TODAY - dt.timedelta(days=n)).isoformat()


def _assess(runs, mhr=WATCH, lthr=LT_WATCH, model="auto"):
    return TC.assess(lthr, mhr, REST, TODAY, runs, {}, 250.0, hr_model=model)


@pytest.mark.parametrize("s10, s60, ago, seen", [
    (185.0, 180.0, 20, True),      # 4 bpm under: close
    (184.0, 180.0, 20, True),      # 5 bpm: the edge counts
    (183.0, 180.0, 20, False),     # 6 bpm: not close
    (191.0, 186.0, 20, True),      # above the value: reached
    (186.0, 180.0, 89, True),      # inside 90 days
    (186.0, 180.0, 91, False),     # older than 90 days
    (None, 185.0, 20, True),       # no 10-s hold on the row: the 60-s one (never higher)
])
def test_watch_seen_needs_a_close_10s_hold_in_90_days(s10, s60, ago, seen):
    r = TC.watch_seen(WATCH, EASY + [run(date=_days_ago(ago), s10=s10, s60=s60, s120=170.0)], TODAY)
    assert bool(r) is seen
    if seen:
        assert r["date"] == _days_ago(ago) and r["days"] == 90


def test_watch_seen_is_only_for_the_watch_account():
    near = EASY + [run(date=_days_ago(10), s10=187.0, s60=183.0, s120=176.0)]
    for kind in ("manual", "test", "estimate", "age"):
        assert TC.watch_seen({**WATCH, "source_kind": kind}, near, TODAY) is None
    assert TC.watch_seen(WATCH, near, None) is None     # hrmax_check without a date: as before


def test_a_seen_watch_max_hr_is_believable_and_auto_picks_hrr():
    near = EASY + [run(date=_days_ago(30), s10=186.0, s60=182.0, s120=176.0)]
    r = _assess(near)
    h = r["hrmax"]
    assert h["confidence"] == "medium" and h["seen"]["value"] == 186
    src = next(s for s in h["signals"] if s["id"] == "source")
    assert src["level"] == "weak" and "10 秒平均最高 186" in src["text"] and "推估" in src["text"] and src["estimate"]
    # 186 is 13 under 189 held for 120 s: no 「spike」 signal once a 10-s hold came within 5 bpm
    assert not any(s["id"] == "sustained" for s in h["signals"])
    a = r["auto"]
    assert a["model"] == "hrr" and "10 秒平均最高 186" in a["reason"] and _days_ago(30) in a["reason"]
    assert not any("hrmax" in s["tests"] for s in r["suggestions"])      # no max-HR test asked for
    assert not r["warn"]["hrmax"]["low"]


def test_an_unseen_watch_max_hr_stays_low_and_auto_picks_hrmax():
    r = _assess(EASY + [run(date=_days_ago(30), s10=180.0, s60=178.0, s120=176.0)])
    assert r["hrmax"]["confidence"] == "low" and "seen" not in r["hrmax"]
    a = r["auto"]
    assert a["model"] == "hrmax" and "近 90 天沒有跑步 10 秒平均到 184 以上" in a["reason"]
    assert any("hrmax" in s["tests"] for s in r["suggestions"])
    old = _assess(EASY + [run(date=_days_ago(120), s10=188.0, s60=186.0, s120=184.0)])   # seen, but too long ago
    assert old["hrmax"]["confidence"] == "low" and old["auto"]["model"] == "hrmax"


def test_a_seen_watch_max_hr_with_other_evidence_against_it_stays_low():
    # seen in the last 90 days, but a 60-s hold well above the value: the 「set too low」 signal wins
    r = _assess(EASY + [run(date=_days_ago(10), s10=196.0, s60=195.0, s120=190.0),
                        run(date=_days_ago(40), s10=195.0, s60=194.0, s120=189.0)])
    assert r["hrmax"]["seen"] and r["hrmax"]["confidence"] == "low"
    assert any(s["id"] == "sustained" and s["direction"] == "up" for s in r["hrmax"]["signals"])
    assert r["auto"]["model"] == "hrmax"


def test_a_locked_model_reads_the_same_confidence():
    near = EASY + [run(date=_days_ago(30), s10=186.0, s60=182.0, s120=176.0)]
    r = _assess(near, model="hrr")
    assert r["hrmax"]["confidence"] == "medium" and not r["warn"]["hrmax"]["low"]
    r = _assess(EASY, model="hrr")
    assert r["hrmax"]["confidence"] == "low" and r["warn"]["hrmax"]["low"]


def test_the_check_reads_the_10s_hold_of_each_run():
    import numpy as np
    t = np.arange(0, 1800.0)
    hr = np.full(1800, 150.0)
    hr[864:900] = np.arange(150.0, 186.0)   # a 36-s ramp, 15 s at 186, back down: a finish, not a spike
    hr[900:915] = 186.0
    hr[915:951] = np.arange(186.0, 150.0, -1.0)
    s = TC.run_summary(t, hr)
    assert s["s10"] == pytest.approx(186.0) and s["s120"] == pytest.approx(150.0)
