"""專項 chip (static/sportchip.js, SP-381) on a dark page (SP-447): the select and its option list
take the page's --panel / --text tokens and the dark color-scheme, not a transparent background with
an inherited (white) text that the browser's white option list can't show.

The browser side (computed colours) is backend/tests/e2e/test_sport_chip.py."""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_the_chip_styles_the_select_and_its_options_with_the_page_tokens():
    js = (STATIC / "sportchip.js").read_text(encoding="utf-8")
    # the bug (SP-447): a transparent select with an inherited text colour — the option list is drawn
    # on the browser's own (white) background with the page's (white) text
    assert "background:transparent" not in js.replace(" ", "")
    assert ".sport-chip select, .sport-chip option" in js
    assert "background-color: var(--panel, Canvas)" in js and "color: var(--text, CanvasText)" in js
    assert ".sport-chip option:checked { background-color: var(--line, Highlight); }" in js   # the chosen one
    # dark pages: the native parts (the list on macOS, the arrow, the scrollbar) follow the page
    assert '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .sport-chip select { color-scheme: dark; } }' in js
    assert ':root[data-theme="dark"] .sport-chip select { color-scheme: dark; }' in js
