"""Browser smoke tests (Playwright, sync API): the real app in a real Chromium.

Opt-in: they run only when selected with `-m e2e` (or TRC_E2E=1), so the default
`pytest` stays fast and needs no browser:

    pip install -r requirements-dev.txt && python -m playwright install chromium
    ./e2e.sh                      # = python -m pytest backend/tests/e2e -m e2e (extra args passed on)
    TRC_E2E_HEADED=1 ./e2e.sh -x  # in a visible browser window

One session server (_server.py): the synthetic demo athlete, built into a temp
folder and served by uvicorn on a free port in owner mode. The pages load
ECharts / Leaflet from cdnjs, so the run needs the internet; map tiles are
answered locally with a blank tile (no tile-server traffic, no flaky tiles).

Every test gets a fresh browser context (`app`): it records page errors,
console errors and the app's own 5xx answers, and the test fails on any of them
at teardown (_browser.Watch)."""
from __future__ import annotations

import os

import pytest

from backend.tests.e2e import _server

E2E_ENV = "TRC_E2E"


def pytest_collection_modifyitems(config, items):
    """The e2e tests only when asked for (-m with "e2e" in it, or TRC_E2E=1)."""
    if "e2e" in (config.getoption("markexpr") or "") or os.environ.get(E2E_ENV) == "1":
        return
    skip = pytest.mark.skip(reason="browser smoke test: run with `-m e2e` (./e2e.sh)")
    for it in items:
        if "e2e" in it.keywords:
            it.add_marker(skip)


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-demo-root")
    base = _server.build_base(root)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


@pytest.fixture(scope="session")
def e2e_browser():
    from playwright.sync_api import sync_playwright
    headed = os.environ.get("TRC_E2E_HEADED") == "1"
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not headed)
        yield b
        b.close()


@pytest.fixture
def app(e2e_browser, e2e_server, request):
    """(page, watch) in a fresh context on the e2e server; fails the test on a page
    error, a console error or a 5xx answer of the app (after the test body)."""
    from backend.tests.e2e import _browser
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 1400, "height": 900},
                                  locale="zh-TW", timezone_id="Asia/Taipei")
    ctx.set_default_timeout(20_000)
    _browser.blank_tiles(ctx, e2e_server.url)
    page = ctx.new_page()
    watch = _browser.Watch(page, e2e_server.url)
    page.on("dialog", lambda d: d.accept())            # confirm() of the flows under test
    try:
        yield page, watch
        problems = watch.problems()
        if problems:
            pytest.fail(f"{request.node.name}: browser errors\n  " + "\n  ".join(problems[:20])
                        + f"\n--- server log ---\n{e2e_server.log_tail(2000)}", pytrace=False)
    finally:
        ctx.close()
