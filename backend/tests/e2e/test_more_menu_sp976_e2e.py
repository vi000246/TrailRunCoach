"""SP-976 in a real browser on the demo athlete: the nav's 「更多」 menu (static/shell.js) on a phone (390 px, 320 px),
a narrow screen (800 px) and a desktop (1400 px), in the four 外觀 (Trig 米白 / 深綠 / 原本 / 原本暗色) and both
languages — every row has its icon at full size, all icons in one column and all names in another, no text runs
out of the menu, the menu stays on screen (on a phone: right above the tab bar, over 更多), and while it is open
nothing floats over it (the 「建議」 box, the 「問 AI」 button); closed, the bar is back under them.

The e2e server runs in owner mode: the sign-in mode's session (an admin, so 管理 and 登出 are in the menu too) is
made in the page, as test_settings_nomode_sp480.py does.

Run: ./e2e.sh -k sp976 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

ADMIN_SESSION = """() => { let v; Object.defineProperty(window, "TRC_SESSION", { configurable: true,
  get() { return v; },
  set(x) { v = { ...x, login: { required: true, signed_in: true }, user: { email: "someone.with.a.long.address@example.com" },
                 membership: { role: "admin", scopes: ["admin", "admin:config", "admin:users"] } }; } }); }"""

GEOM = """() => {
  const R = (e) => { const r = e.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height}; };
  const nav = document.querySelector('.appnav');
  const menu = nav.querySelector('.an-menu');
  const rows = [...menu.querySelectorAll('.an-item')].filter((a) => a.getBoundingClientRect().height > 0).map((a) => {
    const svg = a.querySelector('svg');
    return {text: a.textContent.trim().replace(/\\s+/g, ' ').slice(0, 24), row: R(a), svg: svg ? R(svg) : null,
            name: R(a.querySelector('.an-name')), purpose: R(a.querySelector('.an-purpose'))};
  });
  // the topmost element at a few points of every row is the menu's own (nothing floats over it)
  const covered = rows.filter((r) => [[r.row.left + 4, (r.row.top + r.row.bottom) / 2], [r.row.right - 4, (r.row.top + r.row.bottom) / 2],
      [(r.row.left + r.row.right) / 2, r.row.top + 3], [(r.row.left + r.row.right) / 2, r.row.bottom - 3]]
    .some(([x, y]) => { const t = document.elementFromPoint(x, y); return !t || !menu.contains(t); })).map((r) => r.text);
  const floats = [...document.querySelectorAll('.sugbox, .askai-fab')].filter((e) => !e.hidden && e.getBoundingClientRect().height > 0);
  return {vw: innerWidth, vh: innerHeight, menu: R(menu), more: R(nav.querySelector('.an-more > summary')), nav: R(nav),
          rows, covered, floats: floats.length, navZ: getComputedStyle(nav).zIndex};
}"""


def _open(page, server, w: int, h: int, theme: str, lang: str = "zh-TW", key: str = "templates"):
    page.context.add_cookies([{"name": "theme", "value": theme, "url": server.url},
                              {"name": "lang", "value": lang, "url": server.url}])
    page.set_viewport_size({"width": w, "height": h})
    page.add_init_script(f"({ADMIN_SESSION})()")
    open_page(page, _server.PAGES[key])
    expect(page.locator(".askai-fab")).to_be_visible(timeout=30_000)
    page.wait_for_timeout(500)                       # the 建議 box arrives after its GET
    page.locator(".appnav .an-more > summary").click()
    expect(page.locator(".appnav .an-menu")).to_be_visible()
    page.wait_for_timeout(150)
    return page.evaluate(GEOM)


def _check(g, phone: bool):
    rows, menu = g["rows"], g["menu"]
    assert len(rows) >= 8, g                          # 活動 … 關於, 管理 and 登出 (the admin's sign-in session)
    for r in rows:
        assert r["svg"] and r["svg"]["width"] >= 18 and r["svg"]["height"] >= 18, ("icon squeezed", r)
        for part in ("svg", "name", "purpose"):
            assert r[part]["right"] <= r["row"]["right"] + 0.5, (f"{part} runs out of the row", r)
            assert r[part]["right"] <= menu["right"] + 0.5, (f"{part} runs out of the menu", r)
        assert r["name"]["left"] >= r["svg"]["right"], ("name over the icon", r)
    # one column of icons, one of names
    assert len({round(r["svg"]["left"]) for r in rows}) == 1, [r["svg"] for r in rows]
    assert len({round(r["name"]["left"]) for r in rows}) == 1, [r["name"] for r in rows]
    # on screen
    assert menu["left"] >= 0 and menu["right"] <= g["vw"] and menu["top"] >= 0 and menu["bottom"] <= g["vh"], g
    if phone:
        # right above the tab bar, over 更多
        assert 0 <= g["more"]["top"] - menu["bottom"] <= 12, g
        assert menu["left"] <= g["more"]["left"] and menu["right"] >= g["more"]["right"] - 8, g
    assert not g["covered"], ("covered by a floating button", g["covered"])


@pytest.mark.parametrize("theme", ["trig-cream", "trig-badge", "classic", "classic-dark"])
def test_the_phone_menu_has_every_icon_its_text_inside_and_nothing_over_it(app, e2e_server, theme):
    page, _ = app
    g = _open(page, e2e_server, 390, 844, theme)
    assert g["floats"] >= 1, "no floating 問 AI / 建議 on the page"
    _check(g, phone=True)


@pytest.mark.parametrize("w,h,lang", [(320, 568, "zh-TW"), (390, 844, "en")])
def test_a_small_phone_and_english_fit_too(app, e2e_server, w, h, lang):
    page, _ = app
    _check(_open(page, e2e_server, w, h, "trig-cream", lang), phone=True)


@pytest.mark.parametrize("w,theme,lang", [(800, "trig-badge", "zh-TW"), (800, "classic", "en"),
                                          (1400, "trig-cream", "zh-TW"), (1400, "classic-dark", "en")])
def test_the_top_bar_menu_keeps_its_rows_and_stays_on_screen(app, e2e_server, w, theme, lang):
    page, _ = app
    g = _open(page, e2e_server, w, 900, theme, lang)
    _check(g, phone=False)
    # the name above its purpose, both right of the icon (at 700–1100 px the purpose had fallen under the icon)
    for r in g["rows"]:
        assert r["purpose"]["top"] >= r["name"]["bottom"] - 1 and r["purpose"]["left"] >= r["svg"]["right"], r


def test_closed_the_bar_goes_back_under_the_floating_buttons(app, e2e_server):
    page, _ = app
    g = _open(page, e2e_server, 390, 844, "trig-cream")
    assert int(g["navZ"]) > 61, g                    # open: above 建議 (60) / 問 AI (61)
    page.keyboard.press("Escape")
    expect(page.locator(".appnav .an-menu")).to_be_hidden()
    page.wait_for_timeout(100)
    assert page.evaluate("() => getComputedStyle(document.querySelector('.appnav')).zIndex") == "40"
