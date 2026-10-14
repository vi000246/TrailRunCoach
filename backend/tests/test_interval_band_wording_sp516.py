"""間歇判讀「全部達標」和每趟功率「偏高」說法一致 (SP-516).

A 6×4′ demo session said 「每趟 6/6 趟在目標帶・全部達標」 on 間歇判讀 and 「4 趟在目標內，2 趟偏高」 on
每趟功率: the verdict counts a rep as in band when its mean is ≥ the lower bound × tol
(interval_eval.evaluate → `in_band`, the same floor as quality_gate.interval_outcome), so a rep above
the band's upper end counts, while 每趟功率 splits those off as 偏高. The rule stays (偏高 still counts as
達標: interval_calib's tol, interval_outcome / dose_step untouched); the words now match — both cards
say 「達標」 for ≥ the floor and name the high reps: 判讀 「5/5 趟達標」「2 趟偏高」, 每趟功率
「5 趟中 5 趟達標（3 趟在目標內，2 趟偏高）」. Each rep's in / high / low is decided once
(interval_eval: `status`), and both cards read it."""
import datetime as dt
import json
from pathlib import Path

import pytest

from backend.engine import interval_eval as IE
from backend.engine import quality_gate as QG
from backend.engine import workout_review as WR
from backend.i18n import use_locale
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds

STATIC = Path(__file__).resolve().parents[1] / "static" / "i18n"


def _session(powers):
    """v1a 5×2′ (band 106–112 % CP of 250 W: floor 259.7 W, ceil 285.6 W) with each rep at its own power."""
    w = _v1a_run(TODAY - dt.timedelta(days=3), reps=len(powers))
    p = list(w.channels["power"])
    for k, pw in enumerate(powers):
        a = 900 + k * 240
        p[a:a + 120] = [pw] * 120
    w.channels["power"] = p
    ds = _planned(_ds([w]), "v1a")
    return ds, ds.workouts[0]


def _cards(ds, w):
    v = WR.review(ds, w, "interval_verdict")
    rows = {r["label"]: r for r in v["chip_rows"]}
    return v, rows, WR.review(ds, w, "interval_reps")


TWO_HIGH = [272.0, 272.0, 272.0, 300.0, 300.0]


def test_two_high_reps_are_on_target_on_both_cards():
    ds, w = _session(TWO_HIGH)
    e = IE.evaluate(ds, w, with_peers=False)
    # the rule is unchanged: a high rep counts (the same floor as quality_gate.interval_outcome)
    assert e["hit"] == 5 and e["outcome"] == "met" and e["verdict"] == "met"
    assert QG.interval_outcome([{"power": r["power"]} for r in e["reps"]], ("v1a", "", 5, 2.0, 2, e["lo"], e["hi"]),
                               e["cp"])["outcome"] == "met"
    # each rep's in / high / low decided once, in the evaluation
    assert [r["status"] for r in e["reps"]] == ["in", "in", "in", "high", "high"]
    assert all(r["in_band"] == (r["status"] != "low") for r in e["reps"])
    assert e["ceil"] == pytest.approx(e["hi"] * e["cp"] * WR.REP_HI_TOL) and e["n_high"] == 2
    v, rows, reps = _cards(ds, w)
    assert rows["每趟"]["chips"] == ["5/5 趟達標", "2 趟偏高"]
    assert rows["每趟"]["verdict"] == {"text": "全部達標", "level": "good"}
    assert "仍算達標" in rows["每趟"]["tip"] and "偏高" in rows["每趟"]["tip"]
    assert all(len(c) <= 24 for r in v["chip_rows"] for c in r["chips"])
    assert reps["subtitle"] == "5 趟中 5 趟達標（3 趟在目標內，2 趟偏高）"
    # the same rep is the same word on both cards
    assert [r["status"] for r in reps["rep_profile"]["reps"]] == [r["status"] for r in e["reps"]]
    reasons = [r["data"]["value"] for r in v["series"]]
    assert any(x.startswith("5/5 趟達標（≥ 260 W") and "2 趟偏高（> 286 W" in x for x in reasons), reasons
    assert not any("趟在目標帶" in x or "每一趟都在目標帶" in x for x in reasons), reasons
    assert rows["逐趟判定"]["tip"] == "每一趟都達標（2 趟偏高）"


def test_no_high_rep_says_on_target_without_a_breakdown():
    ds, w = _session([272.0] * 5)
    v, rows, reps = _cards(ds, w)
    assert rows["每趟"]["chips"] == ["5/5 趟達標"] and rows["每趟"]["verdict"]["text"] == "全部達標"
    assert reps["subtitle"] == "5 趟中 5 趟達標"
    assert rows["逐趟判定"]["tip"] == "每一趟都在目標帶"          # quality_gate's own words: no rep high


def test_a_low_and_a_high_rep():
    ds, w = _session([272.0, 300.0, 272.0, 272.0, 255.0])        # the last rep low (≥ 0.95 × lo: still found)
    e = IE.evaluate(ds, w, with_peers=False)
    assert e["hit"] == 4 and [r["status"] for r in e["reps"]] == ["in", "high", "in", "in", "low"]
    v, rows, reps = _cards(ds, w)
    assert rows["每趟"]["chips"] == ["4/5 趟達標", "1 趟偏高"] and rows["每趟"]["verdict"]["text"] == "差 1 趟"
    assert reps["subtitle"] == "5 趟中 4 趟達標（3 趟在目標內，1 趟偏高），1 趟偏低"


def test_english_says_the_same():
    ds, w = _session(TWO_HIGH)
    with use_locale("en"):
        v, rows, reps = _cards(ds, w)
        rows = {r["label"]: r for r in v["chip_rows"]}
    assert rows["Each rep"]["chips"] == ["5/5 reps on target", "2 high"]
    assert rows["Each rep"]["verdict"]["text"] == "All on target"
    assert "still counts as on target" in rows["Each rep"]["tip"]
    assert reps["subtitle"] == "5 of 5 reps on target (3 in the band, 2 high)"
    reasons = [r["data"]["value"] for r in v["series"]]
    assert any(x.startswith("5/5 reps on target (≥ 260 W") and "2 high (> 286 W" in x for x in reasons), reasons


def test_the_legend_calls_a_high_rep_on_target():
    zh = json.loads((STATIC / "zh-TW" / "viewer.json").read_text("utf-8"))
    en = json.loads((STATIC / "en" / "viewer.json").read_text("utf-8"))
    assert zh["iv.in"] == "在目標內（在綠框裡）" and zh["iv.in_s"] == "在目標內"
    assert "仍算達標" in zh["iv.high"] and "達標" in zh["iv.rule"] and "上面" in zh["iv.rule"]
    assert en["iv.in"] == "In the band (inside the green frame)" and en["iv.in_s"] == "In the band"
    assert "still on target" in en["iv.high"] and "above" in en["iv.rule"]
