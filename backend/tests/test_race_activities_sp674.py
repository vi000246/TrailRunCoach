"""SP-674: which activity was a race — a finished plan event matched to its activity (date, distance
or climbing; a multi-day event by its days), the user's own mark / link on the activity page, and
one query (race_activities) the readiness / feasibility / back-test can read. Fake datasets and
tmp DBs only."""
import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import activity_tags as AT
from backend.engine import race_activities as RA
from backend.engine.planning import Event


def _w(idx, start, km, gain=None, trail=True, sport="run", sport_type=None, title="", file=None):
    st = sport_type or ("trail running" if trail else "running")
    return SimpleNamespace(idx=idx, sport=sport, sport_type=st, tags=["runningtrail"] if trail else [],
                           metrics={"distance": km, "climbing": gain},
                           entry=SimpleNamespace(start=start, file=file or f"f{idx}.fit", title=title))


def _ds(ws, events):
    return SimpleNamespace(workouts=ws, plan=SimpleNamespace(events=events))


TODAY = dt.date(2026, 10, 16)
D = dt.datetime


def test_finished_event_matches_the_run_that_day_by_distance():
    ev = Event(id="e1", name="北橫 30K", date="2026-09-20", kind="race", priority="B", distance_km=30,
               climbing_m=1800)
    ws = [_w(0, D(2026, 9, 20, 6, 0), 5.0, 100), _w(1, D(2026, 9, 20, 7, 0), 31.2, 1750),
          _w(2, D(2026, 9, 21, 7, 0), 30.0, 1800)]
    r = RA.results(_ds(ws, [ev]), TODAY, tags=[])["e1"]
    assert r["status"] == "matched"
    assert [a["idx"] for a in r["activities"]] == [1]
    assert r["activities"][0]["how"] == "auto"
    assert r["why"]


def test_climbing_alone_can_match_a_shortened_course():
    ev = Event(id="e1", name="改道", date="2026-09-20", kind="race", distance_km=40, climbing_m=2000)
    ws = [_w(0, D(2026, 9, 20, 7, 0), 26.0, 1950)]           # km −35 %, climb −2.5 %
    assert RA.results(_ds(ws, [ev]), TODAY, tags=[])["e1"]["status"] == "matched"


def test_unmatched_event_says_why():
    ev = Event(id="e1", name="A", date="2026-09-20", kind="race", distance_km=50, climbing_m=3000)
    none = RA.results(_ds([], [ev]), TODAY, tags=[])["e1"]
    assert none["status"] == "unmatched" and "2026-09-20" in none["why"]
    far = RA.results(_ds([_w(0, D(2026, 9, 20, 7), 10.0, 300)], [ev]), TODAY, tags=[])["e1"]
    assert far["status"] == "unmatched" and "10.0" in far["why"]


def test_upcoming_event_is_not_matched():
    ev = Event(id="e1", name="A", date="2026-11-20", kind="race", distance_km=50)
    assert RA.results(_ds([_w(0, D(2026, 11, 20, 7), 50.0)], [ev]), TODAY, tags=[])["e1"]["status"] == "upcoming"


def test_stage_race_matches_each_day_and_continuous_any_day():
    ev = Event(id="s", name="三日賽", date="2026-09-01", days=3, kind="race", race_format="stage",
               day_plan=[{"km": 20, "gain_m": 1000}, {"km": 30, "gain_m": 1500}, {"km": 25, "gain_m": 1200}],
               distance_km=75, climbing_m=3700)
    ws = [_w(0, D(2026, 9, 1, 7), 20.5, 990), _w(1, D(2026, 9, 2, 7), 29.0, 1480)]
    r = RA.results(_ds(ws, [ev]), TODAY, tags=[])["s"]
    assert r["status"] == "partial" and [a["idx"] for a in r["activities"]] == [0, 1]
    assert "2026-09-03" in r["why"]
    cont = Event(id="c", name="連續", date="2026-09-01", days=2, kind="race", race_format="continuous",
                 distance_km=100, climbing_m=6000)
    r = RA.results(_ds([_w(0, D(2026, 9, 1, 22), 98.0, 5900)], [cont]), TODAY, tags=[])["c"]
    assert r["status"] == "matched"


def test_user_link_wins_and_user_not_race_drops_the_auto_pick():
    ev = Event(id="e1", name="A", date="2026-09-20", kind="race", distance_km=30)
    ws = [_w(0, D(2026, 9, 20, 7), 30.0), _w(1, D(2026, 9, 19, 7), 12.0)]
    link = [{"start_local": "2026-09-19T07:00", "file": "f1.fit", "activity_type": "race",
             "activity_type_overridden": True, "race_event": "e1"}]
    r = RA.results(_ds(ws, [ev]), TODAY, tags=link)["e1"]
    assert [a["idx"] for a in r["activities"]] == [1] and r["activities"][0]["how"] == "manual"
    notrace = [{"start_local": "2026-09-20T07:00", "file": "f0.fit", "activity_type": "training",
                "activity_type_overridden": True}]
    r = RA.results(_ds(ws, [ev]), TODAY, tags=notrace)["e1"]
    assert r["status"] == "unmatched" and "不是比賽" in r["why"]


