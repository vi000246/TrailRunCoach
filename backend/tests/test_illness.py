"""
SP-117 生病 in the 傷病紀錄 (engine/injuries.py illness_rule / pause_reason, engine/reentry.py,
overview.illness_apply, api/injuries.py, db migration): two types — 輕微感冒 (Z1 recovery runs while
the symptoms last) and 發燒或全身症狀 (no run until a day after them, the first run at recovery pace)
— then the break's own re-entry block, never the injury step-up. No COVID branch.
Synthetic data and tmp SQLite only.
"""
from __future__ import annotations

import datetime as dt
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.engine import injuries as INJ
from backend.engine import overview as O
from backend.engine import reentry as RE
from backend.tests.test_injuries import _run, _runs, _tmp_session, api, ev  # noqa: F401 — api is a fixture
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    INJ._memo.clear()
    yield
    INJ._memo.clear()


def ill(id, onset, kind="cold", resolved=None):
    return {**ev(id, onset, area="unknown", severity="mild", status="resolved" if resolved else "active",
                 resolved=resolved), "category": "illness", "illness": kind}


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

def test_cold_is_z1_while_the_symptoms_last():
    e = [ill(1, "2026-09-20", "cold")]
    assert INJ.illness_rule(e, date(2026, 9, 19)) is None
    r = INJ.illness_rule(e, date(2026, 9, 25))
    assert r["rule"] == "z1" and r["label"] == "生病（輕微感冒）" and "Kaulback 2023" in r["src"]
    e = [ill(1, "2026-09-20", "cold", resolved="2026-09-24")]            # 9/24 = the first symptom-free day
    assert INJ.illness_rule(e, date(2026, 9, 23))["rule"] == "z1" and INJ.illness_rule(e, date(2026, 9, 24)) is None


def test_fever_no_run_until_a_day_after_then_a_recovery_run():
    e = [ill(2, "2026-09-20", "fever")]
    assert INJ.illness_rule(e, date(2026, 9, 28))["rule"] == "off"
    e = [ill(2, "2026-09-20", "fever", resolved="2026-09-26")]
    assert INJ.illness_rule(e, date(2026, 9, 25))["rule"] == "off"
    assert INJ.illness_rule(e, date(2026, 9, 26))["rule"] == "off"          # symptom-free, wait ≥ 1 day
    r = INJ.illness_rule(e, date(2026, 9, 27))
    assert r["rule"] == "first" and "恢復配速" in r["text"] and "教練級" in r["src"]
    assert INJ.illness_rule(e, date(2026, 9, 28)) is None
    # the strictest of several
    both = e + [ill(3, "2026-09-27", "cold")]
    assert INJ.illness_rule(both, date(2026, 9, 27))["rule"] == "z1"


def test_illness_pauses_the_intervals_and_says_so():
    from backend.engine import quality_gate as QG
    why = INJ.pause_reason([ill(5, "2026-09-25", "cold")], TODAY)
    assert why and "#5" in why and "輕微感冒" in why
    assert QG.guard(injury=why)["block"]
    assert INJ.pause_reason([ill(5, "2026-09-20", "cold", resolved="2026-09-25")], TODAY) is None
    n = INJ.week_notes([ill(5, "2026-09-25", "fever")], date(2026, 9, 28), TODAY)
    assert n and n[0]["src"] == "illness" and "發燒或全身症狀" in n[0]["text"] and "先不跑" in n[0]["text"]


def test_illness_never_takes_a_pain_mark_and_is_not_an_injury_case():
    out = INJ.attach(2, None, None, date(2026, 9, 26), "k", "f", None, [ill(1, "2026-09-25", "cold")])
    assert out["injury_id"] is None and out["create"] is not None          # a new injury draft, not the cold
    assert INJ.recurrence(ill(2, "2026-09-25"), [ill(1, "2026-09-01", resolved="2026-09-05")]) is None
    from backend.engine import injury_exposure as IE
    ds = FakeDataset(_runs(date(2025, 1, 1), TODAY, seed=2), TODAY)
    a = IE.analysis(ds, [ill(1, "2026-06-01", "fever", resolved="2026-06-05")], TODAY)
    assert a["n"] == 0 and not a["excluded"]


