"""SP-855: the 常見問題 chapter's folds say 「細節」 (en Details) — its folded parts are not formulas — while
every other chapter keeps manual.json's 「公式、模型與來源」 (front matter deep_sub, backend/api/manual.py)."""
from __future__ import annotations

import re

import pytest

from backend.api import manual as M

SUB = re.compile(r'<span class="m-deep-sub">([^<]*)</span>')


def _subs(loc: str, cid: str) -> set[str]:
    ch = M.chapter(loc, cid)
    return set(SUB.findall(M._fill(loc, M.chapters(loc), ch)["main"]))


@pytest.mark.parametrize("loc, faq, other", [("zh-TW", "細節", "公式、模型與來源"),
                                             ("en", "Details", "Formulas, models and sources")])
def test_faq_folds_say_details_and_the_other_chapters_do_not(loc, faq, other):
    assert _subs(loc, "faq") == {faq}
    rest = [c.id for c in M.chapters(loc) if c.id != "faq" and any(s.deep_html for s in c.sections)]
    assert rest
    for cid in rest:
        assert _subs(loc, cid) == {other}, cid


def test_deep_sub_in_the_front_matter():
    text = "---\nid: x\ntitle: X\ndeep_sub: 細節\n---\n## A {#a}\n簡單\n:::deep\n深入\n:::\n"
    assert M.parse(text, "zh-TW").deep_sub == "細節"
    assert M.parse(text.replace("deep_sub: 細節\n", ""), "zh-TW").deep_sub == ""
