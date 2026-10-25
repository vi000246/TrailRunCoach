"""SP-944: the race calculator's 環境 card on the English page (?lang=en), in a real browser on the demo athlete —
no Chinese left in the chips, the From / To boxes of 進階, the 嘗試 line or the CWA key box (the SP-552
screenshots showed 「白天 06–18 平均，取得於…」, 「Open-Meteo 歷史資料：近 90 天…」, 「沒有逐時資料，熱用這個平均值」,
氣壓／海拔係數／露點／熱負荷, 「嘗試：」, 「儲存」); the Chinese page keeps its Chinese.

The race-day weather is a mocked GET /weather answer (its server texts — label, reasons, attribution — are
the English the server gives, tested in test_racepower_env_en_sp944.py); the place name in it (北投區 / 七星山)
is data and stays. The From label is the real /inputs answer. The region is mocked to Taiwan so the CWA key
box shows.

Run: ./e2e.sh -k sp944 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = _server.PAGES["racepower"]
PLACES = ("北投區", "七星山")


def _weather(lang: str, kind: str) -> dict:
    en = lang == "en"
    base = {"values": {"altitude_m": 620, "temp_c": 16.75, "rh_pct": 80, "dew_c": 13.3},
            "location": {"name": None, "lat": 25.10, "lon": 121.55, "elevation_m": 620},
            "fetched_at": "2026-10-24T09:00:00+08:00", "lead_days": 1, "rain": None, "wind": None,
            "sun": None, "peak": None, "points": None, "cairn": None}
    if kind == "town":                       # 鄉鎮預報 without hourly rows (the SP-552 screenshot)
        return {**base, "provider": "cwa_town_weekly",
                "label": "CWA township 1-week forecast" if en else "中央氣象署 鄉鎮一週預報",
                "tried": [{"provider": "cwa_hourly", "ok": False,
                           "reason": "No CWA mountain forecast point within 5 km" if en else "5 km 內沒有 CWA 登山預報點"},
                          {"provider": "cwa_town_weekly", "ok": True}],
                "detail": {"cwa_location": "北投區", "town": True, "match": "nearest", "distance_km": 3.8,
                           "town_elevation_m": 120, "target_elevation_m": 620, "lapse_m": 500, "offset_c": -3.25,
                           "attribution": "Weather data: CWA" if en else "天氣資料來源：中央氣象署"},
                "hourly": None}
    hours = [{"t": f"2026-10-25T{h:02d}:00", "temp_c": 15.0 + h / 4, "rh_pct": 80.0, "dew_c": 12.0} for h in range(24)]
    return {**base, "provider": "cwa_hourly",           # a 登山 point nearby, with hourly rows
            "label": "CWA mountain 3-day forecast" if en else "中央氣象署 登山三天預報",
            "tried": [{"provider": "cwa_hourly", "ok": True}],
            "detail": {"cwa_location": "七星山", "match": "nearest", "distance_km": 2.1,
                       "attribution": "Weather data: CWA" if en else "天氣資料來源：中央氣象署"},
            "hourly": hours}


def _open(page, lang: str, kind: str) -> None:
    page.route("**/api/v1/region", lambda r: r.fulfill(status=200, json={"region": "tw"}))
    page.route(re.compile(r".*/api/v1/racepower/weather[?].*"), lambda r: r.fulfill(status=200, json=_weather(lang, kind)))
    page.route("**/api/v1/racepower/weather/key", lambda r: r.fulfill(
        status=200, json={"configured": True, "masked": "CWA-****-123", "source": "file", "shared": False}))
    open_page(page, PAGE + f"?lang={lang}")
    page.evaluate("() => { document.getElementById('latlon').value = '25.10,121.55,620';"
                  " document.getElementById('date').value = '2026-10-25'; getWeather(); }")
    page.wait_for_function("() => /torr/.test(document.getElementById('t-kv').textContent)"
                           " && /°C/.test(document.getElementById('to-v').textContent)", timeout=60_000)
    page.click("#env-adv > summary")
    page.evaluate("() => document.querySelectorAll('#env-adv details').forEach((d) => d.open = true)")


CJK_LEFT = r"""(places) => {
  const cjk = /[㐀-鿿　-〿！-｠]/;
  const sec = document.getElementById("env-adv").closest("section");
  let s = sec.innerText;
  for (const p of places) s = s.split(p).join("");
  return s.split("\n").filter((l) => cjk.test(l));
}"""


@pytest.mark.parametrize("kind", ["town", "mountain"])
def test_the_environment_card_is_english_on_the_english_page(app, kind):
    page, _ = app
    _open(page, "en", kind)
    page.click("#cwasave")
    page.wait_for_function("() => document.getElementById('cwastatus').textContent.includes('CWA-****')")
    left = page.evaluate(CJK_LEFT, list(PLACES))
    assert not left, "Chinese on the English 環境 card:\n  " + "\n  ".join(left)
    meta = page.inner_text("#wx-meta")
    assert "06–18" in meta and "10-24 09:00" in meta, meta
    assert ("no hourly" in meta) if kind == "town" else ("24 hourly" in meta), meta
    assert page.inner_text("#wx-tried").startswith("Tried:")
    assert "Pressure" in page.inner_text("#t-kv") and "Dew point" in page.inner_text("#f-kv")


def test_the_chinese_page_keeps_its_chinese(app):
    page, _ = app
    _open(page, "zh-TW", "town")
    meta = page.inner_text("#wx-meta")
    assert "白天 06–18 平均，取得於 10-24 09:00" in meta and "沒有逐時資料，熱用這個平均值" in meta, meta
    assert page.inner_text("#wx-tried").startswith("嘗試：")
    assert "氣壓" in page.inner_text("#t-kv") and "露點" in page.inner_text("#f-kv")
    assert "環境係數 M" in page.inner_text("#env-kv")
    assert page.inner_text("#cwasave") == "儲存"
