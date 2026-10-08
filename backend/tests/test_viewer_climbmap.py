"""
爬坡與地形 › 爬坡段 (SP-218; wko5_viewer.html drawClimbMap): the route map over the elevation profile
against km, metric panels picked by buttons, climbs shaded, a picked climb outlined on the map and the
profile, the climb table with 「跟平常比」. The page's climb-map block's pure helpers run under node;
synthetic data only.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "static" / "wko5_viewer.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _block() -> str:
    m = re.search(r"// ---- climb map \(SP-218\) ----.*?// ---- end climb map ----", PAGE.read_text(encoding="utf-8"), re.S)
    assert m, "climb map block not found"
    return m.group(0)


def _run(expr: str, **data):
    js = r"""
const vm = require("vm");
const ctx = {
  DATA: JSON.parse(process.argv[2]),
  pctl: (vals, p) => { const v = [...vals].sort((a, b) => a - b); return v[Math.min(v.length - 1, Math.max(0, Math.round(p * (v.length - 1))))]; },
};
vm.runInNewContext(process.argv[1] + "\nOUT = (" + process.argv[3] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, _block(), json.dumps(data), expr], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


CLIMBS = [{"no": "①", "start_km": 1.0, "end_km": 2.5}, {"no": "②", "start_km": 4.0, "end_km": 5.0}]


def test_pick_defaults_to_vam_and_hr_and_drops_metrics_without_data():
    has = "(id) => DATA.has.includes(id)"
    assert _run(f"cmPick(undefined, {has})", has=["vam", "hr", "power", "gap"]) == ["vam", "hr"]
    assert _run(f"cmPick(['gap', 'power'], {has})", has=["vam", "hr", "gap"]) == ["gap"]      # no power here
    assert _run(f"cmPick([], {has})", has=["vam"]) == []


def test_climb_at_a_distance():
    assert _run("cmClimbAt(DATA.c, 1.7)", c=CLIMBS) == 0
    assert _run("cmClimbAt(DATA.c, 4.0)", c=CLIMBS) == 1
    assert _run("cmClimbAt(DATA.c, 3.0)", c=CLIMBS) == -1
    assert _run("cmClimbAt(DATA.c, null)", c=CLIMBS) == -1


def test_segment_is_the_gps_samples_inside_the_climb():
    s = {"d": [0.5, 1.0, 1.5, None, 2.0, 2.6, 3.0], "lat": [1, 1, None, 1, 1, 1, 1], "lng": [2, 2, 2, 2, 2, 2, 2]}
    assert _run("cmSegment(DATA.s, 1.0, 2.5)", s=s) == [1, 4]      # no fix at index 2, no distance at 3
    assert _run("cmSegment(DATA.s, 5, 6)", s=s) == []
    assert _run("cmSegment(null, 1, 2)") == []


def test_vs_the_usual():
    b = {"ok": True, "median": 600, "q1": 550, "q3": 650, "n": 7, "weeks": 8}
    hi = _run("cmVs(720, DATA.b)", b=b)
    assert hi["out"] is True and hi["d"] == pytest.approx(0.2)
    assert _run("cmVsText(cmVs(720, DATA.b))", b=b) == "▲+20%"
    assert _run("cmVsText(cmVs(480, DATA.b))", b=b) == "▼−20%"
    assert _run("cmVsText(cmVs(610, DATA.b))", b=b) == "≈"                       # inside the middle 50 %
    assert _run("cmVs(610, DATA.b)", b={**b, "ok": False}) is None              # < 5 similar climbs
    assert _run("cmVsText(cmVs(null, DATA.b))", b=b) == "–"