# ---------------------------------------------------------------------------
# re-entry: the four cases of the ticket
# ---------------------------------------------------------------------------

def _gap(a: date, b: date):
    """A 45-min run every day from June, none from `a` to `b` (both included)."""
    days = [date(2026, 6, 1) + dt.timedelta(days=k) for k in range((TODAY - date(2026, 6, 1)).days + 1)]
    return FakeDataset([FakeWorkout(start=dt.datetime(d.year, d.month, d.day, 7), sport="run",
                                    metrics={"duration": 2700.0, "distance": 7.5, "tss": 40.0})
                        for d in days if not a <= d <= b], TODAY)


def test_cold_without_a_break_no_reentry():
    ds = _gap(date(2026, 1, 1), date(2026, 1, 2))                         # no gap since June
    assert RE.find(ds, TODAY, injuries=[ill(1, "2026-09-24", "cold")], step_up=True) is None


def test_cold_seven_days_off():
    ds = _gap(date(2026, 9, 10), date(2026, 9, 16))                       # 7 days off
    p = RE.find(ds, TODAY, injuries=[ill(4, "2026-09-09", "cold", resolved="2026-09-16")], step_up=True)
    assert p["category"] == "6-13" and p["days"] == 7 and not p["stepped_up"]
    assert p["illness"]["illness"] == "cold" and p["injury"] is None
    assert "生病停跑 7 天（輕微感冒，傷病紀錄 #4）" in p["text"]


def test_fever_ten_days_off():
    ds = _gap(date(2026, 9, 10), date(2026, 9, 19))                       # 10 days off
    p = RE.find(ds, TODAY, injuries=[ill(6, "2026-09-10", "fever", resolved="2026-09-18")], step_up=True)
    assert p["category"] == "6-13" and p["days"] == 10 and not p["stepped_up"] and p["days_effective"] == 10
    assert "生病停跑 10 天（發燒或全身症狀，傷病紀錄 #6）" in p["text"]


def test_illness_and_injury_overlapping_both_apply():
    ds = _gap(date(2026, 9, 10), date(2026, 9, 19))
    evs = [ev(3, "2026-09-09", status="resolved", resolved="2026-09-20", side="right"),
           ill(6, "2026-09-12", "fever", resolved="2026-09-15")]
    p = RE.find(ds, TODAY, injuries=evs, step_up=True)
    assert p["injury"]["id"] == 3 and p["illness"]["id"] == 6
    assert p["stepped_up"] and p["category"] == "14-28"                  # the injury's step-up still applies
    assert "傷停＋生病停跑 10 天" in p["text"] and "右膝" in p["text"] and "發燒或全身症狀" in p["text"]
    # an illness alone never steps up
    assert not RE.plan(date(2026, 9, 9), date(2026, 9, 20), illness=ill(6, "2026-09-12", "fever"),
                       step_up=True)["stepped_up"]


# ---------------------------------------------------------------------------
# the week plan
# ---------------------------------------------------------------------------

def _s(id, kind, day, minutes, **kw):
    return O.Session(id=id, kind=kind, title=kw.pop("title", id), minutes=minutes, day=day, tss=minutes * 0.8, **kw)


def _week():
    return [_s("easy1", "easy", "2026-09-29", 50, title="輕鬆跑＋加速跑 6×20 秒"),
            _s("quality", "quality", "2026-09-30", 60, variant_key="v1a", rung_key="z5a"),
            _s("strength1", "strength", "2026-10-01", 35),
            _s("easy2", "easy", "2026-10-02", 50),
            _s("long", "long", "2026-10-03", 120, terrain="trail"),
            _s("done", "easy", "2026-09-28", 40, done=True)]


