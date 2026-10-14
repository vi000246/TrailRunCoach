"""分析頁頁首的圖表張數扣掉被隱藏的卡 (SP-506).

The header's 「N 張圖」 (renderCrumb) counted the page's definition (visCharts), so a non-test activity's
本次重點 said 「5 張圖」 with the CP-test card taken off (AN8, SP-487) and four cards on the screen; the same
for the interval cards on an easy run and the segments card (AN12). Now it counts the cards on the grid
once load() has drawn the page there. The page's own marked block runs under node with a stub grid;
the real page is checked in e2e/test_viewer_card_count_sp506_e2e.py."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = (STATIC / "wko5_viewer.html").read_text("utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")

BLOCK = re.search(r"// ---- card count \(SP-506\) ----.*?// ---- end card count ----", PAGE, re.S)

# a grid holding `cards` section.card children (and a description <p>), the page `page` on it
STUBS = r"""
const D = DATA;
const S = { mode: D.mode, dash: { season: 0, workout: D.dash }, workout: D.workout };
const curView = () => ({ name: "單次活動判讀" });
const visCharts = (d) => d.charts;
const GRID = { dataset: D.page == null ? {} : { page: D.page },
  querySelectorAll: (sel) => { if (sel !== ":scope > section.card") throw new Error(sel); return { length: D.cards }; } };
const $ = (id) => { if (id !== "grid") throw new Error(id); return GRID; };
"""


def _count(*, page, cards, defined=6, mode="workout", dash=0, workout=12):
    js = r"""
const vm = require("vm");
const ctx = { DATA: JSON.parse(process.argv[1]) };
vm.createContext(ctx);
vm.runInContext(process.argv[2] + process.argv[3] + "\nOUT = shownCharts({ charts: new Array(DATA.defined).fill(0) });", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    data = {"page": page, "cards": cards, "defined": defined, "mode": mode, "dash": dash, "workout": workout}
    r = subprocess.run([NODE, "-e", js, json.dumps(data), STUBS, BLOCK.group(0)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_page_has_the_marked_block():
    assert BLOCK, "wko5_viewer.html: // ---- card count (SP-506) ---- block"


@needs_node
def test_a_drawn_page_counts_the_cards_on_the_grid():
    # 本次重點 of a non-test run: 6 charts defined, the CP-test card taken off → 5 on the screen
    assert _count(page="workout|單次活動判讀|0|12", cards=5) == 5
    # every review card of the page taken off (the 間歇 page of an easy run): 0, not the definition
    assert _count(page="workout|單次活動判讀|2|12", cards=0, dash=2) == 0
    # the segments card added after the viewer's own cards counts too
    assert _count(page="workout|單次活動判讀|0|12", cards=7) == 7


@needs_node
def test_before_the_page_is_drawn_the_definition():
    assert _count(page=None, cards=0) == 6                              # nothing drawn yet / no activity
    assert _count(page="workout|單次活動判讀|0|11", cards=4) == 6         # the grid holds another activity
    assert _count(page="workout|單次活動判讀|1|12", cards=4) == 6         # ... or another page
    assert _count(page="season|單次活動判讀|0|", cards=4) == 6            # ... or the other mode


@needs_node
def test_season_pages_ignore_the_activity():
    assert _count(page="season|單次活動判讀|0|", cards=3, mode="season", workout=12) == 3


def test_the_header_and_load_use_the_count():
    crumb = re.search(r"function renderCrumb\(\) \{.*?\n\}", PAGE, re.S).group(0)
    assert 'n: shownCharts(d)' in crumb and "visCharts(d).length" not in crumb
    load = re.search(r"async function load\(\) \{.*?\n\}", PAGE, re.S).group(0)
    # the old page's cards are not this page's count; the new ones are once they are on the grid
    assert load.index("delete grid.dataset.page") < load.index('grid.innerHTML = ""')
    assert load.index("grid.appendChild(card)") < load.index("grid.dataset.page = gridPage()")
    # a card taken off later (draw → res.hide) or the segments card coming and going: the header follows
    assert re.search(r"new MutationObserver\(\(\) => \{ if \(\$\(\"grid\"\)\.dataset\.page === gridPage\(\)\) renderCrumb\(\); \}\)"
                     r"\.observe\(\$\(\"grid\"\), \{ childList: true \}\)", PAGE)
