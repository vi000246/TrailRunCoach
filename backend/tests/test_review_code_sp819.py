"""SP-819: the review card's render-cache key follows the code of every section.

`workout_review.review()` calls its sections through the `_SECTIONS` registry, which codehash
cannot see (a dict of functions): editing `_iv_verdict`, or the interval_eval code it calls,
left the review chart's code signature as it was, and a deploy kept serving the old card.

Synthetic data only (FakeDataset)."""
import datetime as dt

from backend.engine import codehash as CH
from backend.engine import interval_eval as IE
from backend.engine import workout_review as WR
from backend.engine.wko5expr import chartscope as CS
from backend.engine.wko5expr.config import EngineConfig
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

REVIEW = {"kind": "review", "id": "r", "section": "interval_verdict"}
TSS = {"kind": "athlete", "id": "t", "series": [{"expression": "tss"}]}


def _ds():
    day = dt.datetime(2026, 9, 30, 7)
    d = FakeDataset([FakeWorkout(day, metrics={"tss": 40.0, "duration": 3600.0})], day.date())
    d.config = EngineConfig()
    return d


def _fresh():
    CH._MEMO.clear()
    CS._CODE.clear()


def test_the_review_roots_reach_every_section_and_what_it_calls():
    roots = CS.kind_roots("review")
    parts = CH.closure(roots)
    for name, fn in WR._SECTIONS.items():
        assert f"{fn.__module__}.{fn.__qualname__}" in parts, name
    assert "backend.engine.interval_eval.card_cached" in parts
    assert "backend.engine.interval_eval.detected_band_text" in parts      # SP-520's wording


def test_editing_a_section_changes_the_review_key_only(monkeypatch):
    ds = _ds()
    _fresh()
    review, tss = CS.code_signature(REVIEW, ds), CS.code_signature(TSS, ds)

    def _iv_verdict(ds, w, m, c, base):                 # a deploy that changes one section
        return {**base, "series": []}
    monkeypatch.setitem(WR._SECTIONS, "interval_verdict", _iv_verdict)
    _fresh()
    assert CS.code_signature(REVIEW, ds) != review
    assert CS.code_signature(TSS, ds) == tss


def test_editing_interval_eval_changes_the_review_key(monkeypatch):
    ds = _ds()
    _fresh()
    review, tss = CS.code_signature(REVIEW, ds), CS.code_signature(TSS, ds)

    def detected_band_text(name, lo, hi):               # the 間歇判讀 wording SP-520 changed
        return name
    monkeypatch.setattr(IE, "detected_band_text", detected_band_text)
    _fresh()
    assert CS.code_signature(REVIEW, ds) != review
    assert CS.code_signature(TSS, ds) == tss
