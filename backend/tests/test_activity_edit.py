"""活動編輯 page (static/activity.html): the name / free-form tags store, the
key-based and bulk API, the terrain override reset, and the watch-recorded
RPE (FIT session workout_rpe) as an effort input. tmp / in-memory DBs only;
no WKO5 folder, no real DB, no COROS calls."""
import asyncio
import datetime as dt
import sqlite3
from datetime import timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT
from backend.tests.fit_builder import build_run

STATIC = Path(__file__).resolve().parents[1] / "static"
T0 = dt.datetime(2025, 12, 13, 1, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- store: name and tags -----------------------------------------------------

def test_name_and_tags_upsert_clear_and_validate(tmp_path):
    db = tmp_path / "t.db"
    AT.upsert(db, start_local="2026-07-27T09:36", name="  五寮尖  ", tags=["百岳", " 雨天 ", "百岳", "", "雨天"])
    r = AT.load(db)[0]
    assert r["name"] == "五寮尖" and r["tags"] == ["百岳", "雨天"]
    AT.upsert(db, start_local="2026-07-27T09:36", note="x")               # untouched fields stay
    assert AT.load(db)[0]["tags"] == ["百岳", "雨天"]
    AT.upsert(db, start_local="2026-07-27T09:36", name="", tags=[])         # cleared → original title, no tags
    r = AT.load(db)[0]
    assert r["name"] is None and r["tags"] == [] and r["note"] == "x"
    with pytest.raises(ValueError):
        AT.upsert(db, start_local="2026-07-27T09:36", tags=["x" * 31])
    with pytest.raises(ValueError):
        AT.upsert(db, start_local="2026-07-27T09:36", tags=[f"t{i}" for i in range(21)])
    assert AT.validate(name="n" * 201) == "INVALID_NAME"
    m = AT.merge({"activity_type": "training", "effort": "easy"},
                 {"name": "晨跑", "tags_json": '["a", "b"]', "start_local": "k"})
    assert m["name"] == "晨跑" and m["tags"] == ["a", "b"]


def test_upsert_adds_late_columns_to_an_old_table(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, athlete_id INTEGER, start_local TEXT, "
                "source TEXT, file TEXT, workout_id INTEGER, distance_km REAL, label TEXT, activity_type TEXT, "
                "activity_type_overridden BOOLEAN, effort TEXT, effort_overridden BOOLEAN, note TEXT, "
                "updated_at DATETIME, UNIQUE(athlete_id, start_local))")
    con.commit()
    con.close()
    assert AT.load(db) == []
    AT.upsert(db, start_local="2025-01-01T08:00", tags=["a"], exclusion="keep")
    r = AT.load(db)[0]
    assert r["tags"] == ["a"] and r["exclusion"] == "keep"


def test_migration_adds_the_new_columns():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        import backend.db.database as db_mod
        async with engine.begin() as conn:      # tables from before: no name / tags_json / rpe / feel
            await conn.execute(text("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, start_local TEXT)"))
            await conn.execute(text("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT)"))
            for t in ("sync_state", "athlete_settings", "plan_sessions"):
                await conn.execute(text(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY)"))
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            async with engine.begin() as conn:
                tags = {r[1] for r in (await conn.execute(text("PRAGMA table_info(activity_tags)"))).fetchall()}
                wf = {r[1] for r in (await conn.execute(text("PRAGMA table_info(workout_files)"))).fetchall()}
        finally:
            db_mod.engine = orig
            await engine.dispose()
        assert {"name", "tags_json", "exclusion"} <= tags and {"rpe", "feel"} <= wf
    _run(_inner())


# ---- workout_files API: name / tags, classification back to auto ------------------

async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, WorkoutFile
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = async_sessionmaker(engine, expire_on_commit=False)()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    s.add(WorkoutFile(id=10, athlete_id=1, file_path="/r.fit", file_format="fit", sport="running",
                      total_distance_m=12000, elevation_gain_m=900, start_time_utc=dt.datetime(2025, 10, 18, 12, 54),
                      workout_date=dt.date(2025, 10, 18), trail_classification="trail"))
    await s.commit()
    return s


def test_update_activity_name_and_tags():
    async def _inner():
        from backend.api.workouts import update_activity, ActivityUpdate
        s = await _session()
        r = await update_activity(10, ActivityUpdate(name="東眼山", tags=["賽前", "賽前", "熱"]), s)
        assert r["name"] == "東眼山" and r["tags"] == ["賽前", "熱"]
        r = await update_activity(10, ActivityUpdate(name=None), s)
        assert r["name"] is None and r["tags"] == ["賽前", "熱"]
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            await update_activity(10, ActivityUpdate(tags=["x" * 40]), s)
        assert e.value.status_code == 400
    _run(_inner())


def test_classification_override_and_back_to_auto():
    async def _inner():
        from backend.api.workouts import update_classification, ClassificationUpdate
        from backend.engine.algorithms.classify import classify_trail
        s = await _session()
        r = await update_classification(10, ClassificationUpdate(trail_classification="road"), s)
        assert r == {"id": 10, "trail_classification": "road", "classification_overridden": True}
        r = await update_classification(10, ClassificationUpdate(trail_classification="auto"), s)
        assert r["classification_overridden"] is False
        assert r["trail_classification"] == classify_trail("running", 12000, 900)
    _run(_inner())


# ---- the watch's RPE / feel --------------------------------------------------------

def test_recorded_from_session_reads_both_field_names():
    assert AT.recorded_from_session({"workout_rpe": 30, "workout_feel": 75}) == (3.0, 75)
    assert AT.recorded_from_session({"unknown_193": 90, "unknown_192": 100}) == (9.0, 100)   # python-fitparse
    assert AT.recorded_from_session({"workout_rpe": None, "workout_feel": 255}) == (None, None)
    assert AT.recorded_from_session(None) == (None, None)
    assert AT.feel_label(75) == "好" and AT.feel_label(None) is None


def test_effort_from_rpe_mapping_and_precedence_reason():
    assert AT.effort_from_rpe(None) is None
    assert AT.effort_from_rpe(3.0)["effort"] == "easy"
    assert AT.effort_from_rpe(6.0)["effort"] == "moderate"
    e = AT.effort_from_rpe(9.0, 0.02, {"effort": "moderate", "hr_frac": 0.88})
    assert e["effort"] == "max" and e["basis"] == "rpe" and e["hr_frac"] == 0.88 and "心率規則" in e["reason"]
    assert AT.effort_from_rpe(10.0, 0.15)["effort"] == "hard_with_rests"


def test_parse_fit_session_carries_the_rpe(tmp_path):
    from backend.files.fit_reader import parse_fit
    p = tmp_path / "g.fit"
    p.write_bytes(build_run(T0, seconds=300, workout_feel=75, workout_rpe=40))
    assert AT.recorded_from_session(parse_fit(str(p)).session) == (4.0, 75)
    q = tmp_path / "c.fit"
    q.write_bytes(build_run(T0, seconds=300))
    assert AT.recorded_from_session(parse_fit(str(q)).session) == (None, None)


def _wf_db(tmp_path, rows):
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT, file_format TEXT, "
                "start_time_utc DATETIME, rpe REAL, feel INTEGER)")
    con.executemany("INSERT INTO workout_files VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return db


def test_load_recorded_and_match_by_file_or_start(tmp_path, monkeypatch):
    monkeypatch.setenv("WKO5COACH_TZ", "Asia/Taipei")
    db = _wf_db(tmp_path, [(1, r"C:\fit\coros\2024\a_2024-03-12_run.fit", "fit", "2024-03-12 10:00:00", 4.0, 75),
                           (2, "/fit/b.fit", "fit", "2024-03-13 10:00:00", None, None)])
    rows = AT.load_recorded(db)
    assert rows == [{"start_local": "2024-03-12T18:00", "file": "a_2024-03-12_run.fit", "rpe": 4.0, "feel": 75}]
    assert AT.recorded_of(rows, dt.datetime(2024, 1, 1), "2024/a_2024-03-12_run.fit")["rpe"] == 4.0   # by file
    assert AT.recorded_of(rows, dt.datetime(2024, 3, 12, 18, 2), "2024/x.wko4")["feel"] == 75        # WKO5 copy, ±3 min
    assert AT.recorded_of(rows, dt.datetime(2024, 3, 12, 19, 0), "x.wko4") is None
    assert AT.load_recorded(tmp_path / "none.db") == []


def test_capacity_sample_uses_the_recorded_rpe_but_the_user_mark_wins(monkeypatch):
    from backend.engine.racepower import athlete as A
    from backend.tests.test_activity_tags import _fake_capacity_ds, _hist
    st = {"moving_s": 13900.0, "hr_avg": 152.0, "hist": _hist(152, 13900.0), "hist_lo": 40, "hr_s": 13900.0}
    ds, w = _fake_capacity_ds(monkeypatch, st, {"elapsed_s": 14500.0, "rest_share": 0.02})
    assert A.capacity_samples(ds, [w], tags=[], recorded=[])[0]["ok"]           # HR rule: 全力
    rec = [{"start_local": "2025-11-02T10:32", "file": "x.wko4", "rpe": 3.0, "feel": 50}]
    c = A.capacity_samples(ds, [w], tags=[], recorded=rec)[0]
    assert not c["ok"] and c["effort"]["effort"] == "easy" and c["effort"]["basis"] == "rpe"
    assert c["tags"]["effort_auto"] == "easy"
    tag = {"start_local": "2025-11-02T10:32", "file": "2025/x.wko4", "effort": "max", "effort_overridden": True}
    assert A.capacity_samples(ds, [w], tags=[tag], recorded=rec)[0]["ok"]       # the user's 全力 wins


def test_backfill_rpe_plan_and_apply(tmp_path):
    from backend.scripts import backfill_rpe as BF
    g, c = tmp_path / "g.fit", tmp_path / "c.fit"
    g.write_bytes(build_run(T0, seconds=300, workout_feel=100, workout_rpe=30))
    c.write_bytes(build_run(T0, seconds=300))
    db = _wf_db(tmp_path, [(1, str(g), "fit", "2025-12-13 01:00:00", None, None),
                           (2, str(c), "fit", "2025-12-14 01:00:00", None, None),
                           (3, str(tmp_path / "gone.fit"), "fit", None, None, None)])
    con = sqlite3.connect(db)
    items = BF.plan(con)
    assert items == [{"id": 1, "file_path": str(g), "rpe": 3.0, "feel": 100}]
    assert BF.apply(con, items) == 1
    assert con.execute("SELECT rpe, feel FROM workout_files WHERE id=1").fetchone() == (3.0, 100)
    assert BF.plan(con) == []                                                  # idempotent
    con.close()


# ---- 活動編輯 API on a FIT dataset ----------------------------------------------------

@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def _fit_ds(tmp_path, classes=None):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    d = tmp_path / "fit" / "coros" / "2025"
    d.mkdir(parents=True, exist_ok=True)
    (d / "0.fit").write_bytes(build_run(T0, seconds=1800, speed_m_s=3.0))
    (d / "1.fit").write_bytes(build_run(T0 + dt.timedelta(days=1), seconds=1037, speed_m_s=12.0, hr=66))   # a car
    root = tmp_path / "fit" / "coros"
    if classes is None:
        from backend.engine.wko5expr.fitdataset import _norm
        r = {"id": 7, "file_path": str(d / "0.fit"), "trail_classification": "road",
             "classification_overridden": True, "duplicate_of": None}
        classes = {_norm(d / "0.fit"): r, "_by_id": {7: r}, "_by_name": {"0.fit": [r]}, "_dups": {}}
    return FitFolderDataset(root, config=EngineConfig(parity=False), today=dt.date(2026, 1, 1),
                            corrections=CorrectionStore(tmp_path / "corr.json"), classifications=classes,
                            athlete_settings=[], estimate_thresholds=False, tz=timezone.utc)


def test_activities_list_terrain_and_excluded(tmp_path, no_plan, monkeypatch):
    from backend.api import wko5views as V
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    AT.upsert(db, start_local="2025-12-13T01:00", file="2025/0.fit", name="河濱", tags=["輕鬆跑"])
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    r = V.activities_list()
    acts = {a["file"]: a for a in r["activities"]}
    run, car = acts["2025/0.fit"], acts["2025/1.fit"]
    assert run["name"] == "河濱" and run["tags"] == ["輕鬆跑"] and run["key"] == "2025-12-13T01:00"
    assert run["terrain"] == {"value": "road", "editable": True, "overridden": True, "workout_file_id": 7,
                              "stored": "road", "why": None}
    assert car["index"] is None and car["excluded"]["auto"] and not car["terrain"]["editable"]
    assert set(r["types"]) == set(AT.TYPES) and r["exclude_enabled"] is True
    # the viewer's activity list carries the user's name and the key for the 編輯活動 link
    lst = V.workouts(begin="2025-12-01", end="2025-12-31", sports=None, parity=None)
    w0 = next(a for a in lst if a["file"] == "2025/0.fit")
    assert w0["name"] == "河濱" and w0["key"] == "2025-12-13T01:00"


def test_patch_activities_bulk_tags_type_and_excluded_by_key(tmp_path, monkeypatch):
    from backend.api import wko5views as V
    from backend.db.models import ActivityTag, Base
    import backend.db.database as D

    async def _inner():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(D, "AsyncSessionLocal", maker)
        items = [V.BulkItem(key="2025-12-13T01:00", file="2025/0.fit"),
                 V.BulkItem(key="2025-12-14T01:00", file="2025/1.fit")]
        r = await V.patch_activities(V.BulkBody(items=items, activity_type="training", add_tags=["冬訓", "冬訓"]))
        assert r["n"] == 2
        async with maker() as s:
            rows = {t.start_local: t for t in (await s.execute(select(ActivityTag))).scalars()}
        assert all(t.activity_type == "training" and t.activity_type_overridden for t in rows.values())
        assert AT.tags_of({"tags_json": rows["2025-12-14T01:00"].tags_json}) == ["冬訓"]
        # remove a tag (the in-memory DB is not what AT.load reads: pass the current list explicitly)
        r = await V.patch_activities(V.BulkBody(items=items[:1], tags=["冬訓", "長距離"], remove_tags=["冬訓"]))
        async with maker() as s:
            t = (await s.execute(select(ActivityTag).where(ActivityTag.start_local == "2025-12-13T01:00"))).scalar_one()
        assert AT.tags_of({"tags_json": t.tags_json}) == ["長距離"] and t.activity_type == "training"
        # back to auto for the type; nothing else changes
        await V.patch_activities(V.BulkBody(items=items[1:], activity_type=None))
        async with maker() as s:
            t = (await s.execute(select(ActivityTag).where(ActivityTag.start_local == "2025-12-14T01:00"))).scalar_one()
        assert not t.activity_type_overridden and AT.tags_of({"tags_json": t.tags_json}) == ["冬訓"]
        from fastapi import HTTPException
        for bad in (V.BulkBody(items=[]), V.BulkBody(items=[V.BulkItem(key="nope")], effort="max"),
                    V.BulkBody(items=items, effort="all_out"), V.BulkBody(items=items, add_tags=["x" * 40])):
            with pytest.raises(HTTPException):
                await V.patch_activities(bad)
        await eng.dispose()
    _run(_inner())


# ---- 活動列表: avg HR / power, RPE, edits never touch the source files ---------------------

def test_averages_of_sample_means():
    import numpy as np
    from backend.engine.wko5expr.fitdataset import averages_of
    r = averages_of(np.array([0.0, 140.0, np.nan, 160.0]), np.array([0.0, 200.0, 400.0, np.nan]))
    assert r == {"avg_hr": 150.0, "avg_power": 200.0}            # 0 bpm = no reading; 0 W counts (WKO5 avg)
    assert averages_of(None, np.zeros(5)) == {"avg_hr": None, "avg_power": None}


def test_activities_stats_and_recorded_rpe(tmp_path, no_plan, monkeypatch):
    from backend.api import wko5views as V
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "tags.db")
    monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: [
        {"start_local": "2025-12-13T01:00", "file": "0.fit", "rpe": 4.0, "feel": 75}])
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    run = next(a for a in V.activities_list()["activities"] if a["file"] == "2025/0.fit")
    assert run["rpe"] == 4.0 and run["feel"] == 75
    st = _run(V.activities_stats())
    assert st["2025-12-13T01:00"] == {"avg_hr": 140.0, "avg_power": None}     # build_run: 140 bpm, no power
    assert _run(V.activities_stats()) == st                                     # second call: from the cache


