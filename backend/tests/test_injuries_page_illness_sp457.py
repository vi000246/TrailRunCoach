"""
SP-457: 傷病紀錄 after 生病 came in (SP-117) still looked like an injury in two places.

* 備註's example was an injury's (「下坡右膝外側；看過復健科」) for an illness too — it follows the kind now.
* The timeline did draw an illness (GET /timeline's bands carry every event, `category` on each), but as an
  injury of 「未指定部位」 (an illness is stored with area 「unknown」): grey, the legend saying 未指定部位, and
  a few days on an 18-month axis ~3 px wide. An illness band has its own colour and legend entry 「生病」, no
  severity in its tip, and every band is at least 5 px wide.

The page's timeline block runs under node with a fake DOM; synthetic events only.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "static" / "injuries.html"
NODE = shutil.which("node")


def _src() -> str:
    return PAGE.read_text("utf-8")


def _blocks() -> str:
    src = _src()
    colours = re.search(r"const AREA_COL = .*?const NOTE_EX = [^\n]*\n", src, re.S)
    tl = re.search(r"// ---- timeline -+\n(.*?)// ---- list", src, re.S)
    assert colours and tl, "the page's colour / timeline blocks not found"
    return colours.group(0) + tl.group(1)


JS = r"""
const vm = require("vm");
const els = {};
const el = (id) => els[id] || (els[id] = { id, innerHTML: "", clientWidth: 360 });
const ctx = {
  $: el, esc: (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  ymd: (s) => String(s).slice(5), hoverable: () => {}, todayIso: () => "2026-10-19",
  S: { metric: "run_h", months: 18, acwr: false, meta: { areas: [{ key: "knee", label: "膝" }] } },
};
vm.runInNewContext(process.argv[1], ctx);
ctx.S.tl = JSON.parse(process.argv[2]);
vm.runInNewContext("drawTimeline()", ctx);
console.log(JSON.stringify({ svg: el("tl").innerHTML, leg: el("tl-leg").innerHTML,
                             ill: vm.runInNewContext("ILL_COL", ctx), knee: vm.runInNewContext("areaCol('knee')", ctx) }));
"""

TL = {"begin": "2025-04-19", "end": "2026-10-19",
      "weeks": [{"week": "2026-10-05", "run_h": 3.0, "desc": 400, "climb": 400, "trail_h": 1.0, "ctl": 30, "acwr": 1.0}],
      "bands": [{"id": 1, "start": "2026-10-08", "end": "2026-10-11", "open": False, "area": "unknown",
                 "label": "生病（發燒或全身症狀）", "category": "illness", "severity": "mild", "severity_label": "輕",
                 "status": "resolved", "kind": "overuse"},
                {"id": 2, "start": "2026-03-01", "end": "2026-04-10", "open": False, "area": "knee", "label": "右膝",
                 "category": "injury", "severity": "moderate", "severity_label": "中", "status": "resolved", "kind": "acute"}],
      "ticks": []}


def _bands(svg: str) -> dict:
    """data-ev → (fill, width, tip) of each band's first rect."""
    out = {}
    for m in re.finditer(r'<g data-tip-html="([^"]*)" data-ev="(\d+)"[^>]*><rect x="[^"]*" y="[^"]*" width="([^"]*)" '
                         r'height="[^"]*" fill="([^"]*)"', svg):
        out[int(m.group(2))] = (m.group(4), float(m.group(3)), m.group(1))
    return out


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_an_illness_shows_on_the_timeline_in_its_own_colour():
    r = subprocess.run([NODE, "-e", JS, _blocks(), json.dumps(TL)], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    b = _bands(out["svg"])
    ill_fill, ill_w, ill_tip = b[1]
    assert ill_fill == out["ill"] != "#9aa3ad"                  # not the grey of 未指定部位
    assert ill_w >= 5                                            # 4 days on 18 months still shows
    assert "生病" in ill_tip and "・輕" not in ill_tip and "急性" not in ill_tip   # no injury severity on an illness
    knee_fill, _w, knee_tip = b[2]
    assert knee_fill == out["knee"] and "・中" in knee_tip and "急性" in knee_tip       # an injury: as before
    assert ">生病</span>" in out["leg"] and "未指定部位" not in out["leg"] and ">膝</span>" in out["leg"]


def test_the_note_example_follows_injury_or_illness():
    src = _src()
    ex = re.search(r"const NOTE_EX = \{ injury: \"([^\"]+)\", illness: \"([^\"]+)\" \};", src)
    assert ex and "右膝" in ex.group(1) and "發燒" in ex.group(2) and "膝" not in ex.group(2)
    dlg = re.search(r"\nfunction renderDlg\(\) \{\n(.*?)\n\}\n", src, re.S)
    assert dlg and '$("f-note").placeholder = NOTE_EX[ill ? "illness" : "injury"]' in dlg.group(1)
    # the static placeholder (the dialog's first paint) is the injury one, the default kind
    assert f'id="f-note" rows="2" maxlength="1000" placeholder="{ex.group(1)}"' in src
