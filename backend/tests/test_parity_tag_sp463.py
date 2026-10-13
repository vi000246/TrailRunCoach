"""SP-463: 圖表分析 shows a small tag that cannot be clicked while 設定 › 計算模式 is
「WKO5 對照模式」 (the viewer's S.parity, from GET /config); with 「我的算式」 nothing is shown.
SP-448 removed the clickable 算式 pill: the tag is plain text (no link, no button, no settings
URL). zh-TW + en."""
from __future__ import annotations

import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
VIEWER = (STATIC / "wko5_viewer.html").read_text("utf-8")


def _tag_markup() -> str:
    m = re.search(r'<span id="paritytag"[^>]*>.*?</span>', VIEWER, re.S)
    assert m, "no #paritytag in the viewer"
    return m.group(0)


def test_the_tag_is_plain_text_hidden_until_the_mode_is_known():
    tag = _tag_markup()
    assert " hidden" in tag.split(">")[0]                    # nothing shows before /config answers
    assert 'data-i18n="viewer.parity_tag"' in tag and "WKO5 對照" in tag
    assert "<a " not in tag and "<button" not in tag and "href" not in tag and "onclick" not in tag
    assert 'tabindex' not in tag and 'role="button"' not in tag
    # it sits in the top bar, next to the 專項 chip
    tb1 = VIEWER[VIEWER.index('<div class="tb1">'):VIEWER.index('<div class="tb2">')]
    assert 'id="paritytag"' in tb1


def test_it_follows_the_mode_and_nothing_makes_it_clickable():
    assert '$("paritytag").hidden = !S.parity' in VIEWER
    assert not re.search(r'\$\("paritytag"\)\.(onclick|addEventListener)', VIEWER)
    css = re.search(r"\.paritytag\s*\{([^}]*)\}", VIEWER)
    assert css and "cursor: default" in css.group(1) and "pointer" not in css.group(1)
    # SP-448 stays: no pill, no link to the settings tab
    assert 'id="modepill"' not in VIEWER and "/api/v1/wko5/settings#mode" not in VIEWER


def test_the_tag_has_both_languages():
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "viewer.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "viewer.json").read_text("utf-8"))
    assert zh["parity_tag"] == "WKO5 對照" and en["parity_tag"] == "WKO5 parity"
    assert zh["parity_tag_tip"] and en["parity_tag_tip"] and "WKO5" in en["parity_tag_tip"]
    assert 'data-i18n-attr="title:viewer.parity_tag_tip"' in _tag_markup()