def test_edits_never_modify_the_source_files(tmp_path, no_plan, monkeypatch):
    """Every edit of the 活動列表 editor is app data (activity_tags keyed by
    start time, the pack in racepower_hike_meta.json); the FIT files keep
    their bytes and mtime, and 排除 is a flag, not a deletion."""
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.engine.racepower import athlete as RA
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "tags.db")
    monkeypatch.setattr(RA, "HIKE_META", tmp_path / "hike_meta.json")
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    files = sorted((tmp_path / "fit").rglob("*.fit"))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}

    async def _inner():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        i = next(w.idx for w in ds.workouts if w.entry.file == "2025/0.fit")
        await V.patch_activity(i, {"name": "河濱", "tags": ["輕鬆跑"], "effort": "easy", "note": "n"})
        await V.patch_activity(i, {"pack_kg": 6.5})
        await V.put_exclusion(V.ExclusionBody(key="2025-12-13T01:00", file="2025/0.fit", exclusion="exclude"))
        await V.patch_activities(V.BulkBody(items=[V.BulkItem(key="2025-12-14T01:00", file="2025/1.fit")], name="車"))
        await eng.dispose()
    _run(_inner())
    assert sorted((tmp_path / "fit").rglob("*.fit")) == files                  # nothing deleted or added
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
    assert RA.hike_meta(tmp_path / "hike_meta.json")                           # the pack went to the app's json


