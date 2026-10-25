"""SP-831: the share page in English — the three built-in examples (backend/demo/example_share*.json, frozen in
Chinese) are read in the request's language (share.localized, i18n/reverse.py): the title, the effort word, the
推估 badge, the 補給 card, the fuel schedule's actions, the warnings and the segment targets' names / 補給.
The files are not touched and the numbers stay as they are; Chinese is unchanged. The calculator's own words
the share page shows (the effort word, the carbohydrate line, the segment names, the fuel warnings) follow
the request's language too, so a share made from the English page reads in English.

No WKO5 folder, no DB: the examples are repo files; the engine checks call the pure functions."""
from __future__ import annotations

import json
import re

import pytest

from backend.engine.racepower import share as SH
from backend.i18n import use_locale
from backend.i18n.reverse import from_source
from backend.scripts import i18n_extract as X
from backend.tests.test_en_leftovers_sp921 import STATIONS, _plan, client  # noqa: F401  (client: the calculator API)

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
STATIC = X.ROOT / "backend" / "static"


def _cjk(texts) -> list:
    return [t for t in texts if isinstance(t, str) and CJK.search(t)]


# ---- reading a frozen Chinese text back --------------------------------------------------------------------

def test_a_msgid_and_a_msgid_with_fields_read_back():
    assert from_source("推估", "en") == "Est."
    assert from_source("全程沒有水站：約帶 3950–5500 ml", "en") == "No water stations on the course: carry about 3950–5500 ml"
    # a number field read back as the number its format wrote (7.1% → 0.071 → {d:.1%})
    t = from_source("預估脫水約 7.1% 體重（流汗推估 8.3 L，喝 3.9 L）：> 2 % 表現會掉，熱天往水量上限靠，但不要喝到體重增加", "en")
    assert t.startswith("Estimated dehydration about 7.1% of body weight (sweat estimate 8.3 L, drinking 3.9 L)")


def test_two_msgids_in_one_text_and_a_chinese_field():
    # 「約 {g} g 碳水（{food}）」 + 「 ＋ 喝 {ml}」, the food a msgid of its own
    assert (from_source("約 25 g 碳水（一包能量膠或等量） ＋ 喝 350–450 ml", "en")
            == "about 25 g carbs (a gel or the equivalent) + drink 350–450 ml")
    # two actions joined by _("；")
    assert (from_source("全程沒有水站：約帶 3950–5500 ml；吃 2 次（每次約 25 g 碳水）", "en")
            == "No water stations on the course: carry about 3950–5500 ml; Eat 2× (about 25 g carbs each)")


def test_the_athletes_own_name_stays():
    assert (from_source("補給站（食物＋水）「CP1 補給」：補水、吃，到下一個有水的站約帶 800–1000 ml", "en")
            == 'Aid station (food + water) "CP1 補給": refill water, eat, carry about 800–1000 ml to the next station with water')


def test_nothing_matched_or_nothing_to_do():
    assert from_source("我自己寫的賽事名稱", "en") is None
    assert from_source("全程沒有水站：約帶 3950–5500 ml", "zh-TW") is None
    assert from_source("No Chinese here", "en") is None
    assert from_source(None, "en") is None


def test_a_long_text_is_quick():
    import time
    t0 = time.perf_counter()
    assert from_source("，".join(f"我的、補給（站）＋ 一段{i}" for i in range(20)), "en") is None
    assert from_source("；".join(["吃 2 次（每次約 25 g 碳水）"] * 12), "en") == "; ".join(["Eat 2× (about 25 g carbs each)"] * 12)
    assert time.perf_counter() - t0 < 2.0


def test_the_calculators_rows_read_back_as_the_english_page_writes_them(client):
    # the same plan computed in both languages (test_en_leftovers_sp921's fake athlete): every Chinese text a
    # share keeps, read back, gives the English one
    body = {"type": "road", "distance_km": 42.195, "course": {"manual": {"km": 42.195, "split": "km"}},
            "start_time": "07:00", "env_to": {"temp_c": 26, "rh_pct": 75}, "stops": STATIONS}
    zh, en = _plan(client, body, "zh-TW"), _plan(client, body, "en")
    pairs = list(zip([e["action"] for e in zh["fuel"]["schedule"]], [e["action"] for e in en["fuel"]["schedule"]]))
    pairs += list(zip([s.get("fuel_action") or "" for s in zh["segments"]], [s.get("fuel_action") or "" for s in en["segments"]]))
    pairs += [(zh["fuel"]["label"], en["fuel"]["label"]), (zh["effort"]["label"], en["effort"]["label"])]
    pairs += [(a["text"], b["text"]) for a, b in zip(zh["fuel"]["warnings"], en["fuel"]["warnings"])]
    assert len(pairs) > 20
    bad = [(z, from_source(z, "en"), e) for z, e in pairs if z and (from_source(z, "en") or z) != e]
    assert bad == []


