"""Workout samples for the route map + synced hover: one downsampled index that
lines up with the workout charts' points, and the map basemap settings."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api import wko5views
from backend.engine.wko5expr import render
from backend.settings.repository import MAP_BASEMAPS, MAP_OVERLAYS, validate
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 9, 30)
VIEWER = Path(__file__).resolve().parents[1] / "static" / "wko5_viewer.html"
SETTINGS = Path(__file__).resolve().parents[1] / "static" / "settings.html"


def _trail(secs=10, gps=True):
    ch = {"elapsedtime": list(range(1, secs + 1)), "heartrate": [140.0 + i for i in range(secs)],
          "power": [200.0] * secs, "elevation": [100.0 + i for i in range(secs)],
          "elapseddistance": [i * 0.01 for i in range(secs)]}
    if gps:
        # no fix for the first two samples: None, then a (0, 0) "no fix"
        ch["latitude"] = [None, 0.0] + [25.0 + i * 1e-4 for i in range(2, secs)]
        ch["longitude"] = [None, 0.0] + [121.5] * (secs - 2)
    return FakeWorkout(dt.datetime.combine(TODAY, dt.time(7)), "run", channels=ch)


@pytest.fixture
def fake_ds(monkeypatch):
    ds = FakeDataset([_trail(), _trail(gps=False)], TODAY)
    monkeypatch.setattr(wko5views, "_dataset", lambda parity=None: ds)
    return ds


def test_samples_are_parallel_arrays_on_the_chart_step(fake_ds, monkeypatch):
    monkeypatch.setattr(render, "MAX_POINTS", 4)       # 10 samples -> step 3, like _downsample
    r = wko5views.workout_samples(0)
    assert r["step"] == 3 and r["n"] == 4
    assert r["t"] == [1.0, 4.0, 7.0, 10.0]
    # the same x values the workout charts plot
    import numpy as np
    t = np.arange(1, 11, dtype=float)
    assert [p[0] for p in render._downsample(t, t)] == r["t"]
    for k in ("d", "lat", "lng", "elev", "hr", "power"):
        assert len(r[k]) == r["n"], k
    assert r["lat"][0] is None and r["lng"][0] is None
    assert r["lat"][1] == pytest.approx(25.0003) and r["hr"][1] == 143.0
    assert r["d"][3] == pytest.approx(0.09)
    assert r["grade"] is None or len(r["grade"]) == r["n"]


def test_zero_zero_is_no_fix(fake_ds, monkeypatch):
    monkeypatch.setattr(render, "MAX_POINTS", 3000)
    r = wko5views.workout_samples(0)
    assert r["step"] == 1 and r["lat"][:2] == [None, None] and r["lng"][:2] == [None, None]


def test_samples_without_gps_and_bad_index(fake_ds):
    r = wko5views.workout_samples(1)
    assert r["lat"] is None and r["lng"] is None and len(r["t"]) == 10
    with pytest.raises(HTTPException):
        wko5views.workout_samples(5)


def test_map_settings_validation():
    for b in MAP_BASEMAPS:
        validate("charts.map.basemap", b)
    validate("charts.map.overlays", [])
    validate("charts.map.overlays", list(MAP_OVERLAYS))
    with pytest.raises(ValueError):
        validate("charts.map.basemap", "bing")
    with pytest.raises(ValueError):
        validate("charts.map.overlays", ["contour", "contour"])
    with pytest.raises(ValueError):
        validate("charts.map.overlays", "contour")


def test_viewer_and_settings_wire_the_map():
    html = VIEWER.read_text(encoding="utf-8")
    assert "leaflet/1.9.4/leaflet.min.js" in html
    for bid in MAP_BASEMAPS:
        assert f'id: "{bid}"' in html
    # NLSC WMTS is z/y/x, Google terrain stops at 15
    assert "GoogleMapsCompatible/{z}/{y}/{x}" in html and "lyrs=p&x={x}&y={y}&z={z}\", maxZoom: 15" in html
    assert "/samples?" in html and "requestAnimationFrame(hoverFlush)" in html
    assert 'type: "showTip"' in html and "tileerror" in html
    s = SETTINGS.read_text(encoding="utf-8")
    assert 'id="m-base"' in s and 'name="m-ovl"' in s and "map_basemap" in s and "map_overlays" in s
