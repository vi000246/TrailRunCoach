"""
SP-271 疼痛燈號 (engine/injuries.light / event_light / foot_log, overview.light_apply, week_plan, the
suggestion box, activity_tags.pain_score + its migration, the injuries list): the last marked run of an
open injury gives green / yellow / red — the 0–10 against the condition's limit (跟腱 ≤ 5, 膝前痛 ≤ 2,
others: not above the last one), a rise of ≥ 2, 中斷 or ≥ 7 /10, two yellows in a row, severity 重. Yellow
cuts the rest of the week (≤ last week, no interval, long × 0.75), red takes the runs out (strength
stays). No open injury / an illness: nothing changes. Synthetic data and tmp SQLite only
(docs/research/injury-graded-return.md §4.6, §6.1 points 6–8).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import asdict
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.engine import activity_tags as AT
from backend.engine import injuries as INJ
from backend.engine import overview as O
from backend.i18n import use_locale
from backend.tests.test_injuries import _run, _tmp_session, ev
from backend.tests.test_quality_gate import TODAY                      # Wed 2026-09-30

D = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    INJ._memo.clear()
    yield
    INJ._memo.clear()


def cev(id, onset, condition, area=None, **kw):
    return {**ev(id, onset, area=area or INJ.CONDITION_AREA.get(condition) or "hip", **kw), "condition": condition}


def mk(day, pain, score=None, eid=1, area=None, cat="run"):
    return {"date": day, "key": day + "T07:00", "cat": cat, "minutes": 40.0, "pain": pain, "score": score,
            "area": area, "injury_id": eid}


# ---------------------------------------------------------------------------
# the light
# ---------------------------------------------------------------------------

def test_knee_3_of_10_is_yellow_with_the_reason():
    e = [cev(1, "2026-09-20", "pfp")]
    lt = INJ.light(e, [mk("2026-09-27", 2, 3)], D)
    assert lt["color"] == "yellow" and lt["reason"] == "上一次 3/10，超過膝前痛的 2/10"
    assert INJ.light(e, [mk("2026-09-27", 1, 2)], D)["color"] == "green"


def test_achilles_4_after_3_is_green():
    e = [cev(1, "2026-09-20", "achilles")]
    assert INJ.light(e, [mk("2026-09-25", 2, 3), mk("2026-09-28", 2, 4)], D)["color"] == "green"
    assert INJ.light(e, [mk("2026-09-25", 2, 3), mk("2026-09-28", 2, 6)], D)["color"] == "yellow"   # over 5
    lt = INJ.light(e, [mk("2026-09-25", 1, 1), mk("2026-09-28", 2, 3)], D)                           # +2
    assert lt["color"] == "yellow" and "高 2 分" in lt["reason"]


def test_other_condition_compares_with_the_last_score():
    e = [cev(1, "2026-09-20", "itb")]
    assert INJ.light(e, [mk("2026-09-25", 2, 4), mk("2026-09-28", 2, 4)], D)["color"] == "green"
    assert INJ.light(e, [mk("2026-09-25", 1, 4), mk("2026-09-28", 2, 5)], D)["color"] == "yellow"
    # no limit and no earlier score: the tap decides (痛 = changes the stride → yellow)
    assert INJ.light(e, [mk("2026-09-25", 2, 4)], D)["color"] == "yellow"
    assert INJ.light(e, [mk("2026-09-25", 1, 4)], D)["color"] == "green"


def test_tap_only():
    e = [ev(1, "2026-09-20")]
    assert INJ.light(e, [mk("2026-09-28", 0)], D)["color"] == "green"
    assert INJ.light(e, [mk("2026-09-28", 1)], D)["color"] == "green"
    assert INJ.light(e, [mk("2026-09-28", 2)], D)["color"] == "yellow"
    assert INJ.light(e, [mk("2026-09-28", 3)], D)["color"] == "red"
    assert INJ.light(e, [mk("2026-09-28", 1, 7)], D)["color"] == "red"                      # ≥ 7/10
    assert INJ.light(e, [], D)["color"] == "green"                                         # no run yet


def test_two_yellows_make_red_and_the_next_mark_changes_it():
    e = [ev(1, "2026-09-20")]
    lt = INJ.light(e, [mk("2026-09-25", 2), mk("2026-09-27", 2)], D)
    assert lt["color"] == "red" and "連兩次黃燈" in lt["reason"]
    assert INJ.light(e, [mk("2026-09-25", 2), mk("2026-09-27", 2), mk("2026-09-29", 1)], D)["color"] == "green"
    # an unmarked run changes nothing; a walk is not a run
    assert INJ.light(e, [mk("2026-09-25", 2), mk("2026-09-27", None), mk("2026-09-28", 1, cat="walk"),
                         mk("2026-09-29", 2)], D)["color"] == "red"


def test_severe_is_red_illness_and_no_injury_none():
    assert INJ.light([ev(1, "2026-09-20", severity="severe")], [], D)["color"] == "red"
    assert INJ.light([], [mk("2026-09-28", 3)], D) is None
    ill = {**ev(1, "2026-09-20"), "category": "illness", "illness": "cold"}
    assert INJ.light([ill], [mk("2026-09-28", 3)], D) is None
    assert INJ.light([ev(1, "2026-09-01", status="resolved", resolved="2026-09-20")], [mk("2026-09-28", 3)], D) is None


def test_marks_of_another_event_or_area_do_not_count():
    e = [ev(1, "2026-09-20", area="knee"), ev(2, "2026-09-20", area="ankle")]
    lt = INJ.light(e, [mk("2026-09-28", 3, eid=2)], D)
    assert lt["color"] == "red" and lt["id"] == 2                                            # the worst
    one = INJ.event_light(e[0], [mk("2026-09-28", 3, eid=2), mk("2026-09-27", 2, eid=None, area="ankle")], D)
    assert one["color"] == "green"


def test_english():
    with use_locale("en"):
        lt = INJ.light([cev(1, "2026-09-20", "pfp")], [mk("2026-09-27", 2, 3)], D)
        assert lt["light_label"] == "Yellow" and "3/10" in lt["reason"] and "2/10" in lt["reason"]


# ---------------------------------------------------------------------------
# the week's sessions
# ---------------------------------------------------------------------------

def _s(id, kind, day, minutes, **kw):
    return O.Session(id=id, kind=kind, title=kw.pop("title", id), minutes=minutes, day=day, tss=minutes * 0.8, **kw)


def _week():
    return [_s("done", "easy", "2026-09-28", 40, done=True),
            _s("quality", "quality", "2026-09-30", 60, variant_key="v1a", rung_key="z5a"),
            _s("strength1", "strength", "2026-10-01", 35, detail="深蹲"),
            _s("easy2", "easy", "2026-10-02", 50),
            _s("long", "long", "2026-10-04", 120)]


def test_yellow_week():
    notes: list = []
    lt = INJ.light([cev(1, "2026-09-20", "pfp")], [mk("2026-09-27", 2, 3)], D)
    ss = O.light_apply(_week(), lt, D, last_min=200.0, done_min=40.0, notes=notes)
    left = [s for s in ss if not s.done and s.kind in O.RUN_KINDS]
    assert not any(s.kind == "quality" for s in ss) and all(s.variant_key is None for s in left)
    assert sum(s.minutes for s in left) <= 200 - 40                                    # ≤ last week's actual
    assert sum(s.minutes for s in left) <= 200
    lng = next(s for s in ss if s.kind == "long")
    assert lng.minutes <= 90                                                            # 120 × 0.75, then the cap
    assert any("上一次 3/10，超過膝前痛的 2/10" in n["text"] and n["src"] == "injury_light" for n in notes)
    assert next(s for s in ss if s.id == "done").minutes == 40


def test_yellow_without_a_cap_needed_keeps_the_easy_minutes():
    lt = INJ.light([ev(1, "2026-09-20")], [mk("2026-09-28", 2)], D)
    ss = O.light_apply(_week(), lt, D, last_min=600.0, done_min=40.0)
    assert next(s for s in ss if s.id == "easy2").minutes == 50
    assert next(s for s in ss if s.kind == "long").minutes == 90


def test_red_week_keeps_strength_only():
    notes: list = []
    lt = INJ.light([ev(1, "2026-09-20")], [mk("2026-09-25", 2), mk("2026-09-27", 2)], D)
    ss = O.light_apply(_week(), lt, D, last_min=200.0, done_min=40.0, notes=notes)
    assert [s.id for s in ss] == ["done", "strength1"]
    assert "會痛的動作先不做" in ss[1].detail and ss[1].detail.startswith("深蹲")
    assert any(n["level"] == "bad" and "先不排跑步" in n["text"] for n in notes)


def test_green_and_no_injury_change_nothing():
    for lt in (None, INJ.light([ev(1, "2026-09-20")], [mk("2026-09-28", 1)], D)):
        ss = _week()
        before = [asdict(s) for s in ss]
        assert O.light_apply(ss, lt, D, 200.0, 40.0) is ss and [asdict(s) for s in ss] == before


def test_week_plan_no_open_injury_is_unchanged_and_red_has_no_run(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_recovery_week import LIGHT, _week as _rw

    def wp(events, tags):
        ds, plan, _ = _rw("2026-11-28", {4: LIGHT})
        monkeypatch.setattr(INJ, "load_events", lambda *a, **k: list(events))
        monkeypatch.setattr(AT, "load", lambda *a, **k: list(tags))
        st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
        return O.week_plan(ds, st, TODAY)

    def strip(w):
        return [{k: v for k, v in s.items() if k != "done_by"} for s in w["sessions"]]
    base = wp([], [])
    assert base["injury_light"] is None
    old = wp([ev(1, "2026-08-01", status="resolved", resolved="2026-08-20")], [])
    assert strip(old) == strip(base) and old["injury_light"] is None
    ill = wp([{**ev(1, "2026-09-29"), "category": "illness", "illness": "fever", "resolved_date": None}], [])
    assert ill["injury_light"] is None
    red = wp([ev(1, "2026-09-20", severity="severe")], [])
    assert red["injury_light"]["color"] == "red"
    left = [s for s in red["sessions"] if not s["done"] and s["day"] and s["day"] >= TODAY.isoformat()]
    assert left and not any(s["kind"] in O.RUN_KINDS for s in left)
    assert any(s["kind"] == "strength" for s in left)


def test_red_light_offers_days_off():
    from backend.engine import suggestions as SG
    evs = [ev(4, "2026-09-25", severity="moderate")]
    lt = {"id": 4, "color": "red", "reason": "標了「中斷」"}
    rows = SG.injury_rows(evs, "2026-09-30", set(), None, [], lt)
    assert rows and rows[0]["type"] == "injury_rest" and "疼痛紅燈" in rows[0]["reason"]
    assert SG.injury_rows(evs, "2026-09-30", set(), None, [], {**lt, "color": "yellow"}) == []


# ---------------------------------------------------------------------------
# the 0–10 on the activity mark, its API and the DB
# ---------------------------------------------------------------------------

def test_pain_score_store_and_validation(tmp_path):
    db = tmp_path / "t.db"
    AT.upsert(db, start_local="2026-09-27T07:00", pain=2, pain_area="knee", pain_score=3)
    r = AT.load(db)[0]
    assert r["pain_score"] == 3
    with pytest.raises(ValueError):
        AT.upsert(db, start_local="2026-09-27T07:00", pain_score=11)
    AT.upsert(db, start_local="2026-09-27T07:00", pain=None)                    # clearing the mark clears it
    assert AT.load(db)[0]["pain_score"] is None
    AT.upsert(db, start_local="2026-09-28T07:00", pain_score=4)                 # no mark: not kept
    assert {x["start_local"]: x for x in AT.load(db)}["2026-09-28T07:00"]["pain_score"] is None
    assert INJ.pain_marks([{"start_local": "2026-09-27T07:00", "pain": 1, "pain_score": 2}])[0]["score"] == 2


def test_pain_score_through_the_api_helper(tmp_path):
    from sqlalchemy import select
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    from backend.db.models import ActivityTag
    eng, S = _tmp_session(tmp_path)

    async def _inner():
        async with S() as s:
            await save_activity_tag(s, ActivityUpdate(pain=2, pain_area="knee"), start_local="2026-09-27T07:00")
            await save_activity_tag(s, ActivityUpdate(pain_score=3), start_local="2026-09-27T07:00")
            t = (await s.execute(select(ActivityTag))).scalars().one()
            assert t.pain == 2 and t.pain_score == 3 and t.injury_id is not None
            with pytest.raises(Exception):
                await save_activity_tag(s, ActivityUpdate(pain_score=12), start_local="2026-09-27T07:00")
    _run(_inner())
    _run(eng.dispose())


def test_migration_adds_pain_score_to_an_old_table(tmp_path):
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        import backend.db.database as db_mod
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, athlete_id INTEGER, "
                                    "start_local TEXT, pain INTEGER, pain_area TEXT, injury_id INTEGER)"))
            await conn.execute(text("INSERT INTO activity_tags (athlete_id, start_local, pain) "
                                    "VALUES (1, '2026-09-27T07:00', 2)"))
            await conn.run_sync(Base.metadata.create_all)
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            await db_mod._migrate_schema()                       # idempotent
            async with engine.begin() as conn:
                cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(activity_tags)"))).fetchall()}
                row = (await conn.execute(text("SELECT pain, pain_score FROM activity_tags"))).one()
        finally:
            db_mod.engine = orig
        assert "pain_score" in cols and tuple(row) == (2, None)
    _run(_inner())
    db = tmp_path / "old.db"                                     # the read-only loader on a file without it
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, athlete_id INTEGER, start_local TEXT, "
                "pain INTEGER, activity_type_overridden BOOLEAN, effort_overridden BOOLEAN)")
    con.execute("INSERT INTO activity_tags (athlete_id, start_local, pain) VALUES (1, '2026-09-27T07:00', 2)")
    con.commit()
    con.close()
    assert AT.load(db)[0]["pain_score"] is None


def test_foot_log_reads_runs_walks_and_marks(monkeypatch):
    from backend.tests.wko5_fakes import FakeDataset, FakeWorkout
    ws = [FakeWorkout(start=dt.datetime(2026, 9, 27, 7), sport="run", metrics={"duration": 2400.0}),
          FakeWorkout(start=dt.datetime(2026, 9, 28, 7), sport="walk", metrics={"duration": 2400.0}),
          FakeWorkout(start=dt.datetime(2026, 9, 29, 7), sport="bike", metrics={"duration": 2400.0})]
    ds = FakeDataset(ws, D)
    tags = [{"start_local": "2026-09-27T07:00", "file": None, "pain": 2, "pain_score": 3, "pain_area": "knee",
             "injury_id": 1}]
    monkeypatch.setattr(AT, "find", lambda rows, start, file=None, **k: next(
        (r for r in rows if r["start_local"] == AT.key_of(start)), None))
    log = INJ.foot_log(ds, tags)
    assert [(m["date"], m["cat"]) for m in log] == [("2026-09-27", "run"), ("2026-09-28", "walk")]
    assert log[0]["pain"] == 2 and log[0]["score"] == 3 and log[1]["pain"] is None
