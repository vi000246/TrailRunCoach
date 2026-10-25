"""SP-921: the race calculator's English leftovers — the 「解除全部鎖定」 and 「分享」 buttons (and the share dialog
they open) are page text (static/i18n/<locale>/racepower.json), and the fuel schedule's actions — 「全程沒有水站：
約帶 … ml」, the start / station / fuel-point rows, the 百岳 day rows, and the segment table's 補給 column made of
them — follow the request's language (engine/racepower/fuel.py). A station's own name stays as the athlete wrote
it. Chinese is unchanged.

No WKO5 folder, no DB: the API runs on test_racepower_v2's fake inputs (test_racepower_export's client set-up)."""
from __future__ import annotations

import re

import pytest

from backend.engine.racepower import backtest as BT
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import grade_model as GM
from backend.i18n import pages as P
from backend.scripts import i18n_extract as X
from backend.tests.test_racepower_v2 import RE0, fake_inputs, synthetic_track

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
STATIC = X.ROOT / "backend" / "static"


def _no_cjk(texts, where: str, allow=()) -> None:
    bad = []
    for t in texts:
        s = t
        for a in allow:
            s = s.replace(a, "")
        if CJK.search(s):
            bad.append(t)
    assert not bad, f"{where}: still Chinese: {bad}"


