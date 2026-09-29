import sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.files.wko5chart_reader import decode, read_view, WKO5ChartFormatError

ROOT = Path(__file__).resolve().parents[2]
SEASON = ROOT / "WKO5 Season View" / "WKO5 Season View.wko5chart"
WORKOUT = ROOT / "WKO5 Workout View" / "WKO5 Workout View.wko5chart"


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


@pytest.mark.skipif(not SEASON.exists(), reason="user export not present")
def test_season_view_inventory():
    view = read_view(SEASON)
    assert view["view"] == "WKO5 Season View"
    assert len(view["dashboards"]) == 10
    charts = [c for d in view["dashboards"] for c in d["charts"]]
    assert len(charts) == 72
    acr = charts[0]
    assert acr["title"] == "ATL CTL Ratio 訓練負荷比 (only run)"
    assert acr["series"][0]["expression"] == \
        'tl(if(sport="run",tss),atlconstant)/tl(if(sport="run",tss),ctlconstant)'
    titles = {c["title"] for c in charts}
    assert "Daily % of CTL (run TSS/CTL)" in titles  # entity-unescaped


@pytest.mark.skipif(not WORKOUT.exists(), reason="user export not present")
def test_workout_view_inventory():
    view = read_view(WORKOUT)
    charts = [c for d in view["dashboards"] for c in d["charts"]]
    assert len([c for c in charts if c["kind"] == "workout"]) == 52
    assert any(c.get("class") == "PKMapPanelConfig" for c in charts)