def test_week_plan_cold_z1_only():
    ss = O.illness_apply(_week(), [ill(1, "2026-09-27", "cold")], "輕鬆跑上限 142 bpm")
    runs = [s for s in ss if s.kind in O.RUN_KINDS and not s.done]
    assert runs and all(s.kind == "easy" and s.title == "恢復跑（心率 1 區）" and s.minutes <= INJ.ILL_RUN_MAX_MIN
                        for s in runs)
    q = next(s for s in ss if s.id == "quality")
    assert q.variant_key is None and q.rung_key is None and "不超過輕鬆跑上限 142 bpm" in q.detail
    assert next(s for s in ss if s.id == "long").terrain == "road"
    assert any(s.kind == "strength" for s in ss) and next(s for s in ss if s.id == "done").minutes == 40
    assert O.illness_apply(_week(), [], "")[1].kind == "quality"         # no illness: untouched


def test_week_plan_fever_no_run_then_a_recovery_first_run():
    open_ = O.illness_apply(_week(), [ill(2, "2026-09-27", "fever")], "")
    assert [s.id for s in open_] == ["done"]                             # nothing but what is done
    # symptom-free 9/30: no run 9/30, the first run (10/1 or later) at recovery pace, then as planned
    ss = O.illness_apply(_week(), [ill(2, "2026-09-27", "fever", resolved="2026-09-30")], "")
    ids = [s.id for s in ss]
    assert "easy1" not in ids and "quality" not in ids                   # 9/29, 9/30: no run
    e2 = next(s for s in ss if s.id == "easy2")
    assert e2.title == "恢復跑（發燒後第一次）" and e2.minutes <= INJ.FIRST_RUN_MAX_MIN
    assert next(s for s in ss if s.id == "long").kind == "long"
    assert any(s.id == "strength1" for s in ss)                          # 10/1 is no longer an off day


# ---------------------------------------------------------------------------
# API and the DB migration
# ---------------------------------------------------------------------------

def test_api_illness_event(api):  # noqa: F811
    m = api.get("/api/v1/wko5/injuries/meta").json()
    assert m["illness"] == {"cold": "輕微感冒", "fever": "發燒或全身症狀"} and "流鼻水" in m["illness_help"]["cold"]
    assert set(m["categories"]) == {"injury", "illness"}
    bad = api.post("/api/v1/wko5/injuries", json={"category": "illness", "onset_date": "2026-09-20"})
    assert bad.status_code == 400 and bad.json()["detail"] == "INVALID_ILLNESS"
    assert api.post("/api/v1/wko5/injuries", json={"category": "illness", "illness": "covid",
                                                   "onset_date": "2026-09-20"}).status_code == 400
    r = api.post("/api/v1/wko5/injuries", json={"category": "illness", "illness": "fever", "area": "knee",
                                                "severity": "severe", "onset_date": "2026-09-20",
                                                "resolved_date": "2026-09-24"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["category"] == "illness" and e["illness"] == "fever" and e["label"] == "生病（發燒或全身症狀）"
    assert e["area"] == "unknown" and e["severity"] == "mild" and e["status"] == "resolved"
    p = api.patch(f"/api/v1/wko5/injuries/{e['id']}", json={"illness": "cold"}).json()
    assert p["label"] == "生病（輕微感冒）"
    inj = api.post("/api/v1/wko5/injuries", json={"area": "knee", "onset_date": "2026-09-21"}).json()
    assert inj["category"] == "injury" and inj["illness"] is None and inj["label"] == "膝"


def test_migration_adds_the_illness_columns_to_an_old_table():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        import backend.db.database as db_mod
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE injury_events (id INTEGER PRIMARY KEY, athlete_id INTEGER, "
                                    "area TEXT, onset_date TEXT, status TEXT)"))
            await conn.execute(text("INSERT INTO injury_events (athlete_id, area, onset_date, status) "
                                    "VALUES (1, 'knee', '2026-01-01', 'active')"))
            await conn.run_sync(Base.metadata.create_all)
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            await db_mod._migrate_schema()                       # idempotent
            async with engine.begin() as conn:
                cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(injury_events)"))).fetchall()}
                row = (await conn.execute(text("SELECT area, category, illness FROM injury_events"))).one()
        finally:
            db_mod.engine = orig
        assert {"category", "illness"} <= cols
        assert tuple(row) == ("knee", "injury", None)            # the old row is an injury
    _run(_inner())
