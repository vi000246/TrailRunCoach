"""
單次活動 map + chart (SP-80; wko5_viewer.html): the route map gets a time-axis chart of
elevation, HR, power, pace and grade beside it (stacked panels, picked by buttons, map on
top or on the left), and the activity charts join the synced hover. The page's map-chart
block runs under node; synthetic samples only.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "wko5_viewer.html"
NODE = shutil.which("node")


def _block() -> str:
    m = re.search(r"// ---- map chart \(SP-80\).*?// ---- end map chart ----", PAGE.read_text(encoding="utf-8"), re.S)
    assert m, "map chart block not found"
    return m.group(0)


def _run(expr: str, **data) -> object:
    js = r"""
const vm = require("vm");
const ctx = {
  DATA: JSON.parse(process.argv[2]),
  t: (k) => k, esc: (s) => String(s), css: (v) => v, fmtDur: (s) => String(Math.round(s)),
  pctl: (vals, p) => { const v = [...vals].sort((a, b) => a - b); return v[Math.min(v.length - 1, Math.max(0, Math.round(p * (v.length - 1))))]; },
  ACT: { pace: (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`, tick: (v) => String(v),
         interval: () => 600, tipBox: (h) => h,
         theme: () => ({ hr: "r", pw: "y", ink: "k", ink2: "g", line: "l", muted: "m", text: "t", panel: "p" }) },
};
vm.runInNewContext(process.argv[1] + "\nOUT = (" + process.argv[3] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, _block(), json.dumps(data), expr], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_pace_from_distance_over_a_moving_window():
    t = list(range(0, 121))                                   # 1 s steps
    # 3 m/s = 10.8 km/h → 333 s/km, with per-second GPS jitter of ±1 m/s (distance stays cumulative)
    step = [0.004 if i % 2 else 0.002 for i in t]
    d = [sum(step[:i]) for i in t]
    pace = _run("mcPace(DATA.t, DATA.d)", t=t, d=d)
    good = [p for p in pace if p]
    assert len(good) == len(t) and pace[60] == pytest.approx(3600 / 10.8, rel=0.03)
    assert max(good) / min(good) < 1.1                        # the ±33 % jitter is smoothed away
    stop = _run("mcPace(DATA.t, DATA.d)", t=t, d=[0.5] * 121)  # standing still: no pace
    assert all(p is None for p in stop)
    assert _run("mcPace(DATA.t, null)", t=t) == [None] * 121
    gaps = _run("mcPace(DATA.t, DATA.d)", t=[0, None, 10, 20], d=[0, None, 0.05, 0.1])
    assert gaps[1] is None and gaps[2] == pytest.approx(200, rel=0.01)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_pick_keeps_the_order_and_drops_metrics_without_data():
    has = "(id) => DATA.has.includes(id)"
    assert _run(f"mcPick(undefined, {has})", has=["elev", "hr", "pace", "grade"]) == ["elev", "hr", "pace"]
    assert _run(f"mcPick(['grade', 'hr'], {has})", has=["elev", "hr", "grade"]) == ["hr", "grade"]
    assert _run(f"mcPick([], {has})", has=["elev"]) == []


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_option_one_panel_per_metric_on_the_time_axis():
    n = 50
    s = {"t": [i * 10 for i in range(n)], "d": [i * 0.03 for i in range(n)], "byT": {"xs": [i * 10 for i in range(n)]}}
    vals = {"elev": [100 + i for i in range(n)], "hr": [140] * n, "pace": [300] * n}
    o = _run("mcOption(DATA.s, ['elev', 'hr', 'pace'], DATA.vals)", s=s, vals=vals)
    assert len(o["grid"]) == len(o["xAxis"]) == len(o["yAxis"]) == len(o["series"]) == 3
    assert [x["gridIndex"] for x in o["xAxis"]] == [0, 1, 2] and o["xAxis"][0]["min"] == 0 and o["xAxis"][0]["max"] == 490
    assert o["axisPointer"]["link"] == [{"xAxisIndex": "all"}]               # one hover line through every panel
    assert o["yAxis"][2]["inverse"] is True and not o["yAxis"][0]["inverse"]  # faster pace up
    assert o["series"][1]["data"][3] == [30, 140] and "areaStyle" in o["series"][0]
    assert [g["style"]["text"] for g in o["graphic"]] == ["viewer.mapchart.elev m", "viewer.mapchart.hr bpm",
                                                          "viewer.mapchart.pace /km"]
    # only the bottom panel labels the time axis
    assert o["xAxis"][2]["axisLabel"].get("show") is not False and o["xAxis"][0]["axisLabel"]["show"] is False


def test_the_activity_charts_join_the_synced_hover():
    page = PAGE.read_text(encoding="utf-8")
    for fn in ("drawActHrPower", "drawActHrTrend"):
        body = re.search(rf"function {fn}\(.*?\n}}\n", page, re.S).group(0)
        assert "hoverAct(chart)" in body, fn
    assert "mapChart(card, tb, wrap, s)" in page and 'map.on("click"' in page
    # the time offset: activity_charts' x starts at the first sample
    assert "hoverChart(chart, s, \"t\", 1, s.byT ? s.byT.xs[0] : 0)" in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "viewer.json").read_text(encoding="utf-8"))
        used = set(re.findall(r'"viewer\.(mapchart\.\w+)"', page))
        assert used and used <= set(cat), (loc, used - set(cat))
