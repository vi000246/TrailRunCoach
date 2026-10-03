"""
長時間 -> LSD (2026-10-03): the long-run type label, the planner's titles, the old
stored auto titles mapped at read time, and the 課表類型 tag of the chart viewer's
activity list (plan_store.session_tag / done_by_index). Synthetic only.
"""
import json
import sqlite3

from sqlalchemy import create_engine

from backend.db.models import Base, PlanSession
from backend.engine import plan_store as PS


def test_kind_label_and_default_title():
    assert PS.KINDS["long"] == "LSD" and PS.DEFAULT_TITLES["long"] == "LSD"


def test_old_auto_titles_map_at_read_time_user_titles_kept():
    assert PS.display_title("長時間輕鬆") == "LSD"
    assert PS.display_title("長時間輕鬆（山路）") == "LSD（山路）"
    assert PS.display_title("長時間輕鬆（路跑）") == "LSD（路跑）"
    assert PS.display_title("長時間輕鬆（山路越野）") == "LSD（山路越野）"
    for own in ("長時間輕鬆 + 補給練習", "週六長跑", "LSD（山路）", None, ""):
        assert PS.display_title(own) == own
    r = PlanSession(uid="u", week_start="2026-09-28", day="2026-10-03", kind="long", title="長時間輕鬆（山路）",
                    minutes=120, origin="auto", edited=False, provisional=False, state="active")
    assert PS.to_dict(r)["title"] == "LSD（山路）"


def test_session_tag():
    assert PS.session_tag({"kind": "long", "title": "LSD"}) == {"kind": "long", "label": "LSD", "icon": "long"}
    assert PS.session_tag({"kind": "easy", "title": "輕鬆跑"})["label"] == "輕鬆跑"
    assert PS.session_tag({"kind": "quality", "title": "閾值 3×10 分"}) == {"kind": "quality", "label": "強度課", "icon": "z3"}
    assert PS.session_tag({"kind": "quality", "title": "VO2max 5×3 分"})["icon"] == "z5"
    assert PS.session_tag({"kind": "test", "title": "CP 測試"})["label"] == "測試"
    assert PS.session_tag({"kind": "hike", "title": "越野跑"})["label"] == "越野跑"
    assert PS.session_tag({"kind": "strength", "title": "肌力"})["icon"] == "strength"
    assert PS.session_tag({"kind": "easy", "title": "陡坡健走 15%（模擬負重）"}) == \
        {"kind": "easy", "label": "陡坡健走", "icon": "climb"}


def _db(tmp_path):
    db = tmp_path / "w.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(db)
    rows = [("a", "2026-10-03", "long", "長時間輕鬆（山路）", "done", {"index": 7, "date": "2026-10-03"}),
            ("b", "2026-10-01", "quality", "閾值 3×10 分", "done", {"index": 5, "date": "2026-10-01"}),
            ("c", "2026-10-02", "easy", "輕鬆跑", "active", None),
            ("d", "2026-10-02", "notice", "⚠ 課表待確認", "done", {"index": 6})]
    for uid, day, kind, title, state, done_by in rows:
        con.execute("INSERT INTO plan_sessions (athlete_id, uid, week_start, day, kind, title, minutes, origin, edited,"
                    " provisional, state, done_by, updated_at) VALUES (1, ?, '2026-09-28', ?, ?, ?, 60, 'auto', 0, 0,"
                    " ?, ?, '2026-10-03')", (uid, day, kind, title, state, json.dumps(done_by) if done_by else None))
    con.commit()
    con.close()
    return db


def test_done_by_index_one_lookup(tmp_path):
    got = PS.done_by_index(_db(tmp_path))
    assert set(got) == {7, 5}                     # active / notice rows: no tag
    assert got[7] == {"kind": "long", "label": "LSD", "icon": "long", "title": "LSD（山路）", "day": "2026-10-03"}
    assert got[5]["label"] == "強度課"
    assert PS.done_by_index(tmp_path / "missing.db") == {}
