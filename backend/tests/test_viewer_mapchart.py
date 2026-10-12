"""
單次活動 map + chart (SP-80; wko5_viewer.html): the route map gets a time-axis chart of
elevation, HR, power, pace and grade beside it (stacked panels, picked by buttons, map on
top or on the left), and the activity charts join the synced hover. The page's map-chart
block runs under node; synthetic samples only.

SP-217: the chart's default is 疊加 — one grid, the elevation a pale background with no
scale shaded by |grade| band (< 3, 3–8, 8–15, 15–25, ≥ 25 %; grade over a ≥ 100 m
window, runs under 200 m merged into a neighbour), HR / power / pace on top with at most
two scales (first left, second right); 分開 is the SP-80 panels; x is time or distance;
power is a 30-s average. Choices are remembered per browser.
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
  localStorage: (() => { const m = new Map(); return { getItem: (k) => m.has(k) ? m.get(k) : null, setItem: (k, v) => m.set(k, String(v)) }; })(),
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


# ---- SP-217: 疊加 (overlay) ------------------------------------------------------------------

def _profile():
    """0–3 km every 10 m: flat 1 km at 100 m, +10 % for 1 km, −10 % for 1 km."""
    d = [round(i * 0.01, 4) for i in range(301)]
    elev = [100.0 if x <= 1 else 100 + (x - 1) * 100 if x <= 2 else 200 - (x - 2) * 100 for x in d]
    return d, elev


def _span(o):
    """a run's length in km on the 10-m grid used below (a … b inclusive, next run starts at b + 1)"""
    return (o["b"] - o["a"] + 1) * 0.01


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_power_is_a_30_s_centred_average():
    t = list(range(121))
    p = [100 if i % 2 else 300 for i in t]
    sm = _run("mcSmooth(DATA.t, DATA.p)", t=t, p=p)
    assert all(v == pytest.approx(200, rel=0.04) for v in sm)       # the per-second swing is averaged away
    assert _run("mcSmooth(DATA.t, DATA.p)", t=t, p=[250] * 121) == [250] * 121
    # a step: the average moves over ±15 s around it, centred (no lag against the map position)
    step = _run("mcSmooth(DATA.t, DATA.p)", t=t, p=[0] * 60 + [300] * 61)
    assert step[30] == 0 and step[90] == 300 and step[60] == pytest.approx(300 * 16 / 31, rel=0.01)
    gaps = _run("mcSmooth(DATA.t, DATA.p)", t=[0, 1, None, 3, 100], p=[100, None, 200, 300, None])
    assert gaps[0] == 200 and gaps[2] is None and gaps[4] is None   # no time / nothing within ±15 s: no value
    assert _run("mcSmooth(DATA.t, null)", t=t) == [None] * 121


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_grade_over_a_distance_window_of_at_least_100_m():
    d, elev = _profile()
    g = _run("mcGrade(DATA.d, DATA.e)", d=d, e=elev)
    assert g[50] == pytest.approx(0, abs=0.01) and g[150] == pytest.approx(10, rel=0.02) and g[250] == pytest.approx(-10, rel=0.02)
    assert g[0] == pytest.approx(0, abs=0.01) and g[300] == pytest.approx(-10, rel=0.02)   # the ends take more of the other side
    # a 4-m one-sample spike (GPS / barometer) is spread over the 100-m window: no 40 % grade
    spiky = list(elev); spiky[50] += 4
    gs = _run("mcGrade(DATA.d, DATA.e)", d=d, e=spiky)
    assert max(abs(v) for v in gs[:90]) <= 4.5
    # no distance or no elevation: no grade
    assert _run("mcGrade(null, DATA.e)", e=elev) == [None] * 301
    assert _run("mcGrade(DATA.d, null)", d=d) == [None] * 301
    holes = _run("mcGrade(DATA.d, DATA.e)", d=[0, None, 0.05, 0.1, 0.15], e=[100, 120, 105, 110, None])
    assert holes[1] is None and holes[4] is None and holes[0] == pytest.approx(10)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_bands_by_absolute_grade_up_and_down_alike():
    g = [-30, -25, -24.9, -15, -8, -3, -2.9, 0, 2.99, 3, 7.99, 8, 14.9, 15, 24.99, 25, 40, None]
    assert _run("DATA.g.map(mcBand)", g=g) == [4, 4, 3, 3, 2, 1, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, None]
    assert _run("MC_GRADE_EDGES") == [3, 8, 15, 25]                  # pole_compare's 3 / 8 / 15 % + 25 %


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_grade_runs_merge_short_stretches_and_keep_up_and_down_together():
    d, elev = _profile()
    runs = _run("mcGradeSegs(DATA.d, mcGrade(DATA.d, DATA.e))", d=d, e=elev)
    # flat, then the climb and the descent as one band (the summit's and the foot's short grade change merged away)
    assert [r["band"] for r in runs] == [0, 2]
    assert runs[0]["a"] == 0 and runs[-1]["b"] == 300 and runs[1]["a"] == runs[0]["b"] + 1
    assert d[runs[1]["a"]] == pytest.approx(1.0, abs=0.05)
    assert all(r["km"] >= 0.2 for r in runs)
    # one 100-m stretch at 5 % on a flat 2 km: no stripe of its own
    d2 = [round(i * 0.01, 4) for i in range(201)]
    e2 = [100 + max(0.0, min(x, 1.0) - 0.9) * 50 for x in d2]
    assert [r["band"] for r in _run("mcGradeSegs(DATA.d, mcGrade(DATA.d, DATA.e))", d=d2, e=e2)] == [0]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_a_short_run_joins_the_neighbour_with_the_nearer_band_else_the_longer():
    d = [round(i * 0.01, 4) for i in range(110)]
    near = _run("mcGradeSegs(DATA.d, DATA.g)", d=d, g=[0] * 50 + [5] * 10 + [18] * 50)       # band 0 | 1 (100 m) | 3
    assert [(r["a"], r["b"], r["band"]) for r in near] == [(0, 59, 0), (60, 109, 3)]
    tie = _run("mcGradeSegs(DATA.d, DATA.g)", d=d[:90], g=[0] * 30 + [5] * 10 + [10] * 50)  # band 0 (300 m) | 1 | 2 (500 m)
    assert [(r["a"], r["b"], r["band"]) for r in tie] == [(0, 29, 0), (30, 89, 2)]
    updown = _run("mcGradeSegs(DATA.d, DATA.g)", d=d[:60], g=[10] * 30 + [-10] * 30)
    assert [(r["a"], r["b"], r["band"]) for r in updown] == [(0, 59, 2)]
    lead = _run("mcGradeSegs(DATA.d, DATA.g)", d=d[:45], g=[None] * 5 + [0] * 40)             # no grade yet: first band
    assert [(r["a"], r["b"], r["band"]) for r in lead] == [(0, 44, 0)]
    short = _run("mcGradeSegs(DATA.d, DATA.g)", d=d[:10], g=[0] * 5 + [10] * 5)              # a 100-m activity: one run
    assert len(short) == 1 and short[0]["a"] == 0 and short[0]["b"] == 9


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_grade_runs_without_distance_or_grade_are_empty():
    assert _run("mcGradeSegs(null, DATA.g)", g=[0, 5, 10]) == []
    assert _run("mcGradeSegs(DATA.d, DATA.g)", d=[0, 0.1, 0.2], g=[None, None, None]) == []
    assert _run("mcGradeSegs(DATA.d, DATA.g)", d=[None, None, None], g=[1, 2, 3]) == []


