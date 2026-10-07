"""SP-253: 失溫風險 checklist on trail / 百岳 plans — lowest temperature ≤ 5 °C AND (rain reminder
on OR wind chill ≤ −5 °C), both thresholds 推估 (owner 2026-10-06); no probability in the text;
merged into the one attention line with 冷風 (SP-305). Fakes only, no network."""
import datetime as dt
import re

import pytest

from backend.engine.racepower import cold as CD
from backend.tests.test_race_calculator import _upload
from backend.tests.test_race_cold_wind import _pts, _rows, _wc_temp
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)

DAY = "2099-01-10"
WET = {"alert": True, "max_pop_pct": 80.0, "total_mm": 6.0}
DRY = {"alert": False, "max_pop_pct": 10.0, "total_mm": 0.0}
ITEMS = ("防水防風外套", "保暖層", "手套帽子", "熱食熱飲", "撤退點")


def _hyp(temp, wind, rain, kind="baiyue"):
    rows = _rows(temp, wind)
    cw = CD.cold_wind(_pts(), rows, date=DAY)
    return CD.hypothermia(_pts(), cw, rain, kind, rows), cw


def test_temperature_boundary_with_rain():
    on, _ = _hyp(5.0, 3.0, WET)
    off, _ = _hyp(5.1, 3.0, WET)
    assert on["alert"] is True and on["wet"] is True and on["min_temp_c"] == 5.0
    assert off["alert"] is False


def test_wind_chill_boundary_without_rain():
    t = _wc_temp(-5.0, 20.0)
    on, _ = _hyp(t - 0.01, 20.0, None)
    off, _ = _hyp(t + 0.05, 20.0, None)
    assert on["alert"] is True and on["windy"] is True
    assert off["alert"] is False and off["windy"] is False


def test_cold_but_dry_and_calm_is_quiet():
    h, cw = _hyp(1.0, 3.0, DRY)
    assert h["alert"] is False and CD.attention(cw, h) is None


def test_no_weather_data_or_road_is_none():
    assert CD.hypothermia(_pts(), None, None, "trail") is None
    rows = _rows(0.0, 30.0)
    cw = CD.cold_wind(_pts(), rows, date=DAY)
    assert CD.hypothermia(_pts(), cw, WET, "road", rows) is None
    # rain data but no temperature anywhere (window points, no rows)
    pts = [{**p, "temp_c": None} for p in _pts()]
    assert CD.hypothermia(pts, None, WET, "trail") is None


def test_hourly_rows_and_segment_temperature_are_used_without_wind():
    pts = _pts()
    hours = [{"t": f"{DAY}T{h:02d}:00", "temp_c": 4.0, "rh_pct": 90.0} for h in range(24)]
    assert CD.hypothermia(pts, None, WET, "trail", None, hours)["min_temp_c"] == pytest.approx(4.0)
    seg = [{**p, "temp_c": 3.0} for p in pts]
    assert CD.hypothermia(seg, None, WET, "trail")["min_temp_c"] == 3.0


def test_checklist_line_has_no_probability():
    h, cw = _hyp(2.0, 3.0, WET)
    a = CD.attention(cw, h)
    assert a["kinds"] == ["hypothermia"]
    assert a["line"].startswith("失溫風險：最低約 2 °C，加上預報有雨 → 帶防水防風外套")
    for item in ITEMS:
        assert item in a["line"]
    text = a["line"] + " ".join(a["details"])
    assert "%" not in text and "機率" not in text and not re.search(r"\d\s*％", text)


def test_merged_with_cold_wind_in_one_line():
    h, cw = _hyp(_wc_temp(-12.0, 30.0), 30.0, WET)
    a = CD.attention(cw, h)
    assert a["kinds"] == ["cold_wind", "hypothermia"]
    assert a["line"].startswith("冷風＋失溫風險：")
    assert a["line"].count("保暖層") == 1 and "防風外套、" not in a["line"].replace("防水防風外套", "")
    assert a["line"].count("風寒") == 1            # the wind chill is said once
    assert a["line"].endswith("；先想好撤退點")


@pytest.mark.parametrize("kind", ["trail", "baiyue"])
def test_plan_checklist_time_unchanged(client, kind):   # noqa: F811
    cid = _upload(client)["course_id"]
    body = {"type": kind, "course": {"course_id": cid}, "date": DAY, "start_time": "06:00"}
    rain = [{"start": f"{DAY}T{h:02d}:00", "end": f"{DAY}T{h + 1:02d}:00", "pop_pct": 80, "mm": 0.5} for h in range(23)]
    cold = client.post("/api/v1/racepower/plan", json={**body, "rain": rain, "wind": _rows(2.0, 3.0)}).json()
    warm = client.post("/api/v1/racepower/plan", json={**body, "rain": rain, "wind": _rows(12.0, 3.0)}).json()
    none = client.post("/api/v1/racepower/plan", json=body).json()
    assert cold["hypothermia"]["alert"] is True and "hypothermia" in cold["attention"]["kinds"]
    assert warm["hypothermia"]["alert"] is False and warm["attention"] is None
    assert none["hypothermia"] is None and none["attention"] is None
    assert cold["summary"]["time_s"] == warm["summary"]["time_s"] == none["summary"]["time_s"]


def test_road_plan_has_no_checklist(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    p = client.post("/api/v1/racepower/plan", json={"type": "road", "course": {"course_id": cid}, "date": DAY,
                                                    "start_time": "06:00", "wind": _rows(2.0, 3.0)}).json()
    assert p["hypothermia"] is None and p["attention"] is None
    assert dt.date.fromisoformat(DAY)
