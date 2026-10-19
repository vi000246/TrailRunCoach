"""SP-686: the banded charts (負荷平衡 % on the 負荷 and 減量期 pages, 負荷比 on the 負荷 page and the 課表統計 page)
draw no edge lines between their bands and no grid lines; the reference line (0 / 1 = 跟平常一樣) is the one solid,
strong line; the band names stay in the right margin (static/charts.html zonedSetup, static/compliance.html
renderRatio)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHARTS = (ROOT / "backend" / "static" / "charts.html").read_text("utf-8")
COMPL = (ROOT / "backend" / "static" / "compliance.html").read_text("utf-8")


def _fn(src: str, head: str) -> str:
    i = src.index(head)
    j = src.index("\n}\n", i)
    return src[i:j]


def test_viewer_bands_have_no_edge_lines_and_the_reference_line_is_solid():
    z = _fn(CHARTS, "function zonedSetup(")
    assert "b.o.markLine" not in z                                       # no line on a band's edges
    assert 'position: "right"' in z                                      # the names in the right margin
    # the 0 line (an hline series) and zoned.ref (負荷比's 1): solid, 1.5 px, the strong ink
    assert re.search(r'kind === "hline".*type: "solid", color: refInk, width: 1\.5', z)
    assert re.search(r'L\.markLine = \{[^}]*lineStyle: \{ color: refInk, type: "solid", width: 1\.5 \}', z)
    assert "type: [4, 4]" not in z                                       # not dashed any more
    # and the zoned chart switches its grid lines off
    assert re.search(r"if \(Z\) \{\s*//[^\n]*\n\s*for \(const a of yAxis\) a\.splitLine = \{ show: false \};", CHARTS)


def test_compliance_ratio_matches():
    r = _fn(COMPL, "function renderRatio(")
    i = r.index("const markLine")
    ml = r[i:r.index("ch.setOption", i)]
    assert "RATIO_BANDS" not in ml                                       # no band-edge lines
    assert 'yAxis: 1, lineStyle: { color: refInk, type: "solid", width: 1.5 }' in ml
    assert "splitLine: { show: false }" in r


def test_every_load_balance_pct_chart_is_zoned_with_a_zero_line():
    seen = 0
    for name in ("training", "periodization"):
        v = json.loads((ROOT / "views" / f"{name}.json").read_text("utf-8"))
        for d in v["dashboards"]:
            for c in d["charts"]:
                if c.get("title", "").startswith("負荷平衡 %"):
                    seen += 1
                    assert c["zoned"]["line"].startswith("負荷平衡 %")
                    assert any(s["expression"] == "(,0)" for s in c["series"])
                if c.get("id") == "load-ratio":
                    assert c["zoned"]["ref"]["y"] == 1
    assert seen == 2                                                     # 負荷 page and 減量期 page
