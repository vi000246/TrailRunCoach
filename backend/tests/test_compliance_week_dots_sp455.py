"""
SP-455: the 統計頁 本週 card's session dots (SP-400).

* 部分 and 換了課 were the same orange ring — told apart only by the hover title, which a phone has not.
  They keep the colour (no traffic light: SP-393 / SP-398) and differ by shape: 換了課 a dashed ring, and
  each carries its status mark (◐ / ≠, the same marks the table uses) at the corner.
* In the dark theme the ring of a session still ahead (border --line #2e2e2b on the panel #1a1a19, ~1.3:1)
  was barely there; the ring has its own token, ≥ 3:1 on the dark panel.

The page's 本週 block runs under node with a fake DOM; synthetic dots only.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "static" / "compliance.html"
NODE = shutil.which("node")


def _src() -> str:
    return PAGE.read_text("utf-8")


JS = r"""
const vm = require("vm");
const els = {};
const el = (id) => els[id] || (els[id] = { id, innerHTML: "", textContent: "", hidden: false, setAttribute() {} });
const ctx = { $: el, T: (k) => "{" + k + "}", q: () => "", n0: String, h1: String, mdw: String, SW: (s) => "[" + s + "]",
  esc: (s) => String(s ?? ""), renderHighlight: () => {}, window: {} };
vm.runInNewContext(process.argv[1], ctx);
ctx.S = { data: JSON.parse(process.argv[2]) };
vm.runInNewContext("renderNow()", ctx);
console.log(JSON.stringify(el("dots").innerHTML));
"""

WEEK = {"week": {"start": "2026-10-19", "end": "2026-10-25", "completed": 3, "dots": [
    {"uid": "a", "day": "2026-10-19", "title": "輕鬆跑", "kind": "easy", "status": "done"},
    {"uid": "b", "day": "2026-10-20", "title": "間歇", "kind": "quality", "family": "vo2max", "status": "partial"},
    {"uid": "c", "day": "2026-10-21", "title": "LSD", "kind": "long", "status": "off_plan"},
    {"uid": "d", "day": "2026-10-23", "title": "輕鬆跑", "kind": "easy", "status": "upcoming"}]},
    "active_streak": {"weeks": 2, "best": 4}, "adjusted": []}


def _block() -> str:
    src = _src()
    icon = re.search(r"\nconst ICON = \{[^\n]*\n", src)
    now = re.search(r"// 本週 \(SP-400\): the session's icon.*?\n(?=// 這週亮點)", src, re.S)
    assert icon and now, "the 本週 block not found"
    return icon.group(0) + now.group(0)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_partial_and_swapped_dots_carry_their_own_mark():
    r = subprocess.run([NODE, "-e", JS, _block(), json.dumps(WEEK)], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout)
    dots = re.findall(r'<span class="dot (\w+)"[^>]*>(.*?)</span>', html)
    by = dict(dots)
    assert '<i class="mk" aria-hidden="true">◐</i>' in by["partial"]
    assert '<i class="mk" aria-hidden="true">≠</i>' in by["off_plan"]
    assert 'class="mk"' not in by["done"] and by["upcoming"] == ""
    # the hover title stays (a mouse still gets the word)
    assert 'title="2026-10-20 間歇 · [partial]"' in html


def _hex(c: str) -> tuple:
    return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _lum(c: str) -> float:
    lin = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in _hex(c)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_the_swapped_ring_is_dashed_and_the_empty_ring_shows_in_the_dark():
    src = _src()
    assert re.search(r"\.dot \{[^}]*border: 1\.5px solid var\(--ring\)", src)
    assert re.search(r"\.dot\.off_plan \{ border-style: dashed; \}", src)
    # every theme block defines the ring; the dark ones ≥ 3:1 on their panel, the light one no fainter than before
    blocks = re.findall(r"(--panel: (#[0-9a-f]{6})[^}]*?--ring: (#[0-9a-f]{6}))", src)
    assert len(blocks) == 3, blocks
    light, dark1, dark2 = [(p, r) for _all, p, r in blocks]
    for panel, ring in (dark1, dark2):
        assert _ratio(ring, panel) >= 3.0, (ring, panel, _ratio(ring, panel))
    assert _ratio(light[1], light[0]) >= _ratio("#e3e3df", light[0])        # the old ring: --line
