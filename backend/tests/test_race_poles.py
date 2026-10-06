"""SP-300: an activity matched to a race marked 「會用登山杖」 (planning.Event.poles, SP-244) shows
有杖 「依賽事設定」 until the user chooses; the user's 有杖 / 沒杖 / 未標 always wins, unticking the
race puts the un-chosen activities back to 未標, the 有杖 vs 沒杖 comparison (SP-243) counts the
race's 有杖, and still no model reads the mark (test_activity_poles.py's reader test). Synthetic
data only."""
import asyncio
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT
from backend.engine.panels import pole_compare as PC
from backend.engine.planning import Event, Plan
from backend.engine.wko5expr.dataset import date_to_day
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 9, 30)
WITH, WITHOUT, NONE = AT.POLES["with"], AT.POLES["without"], AT.POLE_NONE_TAG


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _w(day: dt.date, kind="trail", km=None, hour=7):
    sport, tags, st = {"hike": ("walk", ["hiking"], "hiking"), "trail": ("run", ["runningtrail"], "trail running"),
                       "road": ("run", ["running"], "running")}[kind]
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(hour)), sport=sport, tags=tags, sport_type=st,
                       metrics={"distance": km} if km is not None else {})


def _ds(acts, events):
    ds = FakeDataset(acts, TODAY)
    ds.plan = Plan(events=events)
    return ds


# ---- the user's choice, the hidden 未標 ------------------------------------------------

def test_the_users_own_unmarked_is_a_choice():
    assert AT.POLES == {"with": "有杖", "without": "沒杖"}               # SP-242 unchanged
    assert AT.pole_choice([]) is None and AT.pole_choice(["冬訓"]) is None
    assert AT.pole_choice([NONE]) == "none" and AT.poles_of([NONE]) is None      # not a 有杖 / 沒杖
    assert AT.pole_choice([WITH, NONE]) == "none" and AT.pole_choice([NONE, WITHOUT]) == "without"
    assert AT.with_poles(["a", WITH], "none") == ["a", NONE]
    assert AT.with_poles(["a", NONE], "with") == ["a", WITH]
    assert AT.with_poles(["a", NONE], None) == ["a"]                     # no choice at all
    assert AT.exclusive_poles(["a", WITH, NONE]) == ["a", NONE]
    assert AT.validate(poles="none") is None and AT.validate(poles="maybe") == "INVALID_POLES"


def test_pole_state_user_wins_else_the_race():
    assert AT.pole_state([], "山之賽") == {"poles": "with", "poles_user": None, "poles_race": "山之賽"}
    assert AT.pole_state([], None) == {"poles": None, "poles_user": None, "poles_race": None}
    for tags, want in (([WITH], "with"), ([WITHOUT], "without"), ([NONE], None)):
        st = AT.pole_state(tags, "山之賽")
        assert st["poles"] == want and st["poles_race"] is None and st["poles_user"] == AT.pole_choice(tags)


# ---- which activities a race covers ----------------------------------------------------

def test_one_day_race_uses_the_existing_match():
    d = dt.date(2026, 5, 10)
    acts = [_w(d, "trail", km=21.5), _w(d, "trail", km=5.0, hour=17),     # the race and a shake-out
            _w(d + dt.timedelta(days=1), "trail", km=21.0)]
    ev = Event(id="r1", name="山之賽", date=d.isoformat(), kind="race", distance_km=22.0, poles=True)
    ds = _ds(acts, [ev])
    assert AT.race_poles(ds) == {0: "山之賽"}                              # date + kind + distance
    # 「會用登山杖」 not ticked (or ticked off later): nothing
    assert AT.race_poles(_ds(acts, [Event(id="r1", name="山之賽", date=d.isoformat(), kind="race",
                                          distance_km=22.0)])) == {}
    # no matching run (way off the distance): nothing
    assert AT.race_poles(_ds([_w(d, "trail", km=8.0)], [ev])) == {}
    assert AT.race_poles(FakeDataset(acts, TODAY)) == {}                   # no plan at all


