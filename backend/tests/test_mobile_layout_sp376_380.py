"""SP-376 (phone 課表: cards and the editor's 儲存) and SP-380 (a swipe over a chart or map moved it
instead of the page) — the page rules that make them hold. The browser checks are in
tests/e2e (test_schedule_phone_*)."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _phone_css(html: str) -> str:
    i = html.index("@media (max-width: 699px) {")
    j = html.index("\n}\n", i)
    return html[i:j]


def test_phone_sheet_keeps_the_buttons_on_screen():
    css = _phone_css((STATIC / "schedule.html").read_text("utf-8"))
    assert "dialog.sd { max-height: 92dvh; }" in css
    assert re.search(r"dialog\.sd form > \.btns \{ position: sticky; bottom: 0;", css)
    assert "env(safe-area-inset-bottom" in css


def test_phone_cards_cut_the_note_to_two_lines():
    css = _phone_css((STATIC / "schedule.html").read_text("utf-8"))
    assert re.search(r"\.ag-day \.chip \.c-d \{[^}]*-webkit-line-clamp: 2", css)
    assert re.search(r"\.ag-day \.chip \.c-x \{[^}]*-webkit-line-clamp: 2", css)


def test_one_finger_scrolls_the_page_over_charts_and_maps():
    rp = (STATIC / "racepower.html").read_text("utf-8")
    assert 'const TOUCH = matchMedia("(pointer: coarse)").matches;' in rp
    assert 'TOUCH ? [{ type: "inside", moveOnMouseMove: false, preventDefaultMouseMove: false }' in rp
    assert "dragging: !TOUCH" in rp
    vw = (STATIC / "wko5_viewer.html").read_text("utf-8")
    assert "const TOUCH_ZOOM = TOUCH ? { moveOnMouseMove: false, preventDefaultMouseMove: false } : {};" in vw
    assert vw.count('{ type: "inside", ...TOUCH_ZOOM }') == 2 and 'type: "inside" }' not in vw
    assert "dragging: !TOUCH" in vw
