"""SP-944: the 環境 card's server texts in English — the race-day weather chain's 嘗試 reasons (the CWA
登山 step said 「未使用」「未設定 CWA 授權碼」「… km 內沒有 CWA 登山預報點」… in every language) and the
From label (「Open-Meteo 歷史資料：近 90 天 N 次跑步…」, 「近 90 天 N 次跑步的中位海拔；溫濕度：…」). The
/inputs answer is one cache for every language (api/racepower.inputs), so the label is an M_() text the
endpoint shows in the request's language. Fakes only: no network, no WKO5 folder."""
import datetime as dt
import json
import re

import pytest

from backend.engine.racepower import athlete as A
from backend.engine.racepower import weather as WX
from backend.i18n import Msg, relocalize, use_locale
from backend.tests.test_race_township import RACE, Fake, _run

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _reasons(r: dict) -> list[str]:
    return [t["reason"] for t in r["tried"] if not t["ok"]]


@pytest.mark.parametrize("case", ["no_key", "no_cwa", "far_off", "no_mountain_point", "beyond_3_days"])
def test_the_tried_reasons_are_english_on_the_english_page(tmp_path, monkeypatch, case):
    monkeypatch.setattr(WX, "site_key", lambda *a, **k: None)
    kw = {"no_key": dict(key=None), "no_cwa": dict(use_cwa=False),
          "far_off": dict(date=RACE + dt.timedelta(days=9)),
          "no_mountain_point": {}, "beyond_3_days": dict(days=4)}[case]
    with use_locale("en"):
        r = _run(tmp_path, Fake(), **kw)
    reasons = _reasons(r)
    assert reasons
    assert not [s for s in reasons if CJK.search(s)], reasons


def test_the_chinese_page_keeps_the_chinese_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(WX, "site_key", lambda *a, **k: None)
    r = _run(tmp_path, Fake(), key=None)
    assert {"provider": "cwa", "ok": False, "reason": "未設定 CWA 授權碼"} in r["tried"]
    r = _run(tmp_path, Fake())
    assert {"provider": "cwa_hourly", "ok": False, "reason": "5 km 內沒有 CWA 登山預報點"} in r["tried"]


def test_the_fallback_label_follows_the_language():
    fb = A.fallback_training()
    assert isinstance(fb["label"], Msg) and "預設" in fb["label"]
    en = relocalize(fb["label"], "en")
    assert not CJK.search(en) and "default" in en, en


def test_the_open_meteo_label_comes_back_from_the_daily_cache_in_any_language(tmp_path, monkeypatch):
    """The archive answer is cached for the day in a JSON file: it keeps the count, not a text in one language."""
    monkeypatch.setattr(A, "TRAINING_ENV_CACHE", str(tmp_path / "tenv.json"))
    today = dt.date(2026, 10, 25)
    start = dt.datetime(2026, 10, 1, 7, 0)

    class W:
        def __init__(self, i):
            self.idx, self.metrics = i, {"duration": 3600}
            self.entry = type("E", (), {"start": start + dt.timedelta(days=i)})()
    runs = [W(i) for i in range(3)]
    metrics = {i: {"elev_median": 100.0 + i, "lat": 25.0, "lon": 121.5, "moving_s": 3600} for i in range(3)}
    monkeypatch.setattr(WX, "fetch_activities_conditions",
                        lambda lat, lon, windows: {"temp_c": 24.0, "rh_pct": 80.0, "activities": len(windows)})
    got = A._training_conditions(None, runs, metrics, today)
    assert got["provider"] == "open_meteo_archive" and "Open-Meteo 歷史資料" in got["label"]
    assert json.loads((tmp_path / "tenv.json").read_text("utf-8"))["values"]["activities"] == 3
    monkeypatch.setattr(WX, "fetch_activities_conditions", lambda *a: pytest.fail("the cache is not used"))
    again = A._training_conditions(None, runs, metrics, today)
    assert again["label"] == got["label"]
    en = relocalize(again["label"], "en")
    assert not CJK.search(en) and "3 runs" in en, en


def test_the_inputs_endpoint_shows_the_label_in_the_request_language(monkeypatch):
    from backend.api import racepower as RP
    cached = {"training_conditions": {"provider": "fallback", "label": A.fallback_training()["label"]}, "x": 1}
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: cached)
    with use_locale("en"):
        got = RP.get_inputs()
    assert not CJK.search(got["training_conditions"]["label"]), got["training_conditions"]["label"]
    assert isinstance(cached["training_conditions"]["label"], Msg)     # the shared cache is left as it was
    with use_locale("zh-TW"):
        assert "預設" in RP.get_inputs()["training_conditions"]["label"]
