"""間歇判讀 (engine/interval_eval.py) and its single-workout cards — synthetic streams only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import interval_eval as IE
from backend.engine import interval_library as IL
from backend.engine import quality_gate as QG
from backend.engine import workout_review as WR
from backend.tests.test_quality_gate import TODAY, _ds, _z3_run
from backend.tests.test_workout_review import _run
from backend.tests.wko5_fakes import FakeWorkout

CP = 250.0


def _v1a_run(day, reps=5, on=272.0, rest_p=120.0):
    """V1 5×2′ @ 109 % CP with 2′ walks, std blocks: 15′ warm-up @ 70 %, 5′ cool-down."""
    p = [175.0] * 900
    for k in range(reps):
        p += [on] * 120
        if k < reps - 1:
            p += [rest_p] * 120
    p += [150.0] * 300
    t = np.arange(len(p), dtype=float)
    hr = np.full(len(t), 140.0)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 5.0})


def _planned(ds, key, reps=None):
    ds.plan_rows = {w.idx: {"title": IL.title(IL.get(key)), "variant_key": key, "rung_key": IL.get(key).rung,
                            "equiv": True, "variant_reps": reps, "state": "done"} for w in ds.workouts}
    return ds


def test_a_full_session_meets_its_goal():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    e = IE.evaluate(ds, ds.workouts[0])
    assert e["ok"] and len(e["reps"]) == 5 and e["hit"] == 5 and e["outcome"] == "met"
    assert e["tiz_plan_s"] == 600 and e["tiz_ratio"] == pytest.approx(1.0, abs=0.05)
    assert e["verdict"] == "met" and e["verdict_label"] == "達到訓練目標"
    # dFRC: 5 × 120 s × 22 W above CP drains it; the battery never goes up past 100 %
    assert 0.5 < e["dfrc_min_pct"] < 1.0 and max(e["series"]["dfrc_pct"]) <= 1.0 + 1e-9
    assert e["wprime_used_j"] == pytest.approx(5 * 120 * 22, rel=0.02)
    assert e["sdec"] == pytest.approx(0.0, abs=0.01)


def test_stopping_early_is_judged_against_the_chosen_variant():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3), reps=3)]), "v1a")
    e = IE.evaluate(ds, ds.workouts[0])
    assert e["outcome"] == "unadapted" and e["verdict"] == "missed" and e["tiz_ratio"] == pytest.approx(0.6, abs=0.05)
    # a shorter equivalent planned as such (v1b 4×2:30) is judged against its own 10′, not v1a's
    assert IE.verdict_of("met", 0.9) == "met" and IE.verdict_of("met", 0.7) == "partial"
    assert IE.verdict_of("border", 1.0) == "partial" and IE.verdict_of("met", 0.5) == "missed"


def test_the_verdict_moves_the_ladder_only_with_enough_time_in_zone():
    good = [{"power": 240.0}] * 3
    h = {"bouts": good, "cp": CP, "variant_key": "t1a", "rung_key": "z3a", "equiv": True, "tiz_ratio": 0.7}
    d = QG.dose_step([h])
    assert h["outcome"] == "border" and d["step"] == 0 and "目標區時間只有計畫的 70%" in d["note"]
    assert QG.dose_step([{**h, "tiz_ratio": 0.95}])["step"] == 1


def test_the_cards_render_and_hide_on_easy_runs():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    w = ds.workouts[0]
    v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["text"] == "達到訓練目標" and v["badge"]["level"] == "good"
    rows = {r["label"]: r for r in v["chip_rows"]}
    assert rows["每趟"]["verdict"] == {"text": "全部達標", "level": "good"} and "0.98" in rows["每趟"]["tip"]
    assert rows["目標區時間"]["verdict"]["level"] == "good" and all(len(c) <= 24 for r in v["chip_rows"] for c in r["chips"])
    reps = WR.review(ds, w, "interval_reps")
    rp = reps["rep_profile"]
    assert rp["mode"] == "plan" and rp["band"][0] < rp["band"][1] and len(rp["reps"]) == rp["n_plan"]
    assert all(r["status"] == "in" for r in rp["reps"]) and reps["subtitle"] == f"{rp['n_plan']} 趟中 {rp['n_plan']} 趟在目標內"
    pw = WR.review(ds, w, "interval_power")
    tr = pw["iv_trace"]
    assert tr["band"] == rp["band"] and len(tr["reps"]) == len(rp["reps"])
    assert len(tr["x"]) == len(tr["power"]) == len(tr["dfrc"]) == len(tr["hr"]) and max(v for v in tr["hr"] if v) == 140
    assert 0 < tr["dfrc_min"]["pct"] < 1 and pw["subtitle"].startswith("W′ 最低 ")
    bat = WR.review(ds, w, "interval_battery")
    assert bat["series"][-1]["labels"][0].startswith("最低 ")
    tiz = WR.review(ds, w, "interval_tiz")
    assert [s["data"]["points"][0][1] for s in tiz["series"]][0] == 600
    easy = _ds([_run(TODAY - dt.timedelta(days=2), power=150.0)])
    assert WR.review(easy, easy.workouts[0], "interval_reps").get("hide") is True


def test_a_rep_is_in_band_above_or_below_with_the_tolerances():
    floor, ceil = 0.98 * 250.0, 270.0 * WR.REP_HI_TOL          # band 250–270 W
    st = lambda p: WR._rep_status({"power": p}, floor, ceil)
    assert [st(p) for p in (244.0, 246.0, 270.0, 275.0, 276.0)] == ["low", "in", "in", "in", "high"]
    assert WR._rep_status({"power": 400.0}, floor, None) == "in"          # an open-ended band has no ceiling


def _cp_test_run(day, fade=True):
    """「CP 測試 3 分 + 12 分」: 15′ warm-up, 3′ @ 330 W (even), 30′ easy, 12′ @ 280 W mean
    (295 → 265 W when `fade`: the 2nd half 10 % down), 10′ cool-down."""
    p = [175.0] * 900 + [330.0] * 180 + [150.0] * 1800
    p += ([295.0] * 360 + [265.0] * 360) if fade else [280.0] * 720
    p += [150.0] * 600
    t = np.arange(len(p), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": [150.0] * len(t), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"], sport_type="running",
                       channels=ch, title="CP 測試 3 分 + 12 分",
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 5.0})


def test_the_interval_tab_shows_on_a_cp_test_with_its_protocol_as_the_plan():
    ds = _ds([_cp_test_run(TODAY - dt.timedelta(days=1))])
    w = ds.workouts[0]
    assert WR.classify(ds, w)["type"] == "test_cp"
    e = IE.card(ds, w)
    assert e["ok"] and e["kind"] == "test" and e["label"] == "CP 測試 3 分 + 12 分（全力）"
    r3, r12 = e["reps"]
    assert r3["duration_s"] == 180 and r3["power"] == pytest.approx(330, abs=1) and r3["even"]
    assert r12["duration_s"] == 720 and r12["power"] == pytest.approx(280, abs=1) and not r12["even"]
    assert r12["split"] == pytest.approx(265 / 295 - 1, abs=0.01) and "前快後掉" in r12["pacing"]
    # all-out reference by the CP model: CP + W′/t with the CP in effect (250) and the 13.1 kJ prior
    assert r3["expected"] == pytest.approx(250 + 13100 / 180) and r12["expected"] == pytest.approx(250 + 13100 / 720)
    assert e["verdict"] == "uneven" and e["level"] == "warn" and "1 段不平均" in e["verdict_label"]
    v = WR.review(ds, w, "interval_verdict")
    assert v["badge"]["text"].startswith("測試配速分配") and not v.get("hide")
    assert all("達標" not in x["data"]["value"] for x in v["series"][2:] if x["data"]["kind"] == "value")
    # the compact rows: label · short chips · verdict chip; the formula and halves only behind the ?
    b3, b12 = v["chip_rows"][:2]
    assert b3["label"] == "3 分段" and b3["chips"][0] == "330 W" and b3["chips"][2].startswith("做到 ")
    assert b3["verdict"] == {"text": "平均", "level": "good"} and "預期全力" in b3["tip"] and "推估" in b3["tip"]
    assert b12["verdict"] == {"text": "前快後掉", "level": "warn"} and "前半 295 → 後半 265 W" in b12["tip"]
    assert v["chip_rows"][-1]["label"] == "W′"
    assert all(len(c) <= 24 for r in v["chip_rows"] for c in r["chips"])
    assert not any("平均＝" in c for r in v["chip_rows"] for c in r["chips"])     # the rule sits in the card's ?
    reps = WR.review(ds, w, "interval_reps")
    rp = reps["rep_profile"]
    assert rp["mode"] == "test" and rp["band"] is None and [r["status"] for r in rp["reps"]] == ["even", "uneven"]
    assert all(r["expected"] for r in rp["reps"]) and "推估" in reps["description"] and reps["subtitle"] == "2 段中 1 段配速平均"
    pw = WR.review(ds, w, "interval_power")
    assert not pw.get("hide") and pw["iv_trace"]["band"] is None and pw["iv_trace"]["test"]
    bat = WR.review(ds, w, "interval_battery")
    assert not bat.get("hide") and bat["subtitle"].startswith("整趟高於 CP")
    assert WR.review(ds, w, "interval_tiz").get("hide") and WR.review(ds, w, "interval_hr").get("hide")
    even = _ds([_cp_test_run(TODAY - dt.timedelta(days=1), fade=False)])
    assert IE.card(even, even.workouts[0])["verdict"] == "even"
    # the reference uses the CP before the test day (a CP applied from this very test is circular)
    two = _ds([_run(TODAY - dt.timedelta(days=5), power=150.0), _cp_test_run(TODAY - dt.timedelta(days=1))])
    two.cp = lambda x: 230.0 if x.idx == 0 else 250.0
    e2 = IE.card(two, two.workouts[1])
    assert e2["cp_ref"] == 230.0 and e2["reps"][0]["expected"] == pytest.approx(230 + 13100 / 180)


def test_an_unplanned_run_offers_the_interval_reading_and_remembers_it(tmp_path, monkeypatch):
    from backend.engine import activity_tags as AT
    # 3×2′ @ 109 %: equivalent T@VO2max 2.5 min < 4 (session_stimulus) and avg HR ≤ AeT+3 → not quality
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3), reps=3)])
    w = ds.workouts[0]
    assert WR.classify(ds, w)["type"] != "quality"
    v = WR.review(ds, w, "interval_verdict")
    assert not v.get("hide") and v["badge"]["text"] == "這次不是間歇課"
    a = v["action"]
    assert a["label"] == "當作間歇判讀" and a["method"] == "PATCH" and a["reload"]
    assert a["body"]["add_tags"] == [IE.FLAG_TAG] and a["body"]["items"][0]["key"] == AT.key_of(w.entry.start)
    assert "偵測到 3 趟" in v["series"][1]["data"]["value"]
    assert WR.review(ds, w, "interval_reps").get("hide")         # no reps to draw until the mark
    bat = WR.review(ds, w, "interval_battery")                  # the battery still shows
    assert not bat.get("hide") and bat["series"][0]["name"] == "dFRC（WKO5）"
    assert not any(s["name"].startswith("Skiba") for s in bat["series"])     # dFRC only (owner 2026-10-02)
    pw = WR.review(ds, w, "interval_power")                     # power / W′ / HR: every run with power
    assert not pw.get("hide") and pw["iv_trace"]["reps"] == [] and pw["iv_trace"]["band"] is None
    small = WR.review(ds, w, "wprime_battery")
    assert not small.get("hide") and not any(s["name"].startswith("Skiba") for s in small["series"])
    # the mark (activity tag) → evaluated on the detected bouts
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    AT.upsert(db, start_local=AT.key_of(w.entry.start), file=w.entry.file, tags=[IE.FLAG_TAG])
    assert IE.flagged(w)
    e = IE.card_cached(ds, w)
    assert e["ok"] and e["kind"] == "detected" and e["flagged"] and len(e["reps"]) == 3
    v = WR.review(ds, w, "interval_verdict")
    assert "當作間歇" in v["badge"]["sub"] and "action" not in v


def test_five_two_minute_reps_are_zone5_even_with_an_easy_average_hr():
    # avg HR 140 ≤ AeT+3 (145.4), but ≥ 4 min of power evidence (90 + 4 × 60 s) wins (owner 2026-10-02)
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3))])
    w = ds.workouts[0]
    c = WR.classify(ds, w)
    assert c["type"] == "quality" and c["stimulus"] == "z5" and c["type_label"] == "Z5 間歇" and c["icon"] == "z5"
    assert c["stim"]["t_vo2_eq_s"] == pytest.approx(270, abs=10) and c["stim"]["easy_hr"]
    cards = {x.get("id"): x for x in WR.review(ds, w, "summary")["cards"]}
    assert cards["type"]["text"] == "Z5 間歇" and "103%" in cards["type"]["tip"] and "推估" in cards["type"]["tip"]
    assert cards["vo2"]["value"] == "4.5" and cards["vo2"]["level"] == "info"


def test_a_run_without_bouts_or_power_says_so_in_one_card():
    ds = _ds([_run(TODAY - dt.timedelta(days=2), power=150.0)])
    w = ds.workouts[0]
    v = WR.review(ds, w, "interval_verdict")
    assert "沒有偵測到用力段" in v["series"][1]["data"]["value"] and "action" not in v
    bat = WR.review(ds, w, "interval_battery")                  # 150 W < CP: the battery stays full
    assert "最低 100%" in bat["subtitle"] and "0.0 kJ" in bat["subtitle"]
    nop = _ds([_run(TODAY - dt.timedelta(days=2))])
    w = nop.workouts[0]
    v = WR.review(nop, w, "interval_verdict")
    assert not v.get("hide") and v["empty"].startswith("沒有功率")
    for sec in ("interval_reps", "interval_power", "interval_battery", "interval_tiz", "interval_hr", "wprime_battery"):
        assert WR.review(nop, w, sec).get("hide"), sec


def test_hr_at_matched_power_compares_like_with_like():
    days = [TODAY - dt.timedelta(days=d) for d in (20, 13, 6)]
    ds = _planned(_ds([_z3_run(d) for d in days]), "t2a")
    e = IE.evaluate(ds, ds.workouts[-1])
    assert [p["current"] for p in e["peers"]] == [False, False, True]
    r = WR.review(ds, ds.workouts[-1], "interval_hr")
    assert [s["name"] for s in r["series"]][:2] == ["之前的同類間歇", "這次"]
