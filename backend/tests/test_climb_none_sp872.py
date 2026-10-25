"""
SP-872: when the 攀爬段 (SP-728) can't be marked because your climbing records can't say yet, the
calculator's legend says so — 「還沒有足夠的攀爬紀錄」 (climb.py reason `few`), or that they can't pick
out scrambles yet (`weak`) — with the counts in its ?, instead of showing nothing (no scramble on the
route and too little data looked the same). Nothing when there are marks, when the model is usable and
the route simply has none, or without a climb model at all; a share keeps no such plan's climb.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.engine.racepower import climb as CL
from backend.engine.racepower import share as SH
from backend.tests.test_climb_marks_sp728 import _run_js, needs_node

STATIC = Path(__file__).resolve().parents[1] / "static"


def _plan(climb):
    P = SH.with_rows(SH.example("example-trail-50k"))
    P["climb"] = climb
    return P


def _legend(P):
    return _run_js("SegProfile.legend(DATA.P, {})", P=P)


@needs_node
def test_few_records_says_so_in_the_legend():
    fitted = CL.fit({})                                          # no runs with a track: few
    assert fitted["reason"] == "few" and not fitted["usable"]
    P = _plan({"marks": [], "cv": {"n_acts": 3, "n_pos": 7, "n_windows": 900}, "usable": False, "reason": "few", "badge": "推估"})
    lg = _legend(P)
    assert "climb.few" in lg and "climb.few_tip" in lg and "climb.legend" not in lg


@needs_node
def test_weak_says_it_cannot_tell_yet():
    P = _plan({"marks": [], "cv": {"n_acts": 12, "n_pos": 60}, "usable": False, "reason": "weak", "badge": "推估"})
    lg = _legend(P)
    assert "climb.weak" in lg and "climb.few" not in lg


@needs_node
def test_nothing_when_marked_usable_or_no_model():
    usable = _plan({"marks": [], "cv": {}, "usable": True, "reason": None})            # the route has none
    marked = _plan({"marks": [{"start_km": 11.0, "end_km": 12.4, "p": 0.4}], "cv": {}, "usable": True, "reason": None})
    none = _plan(None)
    for P in (usable, none):
        assert "climb." not in _legend(P)
    lg = _legend(marked)
    assert "climb.legend" in lg and "climb.few" not in lg and "climb.weak" not in lg
    # a share of such a plan keeps no climb (climb.summary): the share page never says it
    assert CL.summary({"marks": [], "usable": False, "reason": "few"}) is None


def test_the_texts_are_in_both_catalogs():
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        for k in ("climb.few", "climb.few_tip", "climb.weak", "climb.weak_tip"):
            assert cat.get(k), (loc, k)
        assert "{acts}" in cat["climb.few_tip"] and "{pos}" in cat["climb.few_tip"]
    assert json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))["climb.few"] == "還沒有足夠的攀爬紀錄"
