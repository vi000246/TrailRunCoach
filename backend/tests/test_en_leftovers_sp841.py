"""SP-841: the 傷病紀錄 page in English — the page's own text (static/i18n/<locale>/injuries.json: labels, the
timeline, the list, the analysis, 進階設定, the dialog, the errors) and what the server gives it (body areas,
sides, kinds, severities, statuses, the event labels, the analysis' metrics and reasons, the pain-monitoring
rules and the disclaimer). A custom area is the user's own label and stays as written. Chinese is unchanged.

Synthetic data and a tmp SQLite only (the test_injuries fixtures)."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from backend.engine import injuries as INJ
from backend.engine import injury_exposure as IE
from backend.i18n import use_locale
from backend.tests.test_injuries import _boosted, _clean, _daily, _runs, _tmp_session  # noqa: F401 (_clean: autouse)

STATIC = Path(__file__).resolve().parents[1] / "static"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _strings(x) -> list:
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    return [x] if isinstance(x, str) else []


def _cjk(x, allow=()) -> list:
    return [s for s in _strings(x) if CJK.search(s) and s not in allow]


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "injuries.json").read_text("utf-8"))


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """test_injuries' minimal app plus the request-language middleware (?lang=en)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import injuries as IA
    from backend.api import wko5views as WV
    from backend.db.database import get_db
    from backend.engine import activity_tags as AT
    from backend.request_context import RequestContextMiddleware
    from backend.tests.test_injuries import _run
    from backend.tests.wko5_fakes import FakeDataset
    eng, S = _tmp_session(tmp_path)

    async def _db():
        async with S() as s:
            yield s
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "app.db")
    ds = FakeDataset(_runs(date(2025, 1, 1), date(2026, 9, 30), seed=3), date(2026, 9, 30))
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: ds)
    app = FastAPI()
    app.include_router(IA.router)
    app.add_middleware(RequestContextMiddleware)
    app.dependency_overrides[get_db] = _db
    IA._an_memo.clear()
    yield TestClient(app)
    _run(eng.dispose())


# ---- the page ------------------------------------------------------------------------------------------

def test_the_page_has_no_unwrapped_chinese():
    from backend.scripts import i18n_extract as X
    scans = X.scan_repo()
    for f in ("backend/static/injuries.html", "backend/api/injuries.py", "backend/engine/injuries.py",
              "backend/engine/injury_exposure.py"):
        assert scans[f].unwrapped == [], (f, scans[f].unwrapped[:5])


def test_the_page_catalog_has_english_for_every_key():
    zh, en = _cat("zh-TW"), _cat("en")
    assert set(zh) == set(en)
    assert [k for k, v in en.items() if not v or CJK.search(v)] == []
    # a safety text keeps its warning
    assert "not a medical diagnosis" in en["disclaimer"] and "doctor or physical therapist" in en["disclaimer"]
    assert en["title"] == "Injury log" and zh["title"] == "傷病紀錄"


def test_the_english_page_shows_no_chinese():
    from backend.i18n.pages import render
    html = render("injuries", "en")
    body = re.sub(r"<script>window.__I18N__.*?</script>|<style>.*?</style>|<!--.*?-->", "", html, flags=re.S)
    body = re.sub(r"(?m)//[^\n]*$", "", body)            # the page script's comments
    assert CJK.findall(body) == []
    assert "Injury log" in html and 'data-tip="Tapping' in html
    zh = render("injuries", "zh-TW")
    assert "<title data-i18n=\"injuries.title\">傷病紀錄</title>" in zh and "＋ 新增" in zh


# ---- the server's words -------------------------------------------------------------------------------

def test_meta_in_english_and_chinese_unchanged(api):
    api.post("/api/v1/app/injuries/areas", json={"label": "足弓"})
    m = api.get("/api/v1/app/injuries/meta?lang=en").json()
    assert _cjk(m, allow={"足弓"}) == [], _cjk(m, allow={"足弓"})
    assert {a["key"]: a["label"] for a in m["areas"]}["shin_calf"] == "Calf / shin"
    assert any(a["label"] == "足弓" and a["custom"] for a in m["areas"])          # the user's own label, as written
    assert m["severity_help"]["severe"] == "stop running" and m["kinds"]["acute"] == "Acute"
    z = api.get("/api/v1/app/injuries/meta?lang=zh-TW").json()
    assert z["areas"][0]["label"] == "膝" and z["severities"] == {"mild": "輕", "moderate": "中", "severe": "重"}
    assert z["sides"]["both"] == "兩側" and z["statuses"]["draft"] == "待補細節"