def test_a_trail_plans_rows_and_warnings_read_back(client):
    from backend.engine.racepower import gpx as GPX
    from backend.tests.test_racepower_v2 import synthetic_track
    tr = synthetic_track({"len": 30000, "z": lambda x: 300 + (x * 0.08 if x < 9000 else max(0, 720 - (x - 9000) * 0.04))})
    up = client.post("/api/v1/racepower/course",
                     files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()
    body = {"type": "trail", "course": {"course_id": up["course_id"]}, "start_time": "06:00",
            "env_to": {"temp_c": 30, "rh_pct": 70}}
    zh, en = _plan(client, body, "zh-TW"), _plan(client, body, "en")
    pairs = [(a["text"], b["text"]) for a, b in zip(zh["fuel"]["warnings"], en["fuel"]["warnings"])]
    assert any(t.startswith("沒有輸入補給站") for t, _e in pairs)
    pairs += [(a.get(k), b.get(k)) for a, b in zip(zh["chart_rows"], en["chart_rows"]) for k in ("label", "fuel")]
    pairs += [(a["action"], b["action"]) for a, b in zip(zh["fuel"]["schedule"], en["fuel"]["schedule"])]
    bad = [(z, from_source(z, "en"), e) for z, e in pairs if z and (from_source(z, "en") or z) != e]
    assert bad == []


# ---- the built-in examples ---------------------------------------------------------------------------------

def _shown(sid: str, loc: str) -> dict:
    with use_locale(loc):
        return SH.with_rows(SH.localized(SH.example(sid)))


def _shown_texts(s: dict) -> list:
    F = s.get("fuel") or {}
    ld = F.get("loading") or {}
    out = [s.get("title"), (s.get("summary") or {}).get("badge"), (s.get("effort") or {}).get("label"), F.get("label"),
           (F.get("cho") or {}).get("label"), ld.get("label"), ld.get("when"), ld.get("amount"), ld.get("note")]
    out += [e.get("action") for e in F.get("schedule") or []]
    out += [w.get("text") if isinstance(w, dict) else w for w in F.get("warnings") or []]
    out += [r.get(k) for r in s.get("chart_rows") or [] for k in ("label", "fuel", "badge")]
    return out


@pytest.mark.parametrize("sid", sorted(SH.EXAMPLES))
def test_an_example_reads_in_english(sid):
    en = _shown(sid, "en")
    assert _cjk(_shown_texts(en)) == [], sid
    assert en["title"].startswith("Demo ") and en["title"].endswith("(fictional runner)")


@pytest.mark.parametrize("sid", sorted(SH.EXAMPLES))
def test_the_numbers_stay_and_chinese_is_unchanged(sid):
    raw = SH.example(sid)
    zh = _shown(sid, "zh-TW")
    assert zh == SH.with_rows(raw)                       # Chinese: the example as it was
    en = _shown(sid, "en")
    assert en["summary"] == {**zh["summary"], **({"badge": en["summary"]["badge"]} if "badge" in zh["summary"] else {})}
    assert en["effort"]["key"] == zh["effort"]["key"] and en["effort"].get("f") == zh["effort"].get("f")
    num = ("start_km", "end_km", "pace_s_per_km", "power", "power_ref", "hr_ref", "hr_cap", "t", "cum_s", "kind", "basis")
    assert [{k: r.get(k) for k in num} for r in en["chart_rows"]] == [{k: r.get(k) for k in num} for r in zh["chart_rows"]]
    assert [e.get("water_ml") for e in en["fuel"]["schedule"]] == [e.get("water_ml") for e in zh["fuel"]["schedule"]]
    assert en["fuel"]["kcal_band"] == zh["fuel"]["kcal_band"]


def test_the_example_file_is_not_touched():
    before = {sid: p.read_bytes() for sid, p in SH.EXAMPLES.items()}
    for sid in SH.EXAMPLES:
        _shown(sid, "en")
    assert {sid: p.read_bytes() for sid, p in SH.EXAMPLES.items()} == before


def test_the_effort_word_and_the_carb_line_come_from_their_keys():
    en = _shown("example-baiyue-3day", "en")
    zh = SH.example("example-baiyue-3day")
    assert zh["fuel"]["label"] == "百岳行進間（推估）"      # frozen before 百岳 → 登山 (SP-738): read from the category
    assert en["fuel"]["label"] == "While hiking (estimate)" and en["effort"]["label"] == "Stable"


@pytest.fixture()
def share_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.request_context import RequestContextMiddleware
    app = FastAPI()
    app.include_router(RP.share_router)
    app.add_middleware(RequestContextMiddleware)
    return TestClient(app)


def test_the_share_data_of_an_example_follows_the_language(share_client):
    en = share_client.get("/share/example-trail-50k/data?lang=en").json()
    assert en["title"] == "Demo mountain trail 50K (fictional runner)" and _cjk(_shown_texts(en)) == []
    zh = share_client.get("/share/example-trail-50k/data?lang=zh-TW").json()
    assert zh["title"] == "示範山徑越野 50K（虛構跑者）" and zh["effort"]["label"] == "輕鬆"


def test_the_link_preview_of_an_example_in_english(share_client):
    page = share_client.get("/share/example-road-half?lang=en").text
    assert 'property="og:title" content="Demo city half marathon (fictional runner)"' in page
    zh = share_client.get("/share/example-road-half?lang=zh-TW").text
    assert 'property="og:title" content="示範城市半程馬拉松（虛構跑者）"' in zh


# ---- a user's own share (Q1: read in the request's language too, the file untouched) -----------------------

def _users_share(client, tmp_path, monkeypatch, title: str) -> str:
    monkeypatch.setattr(SH, "SHARES_DIR", tmp_path)
    body = {"type": "road", "distance_km": 42.195, "course": {"manual": {"km": 42.195, "split": "km"}},
            "start_time": "07:00", "env_to": {"temp_c": 26, "rh_pct": 75}, "stops": STATIONS}
    plan = _plan(client, body, "zh-TW")                 # made on the Chinese page
    return SH.save(SH.snapshot(plan, title=title, request=body))


def test_a_users_chinese_share_reads_in_english(client, share_client, tmp_path, monkeypatch):
    sid = _users_share(client, tmp_path, monkeypatch, "我的春季全馬")
    stored = (tmp_path / f"{sid}.json").read_bytes()
    en = share_client.get(f"/share/{sid}/data?lang=en").json()
    texts = _shown_texts(en)
    # what the athlete typed stays as written: the title and a station's own name (CP1 補給)
    assert en["title"] == "我的春季全馬"
    assert _cjk([t.replace("CP1 補給", "") for t in texts[1:] if isinstance(t, str)]) == []
    assert any('"CP1 補給": refill water, eat' in (e["action"] or "") for e in en["fuel"]["schedule"])
    zh = share_client.get(f"/share/{sid}/data?lang=zh-TW").json()
    assert zh["fuel"]["schedule"] == json.loads(stored)["fuel"]["schedule"] and zh["title"] == "我的春季全馬"
    assert en["summary"]["time_s"] == zh["summary"]["time_s"]
    assert (tmp_path / f"{sid}.json").read_bytes() == stored         # the stored file is not touched


def test_an_untitled_share_and_unknown_text(client, share_client, tmp_path, monkeypatch):
    sid = _users_share(client, tmp_path, monkeypatch, "")
    p = tmp_path / f"{sid}.json"
    snap = json.loads(p.read_text("utf-8"))
    snap["fuel"]["schedule"][0]["action"] = "在登山口加滿水（我自己寫的）"      # a text no msgid reads
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    en = share_client.get(f"/share/{sid}/data?lang=en").json()
    assert en["title"] == "Race plan" and en["fuel"]["schedule"][0]["action"] == "在登山口加滿水（我自己寫的）"
    assert 'content="Race plan"' in share_client.get(f"/share/{sid}?lang=en").text
    with use_locale("en"):
        assert [r["title"] for r in SH.listing(tmp_path)] == ["Race plan"]


# ---- the calculator's words a share shows ------------------------------------------------------------------

def test_the_calculators_words_follow_the_language():
    from backend.engine.racepower import difficulty as DF
    from backend.engine.racepower import fuel as FU
    from backend.engine.racepower import seg_targets as ST
    with use_locale("en"):
        assert DF.label_of(0.5)["label"] == "Easy" and DF.label_of(1.2)["label"] == "Over"
        assert DF.hike_effort(1.0, [0.8, 1.0, 1.2, 1.4])["label"] == "Stable"
        assert FU.carb_target("ultra")["label"].startswith("Over 6 h")
        assert ST.label_of({"grade": 0.0}, "flat") == "Flat / runnable" and ST.label_of({"grade": 0.0}, "flat", hike=True) == "Gentle"
    with use_locale("zh-TW"):
        assert DF.label_of(0.85)["label"] == "穩定" and FU.carb_target("half")["label"] == "半馬級（< 2–2.5 h）"
        assert ST.label_of({"grade": 0.05}, "run_climb") == "可跑的爬坡"


def test_the_ree_warning_never_goes_into_a_share_whatever_its_language():
    from backend.engine import hints as H
    plan = {"type": "baiyue", "summary": {}, "fuel": {"warnings": [
        H.warning(H.tag("RC28", "Resting energy is missing height: assumed values")),
        H.warning(H.tag("RC22", "Moving energy: Pandolf 1 kcal and Yamamoto 2 kcal differ by more than 15 %"))]}}
    snap = SH.snapshot(plan, title="t")
    assert [w["id"] for w in snap["fuel"]["warnings"]] == ["RC22"]


# ---- the page and its 分段目標 component ---------------------------------------------------------------------

def test_the_segment_component_has_no_hardcoded_chinese_and_its_keys_exist():
    src = (STATIC / "seg_profile.js").read_text("utf-8")
    assert X.scan_js_source("seg_profile.js", src).unwrapped == []
    keys = {k for k in re.findall(r'\bT\("([\w.]+)"', src) if not k.endswith((".", "_"))}    # "x." + k: a family
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "racepower.json").read_text("utf-8"))
    assert keys and [k for k in sorted(keys) if k not in zh] == [] and [k for k in sorted(keys) if not en.get(k)] == []