# ---- pages ----------------------------------------------------------------------------

def _catalog(loc, ns="activity"):
    import json
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text(encoding="utf-8"))


def test_activity_list_page_single_edit_and_i18n():
    import re
    page = (STATIC / "activity.html").read_text(encoding="utf-8")
    # one activity at a time: no batch selection / bulk bar
    assert 'id="bulk"' not in page and "S.checked" not in page and 'type="checkbox" aria-label' not in page
    assert 'data-edit=' in page and 'id="ed"' in page and "/api/v1/wko5/viewer?" in page
    assert "/activities/stats" in page and "S.usePower" in page
    zh, en = _catalog("zh-TW"), _catalog("en")
    used = {k for k in re.findall(r'\bT\("([\w.]+)"', page) if k[-1] not in "._"}   # T("key"); T("col." + k) below
    used |= set(re.findall(r'"activity\.([\w.]+)"', page))                      # data-i18n="activity.key"
    used |= set(re.findall(r':activity\.([\w.]+)', page))                       # data-i18n-attr
    used |= {f"col.{k}" for k in re.findall(r'\{ k: "(\w+)", cls:', page)} | {"col.actions"}
    used |= {f"sport.{s}" for s in re.findall(r'"([a-z ]+)"', page[page.index("const SPORTS"):page.index("const sportName")])}
    used |= {f"pain.{i}" for i in range(4)} | {"terrain.road", "terrain.trail", "f_activity_type", "f_effort"}
    assert not sorted(k for k in used if k not in zh)
    assert set(zh) == set(en)
    assert "原始 FIT 檔不會被改" in zh["help.editor"] and "原始 FIT 檔不會被改" in zh["help.page"]
    old = chr(0x81EA) + chr(0x7D44)                                            # the retired label (relabel_estimate)
    assert old not in page and old not in "".join(zh.values())


