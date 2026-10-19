"""單次活動 › 路線地圖旁的折線圖 「區間上色」 (SP-660) in a real browser on the demo athlete: off by default;
switched on, the HR line is drawn as one series per zone of the settings' 課表心率區間, a legend lists the zones,
another set can be picked (power, pace) and both the switch and the set are remembered.

Run: ./e2e.sh -k sp660 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SERIES_JS = """() => { const el = document.querySelector('#grid .mapwrap .mapchart');
  const c = echarts.getInstanceByDom(el); return c ? c.getOption().series.map((s) => s.id) : null; }"""


def _open(page):
    rows = page.request.get("/api/v1/app/workouts").json()
    rows = rows if isinstance(rows, list) else rows.get("workouts")
    idx = next(r["index"] for r in rows if r.get("name", "").startswith("間歇"))
    page.add_init_script("localStorage.getItem('charts.mapchart') || localStorage.setItem('charts.mapchart', JSON.stringify({layout: 'top'}))")
    open_page(page, f"{_server.PAGES['viewer']}?view=單次活動判讀&dash=0&workout={idx}")
    page.locator("#grid .mapwrap .mapchart").wait_for(timeout=60_000)


def test_the_switch_colours_the_line_by_the_settings_zones_and_is_remembered(app):
    page, _ = app
    _open(page)
    btn = page.locator('#grid .mcbar button[data-zc]')
    expect(btn).to_have_attribute("aria-pressed", "false")            # off by default: the lines as before
    assert "hr" in page.evaluate(SERIES_JS)
    expect(page.locator("#grid .mcbar select.mczs")).to_have_count(0)
    btn.click()
    sel = page.locator("#grid .mcbar select.mczs")
    expect(sel).to_be_visible(timeout=30_000)                         # the sets are read on first use
    expect(sel).to_have_value("hr:coroslthr")                          # the 課表心率區間 in effect
    expect(sel.locator("option:checked")).to_contain_text("（設定）")
    ids = page.evaluate(SERIES_JS)
    assert "hr" not in ids and [i for i in ids if i.startswith("hr-z")] == [f"hr-z{k}" for k in range(6)], ids
    expect(page.locator("#grid .mapside .ac-leg .mczleg i.band")).to_have_count(6)
    # the optgroups: the kinds of the lines shown (疊加: HR, power, pace)
    assert page.evaluate("() => [...document.querySelectorAll('#grid select.mczs optgroup')].map((g) => g.label)") == [
        "心率區間", "功率區間", "配速區間"]
    sel.select_option("power:palladino")
    ids = page.evaluate(SERIES_JS)
    assert "hr" in ids and "power" not in ids and "power-z0" in ids
    saved = json.loads(page.evaluate("() => localStorage.getItem('charts.mapchart')"))
    assert saved["zone"] == {"on": True, "set": "power:palladino"}
    page.reload()
    page.locator("#grid .mcbar select.mczs").wait_for(timeout=60_000)
    expect(page.locator("#grid .mcbar select.mczs")).to_have_value("power:palladino")
    page.locator('#grid .mcbar button[data-zc]').click()                # off again: one line per metric
    assert "power" in page.evaluate(SERIES_JS)
    expect(page.locator("#grid .mapside .ac-leg .mczleg")).to_have_count(0)
