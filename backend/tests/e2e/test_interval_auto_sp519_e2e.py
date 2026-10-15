"""SP-519: 圖表分析 › 單次活動判讀 › 間歇 — an easy run with a set of efforts shows the 間歇 page by itself.

On the demo athlete: 「恢復週 fartlek 4×1 分」, the linked interval run of the recovery week. It is matched to
its 課表 session (SP-530: the e2e build keeps its 15′ warm-up, 4×1′ and rests as planned, only the cool-down
is quartered), so its 間歇 page judges it against the plan. Taken off the session (取消配對) it is an unplanned
run with four 1′ efforts at ≥ 95 % CP after minute 10: the page shows its charts by itself, saying it was
reviewed automatically, without 「當作間歇判讀」 — in Chinese and English. Paired back afterwards (the e2e
server is shared by every test). R1 (efforts in the first 10 minutes don't count, the user 2026-10-14) is
checked on the review's answer in test_interval_auto_sp519.py. A plain run: test_interval_page_sp513_e2e.py.

Run: ./e2e.sh -k interval_auto_sp519 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e.test_interval_page_sp513_e2e import _open, _workouts  # noqa: E402

pytestmark = pytest.mark.e2e

SESSIONS = "/api/v1/overview/plan/sessions"


def _fartlek(rows: list[dict]) -> dict:
    return next(a for a in rows if "fartlek" in str(a.get("name")))


def _row(page, index: int) -> dict:
    return next(x for x in _workouts(page) if x["index"] == index)


def _open_fresh(page, a: dict, lang: str = "") -> dict:
    """_open after the pairing changed: the viewer first shows each chart's previous drawing (更新中, the
    render cache's stale-while-revalidate) — wait until none is stale, then read the page again."""
    _open(page, a, lang)
    page.wait_for_function("() => !document.querySelector('#grid > section.card.is-stale')", timeout=120_000)
    return _open(page, a, lang)


def test_unpaired_from_its_session_the_set_shows_the_charts(app):
    page, _ = app
    a = _fartlek(_workouts(page))
    assert (a.get("plan") or {}).get("kind") == "quality", a        # matched to its session (SP-530)
    ss = page.request.get(SESSIONS).json()["sessions"]
    s = next(s for s in ss if (s.get("done_by") or {}).get("index") == a["index"])
    st = _open(page, a)
    assert len(st["ids"]) > 1 and "這次不是間歇課" not in st["text"] and "自動判讀" not in st["text"], st
    assert page.request.delete(f"{SESSIONS}/{s['uid']}/link").ok
    try:
        a = _row(page, a["index"])
        assert not (a.get("plan") or {}).get("kind"), a
        st = _open_fresh(page, a)
        assert len(st["ids"]) > 1 and "自動判讀" in st["text"], st
        assert "這次不是間歇課" not in st["text"] and "當作間歇判讀" not in st["text"], st
        st = _open_fresh(page, a, "en")
        assert len(st["ids"]) > 1 and "reviewed automatically" in st["text"], st
        assert "Not an interval session" not in st["text"], st
    finally:
        assert page.request.post(f"{SESSIONS}/{s['uid']}/link", data={"index": a["index"]}).ok
    assert (_row(page, a["index"]).get("plan") or {}).get("kind") == "quality"
