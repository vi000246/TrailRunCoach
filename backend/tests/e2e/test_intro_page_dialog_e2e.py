"""The 介紹頁 keeps its reader: 方案與價格、關於、操作手冊 and the legal documents open over the page, covering the
screen, embedded (no top bar or footer of their own); inside, another of these pages stays in the frame and the
sign-in leaves for real; × closes it and the 介紹頁 is where it was.

Run: ./e2e.sh -k page_dialog (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"


def _open(page, loc="zh-TW", size=(1400, 900)):
    from backend.api import intro as I
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(reduced_motion="reduce")
    page.add_init_script(f"window.TRC_WHY = {json.dumps(I.why())};")
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")


def _framed(page, path):
    page.wait_for_function("() => !document.getElementById('man-modal').hidden")
    page.wait_for_function("(p) => { const w = document.getElementById('man-frame').contentWindow;"
                           " return w && w.location.pathname === p && w.document.readyState === 'complete'; }", arg=path)
    return page.frame_locator("#man-frame")


@pytest.mark.parametrize("sel,path,hidden", [
    ('header.top a[href="/pricing"]', "/pricing", "header.top"),
    ('header.top a[href="/about"]', "/about", None),
    ('header.top a[href="/manual"]', "/manual", ".m-side"),
])
def test_the_bar_opens_each_page_over_the_intro(app, sel, path, hidden):
    page, _w = app
    if page.request.get(path).status == 404:                             # no billing in this mode: no /pricing
        pytest.skip(f"no {path} in this mode")
    _open(page)
    url = page.url
    page.locator(sel).click()
    frame = _framed(page, path)
    assert page.url == url
    assert "embed=1" in page.locator("#man-frame").get_attribute("src")
    assert frame.locator("script[src*='shell.js']").count() == 0
    if hidden:
        assert frame.locator(hidden).first.evaluate("(el) => getComputedStyle(el).display") == "none"
    box = page.locator("#man-modal .dlg").bounding_box()
    assert box["width"] == 1400 and box["height"] == 900                 # covers the screen
    page.wait_for_function("() => document.getElementById('man-title').textContent.trim() !== ''")
    page.locator("#man-close").click()
    page.wait_for_function("() => document.getElementById('man-modal').hidden")
    assert page.url == url


def test_inside_the_frame_a_page_stays_framed_and_the_sign_in_leaves(app):
    page, _w = app
    _open(page)
    url = page.url
    page.locator('header.top a[href="/manual"]').click()
    frame = _framed(page, "/manual")
    frame.locator(".m-cards a.m-card").first.click()                    # a chapter: still in the frame
    page.wait_for_function("() => /^\\/manual\\/[a-z0-9-]+$/.test("
                           "document.getElementById('man-frame').contentWindow.location.pathname)")
    assert page.url == url
    page.locator("#man-close").click()
    if page.request.get("/pricing").status == 404:
        return
    page.locator('header.top a[href="/pricing"]').click()
    frame = _framed(page, "/pricing")
    login = frame.locator('main a[href^="/login"]').first
    if login.count():
        login.click()
        page.wait_for_url("**/login**")
