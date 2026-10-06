"""跑步經驗問卷 (SP-290): engine/experience.py, the shared race list (engine/race_results.py), the
profile API (GET /profile setup.survey_pending, PUT /profile/experience, /profile/detect
has_history), the 精靈 and the 設定頁 fields, and an old DB going through init_db.
Synthetic data only; plan.json and the DB in tmp."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import athlete_profile as AP
from backend.engine import experience as EX
from backend.engine import race_results as RR
from backend.tests.test_general_settings import plan_file  # noqa: F401  (fixture)
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 7)          # a Wednesday
STATIC = Path(__file__).resolve().parents[1] / "static"


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


def _foot(day: dt.date, minutes=40, sport="run", sport_type="running", tags=("running",)):
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport=sport, sport_type=sport_type,
                       tags=list(tags), metrics={"duration": minutes * 60.0, "movingduration": minutes * 60.0})


def _weeks(n_weeks: int, per_week: int, end_monday: dt.date, hike_day: bool = False):
    """`per_week` runs (Mon / Wed / Fri / …) in each of the `n_weeks` weeks before `end_monday`."""
    ws = []
    for k in range(n_weeks, 0, -1):
        mon = end_monday - dt.timedelta(weeks=k)
        for i in range(per_week):
            d = mon + dt.timedelta(days=2 * i)
            ws.append(_foot(d, sport="other", sport_type="hiking", tags=("hiking",)) if hike_day and i == 0
                      else _foot(d))
    return ws


# ---- engine ----------------------------------------------------------------------------------

def test_answer_validates_and_the_volume_is_not_discounted():
    now = dt.datetime(2026, 10, 7, 1, 0, tzinfo=dt.timezone.utc)
    a = EX.answer({"runs_per_week": 3, "minutes_per_run": 40, "longest_min": 60, "can_run_30": True}, now)
    assert a == {"runs_per_week": 3, "minutes_per_run": 40, "longest_min": 60, "can_run_30": True,
                 "at": "2026-10-07T01:00:00+00:00"}
    assert EX.weekly_hours(a) == pytest.approx(2.0)            # 3 × 40 min, as entered (owner: no × 0.75)
    # every question can be skipped: blank answers are left out, saving still counts as answered
    blank = EX.answer({}, now)
    assert blank == {"at": "2026-10-07T01:00:00+00:00"} and EX.answered(blank) and EX.weekly_hours(blank) is None
    assert not EX.answered(None) and not EX.answered({})
    assert EX.weekly_hours({"runs_per_week": 0, "minutes_per_run": 30, "at": "x"}) == 0.0
    for bad in ({"runs_per_week": 15, "at": "x"}, {"minutes_per_run": 2, "at": "x"}, {"longest_min": 30.5, "at": "x"},
                {"can_run_30": "yes", "at": "x"}, {"runs_per_week": 3}, {"weeks": 3, "at": "x"}, [1, 2]):
        with pytest.raises(ValueError):
            EX.validate(bad)
    EX.validate(None)


def test_race_results_one_shared_list():
    assert RR.parse_time("1:05:30") == 3930 and RR.parse_time("45:00") == 2700 and RR.parse_time(2700) == 2700
    for bad in ("", "45", "1:65:00", "a:b"):
        with pytest.raises(ValueError):
            RR.parse_time(bad)
    r = RR.make(10, 2700, "2026-05-01", source="survey")
    assert r == {"date": "2026-05-01", "distance_km": 10.0, "time_s": 2700, "trail": False, "source": "survey",
                 "confirmed": True}
    manual = RR.make(21.1, 6000, "2026-03-01", trail=True)
    rows = RR.with_survey([manual], r)
    assert [x["source"] for x in rows] == ["survey", "manual"]           # newest first
    # answering again replaces the questionnaire's row, never a second one; None removes it
    r2 = RR.make(5, 1300, "2026-09-01", source="survey")
    rows = RR.with_survey(rows, r2)
    assert RR.survey_entry(rows) == r2 and len(rows) == 2
    assert RR.with_survey(rows, None) == [manual]
    with pytest.raises(ValueError):
        RR.validate([r, r2])
    # what SP-293 / SP-276 read: confirmed rows of the last year, road or trail
    old = RR.make(42.2, 12000, "2025-01-01")
    unconfirmed = RR.make(10, 2600, "2026-08-01", source="activity", confirmed=False)
    every = rows + [old, unconfirmed]
    RR.validate(every)
    assert [x["date"] for x in RR.usable(every, TODAY)] == ["2026-09-01", "2026-03-01"]
    assert [x["date"] for x in RR.usable(every, TODAY, trail=False)] == ["2026-09-01"]
    for bad in ({**r, "distance_km": 0.1}, {**r, "time_s": 10}, {**r, "date": "2026-13-01"},
                {**r, "source": "garmin"}, {**r, "extra": 1}, {**r, "date": "2027-01-01"}):
        with pytest.raises(ValueError):
            RR.validate_entry(bad, TODAY)
    assert RR.normalise([{"bad": 1}, r]) == [r]                        # a bad stored row is dropped


def test_has_history_needs_four_complete_weeks_with_three_days():
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    assert EX.has_history(FakeDataset(_weeks(4, 3, mon), TODAY), TODAY)
    assert EX.has_history(FakeDataset(_weeks(4, 3, mon, hike_day=True), TODAY), TODAY)      # hikes count
    assert not EX.has_history(FakeDataset(_weeks(3, 3, mon), TODAY), TODAY)
    assert not EX.has_history(FakeDataset(_weeks(6, 2, mon), TODAY), TODAY)
    assert not EX.has_history(FakeDataset([], TODAY), TODAY)
    # four good weeks long ago still count (an experienced runner back after a break is not a beginner)
    assert EX.has_history(FakeDataset(_weeks(4, 3, mon - dt.timedelta(weeks=30)), TODAY), TODAY)
    # a gap week breaks the run of weeks
    gap = _weeks(2, 3, mon - dt.timedelta(weeks=3)) + _weeks(2, 3, mon)
    assert not EX.has_history(FakeDataset(gap, TODAY), TODAY)
    # this (incomplete) week doesn't count
    this = _weeks(3, 3, mon) + [_foot(mon + dt.timedelta(days=i)) for i in range(3)]
    assert not EX.has_history(FakeDataset(this, TODAY), TODAY)


# ---- API ---------------------------------------------------------------------------------------

def _store(monkeypatch):
    """read_setting backed by a dict the test fills from the repository writes."""
    from backend.engine.wko5expr import datasource as DS
    store: dict = {}
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, *a: store.get(k, d))
    return store


def test_new_account_sees_the_questionnaire_then_saves_and_edits_it(plan_file, monkeypatch):  # noqa: F811
    from fastapi import HTTPException
    from backend.api import plan as PA
    from backend.settings.repository import SettingsRepository
    monkeypatch.setattr(PA, "_app_weight", lambda: None)
    store = _store(monkeypatch)
    g = PA.get_profile()
    assert g["setup"]["survey_pending"] and g["setup"]["remind"] and g["experience"] is None
    # the basics filled, the questionnaire still asked (same 精靈, same reminder)
    PA.put_profile(PA.ProfileIn(weights=[PA.WeightIn(date="2026-09-01", kg=60.0)], sex="female", age=35))
    g = PA.get_profile()["setup"]
    assert not g["needed"] and g["survey_pending"] and g["remind"] and g["missing"] == []

    async def go():
        s = await _session()
        r = await PA.put_experience(PA.ExperienceIn(runs_per_week=3, minutes_per_run=40, longest_min=60,
                                                    can_run_30=True,
                                                    race=PA.RaceIn(distance_km=10, time="48:30", date="2026-06-01")),
                                    db=s)
        repo = SettingsRepository(s, 1)
        store[EX.KEY], store[RR.KEY] = await repo.get(EX.KEY), await repo.get(RR.KEY)
        # 設定頁 edits it: answered again, race removed
        r2 = await PA.put_experience(PA.ExperienceIn(runs_per_week=4, minutes_per_run=30), db=s)
        edited = await repo.get(EX.KEY), await repo.get(RR.KEY)
        with pytest.raises(HTTPException) as ei:
            await PA.put_experience(PA.ExperienceIn(runs_per_week=30), db=s)
        assert ei.value.status_code == 400
        with pytest.raises(HTTPException):
            await PA.put_experience(PA.ExperienceIn(race=PA.RaceIn(distance_km=10, time="soon", date="2026-06-01")), db=s)
        with pytest.raises(HTTPException):
            await PA.put_experience(PA.ExperienceIn(race=PA.RaceIn(distance_km=10, time="45:00", date="2099-01-01")), db=s)
        return r, r2, edited
    r, r2, edited = _run(go())
    assert r["experience_hours"] == pytest.approx(2.0) and r["survey_race"]["time_s"] == 48 * 60 + 30
    g = PA.get_profile()
    assert not g["setup"]["survey_pending"] and not g["setup"]["remind"]
    assert g["experience"]["minutes_per_run"] == 40 and g["experience_hours"] == pytest.approx(2.0)
    assert g["survey_race"]["distance_km"] == 10.0 and g["survey_race"]["source"] == "survey"
    assert edited[0]["runs_per_week"] == 4 and "longest_min" not in edited[0] and edited[1] == []
    assert r2["survey_race"] is None


def test_later_snoozes_the_questionnaire_a_week_then_reminds(plan_file, monkeypatch):  # noqa: F811
    from backend.api import plan as PA
    monkeypatch.setattr(PA, "_app_weight", lambda: None)
    store = _store(monkeypatch)
    PA.put_profile(PA.ProfileIn(weights=[PA.WeightIn(date="2026-09-01", kg=60.0)], sex="male", age=40))
    now = dt.datetime.now(dt.timezone.utc)
    store[AP.SETUP_LATER_KEY] = (now - dt.timedelta(days=2)).isoformat()
    assert not PA.get_profile()["setup"]["remind"]                       # 「稍後再說」 two days ago
    store[AP.SETUP_LATER_KEY] = (now - dt.timedelta(days=AP.REMIND_DAYS + 1)).isoformat()
    g = PA.get_profile()["setup"]
    assert g["remind"] and g["survey_pending"]                           # a week later: asked again
    store[EX.KEY] = EX.answer({})                                        # answered (all skipped)
    assert not PA.get_profile()["setup"]["remind"]


def test_detect_says_whether_the_data_has_four_good_weeks(plan_file, monkeypatch):  # noqa: F811
    from backend.api import plan as PA
    from backend.api import wko5views as WV
    monkeypatch.setattr(PA, "_app_weight", lambda: None)
    monkeypatch.setattr(PA, "today_local", lambda: TODAY)
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    ds = FakeDataset(_weeks(5, 3, mon), TODAY)
    ds.power_source = lambda w: "none"
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: ds)
    assert PA.detect_profile()["has_history"] is True
    new = FakeDataset(_weeks(1, 2, mon), TODAY)
    new.power_source = lambda w: "none"
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: new)
    assert PA.detect_profile()["has_history"] is False

    def boom(*a, **k):
        raise FileNotFoundError("no data yet")
    monkeypatch.setattr(WV, "_dataset", boom)
    assert PA.detect_profile()["has_history"] is None                   # unknown: the 精靈 still asks


# ---- 精靈 / 設定頁 -----------------------------------------------------------------------------

def test_wizard_asks_four_skippable_questions_on_the_same_wizard():
    js = (STATIC / "setup_wizard.js").read_text("utf-8")
    for q in ("exp_q1", "exp_q2", "exp_q3", "exp_q4", "exp_import"):
        assert f'T("{q}")' in js
    for name in ('name="xn"', 'name="xm"', 'name="xl"', 'name="x30"', 'name="rk"', 'name="rt"', 'name="rd"'):
        assert name in js and f"{name} required" not in js                    # skippable: never required
    assert "prof.setup.survey_pending" in js and "has_history === true" in js
    assert "/experience" in js and "done(true)" in js                    # the same 稍後再說 (SP-211)
    assert js.count("j(P)") == 1                                          # one wizard, not a second one
    keys = set(re.findall(r'T\("(\w+)"', js))
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        assert not [k for k in keys if not cat.get(f"setup.{k}")], loc
    en = json.loads((STATIC / "i18n" / "en" / "common.json").read_text("utf-8"))
    assert not re.search("[一-鿿]", "".join(v for k, v in en.items() if k.startswith("setup.exp")))


def test_settings_page_edits_the_answers():
    s = (STATIC / "settings.html").read_text("utf-8")
    for el in ('id="x-n"', 'id="x-m"', 'id="x-l"', 'id="x-30"', 'id="x-rk"', 'id="x-rt"', 'id="x-rd"', 'id="saveexp"'):
        assert el in s
    assert "/api/v1/plan/profile/experience" in s and "renderExperience()" in s


# ---- old DB ------------------------------------------------------------------------------------

def test_an_old_db_reads_no_answer_and_takes_one(tmp_path, monkeypatch):
    """A DB from before SP-290 (no athlete.experience / athlete.race_results rows) goes through
    init_db() twice as on start-up: nothing is lost, the new keys read as their defaults (the
    questionnaire is pending), and an answer can be stored."""
    from sqlalchemy import select, text
    import backend.db.database as db_mod
    from backend.db.models import Base, UserSetting
    from backend.settings.repository import SettingsRepository
    path = tmp_path / "old.db"

    async def make_old():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for k, v in (("athlete.setup.done", True), ("athlete.setup.later_at", "2026-09-01T00:00:00+00:00")):
                await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                                        "VALUES (1, :k, :v, '2026-09-01')"), {"k": k, "v": json.dumps(v)})
        await eng.dispose()
    _run(make_old())
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()
        async with db_mod.AsyncSessionLocal() as db:
            repo = SettingsRepository(db, 1)
            before = await repo.get(EX.KEY), await repo.get(RR.KEY)
            await repo.set(EX.KEY, EX.answer({"runs_per_week": 2}))
            await repo.set(RR.KEY, [RR.make(10, 3000, "2026-05-01", source="survey")])
            with pytest.raises(ValueError):
                await repo.set(EX.KEY, {"runs_per_week": 99, "at": "x"})
            with pytest.raises(ValueError):
                await repo.set(RR.KEY, {"not": "a list"})
            await db.commit()
            kept = {r.key for r in (await db.execute(select(UserSetting))).scalars().all()}
            after = await repo.get(EX.KEY), await repo.get(RR.KEY)
        await db_mod.dispose(path)
        return before, kept, after
    before, kept, after = _run(go())
    assert before == (None, []) and not EX.answered(before[0])
    assert {"athlete.setup.done", "athlete.setup.later_at", EX.KEY, RR.KEY} <= kept
    assert after[0]["runs_per_week"] == 2 and after[1][0]["source"] == "survey"
