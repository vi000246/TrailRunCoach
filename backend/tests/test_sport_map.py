"""SP-263: one platform-neutral activity type, the platform mapping table
(COROS sportType, Garmin / FIT sport + sub_sport, WKO5 names), the 圖表分析
activity-type filter and the user's mark winning. Synthetic data only."""
import datetime as dt
from datetime import timezone
from types import SimpleNamespace

import pytest

from backend.engine import activity_tags as AT
from backend.engine import overview as O
from backend.engine import sport_map as SM
from backend.engine.wko5expr.dataset import Workout, date_to_day


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------

def test_app_types_are_the_overview_categories():
    assert list(SM.APP_TYPES) == list(O.CATEGORIES)
    assert set(SM.FILTER_KINDS) == set(SM.APP_TYPES) | {SM.BAIYUE}


def test_every_table_value_is_an_app_type():
    for name, table in SM.PLATFORMS.items():
        assert table, name
        assert set(table.values()) <= set(SM.APP_TYPES), name
    assert set(SM.WKO5_GROUPS.values()) <= set(SM.APP_TYPES)


def test_every_coros_code_the_sync_names_is_mapped():
    from backend.sync.coros_sport import COROS_SPORT_TYPES
    assert set(COROS_SPORT_TYPES) <= set(SM.COROS)


def test_garmin_keys_are_fit_profile_names():
    sports, subs = set(SM.FIT_SPORT.values()), set(SM.FIT_SUB_SPORT.values())
    for sp, sub in SM.GARMIN:
        assert sp in sports and (sub is None or sub in subs), (sp, sub)


@pytest.mark.parametrize("code, t", [
    (100, "road"), (101, "road"), (102, "trail"), (103, "road"), (104, "hike"), (105, "hike"),
    (200, "bike"), (201, "bike"), (204, "bike"), (300, "other"), (301, "other"), (400, "other"),
    (402, "strength"), (900, "walk"), (10000, "other"), ("402", "strength"),
    (9904, None), (None, None), ("x", None),
])
def test_coros_codes(code, t):
    assert SM.coros_type(code) == t
    assert SM.type_of("coros", code) == t


@pytest.mark.parametrize("sport, sub, t", [
    ("running", None, "road"), ("running", "street", "road"), ("running", "trail", "trail"),
    ("running", "treadmill", "road"), ("running", "indoor_running", "road"), ("running", "generic", "road"),
    (1, 3, "trail"), ("1", "2", "road"),                         # raw enum numbers
    ("hiking", "generic", "hike"), ("hiking", None, "hike"), ("mountaineering", None, "hike"), (17, 0, "hike"),
    ("walking", "casual_walking", "walk"), ("cycling", "gravel_cycling", "bike"), ("e_biking", None, "bike"),
    ("training", "strength_training", "strength"), (10, 20, "strength"),
    ("training", "cardio_training", "strength"), ("training", None, "strength"),
    ("training", "yoga", "other"), ("fitness_equipment", "elliptical", "other"),
    ("fitness_equipment", "strength_training", "strength"),
    ("swimming", "lap_swimming", "other"), ("golf", None, "other"),
    ("generic", None, None), (None, None, None), (0, None, None), ("", "trail", None),
])
def test_fit_sport_sub_sport(sport, sub, t):
    assert SM.fit_type(sport, sub) == t
    assert SM.type_of("garmin", (sport, sub)) == t


def test_wko5_names():
    assert SM.type_of("wko5", "Trail Running") == "trail"
    assert SM.type_of("wko5", "hiking") == "hike"
    assert SM.type_of("wko5", "table tennis") is None


