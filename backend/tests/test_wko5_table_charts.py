"""WKO5 table-style charts (Palladino / Friel / Classic zones, Optimized Interval
Targets): every series evaluates to a list, rendered as {"kind": "values"} and
drawn by the viewer as one column per series."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
from pathlib import Path

from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.render import result_to_json
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 9, 29)
VIEWER = Path(__file__).resolve().parents[1] / "static" / "wko5_viewer.html"


def _json(expr):
    ds = FakeDataset([], TODAY, settings={"runftp": 250.0, "runthr": 160.0})
    ev = Evaluator(ds, ds.today - 30, ds.today)
    return result_to_json(ev.evaluate(expr), ds, int(ds.today) - 30, int(ds.today))


def test_palladino_power_column_is_a_list_of_range_strings():
    js = _json('@ftp:=(,lookup(runftp,now)), '
               '@from:=round({.5,.65,.75,.801,.88,.95,1.02,1.06,1.161,1.5}*@ftp), '
               '@to:=round({.649,0.749,0.8,.879,.949,1.019,1.059,1.16,1.499,NA}*@ftp), '
               'string(@from) + " to " + string(@to)')
    assert js["kind"] == "values"
    assert js["values"] == ["125 to 162", "163 to 187", "188 to 200", "200 to 220", "220 to 237",
                            "238 to 255", "255 to 265", "265 to 290", "290 to 375", "375 to "]


def test_palladino_percent_column_keeps_decimals():
    js = _json('@from:={.5,.65,.75,.801,.88,0.95,1.02,1.06,1.161,1.5}*100, '
               '@to:={.649,.749,.8,.879,0.949,1.019,1.059,1.16,1.499,NA}*100, '
               'string(@from) + " to " + string(@to)')
    assert js["values"][0] == "50 to 64.9"
    assert js["values"][-1] == "150 to "


def test_string_list_column_passes_through():
    js = _json('{"Pmax", "Pmax/FRC", "FRC", "FRC/FTP", "FRC/FTP", "FTP"}')
    assert js == {"kind": "values", "values": ["Pmax", "Pmax/FRC", "FRC", "FRC/FTP", "FRC/FTP", "FTP"]}


def test_targetname_levels_high_to_low():
    js = _json("targetname({5:0:-1})")
    assert js["kind"] == "values"
    assert js["values"][0] == "Max" and js["values"][-1] == "Extensive Aerobic (FTP)"
    assert len(js["values"]) == 6


def test_viewer_draws_values_series_as_a_table():
    """Regression: the viewer only plotted points / scalar values, so list-valued
    table charts showed nothing but the collapsed formula list."""
    html = VIEWER.read_text(encoding="utf-8")
    assert "function drawValuesTable(" in html
    assert 's.data.kind === "values" && s.type !== "gauge"' in html
