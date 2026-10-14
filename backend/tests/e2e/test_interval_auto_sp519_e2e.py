"""SP-519: 圖表分析 › 單次活動判讀 › 間歇 — an easy run with a set of efforts shows the interval charts.

On the demo athlete: 「恢復週 fartlek 4×1 分」 (the linked interval run of the recovery week, not matched
to its session, so it is classified an easy run; the e2e build quarters the steps: 4 × 30″ laps) shows
間歇判讀 with 「自動判讀」 and the power trace, without 「當作間歇判讀」 — in Chinese and English. A plain
run keeps SP-513's one sentence (test_interval_page_sp513_e2e.py).

Run: ./e2e.sh -k interval_auto_sp519 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e.test_interval_page_sp513_e2e import _open, _workouts  # noqa: E402

pytestmark = pytest.mark.e2e


def _fartlek(rows: list[dict]) -> dict:
    return next(a for a in rows if "fartlek" in str(a.get("name")))


def test_an_easy_run_with_a_set_of_efforts_shows_its_charts(app):
    page, _ = app
    a = _fartlek(_workouts(page))
    st = _open(page, a)
    assert len(st["ids"]) > 1 and st["ids"][0] == "間歇判讀", st
    assert any(i and i.startswith("功率、無氧儲備") for i in st["ids"]), st
    assert "自動判讀：這次算輕鬆跑，有 4 趟" in st["text"], st
    assert "這次不是間歇課" not in st["text"] and "當作間歇判讀" not in st["text"], st
    st = _open(page, a, "en")
    assert len(st["ids"]) > 1 and "reviewed automatically: Easy run, with 4 efforts" in st["text"], st
    assert "Review as intervals" not in st["text"], st
