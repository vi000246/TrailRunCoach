"""SP-854 in a real browser on the demo athlete: the 課表's week reminders of class 1 (static/hints.js .hint-must)
have no red frame — each has its level's left bar, as the 總覽's notes since SP-551. The demo has no class-1
reminder: the /calendar answer's plan_notes are replaced (page.route; the rest of the answer stays real).

Run: ./e2e.sh -k sp854 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

NOTES = [
    {"id": "PL-t1", "cls": 1, "prio": 1, "show": "●●●", "level": "bad", "src": "prefs", "week_start": "2026-10-19",
     "text": "這週課表偏好的上限放不下長天"},
    {"id": "PL-t2", "cls": 1, "prio": 2, "show": "●●●", "level": "watch", "src": "blackout", "week_start": "2026-10-26",
     "text": "不排課日期 10/27–10/29：這週少了約 2.5 小時"},
    {"id": "PL-t3", "cls": 2, "prio": 6, "show": "●●●", "level": "info", "src": "prefs", "week_start": "2026-10-19",
     "text": "CP 測試不受平日時間上限限制"},
]

STYLE = """() => {
  const must = document.querySelector('#pnotes .hint-must');
  const cs = (e) => getComputedStyle(e);
  const color = (v) => { const d = document.createElement('i'); d.style.color = v; document.body.appendChild(d);
                         const c = getComputedStyle(d).color; d.remove(); return c; };
  const root = cs(document.documentElement);
  const rows = [...must.querySelectorAll('.hint-row')].map((r) => ({w: cs(r).borderLeftWidth, c: cs(r).borderLeftColor}));
  const other = document.querySelector('#pnotes > .hint-row');
  return {frame: [cs(must).borderTopWidth, cs(must).borderRightWidth, cs(must).borderBottomWidth, cs(must).borderLeftWidth],
          rows, bad: color(root.getPropertyValue('--bad')), watch: color(root.getPropertyValue('--watch')),
          other: other && cs(other).borderLeftWidth};
}"""


def _mock_calendar(page):
    def handle(route):
        r = route.fetch()
        body = r.json()
        body["plan_notes"] = NOTES
        route.fulfill(response=r, body=json.dumps(body), headers={**r.headers, "content-type": "application/json"})
    page.route("**/plan/calendar?*", handle)


@pytest.mark.parametrize("vp", [{"width": 1400, "height": 900}, {"width": 390, "height": 844}])
def test_class_one_reminders_have_a_left_bar_not_a_red_frame(app, vp):
    page, _watch = app
    page.set_viewport_size(vp)
    _mock_calendar(page)
    page.goto(_server.PAGES["schedule"])
    page.wait_for_selector("#pnotes .hint-must .hint-row")
    s = page.evaluate(STYLE)
    assert s["frame"] == ["0px", "0px", "0px", "0px"], s                  # no red frame around the group
    assert [r["w"] for r in s["rows"]] == ["4px", "4px"], s               # each class-1 reminder: a 4px left bar
    assert s["rows"][0]["c"] == s["bad"] and s["rows"][1]["c"] == s["watch"], s   # its level's colour
    assert s["other"] == "0px"                                            # a class-2 reminder: no bar, as before
