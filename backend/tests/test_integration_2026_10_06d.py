"""Integration 2026-10-06d: cross-branch checks.

- SP-243 (有杖 vs 沒杖 comparison, pole_counts) and SP-250 (路況 乾 / 濕 / 未標):
  both marks live in the same free-form tag list of one activity, stay
  independent of each other, both come back from GET /activities, and neither
  feeds a model except the allowed readers (the pole_compare panel; the
  surface split in grade_model, only with ≥ 30 windows in each group).
- SP-232 (uvicorn access-log masking) leaves the ordinary requests of the
  pages SP-243 / SP-250 touched readable (the viewer's chart endpoint, the
  activity list / editor, the calculator's 路況), while a share link is still
  masked.
- A DB from before these merges (pole marks stored, an activity_tags table
  without the late columns, a pre-SP-231 workout_files) upgrades on start-up
  and takes a 路況 mark next to its pole mark.

tmp / in-memory DBs and synthetic numbers only.
"""
import asyncio
import datetime as dt
import logging
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import quote

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT

ROOT = Path(__file__).resolve().parents[1]
WITH, WITHOUT = AT.POLES["with"], AT.POLES["without"]
DRY, WET = AT.SURFACES["dry"], AT.SURFACES["wet"]
TODAY = dt.date(2026, 10, 6)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- SP-243 × SP-250: one activity, two marks ------------------------------------

def test_the_two_marks_are_distinct_values():
    assert not set(AT.POLES.values()) & set(AT.SURFACES.values())
    assert AT.validate(poles="wet") == "INVALID_POLES" and AT.validate(surface="with") == "INVALID_SURFACE"
    assert AT.poles_of([DRY, WET]) is None and AT.surface_of([WITH, WITHOUT]) is None


def test_pole_and_surface_marks_coexist_and_do_not_touch_each_other(tmp_path):
    db = tmp_path / "t.db"
    k = "2026-09-20T07:00"
    AT.upsert(db, start_local=k, tags=["冬訓"], poles="with")
    AT.upsert(db, start_local=k, surface="wet")
    assert AT.load(db)[0]["tags"] == ["冬訓", WITH, WET]
    AT.upsert(db, start_local=k, poles="without")                       # pole switch keeps 濕
    assert AT.load(db)[0]["tags"] == ["冬訓", WET, WITHOUT]
    AT.upsert(db, start_local=k, surface="dry")                         # surface switch keeps 沒杖
    r = AT.load(db)[0]
    assert AT.poles_of(r["tags"]) == "without" and AT.surface_of(r["tags"]) == "dry"
    AT.upsert(db, start_local=k, poles=None)                            # 未標 pole: 乾 stays
    r = AT.load(db)[0]
    assert AT.poles_of(r["tags"]) is None and AT.surface_of(r["tags"]) == "dry"
    AT.upsert(db, start_local=k, poles="with", surface=None)            # both in one write
    r = AT.load(db)[0]
    assert r["tags"] == ["冬訓", WITH]
    # a full tag list with duplicates of both marks: one of each kept (the last)
    AT.upsert(db, start_local=k, tags=[WITH, DRY, "x", WITHOUT, WET])
    r = AT.load(db)[0]
    assert AT.poles_of(r["tags"]) == "without" and AT.surface_of(r["tags"]) == "wet"
    assert sum(t in AT.POLES.values() for t in r["tags"]) == 1
    assert sum(t in AT.SURFACES.values() for t in r["tags"]) == 1
    m = AT.merge({"activity_type": "training", "effort": "easy"}, r)
    assert m["poles"] == "without" and m["surface"] == "wet"


def _rows(n_with, n_without, surface=None, day0=TODAY):
    out = []
    for i, p in enumerate(["with"] * n_with + ["without"] * n_without):
        tags = [AT.POLES[p]] + ([AT.SURFACES[surface]] if surface else [])
        out.append({"start_local": f"{(day0 - dt.timedelta(days=i)).isoformat()}T07:00",
                    "file": f"2026/{i}.fit", "tags": tags})
    return out


def test_pole_counts_and_the_chart_stamp_ignore_the_surface_mark():
    plain, wet = _rows(5, 5), _rows(5, 5, surface="wet")
    assert AT.pole_counts(plain, TODAY) == AT.pole_counts(wet, TODAY)
    assert AT.pole_counts(wet, TODAY)["eligible"]
    # the 有杖 vs 沒杖 render-cache input does not change when only 路況 changes
    assert AT.pole_marks_stamp(plain) == AT.pole_marks_stamp(wet)
    only_surface = [{**r, "tags": [WET]} for r in plain]
    assert AT.pole_counts(only_surface, TODAY)["with"] == 0 and AT.pole_marks_stamp(only_surface) == []


