import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.files.wko5chart_reader import decode, read_view, WKO5ChartFormatError

from backend.tests import wko5chart_builder as WB


def _tag(fid, wire):
    v, out = fid << 3 | wire, b""
    while True:
        b = v & 0x7F
        v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out


def _str(fid, s):
    raw = s.encode()
    return _tag(fid, 3) + bytes([len(raw)]) + raw


def test_decodes_all_wire_types():
    inner = _str(461, 'tl(tss,42)') + _tag(190, 6) + struct.pack("<f", 0.5)
    body = _tag(412, 1) + b"\x02" + _tag(420, 2) + struct.pack("<d", 1.5) \
        + _tag(415, 4) + bytes([len(inner)]) + inner
    rec = decode(b"wko5chart\x1a" + body)
    assert rec.get(412) == 2 and rec.get(420) == 1.5
    assert rec.get(415).get(461) == "tl(tss,42)"
    assert rec.get(415).get(190) == 0.5


def test_rejects_non_wko5chart():
    with pytest.raises(WKO5ChartFormatError):
        decode(b"PK\x03\x04garbage")


def test_wire5_blob_is_kept_raw_and_unpackable():
    from backend.files.wko5chart_reader import unpack_varints
    samples = b"\xd0\x0f\xa0\x1f\xd0\x0f"  # 2000, 4000, 2000 (ms deltas)
    rec = decode(b"wko4\x1a" + _tag(925, 5) + bytes([len(samples)]) + samples)
    assert unpack_varints(rec.get(925)) == [2000, 4000, 2000]


def test_season_view_inventory(tmp_path):
    """A synthetic export (wko5chart_builder.py): view / dashboard / chart titles,
    series expressions, axes; WKO5's XML-escaped text is unescaped."""
    view = read_view(WB.season_view(tmp_path / "s.wko5chart", "Exports/WKO5 Season View"))
    assert view["view"] == "WKO5 Season View"            # the folder part of the title is dropped
    assert [d["title"] for d in view["dashboards"]] == ["Load", "PDC"]
    charts = [c for d in view["dashboards"] for c in d["charts"]]
    assert [c["kind"] for c in charts] == ["athlete", "athlete"]
    assert charts[0]["title"] == "Daily % of CTL (run TSS/CTL)"   # entity-unescaped
    assert charts[0]["axes"] == [{"id": "PERCENT", "min": 0.0, "max": None}]
    pd = charts[1]
    assert [s["name"] for s in pd["series"]] == ["MMP Curve", "New Bests"]
    assert pd["series"][0]["expression"] == "meanmax(runpower)"
    assert pd["series"][1]["type"] == "area" and pd["series"][1]["y_axis"] == "WATTS"


def test_workout_view_inventory(tmp_path):
    view = read_view(WB.workout_view(tmp_path / "w.wko5chart"))
    charts = [c for d in view["dashboards"] for c in d["charts"]]
    assert [c["kind"] for c in charts] == ["workout", "other"]
    assert charts[1]["class"] == "PKMapPanelConfig"
    assert [s["expression"] for s in charts[0]["series"]] == ["power", "heartrate"]