# what one account's 812 synced COROS activities carry (2026-10-06, aggregate: code, FIT
# sport, FIT sub_sport, the DB trail classification) and the app type each must get
PRODUCTION_COMBOS = [
    (100, "running", None, "road", "road"), (100, "running", "generic", "road", "road"),
    (100, "running", "generic", "trail", "trail"),
    (101, "running", "generic", "road", "road"), (101, "running", "treadmill", "road", "road"),
    (101, "running", "indoor_running", None, "road"),
    (102, "running", "trail", "trail", "trail"),
    (102, "running", "trail", "road", "road"),          # the climb rate said road: the classification decides
    (104, "hiking", "generic", None, "hike"), (104, "hiking", None, None, "hike"),
    (105, "mountaineering", None, None, "hike"), (105, "mountaineering", "generic", None, "hike"),
    (200, "cycling", "generic", None, "bike"), (200, "cycling", "road", None, "bike"),
    (201, "cycling", "generic", None, "bike"),
    (300, "swimming", "lap_swimming", None, "other"), (301, "swimming", "open_water", None, "other"),
    (402, "training", "strength_training", None, "strength"),
    (402, "training", "cardio_training", None, "strength"),
    (9904, "generic", None, None, "other"),             # unmapped code + a generic FIT
]


def _fw(code, sport, sub, classification, start=dt.datetime(2026, 9, 1, 7, 0), file="a.fit"):
    from backend.engine.wko5expr.fitdataset import TYPE_TAGS, sport_of
    group, stype = sport_of(sport, sub, classification)
    entry = SimpleNamespace(start=start, file=file)
    p = {"fit": (sport, sub)}
    if code is not None:
        p["coros"] = code
    return Workout(idx=0, entry=entry, day=date_to_day(start), sport=group, sport_type=stype,
                   tags=list(TYPE_TAGS.get(stype, [])), platform=p)


@pytest.mark.parametrize("code, sport, sub, cls, t", PRODUCTION_COMBOS)
def test_production_combos_map(code, sport, sub, cls, t):
    w = _fw(code, sport, sub, cls)
    assert SM.app_type(w) == t
    assert O.category(w) == t


def test_production_codes_all_mapped_but_the_custom_one():
    cov = SM.coverage(c for c, *_ in PRODUCTION_COMBOS)
    assert set(cov["unmapped"]) == {9904}
    assert set(cov["mapped"]) == {"road", "trail", "hike", "bike", "strength", "other"}


def test_platform_code_fills_a_fit_that_says_nothing():
    assert SM.app_type(_fw(104, "generic", None, None)) == "hike"
    assert SM.app_type(_fw(402, "generic", None, None)) == "strength"
    assert SM.app_type(_fw(None, "generic", None, None)) == "other"
    # a Garmin FIT (no COROS code) maps by its sport / sub_sport alone
    assert SM.app_type(_fw(None, "training", "strength_training", None)) == "strength"
    assert SM.app_type(_fw(None, "hiking", None, None)) == "hike"


@pytest.mark.parametrize("sport, sport_type, tags, cat", [
    ("run", "running", (), "road"),
    ("run", "treadmill running", ["runningtreadmill"], "road"),
    ("run", "trail running", ["runningtrail"], "trail"),
    ("walk", "hiking", ["hiking"], "hike"),
    ("other", "mountaineering", ["mountaineering"], "hike"),
    ("road bike", "cycling", ["cycling"], "bike"),
    ("strength", "strength", (), "strength"),
    ("other", "strength", (), "strength"),
    ("walk", "walking", ["walking"], "walk"),
    ("other", "table tennis", (), "other"),
    ("swim", "swimming", (), "other"),
])
def test_wko5_workouts_keep_their_categories(sport, sport_type, tags, cat):
    w = SimpleNamespace(sport=sport, sport_type=sport_type, tags=list(tags))      # no platform data
    assert SM.app_type(w) == cat == O.category(w)


# ---------------------------------------------------------------------------
# the filter kind: 百岳 and the user's mark
# ---------------------------------------------------------------------------

HIKE = dict(code=104, sport="hiking", sub=None, cls=None)


def _hike(**kw):
    a = {**HIKE, **kw}
    return _fw(a["code"], a["sport"], a["sub"], a["cls"])


def test_kind_baiyue_from_the_plan_event():
    assert SM.kind_of(_hike()) == "hike"
    assert SM.kind_of(_hike(), baiyue_event="玉山") == SM.BAIYUE
    assert SM.kind_of(_fw(102, "running", "trail", "trail"), baiyue_event="玉山") == "trail"   # a run stays a run


