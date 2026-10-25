"""SP-909: the 自動調整 panel of the 課表 / 總覽 pages in English.

static/autoplan.js has no Chinese of its own (common.autoplan.* in both catalogs); the change log's texts
(engine/plan_auto.py) are msgids whose zh-TW text is unchanged — stored as the run wrote them (a run in the
background writes zh-TW) and shown in the request's language by GET /plan/auto. Synthetic data only."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import request_context as RC
from backend.engine import plan_auto as PA
from backend.engine import stored_text as ST
from backend.scripts import i18n_extract as X
from backend.scripts.i18n_extract import CJK
from backend.tests.test_coros_workouts import make_db, run


def test_autoplan_js_has_no_chinese_of_its_own():
    p = X.ROOT / "backend" / "static" / "autoplan.js"
    assert not X.scan_js_source("autoplan.js", p.read_text("utf-8")).unwrapped
    cats = X.frontend_catalogs()
    zh, en = cats["zh-TW"]["common"], cats["en"]["common"]
    keys = [k for k in zh if k.startswith("autoplan.")]
    assert len(keys) > 30 and all(en.get(k) and not CJK.search(json.dumps(en[k], ensure_ascii=False)) for k in keys)
    for k in ("st.applied", "act.changed", "push.ok", "pending_title", "approve_btn", "undo_confirm"):
        assert f"autoplan.{k}" in zh and f"autoplan.{k}" in en


def test_the_summary_line_is_the_same_chinese_and_reads_in_english():
    done = [{"action": "done"}, {"action": "done"}, {"action": "missed"}]
    fw = [{"action": "changed"}, {"action": "added"}]
    cases = {
        PA.summary(done + fw): ("完成 2 堂、沒跑 1 堂；調整 2 堂", "2 done, 1 missed; 2 sessions adjusted"),
        PA.summary(fw, held=True): ("調整 2 堂（待你確認）", "2 sessions adjusted (waiting for you)"),
        PA.summary([{"action": "missed"}]): ("沒跑 1 堂", "1 missed"),
        PA.summary([]): ("沒有變更", "No changes"),
        PA.cp_summary(220, 226, 3, True): ("CP 220 → 226 W：未來 3 堂課的功率目標已更新並重新推送",
                                           "CP 220 → 226 W: the power targets of 3 upcoming sessions updated and sent again"),
        PA.cp_summary(220, 226, 1, False): ("CP 220 → 226 W：未來 1 堂課的功率目標已更新，待推送",
                                            "CP 220 → 226 W: the power targets of 1 upcoming session updated, to send"),
    }
    for got, (zh, en) in cases.items():
        assert got == zh
        assert ST.shown(got, "en") == en


def test_big_change_texts_in_english():
    st = [{"uid": "a", "day": "2026-10-01", "kind": "easy", "state": "active", "tss": 40.0, "minutes": 50,
           "title": "輕鬆跑"}]
    up = [{**st[0], "tss": 60.0, "minutes": 75}]
    big = PA.classify(st, up, PA.diff(st, up, [], []), "2026-09-30", "2026-10-06", "base", "base", None)
    assert big[0]["text"] == "9/28 那週計畫負荷 40 → 60（+50%，> +20%）"
    assert ST.shown(big[0]["text"], "en") == "Week of 9/28: planned load 40 → 60 (+50%, > +20%)"
    many_s = [{**st[0], "uid": str(i), "day": f"2026-10-0{i}"} for i in range(1, 6)]
    many_n = [{**s, "title": "別的", "tss": 41.0} for s in many_s]
    m = [b for b in PA.classify(many_s, many_n, PA.diff(many_s, many_n, [], []), "2026-09-30", "2026-10-06",
                                "b", "b", None) if b["rule"] == "many"][0]
    assert not CJK.search(ST.shown(m["text"], "en")), m["text"]


def test_get_auto_shows_the_stored_log_in_the_request_language(monkeypatch):
    from backend.api import plan_auto as API
    from backend.db.database import get_db
    db = run(make_db())

    async def add():
        await PA._add_entry(db, trigger="sync:coros", status="applied",
                            summary=PA.summary([{"action": "done"}, {"action": "changed"}]),
                            items=[{"action": "changed", "day": "2026-10-02", "title": "LSD（山路）",
                                    "reason": "CP 220 → 226 W：功率目標依 % CP 重算", "push": PA.TO_PUSH}],
                            push={"status": "ok"})
    run(add())

    async def fake_db():
        yield db
    app = FastAPI()
    app.include_router(API.router)
    app.dependency_overrides[get_db] = fake_db
    app.add_middleware(RC.RequestContextMiddleware)
    c = TestClient(app)
    en = c.get("/api/v1/overview/plan/auto?lang=en").json()
    zh = c.get("/api/v1/overview/plan/auto?lang=zh-TW").json()
    e, z = en["log"][0], zh["log"][0]
    assert z["summary"] == "完成 1 堂；調整 1 堂" and z["items"][0]["push"] == "待推送"
    assert e["summary"] == "1 done; 1 session adjusted"
    it = e["items"][0]
    assert it["title"] == "LSD (trail)" and it["push"] == "To send"
    assert it["reason"] == "CP 220 → 226 W: power targets recalculated from % CP"
    assert not any(CJK.search(t) for t in en["thresholds"]["texts"].values())
    assert any(CJK.search(t) for t in zh["thresholds"]["texts"].values())
