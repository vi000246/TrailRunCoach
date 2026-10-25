"""SP-784: the race calculator's 分段目標 (the target line over the elevation profile and the segment table) is one
component, static/seg_profile.js (SegProfile): the calculator loads it by its address, the share page gets it
written into the page by the server (Cloudflare Access lets only /share/* through, as with i18n.js, SP-698),
so a change of the calculator's look reaches every share link and the built-in examples too.

A share stores the rows it draws (share.ROW_KEYS: never the 位置記憶 runs or the model's sources); a share made
before SP-784, and the examples, get them derived from their own frozen segments (share.with_rows)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine.racepower import share as SH
from backend.i18n import pages as P

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def _src(name: str) -> str:
    return (STATIC / name).read_text("utf-8")


# ---- one component, two pages ------------------------------------------------------------------------------

def test_the_calculator_loads_the_component_and_draws_with_it():
    html = _src("racepower.html")
    assert '<script src="/api/v1/static/seg_profile.js"></script>' in html
    prof = html[html.index("function renderProfile"):html.index("function courseLatLngs")]
    assert "SegProfile.option(P, {" in prof and "SegProfile.legend(P, {" in prof
    cmp = html[html.index("function renderSegCompact"):html.index("function poleHint")]
    assert "SegProfile.table(PLAN, {" in cmp
    # the drawing lives in the component only, not a second copy in the page
    assert "const MEASURE = {" not in html and "function fmtRow" not in html and "const yMain = {" not in html
    assert 'id="profile-sec" class="tab-calc segp hidden"' in html


def test_the_share_page_draws_with_the_same_component():
    src = _src("share.html")
    assert "<!--inline:seg_profile.js-->" in src
    assert "SegProfile.option(TG.P, o)" in src and "SegProfile.legend(TG.P, o)" in src and "SegProfile.table(TG.P, {" in src
    assert "function targetsHtml" not in src and "tcard" not in src            # the old cards are gone
    assert 'class="segp" id="profile-sec"' in src


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_share_page_carries_the_component_inline(loc):
    P._CACHE.clear()
    html = P.render("share", loc, debug=False)
    js = _src("seg_profile.js")
    assert "window.SegProfile = {" in html and "<!--inline:" not in html
    assert "seg_profile.js\"" not in html                                       # never loaded by its address
    # after the catalog and i18n.js, before the page's own script
    assert html.index("window.I18N = {") < html.index("window.SegProfile = {") < html.index("const JT = ")
    assert js.count("</script") == 0 or "<\\/script" in html


def test_a_changed_component_reaches_the_share_page(tmp_path, monkeypatch):
    (tmp_path / "seg_profile.js").write_text("window.SegProfile = {probe: 1}; // </script> inside", "utf-8")
    real = P.STATIC
    monkeypatch.setattr(P, "STATIC", tmp_path)
    (tmp_path / "share.html").write_text((real / "share.html").read_text("utf-8"), "utf-8")
    P._CACHE.clear()
    html = P.render("share", "zh-TW", debug=False)
    assert "window.SegProfile = {probe: 1}; // <\\/script> inside" in html      # cannot close the tag early
    P._CACHE.clear()


def test_the_components_text_is_in_both_catalogs():
    js = _src("seg_profile.js")
    keys = {k for k in re.findall(r'\bT\("([\w.]+)"', js) if not k.endswith((".", "_"))}
    keys |= {"ref.tip_power", "ref.tip_hr", "m.pace", "m.power", "m.hr"}      # built at run time ("ref.tip_" + m, "m." + k)
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "racepower.json").read_text("utf-8"))
    assert sorted(k for k in keys if k not in zh) == [] and sorted(k for k in keys if not en.get(k)) == []
    assert zh["ref.cell"] == "{v} 參考" and zh["ref.power_plain"] == "約 {w} W（參考）"
    cjk = re.compile(r"[㐀-鿿　-〿！-｠]")
    assert not [k for k in keys if cjk.search(en[k])]


# ---- the rows a share draws --------------------------------------------------------------------------------

def _plan_with_rows() -> dict:
    row = {"n": 1, "mark": "①", "i": 1, "start_km": 0.0, "end_km": 2.0, "grade": 0.12, "kind": "steep_climb", "label": "陡上坡",
           "basis": "hr", "pace_s_per_km": 720.0, "power": None, "power_ref": 250.0, "hr_cap": 165.0,
           "hr_cap_src": "LTHR", "walk": True, "t": 1440.0, "cum_s": 1440.0, "fuel": "吃 1 次",
           "memory": {"f": 1.1, "R": 1.1, "applied": False, "passes": [{"date": "2025-01-01", "label": "我的跑步", "r": 1.1}]},
           "z_mean": 420.0, "tech_f": 1.05, "gap_pace_s_per_km": 400.0, "night": False}
    return {"type": "trail", "summary": {"time_s": 1440, "km": 2.0}, "segments": [{"i": 1, "start_km": 0, "end_km": 2}],
            "chart_rows": [row], "profile": {"km": [0, 2], "z": [100, 340]}}


def test_a_share_keeps_the_rows_without_the_athletes_runs_or_the_models_sources():
    snap = SH.snapshot(_plan_with_rows(), title="t")
    r = snap["chart_rows"][0]
    assert r["pace_s_per_km"] == 720.0 and r["hr_cap"] == 165.0 and r["power_ref"] == 250.0 and r["walk"] is True
    assert r["night"] is False and r["fuel"] == "吃 1 次" and r["mark"] == "①"
    for k in ("memory", "hr_cap_src", "z_mean", "tech_f", "gap_pace_s_per_km"):
        assert k not in r
    assert "我的跑步" not in json.dumps(snap, ensure_ascii=False)


def test_a_plan_without_rows_stores_none():
    p = _plan_with_rows()
    p.pop("chart_rows")
    assert SH.snapshot(p, title="t")["chart_rows"] is None


@pytest.mark.parametrize("sid", sorted(SH.EXAMPLES))
def test_the_examples_get_their_rows_from_their_own_segments(sid):
    snap = SH.example(sid)
    assert snap and "chart_rows" not in snap                                     # the files are as they were
    out = SH.with_rows(snap)
    R = out["chart_rows"]
    assert len(R) == len(snap["segments"])
    assert [r["i"] for r in R] == [s["i"] for s in snap["segments"]]
    assert all(r["pace_s_per_km"] > 0 and r["label"] and r["mark"] for r in R)
    assert all(abs(r["t"] - s["t"]) < 1e-6 for r, s in zip(R, snap["segments"]))
    if snap["type"] in ("trail", "baiyue"):
        cap = next(t["hr_cap"] for t in snap["seg_targets"] if t.get("hr_cap"))
        assert {r["hr_cap"] for r in R if r["kind"] != "descent"} == {cap}           # the race's cap from its targets
        assert all(r.get("hr_cap") is None for r in R if r["kind"] == "descent")
    if snap["type"] == "trail":
        assert all(r.get("power") is None for r in R if r["kind"] in ("steep_climb", "descent"))   # a reference only
    if snap["type"] == "road":
        assert all(r["power"] for r in R)
    assert "hr_cap_src" not in json.dumps(R)


def test_a_share_with_its_own_rows_is_served_as_it_is():
    snap = SH.snapshot(_plan_with_rows(), title="t")
    assert SH.with_rows(snap) is snap


def test_the_share_data_answers_the_rows():
    from backend.api.racepower import share_data
    r = share_data("example-trail-50k")                  # a built-in example: no tenant, no account
    assert r.status_code == 200
    body = json.loads(r.body)
    assert len(body["chart_rows"]) == len(body["segments"]) and body["chart_rows"][0]["mark"] == "①"


# ---- the component itself (node) ---------------------------------------------------------------------------

def _run(expr: str, **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const el = { style: {}, appendChild() {}, set textContent(v) {} };
const ctx = { DATA: JSON.parse(process.argv[3]), console, isFinite };
ctx.window = ctx;
ctx.document = { getElementById: () => null, createElement: () => ({}), head: el, documentElement: {} };
ctx.getComputedStyle = () => ({ getPropertyValue: () => " #123456 " });
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/seg_profile.js", "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
def test_the_component_draws_the_example():
    P_ = SH.with_rows(SH.example("example-trail-50k"))
    o = _run("(() => { const o = SegProfile.option(DATA.P, { measure: 'power' }); return { series: o.series.map((s) => [s.name, s.type, s.data.length]), "
             "y: o.yAxis[0], gap: o.series[o.series.length - 1].data.filter((p) => p[1] === null).length,"
             "table: SegProfile.table(DATA.P, { measure: 'power' }), legend: SegProfile.legend(DATA.P, { measure: 'power' }) }; })()", P=P_)
    names = [s[0] for s in o["series"]]
    assert names == ["elev", "m.power"]                                    # the keys: no catalog under node
    assert o["series"][0][2] == len(P_["profile"]["km"])
    # the trail's steep climbs and descents leave the power line empty (a reference in the table)
    assert o["gap"] == sum(1 for r in P_["chart_rows"] if r.get("power") is None)
    assert o["y"]["inverse"] is False and o["y"]["name"] == "W"
    assert o["table"].count('<tr data-row="') == len(P_["chart_rows"])
    assert 'class="ref"' in o["table"] and "ref.cell" in o["table"]
    assert "legend.gap_power" in o["legend"]


@needs_node
def test_the_component_marks_the_selected_and_hovered_segments():
    P_ = SH.with_rows(SH.example("example-trail-50k"))
    o = _run("(() => { const sel = new Set([2]); const o = SegProfile.option(DATA.P, { measure: 'pace', sel, hover: 3 });"
             "const t = SegProfile.table(DATA.P, { measure: 'pace', sel }); return { areas: o.series[o.series.length - 1].markArea.data.length,"
             "hl: (t.match(/class=\"hl\"/g) || []).length, y: o.yAxis[0] }; })()", P=P_)
    walk = sum(1 for r in P_["chart_rows"] if r.get("walk"))
    assert o["areas"] == walk + 2 and o["hl"] == 1
    assert o["y"]["inverse"] is True and o["y"]["nameLocation"] == "start"