def test_user_mark_wins():
    def mark(t):
        return {"activity_type": t, "activity_type_overridden": True}
    assert SM.kind_of(_hike(), mark("hike"), baiyue_event="玉山") == "hike"        # 不是跟團
    assert SM.kind_of(_hike(), mark("baiyue_group")) == SM.BAIYUE                  # no plan event needed
    assert SM.kind_of(_fw(102, "running", "trail", "trail"), mark("hike")) == "hike"
    assert SM.kind_of(_fw(102, "running", "trail", "trail"), mark("baiyue_group")) == SM.BAIYUE
    assert SM.kind_of(_hike(), mark("race"), baiyue_event="玉山") == "hike"         # another mark: not 百岳
    # a stored row without an override is the auto value
    assert SM.kind_of(_hike(), {"activity_type": "hike", "activity_type_overridden": False},
                      baiyue_event="玉山") == SM.BAIYUE


def test_auto_type_uses_the_app_type():
    assert AT.auto_type(sport="other", sport_type="generic", app_type="hike", baiyue_event="玉山")[0] == "baiyue_group"
    assert AT.auto_type(sport="walk", sport_type="hiking", app_type="walk")[0] == "other"
    # without app_type: the old sport-type rule
    assert AT.auto_type(sport="walk", sport_type="hiking", baiyue_event="玉山")[0] == "baiyue_group"


def test_parse_and_make_filter():
    assert SM.make_filter(None) is None and SM.make_filter("") is None
    assert SM.make_filter(",".join(SM.FILTER_KINDS), tag_rows=[]) is None       # everything = no filter
    f = SM.make_filter("trail, BAIYUE", tag_rows=[])
    assert f.kinds == {"trail", "baiyue"} and not f.groups
    assert SM.make_filter("run", tag_rows=[]).groups == {"run"}
    assert SM.make_filter("trail,road", tag_rows=[]).sport_hint() == "run"
    assert SM.make_filter("trail,hike", tag_rows=[]).sport_hint() is None


# ---------------------------------------------------------------------------
# the endpoints: /workouts, /sports and a chart's Evaluator
# ---------------------------------------------------------------------------

DAY0 = dt.datetime(2026, 9, 1, 7, 0)


def _ds():
    rows = [  # (code, sport, sub, classification)
        (100, "running", None, "road"),             # 0 road
        (102, "running", "trail", "trail"),         # 1 trail
        (104, "hiking", None, None),                # 2 hike on the 百岳 trip day -> 百岳
        (105, "mountaineering", None, None),        # 3 hike, no event
        (402, "training", "strength_training", None),   # 4 strength
        (104, "hiking", None, None),                # 5 hike, the user marked 百岳跟團
        (200, "cycling", None, None),               # 6 bike
    ]
    ws = []
    for i, (code, sp, sub, cls) in enumerate(rows):
        w = _fw(code, sp, sub, cls, start=DAY0 + dt.timedelta(days=i), file=f"2026/{i}.fit")
        w.idx = i
        ws.append(w)
    ev = SimpleNamespace(kind="baiyue", start=dt.date(2026, 9, 3), days=1, name="雪山", priority="A")
    return SimpleNamespace(workouts=ws, today=date_to_day(dt.date(2026, 9, 30)), config=SimpleNamespace(parity=False),
                           plan=SimpleNamespace(events=[ev]), excluded=[])


@pytest.fixture
def api(tmp_path, monkeypatch):
    from backend.api import wko5views as V
    from backend.engine import plan_store
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    AT.upsert(db, start_local="2026-09-06T07:00", file="2026/5.fit", activity_type="baiyue_group")
    ds = _ds()
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    monkeypatch.setattr(plan_store, "done_by_index", lambda: {})
    return V, ds


def _files(lst):
    return sorted(a["file"] for a in lst)


