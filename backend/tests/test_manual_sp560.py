"""SP-560: the manual's 常見問題 (backend/manual/<locale>/faq.md).

  - Chinese and English with the same questions (anchors), on the places people get stuck: sync failing,
    a missing activity, background sync, why the schedule looks like this, numbers that differ from the
    watch, too many tips, heat acclimation, importing / exporting files …
  - every question links to the section that explains it (another chapter or the 名詞表), and every link
    points at a section that exists; the contents table at the top links every question
  - the search finds the questions; the settings ? about syncing link to them
Synthetic data only."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.api import manual as M
from backend.tests.test_manual_sp558 import _search, needs_node

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCALES = ("zh-TW", "en")
TOPICS = {"sync-failed", "missing-activity", "background-sync", "calculating", "why-this-plan", "recovery-week",
          "no-intervals", "plan-confirm", "not-on-watch", "watch-numbers", "threshold-change", "no-power", "estimate",
          "too-many-hints", "heat", "import-export", "injury"}
_HREF = re.compile(r'href="(/manual/[^"]*|#[^"]*)"')


def _faq(loc):
    ch = M.chapter(loc, "faq")
    assert ch is not None, loc
    return ch


def _target(href: str) -> str:
    """/manual/schedule#push → schedule#push; #x (this chapter) → faq#x; /manual/glossary → glossary"""
    return "faq" + href if href.startswith("#") else href[len("/manual/"):]


def test_both_languages_have_the_same_questions():
    zh, en = _faq("zh-TW"), _faq("en")
    assert [s.anchor for s in zh.sections] == [s.anchor for s in en.sections]
    assert set(s.anchor for s in zh.sections) == TOPICS
    assert zh.order > M.chapter("zh-TW", "glossary").order


@pytest.mark.parametrize("loc", LOCALES)
def test_every_question_links_to_the_section_that_explains_it(loc):
    have = M.anchors(loc)
    for s in _faq(loc).sections:
        assert len(s.simple_text) > 40 and s.deep_html, (loc, s.anchor)
        out = [_target(h) for h in _HREF.findall(s.simple_html)]
        elsewhere = [t for t in out if not t.startswith("faq")]
        assert elsewhere, f"{loc}#{s.anchor}: no link to a chapter or the glossary"
        for t in out + [_target(h) for h in _HREF.findall(s.deep_html)]:
            assert t in have, (loc, s.anchor, t)


@pytest.mark.parametrize("loc", LOCALES)
def test_the_contents_table_links_every_question(loc):
    ch = _faq(loc)
    linked = {_target(h) for h in _HREF.findall(ch.intro_html)}
    assert linked == {f"faq#{a}" for a in TOPICS}


@pytest.mark.parametrize("loc", LOCALES)
def test_the_questions_use_the_apps_own_words(loc):
    deny = re.compile(r"WKO|\.md\b|docs/|擁有者|站主|\bowner\b|\bTSS\b|\bCTL\b|\bATL\b|\bTSB\b|\bPMC\b", re.I)
    for ch in M.chunks(loc):
        if ch["chapter"] == "faq":
            assert not deny.findall(ch["title"] + " " + ch["text"]), (loc, ch["url"])


@needs_node
@pytest.mark.parametrize("loc,cases", [
    ("zh-TW", {"同步失敗": "/manual/faq#sync-failed", "提示太多": "/manual/faq#too-many-hints",
               "熱適應": "/manual/faq#heat", "匯出": "/manual/faq#import-export", "對不上": "/manual/faq#watch-numbers"}),
    ("en", {"sync fails": "/manual/faq#sync-failed", "too many tips": "/manual/faq#too-many-hints",
            "heat acclimation": "/manual/faq#heat", "export": "/manual/faq#import-export",
            "differ from the watch": "/manual/faq#watch-numbers"})])
def test_the_search_finds_the_questions(loc, cases):
    out = _search(loc, list(cases))
    for q, want in cases.items():
        urls = [h["url"].split("--")[0] for h in out[q][:3]]
        assert want in urls, (q, [h["url"] for h in out[q]])


def test_the_settings_help_about_syncing_links_to_the_questions():
    html = (STATIC / "settings.html").read_text("utf-8")
    for key in ("faq#background-sync", "faq#missing-activity"):
        assert f'data-manual="{key}"' in html, key
