"""負重訓練（engine/loaded_carry.py; docs/research/loaded-carry-training.md):
the trip pack on the event, the pack per activity (racepower_hike_meta.json,
the activity-tags API), the schedule (stages, frequency, caps, B2B, taper,
ME), the projection over a synthetic 3-day 百岳 and the evaluation. Synthetic
data only — never the WKO5 folder, the app DB, the user's plan or COROS."""
import asyncio
import datetime as dt
from datetime import date

import numpy as np
import pytest

from backend.engine import loaded_carry as LC
from backend.engine.planning import Event, Plan
from backend.engine.racepower import athlete as A
from backend.engine.racepower import hikehr as HH


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def test_event_pack_kg_default_and_validation():
    e = Event(id="x", name="嘉明湖", date="2026-12-05", kind="baiyue", days=3)
    assert e.pack_kg is None and e.pack == 9.0
    plan = Plan()
    ev = plan.upsert_event({"name": "嘉明湖", "date": "2026-12-05", "kind": "baiyue", "days": 3, "pack_kg": "11.5"})
    assert ev.pack_kg == 11.5 and ev.pack == 11.5
    assert plan.upsert_event({"name": "b", "date": "2026-12-05", "pack_kg": ""}).pack_kg is None
    with pytest.raises(ValueError):
        plan.upsert_event({"name": "c", "date": "2026-12-05", "pack_kg": 55})


def test_event_pack_kg_round_trips_through_the_plan_file(tmp_path):
    plan = Plan()
    plan.upsert_event({"id": "e1", "name": "嘉明湖", "date": "2026-12-05", "kind": "baiyue", "days": 3, "pack_kg": 10})
    p = tmp_path / "plan.json"
    plan.save(p)
    assert Plan.load(p).events[0].pack_kg == 10.0


def test_trip_and_stage_weights():
    assert LC.trip_kg({"days": 3}) == 9.0 and LC.trip_kg({"days": 3, "pack_kg": 7.0}) == 7.0
    assert LC.stage_kgs(68.0, 9.0) == [3.5, 7.0, 9.0]                   # 5 %, 10 %, the trip pack
    assert LC.stage_kgs(68.0, 3.0) == [3.0, 3.0, 3.0]                   # a trip pack < 5 %: the steps collapse
    assert LC.stage_kgs(None, 9.0) == [None, None, 9.0]
    assert LC.machine_cap(68.0, 9.0) == 10.5                            # 1.15 × 9 = 10.35 → 10.5 ≤ 13.6
    assert LC.machine_cap(50.0, 9.0) == 10.0                            # 20 % of 50 kg wins
    assert LC.machine_cap(40.0, 9.0) == 9.0                             # never below the trip pack
    kgs = [3.5, 7.0, 9.0]
    assert [LC.stage_done(k, kgs) for k in (0, 2.0, 3.0, 5.7, 7.2, 9.0)] == [0, 0, 1, 2, 3, 3]


def test_hikehr_filter_windows_hr_band():
    rows = [{"k": k, "g": 0.2, "v": 0.8, "hr": hr} for k, hr in enumerate([120, 128, 130, 135, 140, 146, 148, 150])]
    assert [w["hr"] for w in HH.filter_windows(rows, 142.0)] == [146, 148, 150]       # the 百岳 filter: ≥ AeT
    band = HH.filter_windows(rows, 142.0, hr_band=LC.TRAIN_HR_BAND)                   # AeT − 15 … AeT + 3
    assert [w["hr"] for w in band] == [128, 130, 135, 140]


class _W:
    def __init__(self, file, start=dt.datetime(2026, 9, 26, 7)):
        self.entry = type("E", (), {"file": file, "start": start})()


def test_activity_pack_reads_and_writes_the_hike_meta_file():
    w = _W("coros/2026/a.fit")
    p = LC.activity_pack(w, planned=7.0)
    assert p["pack_kg"] is None and not p["recorded"] and p["default_kg"] == 7.0
    A.set_hike_meta(w.entry.file, 6.5)
    p = LC.activity_pack(w)
    assert p["pack_kg"] == 6.5 and p["recorded"] and p["range"] == [0, 40]
    assert LC.meta_pack_of()(w) == 6.5
    with pytest.raises(ValueError):
        A.set_hike_meta(w.entry.file, 41)


def test_activity_tags_api_patch_pack_kg_only(monkeypatch):
    from fastapi import HTTPException
    from backend.api import wko5views as V
    w = _W("fake/0.wko4")
    ds = type("DS", (), {"workouts": [w]})()
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    monkeypatch.setattr(V, "_activity_json", lambda ds_, w_: {"pack": V._pack_json(w_)})

    async def save(*a, **k):                                    # the tag DB is never touched for a pack edit
        raise AssertionError("no tag write")
    import backend.api.workouts as WK
    monkeypatch.setattr(WK, "save_activity_tag", save)
    run = lambda c: asyncio.new_event_loop().run_until_complete(c)
    r = run(V.patch_activity(0, {"pack_kg": 9}))
    assert r["pack"]["pack_kg"] == 9.0 and A.hike_meta()["fake/0.wko4"]["pack_kg"] == 9.0
    r = run(V.patch_activity(0, {"pack_kg": None}))
    assert r["pack"]["pack_kg"] is None
    with pytest.raises(HTTPException):
        run(V.patch_activity(0, {"pack_kg": 99}))


def test_planned_kg_from_the_title():
    assert LC.planned_kg_of_title("長時間輕鬆（山路） · 背 7 kg") == 7.0
    assert LC.planned_kg_of_title("背包爬坡機 · 背 10.5 kg") == 10.5
    assert LC.planned_kg_of_title("輕鬆跑") is None
