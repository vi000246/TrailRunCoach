"""「每趟功率 vs 目標帶」卡對偵測到的趟別寫「目標」 (SP-849).

SP-520 made the 理由 line of detected reps (no 課表) say the band they fell in; the 每趟功率 card still
said 「目標 254–278 W」 in its legend, frame and tips. What these lock: on detected reps the card names the
band (「超閾值帶 254–278 W」) and never 「目標」 in its subtitle or description; the 功率、無氧儲備
card's grey band the same; a planned session keeps 「目標」. The front end reads `source` / `band_name`."""
import datetime as dt
from pathlib import Path

from backend.engine import interval_eval as IE
from backend.engine import workout_review as WR
from backend.i18n import use_locale
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds

CHARTS = Path(__file__).resolve().parents[1] / "static" / "charts.html"


def _detected(on: float = 260.0):
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3), on=on)])
    ds.plan_rows = {}
    return ds, ds.workouts[0]


def test_detected_reps_card_names_the_band_not_a_target():
    ds, w = _detected(260.0)                                    # 104 % CP: 超閾值
    e = IE.card_cached(ds, w)
    assert e["kind"] == "detected" and e["band_name"] == "超閾值帶", e.get("band_name")
    r = WR.review(ds, w, "interval_reps")
    p = r["rep_profile"]
    assert p["source"] == "detected" and p["band_name"] == "超閾值帶", p
    assert "目標" not in r["subtitle"], r["subtitle"]
    assert "目標 " not in r["description"] and "偵測到的趟落在的超閾值帶（101–106% CP）" in r["description"], r["description"]
    t = WR.review(ds, w, "interval_power")
    assert t["iv_trace"]["band_name"] == "超閾值帶" and "目標帶" not in t["description"], t["description"]
    with use_locale("en"):
        r = WR.review(ds, w, "interval_reps")
    assert r["rep_profile"]["band_name"] == "supra-threshold band", r["rep_profile"]
    assert "the supra-threshold band (101–106% CP) the detected reps fell in" in r["description"], r["description"]


def test_detected_band_name_below_every_band():
    assert IE.detected_band_name("") == "中位數 ±3%"
    assert IE.detected_band_name("VO2max") == "VO2max帶"


def test_a_planned_session_keeps_its_target():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    w = ds.workouts[0]
    r = WR.review(ds, w, "interval_reps")
    p = r["rep_profile"]
    assert p["source"] == "plan" and p["band_name"] is None, p
    assert "綠色帶 = 目標" in r["description"]
    assert WR.review(ds, w, "interval_power")["iv_trace"]["band_name"] is None


def test_the_viewer_says_the_band_on_detected_reps():
    """drawRepProfile: legend, frame label, tip and the 「在帶內」 word go by `source` / `band_name`."""
    src = CHARTS.read_text(encoding="utf-8")
    body = src[src.index("function drawRepProfile"):src.index("function drawIntervalTrace")]
    assert 'P.source === "detected"' in body
    assert 't("viewer.iv.band_2l", ' in body and 't("viewer.iv.band", ' in body
    assert 'sw(det ? "in_band" : "in")' in body
    assert "viewer.it.leg_band_det" in src
