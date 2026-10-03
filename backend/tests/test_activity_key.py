"""Stored per-activity data resolves by start time (engine/activity_key.py),
so it holds across a 資料來源 switch (and for keys stored with the old merged
source's "coros/" prefix); the chart source; cptest.curves reading only the
資料來源's folder.
Synthetic data only."""
import asyncio
import datetime as dt
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from backend.engine import activity_key as AK

WKO4 = "2025/Example_2025_04_12_10_32.wko4"         # local 10:32
COROS = "2025/470000000000000001_2025-04-12_trail_run.fit"
START = datetime(2025, 4, 12, 10, 32, 40)             # the FIT's local start (40 s later)


def _w(file, start, idx=0):
    return NS(idx=idx, entry=NS(file=file, start=start), sport="walk", sport_type="hiking", tags=["hiking"])


def test_names_and_bare_paths():
    assert AK.start_from_name(WKO4) == datetime(2025, 4, 12, 10, 32)
    assert AK.start_from_name(COROS) is None
    assert AK.bare("coros/" + COROS) == COROS and AK.bare("tp/2025/x.fit") == "2025/x.fit"
    assert AK.same_file("coros/" + COROS, COROS) and not AK.same_file("tp/" + COROS, "2025/other.fit")


def test_by_start_dict():
    d = AK.ByStartDict({WKO4: {"pack_kg": 12.0}, "2024/a.fit": {"pack_kg": 5.0, "start": "2024-05-01T08:00"}})
    assert d.find("coros/2024/a.fit")["pack_kg"] == 5.0                       # same file, merged prefix
    assert d.find(COROS, START)["pack_kg"] == 12.0                            # WKO5 name's start ±3 min
    assert d.find("tp/2024/x.fit", datetime(2024, 5, 1, 8, 2))["pack_kg"] == 5.0     # record "start"
    assert d.find(COROS, START + dt.timedelta(minutes=5)) is None             # beyond ±3 min
    assert d.key_for(COROS, START) == WKO4 and d.key_for("new.fit") == "new.fit"
    # .get(file) uses a registered Dataset's start for that file
    AK.register_dataset(NS(source="synced", config=NS(parity=False), excluded=[],
                           workouts=[_w("coros/" + COROS, START, 3)]))
    assert d.get("coros/" + COROS)["pack_kg"] == 12.0 and ("coros/" + COROS) in d
    assert AK.start_of_file(COROS) == START.isoformat()                        # the bare path too


def test_pack_weight_survives_a_source_switch(tmp_path):
    from backend.engine.racepower import athlete as A
    p = tmp_path / "hike_meta.json"
    A.set_hike_meta(WKO4, 12.0, path=p)                       # set on the WKO5 source
    meta = A.hike_meta(p)
    w = _w("coros/" + COROS, START)                            # read on 同步資料
    assert A.activity_pack(w, meta)["pack_kg"] == 12.0
    # set again from the COROS file: the same record is updated, not a second one
    A.set_hike_meta("coros/" + COROS, 9.0, path=p, start=START)
    raw = json.loads(p.read_text("utf-8"))["trips"]
    assert list(raw) == [WKO4] and raw[WKO4]["pack_kg"] == 9.0 and raw[WKO4]["start"] == "2025-04-12T10:32"
    # a new trip from a FIT source carries its start (any source finds it later)
    A.set_hike_meta("tp/2026/tp_2026_01_01_1.fit", 3.0, path=p, start=datetime(2026, 1, 1, 7, 0))
    meta = A.hike_meta(p)
    assert meta.find("2026/9_2026-01-01_hike.fit", datetime(2026, 1, 1, 7, 1, 30))["pack_kg"] == 3.0
    A.set_hike_meta(COROS, None, path=p, start=START)          # clearing finds it too
    assert WKO4 not in json.loads(p.read_text("utf-8"))["trips"]


def test_solo_hikes_match_by_start(tmp_path):
    from backend.engine.racepower import athlete as A
    p = tmp_path / "solo.json"
    A.set_solo_hikes([WKO4], path=p)
    solo = A.solo_hikes(p)
    assert WKO4 in solo and "coros/" + COROS not in solo        # no start known for the FIT yet
    AK.register_dataset(NS(source="coros", config=NS(parity=False), excluded=[], workouts=[_w(COROS, START)]))
    assert COROS in solo and "coros/" + COROS in solo
    assert json.loads(p.read_text("utf-8"))["starts"] == {WKO4: "2025-04-12T10:32"}


def test_rebase_done_by_and_unlinked():
    from backend.engine.plan_store import unlinked_indexes
    acts = [{"index": 0, "start": "2025-04-09T07:00:00"}, {"index": 5, "start": "2025-04-12T10:32:40"}]
    ss = [{"state": "done", "done_by": {"index": 801, "start": "2025-04-12T10:32:00", "tss": 90}},
          {"state": "done", "done_by": {"index": 9, "start": "2025-04-11T09:00:00"}},     # not in this source
          {"state": "done", "done_by": {"index": 7, "start": "2024-01-01T09:00:00"}},     # outside the range
          {"state": "active", "done_by": None}]
    assert AK.rebase_done_by(ss, acts) == 2
    assert ss[0]["done_by"] == {"index": 5, "start": "2025-04-12T10:32:00", "tss": 90}
    assert ss[1]["done_by"]["index"] is None and ss[2]["done_by"]["index"] == 7
    assert unlinked_indexes([{"start": "2025-04-12T10:31:10", "index": 801}], acts) == {5}


