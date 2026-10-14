"""SP-506: 圖表分析 › 單次活動判讀 — the header's 「N 張圖」 is the number of cards on the screen.

On the demo athlete: 本次重點 of a run that is not a CP test (the CP-test card taken off, AN8), the
間歇 page of a run that is not an interval session (its review cards taken off) and 本次重點 of the CP
test (the card stays), in Chinese and in English. The number in #crumb .n equals the cards in #grid
(the segments card of segments_card.js included when it is there).

Run: ./e2e.sh -k card_count (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

VIEWER = _server.PAGES["viewer"]
VIEW = "單次活動判讀"
FOCUS, INTERVALS = 0, 2           # views/workout.json: 本次重點, 間歇


def _workouts(page) -> list[dict]:
    r = page.request.get("/api/v1/wko5/workouts")
    assert r.ok
    j = r.json()
    rows = j if isinstance(j, list) else j.get("workouts") or []
    assert rows, "the e2e athlete has no activities"
    return rows


def _settled(page) -> None:
    """Every card drawn (or taken off), the segments card added and filled (or put away)."""
    done = """() => { const cs = document.querySelectorAll('#grid > section.card');
                      if (!cs.length) return !!document.querySelector('#grid > .empty:not([data-i18n])')
                        || document.querySelector('#grid').dataset.page != null;
                      return ![...cs].some((c) => c.querySelector('.loading')); }"""
    page.wait_for_function(done, timeout=120_000)
    page.wait_for_timeout(800)                      # segments_card.js adds its card 120 ms after the grid changes
    page.wait_for_function(done, timeout=60_000)


def _state(page) -> dict:
    return page.evaluate("""() => {
      const d = curView().dashboards[S.dash[S.mode]];
      return { header: document.querySelector('#crumb .n').textContent.trim(),
               cards: document.querySelectorAll('#grid > section.card').length,
               titles: [...document.querySelectorAll('#grid > section.card > h2')].map((h) => h.firstChild?.textContent || h.textContent),
               seg: !!document.querySelector('#grid > #segcard'),
               defined: visCharts(d).length };
    }""")


def _open(page, a: dict, dash: int, lang: str = "") -> dict:
    open_page(page, f"{VIEWER}?view={VIEW}&dash={dash}&workout={a['index']}{'&lang=' + lang if lang else ''}")
    _settled(page)
    return _state(page)


def _n(header: str) -> int:
    m = re.search(r"\d+", header)
    assert m, header
    return int(m.group(0))


def test_header_counts_the_cards_on_the_screen(app):
    page, _ = app
    rows = _workouts(page)
    cp = next(a for a in rows if "CP" in str(a.get("name")))
    plain = next(a for a in rows if "CP" not in str(a.get("name")) and "間歇" not in str(a.get("name"))
                 and (a.get("plan") or {}).get("kind") != "quality" and "trail" in str(a.get("sport_type") or a.get("kind") or ""))

    # 本次重點, not a test: the CP-test card is off, and the header says so — on a few activities, one of
    # them without the segments card (with it, the old count of the definition came out right by chance)
    others = [a for a in rows if "CP" not in str(a.get("name"))][:6]
    seen = []
    for a in others:
        st = _open(page, a, FOCUS)
        assert not any("CP" in t for t in st["titles"]), st
        assert st["header"] == f"{st['cards']} 張圖", st
        assert st["cards"] - st["seg"] < st["defined"], st         # the page's own cards: fewer than defined
        seen.append(st["seg"])
    assert False in seen, "no activity of the demo without the segments card"

    # 間歇 of the same run: its review cards are off, the header goes with them
    st = _open(page, plain, INTERVALS)
    assert _n(st["header"]) == st["cards"] < st["defined"], st

    # 本次重點 of the CP test: the card stays and counts
    st = _open(page, cp, FOCUS)
    assert any("CP" in t for t in st["titles"]), st
    assert _n(st["header"]) == st["cards"], st

    # English: 「N charts」 of the same cards
    st = _open(page, plain, FOCUS, "en")
    assert st["header"] == f"{st['cards']} chart{'' if st['cards'] == 1 else 's'}", st