def test_multi_day_trip_covers_each_day():
    d = dt.date(2026, 7, 1)
    acts = [_w(d, "hike"), _w(d + dt.timedelta(days=1), "hike"), _w(d + dt.timedelta(days=2), "trail"),
            _w(d + dt.timedelta(days=1), "road", hour=18),                 # a road run: not a pole activity
            _w(d + dt.timedelta(days=3), "hike"), _w(d - dt.timedelta(days=1), "hike")]
    ev = Event(id="b1", name="南湖", date=d.isoformat(), kind="baiyue", days=3, poles=True)
    ds = _ds(acts, [ev])
    got = AT.race_poles(ds)
    days = sorted(ds.workouts[i].entry.start.date() for i in got)
    assert days == [d, d + dt.timedelta(days=1), d + dt.timedelta(days=2)] and set(got.values()) == {"南湖"}
    # a 2-day stage race (越野賽): both days, whatever each day's distance
    ev2 = Event(id="s1", name="分站賽", date=d.isoformat(), kind="race", days=2, distance_km=60.0, poles=True)
    got2 = AT.race_poles(_ds([_w(d, "trail", km=28.0), _w(d + dt.timedelta(days=1), "trail", km=31.0)], [ev2]))
    assert sorted(got2) == [0, 1]
    # a one-day 百岳 (單攻) is matched by its day too
    ev3 = Event(id="b2", name="單攻", date=d.isoformat(), kind="baiyue", days=1, poles=True)
    assert AT.race_poles(_ds([_w(d, "hike")], [ev3])) == {0: "單攻"}


# ---- the 有杖 vs 沒杖 comparison counts the race's 有杖 ---------------------------------------

def test_comparison_counts_the_race_default_and_the_user_wins():
    base = TODAY - dt.timedelta(days=60)
    hikes = [_w(base + dt.timedelta(days=k), "hike") for k in range(5)]   # a 5-day trip, ticked
    ev = Event(id="b1", name="大縱走", date=base.isoformat(), kind="baiyue", days=5, poles=True)
    solo = [_w(TODAY - dt.timedelta(days=10 + k), "hike") for k in range(5)]
    rows = [{"start_local": AT.key_of(w.start), "file": None, "tags": [WITHOUT]} for w in solo]
    c = PC.counts(_ds(hikes + solo, [ev]), rows, TODAY)
    assert (c["with"], c["without"], c["eligible"]) == (5, 5, True)
    # the user's 沒杖 on one trip day and their own 未標 on another: theirs wins
    rows2 = rows + [{"start_local": AT.key_of(hikes[0].start), "file": None, "tags": [WITHOUT]},
                    {"start_local": AT.key_of(hikes[1].start), "file": None, "tags": [NONE]}]
    c = PC.counts(_ds(hikes + solo, [ev]), rows2, TODAY)
    assert (c["with"], c["without"]) == (3, 6)
    # unticked: the trip's un-chosen days are 未標 again
    ev_off = Event(id="b1", name="大縱走", date=base.isoformat(), kind="baiyue", days=5)
    c = PC.counts(_ds(hikes + solo, [ev_off]), rows2, TODAY)
    assert (c["with"], c["without"]) == (0, 6)
    # no user mark at all but a ticked race: still counted (the gate does not need a stored mark)
    assert PC.counts(_ds(hikes, [ev]), [], TODAY)["with"] == 5
    # the panel reads the same marks
    res = PC.compute(_ds(hikes + solo, [ev]), 0, date_to_day(TODAY), {}, rows=rows, today=TODAY)
    assert res["counts"]["eligible"] and res["activities"] == {"with": 5, "without": 5}
    assert sum(1 for x in res["list"] if x["poles"] == "with") == 5


