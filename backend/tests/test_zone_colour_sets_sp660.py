"""SP-660: the zone sets the single activity's HR / power / pace lines are coloured by
(panels/activity_charts.zone_sets, GET /api/v1/app/workouts/{i}/zone-sets) and the viewer side
(charts.html: the 區間上色 toggle, the picker, one series per zone)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.engine.panels import activity_charts as A
from backend.tests.test_activity_charts import _ds, _run

ROOT = Path(__file__).resolve().parents[1]
VIEWER = (ROOT / "static" / "charts.html").read_text("utf-8")


def _sets(monkeypatch, plan="lthr", tpace=None):
    monkeypatch.setattr(A.HP, "effective_model", lambda ds=None, day=None, setting=None: {"model": plan})
    import backend.engine.thresholds as TH
    monkeypatch.setattr(TH, "estimate_tpace", lambda ds, day: {"value": tpace, "reason": "too few road runs"})
    hr = np.repeat([130.0, 150.0, 165.0], 300)
    t = np.arange(len(hr), dtype=float)
    ds = _ds(_run(t, hr, np.full(len(hr), 240.0)))
    return A.zone_sets(ds, ds.workouts[0])


def test_every_kind_lists_its_models_with_edges_easy_to_hard(monkeypatch):
    r = _sets(monkeypatch, tpace=5.0)
    assert set(r) == {"hr", "power", "pace"}
    cl = next(m for m in r["hr"]["models"] if m["id"] == "coroslthr")      # LTHR 160 (the fake's)
    assert cl["available"] and [(z["from"], z["to"]) for z in cl["rows"]] == [
        (0, 128), (128, 144), (144, 152), (152, 163), (163, 170), (170, None)]
    assert "seconds" not in cl["rows"][0]                                   # edges only, no time in zone
    pal = next(m for m in r["power"]["models"] if m["id"] == "palladino")
    assert pal["available"] and pal["rows"][0]["from"] == 0 and pal["rows"][-1]["to"] is None
    # Friel pace at a 5:00/km threshold pace: zone 1 ≥ 1.29 × 300 s, 5c < 0.90 × 300 s, easy → hard
    fp = r["pace"]["models"][0]
    assert fp["id"] == "frielpace" and fp["available"] and fp["estimate"]
    assert (fp["rows"][0]["from"], fp["rows"][0]["to"]) == (387.0, None)
    assert (fp["rows"][-1]["from"], fp["rows"][-1]["to"]) == (0, 270.0)
    assert "5:00" in fp["basis_text"] and "推估" in fp["basis_text"]


def test_the_default_is_the_settings_zones(monkeypatch):
    assert _sets(monkeypatch, plan="lthr")["hr"]["default"] == "coroslthr"
    assert _sets(monkeypatch, plan="hrr")["hr"]["default"] == "rqhrr"
    assert _sets(monkeypatch, plan="hrmax")["hr"]["default"] == "coroshrmax"
    monkeypatch.setattr(A.PZP, "setting", lambda: "stryd")
    assert _sets(monkeypatch)["power"]["default"] == "stryd"


def test_no_threshold_pace_says_why(monkeypatch):
    fp = _sets(monkeypatch, tpace=None)["pace"]["models"][0]
    assert not fp["available"] and fp["reason"] == "too few road runs"


def test_the_viewer_has_the_toggle_the_picker_and_one_series_per_zone():
    assert '<script src="/api/v1/static/zonecolors.js" defer></script>' in VIEWER
    assert "/zone-sets?" in VIEWER                          # read when first switched on
    assert 'data-zc="1"' in VIEWER and 'class="mczs"' in VIEWER
    assert "mcZoneSeries(se, zp.model.rows" in VIEWER       # the coloured line replaces that metric's series
    assert "mcSave({ zone: zc })" in VIEWER                 # on / off and the set are remembered
