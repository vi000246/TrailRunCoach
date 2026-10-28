"""SP-838 (838 back-a): the race calculator's result texts from the server in English — the 補給 card (the
carbohydrate / water / sodium rules and their sources, the schedule's actions, the stations), the segment
targets (terrain names, chips, explanations), the effort words, the plan's warnings and badges, the
per-segment heat correction and the stop / pack texts. With ?lang=en the /plan answer has no Chinese; the
Chinese answer is the one it was (same numbers, same structure, the source texts).

Synthetic courses and inputs only (the test_racepower_v2 / test_racepower_export fakes)."""
from __future__ import annotations

import re

import pytest

from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import fuel as FU
from backend.engine.racepower import seg_targets as ST
from backend.i18n import M_, use_locale

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
FILES = ["backend/engine/racepower/" + f + ".py" for f in (
    "planner", "fuel", "seg_targets", "intensity", "heatacc", "hrcap", "difficulty", "hike", "pack_defaults",
    "course", "nonmoving", "night", "climb", "group", "memory", "calc")] + ["backend/engine/terrain_difficulty.py"]


def _strings(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(x, str):
        yield path, x


def _cjk(x) -> list:
    return [(p, s) for p, s in _strings(x) if CJK.search(s)]


def _shape(x):
    """The answer with every text blanked: what must not change with the language."""
    if isinstance(x, dict):
        return {k: _shape(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_shape(v) for v in x]
    return "" if isinstance(x, str) else x


@pytest.fixture()
def api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.request_context import RequestContextMiddleware
    from backend.tests.test_racepower_v2 import BT, GM, RE0, fake_inputs
    inp = fake_inputs()
    # the fake athlete's source words are the fixture's own Chinese: plain ones here
    inp["weight"]["source"] = inp["aet"]["source"] = "test"
    inp["cp"]["sources"][0]["label"] = "activities"
    inp["tte"]["source"] = "default"
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: inp)
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    RP._courses.clear()
    app = FastAPI()
    app.include_router(RP.router)
    app.add_middleware(RequestContextMiddleware)
    return TestClient(app)


def _course(api, km=30.0):
    from backend.engine.racepower import gpx as GPX
    from backend.tests.test_racepower_v2 import synthetic_track
    half = km * 500.0
    tr = synthetic_track({"len": km * 1000, "z": lambda x: 300 + (x * 0.12 if x < half else (2 * half - x) * 0.12)})
    r = api.post("/api/v1/racepower/course", files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")})
    assert r.status_code == 200, r.text
    return r.json()["course_id"]


def _bodies(cid):
    from backend.tests.test_racepower_export import hot_later, rows_from
    stops = [{"km": 8, "type": "water"}, {"km": 15, "type": "aid", "name": "CP2"}, {"km": 22, "type": "big"},
             {"km": 26, "type": "medical"}]
    return {
        "road_hourly": {"type": "road", "distance_km": 21.1, "mode": "auto", "date": "2026-10-04", "start_time": "06:00",
                        "course": {"manual": {"km": 21.1, "split": "km"}}, "stops": [{"km": 10, "minutes": 2}],
                        "hourly": rows_from(hot_later)},
        "road_short_no_start": {"type": "road", "distance_km": 10.0, "mode": "auto", "date": "2026-10-04",
                                "course": {"manual": {"km": 10.0, "split": "km"}}, "hourly": rows_from(hot_later)},
        "trail": {"type": "trail", "mode": "auto", "date": "2026-10-25", "start_time": "06:00",
                  "course": {"course_id": cid}, "stops": stops, "poles": True,
                  "heat_acclimatisation": {"mode": "partial"}},
        "trail_no_stops_power": {"type": "trail", "mode": "power", "target_power": 400, "date": "2026-10-25",
                                 "start_time": "06:00", "course": {"course_id": cid}},
        "baiyue": {"type": "baiyue", "mode": "auto", "date": "2026-10-25", "start_time": "05:00",
                   "course": {"course_id": cid}, "days": 1, "acclimatisation": "partial"},
    }


def test_the_files_have_no_unwrapped_chinese():
    from backend.scripts import i18n_extract as X
    s = X.scan_repo()
    # hike.is_yushan matches a route's name with 玉山 (data; the same word is UI text in racepower.html, so it
    # is not in allow_cjk.txt)
    left = {f: [t for _ln, t in s[f].unwrapped if not (f.endswith("/hike.py") and t == "玉山")] for f in FILES}
    assert {f: v for f, v in left.items() if v} == {}


def test_every_msgid_of_the_files_has_english():
    from backend.i18n import catalog
    from backend.scripts import i18n_extract as X
    en = catalog("en")
    s = X.scan_repo()
    missing = sorted({m for f in FILES + ["backend/api/racepower.py", "backend/engine/racepower/weather.py"]
                      for _ln, m in s[f].msgids if X.has_cjk(m) and not en.get(m)})
    assert missing == []


def test_the_plan_in_english_has_no_chinese_and_the_chinese_plan_is_unchanged(api):
    cid = _course(api)
    for name, body in _bodies(cid).items():
        en = api.post("/api/v1/racepower/plan?lang=en", json=body)
        zh = api.post("/api/v1/racepower/plan?lang=zh-TW", json=body)
        assert en.status_code == 200 and zh.status_code == 200, (name, en.text[:300])
        en, zh = en.json(), zh.json()
        assert _cjk(en) == [], name
        assert _shape(en) == _shape(zh), name            # the language changes only the texts
        assert _cjk(zh), name                            # the Chinese answer is still Chinese


def test_the_fuel_card_in_both_languages(api):
    cid = _course(api)
    body = _bodies(cid)["trail"]
    zh = api.post("/api/v1/racepower/plan?lang=zh-TW", json=body).json()["fuel"]
    en = api.post("/api/v1/racepower/plan?lang=en", json=body).json()["fuel"]
    assert zh["cho"]["label"] == FU.CHO_RULES[zh["category"]]["label"]          # the source text, as before
    assert zh["water"]["caution"] == "別喝到體重增加（低血鈉；NATA 2017、Hew-Butler 2015）"
    assert en["water"]["caution"].startswith("Don't drink so much")
    assert en["cho"]["label"] in ("Marathon / trail 2–6 h: glucose + fructose", "Over 6 h: start at 30–50, move toward 60–90 if your gut allows")
    zst = [e["action"] for e in zh["schedule"] if e["kind"] == "aid"]
    est_ = [e["action"] for e in en["schedule"] if e["kind"] == "aid"]
    assert any(a.startswith("水站：補水") for a in zst) and any(a.startswith("Water station: refill water") for a in est_)
    assert any(a.startswith("補給站（食物＋水）「CP2」：補水、吃") for a in zst)
    assert any(a.startswith('Aid station (food + water) "CP2": refill water, eat') for a in est_)
    assert any(a.startswith("醫護站（不補給）") or a.startswith("醫護站：不補給") for a in zst)
    start = next(e["action"] for e in en["schedule"] if e["kind"] == "start")
    assert start.startswith("Start: carry about")
    assert en["body_src"] == {"height_cm": "Est.", "age": "Est.", "sex": "Est."}
    assert zh["body_src"] == {"height_cm": "推估", "age": "推估", "sex": "推估"}


def test_a_fuel_point_at_a_food_station_joins_its_row_in_both_languages():
    segs = [{"i": i + 1, "start_km": i * 5.0, "end_km": (i + 1) * 5.0, "t": 1800.0, "cum_s": (i + 1) * 1800.0,
             "grade": 0.0, "power": 250.0, "temp_c": 15.0, "dist_m": 5000.0} for i in range(8)]
    plan = {"type": "trail", "segments": segs, "summary": {"time_s": 8 * 1800.0, "km": 40.0}}
    stops = [{"km": 10.0, "type": "aid"}]
    with use_locale("zh-TW"):
        zh = FU.plan_fuel({**plan, "segments": [dict(s) for s in segs]}, weight=60.0, stops=stops)
    with use_locale("en"):
        en = FU.plan_fuel({**plan, "segments": [dict(s) for s in segs]}, weight=60.0, stops=stops)
    za = next(e["action"] for e in zh["schedule"] if e["kind"] == "aid")
    ea = next(e["action"] for e in en["schedule"] if e["kind"] == "aid")
    assert re.fullmatch(r"補給站（食物＋水）：補水、吃，到下一個有水的站約帶 \S+ ml；吃約 30 g 碳水（能量膠、香蕉或等量的固體） ＋ 喝 \S+ ml", za), za
    assert re.fullmatch(r"Aid station \(food \+ water\): refill water, eat, carry about \S+ ml to the next station with water; "
                        r"eat about 30 g carbs \(a gel, a banana or the equivalent in solid food\) \+ drink \S+ ml", ea), ea
    assert all("_eat" not in e for e in zh["schedule"] + en["schedule"])


def test_effort_words_and_target_chips():
    with use_locale("en"):
        assert [DF.label_of(f)["label"] for f in (0.5, 0.85, 0.93, 0.99, 1.2)] == ["Easy", "Stable", "Hard", "Max", "Over"]
    assert [DF.label_of(f)["label"] for f in (0.5, 0.85, 0.93, 0.99, 1.2)] == ["輕鬆", "穩定", "吃力", "極限", "超出"]
    plan = {"type": "trail", "used": {"cp": {"value": 300.0}},
            "segments": [{"i": 1, "grade": 0.12, "t": 600.0, "gain_m": 100.0, "pace_s_per_km": 600.0},
                         {"i": 2, "grade": 0.0, "t": 600.0, "power": 250.0, "pace_s_per_km": 300.0}],
            "summary": {"time_s": 7200.0}}
    with use_locale("en"):
        en = ST.plan_targets({**plan, "segments": [dict(s) for s in plan["segments"]]}, aet=140.0, lthr=165.0)
    zh = ST.plan_targets({**plan, "segments": [dict(s) for s in plan["segments"]]}, aet=140.0, lthr=165.0)
    assert zh[0]["label"] == "陡坡（走）" and zh[0]["chips"][0]["text"] == "心率 ≤ 165" and zh[0]["badge"] == "推估"
    assert en[0]["label"] == "Steep climb (walk)" or not CJK.search(en[0]["label"])
    assert en[0]["chips"][0]["text"] == "HR ≤ 165" and en[0]["badge"] == "Est."
    assert zh[1]["chips"][1]["text"] == "心率上限 165" and en[1]["chips"][1]["text"] == "HR cap 165"
    assert zh[1]["ref"] == ["配速參考 5:00 /km"] and en[1]["ref"] == ["Pace reference 5:00 /km"]
    assert not CJK.search(en[1]["src"]) and zh[1]["src"] == ST.SRC["flat"]


def test_cached_texts_follow_the_request_language():
    """py() (every /plan, /inputs answer) shows an M_() text — a cached result's — in the request language."""
    from backend.engine.racepower import calc as CALC
    from backend.engine.racepower import intensity as I
    with use_locale("zh-TW"):
        c = I.classify(None, None, None)               # computed (and cached) in Chinese ...
    assert c["reason"] == "沒有資料"
    with use_locale("en"):
        assert CALC.py(c)["reason"] == "No data"      # ... shown in English
        assert CALC.py({"x": [M_("推估")]}) == {"x": ["Est."]}


def test_the_capacity_hike_plan_in_english_with_a_code_for_the_default_pack(monkeypatch):
    """The 百岳 plan on the walking capacity (planner.plan_hike): its texts in English, the Chinese one as
    before; pack_src_kind says manual / default for code (the page tested pack_src.startsWith("預設"))."""
    from backend.engine import calibrate as CAL
    from backend.engine.racepower import calc as CALC
    from backend.engine.racepower import pack_defaults as PD
    from backend.tests.test_group_itinerary_sp578 import plan
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)
    PD.forget()
    with use_locale("en"):
        en = CALC.py(plan(acclimatisation="partial", days=2))
        en_manual = CALC.py(plan(pack_kg=12.0))
    zh = CALC.py(plan(acclimatisation="partial", days=2))
    PD.forget()
    assert _cjk(en) == [] and _cjk(en_manual) == []
    assert _shape(en) == _shape(zh)
    assert en["summary"]["pack_src_kind"] == zh["summary"]["pack_src_kind"] == "default"
    assert en_manual["summary"]["pack_src_kind"] == "manual" and en_manual["summary"]["pack_src"] == "Manual"
    assert zh["summary"]["pack_src"].startswith("預設背負") and en["summary"]["pack_src"].startswith("Default pack")
    assert any(w.startswith("沒填背負：用預設背負") for w in zh["warnings"])
    assert any(w.startswith("No pack weight entered: using Default pack") for w in en["warnings"])


def test_api_errors_in_english(api):
    r = api.get("/api/v1/racepower/weather?peak=NoSuchPeak&lang=en")
    assert r.status_code == 404 and r.json()["detail"] == "Peak not found: NoSuchPeak"
    assert api.get("/api/v1/racepower/weather?peak=NoSuchPeak&lang=zh-TW").json()["detail"] == "找不到山名 NoSuchPeak"
