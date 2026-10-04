"""SP-46: the dated threshold rows (plan.thresholds: LTHR / AeT / CP, plus 最大／靜息心率)
are edited on the settings page; the 賽事周期 page only shows what is in effect and links
there. Same data, same PUT /thresholds, same 「from its date on」 semantics.
Uses a temp plan.json (never the user's) and no WKO5 folder."""
import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.engine import planning as PL

STATIC = Path(__file__).resolve().parents[1] / "static"
T = PL.Threshold


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.api import plan as API
    path = tmp_path / "plan.json"
    PL.Plan(thresholds=[
        T("2999-01-01", lthr=170.0, note="future"),                   # not in effect yet
        T("2026-01-01", lthr=160.0, aethr=140.0, cp=220.0, note="test"),
        T("2026-03-01", mhr=195.0, rhr=48.0, note=API.HR_NOTE),       # 設定 → 心率 row
    ]).save(path)
    load, save = PL.Plan.load.__func__, PL.Plan.save
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, p=path: load(cls, p)))
    monkeypatch.setattr(PL.Plan, "save", lambda self, p=path: save(self, p))
    monkeypatch.setattr(API, "_notify", lambda thresholds: None)
    monkeypatch.setattr(API, "_wko5_settings",
                        lambda: {"runthr": None, "runmhr": None, "bikethr": None, "runtpace": None, "mftp": None})
    app = FastAPI()
    app.include_router(API.router)
    c = TestClient(app)
    c.path = path
    return c


def test_get_thresholds_is_the_plan_rows_and_what_is_in_effect(client):
    r = client.get("/api/v1/plan/thresholds")
    assert r.status_code == 200
    d = r.json()
    assert [t["date"] for t in d["thresholds"]] == ["2026-01-01", "2026-03-01", "2999-01-01"]   # by date
    ef = d["effective_thresholds"]
    # a row applies from its date on: the 2999 LTHR is not in effect today
    assert (ef["lthr"]["value"], ef["aethr"]["value"], ef["cp"]["value"]) == (160.0, 140.0, 220.0)
    assert ef["mhr"] == {"value": 195.0, "source": "plan"}
    assert d["power_zones"]["zones"] and d["today"] and "runthr" in d["wko5_settings"]
    # the same data the 賽事周期 page reads
    full = client.get("/api/v1/plan").json()
    assert full["thresholds"] == d["thresholds"] and full["effective_thresholds"] == ef


def test_settings_round_trip_keeps_the_max_hr_rows(client):
    """The settings table hides rows holding only 最大／靜息心率 but PUTs them back."""
    rows = client.get("/api/v1/plan/thresholds").json()["thresholds"]
    rows[0]["lthr"], rows[0]["lthr_method"] = 162, "manual"
    assert client.put("/api/v1/plan/thresholds", json=rows).status_code == 200
    got = {t.date: t for t in PL.Plan.load(client.path).thresholds}
    assert got["2026-01-01"].lthr == 162 and got["2026-01-01"].lthr_method == "manual"
    assert (got["2026-03-01"].mhr, got["2026-03-01"].rhr) == (195, 48)


def _keys(src: str, ns: str) -> set[str]:
    return set(re.findall(rf'"{ns}\.(thr\.[\w.]+)"', src)) | set(re.findall(rf':{ns}\.(thr\.[\w.]+)', src))


def test_settings_page_edits_the_table_without_max_hr():
    s = (STATIC / "settings.html").read_text("utf-8")
    sec = s[s.index('<section id="thresholds">'):]
    sec = sec[:sec.index("</section>")]
    assert 'id="thr"' in sec and 'id="thr-save"' in sec and 'id="thr-est"' in sec and 'id="thr-pz"' in sec
    thead = sec[sec.index("<thead>"):sec.index("</thead>")]
    assert "最大心率" not in thead and "settings.thr.lthr" in thead       # 最大心率 only in 心率 (one place)
    assert s.index('<section id="hr">') < s.index('<section id="thresholds">') < s.index('<section id="sync">')
    js = s[s.index("// ---- 閾值測試紀錄"):]
    assert 'const THR = "/api/v1/plan/thresholds"' in js and 'method: "PUT"' in js
    assert 'data-f="mhr"' not in s and '"mhr"' not in js.split("$(\"thr\").addEventListener(\"click\"")[0]
    assert "loadThr();          // max / resting HR" in s                   # 心率 saves reload the table
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "settings.json").read_text("utf-8"))
        assert all(cat.get(k) for k in _keys(s, "settings")), loc


def test_plan_page_shows_a_read_only_summary_with_a_link():
    p = (STATIC / "plan.html").read_text("utf-8")
    sec = p[p.index('<section id="thresholds">'):]
    sec = sec[:sec.index("</section>")]
    assert 'href="/api/v1/wko5/settings#thresholds"' in sec and "<input" not in sec and "<button" not in sec
    assert '"/thresholds"' not in p and "savethr" not in p and "threshold-estimate" not in p
    assert "到下方填測試結果" not in p
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "plan.json").read_text("utf-8"))
        assert all(cat.get(k) for k in _keys(p, "plan")), loc


def test_pages_render_the_section_in_english():
    from backend.i18n.pages import render
    assert "Threshold tests (LTHR / AeT / CP)" in render("settings", "en", False)
    out = render("plan", "en", False)
    assert '<h2 data-i18n="plan.thr.title">Threshold tests (LTHR / AeT / CP)</h2>' in out
    assert '#thresholds" data-i18n="plan.thr.edit">Edit in Settings</a>' in out
