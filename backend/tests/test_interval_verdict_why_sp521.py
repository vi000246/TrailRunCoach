"""間歇課大徽章「未達到」和判讀卡「全部達標」並列，結論說清楚 (SP-521).

The demo's 10/6 「間歇 6×4 分」: the badge said 「✕ 未達到」 (time in the target zone 31 %) next to the
card's 「6/6 趟達標・全部達標」. Both are right — 達標 is each rep's average ≥ the floor, the verdict also
needs the time in the zone (10-s power in the band, stretches ≥ 30 s) ≥ the goal — but read as a
contradiction. The badge now says why (`badge.why`, interval_eval `verdict_why`): 「每趟都達標，但目標區
時間只有 31%（要 ≥ 85%）」; the 目標區時間 row's tip says how the two differ. Words only: verdict_of,
quality_gate.interval_outcome, TIZ_GOAL and interval_calib are unchanged."""
import datetime as dt

from backend.engine import interval_eval as IE
from backend.engine import quality_gate as QG
from backend.engine import workout_review as WR
from backend.i18n import use_locale
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds

CHOPPY = lambda i: 290.0 if (i // 20) % 2 == 0 else 255.0     # noqa: E731  avg 272.5 W ≥ the floor, < 30 s in the zone at a time


def _run(reps):
    """v1a (5×2′, 106–112 % CP, CP 250: floor 259.7 W) planned; each rep's 120 s from `reps[k](i)`."""
    w = _v1a_run(TODAY - dt.timedelta(days=3))
    p = list(w.channels["power"])
    for k, f in enumerate(reps):
        a = 900 + k * 240
        for i in range(120):
            p[a + i] = f(i)
    w.channels["power"] = p
    ds = _planned(_ds([w]), "v1a")
    return ds, ds.workouts[0]


def _steady(i):
    return 272.0


def test_every_rep_on_target_but_little_time_in_the_zone_says_so():
    ds, w = _run([CHOPPY] * 5)
    e = IE.card_cached(ds, w)
    # the rule as before: every rep 達標, 逐趟判定 達標, TIZ 0 % → 未達到
    assert e["hit"] == 5 and e["outcome"] == "met" and e["tiz_ratio"] == 0.0 and e["verdict"] == "missed"
    assert IE.verdict_of(e["outcome"], e["tiz_ratio"]) == "missed"
    assert e["verdict_why"] == "每趟都達標，但目標區時間只有 0%（要 ≥ 85%）"
    v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["text"] == "未達到" and v["badge"]["why"] == e["verdict_why"]
    rows = {r["label"]: r for r in v["chip_rows"]}
    assert rows["每趟"]["verdict"]["text"] == "全部達標"                     # the per-rep word stays
    assert rows["目標區時間"]["verdict"]["text"] == "不足"
    tip = rows["目標區時間"]["tip"]
    assert "整趟平均" in tip and "連續 ≥ 30 秒" in tip and "265 W 以上" in tip, tip    # Z5: no upper bound
    judged = next(s for s in v["series"] if s["name"] == "判定")
    assert judged["data"]["value"] == "未達到：每趟都達標，但目標區時間只有 0%（要 ≥ 85%）"


def test_partly_met_by_time_says_so_too():
    ds, w = _run([_steady] * 4 + [CHOPPY])
    e = IE.card_cached(ds, w)
    assert e["verdict"] == "partial" and e["outcome"] == "met" and e["hit"] == 5
    assert 0.6 <= e["tiz_ratio"] < 0.85
    assert e["verdict_why"] == f"每趟都達標，但目標區時間只有 {e['tiz_ratio']:.0%}（要 ≥ 85%）"
    assert WR.review(ds, w, "interval_verdict")["badge"]["why"] == e["verdict_why"]


def test_a_rep_under_the_floor_names_the_rep_by_rep_outcome():
    ds, w = _run([_steady] * 3 + [lambda i: 230.0, _steady])
    e = IE.card_cached(ds, w)
    assert e["hit"] == 4 and e["outcome"] != "met" and e["verdict"] in ("partial", "missed")
    lab = QG.OUTCOME_LABEL[e["outcome"]]
    assert e["verdict_why"].startswith(f"逐趟判定是「{lab}」"), e["verdict_why"]
    assert "每趟都達標" not in e["verdict_why"]


def test_met_has_no_why():
    ds, w = _run([_steady] * 5)
    e = IE.card_cached(ds, w)
    assert e["verdict"] == "met" and e["verdict_why"] is None
    assert "why" not in WR.review(ds, w, "interval_verdict")["badge"]


def test_english_says_the_same():
    ds, w = _run([CHOPPY] * 5)
    with use_locale("en"):
        v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["text"] == "Not met"
    assert v["badge"]["why"] == "Every rep on target, but only 0% time in the target zone (needs ≥ 85%)"
    tip = {r["label"]: r for r in v["chip_rows"]}["Time in target zone"]["tip"]
    assert "whole rep's average" in tip and "≥ 30 s" in tip and "265 W and up" in tip, tip


def test_the_rule_is_unchanged():
    assert (IE.TIZ_GOAL, IE.TIZ_PART, IE.TIZ_RUN_S) == (0.85, 0.60, 30)
    assert IE.verdict_of("met", 0.31) == "missed" and IE.verdict_of("met", 0.7) == "partial"
    assert IE.verdict_of("met", 0.9) == "met" and IE.verdict_of("border", 0.9) == "partial"


def test_the_viewer_draws_the_why():
    from pathlib import Path
    html = (Path(WR.__file__).resolve().parents[1] / "static" / "wko5_viewer.html").read_text("utf-8")
    assert "b.why" in html and 'class="why"' in html
