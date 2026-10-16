"""SP-514: while the box's test is held by 稍後提醒 (SP-507) the overview's 測試 card and 5 區 card say when it is back
(「10/21 再提醒你」) instead of pointing to a box that does not show it; once it is back in the box they link there
again (SP-483 R15), and 「不要」 / ✕ still gives the cards their own (SP-503 / SP-508). overview.html's OVH block and
the real readiness.js (SP-678: the 強度課表解鎖條件 card) run under node (skipped without node); synthetic data only. The page itself is the browser test
(e2e/test_overview_snoozed_test_sp514_e2e.py)."""
import json

from backend.tests.test_overview_hints_sp483 import PAGE, STATIC, _flow, _run, needs_node

TEST = {"id": "test:cp:2026-10-12", "type": "test"}
BASE = {"id": "baseline:aet:2026-10-12", "type": "baseline"}
ZONE = {"id": "zone:thr_check:hrmax", "type": "zone_test"}
B2B = {"id": "b2b:2026-10-12", "type": "b2b"}
HELD = {**TEST, "until": "2026-10-21"}

# renderZ5's call (overview.html) with the page's atDay / toBox
PAGE_CALL = ("(() => { const later = OVH.testLater(DATA.sg); const o = later ? OVH.z5Later() : OVH.z5For(OVH.testOut(DATA.sg));"
             " const atDay = (d) => '<span class=\"test-later\">' + esc(tO('hint.test_later', {day: d.slice(5).replace('-', '/')})) + '</span>';"
             " return Readiness.unlockHtml(DATA.f, {sel: 5, schedule: '/s', skip: o.skip,"
             " testNote: o.testNote ? () => (later ? atDay(later) : toBox(tO('hint.test_in_box'))) : undefined,"
             " testAction: o.testAction ? () => toBox(tO('hint.schedule_in_box')) : undefined}); })()")


@needs_node
def test_test_later_is_the_day_a_held_test_is_back():
    cases = {
        "not loaded": None,
        "in the box": {"rows": [TEST], "declined": [], "snoozed": []},
        "held": {"rows": [B2B], "declined": [], "snoozed": [HELD]},
        "two held: the first day": {"rows": [], "declined": [], "snoozed": [{**BASE, "until": "2026-10-25"}, HELD]},
        "held, another in the box": {"rows": [ZONE], "declined": [], "snoozed": [HELD]},
        "held, another declined": {"rows": [], "declined": [ZONE], "snoozed": [HELD]},
        "declined only (SP-503)": {"rows": [], "declined": [TEST], "snoozed": []},
        "a B2B held": {"rows": [], "declined": [], "snoozed": [{**B2B, "until": "2026-10-21"}]},
    }
    got = _run("Object.fromEntries(Object.entries(DATA.c).map(([k, v]) => [k, OVH.testLater(v)]))", c=cases)
    assert got == {"not loaded": None, "in the box": None, "held": "2026-10-21", "two held: the first day": "2026-10-21",
                   "held, another in the box": None, "held, another declined": "2026-10-21",
                   "declined only (SP-503)": None, "a B2B held": None}


@needs_node
def test_the_5_zone_card_says_when_the_held_test_is_back():
    flow = _flow()
    plain = _run("Readiness.unlockHtml(DATA.f, {sel: 5, schedule: '/s'})", f=flow)
    for loc, text in (("zh-TW", "建議做的測試 10/21 再提醒你"), ("en", "I'll remind you of the suggested test on 10/21")):
        page = _run(PAGE_CALL, loc, f=flow, sg={"rows": [], "declined": [], "snoozed": [HELD]})
        assert text.replace("'", "&#39;") in page, (loc, page)
        assert "data-open-sugg" not in page and "建議框" not in page and "suggestion box" not in page   # not to the box
        assert page.count('class="test-later"') == 1
        assert "排一次：平路" not in page                                          # the card's own note: replaced
        assert page.count("/schedule/page?test=aet") == plain.count("/schedule/page?test=aet") == 1   # its 安排課表
    # back in the box (SP-483) / declined (SP-508): as before
    box = _run(PAGE_CALL, f=flow, sg={"rows": [TEST], "declined": [], "snoozed": []})
    assert "test-later" not in box and "在建議框排入" in box and "建議做的測試在建議框" in box
    out = _run(PAGE_CALL, f=flow, sg={"rows": [], "declined": [TEST], "snoozed": []})
    assert "test-later" not in out and "建議框" not in out and "排一次：平路" in out


def test_the_test_card_and_the_redraw_follow_it():
    html = PAGE.read_text("utf-8")
    ind = html[html.index("function renderInd()"):html.index("// 熱適應 card")]
    assert "const later = test ? OVH.testLater(SUGG) : null;" in ind
    assert "const verdict = later ? atDay(later) : box ? toBox(" in ind
    assert "thrCheck(i, box)" in ind                                             # its 安排課表 links meanwhile
    z5 = html[html.index("function renderZ5()"):html.index("// ---------------- B2B")]
    assert "const o = later ? OVH.z5Later() : OVH.z5For(OVH.testOut(SUGG));" in z5
    redraw = html[html.index("const sugState"):html.index('addEventListener("suggestions:loaded"')]
    assert "OVH.testLater(sg)" in redraw and "if (Z5C) renderZ5();" in redraw
    assert 'const atDay = (day) => `<span class="test-later">${esc(tO("hint.test_later", { day: md(day) }))}</span>`;' in html


def test_strings_in_both_languages():
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "overview.json").read_text("utf-8"))
        assert "{day}" in cat["hint.test_later"], loc
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "overview.json").read_text("utf-8"))
    assert zh["hint.test_later"] == "建議做的測試 {day} 再提醒你"