def _samples(n=50):
    t = [i * 10 for i in range(n)]
    d = [round(i * 0.03, 4) for i in range(n)]
    return ({"t": t, "d": d, "byT": {"xs": t, "is": list(range(n))}, "byD": {"xs": d, "is": list(range(n))}},
            {"elev": [100 + i for i in range(n)], "hr": [140] * n, "power": [200] * n, "pace": [300] * n, "g100": [5.0] * n})


SEGS = [{"a": 0, "b": 19, "band": 0, "km": 0.6}, {"a": 20, "b": 49, "band": 2, "km": 0.87}]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_overlay_elevation_background_and_at_most_two_scales():
    s, vals = _samples()
    o = _run("mcOverOption(DATA.s, ['hr', 'power', 'pace'], DATA.vals, 't', DATA.segs)", s=s, vals=vals, segs=SEGS)
    assert isinstance(o["grid"], dict) and isinstance(o["xAxis"], dict)                     # one grid, one x axis
    assert o["xAxis"]["min"] == 0 and o["xAxis"]["max"] == 490
    assert [x["id"] for x in o["series"]] == ["elev", "hr", "power", "pace"]
    assert [x["yAxisIndex"] for x in o["series"]] == [0, 1, 2, 3]
    elev, hr, pw, pace = o["yAxis"]
    assert elev["show"] is False                                                                # the background: no scale
    assert hr["position"] == "left" and hr.get("show") is not False and hr["name"] == "viewer.mapchart.hr bpm"
    assert pw["position"] == "right" and pw.get("show") is not False and pw["name"] == "viewer.mapchart.power W"
    assert pace["show"] is False and pace["inverse"] is True                                    # third: no scale, faster up
    assert "areaStyle" in o["series"][0] and o["series"][0]["z"] < o["series"][1]["z"]
    assert o["series"][1]["data"][3] == [30, 140]
    vm = o["visualMap"]                                                                         # the bands shade the elevation
    assert vm["show"] is False and vm["seriesIndex"] == 0 and vm["dimension"] == 0
    assert vm["pieces"] == [{"gte": 0, "lt": 200, "color": "--mc-g0"}, {"gte": 200, "lte": 490, "color": "--mc-g2"}]
    one = _run("mcOverOption(DATA.s, ['hr'], DATA.vals, 't', DATA.segs)", s=s, vals=vals, segs=SEGS)
    assert one["yAxis"][1]["position"] == "left" and one["grid"]["right"] < one["grid"]["left"]
    two = _run("mcOverOption(DATA.s, ['power', 'pace'], DATA.vals, 't', DATA.segs)", s=s, vals=vals, segs=SEGS)
    assert [(y["position"], y.get("show")) for y in two["yAxis"][1:]] == [("left", None), ("right", None)]
    assert two["yAxis"][2]["inverse"] is True


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_overlay_tooltip_lists_elevation_grade_and_every_line_on():
    s, vals = _samples()
    tip = _run("mcOverOption(DATA.s, ['hr', 'power', 'pace'], DATA.vals, 't', DATA.segs).tooltip.formatter([{dataIndex: 3}])",
               s=s, vals=vals, segs=SEGS)
    for want in ("viewer.mapchart.elev <b>103</b> m", "viewer.mapchart.grade <b>5.0</b>%", "viewer.mapchart.hr <b>140</b> bpm",
                 "viewer.mapchart.power <b>200</b> W", "viewer.mapchart.pace <b>5:00</b> /km", "<b>30</b> · 0.09 km"):
        assert want in tip, (want, tip)
    only = _run("mcOverOption(DATA.s, ['pace'], DATA.vals, 't', DATA.segs).tooltip.formatter([{dataIndex: 3}])",
                s=s, vals=vals, segs=SEGS)
    assert "viewer.mapchart.hr" not in only and "viewer.mapchart.elev" in only and "viewer.mapchart.grade" in only


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_overlay_without_bands_or_elevation():
    s, vals = _samples()
    flat = _run("mcOverOption(DATA.s, ['hr'], DATA.vals, 't', [])", s=s, vals=vals)
    assert "visualMap" not in flat and flat["series"][0]["areaStyle"]["color"]                 # one plain tone
    no_elev = _run("mcOverOption(DATA.s, ['hr'], DATA.vals, 't', DATA.segs)", s=s, vals={**vals, "elev": [None] * 50}, segs=SEGS)
    assert "visualMap" not in no_elev and [x["id"] for x in no_elev["series"]] == ["hr"]
    assert no_elev["series"][0]["yAxisIndex"] == 1


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_overlay_and_split_on_the_distance_axis():
    s, vals = _samples()
    o = _run("mcOverOption(DATA.s, ['hr'], DATA.vals, 'd', DATA.segs)", s=s, vals=vals, segs=SEGS)
    assert o["xAxis"]["min"] == 0 and o["xAxis"]["max"] == pytest.approx(1.47) and o["series"][1]["data"][3] == [0.09, 140]
    assert o["visualMap"]["pieces"] == [{"gte": 0, "lt": 0.6, "color": "--mc-g0"}, {"gte": 0.6, "lte": 1.47, "color": "--mc-g2"}]
    sp = _run("mcOption(DATA.s, ['elev', 'hr'], DATA.vals, 'd')", s=s, vals=vals)
    assert sp["xAxis"][0]["min"] == 0 and sp["xAxis"][1]["max"] == pytest.approx(1.47) and sp["series"][1]["data"][3] == [0.09, 140]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_view_defaults_to_overlay_on_time_and_is_remembered():
    assert _run("mcView({}, true)") == {"mode": "over", "x": "t"}
    assert _run("mcView({mode: 'split', x: 'd'}, true)") == {"mode": "split", "x": "d"}
    assert _run("mcView({mode: 'odd', x: 'd'}, false)") == {"mode": "over", "x": "t"}       # no distance: time
    assert _run("(mcSave({mode: 'split'}), mcSave({x: 'd'}), mcSave({metrics: ['hr']}), mcPrefs())") == \
        {"mode": "split", "x": "d", "metrics": ["hr"]}
    assert _run("(mcSave({mode: 'split'}), mcView(mcPrefs(), true))") == {"mode": "split", "x": "t"}
    # a browser that refuses storage: defaults, no error
    assert _run("(localStorage = {getItem() { throw new Error('no'); }, setItem() { throw new Error('no'); }}, "
                "mcSave({mode: 'split'}), mcView(mcPrefs(), true))") == {"mode": "over", "x": "t"}
    # the overlay's lines: HR / power / pace among the remembered pick
    has = "(id) => DATA.has.includes(id)"
    assert _run(f"mcPickOver(undefined, {has})", has=["elev", "hr", "power", "pace", "grade"]) == ["hr", "power", "pace"]
    assert _run(f"mcPickOver(['elev', 'pace', 'power'], {has})", has=["elev", "hr", "pace"]) == ["pace"]


