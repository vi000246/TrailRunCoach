"""專項 chip (static/sportchip.js) in the browser.

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
@pytest.mark.parametrize("name", ["viewer"])
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