def test_the_list_and_timeline_in_english(api):
    api.post("/api/v1/app/injuries", json={"area": "knee", "side": "right", "severity": "moderate",
                                            "onset_date": "2026-08-01"})
    api.post("/api/v1/app/injuries", json={"category": "illness", "illness": "fever", "onset_date": "2026-09-10"})
    lst = api.get("/api/v1/app/injuries?lang=en").json()["injuries"]
    assert _cjk(lst) == [], _cjk(lst)
    knee = next(x for x in lst if x["area"] == "knee")
    assert knee["label"] == "Knee (right)" and knee["kind_label"] == "Overuse" and knee["status_label"] == "Ongoing"
    assert next(x for x in lst if x["category"] == "illness")["label"].startswith("Illness")
    t = api.get("/api/v1/app/injuries/timeline?begin=2026-01-01&end=2026-09-30&lang=en").json()
    assert _cjk(t) == [], _cjk(t)
    zh = api.get("/api/v1/app/injuries?lang=zh-TW").json()["injuries"]
    assert next(x for x in zh if x["area"] == "knee")["label"] == "右膝"


def test_the_analysis_in_english():
    ds, evs, today = _boosted()
    evs[1]["status"] = "draft"
    D = _daily(ds, today)
    with use_locale("en"):
        an = IE.analysis(ds, evs, today, D=D)
    left = _cjk(an)
    assert left == [], left[:5]
    run_h = next(r for r in an["metrics"] if r["key"] == "run_h")
    assert run_h["label"] == "Running time" and run_h["help"].startswith("Total running time")
    assert next(x for x in an["excluded"] if x["id"] == 2)["why"].startswith("Goes into the analysis")
    long_ = next(r for r in an["metrics"] if r["key"] == "long")
    assert (long_["unit"], long_["unit_one"]) == ("times", "time")
    with use_locale("en"):                                                  # fmt reads the request's language
        assert IE.fmt("b2b", 1) == "1 day" and IE.fmt("b2b", 3) == "3 days" and IE.fmt("run_h", None) == "unknown"
    zh = IE.analysis(ds, evs, today, D=D)
    assert next(r for r in zh["metrics"] if r["key"] == "run_h")["label"] == "跑步時間"
    assert IE.fmt("b2b", 3) == "3 天"


def test_the_pattern_alert_in_english():
    ds, evs, today = _boosted()
    D = _daily(ds, today)
    hot = {"run_h": 99.0}
    ctrl = {"run_h": [1.0] * 30}
    with use_locale("en"):
        an = IE.analysis(ds, evs, today, D=D)
        alerts = IE.pattern_alerts(an, hot, ctrl, today)
    assert alerts and _cjk(alerts) == [], _cjk(alerts)
    assert alerts[0]["title"] == "Running time looks like before your injuries"


def test_labels_and_planner_lines_in_english():
    e = {"id": 12, "area": "shin_calf", "side": "both", "severity": "severe", "status": "active",
         "onset_date": "2026-09-20", "pause_quality": True}
    with use_locale("en"):
        assert INJ.full_label("shin_calf", "both") == "Calf / shin (both sides)"
        assert INJ.area_label("unknown") == "Area not set" and INJ.area_label("足弓") == "足弓"
        assert INJ.pause_reason([e], date(2026, 9, 25)) == "Injury log #12 (Calf / shin (both sides)) ongoing: no intervals for now"
        n = INJ.week_notes([e], date(2026, 9, 21), date(2026, 9, 25))[0]["text"]
        assert n == "Calf / shin (both sides) ongoing (day 6, Severe: stop running)"
        j = INJ.event_json(e, date(2026, 9, 25))
        assert _cjk(j) == [], _cjk(j)
    # Chinese unchanged
    assert INJ.full_label("shin_calf", "both") == "雙側小腿／脛骨" and INJ.full_label("knee", "right") == "右膝"
    assert INJ.pause_reason([e], date(2026, 9, 25)) == "傷病紀錄 #12（雙側小腿／脛骨）進行中：先不排間歇"
    assert INJ.week_notes([e], date(2026, 9, 21), date(2026, 9, 25))[0]["text"] == "雙側小腿／脛骨進行中（第 6 天，重：停跑）"


def test_the_recurrence_note_is_stored_in_chinese():
    """The note joins the user's text in the DB: the source language whatever the request's."""
    prev = {"id": 3, "area": "knee", "status": "resolved", "resolved_date": "2026-09-01", "onset_date": "2026-08-01"}
    with use_locale("en"):
        r = INJ.recurrence({"area": "knee", "onset_date": "2026-09-11"}, [prev])
    assert r["note"] == "距上次好了 10 天、同部位（#3）"


def test_the_safety_texts_keep_their_warning_in_english():
    with use_locale("en"):
        m = INJ.monitor({"condition": None, "area": "shin_calf"})
        assert "doctor" in m["rule"].lower() and "doctor" in m["text"].lower()
        assert "not a medical diagnosis" in m["disclaimer"].lower()