def _button(html, attr, key):
    m = re.search(rf'<button {attr}="{key}"[^>]*>', html)
    return m.group(0) if m else None


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_bar_disables_what_the_activity_has_no_data_for():
    has = "(id) => DATA.has.includes(id)"
    over = _run(f"mcBar('over', ['hr'], {has}, 't', false)", has=["elev", "hr", "pace"])
    assert "disabled" in _button(over, "data-m", "power") and "disabled" not in _button(over, "data-m", "hr")
    assert 'class="on"' in _button(over, "data-m", "hr") and 'class="on"' not in _button(over, "data-m", "pace")
    assert _button(over, "data-m", "elev") is None and _button(over, "data-m", "grade") is None    # background / bands
    assert "disabled" in _button(over, "data-x", "d") and 'class="on"' in _button(over, "data-x", "t")
    assert 'class="on"' in _button(over, "data-v", "over") and 'class="on"' not in _button(over, "data-v", "split")
    nohr = _run(f"mcBar('over', [], {has}, 'd', true)", has=["elev", "power", "pace"])
    assert "disabled" in _button(nohr, "data-m", "hr") and "disabled" not in _button(nohr, "data-x", "d")
    split = _run(f"mcBar('split', ['elev', 'hr'], {has}, 't', true)", has=["elev", "hr", "grade"])
    assert all(_button(split, "data-m", k) for k in ("elev", "hr", "power", "pace", "grade"))
    assert 'class="on"' in _button(split, "data-v", "split")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_legend_names_the_five_grade_bands():
    leg = _run("mcLegend(true)")
    assert re.findall(r"var\(--mc-g(\d)\)", leg) == ["0", "1", "2", "3", "4"]
    for lab in ("< 3%", "3–8%", "8–15%", "15–25%", "≥ 25%"):           # (esc is the identity here)
        assert lab in leg, (lab, leg)
    assert "viewer.mapchart.bands" in leg and "viewer.mapchart.smooth" in leg
    assert "--mc-g" not in _run("mcLegend(false)")                                          # no distance: no bands


