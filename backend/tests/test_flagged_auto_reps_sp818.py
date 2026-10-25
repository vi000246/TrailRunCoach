"""標了「當作間歇」的課也用自動判讀的找趟法 (SP-818, from SP-520 Q1-2).

A run marked 「當作間歇」 was read only on interval_reps.find_reps' reps: SP-519's set
(interval_reps.auto_reps) was tried only on unmarked runs (`not fl` in interval_eval.card), so some
runs read worse marked than unmarked — 「標了「當作間歇」，但找不到用力段」 where the unmarked run
showed its reps. Now a marked run whose reps find_reps can't find tries auto_reps' set; found → it
is read on it, still as marked (「你標了「當作間歇」：用偵測到的趟」). A marked run find_reps reads
and every unmarked run are unchanged.

The case: 4×30″ @ 100 % CP / 30″ easy on the watch's laps — under count_reps' 40 s, so find_reps
finds nothing; auto_reps finds the 4 laps (SP-519's test_thirty_second_reps_need_the_watch_laps)."""
import datetime as dt

import pytest

from backend.engine import interval_eval as IE
from backend.engine import interval_reps as IR
from backend.engine import workout_review as WR
from backend.tests.test_interval_auto_sp519 import CP, TODAY, _one, _set_run
from backend.tests.test_interval_eval import _v1a_run

MARKED = "（你標了「當作間歇」：用偵測到的趟）"
NOT_FOUND = "標了「當作間歇」，但找不到用力段"


def _laps_run():
    fw = _set_run(TODAY - dt.timedelta(days=2), [30] * 4, 250.0, rest_s=30, rest_p=140.0)
    ds, w = _one(fw)
    laps = [{"start_s": 0.0, "duration_s": 900.0, "power": 175.0}]
    for k in range(4):
        laps.append({"start_s": 900.0 + 60 * k, "duration_s": 30.0, "power": 250.0})
        if k < 3:
            laps.append({"start_s": 930.0 + 60 * k, "duration_s": 30.0, "power": 140.0})
    laps.append({"start_s": 1110.0, "duration_s": 300.0, "power": 175.0})
    ds.laps = lambda idx: laps
    return ds, w


@pytest.fixture
def marked(monkeypatch):
    monkeypatch.setattr(IE, "flagged", lambda w: True)


def test_a_marked_run_find_reps_misses_is_read_on_the_auto_set(marked):
    ds, w = _laps_run()
    s = WR._samples(ds, w)
    assert IR.find_reps(ds, w, s, CP, None, hr_z3=True)["bouts"] == []      # the bug's premise
    e = IE.card(ds, w)
    assert e["ok"] and e["flagged"] and e["kind"] == "detected"
    assert e["rep_source"] == "auto_lap" and [r["duration_s"] for r in e["reps"]] == [30.0] * 4
    v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["sub"].endswith(MARKED)
    assert NOT_FOUND not in (v["badge"].get("why") or "")


def test_a_marked_run_find_reps_reads_is_unchanged(marked):
    """SP-519's test_the_mark_wins_over_the_auto_display: find_reps' reps, not the auto set."""
    ds, w = _one(_v1a_run(TODAY - dt.timedelta(days=3), reps=3))
    e = IE.card(ds, w)
    assert e["ok"] and e["flagged"] and not e["auto"] and e["rep_source"] == "short" and len(e["reps"]) == 3


def test_a_marked_run_with_some_reps_found_keeps_them(marked):
    """Trail climbs: find_reps finds 2 (3 區), auto_reps 3 — find_reps read it, so it stays as it was."""
    ds, w = _one(_set_run(TODAY - dt.timedelta(days=2), [150, 140, 160], [265.0, 260.0, 270.0], rest_s=150,
                          rest_p=125.0, trail=True))
    e = IE.card(ds, w)
    assert e["ok"] and e["flagged"] and not e["auto"] and e["rep_source"] == "z3" and len(e["reps"]) == 2


def test_a_marked_run_neither_finds_still_says_so(marked):
    # 6×45″ @ 100 % CP / 90″ easy, no laps: neither find_reps nor auto_reps
    ds, w = _one(_set_run(TODAY - dt.timedelta(days=2), [45] * 6, 250.0, rest_s=90, rest_p=150.0))
    e = IE.card(ds, w)
    assert not e["ok"] and e["state"] == "offer" and e["flagged"] and e["why"].startswith(NOT_FOUND)


def test_unmarked_runs_are_unchanged():
    ds, w = _laps_run()
    e = IE.card(ds, w)
    assert e["ok"] and e["auto"] and not e["flagged"] and e["rep_source"] == "auto_lap"
    ds, w = _one(_set_run(TODAY - dt.timedelta(days=2), [45] * 6, 250.0, rest_s=90, rest_p=150.0))
    assert IE.card(ds, w)["not_interval"]
