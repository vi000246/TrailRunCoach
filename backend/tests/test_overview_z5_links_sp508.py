"""5 區卡的測試連結 (SP-508): SP-483 (R15) turns the 5 區 card's test note and its test 「安排課表」 into links to the
suggestion box; once every test row of the box was 「不要」 / ✕ (OVH.testOut, SP-503) the card has its own note and
「安排課表」 again (Z5Flow's plain ones), and it is drawn again when that changes. overview.html's OVH block and the
real z5flow.js run under node (skipped without node); synthetic data only. The page itself is the browser test
(e2e/test_overview_z5_links_sp508_e2e.py)."""
import json

from backend.tests.test_overview_hints_sp483 import PAGE, _block, _flow, _run, needs_node

# renderZ5's call (overview.html), the box texts as in the SP-483 test
PAGE_CALL = ("(() => { const o = OVH.z5For(DATA.out); return Z5Flow.html(DATA.f, {q: 'q', skip: o.skip, "
             "testNote: o.testNote ? () => toBox('建議做的測試在建議框') : undefined, "
             "testAction: o.testAction ? () => toBox('在建議框排入') : undefined}); })()")


@needs_node
def test_z5_options_follow_test_out():
    got = _run("[OVH.z5For(false), OVH.z5For(true), OVH.z5Opt()]")
    assert got[0] == got[2] == {"skip": ["intensity"], "testNote": True, "testAction": True}      # SP-483 as is
    assert got[1] == {"skip": ["intensity"]}                                                      # SP-508
    assert "level" not in _block()[_block().index("z5For:"):].split("\n")[0]                     # every level alike


@needs_node
def test_box_has_the_test_the_card_links_to_the_box():
    page = _run(PAGE_CALL, f=_flow(), out=False)
    assert "建議做一次測試" not in page and "建議做的測試在建議框" in page
    assert page.count("安排課表") == 1 and page.count("在建議框排入") == 2 and "data-open-sugg" in page


@needs_node
def test_tests_declined_the_card_schedules_them_itself():
    flow = _flow()
    plain = _run("Z5Flow.html(DATA.f, {q: 'q'})", f=flow)
    page = _run(PAGE_CALL, f=flow, out=True)
    assert "data-open-sugg" not in page and "建議框" not in page                    # nothing points to the box
    assert "建議做一次測試" in page                                                   # the card's own test note
    assert page.count("安排課表") == plain.count("安排課表") == 3                       # its own 安排課表 links
    assert "/schedule/page?test=aet" in page
    assert "本週低強度占比" not in page                                               # R16 stays (OV8e says it)
    assert page.count('class="zf-st ') == 4


def test_the_card_is_drawn_again_when_test_out_changes():
    html = PAGE.read_text("utf-8")
    redraw = html[html.index("const sugRedraw"):html.index('addEventListener("suggestions:loaded"')]
    assert "if (Z5C) renderZ5();" in redraw
    z5 = html[html.index("function renderZ5()"):html.index("// ---------------- B2B")]
    assert "OVH.z5For(OVH.testOut(SUGG))" in z5
    assert "testNote: o.testNote ?" in z5 and "testAction: o.testAction ?" in z5
    load = html[html.index("async function loadZ5()"):html.index("function renderZ5()")]
    assert "Z5C = c;" in load and "renderZ5();" in load
    assert json.dumps("OVH.z5Opt()")[1:-1] in html
