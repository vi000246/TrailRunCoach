"""總覽提示精簡 (SP-483; docs/research/ui-hint-audit.md §7 T2) in a real browser on the demo athlete, its A race
moved so today is an ordinary 基礎期 week (_server.base_phase): the hints a page shows in 標準 (≤ 15), one place
for each thing (測試, 期別), the 精簡 page without OV8d / OV8f / OV8h / OV8i / OV14, and the whole 5 區 ladder in
every level. The level is set by patching GET /profile/hint-level's answer (the rest of it stays real).

What counts as a hint (COUNT_JS; the audit's §2.3 count, the same kinds): each KPI tile's status word, each
open indicator card and the 「其他 N 項正常」 line, each 待辦 row, each week-note row on the page (the class-1 box's
rows, the reminders, the 「還有 N 則提醒」 line), the week's 完成 line, the 5 區 card's 「下一步」 boxes and its
visible notes / links, the B2B card's 「本週」 line, each item of the suggestion box as it opens, and every
banner / block above the page (課表待確認, SP-471 測試, SP-472 體重). Not counted: what sits in a closed
「詳細」 / 「還有 N 則」 or behind a ? (not on the page), and the 5 區 ladder's stage tags — the owner chose to
see the whole ladder in every level (SP-305), it is the card's content, counted by its 「下一步」 lines.

Run: ./e2e.sh -k sp483 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

MAX_STANDARD = 15            # the ticket's 驗收: 標準下一般基礎期週頁面上的提示 ≤ 15 個

COUNT_JS = r"""() => {
  const on = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const all = (sel) => [...document.querySelectorAll(sel)].filter(on);
  const txt = (els) => els.map((e) => e.textContent.replace(/\s+/g, " ").trim().slice(0, 80));
  const parts = {
    kpi: all(".kpi .st"),
    cards: all("#ind > .ic"),
    folded: all("#ind > details.ind-more > summary"),
    todo: all("#actions > details.al"),
    notes: all("#wk-notes .hint-row, #wk-notes .hint-more > summary"),
    done: all("#wk-cp > span"),
    z5: all("#z5 .rd-here, #z5 .rd-chk .todo"),
    b2b: all("#b2bcard .now-l"),
    box: all(".sugbox .sg-item"),
    banners: all("#autoplan > *, #eptest:not([hidden]), #weighthint:not([hidden]), .sess-banner"),
  };
  const out = { total: 0, parts: {} };
  for (const [k, v] of Object.entries(parts)) { out.parts[k] = txt(v); out.total += v.length; }
  // everything on the page a person can read (not in a closed 詳細, not a ? tip)
  out.text = document.body.innerText;
  out.tiles = document.querySelectorAll("#z5 .ru-tile").length;
  out.testLinks = document.querySelectorAll('#z5 a[href*="test="]').length;
  out.ids = [...document.querySelectorAll("[data-hint-id]")].map((e) => e.dataset.hintId);
  return out;
}"""


@pytest.fixture(scope="module")
def base_server(tmp_path_factory):
    """The demo athlete with today in an ordinary 基礎期 week (its own server: the session one stays as built)."""
    root = tmp_path_factory.mktemp("e2e-sp483")
    base = _server.build_base(root)
    _server.base_phase(base)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


def _open(browser, srv, level: str, viewport=(1400, 900)):
    ctx = browser.new_context(base_url=srv.url, viewport={"width": viewport[0], "height": viewport[1]},
                              locale="zh-TW", timezone_id="Asia/Taipei")
    ctx.set_default_timeout(60_000)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def hint_level(route):
        if route.request.method != "GET":
            return route.continue_()
        r = route.fetch()
        route.fulfill(response=r, json={**r.json(), "level": level})
    page.route("**/api/v1/plan/profile/hint-level", hint_level)

    page.goto("/api/v1/overview/page")
    page.wait_for_selector("#wk-days .dc")
    page.wait_for_selector("#z5 .ru-tile")
    page.locator('#z5 .ru-tile[data-z="5"]').click()          # SP-678: the AeT test is on the 5 區 tile
    page.wait_for_selector("#ind .ic, #ind details.ind-more")
    try:
        page.wait_for_selector(".sugbox .sg-item", timeout=15_000)
    except Exception:                    # noqa: BLE001 — no suggestion this week is fine
        pass
    page.wait_for_load_state("networkidle")
    out = page.evaluate(COUNT_JS)
    out["errors"] = errors
    ctx.close()
    return out


@pytest.fixture(scope="module")
def pages(e2e_browser, base_server):
    return {lv: _open(e2e_browser, base_server, lv) for lv in ("full", "standard", "lean")}


def test_it_is_a_base_week(base_server):
    import urllib.request
    with urllib.request.urlopen(base_server.url + "/api/v1/overview/status", timeout=120) as r:
        st = json.loads(r.read())
    assert st["phase"]["kind"] == "base"


def test_standard_base_week_shows_at_most_15_hints(pages):
    p = pages["standard"]
    assert p["errors"] == []
    assert p["total"] <= MAX_STANDARD, json.dumps(p["parts"], ensure_ascii=False, indent=1)
    # and the full level shows more: the count is the level's doing
    assert pages["full"]["total"] > p["total"]


def test_a_test_is_suggested_in_one_place_only(pages):
    """R15: the suggestion box offers the test; the 待辦, the 測試 card and the 5 區 card only link to it."""
    for lv, p in pages.items():
        offers = [x for x in p["parts"]["todo"] if x.startswith("測試")]
        assert offers == [], lv
        assert "排一次 AeT 測試" not in p["text"], lv              # OV8k / the card's action
        assert "建議做一次測試" not in p["text"], lv               # the 5 區 note is a link now
        assert p["testLinks"] == 0, lv                            # its test links go to the box
        assert any("建議框" in x for x in p["parts"]["z5"] + p["parts"]["cards"]), lv


def test_the_phase_focus_is_in_one_place(pages):
    """OV3: 完整 in the 待辦; 標準 in its 「詳細」 (not on the page); 精簡 not at all."""
    assert any("基礎期重點" in x for x in pages["full"]["parts"]["todo"])
    for lv in ("standard", "lean"):
        assert not any("重點" in x for x in pages[lv]["parts"]["todo"]), lv


def test_lean_has_no_ov8d_ov8f_ov8h_ov8i_or_ov14(pages):
    p = pages["lean"]
    assert not set(p["ids"]) & {"OV8d", "OV8f", "OV8h", "OV8i"}
    assert "底線 75%・目標 80%" not in p["text"]
    # the demo week has OV8d notes: 完整 shows them, so their absence is the level's doing
    assert "OV8d" in pages["full"]["ids"]


def test_the_zone_tiles_in_every_level(pages):
    for lv, p in pages.items():
        assert p["tiles"] == 2, lv                                # SP-678: 3 區 and 5 區, the same in every level