def test_band_colours_for_light_and_dark():
    page = PAGE.read_text(encoding="utf-8")
    light = re.search(r":root \{ (--mc-g0:[^}]*)\}", page)
    dark_mq = re.search(r'@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme="light"\]\) \{ (--mc-g0:[^}]*)\}', page)
    dark = re.search(r':root\[data-theme="dark"\] \{ (--mc-g0:[^}]*)\}', page)
    for m in (light, dark_mq, dark):
        assert m and all(f"--mc-g{k}:" in m.group(1) for k in range(5)), m


def test_hover_follows_the_chart_axis():
    """the map chart's x switches between time and km; its hover member reads the current one"""
    if NODE is None:
        pytest.skip("node not installed")
    page = PAGE.read_text(encoding="utf-8")
    src = "\n".join(re.search(rf"function {fn}\(.*?\n}}\n", page, re.S).group(0) for fn in ("nearestIn", "hoverChart"))
    js = r"""
const vm = require("vm");
const ZR = {}, PX = [];
const ctx = {
  HOVER: { members: new Set() }, LAST: undefined, ZR,
  hoverEmit: (m, idx) => { ctx.LAST = typeof idx === "function" ? idx() : idx; },
  gridRect: () => ({ x: 0, y: 0, width: 1000, height: 100 }), gridRects: () => [{ x: 0, y: 0, width: 1000, height: 100 }],
  chart: { isDisposed: () => false, convertToPixel: (o, x) => { PX.push(x); return 10; }, convertFromPixel: (o, px) => px,
           dispatchAction: () => {}, getZr: () => ({ on: (ev, fn) => { ZR[ev] = fn; } }) },
  s: { workout: 1, t: [0, 10, 20, 30], d: [0, 0.5, 1.0, 1.5], byT: { xs: [0, 10, 20, 30], is: [0, 1, 2, 3] },
       byD: { xs: [0, 0.5, 1.0, 1.5], is: [0, 1, 2, 3] } },
  AX: "t",
};
vm.runInNewContext(process.argv[1] + `
hoverChart(chart, s, () => AX);
const m = [...HOVER.members][0];
m.show(2); AX = "d"; m.show(2);
ZR.mousemove({ offsetX: 1.4, offsetY: 5 }); const byD = LAST;
AX = "t"; ZR.mousemove({ offsetX: 11, offsetY: 5 }); const byT = LAST;
OUT = { n: HOVER.members.size, byD, byT };`, ctx);
console.log(JSON.stringify({ ...ctx.OUT, px: PX }));
"""
    r = subprocess.run([NODE, "-e", js, src], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"n": 1, "byD": 3, "byT": 1, "px": [20, 1.0]}
    assert "hoverChart(chart, s, () => view.x)" in page                   # mapChart: one member, the current axis


def test_the_bar_remembers_each_choice():
    """mapChart's button handler saves the arrangement, the x axis and the lines picked (MC_KEY)"""
    body = re.search(r"function mapChart\(.*?\n}\n", PAGE.read_text(encoding="utf-8"), re.S).group(0)
    for save in ("mcSave({ mode: view.mode })", "mcSave({ x: view.x })", "mcSave({ metrics: want })", "mcSave({ layout })"):
        assert save in body, save
    assert "view = mcView(pref, !!s.byD)" in body                         # read back on the next draw
