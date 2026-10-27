"""SP-689 in a real browser on the demo athlete: switching 「輕鬆路跑的心率飄移」 between 配速 and 功率 shows one
「計算中」 box floating over the middle of the card (not a line under the chart: the card keeps its size), however
many clicks; the answer of an older click never overwrites the newer choice. The power answer is held back 2.5 s
in the page (a wrapped fetch), so a click on 配速 right after 功率 is answered first. The same box (charts.html
cardBusy) is used by the 日／週, 近 N 天, variant and zone-model toggles and the 爬坡 VAM card's route picker.
Run: ./e2e.sh -k sp689 (see conftest.py)."""
from __future__ import annotations

import urllib.parse

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.e2e

TRAINING = urllib.parse.quote("我的訓練")

TITLE = "輕鬆路跑的心率飄移"
SLOW_POWER = """(() => {
  const of = window.fetch;
  window.__basisAsked = [];
  window.fetch = (u, o) => {
    const s = String(u), p = of(u, o);
    if (s.includes('/charts/') && s.includes('basis=')) window.__basisAsked.push(s.includes('basis=power') ? 'power' : 'pace');
    if (s.includes('/charts/') && s.includes('basis=power')) return p.then((r) => new Promise((ok) => setTimeout(() => ok(r), 2500)));
    return p;
  };
})()"""


def _drift_card(page):
    # SP-954: an unpicked 配速／功率 toggle follows today's power state (功率 on the demo athlete), and the demo's one
    # drift run is on a stale-CP day (no power point): start from 配速, the choice this test makes first anyway
    page.add_init_script("""(() => { try { const k = 'charts.basis', p = JSON.parse(localStorage.getItem(k) || '{}');
      if (!('我的訓練|3|3' in p)) localStorage.setItem(k, JSON.stringify({ ...p, '我的訓練|3|3': 'pace' })); } catch {} })()""")
    page.goto("/api/v1/app/viewer?" + urllib.parse.urlencode({"view": "我的訓練", "dash": 3}, safe=""))
    card = page.locator("#grid .card", has=page.locator("h2", has_text=TITLE)).first
    expect(card.locator('.seg[data-kind="basis"] button')).to_have_count(2, timeout=60_000)
    expect(card.locator(".plot")).to_have_count(1, timeout=60_000)
    return card


def _basis_shown(card):
    return card.evaluate("(c) => c._res && c._res.basis")


def test_one_floating_busy_box_and_the_last_click_wins(app):
    page, _ = app
    page.add_init_script(SLOW_POWER)
    card = _drift_card(page)
    seg = card.locator('.seg[data-kind="basis"]')
    if _basis_shown(card) == "power":                       # start on 配速 (the choice is remembered per chart)
        seg.locator('button[data-p="pace"]').click()
        expect(card.locator(".card-busy")).to_have_count(0, timeout=30_000)
        expect(seg.locator('button[data-p="pace"]')).to_have_class("on")
    seg = card.locator('.seg[data-kind="basis"]')
    box0 = card.bounding_box()
    # 功率 (held back), then 配速 at once, then 功率 and 配速 again: four clicks, one box
    seg.locator('button[data-p="power"]').click()
    busy = card.locator(".card-busy")
    expect(busy).to_have_count(1)
    expect(busy).to_have_text("計算中…")
    # floating over the middle of the card, the card not taller, no 「計算中」 line under the chart
    assert busy.evaluate("(b) => getComputedStyle(b).position") == "absolute"
    # (the card's box now, not box0's place: a card above that is still loading can move it down the page)
    bb, cb = busy.bounding_box(), card.bounding_box()
    mid_x, mid_y = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
    assert abs(mid_x - (cb["x"] + cb["width"] / 2)) < 4 and abs(mid_y - (cb["y"] + cb["height"] / 2)) < 4, (bb, cb)
    assert abs(cb["height"] - box0["height"]) < 1
    expect(card.locator(".loading")).to_have_count(0)
    # the button picked shows at once
    expect(seg.locator('button[data-p="power"]')).to_have_class("on")
    seg.locator('button[data-p="pace"]').click()
    seg.locator('button[data-p="power"]').click()
    seg.locator('button[data-p="pace"]').click()
    assert card.locator(".card-busy").count() <= 1       # never a second box (0: the fast answer already came)
    assert page.locator(".card-busy").count() <= 1
    # the 配速 answer comes first and is drawn; the held-back 功率 answers (2.5 s) must not replace it
    expect(card.locator(".card-busy")).to_have_count(0, timeout=30_000)
    page.wait_for_timeout(3500)
    assert _basis_shown(card) == "pace"
    expect(card.locator('.seg[data-kind="basis"] button[data-p="pace"]')).to_have_class("on")
    assert page.evaluate("window.__basisAsked.filter((b) => b === 'power').length") >= 2
    expect(card.locator(".card-busy")).to_have_count(0)
    assert abs(card.bounding_box()["height"] - box0["height"]) < 40          # the same chart, redrawn


def test_the_older_answer_never_overwrites_when_power_is_last(app):
    page, _ = app
    page.add_init_script(SLOW_POWER)
    card = _drift_card(page)
    seg = card.locator('.seg[data-kind="basis"]')
    if _basis_shown(card) == "power":
        seg.locator('button[data-p="pace"]').click()
        expect(card.locator(".card-busy")).to_have_count(0, timeout=30_000)
    seg = card.locator('.seg[data-kind="basis"]')
    seg.locator('button[data-p="power"]').click()
    seg.locator('button[data-p="pace"]').click()
    seg.locator('button[data-p="power"]').click()      # the last choice: 功率
    expect(card.locator(".card-busy")).to_have_count(1)
    expect(card.locator(".card-busy")).to_have_count(0, timeout=30_000)
    page.wait_for_timeout(3000)
    assert _basis_shown(card) == "power"
    expect(card.locator('.seg[data-kind="basis"] button[data-p="power"]')).to_have_class("on")
