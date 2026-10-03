"""Unit registry, render-time unit handling, and the WKO5 chart-fixes loader."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from backend.engine.wko5expr import units as U
from backend.engine.wko5expr.chartfixes import FixError, apply_fixes, load_fixes, unmatched
from backend.engine.wko5expr.customviews import load_custom_views
from backend.engine.wko5expr.render import render_chart
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 29)


# ---------------------------------------------------------------- registry

@pytest.mark.parametrize("uid,label,kind", [
    ("WATTS", "W", "number"), ("WATTSKG", "W/kg", "number"), ("KJ", "kJ", "number"),
    ("PERCENT", "%", "percent"), ("HHMMSS", "h:mm:ss", "duration"), ("TSS", "TSS", "number"),
    ("TSSPERDAY", "TSS/天", "number"), ("TSSPERDAYPERWEEK", "TSS/天/週", "number"),
    ("METERS", "m", "number"), ("KM", "km", "number"), ("KPH", "km/h", "number"),
    ("METERSPERSECOND", "m/s", "number"), ("METERSPERHOUR", "m/h", "number"),
    ("PACEKM", "/km", "pace"), ("BPM", "bpm", "number"), ("RPM", "spm", "number"),
    ("CM", "cm", "number"), ("L/min", "L/min", "number"), ("mL/min/kg", "mL/min/kg", "number"),
    ("DATE", "日期", "date"), ("MILLISECONDS", "ms", "number"), ("SECONDS", "s", "duration"),
])
def test_known_ids_have_label_and_kind(uid, label, kind):
    u = U.unit(uid)
    assert (u.label, u.kind) == (label, kind)


def test_decimals_by_unit():
    assert U.unit("WATTS").decimals_for(234.567) == 0
    assert U.unit("WATTSKG").decimals_for(3.456) == 2
    assert U.unit("KJ").decimals_for(1234.5) == 0
    assert U.unit("TSSPERDAY").decimals_for(52.3) == 0
    assert U.unit("TSSPERDAY").decimals_for(4.2) == 1
    assert U.unit("KM").decimals_for(5.123) == 2
    assert U.unit("KM").decimals_for(42.195) == 1
    assert U.unit("KM").decimals_for(123.4) == 0
    assert U.unit("KPH").decimals_for(8.33) == 1
    # percent looks at the displayed value (x100)
    assert U.unit("PERCENT").decimals_for(0.456) == 0
    assert U.unit("PERCENT").decimals_for(0.045) == 1


def test_custom_and_none_use_magnitude_rule():
    u = U.unit("CUSTOMkN/m")
    assert u.label == "kN/m" and u.kind == "number"
    assert [u.decimals_for(v) for v in (250.0, 25.0, 2.5, -250.0)] == [0, 1, 2, 0]
    assert U.unit("NONE").label == "" and U.unit(None).id == "NONE" and U.unit("").id == "NONE"
    assert U.unit("NONE").decimals_for(1.2345) == 2
    # known custom labels get proper decimals
    assert U.unit("CUSTOMm/km").decimals_for(52.7) == 0
    assert U.unit("CUSTOMm/min/bpm").decimals_for(1.234) == 2
    assert U.unit("CUSTOM次").label == "次" and U.unit("CUSTOM次").decimals_for(2.0) == 0
    assert U.unit("CUSTOMTSS/week").label == "TSS/週"
    # unknown non-custom id: its own text
    assert U.unit("steps/min").label == "spm"
    assert U.unit("WEIRD").label == "WEIRD"


def test_rpm_label_depends_on_sport():
    assert U.unit("RPM", "run").label == "spm"
    assert U.unit("RPM", "bike").label == "rpm"


def test_imperial_conversions():
    assert U.metric_id("FT") == "METERS" and U.metric_id("MI") == "KM"
    assert U.metric_id("MPH") == "KPH" and U.metric_id("PACEMI") == "PACEKM"
    assert U.metric_id("WATTS") == "WATTS"
    assert U.to_metric_value("FT", 1000) == pytest.approx(304.8)
    assert U.to_metric_value("MI", 1) == pytest.approx(1.609344)
    assert U.to_metric_value("MPH", 10) == pytest.approx(16.09344)
    assert U.to_metric_value("PACEMI", 8.0) == pytest.approx(4.97097, rel=1e-4)   # 8:00/mi -> 4:58/km
    assert U.to_metric_value("FAHRENHEIT", 212) == pytest.approx(100)
    assert U.to_metric_value("METERS", 5) == 5
    assert U.is_imperial("FT") and not U.is_imperial("METERS")


def test_metricize_expression():
    e = 'english(climbing)/English (distance)'
    assert U.uses_english(e)
    assert U.metricize_expression(e) == "metric(climbing)/metric(distance)"
    assert U.metricize_expression("englishman(x)") == "englishman(x)"
    assert U.metricize_expression(None) is None


def test_pace_base():
    assert U.pace_base([528.0], "avg(duration/distance)") == "s"
    assert U.pace_base([8.7], "ngp") == "kph"
    assert U.pace_base([4.66], "runtpace") == "min"
    assert U.pace_base([5.2], "avg(60/runspeed)") == "min"
    assert U.pace_to_min_per_km(330, "s") == pytest.approx(5.5)
    assert U.pace_to_min_per_km(12, "kph") == pytest.approx(5.0)


def test_format_value():
    assert U.format_value(234.6, U.unit("WATTS")) == "235 W"
    assert U.format_value(0.456, U.unit("PERCENT")) == "46%"
    assert U.format_value(3725, U.unit("HHMMSS")) == "1:02:05"
    assert U.format_value(95, U.unit("HHMMSS")) == "1:35"
    assert U.format_value(5.5, U.unit("PACEKM")) == "5:30 /km"
    # expressions see verticaloscillation in cm and stancetime in ms (as in WKO5),
    # so these axes need no display scale
    assert U.format_value(5.2, U.unit("CM")) == "5.2 cm"
    assert U.format_value(363.0, U.unit("MILLISECONDS")) == "363 ms"
    assert "scale" not in U.unit("CM").meta() and "scale" not in U.unit("MILLISECONDS").meta()
    assert U.unit("PERCENT").meta()["scale"] == 100.0
    assert U.format_value(None, U.unit("WATTS")) == "—"


# ---------------------------------------------------------------- render_chart

def _ds(parity):
    ds = FakeDataset([FakeWorkout(dt.datetime(2026, 9, 20, 8), metrics={
        "distance": 10.0, "climbing": 500.0, "duration": 3600.0, "tss": 60.0})], TODAY)
    ds.config = SimpleNamespace(parity=parity)
    return ds


CHART = {"title": "t", "kind": "athlete",
         "axes": [{"id": "FT", "min": 0, "max": 3280.84}, {"id": "PERCENT", "min": None, "max": None}],
         "series": [
             {"name": "climb ft", "type": "line", "y_axis": "FT", "expression": "english(climbing)"},
             {"name": "climb", "type": "line", "y_axis": "FT", "expression": "climbing"},
             {"name": "dist", "type": "line", "y_axis": "KM", "expression": "distance"},
         ]}


def _last(s):
    return [p for p in s["data"]["points"] if p[1] is not None][-1][1]


def test_render_non_parity_converts_imperial_to_metric():
    ds = _ds(parity=False)
    res = render_chart(CHART, ds, ds.today - 30, ds.today)
    a, b, c = res["series"]
    assert a["y_axis"] == "METERS" and a["unit"]["label"] == "m"
    assert _last(a) == pytest.approx(500.0)             # english() evaluated as metric()
    assert "metric(climbing)" in a["expression"]
    assert b["y_axis"] == "METERS" and _last(b) == pytest.approx(500.0)   # relabelled only
    assert c["unit"]["label"] == "km" and c["unit"]["dec"] == [[100, 0], [10, 1], [0, 2]]
    ax = {x["id"]: x for x in res["axes"]}
    assert "FT" not in ax and ax["METERS"]["max"] == pytest.approx(1000.0, rel=1e-4)
    assert ax["METERS"]["used"] and not ax["PERCENT"]["used"] and ax["KM"]["used"]
    assert res["fixes"] and res["parity"] is False


def test_render_parity_keeps_wko5_ids_and_values():
    ds = _ds(parity=True)
    res = render_chart(CHART, ds, ds.today - 30, ds.today)
    a, b, _ = res["series"]
    assert a["y_axis"] == "FT" and a["unit"]["label"] == "ft"
    assert _last(a) == pytest.approx(500.0 * 3.28084)
    assert b["y_axis"] == "FT"
    assert res["fixes"] == []


def test_render_pace_normalised_outside_parity_and_scale_fix():
    ds = _ds(parity=False)
    chart = {"title": "p", "kind": "athlete", "axes": [], "series": [
        {"name": "pace", "y_axis": "PACEKM", "expression": "duration/distance"},
        {"name": "x2", "y_axis": "NONE", "expression": "tss", "scale": 2},
    ]}
    res = render_chart(chart, ds, ds.today - 30, ds.today)
    p, t = res["series"]
    assert _last(p) == pytest.approx(6.0)                    # 360 s/km -> 6 min/km
    assert p["unit"]["base"] == "min" and p["unit"]["converted_from"] == "s"
    assert _last(t) == pytest.approx(120.0)
    ds2 = _ds(parity=True)
    p2 = render_chart(chart, ds2, ds2.today - 30, ds2.today)["series"][0]
    assert _last(p2) == pytest.approx(360.0) and p2["unit"]["base"] == "s"


# ---------------------------------------------------------------- fixes loader

VIEWS = {"V": {"view": "V", "source": "wko5", "dashboards": [
    {"title": "D", "charts": [{"title": "C", "kind": "athlete",
                               "axes": [{"id": "NONE", "min": 0.5, "max": 2.0}],
                               "series": [{"name": "Distance (mi)", "y_axis": "NONE", "expression": "english(distance)"},
                                          {"name": "cad", "y_axis": "RPM", "expression": "cadence"}]}]}]}}


def _write(tmp_path, data):
    p = tmp_path / "wko5_fixes.json"
    p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return p


def test_fixes_apply_series_axis_and_scale(tmp_path):
    fixes = load_fixes(_write(tmp_path, {"fixes": [
        {"view": "V", "chart": "C", "series": "Distance (mi)", "set": {"y_axis": "KM", "name": "距離"}, "note": "n1"},
        {"view": "V", "chart": "C", "axis": "NONE", "set": {"min": None, "max": None}, "note": "n2"},
        {"view": "V", "chart": "C", "series_index": 1, "scale": 2, "note": "n3"},
        {"view": "V", "chart": "gone", "series": "x", "note": "stale"},
    ]}))
    out = apply_fixes(VIEWS, fixes)
    c = out["V"]["dashboards"][0]["charts"][0]
    assert c["series"][0]["y_axis"] == "KM" and c["series"][0]["name"] == "距離"
    assert c["axes"][0]["min"] is None and c["axes"][0]["max"] is None
    assert c["series"][1]["scale"] == 2.0
    assert c["fixes"] == ["n1", "n2", "n3"]
    # the input is untouched (parity mode keeps using it)
    assert VIEWS["V"]["dashboards"][0]["charts"][0]["series"][0]["y_axis"] == "NONE"
    assert [f["note"] for f in unmatched(VIEWS, fixes)] == ["stale"]


def test_fix_without_view_applies_to_every_imported_view(tmp_path):
    import copy
    views = {"A": VIEWS["V"], "B": {**copy.deepcopy(VIEWS["V"]), "view": "B"}}
    fixes = load_fixes(_write(tmp_path, {"fixes": [
        {"chart": "C", "axis": "NONE", "set": {"min": None}, "note": "all"},
        {"view": "B", "chart": "C", "series_index": 1, "scale": 2, "note": "only B"},
        {"chart_id": "gone", "axis": "NONE", "note": "stale"},
    ]}))
    out = apply_fixes(views, fixes)
    assert out["A"]["dashboards"][0]["charts"][0]["fixes"] == ["all"]
    assert out["B"]["dashboards"][0]["charts"][0]["fixes"] == ["all", "only B"]
    assert [f["note"] for f in unmatched(views, fixes)] == ["stale"]


def test_fixes_file_validation(tmp_path):
    assert load_fixes(tmp_path / "missing.json") == []
    with pytest.raises(FixError):
        load_fixes(_write(tmp_path, {"fixes": [{"view": "V"}]}))
    with pytest.raises(FixError):
        load_fixes(_write(tmp_path, {"fixes": [{"view": "V", "chart": "C", "series": "s", "set": {"bogus": 1}}]}))
    with pytest.raises(FixError):
        load_fixes(_write(tmp_path, [1, 2]))


def test_fixes_file_is_not_loaded_as_a_custom_view(tmp_path):
    _write(tmp_path, {"fixes": []})
    (tmp_path / "mine.json").write_text(json.dumps({"name": "Mine", "dashboards": []}), "utf-8")
    views = load_custom_views([tmp_path])
    assert list(views) == ["Mine"]


def test_repo_fixes_file_is_valid_and_matches_wko5_charts():
    from pathlib import Path
    from backend.engine.wko5expr.chartfixes import FIXES_PATH
    from backend.files.wko5chart_reader import read_view
    import os
    fixes = load_fixes()
    from backend.engine.wko5expr.viewids import ensure_ids
    assert all(f.get("chart_id") for f in fixes)
    assert not any(f.get("view") for f in fixes)     # keyed by chart id, not by a view file name
    # your own exported views (opt-in): WKO5_VIEWS_DIR; WKO5 views are never shipped in the repo
    root = os.getenv("WKO5_VIEWS_DIR")
    # as wko5views._wko5_views_raw reads them: with the title-derived chart ids the fixes match on
    views = {p.stem: ensure_ids(read_view(p)) for p in Path(root).rglob("*.wko5chart")} if root else {}
    if not views:
        pytest.skip("set WKO5_VIEWS_DIR to your exported .wko5chart views")
    assert FIXES_PATH.name == "wko5_fixes.json"
    assert unmatched(views, fixes) == []
