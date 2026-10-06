"""
SP-269 傷別 (engine/injuries.py CONDITIONS / monitor / check_condition, api/injuries.py, the suggestion
box, the activity card's summary, db migration): an optional condition per injury event — 跟腱、足底筋膜、
髂脛束、膝前痛、其他 — each tied to one area, with its own pain-monitoring text (跟腱 Silbernagel 2007,
膝前痛 Esculier 2016, the rest the general return-to-run rules). No condition = the general rules.
Synthetic data and tmp SQLite only (docs/research/injury-graded-return.md §2.3, §4.2, §6.1 1–2).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.engine import injuries as INJ
from backend.i18n import use_locale
from backend.tests.test_injuries import _run, api, ev  # noqa: F401 — api is a fixture

PFP_TEXT = "跑時 ≤ 2/10、跑完 60 分鐘內回到原本"
GENERAL = "不能越跑越痛、不能改變跑姿、隔天不能更痛"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    INJ._memo.clear()
    yield
    INJ._memo.clear()


def cev(id, onset, condition=None, area="knee", **kw):
    return {**ev(id, onset, area=area, **kw), "condition": condition}


def test_conditions_are_the_four_and_other():
    assert list(INJ.CONDITIONS) == ["achilles", "plantar_fascia", "itb", "pfp", "other"]
    assert INJ.CONDITION_AREA == {"achilles": "achilles", "plantar_fascia": "foot", "itb": "knee", "pfp": "knee",
                                  "other": None}


def test_old_event_without_condition_shows_the_general_rules():
    old = ev(1, "2026-09-20")                                    # no "condition" key at all (an old row)
    m = INJ.monitor(old)
    assert m["key"] == "general" and GENERAL in m["text"] and "Silbernagel" not in m["text"]
    assert "這不是醫療診斷" in m["disclaimer"]
    j = INJ.event_json(old, date(2026, 9, 30))
    assert j["condition"] is None and j["condition_label"] == "" and GENERAL in j["monitor"]
    assert INJ.summary(old, date(2026, 9, 30))["monitor"] == j["monitor"]


@pytest.mark.parametrize("cond, want, src", [
    ("achilles", None, "Silbernagel 2007"),
    ("pfp", PFP_TEXT, "Esculier 2016"),
    ("plantar_fascia", GENERAL, "Ohio State Wexner"),
    ("itb", GENERAL, "Ohio State Wexner"),
    ("other", GENERAL, "Ohio State Wexner"),
])
def test_each_condition_text(cond, want, src):
    area = INJ.CONDITION_AREA[cond] or "hip"
    m = INJ.monitor(cev(1, "2026-09-20", cond, area=area))
    if cond == "achilles":
        assert m["text"] == INJ.SILBERNAGEL["text"]             # the same text as before SP-269
    else:
        assert want in m["text"]
    assert src in m["text"] and "這不是醫療診斷" in m["disclaimer"]
    if cond not in ("other",):
        assert "醫師或物理治療師說是" in m["text"] or cond == "achilles"


def test_shin_says_see_a_doctor_first():
    m = INJ.monitor(cev(1, "2026-09-20", None, area="shin_calf"))
    assert "先給醫師看" in m["text"] and GENERAL in m["text"]


def test_condition_area_check():
    assert INJ.check_condition("ankle", "pfp") == "CONDITION_AREA_MISMATCH"
    assert INJ.check_condition("knee", "achilles") == "CONDITION_AREA_MISMATCH"
    assert INJ.check_condition("足弓", "plantar_fascia") == "CONDITION_AREA_MISMATCH"    # a custom area
    assert INJ.check_condition("knee", "itb") is None and INJ.check_condition("knee", "pfp") is None
    assert INJ.check_condition("ankle", "other") is None and INJ.check_condition("ankle", None) is None
    assert INJ.validate_event({"condition": "shin_splints"}) == "INVALID_CONDITION"
    assert INJ.validate_event({"condition": None}) is None


def test_english_texts():
    with use_locale("en"):
        assert "≤ 2/10 while running" in INJ.monitor({"condition": "pfp"})["text"]
        assert "not a medical diagnosis" in INJ.monitor(None)["disclaimer"]
        assert INJ.condition_label("itb") == "IT band"


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------

def test_api_condition(api):  # noqa: F811
    m = api.get("/api/v1/wko5/injuries/meta").json()
    assert list(m["conditions"]) == ["achilles", "plantar_fascia", "itb", "pfp", "other"]
    assert PFP_TEXT in m["condition_monitor"]["pfp"] and GENERAL in m["monitor"]
    assert m["condition_area"]["itb"] == "knee" and "先給醫師看" in m["shin_note"]
    assert "這不是醫療診斷" in m["disclaimer"]
    # mismatch → an error code
    r = api.post("/api/v1/wko5/injuries", json={"area": "ankle", "condition": "pfp", "onset_date": "2026-09-20"})
    assert r.status_code == 400 and r.json()["detail"] == "CONDITION_AREA_MISMATCH"
    r = api.post("/api/v1/wko5/injuries", json={"area": "knee", "condition": "x", "onset_date": "2026-09-20"})
    assert r.status_code == 400 and r.json()["detail"] == "INVALID_CONDITION"
    # an unknown area takes the condition's own
    e = api.post("/api/v1/wko5/injuries", json={"condition": "pfp", "side": "right", "onset_date": "2026-09-20"}).json()
    assert e["area"] == "knee" and e["condition"] == "pfp" and e["condition_label"] == "膝前痛"
    assert PFP_TEXT in e["monitor"] and "Esculier 2016" in e["monitor"]
    # moving the area away from the condition is refused; clearing both works
    r = api.patch(f"/api/v1/wko5/injuries/{e['id']}", json={"area": "ankle"})
    assert r.status_code == 400 and r.json()["detail"] == "CONDITION_AREA_MISMATCH"
    p = api.patch(f"/api/v1/wko5/injuries/{e['id']}", json={"area": "ankle", "condition": None}).json()
    assert p["area"] == "ankle" and p["condition"] is None and GENERAL in p["monitor"]
    # 跟腱 keeps the Silbernagel text; an illness never carries a condition
    a = api.post("/api/v1/wko5/injuries", json={"area": "achilles", "condition": "achilles",
                                                "onset_date": "2026-09-21"}).json()
    assert a["monitor"] == INJ.SILBERNAGEL["text"]
    i = api.post("/api/v1/wko5/injuries", json={"category": "illness", "illness": "cold", "condition": "pfp",
                                                "onset_date": "2026-09-22"}).json()
    assert i["condition"] is None and i["monitor"] is None
    # an old event (no condition) lists with the general text
    o = api.post("/api/v1/wko5/injuries", json={"area": "hip", "onset_date": "2026-09-23"}).json()
    assert o["condition"] is None and GENERAL in o["monitor"]


# ---------------------------------------------------------------------------
# the suggestion box and the activity card's summary
# ---------------------------------------------------------------------------

def test_hold_box_follows_the_condition():
    from backend.engine import suggestions as SG
    rp = {"return": "2026-09-20", "end": "2026-10-04"}
    evs = [cev(7, "2026-09-10", "pfp", side="right")]
    marks = [{"date": "2026-09-26", "pain": 2, "area": "knee", "injury_id": 7}]
    row = SG.injury_rows(evs, "2026-09-28", set(), rp, marks)[0]
    assert row["type"] == "injury_hold" and PFP_TEXT in row["help"] and "Esculier 2016" in row["help"]
    assert "這不是醫療診斷" in row["help"]
    # an unlinked mark of the same area follows the open event of that area
    row = SG.injury_rows(evs, "2026-09-28", set(), rp, [{**marks[0], "injury_id": None}])[0]
    assert PFP_TEXT in row["help"]
    # no condition: the general rules
    row = SG.injury_rows([ev(7, "2026-09-10")], "2026-09-28", set(), rp, marks)[0]
    assert GENERAL in row["help"] and "Silbernagel" not in row["help"]


def test_activity_card_summary_carries_the_text():
    s = INJ.summary(cev(3, "2026-09-20", "pfp"), date(2026, 9, 25))
    assert s["condition"] == "pfp" and PFP_TEXT in s["monitor"]


# ---------------------------------------------------------------------------
# the DB: an old injury_events table upgrades
# ---------------------------------------------------------------------------

def test_migration_adds_the_condition_column_to_an_old_table(tmp_path):
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        import backend.db.database as db_mod
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE injury_events (id INTEGER PRIMARY KEY, athlete_id INTEGER, "
                                    "area TEXT, onset_date TEXT, status TEXT, category TEXT, illness TEXT)"))
            await conn.execute(text("INSERT INTO injury_events (athlete_id, area, onset_date, status, category) "
                                    "VALUES (1, 'knee', '2026-01-01', 'active', 'injury')"))
            await conn.run_sync(Base.metadata.create_all)
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            await db_mod._migrate_schema()                       # idempotent
            async with engine.begin() as conn:
                cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(injury_events)"))).fetchall()}
                row = (await conn.execute(text("SELECT area, condition FROM injury_events"))).one()
        finally:
            db_mod.engine = orig
        assert "condition" in cols and tuple(row) == ("knee", None)
    _run(_inner())
    # the engine's read-only loader copes with a file from before the column
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE injury_events (id INTEGER PRIMARY KEY, athlete_id INTEGER, area TEXT, "
                "onset_date TEXT, status TEXT)")
    con.execute("INSERT INTO injury_events (athlete_id, area, onset_date, status) VALUES (1, 'knee', '2026-01-01', 'active')")
    con.commit()
    con.close()
    rows = INJ.load_events(db)
    assert rows[0]["area"] == "knee" and "condition" not in rows[0]
    assert GENERAL in INJ.monitor(rows[0])["text"]
