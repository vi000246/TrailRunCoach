"""分析頁左側目錄每頁旁邊不再寫張數 (SP-506 R1).

SP-506 made the header's 「N 張圖」 count the cards on the screen; the directory's number beside each page
was still the page's definition (visCharts), so one screen showed two numbers that didn't match (頁首「5 張圖」,
目錄「本次重點 6」). The owner's answer (SP-506 Q1-1, C): take the directory's numbers off — the tree is
for moving around. A view's header (imported WKO5 views) keeps its page count: pages don't come and go.
The real page is checked in e2e/test_viewer_sidebar_sp506r1_e2e.py."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = (STATIC / "wko5_viewer.html").read_text("utf-8")
TREE = re.search(r"\nfunction renderTree\(\) \{.*?\n\}", PAGE, re.S).group(0)


def test_a_page_row_has_no_count():
    rows = re.findall(r'<button class="db .*?</button></li>', TREE, re.S)
    assert len(rows) == 2, rows                     # the flat 我的訓練 list and the imported views' pages
    for r in rows:
        assert 'class="n"' not in r and "viewer.n_charts" not in r and "visCharts(d).length}" not in r, r


def test_the_tree_still_skips_pages_without_charts():
    # the count is gone, the rule that hides a page with no chart for this setting is not
    assert TREE.count("!visCharts(d).length") == 2


def test_the_view_header_keeps_its_page_count():
    assert TREE.count('class="n"') == 1 and "viewer.n_pages" in TREE and "${view.dashboards.length}</span>" in TREE


def test_the_header_still_counts_the_cards():
    crumb = re.search(r"function renderCrumb\(\) \{.*?\n\}", PAGE, re.S).group(0)
    assert 'n: shownCharts(d)' in crumb                       # SP-506 stays