def test_option_elevation_on_top_then_one_panel_per_metric_with_the_climbs_shaded():
    n = 40
    P = {"x": [i * 0.15 for i in range(n)], "alt": [100 + i * 5 for i in range(n)], "vam": [600] * n, "hr": [150] * n,
         "gap": [400 + (i % 3) for i in range(n)]}
    T = {"elev": "#777777", "up": "#eb6834", "line": "#dddddd", "muted": "#888888", "ink": "#111111", "panel": "#ffffff",
         "colors": {"vam": "#1f5fbf", "hr": "#e34948", "gap": "#2563eb"}, "names": {"elev": "海拔", "vam": "VAM", "hr": "心率", "gap": "GAP"},
         "units": {"vam": "m/h", "hr": "bpm", "gap": "/km"}}
    o = _run("cmOption(DATA.P, ['vam', 'hr', 'gap'], DATA.c, 1, DATA.T)", P=P, c=CLIMBS, T=T)
    assert len(o["grid"]) == len(o["xAxis"]) == len(o["yAxis"]) == len(o["series"]) == 4
    assert o["grid"][0]["height"] > o["grid"][1]["height"]                     # the elevation is the big panel
    assert all(x["min"] == 0 and x["max"] == pytest.approx(5.85) for x in o["xAxis"])   # one km range
    assert o["axisPointer"]["link"] == [{"xAxisIndex": "all"}]
    assert [s["id"] for s in o["series"]] == ["alt", "vam", "hr", "gap"]
    assert o["yAxis"][3]["inverse"] is True and not o["yAxis"][1].get("inverse")   # faster GAP up
    assert o["series"][0]["data"][2] == [0.3, 110] and "areaStyle" in o["series"][0]
    elev_areas = o["series"][0]["markArea"]["data"]
    assert [a[0]["xAxis"] for a in elev_areas] == [1.0, 4.0] and [a[1]["xAxis"] for a in elev_areas] == [2.5, 5.0]
    assert elev_areas[0][0]["label"]["formatter"] == "①"                       # labelled on the elevation only
    assert elev_areas[1][0]["itemStyle"]["borderWidth"] > 0 and elev_areas[0][0]["itemStyle"]["borderWidth"] == 0   # ② picked
    assert len(o["series"][2]["markArea"]["data"]) == 2 and o["series"][2]["markArea"]["data"][0][0]["label"]["show"] is False
    assert [g["style"]["text"] for g in o["graphic"]] == ["海拔 m", "VAM m/h", "心率 bpm", "GAP /km"]
    assert _run("cmHeight(3) > cmHeight(1)")


T0 = {"elev": "#777777", "up": "#eb6834", "line": "#dddddd", "muted": "#888888", "ink": "#111111", "panel": "#ffffff",
      "colors": {}, "names": {"elev": "海拔"}, "units": {}}


def test_a_pick_is_a_merge_of_the_shading_only():
    """SP-218 review M3: picking / previewing a climb patches only the series' markArea (by id), no rebuild."""
    o = _run("cmShade(['vam', 'hr'], DATA.c, 0, DATA.T)", c=CLIMBS, T=T0)
    assert list(o) == ["series"] and [s["id"] for s in o["series"]] == ["alt", "vam", "hr"]
    assert all(set(s) == {"id", "markArea"} for s in o["series"])
    elev = o["series"][0]["markArea"]["data"]
    assert elev[0][0]["itemStyle"]["borderWidth"] > 0 and elev[1][0]["itemStyle"]["borderWidth"] == 0
    assert elev[0][0]["label"]["show"] is True and o["series"][1]["markArea"]["data"][0][0]["label"]["show"] is False
    assert _run("JSON.stringify(cmShade([], DATA.c, -1, DATA.T).series[0].markArea.data) === "
                "JSON.stringify(cmOption({x: [0, 6], alt: [1, 2]}, [], DATA.c, -1, DATA.T).series[0].markArea.data)",
                c=CLIMBS, T=T0)


def test_click_and_tap_rules():
    # mouse: a climb picks it, the same climb again or off the climbs clears
    assert _run("[cmClickPick(-1, 1, false), cmClickPick(1, 1, false), cmClickPick(1, -1, false), cmClickPick(0, 1, false)]") == [1, -1, -1, 1]
    # tap: only ever picks another climb — tapping the picked one or between climbs reads values, keeps the pick
    assert _run("[cmClickPick(-1, 1, true), cmClickPick(1, 1, true), cmClickPick(1, -1, true), cmClickPick(0, 1, true)]") == [1, 1, 1, 1]


def test_refresh_skips_unchanged_picks_and_never_rebuilds_the_profile():
    html = PAGE.read_text(encoding="utf-8")
    body = html[html.index("async function drawClimbMap"):html.index("// ---- end climb map ----")]
    ref = body[body.index("function refresh(fit, force = false) {"):body.index("function select(i)")]
    assert 'if (key === last && !force) return;' in ref and "shade();" in ref and "paint()" not in ref
    a = body.index("shade = () => { if (!chart.isDisposed())")
    sh = body[a:body.index("\n", a)]
    assert "resize" not in sh and "true)" not in sh                            # a merge, no notMerge, no resize
    assert "select(cmClickPick(sel, i, isTouch(e.event)))" in body             # the profile's click / tap
    assert "_zoom: res._zoom" in body                                          # the enlarged copy's map


def test_option_without_metric_panels_is_the_elevation_alone():
    P = {"x": [0, 1, 2], "alt": [10, 20, 30]}
    T = {"elev": "#777777", "up": "#eb6834", "line": "#dddddd", "muted": "#888888", "ink": "#111111", "panel": "#ffffff",
         "colors": {}, "names": {"elev": "海拔"}, "units": {}}
    o = _run("cmOption(DATA.P, [], [], -1, DATA.T)", P=P, T=T)
    assert len(o["grid"]) == 1 and o["series"][0]["markArea"]["data"] == []
    assert o["xAxis"][0]["axisLabel"].get("show") is not False                  # the km labels sit on the last panel
