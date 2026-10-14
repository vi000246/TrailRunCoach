"""SP-506 R1: 圖表分析's directory — no number beside a page, the header's 「N 張圖」 stays.

On the demo athlete: 單次活動判讀 (本次重點 and 間歇 of a run) and the season views, in Chinese and English.

Run: ./e2e.sh -k sidebar_sp506r1 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

VIEWER = _server.PAGES["viewer"]
VIEW = "單次活動判讀"


def _first_run(page) -> dict:
    r = page.request.get("/api/v1/wko5/workouts")
    assert r.ok
    j = r.json()
    rows = j if isinstance(j, list) else j.get("workouts") or []
    assert rows, "the e2e athlete has no activities"
    return rows[0]


def _tree(page) -> dict:
    page.wait_for_function("() => document.querySelectorAll('#tree .db').length > 0", timeout=60_000)
    page.wait_for_function("() => (document.querySelector('#crumb .n')?.textContent || '').trim() !== ''", timeout=60_000)
    return page.evaluate("""() => ({
      pages: [...document.querySelectorAll('#tree .db')].map((b) => b.textContent.trim()),
      counts: document.querySelectorAll('#tree .db .n').length,
      header: document.querySelector('#crumb .n').textContent.trim() })""")


@pytest.mark.parametrize("lang", ["", "en"])
def test_the_directory_has_no_counts_on_a_workout(app, lang):
    page, _ = app
    a = _first_run(page)
    for dash in (0, 2):                                 # 本次重點, 間歇
        open_page(page, f"{VIEWER}?view={VIEW}&dash={dash}&workout={a['index']}{'&lang=' + lang if lang else ''}")
        st = _tree(page)
        assert st["counts"] == 0, st
        assert not any(re.search(r"\d+$", p) for p in st["pages"]), st      # no number glued to a page's name
        assert re.fullmatch(r"\d+ (張圖|charts?)", st["header"]), st          # the header keeps its count


def test_the_directory_has_no_counts_on_season_pages(app):
    page, _ = app
    open_page(page, f"{VIEWER}?view=我的訓練&dash=0")     # a season view (views/training.json)
    st = _tree(page)
    assert len(st["pages"]) > 1 and st["counts"] == 0, st
