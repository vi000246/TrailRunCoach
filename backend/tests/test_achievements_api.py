"""The achievements API as the 活動列表 page's 成就 tab reads it (synthetic records)."""
from __future__ import annotations

import pytest

from backend.api import achievements as AP
from backend.engine import achievements as E


def _rec(rid, start, top, kind=E.KIND_HIKE, moving=7200.0):
    return E.Achievement(id=rid, start=start, kind=kind, mclass=E.mountain_class(top), auto_name=f"auto {rid}",
                         distance_km=12.0, climbing_m=900.0, descending_m=900.0, moving_s=moving,
                         elapsed_s=moving + 600, top_m=top)


@pytest.fixture
def api(monkeypatch, tmp_path):
    recs = [_rec("a.fit", "2026-05-01T06:30:12", 3400.0),
            _rec("b.fit", "2026-06-02T07:05:00", 1800.0, kind=E.KIND_TRAIL),
            _rec("c.fit", "2026-07-03T08:00:59", 600.0)]
    monkeypatch.setattr(AP, "_dataset", lambda: None)
    monkeypatch.setattr(AP, "build_achievements", lambda ds: recs)
    monkeypatch.setattr(AP, "load_peaks", lambda: [])
    monkeypatch.setattr(E, "ANNOTATIONS_PATH", tmp_path / "annotations.json")
    from backend.engine import region as RG
    monkeypatch.setattr(RG, "is_tw", lambda user_id=1: True)
    return AP


def _export(api, **kw):
    args = dict(kind=None, mclass=None, q=None, min_km=None, max_km=None, min_climb=None, max_climb=None,
                min_top=None, date_from=None, date_to=None, sort="date", limit=10)
    return api.export_text(**{**args, **kw})


def test_export_names_baiyue_only_in_taiwan(api, monkeypatch):
    from backend.engine import region as RG
    api.build_achievements(None)[0].peaks = [{"name": "Jade", "elevation_m": 3952}]
    assert "Jade(3952)" in _export(api) and f"{E.CLASS_BAIYUE} 1" in _export(api)
    monkeypatch.setattr(RG, "is_tw", lambda user_id=1: False)
    text = _export(api)
    assert "Jade(3952)" not in text and E.CLASS_BAIYUE not in text and "3000 m+ 1" in text


def test_rows_carry_the_activity_key_and_language_neutral_ids(api):
    rows = {r["id"]: r for r in api.list_achievements()["rows"]}
    assert rows["a.fit"]["key"] == "2026-05-01T06:30"            # = the 活動列表 row's key (start minute)
    assert [rows[k]["class_id"] for k in ("a.fit", "b.fit", "c.fit")] == ["baiyue", "mid", "low"]
    assert rows["b.fit"]["kind_id"] == "trail" and rows["a.fit"]["kind_id"] == "hike"


def test_filters_take_ids_as_well_as_names(api):
    ids = lambda **kw: sorted(r["id"] for r in api.list_achievements(**kw)["rows"])
    assert ids(mclass="baiyue,low") == ["a.fit", "c.fit"]
    assert ids(mclass=E.CLASS_MID) == ["b.fit"]
    assert ids(kind="trail") == ["b.fit"] and ids(kind=E.KIND_HIKE) == ["a.fit", "c.fit"]
    text = _export(api, mclass="mid")
    assert "auto b.fit" in text and "auto a.fit" not in text


def test_null_clears_an_annotation_and_unsent_fields_stay(api):
    api.annotate_record("a.fit", api.RecordAnnotation(name="Jade", shang_he_min=100))
    r = next(r for r in api.list_achievements()["rows"] if r["id"] == "a.fit")
    assert r["name"] == "Jade" and r["shang_he_ratio"] == 1.2
    api.annotate_record("a.fit", api.RecordAnnotation(shang_he_min=None))     # the tab's cleared 上河 box
    r = next(r for r in api.list_achievements()["rows"] if r["id"] == "a.fit")
    assert r["shang_he_min"] is None and r["name"] == "Jade"
    api.annotate_record("a.fit", api.RecordAnnotation(hidden=True))
    assert [r["id"] for r in api.list_achievements()["rows"]] == ["c.fit", "b.fit"]
    assert len(api.list_achievements(include_hidden=True)["rows"]) == 3