def test_surface_marks_ignore_the_pole_mark():
    from backend.engine.racepower import athlete as A
    t0 = dt.datetime(2026, 9, 1, 7, 0)
    runs = [NS(idx=i, entry=NS(start=t0 + dt.timedelta(days=i), file=f"2026/{i}.fit")) for i in range(4)]
    tag = lambda i, tags: {"start_local": AT.key_of(t0 + dt.timedelta(days=i)), "file": f"2026/{i}.fit",   # noqa: E731
                           "tags_json": __import__("json").dumps(tags, ensure_ascii=False)}
    tags = [tag(0, [WITH]), tag(1, [WITHOUT, WET]), tag(2, [DRY, WITH]), tag(3, ["雨天"])]
    assert A.surface_marks(None, runs, tags) == {1: "wet", 2: "dry"}
    assert A.surface_marks(None, runs, [tag(i, [WITH]) for i in range(4)]) == {}


def test_a_pole_only_marked_history_gives_no_surface_split():
    """The grade model's surface split reads the 路況 marks only, and only with ≥ 30
    windows in each group: pole marks never make one."""
    from backend.engine.racepower import grade_model as GM
    prior = GM.re_prior(-0.05, 1.0)
    samples = [{"g": -0.05, "re": prior * (0.8 if a == 2 else 1.0), "v": 3.0, "a": a, "run": 1.0, "trail": True}
               for a in (1, 2) for _ in range(40)]
    assert not GM.fit_gait_re(samples, 1.0, surfaces={}).surface_split           # marked only 有杖 / 沒杖
    assert GM.fit_gait_re(samples, 1.0, surfaces={1: "dry", 2: "wet"}).surface_split
    few = [s for s in samples if s["a"] == 1] + [s for s in samples if s["a"] == 2][:GM.SURFACE_MIN_N - 1]
    assert not GM.fit_gait_re(few, 1.0, surfaces={1: "dry", 2: "wet"}).surface_split


POLE_PAT = re.compile(r"poles_of|with_poles|exclusive_poles|pole_counts|pole_marks_stamp|\bPOLES\b|有杖|沒杖")
SURFACE_PAT = re.compile(r"surface_of|with_surface|exclusive_surface|surface_marks|\bSURFACES\b|乾路|濕路")
POLE_READERS = {"engine/activity_tags.py", "engine/panels/pole_compare.py"}
# activity_tags: the store; athlete.surface_marks: the one reader of the stored marks; grade_model: the
# ≥ 30-window split; backtest: the gate that keeps or drops the split
SURFACE_READERS = {"engine/activity_tags.py", "engine/racepower/athlete.py",
                   "engine/racepower/grade_model.py", "engine/racepower/backtest.py"}


def _readers(pat):
    return {p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "engine").rglob("*.py"))
            if pat.search(p.read_text(encoding="utf-8"))}


def test_only_the_allowed_engine_modules_read_either_mark():
    assert _readers(POLE_PAT) <= POLE_READERS, _readers(POLE_PAT) - POLE_READERS
    assert _readers(SURFACE_PAT) <= SURFACE_READERS, _readers(SURFACE_PAT) - SURFACE_READERS
    # neither side reads the other's mark
    pc = (ROOT / "engine" / "panels" / "pole_compare.py").read_text(encoding="utf-8")
    assert not SURFACE_PAT.search(pc)
    for f in ("athlete.py", "grade_model.py", "backtest.py"):
        assert not POLE_PAT.search((ROOT / "engine" / "racepower" / f).read_text(encoding="utf-8")), f


@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def test_activities_list_returns_both_marks_and_the_pole_counts(tmp_path, no_plan, monkeypatch):
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    i = next(w.idx for w in ds.workouts if w.entry.file == "2025/0.fit")
    start = next(w.entry.start for w in ds.workouts if w.entry.file == "2025/0.fit")
    monkeypatch.setattr(V, "today_local", lambda: start.date())

    async def _inner():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        r = await V.patch_activity(i, {"poles": "with"})
        r = await V.patch_activity(i, {"surface": "wet"})
        assert r["poles"] == "with" and r["surface"] == "wet" and r["tags"] == [WITH, WET]
        lst = V.activities_list()
        a = {x["file"]: x for x in lst["activities"]}["2025/0.fit"]
        assert a["poles"] == "with" and a["surface"] == "wet" and a["tags"] == [WITH, WET]
        assert lst["pole_tags"] == AT.POLES and lst["surface_tags"] == AT.SURFACES
        assert lst["pole_compare"]["with"] == 1 and lst["pole_compare"]["without"] == 0
        r = await V.patch_activity(i, {"surface": None})                 # clearing 路況 keeps 有杖
        assert r["poles"] == "with" and r["surface"] is None
        lst = V.activities_list()
        assert lst["pole_compare"]["with"] == 1
        await eng.dispose()
    _run(_inner())