def test_workouts_filter_by_kind(api):
    V, ds = api
    q = dict(begin="2026-09-01", end="2026-09-30", parity=None)
    allw = V.workouts(sports=None, **q)
    assert {a["file"]: a["kind"] for a in allw} == {
        "2026/0.fit": "road", "2026/1.fit": "trail", "2026/2.fit": "baiyue", "2026/3.fit": "hike",
        "2026/4.fit": "strength", "2026/5.fit": "baiyue", "2026/6.fit": "bike"}
    assert _files(V.workouts(sports="trail", **q)) == ["2026/1.fit"]
    assert _files(V.workouts(sports="baiyue", **q)) == ["2026/2.fit", "2026/5.fit"]
    assert _files(V.workouts(sports="hike", **q)) == ["2026/3.fit"]
    assert _files(V.workouts(sports="trail,baiyue", **q)) == ["2026/1.fit", "2026/2.fit", "2026/5.fit"]
    assert _files(V.workouts(sports="strength", **q)) == ["2026/4.fit"]
    # an older link with a WKO5 sport group still works
    assert _files(V.workouts(sports="run", **q)) == ["2026/0.fit", "2026/1.fit"]


def test_sports_list_counts_kinds_in_order(api):
    V, ds = api
    r = V.sports_list()
    assert [(x["sport"], x["count"]) for x in r] == [
        ("trail", 1), ("road", 1), ("hike", 1), ("baiyue", 2), ("bike", 1), ("strength", 1)]
    assert all(x["label"] for x in r)


def test_evaluator_keeps_only_the_filtered_activities(api):
    from backend.engine.wko5expr.evaluator import Evaluator
    V, ds = api
    ds.first_day = ds.last_day = int(ds.workouts[0].day)
    f = SM.make_filter("baiyue,trail", ds)
    ev = Evaluator(ds, ds.workouts[0].day, ds.today, keep=f)
    assert [w.entry.file for w in ev.wlist] == ["2026/1.fit", "2026/2.fit", "2026/5.fit"]
    assert ev.keep is f


def test_render_cache_key_follows_the_user_marks(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    AT.upsert(db, start_local="2026-09-06T07:00", note="x")
    a = SM.tags_stamp()
    AT.upsert(db, start_local="2026-09-06T07:00", activity_type="baiyue_group")
    assert SM.tags_stamp() != a


# ---------------------------------------------------------------------------
# the FIT dataset attaches the platform data
# ---------------------------------------------------------------------------

def test_fit_dataset_attaches_coros_code_and_fit_sport(tmp_path, monkeypatch):
    from backend.engine import planning
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.engine.wko5expr.fitdataset import FitFolderDataset, _norm
    from backend.tests.fit_builder import build_run
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    t0 = dt.datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
    (d / "0.fit").write_bytes(build_run(t0, seconds=900, sport=10, sub_sport=26))           # training / cardio
    (d / "1.fit").write_bytes(build_run(t0 + dt.timedelta(days=1), seconds=900, sport=1, sub_sport=3))
    r0 = {"id": 1, "file_path": str(d / "0.fit"), "trail_classification": "unknown",
          "classification_overridden": False, "duplicate_of": None, "coros_sport_type": 402}
    r1 = {"id": 2, "file_path": str(d / "1.fit"), "trail_classification": "trail",
          "classification_overridden": False, "duplicate_of": None, "coros_sport_type": 102}
    classes = {_norm(d / "0.fit"): r0, _norm(d / "1.fit"): r1, "_by_id": {1: r0, 2: r1},
               "_by_name": {"0.fit": [r0], "1.fit": [r1]}, "_dups": {}}
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=False),
                          today=dt.date(2026, 9, 30), corrections=CorrectionStore(tmp_path / "corr.json"),
                          classifications=classes, athlete_settings=[], estimate_thresholds=False,
                          tz=timezone.utc)
    by = {w.entry.file: w for w in ds.workouts}
    s, t = by["2026/0.fit"], by["2026/1.fit"]
    assert s.platform["coros"] == 402 and s.platform["fit"][0] == "training"
    assert SM.app_type(s) == "strength" and SM.app_type(t) == "trail"


