"""專項 chip (static/sportchip.js, SP-381) beyond 圖表分析: SP-450 mounts it on 賽事計算機 and 課表
too (主要訓練項目 sets the calculator's default race type and the 自動課表's road / trail sessions,
so switching it there has an effect); SP-447 makes it readable on a dark page — the select and its
option list take the page's --panel / --text tokens and the dark color-scheme, not a transparent
background with an inherited (white) text that the browser's white option list can't show.

The browser side (computed colours, the switch) is backend/tests/e2e/test_sport_chip.py."""
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
MOUNT = '<span id="sport-chip"></span><script src="/api/v1/static/sportchip.js" defer></script>'


# the page's top row: the chip goes at its right end (圖表分析: the toolbar, SP-381)
TOP_ROW = {"charts.html": ('<span class="spacer"></span>', '<div class="tb2">'),
           "racepower.html": ("<header>", "</header>"),
           "schedule.html": ('<div class="bar calnav">', '<section class="sync"')}


@pytest.mark.parametrize("page", sorted(TOP_ROW))
def test_the_page_mounts_the_sport_chip_in_its_top_row(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert html.count(MOUNT) == 1
    start, end = TOP_ROW[page]
    assert html.index(start) < html.index(MOUNT) < html.index(end)


def test_the_calculator_and_the_schedule_read_the_main_sport_on_load():
    """The chip reloads the page; each page reads 主要訓練項目 when it loads, so the reload is the switch."""
    rp = (STATIC / "racepower.html").read_text(encoding="utf-8")
    assert 'j("/api/v1/app/primary-sport")' in rp and "SPORT = ps?.sport" in rp
    from backend.api import plan_sessions as PSN
    import inspect
    assert "PSP.stored()" in inspect.getsource(PSN)     # the 自動課表's cache key: a switch re-plans


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