def test_race_activities_combines_plan_manual_and_title():
    ev = Event(id="e1", name="北橫", date="2026-09-20", kind="race", priority="B", distance_km=30)
    trip = Event(id="t", name="雪山", date="2026-08-01", kind="baiyue", priority="A", distance_km=20)
    ws = [_w(0, D(2026, 9, 20, 7), 30.0), _w(1, D(2026, 8, 10, 7), 21.0),
          _w(2, D(2026, 7, 5, 7), 10.0, title="大屯山越野賽"), _w(3, D(2026, 8, 1, 7), 20.0, sport="hike",
                                                                sport_type="hiking", trail=False),
          _w(4, D(2026, 6, 1, 7), 10.0)]
    mark = [{"start_local": "2026-08-10T07:00", "file": "f1.fit", "activity_type": "race",
             "activity_type_overridden": True}]
    out = RA.race_activities(_ds(ws, [ev, trip]), dt.date(2026, 6, 1), dt.date(2026, 10, 1), tags=mark,
                             today=TODAY)
    by = {a["idx"]: a for a in out}
    assert set(by) == {0, 1, 2}                       # the 百岳 trip is not a race; idx 4 is training
    assert by[0]["source"] == "plan" and by[0]["event"]["id"] == "e1" and by[0]["priority"] == "B"
    assert by[1]["source"] == "manual" and by[1]["event"] is None
    assert by[2]["source"] == "title"
    assert [a["date"] for a in out] == sorted(a["date"] for a in out)
    # the window
    assert {a["idx"] for a in RA.race_activities(_ds(ws, [ev, trip]), dt.date(2026, 9, 1), dt.date(2026, 9, 30),
                                                 tags=mark, today=TODAY)} == {0}


# ---- the store: activity_tags.race_event --------------------------------------------------

def test_race_event_is_stored_and_implies_race(tmp_path):
    db = tmp_path / "t.db"
    AT.upsert(db, start_local="2026-09-20T07:00", race_event="e1")
    r = AT.load(db)[0]
    assert r["race_event"] == "e1" and r["activity_type"] == "race" and r["activity_type_overridden"]
    AT.upsert(db, start_local="2026-09-20T07:00", activity_type="training")   # not a race: the link goes
    r = AT.load(db)[0]
    assert r["race_event"] is None and r["activity_type"] == "training"
    AT.upsert(db, start_local="2026-09-20T07:00", race_event="e1")
    AT.upsert(db, start_local="2026-09-20T07:00", race_event=None)           # unlink keeps the race mark
    r = AT.load(db)[0]
    assert r["race_event"] is None and r["activity_type"] == "race"
    with pytest.raises(ValueError):
        AT.upsert(db, start_local="2026-09-20T07:00", race_event="x" * 100)


def test_migration_lists_the_new_column():
    from backend.db import database
    import inspect
    assert "race_event" in AT.LATE_COLS and '"race_event"' in inspect.getsource(database)


def test_patch_body_accepts_race_event():
    from backend.api.workouts import ActivityUpdate, STORED_FIELDS
    assert "race_event" in STORED_FIELDS
    assert ActivityUpdate(race_event="e1").race_event == "e1"


# ---- the API ----------------------------------------------------------------------------------

def test_race_activities_api_gives_the_plan_results_and_the_window(monkeypatch):
    from backend.api import wko5views as V
    ev = Event(id="e1", name="北橫", date="2026-09-20", kind="race", priority="B", distance_km=30)
    ds = _ds([_w(0, D(2026, 9, 20, 7), 30.0)], [ev])
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    monkeypatch.setattr(AT, "load", lambda *a, **k: [])
    monkeypatch.setattr(V, "today_local", lambda *a, **k: TODAY)
    r = V.race_activities_api(since="2026-09-01", until="2026-09-30")
    assert r["results"]["e1"]["status"] == "matched"
    assert [a["idx"] for a in r["activities"]] == [0]
    with pytest.raises(Exception):
        V.race_activities_api(since="bad", until=None)


def test_activities_list_carries_the_link_the_auto_match_and_the_events(tmp_path, monkeypatch):
    from backend.api import wko5views as V
    from backend.engine import planning
    from backend.tests.test_activity_edit import _fit_ds, T0
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    day = T0.date().isoformat()
    plan = planning.Plan(events=[Event(id="e1", name="河濱賽", date=day, kind="road", priority="C", distance_km=5.4)])
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: plan))
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    r = V.activities_list()
    run = next(a for a in r["activities"] if a["file"] == "2025/0.fit")
    assert run["race_event_auto"]["id"] == "e1" and run["race_event"] is None
    assert [e["id"] for e in r["events"]] == ["e1"]
    AT.upsert(db, start_local=run["key"], file="2025/0.fit", race_event="e1")
    run = next(a for a in V.activities_list()["activities"] if a["file"] == "2025/0.fit")
    assert run["race_event"] == "e1" and run["user_type"] == "race"