def test_load_classifications_reads_the_coros_code(tmp_path):
    import sqlite3
    from backend.engine.wko5expr.fitdataset import load_classifications
    db = tmp_path / "a.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE workout_files (id INTEGER, file_path TEXT, trail_classification TEXT, "
                "classification_overridden INTEGER, duplicate_of INTEGER, coros_sport_type INTEGER)")
    con.execute("INSERT INTO workout_files VALUES (1, '/x/a.fit', 'road', 0, NULL, 100)")
    con.commit()
    con.close()
    assert load_classifications(db)["_by_id"][1]["coros_sport_type"] == 100
    # a DB from before the column: still loads, code None
    db2 = tmp_path / "b.db"
    con = sqlite3.connect(db2)
    con.execute("CREATE TABLE workout_files (id INTEGER, file_path TEXT, trail_classification TEXT, "
                "classification_overridden INTEGER, duplicate_of INTEGER)")
    con.execute("INSERT INTO workout_files VALUES (1, '/x/a.fit', 'road', 0, NULL)")
    con.commit()
    con.close()
    assert load_classifications(db2)["_by_id"][1]["coros_sport_type"] is None


# ---------------------------------------------------------------------------
# 百岳登山 from the 成就 page's GPS detection (owner 2026-10-06)
# ---------------------------------------------------------------------------

def test_kind_baiyue_from_a_gps_summit_user_mark_wins():
    def mark(t):
        return {"activity_type": t, "activity_type_overridden": True}
    assert SM.kind_of(_hike(), summit=True) == SM.BAIYUE
    assert SM.kind_of(_hike(), summit=False) == "hike"
    assert SM.kind_of(_fw(102, "running", "trail", "trail"), summit=True) == "trail"     # a run stays a run
    assert SM.kind_of(_hike(), mark("hike"), summit=True) == "hike"                     # the user's mark wins
    assert SM.kind_of(_hike(), mark("race"), summit=True) == "hike"


def test_baiyue_summits_reads_the_achievements_detection(tmp_path, monkeypatch):
    """The filter reuses achievements' own cached GPS summaries (no second detection)."""
    import json
    from backend.engine import achievements as ACH
    (tmp_path / "2026").mkdir()
    ws = []
    for i, (sport, peaks) in enumerate([("hiking", [{"name": "雪山主峰"}]), ("hiking", []),
                                        ("mountaineering", [{"name": "玉山主峰"}, {"name": "玉山北峰"}])]):
        f = tmp_path / f"2026/{i}.fit"
        f.write_bytes(b"x" * (i + 1))
        w = _fw(104, sport, None, None, file=f"2026/{i}.fit")
        w.idx = i
        ws.append(w)
    peaks = ACH.load_peaks()
    cache = {}
    for i, (_s, pk) in enumerate([(0, [{"name": "雪山主峰"}]), (1, []), (2, [{"name": "玉山主峰"}, {"name": "玉山北峰"}])]):
        st = (tmp_path / f"2026/{i}.fit").stat()
        cache[f"2026/{i}.fit"] = {"stamp": [st.st_size, int(st.st_mtime), ACH.ALGO_VERSION, len(peaks)],
                                  "summary": {"peaks": pk}}
    cp = tmp_path / "ach.json"
    cp.write_text(json.dumps(cache), "utf-8")
    monkeypatch.setattr(ACH, "CACHE_PATH", cp)
    ds = SimpleNamespace(dir=tmp_path, workouts=ws, wko4=lambda idx: pytest.fail("re-parsed a cached file"))
    assert ACH.baiyue_summits(ds) == {"2026/0.fit": ["雪山主峰"], "2026/2.fit": ["玉山主峰", "玉山北峰"]}
    assert ACH.baiyue_summits(SimpleNamespace(workouts=ws)) == {}                    # no files: nothing, no error


def test_workouts_and_counts_count_a_gps_summit_as_baiyue(api, monkeypatch):
    from backend.engine import achievements as ACH
    V, ds = api
    monkeypatch.setattr(ACH, "baiyue_summits", lambda ds, peaks=None: {"2026/3.fit": ["雪山主峰"], "2026/0.fit": ["x"]})
    q = dict(begin="2026-09-01", end="2026-09-30", parity=None)
    kinds = {a["file"]: a["kind"] for a in V.workouts(sports=None, **q)}
    assert kinds["2026/3.fit"] == "baiyue" and kinds["2026/0.fit"] == "road"           # a road run stays a road run
    assert _files(V.workouts(sports="baiyue", **q)) == ["2026/2.fit", "2026/3.fit", "2026/5.fit"]
    assert _files(V.workouts(sports="hike", **q)) == []
    assert dict((x["sport"], x["count"]) for x in V.sports_list())["baiyue"] == 3
