"""賽事計算機 努力度 bar (SP-697), in a real browser on the demo athlete's GPX trail race:

- 「模型預測」: dragging the bar three times quickly sends ONE /plan (released → a short wait, a new drag
  restarts it), shows one 「重新計算中…」 line, the value lands in the plan and a value off the
  100／95／85 % buttons reads 「自訂」;
- an older run's failure that comes back late never overwrites the newer result;
- 目標功率: the bar is a greyed result with the hint to switch to 模型預測, and dragging it neither
  recomputes nor switches the mode;
- trail 模型預測 with the HR model: 95 % picked → 努力度 95 % 全力心率.

Run: ./e2e.sh -k sp697 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e


def _event(page) -> str:
    r = page.request.get("/api/v1/racepower/inputs")
    assert r.ok, r.status
    ids = [e["id"] for e in r.json().get("events") or []]
    eid = next((x for x in ("demo-50k", "demo-maokong") if x in ids), None)
    assert eid, f"no GPX race among the e2e athlete's events: {ids}"
    return eid


def _calc(page, eid: str, mode: str = "auto") -> None:
    open_page(page, f"{_server.PAGES['racepower']}?event={eid}")
    expect(page.locator("#event")).to_have_value(eid, timeout=30_000)
    page.click(f'#mode button[data-m="{mode}"]')
    if mode == "power":
        page.fill("#tpower", "200")
    page.click("#calc")
    _settled(page)


def _settled(page) -> None:
    expect(page.locator("#results .tiles").first).to_be_visible(timeout=90_000)
    page.wait_for_function("() => typeof PLAN !== 'undefined' && PLAN && document.querySelector('#ebar')", timeout=90_000)
    # the race-day weather arrives after the first plan and recomputes once (the page's own behaviour): let it
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle")


def _drag(page, *fracs: float) -> None:
    """Drag the needle to each fraction of the bar's width in turn, released in between (quickly:
    the box is read once, three mouse calls per drag)."""
    page.locator("#ebar").scroll_into_view_if_needed()
    box = page.locator("#ebar").bounding_box()
    y = box["y"] + box["height"] / 2
    for frac in fracs:
        page.mouse.move(box["x"] + box["width"] * 0.5, y)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * frac, y)
        page.mouse.up()


def _plans(page) -> list:
    seen = []
    page.on("request", lambda r: seen.append(r.url) if r.url.endswith("/racepower/plan") and r.method == "POST" else None)
    return seen


def test_three_quick_drags_recompute_once_with_one_busy_line(app):
    page, _ = app
    eid = _event(page)
    _calc(page, eid)
    sent = _plans(page)
    _drag(page, 0.3, 0.45, 0.52)                       # 60–110 % across: ≈ 75 %, 82 %, 86 %
    assert page.locator("#results .busy").count() == 1
    expect(page.locator("#recalc-busy")).to_contain_text("重新計算中")
    want = page.evaluate("() => S.effort")
    page.wait_for_function("(w) => PLAN.summary.effort_target != null && Math.abs(PLAN.summary.effort_target - w) < 1e-9",
                           arg=want, timeout=90_000)
    page.wait_for_timeout(1000)
    assert len(sent) == 1, sent
    assert page.locator("#recalc-busy").count() == 0
    assert page.evaluate("() => S.mode") == "auto"
    if want not in (1, 0.95, 0.85):
        expect(page.locator("#effort-custom")).to_contain_text("自訂")


def test_a_late_failure_of_an_older_run_does_not_overwrite_the_newer_result(app):
    page, _ = app
    eid = _event(page)
    _calc(page, eid)
    # the next /plan answers late with an error and ignores the page's abort (a slow server)
    page.evaluate("""() => { let n = 0; const of = window.fetch;
      window.fetch = async (u, o) => {
        if (String(u).endsWith('/racepower/plan') && n++ === 0) {
          await new Promise((r) => setTimeout(r, 2500));
          return new Response(JSON.stringify({ detail: 'old failure' }), { status: 409, headers: { 'content-type': 'application/json' } });
        }
        return of(u, o);
      }; }""")
    page.click('#effort button[data-e="0.95"]')
    page.wait_for_timeout(400)                         # the first run is on its way
    page.click('#effort button[data-e="0.85"]')
    page.wait_for_function("() => PLAN.summary.effort_target === 0.85", timeout=90_000)
    page.wait_for_timeout(3000)                        # the old failure has come back by now
    assert page.locator("#results .err").count() == 0
    assert "old failure" not in page.locator("#results").inner_text()
    expect(page.locator("#results .tiles").first).to_be_visible()


def test_target_power_shows_the_bar_as_a_result_only(app):
    page, _ = app
    eid = _event(page)
    _calc(page, eid, mode="power")
    expect(page.locator("#ebar")).to_have_class("ebar ro")
    expect(page.locator("#ebar-ro")).to_contain_text("模型預測")
    sent = _plans(page)
    before = page.evaluate("() => S.effort")
    _drag(page, 0.3)
    page.wait_for_timeout(1500)
    assert sent == [] and page.evaluate("() => S.mode") == "power" and page.evaluate("() => S.effort") == before
    assert page.locator("#recalc-busy").count() == 0


def test_trail_model_effort_reads_the_hr_level_picked(app):
    page, _ = app
    eid = _event(page)
    _calc(page, eid)
    page.click('#effort button[data-e="0.95"]')
    page.wait_for_function("() => PLAN.summary.effort_target === 0.95", timeout=90_000)
    if page.evaluate("() => PLAN.summary.total_method") != "trail_hr":
        pytest.skip("the demo athlete has no trail HR model")
    assert page.evaluate("() => PLAN.effort.basis") == "hr"
    expect(page.locator("#results .tile", has_text="努力度")).to_contain_text("95% 全力心率")
    expect(page.locator("#ebar-val")).to_have_text("95%")
    expect(page.locator("#effort-custom")).to_have_text("")
