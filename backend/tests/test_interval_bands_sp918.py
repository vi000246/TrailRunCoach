"""每趟功率 vs 目標帶的圖是在表達什麼 (SP-918).

Without a 課表 (「當作間歇判讀」, a quality run, SP-519's automatic reading) the band the reps were judged
against was picked from the reps' own median (SP-520), so 「6 趟中 6 趟達標」 was the reps judged against
themselves — it never said whether they reached the band the runner meant (e.g. 閾值). The owner (Q1,
2026-10-27): no 達標 on detected reps — draw every band, colour each rep by the band its mean power fell in,
count them (「6 趟：0 趟閾值、4 趟超閾值、2 趟 VO2max」), the 閾值 band marked; the 間歇判讀 badge says which
intensity was trained, not 達到／部分達到; the time-in-zone card lists the time in each band. Q2 (b): each rep's
tip adds % LTHR and the HR zone. A 課表's reps are judged exactly as before."""
import datetime as dt
import json
from pathlib import Path

import pytest

from backend.engine import interval_eval as IE
from backend.engine import workout_review as WR
from backend.i18n import use_locale
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds

CHARTS = Path(__file__).resolve().parents[1] / "static" / "charts.html"
STATIC = Path(__file__).resolve().parents[1] / "static" / "i18n"
MIXED = [258.0, 258.0, 258.0, 258.0, 275.0, 275.0]      # CP 250: 4 × 103 % (超閾值), 2 × 110 % (VO2max)


def _run(powers, planned: bool = False):
    w = _v1a_run(TODAY - dt.timedelta(days=3), reps=len(powers))
    p = list(w.channels["power"])
    for k, pw in enumerate(powers):
        a = 900 + k * 240
        p[a:a + 120] = [pw] * 120
    w.channels["power"] = p
    ds = _ds([w])
    if planned:
        _planned(ds, "v1a")
    else:
        ds.plan_rows = {}
    return ds, ds.workouts[0]


def test_detected_reps_are_counted_per_band_not_judged():
    ds, w = _run(MIXED)
    e = IE.card_cached(ds, w)
    assert e["kind"] == "detected" and len(e["reps"]) == 6
    assert [r["band"] for r in e["reps"]] == ["supra"] * 4 + ["vo2"] * 2
    assert [r["status"] for r in e["reps"]] == [r["band"] for r in e["reps"]]       # no in / high / low
    assert e["band_dist"] == "6 趟：0 趟閾值、4 趟超閾值、2 趟 VO2max"
    # nothing judged against the reps' own band
    assert e["hit"] is None and e["floor"] is None and e["outcome"] is None and e["tiz_ratio"] is None
    assert e["verdict"] == "band" and e["verdict_label"] == "練到超閾值" and e["level"] == ""
    assert not any("達標線" in x or "趟達標" in x for x in e["reasons"]), e["reasons"]


def test_the_verdict_card_says_what_was_trained():
    ds, w = _run(MIXED)
    v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["text"] == "練到超閾值" and v["badge"]["level"] == "" and "why" not in v["badge"]
    assert v["text_source"] == "detected"
    rows = {r["label"]: r for r in v["chip_rows"]}
    assert rows["每趟"]["chips"] == ["0 趟閾值", "4 趟超閾值", "2 趟 VO2max"] and rows["每趟"]["verdict"] is None
    assert "逐趟判定" not in rows and "目標區時間" not in rows
    assert rows["各強度帶的時間"]["verdict"] is None and any(c.startswith("閾值 ") for c in rows["各強度帶的時間"]["chips"])
    words = " ".join(c for r in v["chip_rows"] for c in r["chips"]) + " " + v["badge"]["text"]
    for bad in ("達到訓練目標", "部分達到", "未達到", "全部達標", "差 "):
        assert bad not in words, words
    judged = v["series"][0]["data"]["value"]
    assert judged == "練到超閾值：6 趟中 4 趟落在這一帶", judged


def test_the_rep_card_draws_every_band_and_colours_each_rep():
    ds, w = _run(MIXED)
    r = WR.review(ds, w, "interval_reps")
    p = r["rep_profile"]
    assert p["mode"] == "bands" and p["source"] == "detected" and r["text_source"] == "detected"
    assert r["subtitle"] == "6 趟：0 趟閾值、4 趟超閾值、2 趟 VO2max"
    assert [b["key"] for b in p["bands"]] == ["sub", "thr", "supra", "vo2", "ana"]
    thr = [b for b in p["bands"] if b["thr"]]
    assert [b["key"] for b in thr] == ["thr"] and thr[0]["lo"] == pytest.approx(237.5) and thr[0]["hi"] == pytest.approx(252.5)
    assert p["bands"][-1]["hi"] is None                                          # 無氧: open-ended
    assert [x["status"] for x in p["reps"]] == ["supra"] * 4 + ["vo2"] * 2
    assert "目標" not in r["subtitle"] and "沒有目標" in r["description"] and "達標 =" not in r["description"]


def test_a_rep_below_every_band_and_the_threshold_band_always_counted():
    assert IE.band_key(0.86) == IE.BELOW and IE.band_key(0.98) == "thr" and IE.band_key(1.20) == "ana"
    reps = [{"band": "thr", "pct_cp": 0.98}] * 3 + [{"band": IE.BELOW, "pct_cp": 0.86}]
    assert IE.band_dist_text(reps) == "4 趟：1 趟低於 88% CP、3 趟閾值"
    assert IE.band_main(reps) == "thr"
    # the 閾值 band is listed even with no rep in it
    assert IE.band_dist_text([{"band": "vo2", "pct_cp": 1.1}]) == "1 趟：0 趟閾值、1 趟 VO2max"