def test_list_views_needs_met_with_only_a_race(monkeypatch):
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import dataset as DS
    base = TODAY - dt.timedelta(days=60)
    hikes = [_w(base + dt.timedelta(days=k), "hike") for k in range(5)]
    solo = [_w(TODAY - dt.timedelta(days=10 + k), "hike") for k in range(5)]
    rows = [{"start_local": AT.key_of(w.start), "file": None, "tags": [WITHOUT]} for w in solo]
    plan = Plan(events=[Event(id="b1", name="大縱走", date=base.isoformat(), kind="baiyue", days=5, poles=True)])
    monkeypatch.setattr(AT, "load", lambda *a, **k: rows)
    monkeypatch.setattr(WV, "today_local", lambda *a, **k: TODAY)
    monkeypatch.setattr(DS, "tenant_plan", lambda: plan)
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: _ds(hikes + solo, plan.events))
    assert WV._needs_met() == {"poles": True}
    monkeypatch.setattr(AT, "load", lambda *a, **k: [])
    built = []
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: built.append(1) or _ds(hikes, plan.events))
    assert WV._needs_met() == {"poles": False} and built                  # a ticked race: looked up
    monkeypatch.setattr(DS, "tenant_plan", lambda: Plan())
    built.clear()
    assert WV._needs_met() == {"poles": False} and not built              # nothing at all: not waited for


# ---- the API: list, editor, user choice, untick -------------------------------------------

@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def test_api_race_default_user_choice_and_untick(tmp_path, no_plan, monkeypatch):
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    run = next(w for w in ds.workouts if w.entry.file == "2025/0.fit")      # 5.4 km, classified road
    on = Event(id="e1", name="河濱賽", date=run.entry.start.date().isoformat(), kind="road", distance_km=5.4,
               poles=True)
    ds.plan = Plan(events=[on])

    def listed():
        return {a["file"]: a for a in V.activities_list()["activities"]}

    a = listed()
    assert (a["2025/0.fit"]["poles"], a["2025/0.fit"]["poles_race"], a["2025/0.fit"]["poles_user"]) == \
        ("with", "河濱賽", None)
    assert a["2025/0.fit"]["tags"] == []                                   # shown, never stored
    assert a["2025/1.fit"]["poles"] is None and a["2025/1.fit"]["poles_race"] is None
    assert V.activities_list()["pole_none_tag"] == NONE

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        r = V.get_activity(run.idx)                                         # the editor's own GET
        assert r["poles"] == "with" and r["poles_race"] == "河濱賽"
        r = await V.patch_activity(run.idx, {"poles": "none"})              # the user's 未標 wins
        assert (r["poles"], r["poles_user"], r["poles_race"], r["tags"]) == (None, "none", None, [NONE])
        assert listed()["2025/0.fit"]["poles"] is None
        r = await V.patch_activity(run.idx, {"poles": "without"})
        assert (r["poles"], r["poles_race"]) == ("without", None)
        ds.plan = Plan(events=[Event(id="e1", name="河濱賽", date=on.date, kind="road", distance_km=5.4)])
        assert listed()["2025/0.fit"]["poles"] == "without"                # unticked: the user's stays
        r = await V.patch_activity(run.idx, {"poles": None})                # no choice any more
        assert (r["poles"], r["poles_race"], r["tags"]) == (None, None, [])   # unticked race: 未標
        ds.plan = Plan(events=[on])
        assert listed()["2025/0.fit"]["poles"] == "with"                   # ticked again: the default
        await eng.dispose()
    _run(_inner())


# ---- the page --------------------------------------------------------------------------

def test_editor_wiring_and_i18n():
    import json
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert 'const POLE_OPTS = ["with", "without", ""]' in page            # SP-242's three-way choice
    assert "S.poleNoneTag = r.pole_none_tag" in page and "a.poles_race != null" in page
    assert 'const want = b.dataset.poles || "none";' in page and "data-pole-race" in page
    assert 'T("poles.by_race")' in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text(encoding="utf-8"))
        assert cat["poles.by_race"] and "{name}" in cat["poles.by_race_why"], loc
    zh = json.loads((ROOT / "static" / "i18n" / "zh-TW" / "activity.json").read_text(encoding="utf-8"))
    assert zh["poles.by_race"] == "依賽事設定"
