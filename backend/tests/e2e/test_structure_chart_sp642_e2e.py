"""SP-642 on the demo athlete: the structure chart's colours follow each step's target type, the
reference line says 「門檻」 (not CP), the legend is one ramp per type used plus one caption, the
heart-rate height says where it comes from (推估 on the small demo), and a 儲備心率 plan's step
tooltip shows % HRR next to its bpm.

The conversion itself is covered by backend/tests/test_workout_steps_hr_scale_sp642.py.

Run: ./e2e.sh -k structure_chart_sp642 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = _server.PAGES["templates"]
UAPI = "/api/v1/overview/plan/steps/templates/user"


def st(kind, sec, target=None):
    return {"kind": kind, "dur": {"type": "time", "value": sec}, "target": target or {"type": "none"}, "note": ""}


def hr(lo, hi):
    return {"type": "hr", "mode": "pct", "lo": lo, "hi": hi}


HR = {"name": "SP-642 心率", "cats": ["quality"], "steps": {"items": [
    st("warm", 600, hr(0.80, 0.85)), st("work", 900, hr(0.95, 0.99)), st("cool", 600, hr(0.75, 0.82))]}}
MIX = {"name": "SP-642 混合", "cats": ["quality"], "steps": {"items": [
    st("warm", 600, hr(0.80, 0.85)), st("work", 900, {"type": "pace", "mode": "pct", "lo": 1.04, "hi": 1.08}),
    st("work", 300, {"type": "rpe", "lo": 6, "hi": 7}), st("rest", 120), st("cool", 600, hr(0.75, 0.82))]}}


def _make(page, body) -> int:
    r = page.request.post(UAPI, data=body)
    assert r.ok, r.text()
    return r.json()["id"]


def _open(page, tid):
    page.goto(f"{PAGE}?t={tid}#{tid}")
    page.wait_for_selector("#we-svg rect.blk", state="attached", timeout=120_000)
    page.evaluate("document.getElementById('we-box').open = true")


def test_colours_threshold_line_and_compact_legend(app):
    page, _ = app
    page.goto(PAGE)
    tid, mid = _make(page, HR), _make(page, MIX)
    _open(page, tid)
    expect(page.locator("#we-svg text", has_text="門檻")).to_have_count(1)
    assert page.locator("#we-svg text", has_text="CP").count() == 0
    fills = page.locator("#we-svg rect.blk").evaluate_all("rs => rs.map(r => r.getAttribute('fill'))")
    assert fills and all(f.startswith("var(--wzh") for f in fills)
    lg = page.locator("#we-legend")
    expect(lg.locator(".we-lt")).to_have_count(1)
    expect(lg.locator('.we-lt[data-type="hr"]')).to_be_visible()
    expect(lg).not_to_contain_text("% CP")
    expect(lg.locator(".we-hrs")).to_contain_text("推估")
    expect(page.locator("#we-stats")).not_to_contain_text("% CP")
    # mixed: one ramp per type, a single caption, 不設目標 for the rest
    _open(page, mid)
    expect(lg.locator(".we-lt")).to_have_count(3)
    assert lg.locator(".we-lt").evaluate_all("xs => xs.map(x => x.dataset.type)") == ["hr", "pace", "rpe"]
    expect(lg.locator(".we-lcap")).to_have_count(1)
    expect(lg).to_contain_text("不設目標")
    fills = page.locator("#we-svg rect.blk").evaluate_all("rs => rs.map(r => r.getAttribute('fill'))")
    assert fills[0].startswith("var(--wzh") and fills[1].startswith("var(--wzs") and fills[2].startswith("var(--wzr")
    assert fills[3] == "var(--bar)"


def test_hrr_tooltip_shows_percent_hrr(app):
    page, _ = app
    page.goto(PAGE)
    tid = _make(page, HR)
    r = page.request.put("/api/v1/plan/hr-profile", data={"max_hr": 190, "rest_hr": 50, "model": "hrr"})
    assert r.ok, r.text()
    try:
        _open(page, tid)
        page.locator("#we-svg rect.blk").nth(1).hover()
        tip = page.locator("#we-tip")
        expect(tip).to_be_visible()
        expect(tip).to_contain_text("bpm")
        expect(tip).to_contain_text("% HRR")
    finally:
        page.request.put("/api/v1/plan/hr-profile", data={"model": "auto", "clear_max": True, "clear_rest": True})