def test_time_per_band_replaces_time_vs_plan():
    ds, w = _run(MIXED)
    e = IE.card_cached(ds, w)
    secs = e["band_secs"]
    assert set(secs) == {"sub", "thr", "supra", "vo2", "ana"} and all(isinstance(x, float) for x in secs.values())
    assert secs["supra"] == pytest.approx(4 * 120, abs=40) and secs["vo2"] == pytest.approx(2 * 120, abs=40)
    t = WR.review(ds, w, "interval_tiz")
    assert t["text_source"] == "detected"
    names = [s["name"] for s in t["series"]]
    assert names == ["閾值下", "閾值", "超閾值", "VO2max"], names                  # 無氧 only when reached
    assert "計畫（這份課表）" not in names and "實際" not in names
    assert "沒有課表" in t["description"]


def test_band_seconds_counts_stretches_in_each_band():
    t = list(range(600))
    p = [100.0] * 200 + [246.0] * 120 + [100.0] * 80 + [270.0] * 120 + [100.0] * 80     # CP 250
    secs = IE.band_seconds(t, p, 250.0)
    assert secs["thr"] == pytest.approx(120, abs=12) and secs["vo2"] == pytest.approx(120, abs=12)
    assert secs["supra"] < 12 and secs["sub"] < 12 and secs["ana"] == 0


def test_band_main_ties_go_to_the_median_rep():
    reps = [{"band": "supra", "pct_cp": 1.03}, {"band": "supra", "pct_cp": 1.04},
            {"band": "vo2", "pct_cp": 1.08}, {"band": "vo2", "pct_cp": 1.09}]
    assert IE.band_main(reps) in ("supra", "vo2")
    assert IE.band_main(reps[:3] + [{"band": "vo2", "pct_cp": 1.10}, {"band": "vo2", "pct_cp": 1.10}]) == "vo2"
    assert IE.band_main([]) is None


def test_english():
    ds, w = _run(MIXED)
    with use_locale("en"):
        e = IE.card_cached(ds, w)
        v = WR.review(ds, w, "interval_verdict")
        r = WR.review(ds, w, "interval_reps")
        t = WR.review(ds, w, "interval_tiz")
    assert e["band_dist"] == "6 reps: 0 threshold, 4 supra-threshold, 2 VO2max", e["band_dist"]
    assert v["badge"]["text"] == "Trained: supra-threshold"
    assert {c["label"] for c in v["chip_rows"]} >= {"Each rep", "Time per intensity band"}
    assert r["subtitle"] == e["band_dist"] and "no target" in r["description"]
    assert [s["name"] for s in t["series"]] == ["sub-threshold", "threshold", "supra-threshold", "VO2max"]
    mine = [c["tip"] for c in v["chip_rows"] if c["label"] in ("Each rep", "Time per intensity band")]
    for txt in [r["description"], t["description"], v["series"][0]["data"]["value"]] + mine + e["reasons"]:
        assert not any("一" <= ch <= "鿿" for ch in txt), txt


def test_a_planned_session_is_judged_as_before():
    ds, w = _run([272.0, 272.0, 272.0, 300.0, 300.0], planned=True)    # SP-516's two-high session
    e = IE.card_cached(ds, w)
    assert e["kind"] == "plan" and e["hit"] == 5 and e["verdict"] == "met" and e["floor"] is not None
    assert [r["status"] for r in e["reps"]] == ["in", "in", "in", "high", "high"]
    v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["text"] == "達到訓練目標" and "text_source" not in v
    r = WR.review(ds, w, "interval_reps")
    assert r["rep_profile"]["mode"] == "plan" and r["subtitle"] == "5 趟中 5 趟達標（3 趟在目標內，2 趟偏高）"
    t = WR.review(ds, w, "interval_tiz")
    assert [s["name"] for s in t["series"]] == ["計畫（這份課表）", "實際"] and "text_source" not in t


def test_each_rep_tip_has_pct_lthr_and_the_hr_zone():
    """Q2 (b): % LTHR and the HR zone in each rep's tip, planned or detected (a reference, never judged)."""
    for planned in (False, True):
        ds, w = _run(MIXED[:5], planned=planned)
        lthr = WR.measure(ds, w).get("lthr")
        reps = WR.review(ds, w, "interval_reps")["rep_profile"]["reps"]
        for x in reps:
            if lthr:
                assert x["pct_lthr"] == pytest.approx(x["hr_end"] / lthr, abs=1e-3)
            assert x["hr_zone"] is None or x["hr_zone"].startswith("Z"), x


def test_the_viewer_draws_the_bands():
    src = CHARTS.read_text(encoding="utf-8")
    body = src[src.index("function drawRepBands"):src.index("function drawIntervalTrace")]
    assert 'mode === "bands") return drawRepBands(card, res)' in src
    assert "--iv-b-${key}" in body and "viewer.iv.thr_band_2l" in body and "viewer.iv.rule_bands" in body
    assert "ivHrLine(r)" in body and src.count("ivHrLine(r)") >= 2                      # plan and detected tips
    for k in ("sub", "thr", "supra", "vo2", "ana", "below"):
        assert src.count(f"--iv-b-{k}:") == 2, k                                       # light and dark
    zh = json.loads((STATIC / "zh-TW" / "viewer.json").read_text("utf-8"))
    en = json.loads((STATIC / "en" / "viewer.json").read_text("utf-8"))
    for k in ("iv.rule_bands", "iv.thr_band", "iv.thr_band_2l", "iv.aria_bands", "iv.in_band_of", "iv.pct_lthr",
              "iv.hr_zone", "iv.paren"):
        assert zh.get(k) and en.get(k), k
    assert "不判達標" in zh["iv.rule_bands"] and "閾值帶" in zh["iv.thr_band"]
