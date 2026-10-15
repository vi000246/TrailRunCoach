"""SP-519: 圖表分析 › 單次活動判讀 › 間歇 — the automatic interval display leaves the run's first 10 minutes out.

On the demo athlete: 「恢復週 fartlek 4×1 分」 (the linked interval run of the recovery week, not matched to
its session — SP-530 — so it is classified an easy run). The e2e build quarters its steps, the 15′ warm-up
too: its four 30″ efforts all start in the first 10 minutes, so R1 (the user 2026-10-14: 「排除開跑前 10 分」)
leaves them out and the page keeps SP-513's one sentence instead of showing itself — in Chinese and English.
The display itself (≥ 3 similar efforts at ≥ 95 % CP after minute 10 → the charts, without 「當作間歇判讀」)
is checked on the review's answer in test_interval_auto_sp519.py: the demo has no unplanned run with such a
set after its first 10 minutes. A plain run: test_interval_page_sp513_e2e.py.

Run: ./e2e.sh -k interval_auto_sp519 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e.test_interval_page_sp513_e2e import _open, _workouts  # noqa: E402

pytestmark = pytest.mark.e2e


def _fartlek(rows: list[dict]) -> dict:
    return next(a for a in rows if "fartlek" in str(a.get("name")))


def test_efforts_in_the_first_ten_minutes_do_not_show_the_charts(app):
    page, _ = app
    a = _fartlek(_workouts(page))
    assert not (a.get("plan") or {}).get("kind"), a                 # still unmatched (SP-530)
    st = _open(page, a)
    assert st["ids"] == ["間歇判讀"] and st["header"] == "1 張圖", st
    assert "這次不是間歇課" in st["text"] and "自動判讀" not in st["text"], st
    st = _open(page, a, "en")
    assert len(st["ids"]) == 1 and "Not an interval session" in st["text"], st
    assert "reviewed automatically" not in st["text"], st