def test_activity_page_wires_both_marks():
    page = (ROOT / "static" / "activity.html").read_text(encoding="utf-8")
    assert "surfaceTags: {}" in page and "poleCompare: null" in page
    assert "S.poleCompare = r.pole_compare" in page and "S.surfaceTags = r.surface_tags" in page
    # the pole hint stays under the 登山杖 choice, the 路況 row comes after it, before the tags
    assert page.index("data-pole-hint") < page.index('T("f_surface")') < page.index('T("f_tags")')
    assert "!isMarkTag(t)" in page                       # neither mark is shown as a free tag chip


# ---- SP-232 × SP-243 / SP-250: the access log -------------------------------------

def _access(path_q: str) -> str:
    from backend.applog import UvicornRedactFilter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("172.17.0.1:5555", "GET", path_q, "1.1", 200), None)
    assert UvicornRedactFilter().filter(rec)
    return rec.getMessage()


ORDINARY = [
    # the viewer's chart endpoint (SP-243's 有杖 vs 沒杖 is one of these charts), uvicorn quotes the path
    quote("/api/v1/wko5/views/我的訓練/dashboards/3/charts/9")
    + "?begin=2025-10-07&end=2026-10-06&sports=&parity=false&period=week&window=90&basis=pace",
    quote("/api/v1/wko5/views/我的訓練/dashboards/3/charts/9")
    + "?begin=2025-10-07&end=2026-10-06&sports=Run%2CTrail%20Run%2CHiking&parity=true",
    "/api/v1/wko5/views?parity=false",
    "/api/v1/wko5/activities",
    "/api/v1/wko5/workouts/1234/activity",
    "/api/v1/wko5/viewer?view=%E6%88%91%E7%9A%84%E8%A8%93%E7%B7%B4&dash=3&chart=9",
    "/static/pole_compare.js",
    "/static/i18n/en/activity.json",
]


@pytest.mark.parametrize("path_q", ORDINARY)
def test_masking_keeps_the_ordinary_requests_readable(path_q):
    from backend.applog import mask_url
    assert mask_url(path_q) == path_q
    assert path_q in _access(path_q)


def test_masking_still_hides_a_share_key_next_to_them():
    msg = _access("/share/calendar/a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6.ics")
    assert "a1B2c3D4" not in msg and "/share/calendar/***.ics" in msg


# ---- an existing DB ---------------------------------------------------------------

def test_an_existing_db_with_pole_marks_upgrades_and_takes_a_surface_mark(tmp_path, monkeypatch):
    import backend.db.database as db_mod
    from backend.db.models import Base

    path = tmp_path / "old.db"
    k = f"{TODAY.isoformat()}T07:00"

    async def make_old():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # an older schema: no SP-231 rating columns, an activity_tags table without the pain columns
            await conn.execute(text("ALTER TABLE workout_files DROP COLUMN coros_feel"))
            await conn.execute(text("ALTER TABLE workout_files DROP COLUMN rpe_source"))
            await conn.execute(text("DROP INDEX IF EXISTS ix_activity_tags_injury_id"))
            for col in ("pain", "pain_area", "injury_id"):
                await conn.execute(text(f"ALTER TABLE activity_tags DROP COLUMN {col}"))
            await conn.execute(text("INSERT INTO athletes (id, name, data_dir, created_at) "
                                    "VALUES (1, 'me', 'x', '2026-01-01')"))
            await conn.execute(text("INSERT INTO activity_tags (athlete_id, start_local, file, activity_type_overridden, "
                                    "effort_overridden, tags_json, updated_at) VALUES (1, :k, '2026/0.fit', 0, 0, :t, "
                                    "'2026-10-01 00:00:00')"),
                               {"k": k, "t": f'["冬訓", "{WITH}"]'})
        await eng.dispose()

    _run(make_old())
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()                                           # idempotent, as every start-up
        await db_mod.dispose(path)

    _run(go())
    con = sqlite3.connect(path)
    wf = {r[1] for r in con.execute("PRAGMA table_info(workout_files)")}
    tg = {r[1] for r in con.execute("PRAGMA table_info(activity_tags)")}
    con.close()
    assert {"coros_feel", "rpe_source"} <= wf and set(AT.LATE_COLS) <= tg
    r = AT.load(path)[0]
    assert r["tags"] == ["冬訓", WITH]
    assert AT.pole_counts(AT.load(path), TODAY)["with"] == 1
    AT.upsert(path, start_local=k, surface="wet")
    r = AT.load(path)[0]
    assert r["tags"] == ["冬訓", WITH, WET] and AT.poles_of(r["tags"]) == "with" and AT.surface_of(r["tags"]) == "wet"
