"""專項 chip (static/sportchip.js) in the browser.

SP-450: 賽事計算機 and 課表 have it too, top right like 圖表分析 (SP-381); choosing 路跑／馬拉松
saves 主要訓練項目 and reloads, and the page follows it — the calculator hides its 越野 / 百岳
buttons, the 課表's week totals drop the 爬升 row. Set back to 自動 afterwards.

SP-447: on a dark page the chip and its option list are readable (and still on a light one): the
select's text against what is behind it, and every option's text against the option's own
background — which has to be opaque, or the browser draws the list on its own white.

Run: ./e2e.sh -k sport_chip (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SEL = "#sport-chip select#sport-sel"


def _setting(page) -> str:
    r = page.request.get("/api/v1/wko5/primary-sport")
    assert r.ok
    return r.json()["setting"]


def _switch(page, value: str) -> None:
    with page.expect_navigation():
        page.locator(SEL).select_option(value)
    expect(page.locator(SEL)).to_have_value(value, timeout=60_000)
    assert _setting(page) == value


def _climb_rows(page) -> int:
    """課表's week totals: rows labelled 爬升 (SP-87; none under 路跑)."""
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)
    page.wait_for_selector("#cal .chip", timeout=60_000)
    return page.evaluate("""() => [...document.querySelectorAll('#cal .r > span:first-child')]
        .filter((s) => s.textContent.trim() === '爬升').length""")


def test_calculator_sport_chip_switches_the_main_sport(app):
    page, _ = app
    open_page(page, _server.PAGES["racepower"])
    expect(page.locator(SEL)).to_be_visible(timeout=60_000)
    assert page.locator(f"{SEL} option").all_inner_texts()[1:] == ["越野跑", "路跑／馬拉松"]
    expect(page.locator('#type button[data-t="trail"]')).to_be_visible()
    try:
        _switch(page, "road")
        expect(page.locator('#type button[data-t="trail"]')).to_be_hidden()
        expect(page.locator('#type button[data-t="road"]')).to_be_visible()
    finally:
        page.request.put("/api/v1/sync/settings", data={"primary_sport": "auto"})
    page.reload()
    expect(page.locator(SEL)).to_have_value("auto", timeout=60_000)
    expect(page.locator('#type button[data-t="trail"]')).to_be_visible()


def test_schedule_sport_chip_switches_the_main_sport(app):
    page, _ = app
    open_page(page, _server.PAGES["schedule"])
    expect(page.locator(SEL)).to_be_visible(timeout=60_000)
    assert _climb_rows(page) > 0, "the demo athlete (trail) has 爬升 week rows"
    try:
        _switch(page, "road")
        assert _climb_rows(page) == 0
    finally:
        page.request.put("/api/v1/sync/settings", data={"primary_sport": "auto"})


# the select's and every option's text vs its background (WCAG contrast); the background behind
# the select is its own or the first opaque ancestor's
_COLOURS = """() => {
  const parse = (s) => { const p = s.match(/rgba?\\(([^)]+)\\)/)[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number);
                         return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = ({ r, g, b }) => { const f = (c) => (c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
                                  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
  const behind = (el) => { for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor);
                           if (c.a >= 1) return c; } return { r: 255, g: 255, b: 255, a: 1 }; };
  const s = document.querySelector('#sport-sel'), cs = getComputedStyle(s), bg = behind(s);
  return { scheme: cs.colorScheme, bg_lum: lum(bg), select: ratio(parse(cs.color), bg),
           options: [...s.options].map((o) => { const oc = getComputedStyle(o), ob = parse(oc.backgroundColor);
                                                 return { text: o.textContent, checked: o.selected, bg: oc.backgroundColor,
                                                          alpha: ob.a, ratio: ratio(parse(oc.color), ob) }; }) };
}"""


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("name", ["viewer", "racepower", "schedule"])
def test_sport_chip_is_readable_in_dark_and_light(app, name, scheme):
    page, _ = app
    page.emulate_media(color_scheme=scheme)
    open_page(page, _server.PAGES[name])
    expect(page.locator(SEL)).to_be_visible(timeout=60_000)
    c = page.evaluate(_COLOURS)
    assert (c["bg_lum"] < 0.1) == (scheme == "dark"), c          # it really is a dark / light page
    assert c["select"] >= 4.5, c
    for o in c["options"]:
        assert o["alpha"] == 1, f"option {o['text']!r}: transparent background (the browser's white shows)"
        assert o["ratio"] >= 4.5, (o, c)
    # the chosen sport stands out in the list (its own background)
    chosen = [o["bg"] for o in c["options"] if o["checked"]]
    others = {o["bg"] for o in c["options"] if not o["checked"]}
    assert len(chosen) == 1 and chosen[0] not in others, c
    # the native parts (the list on macOS, the arrow) follow the page's scheme
    assert ("dark" in c["scheme"]) == (scheme == "dark"), c
