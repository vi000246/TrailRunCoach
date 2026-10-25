"""
SP-870: the race calculator's profile chart and course map mark the stretches you ran before (位置記憶,
SP-726) — the same segments the table's 📍 column marks (chart_rows' `memory`), each drawn over the
passes' km (merged, clipped to the segment); dashed / fainter when the back-test has not switched place
memory on. Only where the page asks (the calculator): the share page shows no one's history.
"""
from __future__ import annotations


import json
from pathlib import Path

from backend.engine.racepower import share as SH
from backend.tests.test_climb_marks_sp728 import _run_js, needs_node

STATIC = Path(__file__).resolve().parents[1] / "static"


def _plan(applied=True):
    P = SH.with_rows(SH.example("example-trail-50k"))
    rows = P["chart_rows"]
    r1, r2 = rows[2], rows[5]
    a1, b1 = r1["start_km"], r1["end_km"]
    # two runs over most of row 2 (overlapping: one stretch), one over part of row 5, row 5's other run without km
    r1["memory"] = {"f": 0.8, "R": 1.22, "applied": applied, "n": 2, "passes": [
        {"date": "2026-05-01", "label": "北插", "r": 1.2, "km0": a1 + 0.1, "km1": a1 + 0.6 * (b1 - a1)},
        {"date": "2026-06-01", "label": "北插 2", "r": 1.25, "km0": a1 + 0.5 * (b1 - a1), "km1": b1 + 5.0}]}
    a2, b2 = r2["start_km"], r2["end_km"]
    r2["memory"] = {"f": 0.3, "R": 0.9, "applied": applied, "n": 1, "passes": [
        {"date": "2026-07-01", "label": "x", "r": 0.9, "km0": a2, "km1": a2 + 0.3 * (b2 - a2)}]}
    return P, (r1, r2)


@needs_node
def test_the_profile_marks_the_stretches_the_table_marks():
    P, (r1, r2) = _plan()
    o = _run_js("(() => { const o = SegProfile.option(DATA.P, { measure: 'pace', memory: true });"
                "const s = o.series.find((x) => x.name === 'mem.legend');"
                "return { area: s && s.markArea.data, spans: SegProfile.memSpans(DATA.P),"
                " legend: SegProfile.legend(DATA.P, { memory: true }) }; })()", P=P)
    sp = o["spans"]
    # one stretch per segment: row 2's two runs merged and clipped to the segment, row 5's part
    assert [(s["i"], round(s["a"], 3), round(s["b"], 3)) for s in sp] == [
        (r1["i"], round(r1["start_km"] + 0.1, 3), round(r1["end_km"], 3)),
        (r2["i"], round(r2["start_km"], 3), round(r2["start_km"] + 0.3 * (r2["end_km"] - r2["start_km"]), 3))]
    # the same segments as the table's 📍 column (the rows with `memory`)
    assert {s["i"] for s in sp} == {r["i"] for r in P["chart_rows"] if r.get("memory")}
    assert len(o["area"]) == 2 and o["area"][0][0]["xAxis"] == sp[0]["a"] and o["area"][0][1]["xAxis"] == sp[0]["b"]
    assert o["area"][0][0]["label"]["formatter"] == "📍" and o["area"][0][0]["itemStyle"]["borderWidth"] == 0
    assert "mem.legend" in o["legend"] and "mem.legend_tip" in o["legend"] and "mem.legend_tip_off" not in o["legend"]


@needs_node
def test_only_marked_is_dashed_and_says_so():
    P, _rows = _plan(applied=False)
    o = _run_js("(() => { const o = SegProfile.option(DATA.P, { memory: true }); const s = o.series.find((x) => x.name === 'mem.legend');"
                "return { area: s.markArea.data, legend: SegProfile.legend(DATA.P, { memory: true }) }; })()", P=P)
    st = o["area"][0][0]["itemStyle"]
    assert st["borderType"] == "dashed" and st["borderWidth"] == 1 and st["opacity"] < 0.2
    assert "mem.legend_tip_off" in o["legend"]


@needs_node
def test_not_on_the_share_page_and_none_without_memory():
    P, _rows = _plan()
    o = _run_js("({ names: SegProfile.option(DATA.P, {}).series.map((x) => x.name), legend: SegProfile.legend(DATA.P, {}) })", P=P)
    assert "mem.legend" not in o["names"] and "mem.legend" not in o["legend"]      # the share page: no o.memory
    bare = SH.with_rows(SH.example("example-trail-50k"))
    o = _run_js("({ names: SegProfile.option(DATA.P, { memory: true }).series.map((x) => x.name),"
                " legend: SegProfile.legend(DATA.P, { memory: true }), n: SegProfile.memSpans(DATA.P).length })", P=bare)
    assert o["n"] == 0 and "mem.legend" not in o["names"] and "mem.legend" not in o["legend"]


@needs_node
def test_a_segment_whose_runs_carry_no_km_is_marked_whole():
    P, (r1, _r2) = _plan()
    for p in r1["memory"]["passes"]:
        p.pop("km0"), p.pop("km1")
    sp = _run_js("SegProfile.memSpans(DATA.P)", P=P)
    assert (sp[0]["a"], sp[0]["b"]) == (r1["start_km"], r1["end_km"])


def test_the_calculator_draws_them_on_the_map_and_the_texts_are_in_both_catalogs():
    html = (STATIC / "racepower.html").read_text("utf-8")
    assert "SegProfile.memSpans(P)" in html and "CMAP.mem" in html and "memory: true" in html
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        for k in ("mem.legend", "mem.legend_tip", "mem.legend_tip_off", "mem.off_short"):
            assert cat.get(k), (loc, k)

