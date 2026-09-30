"""Single-activity review (backend/engine/workout_review.py) on synthetic data."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout


def _t(minutes):
    return np.arange(0, minutes * 60 + 1, 1.0)


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kw, expected", [
    (dict(category="strength", moving_s=1800, hard_s=0), "strength"),
    (dict(category="bike", moving_s=7200, hard_s=3000), "bike"),
    (dict(category="road", moving_s=3000, hard_s=0, title="CP 3/12"), "test_cp"),
    (dict(category="road", moving_s=3000, hard_s=0, title="12 分測試"), "test_cp"),
    (dict(category="road", moving_s=3000, hard_s=0, plan_test={"cp": 250}), "test_cp"),
    (dict(category="road", moving_s=3000, hard_s=0, cp_detected=True), "test_cp"),
    (dict(category="road", moving_s=3000, hard_s=0, plan_test={"aethr": 140}), "test_aet"),
    (dict(category="road", moving_s=3600, hard_s=0, aet_steady=True), "test_aet"),
    (dict(category="road", moving_s=2700, hard_s=0, aet_steady=True), "easy"),      # < 55 min
    (dict(category="road", moving_s=3000, hard_s=900, hard_power_s=900, n_efforts=3), "quality"),
    # HR over LTHR but no work bout: an easy run that drifted, not intervals
    (dict(category="road", moving_s=3000, hard_s=900, hard_power_s=100, n_efforts=0), "easy"),
    (dict(category="road", moving_s=3000, hard_s=900), "quality"),                     # HR only, no power
    (dict(category="road", moving_s=3000, hard_s=900, hard_power_s=900, easy_hr=True), "easy"),
    (dict(category="hike", moving_s=3000, hard_s=1500), "quality"),                    # sustained climb above threshold counts
    (dict(category="hike", moving_s=3000, hard_s=1500, easy_hr=True), "easy"),         # avg HR ≤ AeT+3: still easy
    (dict(category="trail", moving_s=80 * 60, hard_s=0), "long"),
    (dict(category="road", moving_s=50 * 60, hard_s=0, long_target_s=60 * 60), "long"),
    (dict(category="road", moving_s=40 * 60, hard_s=0), "easy"),
])
def test_session_type(kw, expected):
    assert R.session_type(**kw) == expected


# ---------------------------------------------------------------------------
# efforts / intervals
# ---------------------------------------------------------------------------

def _intervals(reps=3, on_w=250.0, fade=0.0):
    """10' warm-up at 150 W, reps × (4' on, 2' at 120 W), 5' cool-down."""
    p, h = [150.0] * 600, [130.0] * 600
    for k in range(reps):
        w = on_w * (1 - fade * k / max(1, reps - 1))
        p += [w] * 240 + [120.0] * 120
        h += list(np.linspace(140, 165, 240)) + list(np.linspace(165, 125, 60)) + [125.0] * 60
    p += [150.0] * 300
    h += [130.0] * 300
    t = np.arange(len(p), dtype=float)
    return t, np.array(p), np.array(h)


def test_detect_efforts_finds_reps_and_recovery():
    t, p, h = _intervals()
    eff = R.detect_efforts(t, p, h, cp=220.0)
    assert len(eff) == 3
    for e in eff:
        assert e["duration_s"] == pytest.approx(240, abs=31)
        assert e["pct_cp"] == pytest.approx(250 / 220, rel=0.03)
        assert e["hr_drop60"] > 20                      # 165 → 125 in the 60 s of rest
    s = R.interval_summary(eff)
    assert s["band"][0] == "VO2max" and s["in_band"] == 3
    assert s["fade"] == pytest.approx(0.0, abs=0.01)


def test_fade_is_flagged():
    t, p, h = _intervals(reps=4, on_w=250.0, fade=0.12)
    eff = R.detect_efforts(t, p, h, cp=220.0)
    s = R.interval_summary(eff)
    assert s["fade"] < -R.FADE
    lines = R.interval_lines({"intervals": s})
    assert any("組數減 1" in ln for ln in lines)


def test_easy_run_has_no_efforts():
    t = _t(45)
    p = 150 + 5 * np.sin(t / 30)
    assert R.detect_efforts(t, p, None, cp=220.0) == []


def test_cp_test_formula_and_detection():
    p = [150.0] * 900 + [300.0] * 180 + [100.0] * 1800 + [240.0] * 720 + [120.0] * 600
    t = np.arange(len(p), dtype=float)
    r = R.cp_test(t, p)
    assert r["cp"] == pytest.approx((240 * 720 - 300 * 180) / 540, rel=0.01)
    assert r["wprime"] == pytest.approx((300 - r["cp"]) * 180, rel=0.02)
    assert r["separate"]
    assert R.looks_like_cp_test(r, cp_now=220.0)
    assert not R.looks_like_cp_test(r, cp_now=280.0)


# ---------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------

def test_drift_sign():
    t = _t(60)
    v = np.full(len(t), 10.0)
    up = R.drift_of(t, np.linspace(135, 150, len(t)), v)
    flat = R.drift_of(t, np.full(len(t), 140.0), v)
    assert up["ok"] and up["drift"] > 0.03            # HR drifted up at the same pace
    assert flat["ok"] and abs(flat["drift"]) < 1e-6
    assert up["hr2"] > up["hr1"]


def test_drift_excludes_warmup():
    t = _t(60)
    hr = np.where(t < 600, 100.0, 140.0)             # low HR only in the first 10 min
    r = R.drift_of(t, hr, np.full(len(t), 10.0))
    assert r["ok"] and abs(r["drift"]) < 1e-6


@pytest.mark.parametrize("kw, word", [
    (dict(climb_m_per_km=30.0), "有坡"),
    (dict(trail=True), "有坡"),
])
def test_drift_refuses_hills(kw, word):
    t = _t(60)
    r = R.drift_of(t, np.full(len(t), 140.0), np.full(len(t), 10.0), **kw)
    assert not r["ok"] and r["drift"] is None and word in r["reason"]


def test_drift_refuses_stops_short_and_unsteady():
    t = _t(60)
    hr = np.full(len(t), 140.0)
    v = np.full(len(t), 10.0)
    v[1800:2400] = 0.0                                 # 10 min standing
    r = R.drift_of(t, hr, v)
    assert not r["ok"] and "停" in r["reason"]
    short = _t(30)
    r = R.drift_of(short, np.full(len(short), 140.0), np.full(len(short), 10.0))
    assert not r["ok"] and "40" in r["reason"]
    p = np.where((t // 60) % 2 == 0, 100.0, 300.0)     # 1' on / 1' off
    r = R.drift_of(t, hr, np.full(len(t), 10.0), power=p, cp=300.0)
    assert not r["ok"] and "功率起伏" in r["reason"]
    r = R.drift_of(t, hr, np.full(len(t), 10.0), power=np.full(len(t), 280.0), cp=300.0)
    assert not r["ok"] and "CP" in r["reason"]


def test_aerobic_lines():
    base = {"aet": 140.0, "avg_hr": 135.0, "hr_s": 3000, "over_aet_s": 0}
    ok = {**base, "drift": {"ok": True, "drift": 0.03, "hr1": 135.0}}
    assert any("可以加一次閾值下間歇" in ln for ln in R.aerobic_lines("easy", ok, streak=3))
    assert not any("閾值下" in ln for ln in R.aerobic_lines("easy", ok, streak=2))
    mid = {**base, "drift": {"ok": True, "drift": 0.07, "hr1": 135.0}}
    assert any("暫時不加間歇" in ln for ln in R.aerobic_lines("easy", mid))
    bad = {**base, "drift": {"ok": True, "drift": 0.12, "hr1": 135.0}}
    assert any("有氧基礎不足" in ln for ln in R.aerobic_lines("easy", bad))
    fast = {**ok, "over_aet_s": 600}
    assert any("下次放慢" in ln for ln in R.aerobic_lines("easy", fast))
    assert any("可以設成 AeT" in ln for ln in R.aerobic_lines("test_aet", ok))


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------

def test_baseline_needs_five_samples():
    b = R.baseline([0.01, 0.02, 0.03, None, 0.04])
    assert not b["ok"] and b["n"] == 4
    assert R.compare(0.5, b) is None


def test_baseline_iqr_flags():
    b = R.baseline([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert b["ok"] and b["median"] == 5 and b["q1"] == 3 and b["q3"] == 7
    assert R.compare(8, b) == "high"
    assert R.compare(2, b) == "low"
    assert R.compare(5, b) == "within"


# ---------------------------------------------------------------------------
# streak and the quality gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drifts, n", [
    ([0.03, 0.08, 0.02, None, 0.04, 0.01], 3),        # refused runs (None) neither count nor break
    ([0.03, 0.06], 0),
    ([], 0),
    ([0.01, 0.02, 0.03, 0.04], 4),
])
def test_streak_of(drifts, n):
    assert R.streak_of(drifts) == n


@pytest.mark.parametrize("kind, levels, streak_ok, allowed", [
    ("base", {"intensity": "good", "drift": "good"}, False, False),
    ("base", {"intensity": "good", "drift": "good"}, True, True),
    ("base", {"intensity": "watch", "drift": "watch"}, True, True),
    ("base", {"intensity": "good", "drift": "bad"}, True, False),
    (None, {"intensity": "good", "drift": "good"}, False, False),       # no phase = base
    ("specific", {"intensity": "good", "drift": "good"}, False, True),
    ("specific", {"intensity": "bad", "drift": "good"}, False, False),
])
def test_quality_gate(kind, levels, streak_ok, allowed):
    assert R.quality_gate(kind, levels, streak_ok) is allowed


def test_next_quality():
    assert R.next_quality(None) == (3, 8)
    assert R.next_quality({"faded": False, "reps": 3}) == (3, 8)
    assert R.next_quality({"faded": True, "reps": 3}) == (2, 8)
    assert R.next_quality({"faded": True, "reps": 2}) == (2, 8)


# ---------------------------------------------------------------------------
# dataset adapters (FakeDataset)
# ---------------------------------------------------------------------------

def _run(day, minutes=45, hr=135.0, hr_end=None, kmh=10.0, power=None, tags=("running",)):
    t = _t(minutes)
    h = np.linspace(hr, hr_end if hr_end is not None else hr, len(t))
    ch = {"elapsedtime": list(t), "heartrate": list(h), "speed": [kmh] * len(t),
          "elapseddistance": list(t * kmh / 3600.0)}
    if power is not None:
        ch["power"] = list(power) if hasattr(power, "__len__") else [float(power)] * len(t)
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=list(tags),
                       sport_type="running", channels=ch,
                       metrics={"duration": minutes * 60.0, "movingduration": minutes * 60.0,
                                "distance": minutes / 60 * kmh, "climbing": 20.0})


SETTINGS = {"runthr": 160.0, "runftp": 250.0}       # AeT = 0.89 × 160 = 142.4


def test_drift_streak_counts_consecutive_easy_road_runs():
    today = dt.date(2026, 9, 30)
    ws = [_run(today - dt.timedelta(days=d)) for d in (9, 7, 5, 3)]
    ws.append(_run(today - dt.timedelta(days=11), hr=130, hr_end=155))       # older, drifted ~8%
    ws.append(_run(today - dt.timedelta(days=1), hr=160))                     # avg HR > AeT+3: not counted
    ws.append(_run(today - dt.timedelta(days=2), tags=("running", "runningtrail")))   # trail: not counted
    ds = FakeDataset(ws, today, settings=SETTINGS)
    st = R.drift_streak(ds, today)
    assert len(st["points"]) == 5
    assert st["streak"] == 4 and st["streak_ok"]
    older = R.drift_streak(ds, today, upto_idx=ds.workouts[0].idx)
    assert older["streak"] == 0 and not older["streak_ok"]


def test_review_summary_on_fake_easy_run():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_run(today, minutes=50)], today, settings=SETTINGS)
    w = ds.workouts[0]
    c = R.classify(ds, w)
    assert c["type"] == "easy" and c["terrain"] == "road"
    r = R.review(ds, w, "summary")
    assert r["kind"] == "review" and r["empty"] is None
    assert all(s["data"]["kind"] in ("value", "values") for s in r["series"])
    text = " ".join(s["data"]["value"] for s in r["series"] if s["data"]["kind"] == "value")
    assert "輕鬆跑" in text and "有氧基礎穩" in text
    assert r["suggested_dashboard"] == 1
    iv = R.review(ds, w, "intervals")
    assert iv["empty"]


def test_review_intervals_on_fake_quality_session():
    today = dt.date(2026, 9, 30)
    t, p, h = _intervals()
    ch = {"elapsedtime": list(t), "heartrate": list(h), "speed": [10.0] * len(t),
          "power": list(p), "elapseddistance": list(t * 10 / 3600.0)}
    fw = FakeWorkout(start=dt.datetime.combine(today, dt.time(7)), sport="run", tags=["running"],
                     sport_type="running", channels=ch,
                     metrics={"duration": float(len(t)), "movingduration": float(len(t)), "distance": 8.0})
    # LTHR 150 → AeT 133.5: the session's average HR (~139) is above AeT+3
    ds = FakeDataset([fw], today, settings={"runthr": 150.0, "runftp": 220.0})
    w = ds.workouts[0]
    assert R.classify(ds, w)["type"] == "quality"
    r = R.review(ds, w, "intervals")
    cols = {s["name"]: s["data"]["values"] for s in r["series"] if s["data"]["kind"] == "values"}
    assert len(cols["組"]) == 3
    assert R.review(ds, w, "summary")["suggested_dashboard"] == 2


def test_customview_accepts_review_kind():
    from backend.engine.wko5expr.customviews import CustomViewError, parse_view
    v = parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [
        {"title": "判讀", "kind": "review", "section": "summary"}]}]})
    ch = v["dashboards"][0]["charts"][0]
    assert ch["kind"] == "review" and ch["section"] == "summary"
    with pytest.raises(CustomViewError):
        parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [
            {"title": "判讀", "kind": "review", "section": "nope"}]}]})


def test_bundled_workout_view_parses():
    from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
    v = load_custom_views([REPO_VIEWS])["單次活動判讀"]
    assert [d["title"] for d in v["dashboards"]] == [
        "本次重點", "有氧／心率飄移", "間歇", "爬坡與地形", "配速與耐久", "跑姿與膝蓋負荷（參考）"]
    assert all(d["charts"][0]["kind"] == "review" for d in v["dashboards"])


# ---------------------------------------------------------------------------
# spec-sync regressions
# ---------------------------------------------------------------------------

def _hike(day, minutes=60, hr=140.0, watts=None, climb_w=None, climb_s=0):
    """A hike at `hr`; `climb_s` seconds at `climb_w` W in the middle."""
    t = _t(minutes)
    ch = {"elapsedtime": list(t), "heartrate": [hr] * len(t), "speed": [4.0] * len(t),
          "elapseddistance": list(t * 4.0 / 3600.0)}
    if watts is not None:
        p = np.full(len(t), float(watts))
        if climb_w is not None:
            a = len(t) // 2 - climb_s // 2
            p[a:a + climb_s] = climb_w
        ch["power"] = list(p)
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="hike", tags=["hiking"],
                       sport_type="hiking", channels=ch,
                       metrics={"duration": minutes * 60.0, "movingduration": minutes * 60.0,
                                "distance": minutes / 60 * 4.0, "climbing": 600.0})


HIKE_SETTINGS = {"otherthr": 160.0, "otherftp": 200.0}     # AeT 142.4, CP 200


def test_hike_reaches_quality_through_power():
    today = dt.date(2026, 9, 30)
    # HR stays under LTHR (150 < 160) but above AeT+3; 15' at 100 % CP
    ds = FakeDataset([_hike(today, hr=150.0, watts=120.0, climb_w=200.0, climb_s=900)], today,
                     settings=HIKE_SETTINGS)
    w = ds.workouts[0]
    m = R.measure(ds, w)
    assert m["hard_power_s"] == pytest.approx(900, abs=40)
    assert len(m["efforts"]) >= 1
    assert R.classify(ds, w, m)["type"] == "quality"
    # the same hike without the climb is not quality
    ds2 = FakeDataset([_hike(today, hr=150.0, watts=120.0)], today, settings=HIKE_SETTINGS)
    assert R.classify(ds2, ds2.workouts[0])["type"] != "quality"


def test_hard_hr_excludes_recording_gaps():
    today = dt.date(2026, 9, 30)
    # 5' at LTHR, a 30-minute recording gap, 5' at LTHR: 10' of samples, not 40'
    t = list(np.arange(0, 301, 1.0)) + list(np.arange(2101, 2402, 1.0))
    ch = {"elapsedtime": t, "heartrate": [165.0] * len(t), "speed": [10.0] * len(t),
          "elapseddistance": [x * 10 / 3600.0 for x in t]}
    fw = FakeWorkout(start=dt.datetime.combine(today, dt.time(7)), sport="run", tags=["running"],
                     sport_type="running", channels=ch,
                     metrics={"duration": 2400.0, "movingduration": 600.0, "distance": 1.7})
    ds = FakeDataset([fw], today, settings=SETTINGS)
    m = R.measure(ds, ds.workouts[0])
    assert m["hard_s"] == pytest.approx(600, abs=5)


def test_hr_drop_ignores_a_short_blip_after_the_effort():
    # 4' effort, 50 s easy, a 30-s surge (too short to be an effort), then rest
    p = [150.0] * 600 + [250.0] * 240 + [120.0] * 50 + [250.0] * 30 + [120.0] * 600
    h = [130.0] * 600 + list(np.linspace(140, 165, 240)) + list(np.linspace(165, 125, 60)) + [125.0] * 620
    t = np.arange(len(p), dtype=float)
    eff = R.detect_efforts(t, np.array(p), np.array(h), cp=220.0)
    assert len(eff) == 1
    assert eff[0]["hr_drop60"] is not None and eff[0]["hr_drop60"] > 20


def test_last_quality_includes_hikes_and_sorts_by_date():
    today = dt.date(2026, 9, 30)
    t, p, h = _intervals()
    run_ch = {"elapsedtime": list(t), "heartrate": list(h), "speed": [10.0] * len(t),
              "power": list(p), "elapseddistance": list(t * 10 / 3600.0)}
    run = FakeWorkout(start=dt.datetime.combine(today - dt.timedelta(days=6), dt.time(7)), sport="run",
                      tags=["running"], sport_type="running", channels=run_ch,
                      metrics={"duration": float(len(t)), "movingduration": float(len(t)), "distance": 8.0})
    hike = _hike(today - dt.timedelta(days=2), hr=150.0, watts=120.0, climb_w=230.0, climb_s=900)
    ds = FakeDataset([run, hike], today,
                     settings={"runthr": 150.0, "runftp": 220.0, **HIKE_SETTINGS})
    hike_idx = next(w.idx for w in ds.workouts if w.sport == "hike")
    ds.workouts.reverse()                                 # not in date order
    q = R.last_quality(ds, today)
    assert q is not None and q["idx"] == hike_idx
    assert q["date"] == (today - dt.timedelta(days=2)).isoformat()
