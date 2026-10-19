"""
SP-458: on a phone (390 px) chart text sat on other text or lines.

* 單次活動 › 疊加圖 (charts.html mcOverOption) and 賽事計算機 › 配速／海拔圖 (racepower.html): the pace axis
  is inverted (faster up), so its name at the axis's "end" was drawn at the bottom — 「配速 /km」 on the
  time ticks, 「/km」 on the km axis's 「0」. An inverted axis puts its name at "start", which is the top.
* 圖表分析 › 負荷 PMC (charts.html, SP-219's projection): a race's name runs along the top of its dashed
  line, where the dashed CTL / ATL projections cross it — the B race's orange on ATL's. The name sits on
  a chip of the panel colour, so the lines pass under it.
"""
import re
import shutil
from pathlib import Path

import pytest

from backend.tests.test_viewer_mapchart import SEGS, _run, _samples

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_overlay_names_the_pace_axis_at_the_top():
    s, vals = _samples()
    two = _run("mcOverOption(DATA.s, ['power', 'pace'], DATA.vals, 't', DATA.segs)", s=s, vals=vals, segs=SEGS)
    pw, pace = two["yAxis"][1:]
    assert pace["inverse"] is True and pace["nameLocation"] == "start"     # inverted: start = the top
    assert pw.get("inverse") is not True and pw["nameLocation"] == "end"    # not inverted: end = the top
    left = _run("mcOverOption(DATA.s, ['pace', 'hr'], DATA.vals, 't', DATA.segs)", s=s, vals=vals, segs=SEGS)
    assert [(y["inverse"], y["nameLocation"]) for y in left["yAxis"][1:]] == [(True, "start"), (False, "end")]


def test_the_calculators_pace_axis_names_itself_at_the_top():
    src = (STATIC / "racepower.html").read_text("utf-8")
    m = re.search(r"const yMain = \{[^\n]*\n", src)
    assert m, "the profile chart's main y axis not found"
    assert 'nameLocation: M.inverse ? "start" : "end"' in m.group(0) and "inverse: M.inverse" in m.group(0)
    # pace is the inverted one
    assert re.search(r'pace: \{[^\n]*unit: "/km", inverse: true', src)


def test_a_race_name_on_the_pmc_sits_on_a_chip_over_the_projection_lines():
    src = (STATIC / "charts.html").read_text("utf-8")
    m = re.search(r"for \(const r of cat \? \[\] : PJ\.races \|\| \[\]\) \{.*?\n    \}\n", src, re.S)
    assert m, "the projection's race loop not found"
    lab = re.search(r"markLine: \{[^}]*?lineStyle: \{[^}]*\},\s*(?://[^\n]*\n\s*)*label: \{(.*?)\},\s*\n\s*data:", m.group(0), re.S)
    assert lab, "the race line's label not found"
    assert 'formatter: `${r.grade} ${r.name}`' in lab.group(1)
    assert 'backgroundColor: css("--panel")' in lab.group(1) and "padding:" in lab.group(1)
