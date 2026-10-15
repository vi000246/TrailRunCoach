"""SP-648: the session kinds' type words (plan_store.KINDS) and the match results (compliance /
plan_match: 完成 / 部分 / 換了課 / 跳過 / 類型不符, 「排X，實際Y」, the match label) go through i18n —
in English none of them comes back Chinese; the default (zh-TW) keeps the original words.
Synthetic sessions only (no dataset, no DB)."""
import re

import pytest

from backend.engine import compliance as C
from backend.engine import plan_match as PM
from backend.engine import plan_store as PS
from backend.i18n import use_locale
from backend.tests.test_plan_match import act, sess

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _no_cjk(x):
    return not CJK.search(x or "")


@pytest.mark.parametrize("kind", list(PS.KINDS))
def test_session_tag_label_is_english(kind):
    with use_locale("en"):
        tag = PS.session_tag({"kind": kind, "title": "x"})
        assert tag["label"] and _no_cjk(tag["label"]), tag
        assert _no_cjk(PS.kind_label(kind))
    assert PS.session_tag({"kind": kind})["label"] == PS.KINDS[kind]          # zh-TW unchanged


def test_quality_and_steep_hill_words():
    with use_locale("en"):
        assert PS.session_tag({"kind": "quality"})["label"] == "Quality session"
        assert _no_cjk(PS.session_tag({"kind": "easy", "title": "陡坡健走 40 分"})["label"])
        assert all(_no_cjk(v) for v in PS.kind_labels().values())
    assert PS.session_tag({"kind": "quality"})["label"] == "強度課"
    assert PS.kind_labels()["quality"] == "強度課"


def test_off_plan_is_english():
    q = sess("q", "2026-10-01", "quality", 60, "閾值 3×10 分", state="done",
             done_by={**act(9, "2026-10-01", 58, hard_s=60, tss=30), "match": "plan"})
    with use_locale("en"):
        vs = PM.compare(q)
        comp = C.with_plan_check(C.session_compliance(q, 48.0), vs)
        assert vs["off_plan"] and comp["label"] == "Different session"
        assert vs["text"] == "Different session: planned Quality session, ran easy"
        assert vs["off_why"] == comp["off_why"] == "planned Quality session, ran easy"
        assert _no_cjk(vs["match_label"])
    vs = PM.compare(q)
    assert vs["text"] == "換了課：排強度課，實際跑輕鬆" and vs["off_why"] == "排強度課，實際跑輕鬆"
    assert C.with_plan_check(C.session_compliance(q, 48.0), vs)["label"] == "換了課"


def test_easy_ran_hard_reason_is_english():
    e = sess("e", "2026-10-01", state="done", done_by=act(11, "2026-10-01", 45, hard_s=1200, tss=60))
    with use_locale("en"):
        assert PM.compare(e)["text"] == "Different session: planned Easy run, ran hard"


def test_level_labels_are_english():
    missed = sess("m", "2026-10-01", state="missed")
    done = sess("d", "2026-10-01", state="done", done_by=act(10, "2026-10-01", 45, tss=36))
    wrong = sess("s", "2026-10-01", "strength", 30, state="done", done_by=act(12, "2026-10-01", 30))
    with use_locale("en"):
        assert C.session_compliance(missed)["label"] == "Skipped"
        assert C.session_compliance(done, 36.0)["label"] == "Done"
        w = C.session_compliance(wrong)
        assert w["wrong_type"] and w["label"] == "Wrong type"
        assert {_no_cjk(v) for v in map(lambda k: C._(C.LEVEL_LABEL[k]), C.LEVEL_LABEL)} == {True}
    assert C.session_compliance(missed)["label"] == "跳過"