@pytest.fixture()
def client(monkeypatch):
    """test_racepower_export's app plus the request-language middleware (?lang=en)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.request_context import RequestContextMiddleware
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    RP._courses.clear()
    app = FastAPI()
    app.include_router(RP.router)
    app.add_middleware(RequestContextMiddleware)
    return TestClient(app)


def _plan(client, body: dict, lang: str) -> dict:
    r = client.post(f"/api/v1/racepower/plan?lang={lang}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _texts(p: dict) -> list[str]:
    f = p["fuel"]
    return [e["action"] for e in f["schedule"]] + [s.get("fuel_action") or "" for s in p["segments"]]


# ---- the page --------------------------------------------------------------------------------------------

def _en_page() -> str:
    src = (STATIC / "racepower.html").read_text("utf-8")
    return P.apply_catalog(src, P.page_catalog(P.namespaces_for("racepower"), "en"))


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def test_unlock_and_share_buttons_are_english():
    page = _en_page()
    unlock = re.search(r'<button[^>]*id="unlock"[^>]*>(.*?)</button>', page).group(1)
    share = re.search(r'<button[^>]*id="share"[^>]*>(.*?)</button>', page).group(1)
    assert unlock == "Unlock all" and share == "🔗 Share"
    _no_cjk([unlock, share], "buttons")


def test_the_share_dialog_is_english():
    page = _en_page()
    dlg = re.search(r'<dialog id="share-dlg">(.*?)</dialog>', page, re.S).group(1)
    _no_cjk([_strip(dlg)], "share dialog")
    assert 'placeholder="e.g. spring half marathon plan"' in dlg
    # the dialog's script words (the list of shares, the messages) are catalog keys now
    js = page[page.index("// ---------------- read-only share links"):page.index("// ---------------- analyses")]
    _no_cjk(re.findall(r'"[^"\n]*"|`[^`]*`', js), "share script")
    cat = P.page_catalog(P.namespaces_for("racepower"), "en")
    for k in ("share.race_on", "share.expired", "share.expires", "share.no_expiry", "share.with_weight", "share.copy",
              "share.copied", "share.delete", "share.delete_confirm", "share.none", "share.making", "share.made"):
        assert f"racepower.{k}" in cat, k
        _no_cjk([cat[f"racepower.{k}"]], k)


def test_chinese_page_keeps_its_words():
    src = (STATIC / "racepower.html").read_text("utf-8")
    page = P.apply_catalog(src, P.page_catalog(P.namespaces_for("racepower"), "zh-TW"))
    assert re.search(r'id="unlock"[^>]*>解除全部鎖定<', page) and re.search(r'id="share"[^>]*>🔗 分享<', page)
    assert "刪除這個分享？拿到連結的人就看不到了。" == P.page_catalog(["racepower"], "zh-TW")["racepower.share.delete_confirm"]


# ---- the fuel schedule and the 補給 column ------------------------------------------------------------------

def test_no_water_station_row_in_english(client):
    tr = synthetic_track({"len": 30000, "z": lambda x: 300 + (x * 0.08 if x < 9000 else max(0, 720 - (x - 9000) * 0.04))})
    up = client.post("/api/v1/racepower/course",
                     files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()
    body = {"type": "trail", "course": {"course_id": up["course_id"]}, "start_time": "06:00",
            "env_to": {"temp_c": 28, "rh_pct": 70}}
    en = _plan(client, body, "en")
    start = next(e for e in en["fuel"]["schedule"] if e["kind"] == "start")
    assert start["action"].startswith("No water stations on the course: carry about ") and start["action"].endswith(" ml")
    assert start["action"] in en["segments"][0]["fuel_action"]
    _no_cjk(_texts(en), "trail, no stations (en)")
    zh = _plan(client, body, "zh-TW")
    assert next(e for e in zh["fuel"]["schedule"] if e["kind"] == "start")["action"].startswith("全程沒有水站：約帶 ")


STATIONS = [{"km": 10, "type": "aid", "name": "CP1 補給"}, {"km": 20, "type": "water"}, {"km": 25, "type": "medical"},
            {"km": 30, "type": "big"}, {"km": 35, "type": "self"}]


def test_stations_and_fuel_points_in_english(client):
    body = {"type": "road", "distance_km": 42.195, "course": {"manual": {"km": 42.195, "split": "km"}},
            "start_time": "07:00", "env_to": {"temp_c": 26, "rh_pct": 75}, "stops": STATIONS}
    en = _plan(client, body, "en")
    acts = [e["action"] for e in en["fuel"]["schedule"]]
    _no_cjk(_texts(en), "marathon with stations (en)", allow=("CP1 補給",))    # the athlete's own name stays
    assert any(a.startswith("Start: carry about ") for a in acts)
    assert any(a.startswith('Aid station (food + water) "CP1 補給": refill water, eat') for a in acts)
    assert any(a.startswith("Major aid station (electrolytes, hot food): refill water, eat, take electrolytes")
               for a in acts)
    assert any(a.startswith("Medical station: no supplies") for a in acts)
    assert any(re.match(r"about \d+ g carbs \(", a) for a in acts)
    assert any(", carry about " in a for a in acts)
    # a fuel point moved onto a food station is part of that station's row, in the same language
    merged = [a for a in acts if "; eat about " in a]
    assert merged, acts
    assert not any("_eat" in e for e in en["fuel"]["schedule"])
    zh = _plan(client, body, "zh-TW")
    zacts = [e["action"] for e in zh["fuel"]["schedule"]]
    assert any(a.startswith("補給站（食物＋水）「CP1 補給」：補水、吃") for a in zacts)
    assert any("；吃約 " in a for a in zacts) and any(a.startswith("出發：到第一個有水的站約帶 ") for a in zacts)


def test_drink_to_thirst_and_short_race_in_english(client):
    half = {"type": "road", "distance_km": 21.1, "start_time": "06:00", "course": {"manual": {"km": 21.1, "split": "km"}},
            "env_to": {"temp_c": 14, "rh_pct": 70}, "stops": [{"km": 10, "minutes": 1}, {"km": 15, "type": "medical"}]}
    en = _plan(client, half, "en")
    acts = [e["action"] for e in en["fuel"]["schedule"]]
    _no_cjk(_texts(en), "half, cool (en)")
    assert any(": drink to thirst" in a for a in acts) and any(a.endswith(" (no supplies)") for a in acts)
    short = {"type": "road", "distance_km": 8, "start_time": "06:00", "course": {"manual": {"km": 8, "split": "km"}}}
    en = _plan(client, short, "en")
    _no_cjk(_texts(en), "short road race (en)")
    assert any(e["action"].startswith("No need to eat") for e in en["fuel"]["schedule"])


def test_baiyue_day_rows_in_english(client):
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("h.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    body = {"type": "baiyue", "course": {"course_id": cid}, "day_splits_km": [8], "start_time": "05:30",
            "stops": [{"km": 4, "type": "water", "name": "山屋"}]}
    en = _plan(client, body, "en")
    acts = [e["action"] for e in en["fuel"]["schedule"]]
    _no_cjk(_texts(en), "baiyue (en)", allow=("山屋",))
    assert sum(a.startswith("Day ") and " start: drink about " in a for a in acts) == 2
    assert any(a.startswith("Trail snack, 30–50 g carbs") for a in acts)
    assert 'Water station "山屋": refill water' in acts
    zh = _plan(client, body, "zh-TW")
    zacts = [e["action"] for e in zh["fuel"]["schedule"]]
    assert any(a.startswith("第 1 天出發：今天約喝 ") for a in zacts) and "水站「山屋」：補水" in zacts
