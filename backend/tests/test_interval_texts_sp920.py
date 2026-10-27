"""間歇頁卡片標題與 ? 說明對偵測到的趟也別寫「目標」 (SP-920).

SP-849 made the 每趟功率 card's legend / frame / tip name the band detected reps (no 課表) fell in, but the
card's title 「每趟功率 vs 目標帶」 and its ? text 「綠框 = 這次課表的目標」 are views/workout.json's fixed
words, and wko5views._render puts them over whatever the card says. What these lock: a card that says
its reps were detected (`text_source`) gets the view's `by_source.detected` title / ? text — 每趟功率 and
功率、無氧儲備與心率 — in Chinese and in English (views/i18n sidecar); a planned session keeps the view's
own words."""
import datetime as dt
import json
from pathlib import Path

from backend.api.wko5views import _render
from backend.engine.wko5expr import customviews as CV
from backend.engine.wko5expr import viewi18n as VI
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds
from backend.tests.test_rep_profile_detected_sp849 import _detected

VIEW = Path(__file__).resolve().parents[2] / "views" / "workout.json"


def _charts(view: dict) -> dict:
    return {c["id"]: c for d in view["dashboards"] for c in d.get("charts", []) if c.get("id")}


def _view() -> dict:
    """The bundled 單次活動判讀 view as the app reads it (customviews.parse_view keeps only the keys it knows)."""
    return CV.parse_view(json.loads(VIEW.read_text("utf-8")), VIEW)


def _card(ch: dict, ds, w) -> dict:
    return _render(ch, ds, 0.0, 0.0, None, w)


def test_detected_reps_get_the_views_detected_title_and_text():
    ds, w = _detected(260.0)                                    # 104 % CP: 超閾值, no 課表
    cs = _charts(_view())
    r = _card(cs["interval-reps"], ds, w)
    assert r["rep_profile"]["source"] == "detected" and r["text_source"] == "detected"
    # SP-918: every band is drawn now, each rep by the band it fell in (was: the band of the reps' median)
    assert r["title"] == "每趟功率落在哪個強度帶", r["title"]
    assert "目標" not in r["title"]
    assert "課表的目標" not in r["description"] and "沒有目標" in r["description"], r["description"]
    assert "加框的是閾值帶" in r["description"], r["description"]
    p = _card(cs["interval-power"], ds, w)
    assert p["title"] == cs["interval-power"]["title"]          # no 目標 in it: the same title
    assert "目標帶" not in p["description"] and "偵測到的趟所在的強度帶" in p["description"], p["description"]


def test_a_planned_session_keeps_the_views_words():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    w = ds.workouts[0]
    cs = _charts(_view())
    r = _card(cs["interval-reps"], ds, w)
    assert "text_source" not in r
    assert r["title"] == "每趟功率 vs 目標帶" and r["description"] == cs["interval-reps"]["description"]
    p = _card(cs["interval-power"], ds, w)
    assert p["description"] == cs["interval-power"]["description"] and "灰帶 = 目標帶" in p["description"]


def test_every_detected_text_has_english():
    tr = VI.sidecar("en").get("workout") or {}
    cs = _charts(VI.translate_view(_view(), tr))
    alt = cs["interval-reps"]["by_source"]["detected"]
    assert alt["title"] == "Which intensity band each rep fell in", alt["title"]          # SP-918
    assert "target" not in alt["title"].lower()
    assert "no target" in alt["description"] and "this session's target" not in alt["description"]
    p = cs["interval-power"]["by_source"]["detected"]
    assert "not a target" in p["description"] and "grey band = target band" not in p["description"]
    # every by_source text in the bundled view has its English
    for cid, c in _charts(_view()).items():
        for src, txt in (c.get("by_source") or {}).items():
            for k in txt:
                assert ((tr.get("charts") or {}).get(cid, {}).get("by_source") or {}).get(src, {}).get(k), (cid, src, k)


def test_a_view_keeps_by_source_only_in_its_shape():
    import pytest
    raw = {"name": "x", "dashboards": [{"title": "d", "charts": [
        {"title": "c", "kind": "review", "section": "interval_reps", "by_source": {"detected": {"title": "偵測"}}}]}]}
    c = CV.parse_view(raw, Path("x.json"))["dashboards"][0]["charts"][0]
    assert c["by_source"] == {"detected": {"title": "偵測"}}
    raw["dashboards"][0]["charts"][0]["by_source"] = {"detected": {"subtitle": "x"}}
    with pytest.raises(CV.CustomViewError):
        CV.parse_view(raw, Path("x.json"))


def test_the_sidecar_translates_by_source_texts():
    view = {"dashboards": [{"id": "d", "charts": [
        {"id": "c", "title": "原", "by_source": {"detected": {"title": "偵測", "description": "說明"}}}]}]}
    tr = {"charts": {"c": {"by_source": {"detected": {"title": "Detected"}}}}}
    c = VI.translate_view(view, tr)["dashboards"][0]["charts"][0]
    assert c["by_source"]["detected"] == {"title": "Detected", "description": "說明"}
    assert view["dashboards"][0]["charts"][0]["by_source"]["detected"]["title"] == "偵測"   # a copy