def test_rebase_stored_uses_the_registered_dataset(monkeypatch):
    from backend.engine.wko5expr import datasource as DS
    monkeypatch.setattr(DS, "current_source", lambda user_id=1: "synced")
    ss = [{"state": "done", "done_by": {"index": 801, "start": "2025-04-12T10:32:00"}}]
    AK.rebase_stored(ss)
    assert ss[0]["done_by"]["index"] == 801                     # nothing registered: unchanged
    AK.register_dataset(NS(source="synced", config=NS(parity=False), excluded=[],
                           workouts=[_w("coros/" + COROS, START, 4)]))
    AK.register_dataset(NS(source="synced", config=NS(parity=True), excluded=[], workouts=[]))   # parity: ignored
    assert AK.rebase_stored(ss)[0]["done_by"]["index"] == 4


def test_activity_tag_and_peak_name_across_sources(tmp_path):
    from backend.engine import activity_tags as AT
    from backend.engine.achievements import Annotations
    rows = [{"start_local": "2025-04-12T10:32", "file": COROS, "tags": ["當作間歇"]}]
    assert AT.find(rows, None, "coros/" + COROS) is rows[0]
    assert AT.find(rows, START + dt.timedelta(seconds=50), "tp/2025/tp_2025_04_12_9.fit") is rows[0]
    ann = Annotations(tmp_path / "ann.json")
    ann.set_record(WKO4, name="測試山")
    AK.register_dataset(NS(source="synced", config=NS(parity=False), excluded=[],
                           workouts=[_w("coros/" + COROS, START)]))
    ann = Annotations(tmp_path / "ann.json")
    assert ann.record("coros/" + COROS) == {"name": "測試山", "start": "2025-04-12T10:32"}
    ann.set_record("coros/" + COROS, note="霧")
    assert list(json.loads((tmp_path / "ann.json").read_text("utf-8"))["records"]) == [WKO4]


def test_weather_by_start_for_another_source():
    from backend.engine import zone_events as ZE
    acts = [{"file": WKO4, "date": "2025-04-12", "temp_c": 18.5, "hadley": 110.0},
            {"file": "2025/Example_2025_04_12_17_00.wko4", "date": "2025-04-12", "temp_c": 25.0}]
    ds = NS(workouts=[_w("coros/" + COROS, START, 0)])
    assert ZE.weather_of(ds, acts) == {0: {"temp_c": 18.5, "hadley": 110.0}}   # two rows that day: by start


# ---- 圖表資料來源: the 資料來源's folder, old values follow it ------------------

def test_current_source_and_settings_api(tmp_path, monkeypatch):
    import sqlite3
    from backend.engine.wko5expr import datasource as DS
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE user_settings (id INTEGER PRIMARY KEY, user_id INT, key TEXT, value_json TEXT, updated_at TEXT)")
    con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, 'charts.data_source', '\"tp\"')")
    con.commit()
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    assert DS.current_source() == "coros"                       # an old per-folder pick follows the 資料來源
    con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, 'sync.primary_source', '\"trainingpeaks\"')")
    con.commit()
    con.close()
    assert DS.current_source() == "tp"

    from backend.api.sync import SyncSettingsBody, get_sync_settings, put_sync_settings
    from backend.tests.test_sync_e2e import make_session

    async def go():
        s = await make_session(tmp_path)
        from backend.db.models import UserSetting
        s.add(UserSetting(user_id=1, key="charts.data_source", value_json=json.dumps("synced")))
        await s.commit()
        got = await get_sync_settings(1, s)
        assert got["chart_data_source"] == "source" and got["chart_data_source_stored"] == "synced"
        r = await put_sync_settings(SyncSettingsBody(chart_data_source="source"), 1, s)
        assert r["chart_data_source"] == "source"
    asyncio.new_event_loop().run_until_complete(go())


# ---- cptest reads only the 資料來源 ----------------------------------------------

def test_cptest_curves_follow_the_primary(tmp_path, monkeypatch, _fit_root_in_tmp):
    from backend.engine.racepower import cptest as T
    from backend.tests.fit_builder import build_run
    monkeypatch.setenv("WKO5COACH_TZ", "UTC")
    t0 = datetime(2025, 12, 15, 1, 0, tzinfo=timezone.utc)
    files = {"coros/2025/1_2025-12-15_run.fit": build_run(t0, seconds=900, power=[220] * 900, stryd=True),
             "tp/2025/tp_2025_12_15_77.fit": build_run(t0 + dt.timedelta(seconds=30), seconds=900,
                                                       power=[230] * 900, stryd=True),
             "tp/2025/tp_2025_12_16_78.fit": build_run(t0 + dt.timedelta(days=1), seconds=600,
                                                       power=[200] * 600, stryd=True)}     # TP only
    for rel, data in files.items():
        p = tmp_path / "fit" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    span = (dt.date(2025, 12, 1), dt.date(2025, 12, 31))
    monkeypatch.setattr(T, "unused_folder", lambda: "tp")
    got = sorted(c["path"].replace("\\", "/") for c in T.curves(tmp_path, *span))
    assert got == ["coros/2025/1_2025-12-15_run.fit"]           # the TP-only run is not back-filled
    monkeypatch.setattr(T, "unused_folder", lambda: "coros")
    got = sorted(c["path"].replace("\\", "/") for c in T.curves(tmp_path, *span))
    assert got == ["tp/2025/tp_2025_12_15_77.fit", "tp/2025/tp_2025_12_16_78.fit"]
    assert T._file_date(tmp_path / "tp_2025_12_16_78.fit") == dt.date(2025, 12, 16)
