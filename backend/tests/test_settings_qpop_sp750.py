"""SP-750: every ? on the settings page opens the same popup as the other pages (a phone reads it too), not only
the data-manual ones; its last row links the glossary words it names (shell.js manDecorate, SP-559); the ? that
used to write their text into a line under the section (功率, 運動類型, 備份, 運動紀錄保留, 離線資料) no longer do.

The shell's selector and the page's handlers; the popup itself runs in a browser (screenshots). Repo files only."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_shell_opens_a_popup_for_every_settings_question_mark():
    js = (STATIC / "shell.js").read_text("utf-8")
    assert ('const QSEL = "[data-manual][title]:not([data-tip])" + (cur && cur.id === "settings" ? '
            '", button.q[title]:not([data-tip])" : "");') in js
    click = js[js.index("const QSEL"):js.index("window.ManualLink = ")]
    assert "e.target.closest(QSEL)" in click and "manDecorate(qpop, b)" in click      # + the glossary row


def test_no_question_mark_writes_into_a_line_any_more():
    html = (STATIC / "settings.html").read_text("utf-8")
    assert not re.search(r"button\.q[^\n]*\.title;", html), "a ? writing its title into the page"
    for gone in ('id="b-note"', 'id="ar-note"', 'id="ds-note"'):
        assert gone not in html, gone
    # the two status lines stay, for their own text (no longer overwritten by the ?)
    assert 'id="equip-note"' in html and 'id="u-sport-note"' in html      # SP-955: 使用功率 → 裝備


def test_every_settings_question_mark_has_a_title():
    html = (STATIC / "settings.html").read_text("utf-8")
    qs = re.findall(r'<button type="button" class="q[^"]*"[^>]*>', html)
    assert len(qs) >= 25
    assert [q for q in qs if "title=" not in q and "data-i18n-attr" not in q] == []
