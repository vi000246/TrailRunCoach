"""賽事計算機 result hints (SP-485 = docs/research/ui-hint-audit.md §7 T4), in a real browser on the demo athlete's
GPX trail race (demo-50k, or 貓空 17K when present).

- 標準, nothing opened: ≤ 12 hints visible (RaceHints.count: the class-1 box rows, the reminders and the
  「還有 N 則提醒」 line, every element marked data-rh — 「推估」 badges, the heat block, the effort line, the pole
  line, the rain line…; nothing inside a closed <details> or a hover tip)
- 沒填補給站, 脫水 > 2 %, 目標太快 first in the box on top of the result in 完整／標準／精簡
- rain 60 % but 1 mm: nothing on top, one line on the 環境 card
- 會用杖: one line on the segments card; per segment only in 完整 (the compact table)
What the demo has no data for (the fuel warnings, a goal, rain, poles) is patched into the real /plan answer;
the level comes from a mocked GET /profile/hint-level.

Run: ./e2e.sh -k sp485 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.engine import hints as H  # noqa: E402
from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

MAX_STANDARD = 12            # the ticket's 驗收: a typical trail race in 標準, nothing opened


def _event(page) -> str:
    r = page.request.get("/api/v1/racepower/inputs")
    assert r.ok, r.status
    ids = [e["id"] for e in r.json().get("events") or []]
    eid = next((x for x in ("demo-50k", "demo-maokong") if x in ids), None)
    assert eid, f"no GPX race among the e2e athlete's events: {ids}"
    return eid


def _level(page, level: str) -> None:
    page.route("**/api/v1/plan/profile/hint-level",
               lambda r: r.fulfill(status=200, json={"level": level, "auto": False, "acks": []}))


def _patch_plan(page, patch) -> None:
    """The real POST /plan answer, changed by patch(answer)."""
    def handle(route):
        resp = route.fetch()
        body = resp.json()
        patch(body)
        route.fulfill(response=resp, body=json.dumps(body), headers={**resp.headers, "content-type": "application/json"})
    page.route("**/api/v1/racepower/plan", handle)


def _calc(page, eid: str) -> None:
    open_page(page, f"{_server.PAGES['racepower']}?event={eid}")
    expect(page.locator("#event")).to_have_value(eid, timeout=30_000)
    page.click('#mode button[data-m="auto"]')
    page.click("#calc")
    expect(page.locator("#results .tiles").first).to_be_visible(timeout=90_000)
    page.wait_for_function("() => typeof PLAN !== 'undefined' && PLAN && document.querySelector('#segcmp table')",
                           timeout=60_000)


def test_standard_shows_at_most_12_hints_on_the_demo_trail_race(app):
    page, _ = app
    eid = _event(page)
    _level(page, "standard")
    _calc(page, eid)
    n, seen = page.evaluate("() => [RaceHints.count(), RaceHints.list()]")
    assert 0 < n <= MAX_STANDARD, seen
    # the GPX warnings only at the upload; no 30-second / GAP line under the tables any more
    assert not page.locator("#results .warn").count()
    assert "30 秒平均功率" not in page.locator("#segtable").inner_text()


def _fuel_and_goal(body):
    fw = body.setdefault("fuel", {}).setdefault("warnings", [])
    fw[:0] = [H.make("RC22", "熱量交叉檢查：功率法和 Fletcher 差超過 15 %"),
              H.make("RC21", "沒有輸入補給站：越野賽請在「補給站」加上 km，才能算每段要帶多少水"),
              H.make("RC20", "預估脫水約 3.1 % 體重：> 2 % 表現會掉")]
    t = body["summary"]["time_s"]
    body["goal"] = {"goal_time_s": t * 0.88, "model_time_s": t, "model_pace_s_per_km": 400, "faster": 0.12,
                    "level": "too_fast", "message": "比模型預測快 12%，很可能撐不住", "badge": "推估"}


@pytest.mark.parametrize("level", H.LEVELS)
def test_no_stops_dehydration_and_too_fast_are_on_top_in_every_level(app, level):
    page, _ = app
    eid = _event(page)
    _level(page, level)
    _patch_plan(page, _fuel_and_goal)
    _calc(page, eid)
    rows = page.locator("#rh-box .hint-row")
    expect(rows.first).to_be_visible()
    texts = rows.all_inner_texts()
    top3 = " ".join(texts[:3])
    assert "沒有輸入補給站" in top3 and "預估脫水" in top3 and "很可能撐不住" in top3, texts
    # the box is the first thing under the result's title
    assert page.evaluate("() => document.querySelector('#results h2').nextElementSibling.id") == "rh-box"
    # the 補給 card's cross-check (RC22, a method line) is in 「這次怎麼算的」, not shown
    expect(page.locator("#rh-how")).to_contain_text("熱量交叉檢查")
    expect(page.locator("#rh-how .hint-detail")).to_be_hidden()


def test_light_rain_is_a_line_on_the_weather_card_not_on_top(app):
    page, _ = app
    eid = _event(page)
    _level(page, "standard")
    _patch_plan(page, lambda b: b.update(rain={"alert": True, "top": False, "max_pop_pct": 60.0, "total_mm": 1.0,
                                               "rows": 6, "message": "預報有雨：下坡和技術路段可能比預估慢"}))
    _calc(page, eid)
    expect(page.locator('#rh-box [data-hint-id="RC2"]')).to_have_count(0)
    expect(page.locator("#rain-card")).to_be_visible()
    expect(page.locator("#rain-card")).to_contain_text("60")
    expect(page.locator("#rain-card")).to_contain_text("1.0 mm")


def test_heavy_rain_is_on_top(app):
    page, _ = app
    eid = _event(page)
    _level(page, "lean")
    _patch_plan(page, lambda b: b.update(rain={"alert": True, "top": True, "max_pop_pct": 80.0, "total_mm": 8.0,
                                               "rows": 6, "message": "預報有雨：下坡和技術路段可能比預估慢"}))
    _calc(page, eid)
    expect(page.locator('#rh-box [data-hint-id="RC2"]')).to_contain_text("預報有雨")
    expect(page.locator("#rain-card")).to_be_hidden()


def _poles(body):
    up = {"key": "up", "text": "用杖：自覺比較輕鬆，速度差不多", "src": "Giovanelli 2019"}
    down = {"key": "down", "text": "用杖：膝蓋負擔少 12–25 %", "src": "Schwameder 1999"}
    for r in body.get("chart_rows") or []:
        g = r.get("grade") or 0
        r["pole_hint"] = up if g > 0.08 else down if g <= -0.15 else None


@pytest.mark.parametrize("level,per_seg", [("full", True), ("standard", False), ("lean", False)])
def test_poles_one_line_on_the_card_per_segment_only_in_full(app, level, per_seg):
    page, _ = app
    eid = _event(page)
    _level(page, level)
    _patch_plan(page, _poles)
    _calc(page, eid)
    if level == "lean":
        expect(page.locator("#pole-line")).to_be_hidden()
    else:
        expect(page.locator("#pole-line")).to_be_visible()
        expect(page.locator("#pole-line")).to_contain_text("會用杖")
        expect(page.locator("#pole-line")).to_contain_text("第一段在")
    assert (page.locator("#segcmp .pole").count() > 0) is per_seg
    assert page.locator("#segtable .pole").count() == 0          # never in the all-columns table
