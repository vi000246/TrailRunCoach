"""登山杖 mark of an activity (SP-242; docs/research/trekking-poles.md §5 #1):
有杖 / 沒杖 / 未標, the user's own choice stored as one of two free-form tags,
mutually exclusive, never detected, and read by NO model. tmp / in-memory
DBs only."""
import asyncio
import datetime as dt
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT

ROOT = Path(__file__).resolve().parents[1]
WITH, WITHOUT = AT.POLES["with"], AT.POLES["without"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- the tag helpers ------------------------------------------------------------

def test_poles_of_with_poles_and_exclusive():
    assert AT.POLES == {"with": "有杖", "without": "沒杖"}
    assert AT.poles_of([]) is None and AT.poles_of(["冬訓"]) is None
    assert AT.poles_of(["冬訓", WITH]) == "with" and AT.poles_of([WITHOUT]) == "without"
    assert AT.poles_of([WITH, WITHOUT]) == "without"                     # the last one wins
    assert AT.with_poles(["冬訓", WITH], "without") == ["冬訓", WITHOUT]
    assert AT.with_poles(["冬訓", WITH, WITHOUT], None) == ["冬訓"]      # 未標 removes either
    assert AT.with_poles([], "with") == [WITH]
    assert AT.exclusive_poles(["a", WITH, "b", WITHOUT]) == ["a", "b", WITHOUT]
    assert AT.exclusive_poles(["a", WITH]) == ["a", WITH]
    assert AT.validate(poles="with") is None and AT.validate(poles="maybe") == "INVALID_POLES"


# ---- store: save / load / mutual exclusion -------------------------------------------

def test_upsert_poles_save_load_switch_and_clear(tmp_path):
    db = tmp_path / "t.db"
    k = "2025-05-17T09:36"
    AT.upsert(db, start_local=k, tags=["百岳", "雨天"])
    assert AT.load(db)[0]["tags"] == ["百岳", "雨天"]                   # no mark = 未標
    assert AT.poles_of(AT.load(db)[0]["tags"]) is None
    AT.upsert(db, start_local=k, poles="with")
    r = AT.load(db)[0]
    assert r["tags"] == ["百岳", "雨天", WITH] and AT.poles_of(r["tags"]) == "with"
    AT.upsert(db, start_local=k, poles="without")                       # switch: never both
    r = AT.load(db)[0]
    assert r["tags"] == ["百岳", "雨天", WITHOUT]
    AT.upsert(db, start_local=k, note="x")                              # other edits keep the mark
    assert AT.poles_of(AT.load(db)[0]["tags"]) == "without"
    AT.upsert(db, start_local=k, tags=["百岳"])                          # a tag list without a pole tag: 未標
    assert AT.load(db)[0]["tags"] == ["百岳"]
    AT.upsert(db, start_local=k, tags=["百岳", WITH, WITHOUT])           # both sent: one kept
    assert AT.load(db)[0]["tags"] == ["百岳", WITHOUT]
    AT.upsert(db, start_local=k, tags=["百岳", WITHOUT], poles="with")   # poles applies after tags
    assert AT.load(db)[0]["tags"] == ["百岳", WITH]
    AT.upsert(db, start_local=k, poles=None)                            # 未標 removes it
    r = AT.load(db)[0]
    assert r["tags"] == ["百岳"] and r["note"] == "x"
    with pytest.raises(ValueError):
        AT.upsert(db, start_local=k, poles="both")


def test_merge_carries_the_mark_and_the_user_always_wins():
    auto = {"activity_type": "hike", "effort": "moderate"}
    assert AT.merge(auto, None)["poles"] is None                         # no auto rule: 未標
    m = AT.merge(auto, {"tags_json": f'["{WITH}"]', "start_local": "k"})
    assert m["poles"] == "with" and m["tags"] == [WITH]
    assert m["activity_type"] == "hike" and m["effort"] == "moderate"   # nothing else changes


# ---- the API: PATCH /workouts/{i}/activity, PATCH /activities, GET /activities ---------

@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def test_api_save_load_and_mutual_exclusion(tmp_path, no_plan, monkeypatch):
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import ActivityTag, Base
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    i = next(w.idx for w in ds.workouts if w.entry.file == "2025/0.fit")

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(D, "AsyncSessionLocal", maker)
        r = await V.patch_activity(i, {"tags": ["冬訓"], "effort": "easy"})
        assert r["poles"] is None and r["tags"] == ["冬訓"]                       # default 未標
        r = await V.patch_activity(i, {"poles": "with"})
        assert r["poles"] == "with" and r["tags"] == ["冬訓", WITH]
        assert r["effort"] == "easy" and r["effort_overridden"]                  # untouched
        r = await V.patch_activity(i, {"poles": "without"})
        assert r["poles"] == "without" and r["tags"] == ["冬訓", WITHOUT]
        lst = {a["file"]: a for a in V.activities_list()["activities"]}
        assert lst["2025/0.fit"]["poles"] == "without" and lst["2025/1.fit"]["poles"] is None
        assert V.activities_list()["pole_tags"] == AT.POLES
        r = await V.patch_activity(i, {"poles": None})
        assert r["poles"] is None and r["tags"] == ["冬訓"]
        with pytest.raises(HTTPException) as e:
            await V.patch_activity(i, {"poles": "both"})
        assert e.value.status_code == 400
        # key-based (an excluded file has no index): the same rule
        item = V.BulkItem(key="2025-12-14T01:00", file="2025/1.fit")
        await V.patch_activities(V.BulkBody(items=[item], add_tags=["車"], poles="with"))
        await V.patch_activities(V.BulkBody(items=[item], poles="without"))
        async with maker() as s:
            t = (await s.execute(select(ActivityTag).where(ActivityTag.start_local == "2025-12-14T01:00"))).scalar_one()
        assert AT.tags_of({"tags_json": t.tags_json}) == ["車", WITHOUT]
        with pytest.raises(HTTPException):
            await V.patch_activities(V.BulkBody(items=[item], poles="maybe"))
        await eng.dispose()
    _run(_inner())


def test_workout_files_activity_update_carries_poles():
    from backend.api.workouts import ActivityUpdate, update_activity
    from backend.tests.test_activity_tags import _session

    async def _inner():
        s = await _session()
        r = await update_activity(10, ActivityUpdate(poles="with"), s)
        assert r["poles"] == "with" and r["tags"] == [WITH]
        r = await update_activity(10, ActivityUpdate(tags=["a", WITH, WITHOUT]), s)
        assert r["poles"] == "without" and r["tags"] == ["a", WITHOUT]
        r = await update_activity(10, ActivityUpdate(poles=None), s)
        assert r["poles"] is None and r["tags"] == ["a"]
    _run(_inner())


# ---- no model reads it --------------------------------------------------------------

def test_the_mark_changes_no_capacity_sample(monkeypatch):
    """The race calculator / feasibility / the trail HR model learn from
    capacity_samples (racepower/athlete.py): its output is the same with 有杖,
    沒杖 or no mark — only the merged tag list itself differs."""
    from backend.engine.racepower import athlete as A
    from backend.tests.test_activity_tags import _fake_capacity_ds, _hist
    st = {"moving_s": 13900.0, "hr_avg": 152.0, "hist": _hist(152, 13900.0), "hist_lo": 40, "hr_s": 13900.0}
    base = {"start_local": "2025-04-12T10:32", "file": "2025/x.wko4"}

    def sample(row, rest):
        ds, w = _fake_capacity_ds(monkeypatch, st, {"elapsed_s": 14500.0, "rest_share": rest})
        c = A.capacity_samples(ds, [w], tags=[row], recorded=[])[0]
        tg = {k: v for k, v in c["tags"].items() if k not in ("tags", "poles")}
        return {**{k: v for k, v in c.items() if k != "tags"}, "tags": tg}

    for rest in (0.02, 0.14):                                       # auto 全力 / 有拼但有休息
        for user in ({}, {"effort": "max", "effort_overridden": True},
                     {"effort": "moderate", "effort_overridden": True, "activity_type": "hike",
                      "activity_type_overridden": True}):
            row = {**base, **user}
            plain = sample(row, rest)
            for tag in (WITH, WITHOUT):
                assert sample({**row, "tags_json": f'["{tag}"]'}, rest) == plain, (rest, user, tag)


def test_no_model_module_reads_the_pole_mark():
    """Only activity_tags.py knows the pole tags: no prediction model (the
    calculator, the HR model, the downhill bump, …) reads them."""
    pat = re.compile(r"poles_of|with_poles|exclusive_poles|\bPOLES\b|有杖|沒杖")
    hits = []
    for p in sorted((ROOT / "engine").rglob("*.py")):
        if p.name == "activity_tags.py":
            continue
        if pat.search(p.read_text(encoding="utf-8")):
            hits.append(str(p.relative_to(ROOT)))
    assert not hits, hits


# ---- the page ------------------------------------------------------------------

def test_editor_has_the_three_way_choice_and_i18n():
    import json
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert 'data-poles="${v}"' in page and 'const POLE_OPTS = ["with", "without", ""]' in page
    assert "!isPoleTag(t)" in page                                    # not shown as a free tag chip
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "activity.json").read_text(encoding="utf-8"))
        for k in ("f_poles", "help.poles", "poles.with", "poles.without", "poles.none"):
            assert cat.get(k), (loc, k)
    zh = json.loads((ROOT / "static" / "i18n" / "zh-TW" / "activity.json").read_text(encoding="utf-8"))
    assert (zh["poles.with"], zh["poles.without"], zh["poles.none"]) == ("有杖", "沒杖", "未標")
    # the field sits with the activity type and effort
    assert page.index('T("f_effort")') < page.index('T("f_poles")') < page.index('T("f_tags")')