def test_nav_order_and_activity_list_name():
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    import re
    ids = re.findall(r'\{ id: "(\w+)"', shell)
    assert ids == ["home", "schedule", "charts", "plan", "activity", "routes", "racepower",
                   "achievements", "injuries", "settings"]
    assert _catalog("zh-TW", "shell")["page.activity.name"] == "活動列表"



def test_viewer_drops_the_card_and_links_the_edit_page():
    html = (STATIC / "wko5_viewer.html").read_text(encoding="utf-8")
    assert "activity_tags_card.js" not in html and not (STATIC / "activity_tags_card.js").exists()
    assert 'id="edit-act"' in html and "/api/v1/wko5/activities/page" in html
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    assert 'id: "activity"' in shell and "/api/v1/wko5/activities/page" in shell
    page = (STATIC / "activity.html").read_text(encoding="utf-8")
    assert 'data-page="activity"' in page and "\u81ea\u7d44" not in page
    from backend.api import wko5views as V
    assert Path(V.activities_page().path).name == "activity.html"


def test_climb_profile_is_one_overlaid_plot():
    html = (STATIC / "wko5_viewer.html").read_text(encoding="utf-8")
    body = html[html.index("function drawClimbProfile"):html.index("function drawGradeProfile")]
    assert 'position: "left"' in body and 'position: "right"' in body and "海拔（m）" in body
    assert "--cp-metric" in html and "gridIndex: 1" not in body          # one grid, no stacked panel
    assert "graphic: []" in body                                           # no inline caption on the plot
