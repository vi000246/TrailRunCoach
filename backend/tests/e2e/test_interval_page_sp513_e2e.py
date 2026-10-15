"""SP-513: 圖表分析 › 單次活動判讀 › 間歇 — a run that isn't an interval session is one sentence.

On the demo athlete: the 間歇 page of a plain run (not quality, not 間歇 in its name) has one card, 間歇判讀,
saying 「這次不是間歇課」 (English: "Not an interval session"), and the header says 1; an interval session's
間歇 page keeps its cards (the power trace among them).

Run: ./e2e.sh -k interval_page_sp513 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

VIEWER = _server.PAGES["viewer"]
VIEW = "單次活動判讀"
INTERVALS = 2                      # views/workout.json: 間歇


def _workouts(page) -> list[dict]:
    r = page.request.get("/api/v1/app/workouts")
    assert r.ok
    j = r.json()
    rows = j if isinstance(j, list) else j.get("workouts") or []
    assert rows, "the e2e athlete has no activities"
    return rows


def _open(page, a: dict, lang: str = "") -> dict:
    open_page(page, f"{VIEWER}?view={VIEW}&dash={INTERVALS}&workout={a['index']}{'&lang=' + lang if lang else ''}")
    done = """() => { const g = document.querySelector('#grid'), cs = g.querySelectorAll(':scope > section.card');
                      return g.dataset.page != null && cs.length && ![...cs].some((c) => c.querySelector('.loading')); }"""
    page.wait_for_function(done, timeout=120_000)
    page.wait_for_timeout(800)                      # segments_card.js would add its card by now (not on this page)
    return page.evaluate("""() => ({
      header: document.querySelector('#crumb .n').textContent.trim(),
      ids: [...document.querySelectorAll('#grid > section.card')].map((c) => c.querySelector('h2')?.firstChild?.textContent?.trim()),
      text: [...document.querySelectorAll('#grid > section.card')].map((c) => c.innerText).join('\\n') })""")


def _plain(rows: list[dict]) -> dict:
    return next(a for a in rows if "CP" not in str(a.get("name")) and "間歇" not in str(a.get("name"))
                and (a.get("plan") or {}).get("kind") != "quality" and "trail" in str(a.get("sport_type") or a.get("kind") or ""))


def test_not_an_interval_session_is_one_sentence(app):
    page, _ = app
    a = _plain(_workouts(page))
    st = _open(page, a)
    assert st["ids"] == ["間歇判讀"], st
    assert "這次不是間歇課" in st["text"] and st["header"] == "1 張圖", st
    for gone in ("功率、無氧儲備", "每組", "這次是"):          # the trace, 每組 and its 「這次是…，不是間歇課」
        assert gone not in st["text"], (gone, st)
    st = _open(page, a, "en")
    assert len(st["ids"]) == 1 and "Not an interval session" in st["text"] and st["header"] == "1 chart", st


def test_an_interval_session_keeps_its_cards(app):
    page, _ = app
    rows = _workouts(page)
    iv = next(a for a in rows if (a.get("plan") or {}).get("kind") == "quality" or "間歇" in str(a.get("name")))
    st = _open(page, iv)
    assert len(st["ids"]) > 1 and st["ids"][0] == "間歇判讀", st
    assert any(i and i.startswith("功率、無氧儲備") for i in st["ids"]), st
    assert "這次不是間歇課" not in st["text"], st
