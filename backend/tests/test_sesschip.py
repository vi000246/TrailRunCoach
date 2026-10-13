"""課表卡片共用元件 (SP-395; docs/research/schedule-chip-ui.md §6–7, §10 ①): static/sesschip.js run
under node — the Linear-style completion ring (check / pie filled to the % / slash / empty / dotted),
the card's state classes, the 22px type tile and which icon a session or an activity gets. Completion
is green / yellow / grey only: no red (SP-393). Synthetic sessions only; skipped without node."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

R = 6                      # the pie's radius in the 16 × 16 ring (mockup schedule-chip-ui-mockup.html)
HALF = math.pi * R         # half the circumference of r = R: the pie is a circle of r = R / 2, stroke R wide


def _run(expr: str, **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const ctx = { DATA: JSON.parse(process.argv[3]) };
ctx.window = ctx;
// the page's I18N.t over the zh-TW common catalog (i18n.js, without the request)
const cat = JSON.parse(fs.readFileSync(process.argv[1] + "/i18n/zh-TW/common.json", "utf8"));
ctx.I18N = { t: (k, p) => String(cat[k.replace(/^common\./, "")] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)) };
vm.createContext(ctx);
for (const f of ["dashicons.js", "sesschip.js"]) vm.runInContext(fs.readFileSync(process.argv[1] + "/" + f, "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _dash(svg: str) -> list[float]:
    m = re.search(r'stroke-dasharray="([\d.]+) ([\d.]+)"', svg)
    assert m, svg
    return [float(m.group(1)), float(m.group(2))]


def _s(state="done", level=None, cp=None, **kw):
    s = {"uid": "u1", "kind": "easy", "title": "輕鬆跑", "day": "2026-10-07", "state": state, "minutes": 45, **kw}
    if level:
        s["compliance"] = {"level": level, "pct": 90, **(cp or {})}
    return s


def test_partial_pie_fills_to_the_percent_of_half_the_circumference():
    a, b = _dash(_run('SessChip.ring("partial", 72)'))
    assert a == pytest.approx(0.72 * HALF, abs=0.01) and b == pytest.approx(HALF, abs=0.01)
    assert _dash(_run('SessChip.ring("partial", 150)'))[0] == pytest.approx(HALF, abs=0.01)    # > 100 % capped
    assert _dash(_run('SessChip.ring("partial", null)'))[0] == pytest.approx(HALF, abs=0.01)   # no % = full
    assert _dash(_run('SessChip.ring("partial", 0)'))[0] == 0


def test_ring_shapes():
    done = _run('SessChip.ring("done")')
    assert 'd="m5 8.3 2 2L11 6"' in done and "stroke-dasharray" not in done            # check
    off = _run('SessChip.ring("off_plan", 187)')
    assert 'd="M11 5 5 11"' in off and "m5 8.3" not in off                              # slash, no pie
    for st in ("missed", "todo"):
        empty = _run(f'SessChip.ring("{st}")')
        assert "<path" not in empty and "stroke-dasharray" not in empty, st             # an empty ring
    dotted = _run('SessChip.ring("todo", null, { prov: true })')
    assert 'stroke-dasharray="2 1.6"' in dotted and "<path" not in dotted                # provisional
    for st in ("done", "partial", "off_plan", "missed", "todo"):
        svg = _run(f'SessChip.ring("{st}", 50)')
        assert svg.startswith("<svg") and 'aria-hidden="true"' in svg and f"sc-r-{st}" in svg, st


def test_status_and_class_of_every_state():
    cases = {
        "done": _s(level="green"),
        "partial": _s(level="yellow"),
        "partial_soft": _s(level="yellow", cp={"wrong_type": True, "soft": "sport"}),
        "off_plan": _s(level="yellow", cp={"off_plan": True}),
        "off_wrong": _s(level="red", cp={"wrong_type": True}),
        "red_time": _s(level="red"),                         # time / TSS > 50 % off: 部分, not 換了課
        "missed": _s("missed", level="missed"),
        "future": _s("active", day="2099-01-01"),
        "provisional": _s("active", day="2099-01-01", provisional=True),
        "done_no_cp": _s(),                                  # a done session with no compliance (heat bath …)
    }
    got = _run("Object.fromEntries(Object.entries(DATA.c).map(([k, s]) => [k, [SessChip.status(s), SessChip.cls(s)]]))",
               c=cases)
    st = {k: v[0] for k, v in got.items()}
    cls = {k: set(v[1].split()) for k, v in got.items()}
    assert st == {"done": "done", "partial": "partial", "partial_soft": "partial", "off_plan": "off_plan",
                  "off_wrong": "off_plan", "red_time": "partial", "missed": "missed", "future": "todo",
                  "provisional": "todo", "done_no_cp": "done"}
    assert cls["done"] == {"done", "cp-ok"} and cls["done_no_cp"] == {"done", "cp-ok"}
    assert cls["partial"] == cls["partial_soft"] == cls["red_time"] == {"done", "cp-part"}
    assert cls["off_plan"] == cls["off_wrong"] == {"done", "cp-off"}
    assert cls["missed"] == {"miss"}
    assert cls["future"] == {"fut"} and cls["provisional"] == {"fut", "prov"}


def test_no_red_for_completion():
    src = (STATIC / "sesschip.js").read_text(encoding="utf-8")
    assert "--bad" not in src and "#c62828" not in src.lower()
    css = _run("SessChip.CSS")
    assert re.search(r"\.cp-ok\s*\{\s*--cp:\s*var\(--good\)", css)
    assert re.search(r"\.cp-part, \.sc\.cp-off\s*\{\s*--cp:\s*var\(--watch\)", css)
    assert re.search(r"\.sc-r-missed, \.sc-r-todo\s*\{\s*color:\s*var\(--faint\)", css)


def test_labels():
    got = _run('["done", "partial", "off_plan", "missed", "todo"].map((s) => SessChip.label(s, 72))'
               ' .concat([SessChip.label("todo", null, { prov: true }), SessChip.label("partial", null)])')
    assert got == ["完成", "部分 72%", "換了課", "跳過", "還沒做", "暫定", "部分"]


def test_type_tile_icons_and_badge():
    assert _run('typeof DI("x") === "string" && DI("bike") !== DI("nope") && DI("other") !== DI("nope")')
    tile = _run('SessChip.icon("bike")')
    assert tile.startswith('<span class="sc-ic" aria-hidden="true">') and _run('DI("bike")') in tile
    assert _run('SessChip.icon("quality", { fam: "vo2max" }) === SessChip.icon("z5")')
    assert _run('SessChip.icon("quality", { fam: "speed" }) === SessChip.icon("z5")')
    assert _run('SessChip.icon("quality", { fam: "aerobic" }) === SessChip.icon("z3")')
    assert '<b class="bd">↻</b>' in _run('SessChip.icon("easy", { badge: \'<b class="bd">↻</b>\' })')


def test_session_kind_icon():
    s = lambda **kw: {"kind": "easy", "title": "輕鬆跑", **kw}
    cases = [s(), s(kind="long", title="LSD"), s(kind="hike", title="越野跑"), s(kind="test", title="CP 測試"),
             s(kind="strength", title="肌力"), s(kind="race", title="比賽"), s(kind="notice", title="課表待確認"),
             s(kind="heat_passive", title="熱水浴"), s(title="熱適應輕鬆跑"),
             s(kind="quality", title="有氧間歇 2×15 分", quality_family={"id": "aerobic"}),
             s(kind="quality", title="VO2max 間歇 5×3 分", quality_family={"id": "vo2max"}),
             s(kind="quality", title="速度 8×200 m", quality_family={"id": "speed"}),
             s(kind="quality", title="VO2max 間歇"), s(kind="quality", title="巡航"),
             s(kind="long", title="B2B 長跑"), s(kind="strength", title="ME 肌耐力")]
    assert _run("DATA.c.map((x) => SessChip.kind(x))", c=cases) == [
        "easy", "long", "hike", "test", "strength", "race", "notice", "heat", "heat",
        "z3", "z5", "z5", "z5", "z3", "b2b", "loaded"]


def test_activity_kind_icon():
    a = lambda cat, icon=None: {"category": cat, **({"session": {"icon": icon}} if icon else {})}
    cases = [a("bike"), a("walk"), a("hike"), a("strength"), a("other"), a("road"), a("trail"),
             a("road", "z5"), a("trail", "long"), a("road", "intensity"), a("bike", "z3")]
    assert _run("DATA.c.map((x) => SessChip.actKind(x))", c=cases) == [
        "bike", "hike", "hike", "strength", "other", "easy", "easy", "z5", "long", "easy", "bike"]
