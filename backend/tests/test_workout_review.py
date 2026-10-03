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
    (dict(category="strength", moving_s=1800), ("strength", None)),
    (dict(category="bike", moving_s=7200, stimulus="z5"), ("bike", None)),
    (dict(category="road", moving_s=3000, title="CP 3/12", stimulus="z5"), ("test_cp", None)),
    (dict(category="road", moving_s=3000, title="12 分測試"), ("test_cp", None)),
    (dict(category="road", moving_s=3000, plan_test={"cp": 250}), ("test_cp", None)),
    (dict(category="road", moving_s=3000, cp_detected=True), ("test_cp", None)),
    (dict(category="road", moving_s=3000, plan_test={"aethr": 140}), ("test_aet", None)),
    (dict(category="road", moving_s=3600, aet_steady=True), ("test_aet", None)),
    (dict(category="road", moving_s=2700, aet_steady=True), ("easy", None)),      # < 55 min
    # the session classifier (docs/research/vo2max-session-detection.md §3.5)
    (dict(category="road", moving_s=3000, stimulus="z5"), ("quality", "z5")),
    (dict(category="road", moving_s=3000, stimulus="z3"), ("quality", "z3")),
    (dict(category="road", moving_s=120 * 60, stimulus="z5"), ("quality", "z5")),  # a race: Z5 even when long
    (dict(category="trail", moving_s=150 * 60, stimulus="z3"), ("hard_long", "z3")),  # threshold climbs in a long day
    (dict(category="hike", moving_s=300 * 60, stimulus="z3"), ("hard_long", "z3")),
    (dict(category="hike", moving_s=300 * 60, stimulus="z5"), ("long", None)),     # 百岳 never auto-Z5
    (dict(category="hike", moving_s=50 * 60, stimulus="z3"), ("quality", "z3")),
    (dict(category="trail", moving_s=80 * 60), ("long", None)),
    (dict(category="road", moving_s=50 * 60, long_target_s=60 * 60), ("long", None)),
    (dict(category="road", moving_s=40 * 60), ("easy", None)),
])
def test_session_class(kw, expected):
    assert R.session_class(**kw) == expected
    assert R.session_type(**kw) == expected[0]


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
    assert not r["ok"] and not r["ref_ok"] and r["tier"] is None and "< 30 分" in r["reason"]
    p = np.where((t // 60) % 2 == 0, 100.0, 300.0)     # 1' on / 1' off
    r = R.drift_of(t, hr, np.full(len(t), 10.0), power=p, cp=300.0)
    assert not r["ok"] and "功率起伏" in r["reason"]
    r = R.drift_of(t, hr, np.full(len(t), 10.0), power=np.full(len(t), 280.0), cp=300.0)
    assert not r["ok"] and "CP" in r["reason"]


def _warmup_run(minutes, warm_hr=110.0, warm_kmh=8.0):
    """A 10′ warm-up (low HR, slower), then steady 10 km/h with HR 140 → 147."""
    t = _t(minutes)
    after = t >= 600
    hr = np.where(after, 140.0 + 7.0 * (t - 600) / max(1.0, t[-1] - 600), warm_hr)
    v = np.where(after, 10.0, warm_kmh)
    return t, hr, v


def test_drift_counts_the_40_minutes_after_the_warmup():
    # 48′ = 10′ warm-up + 38′: the old floor (40′ total) took it, UA's 40′ after the warm-up doesn't —
    # strict refused; the 參考 tier (≥ 30′ after the warm-up, 推估) keeps the number
    t, hr, v = _warmup_run(48)
    r = R.drift_of(t, hr, v)
    assert not r["ok"] and r["ref_ok"] and r["tier"] == "ref" and r["drift"] is not None
    assert "暖身後只有 38 分鐘" in r["reason"] and "< 40 分" in r["reason"]
    assert R.basis_drift(r)[0] is None and R.basis_drift(r, ref=True)[0] == r["drift"]
    # 51′ on the clock, but a 90-s stop (< 5 %) after the warm-up leaves 39.5′ of moving time
    t, hr, v = _warmup_run(51)
    v[1200:1290] = 0.0
    r = R.drift_of(t, hr, v)
    assert not r["ok"] and r["tier"] == "ref" and "暖身後只有 39 分鐘" in r["reason"]
    assert r["measured_s"] == pytest.approx(39.5 * 60, abs=2)
    # 52′ = 10′ + 42′: measured on the 42′ after the warm-up only
    t, hr, v = _warmup_run(52)
    r = R.drift_of(t, hr, v)
    assert r["ok"] and r["measured_s"] == pytest.approx(42 * 60, abs=2)
    # the warm-up's low HR / speed is not in the first half: HR 140 → 143.5 over the first 21′
    assert r["hr1"] == pytest.approx(140.0 + 3.5 / 2, abs=0.05) and r["v1"] == pytest.approx(10.0)
    plain = (10.0 / r["hr1"] - 10.0 / r["hr2"]) / (10.0 / r["hr1"])
    assert r["drift"] == pytest.approx(plain, abs=1e-12) and r["drift"] > 0.02     # 141.75 → 145.25 bpm


def test_drift_keeps_a_hot_run_with_its_band_and_source():
    # heat bands: > 25 °C is no longer a refusal
    t, hr, v = _warmup_run(60)
    p = np.full(len(t), 200.0)
    r = R.drift_of(t, hr, v, p, cp=300.0, temp_c=27.4, temp_src="watch")
    assert r["ok"] and r["pw_ok"] and r["temp_band"] == "warm" and r["heat"] and not r["reason"]
    r = R.drift_of(t, hr, v, temp_c=29.0, temp_src="route_weather")
    assert r["ok"] and r["temp_band"] == "hot" and r["temp_src"] == "route_weather"
    r = R.drift_of(t, hr, v, temp_c=24.0, temp_src="watch")
    assert r["ok"] and r["temp_c"] == 24.0 and r["temp_band"] == "cool" and not r["heat"]
    assert R.basis_drift(R.heat_band(r, 30.0, "watch"), "pace")[0] == r["drift"]
    assert R.band_chip(R.heat_band(r, 30.0, "watch")) == "🌡 > 28 °C"


def _hot_run(day, temp):
    w = _run(day, minutes=60)
    w.channels["temperature"] = [float(temp)] * len(w.channels["elapsedtime"])
    return w


def test_measure_uses_the_archive_first_then_the_watch():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_hot_run(today, 28.0)], today, settings=SETTINGS)
    w = ds.workouts[0]
    ds.activity_temps = {}                                          # no archive: the watch's 28 °C …
    m = R.measure(ds, w)
    assert m["watch_temp_c"] == pytest.approx(28.0)                 # (raw, cached)
    # … minus the wrist bias (3.7 °C): 24.3 °C in the air, the cool band, lower confidence
    assert m["drift"]["ok"] and m["drift"]["temp_src"] == "watch"
    assert m["drift"]["temp_c"] == pytest.approx(28.0 - R.WATCH_BIAS_C) and m["drift"]["temp_band"] == "cool"
    ds.activity_temps = {w.entry.file: 22.4}                         # the archive's air temperature wins
    m = R.measure(ds, w)
    assert m["drift"]["ok"] and m["drift"]["temp_c"] == 22.4 and m["drift"]["temp_src"] == "route_weather"
    res = R.review(ds, w, "aerobic")
    rows = {s["name"]: s["data"]["value"] for s in res["series"]}
    assert rows["溫度"] == "路線天氣（Open-Meteo 檔案） 22 °C · 🌡 < 25 °C"
    assert res["chip"]["text"] == "🌡 < 25 °C" and not res["chip"]["heat"] and "推估" in res["chip"]["tip"]
    ds.activity_temps = {w.entry.file: 29.0}                         # a hot run: kept, chipped, noted
    m = R.measure(ds, w)
    assert m["drift"]["ok"] and m["drift"]["temp_band"] == "hot" and m["drift"]["heat"]
    res = R.review(ds, w, "aerobic")
    rows = {s["name"]: s["data"]["value"] for s in res["series"]}
    assert res["chip"]["text"] == "🌡 > 28 °C" and res["chip"]["heat"]
    assert "心率飄移（配速）" in rows and R.HEAT_NOTE in rows["溫度"]
    assert any(R.HEAT_NOTE in s["data"]["value"] for s in res["series"] if s["name"] in ("判讀", ""))


def test_activity_temp_reads_the_route_weather_archive(tmp_path, monkeypatch):
    import json
    from backend.engine import route_weather as RW
    from backend.engine import routes as RT
    monkeypatch.setattr(RT, "HOME", tmp_path)
    R._WX_CACHE.clear()
    (tmp_path / RW.ACTIVITY_WX_FILE).write_text(json.dumps(
        {"version": RW.ACTIVITY_WX_VERSION, "activities": {"fake/0.wko4": {"temp_c": 26.3}, "fake/1.wko4": None}}),
        "utf-8")
    ds = FakeDataset([_run(dt.date(2026, 9, 30)), _run(dt.date(2026, 9, 29))], dt.date(2026, 9, 30),
                     settings=SETTINGS)
    w0 = next(w for w in ds.workouts if w.entry.file == "fake/0.wko4")
    w1 = next(w for w in ds.workouts if w.entry.file == "fake/1.wko4")
    assert R.activity_temp(ds, w0) == (26.3, "route_weather")
    # the watch: minus the wrist bias
    assert R.activity_temp(ds, w1, {"watch_temp_c": 21.0}) == (pytest.approx(21.0 - R.WATCH_BIAS_C), "watch")
    assert R.activity_temp(ds, w1) == (None, None)
    d = R.measure(ds, w0)["drift"]
    assert d["ok"] and d["temp_band"] == "warm"                      # 26.3 °C: kept, in its band
    R._WX_CACHE.clear()


def test_activity_temp_falls_back_to_the_only_archive_row_of_that_date(tmp_path, monkeypatch):
    # the archive is keyed by the WKO5 file; a COROS dataset's files are named otherwise
    import json
    from backend.engine import route_weather as RW
    from backend.engine import routes as RT
    monkeypatch.setattr(RT, "HOME", tmp_path)
    R._WX_CACHE.clear()
    (tmp_path / RW.ACTIVITY_WX_FILE).write_text(json.dumps(
        {"version": RW.ACTIVITY_WX_VERSION, "activities": {
            "2026/Y_2026_09_30_20_30.wko4": {"date": "2026-09-30", "temp_c": 27.0},
            "2026/Y_2026_09_29_06_00.wko4": {"date": "2026-09-29", "temp_c": 24.0},
            "2026/Y_2026_09_29_18_00.wko4": {"date": "2026-09-29", "temp_c": 29.0}}}), "utf-8")
    ds = FakeDataset([_run(dt.date(2026, 9, 30)), _run(dt.date(2026, 9, 29))], dt.date(2026, 9, 30),
                     settings=SETTINGS)
    by_day = {R._wdate(w).isoformat(): w for w in ds.workouts}
    assert R.activity_temp(ds, by_day["2026-09-30"]) == (27.0, "route_weather")
    assert R.activity_temp(ds, by_day["2026-09-29"]) == (None, None)        # two rows that day: ambiguous
    R._WX_CACHE.clear()


@pytest.mark.parametrize("channel", ["speed", "power"])
def test_drift_refuses_a_fast_finish(channel):
    t, hr, v = _warmup_run(60)
    p = np.full(len(t), 200.0)
    last = t > 600 + 0.9 * (t[-1] - 600)                            # the last 10 % of the measured time
    x = v if channel == "speed" else p
    x[last] *= 1.08
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert not r["ok"] and "快速結尾" in r["reason"] and "8%" in r["reason"]
    assert ("配速" if channel == "speed" else "功率") in r["reason"]
    assert r["finish"] == pytest.approx(0.08, abs=0.005)
    # +3 %: within the 5 % (推估) — a fair run
    t, hr, v = _warmup_run(60)
    p = np.full(len(t), 200.0)
    (v if channel == "speed" else p)[last] *= 1.03
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and r["finish"] == pytest.approx(0.03, abs=0.005)


def test_pa_and_pw_share_one_window():
    t, hr, v = _warmup_run(60)
    p = np.full(len(t), 200.0)
    p[2000:2060] = np.nan                                           # a 1-min Stryd dropout (< 5 %)
    hr2 = hr.copy()
    hr2[2000:2060] = 200.0                                          # …where HR spikes: both bases drop it
    r = R.drift_of(t, hr2, v, power=p, cp=300.0)
    assert r["ok"] and r["pw_ok"]
    assert r["pw_hr1"] == pytest.approx(r["hr1"], abs=1e-12) and r["pw_hr2"] == pytest.approx(r["hr2"], abs=1e-12)
    assert r["pw_drift"] == pytest.approx(r["drift"], abs=1e-12)    # constant speed and power
    assert r["measured_s"] == pytest.approx(50 * 60 - 60, abs=2)
    # power on only 80 % of the window: Pa:HR keeps the whole window, Pw:HR is refused
    p = np.full(len(t), 200.0)
    p[2400:3000] = np.nan
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and not r["pw_ok"] and "涵蓋" in r["pw_reason"]
    assert r["measured_s"] == pytest.approx(50 * 60, abs=2)


def test_aerobic_lines():
    base = {"aet": 140.0, "avg_hr": 135.0, "hr_s": 3000, "over_aet_s": 0}
    ok = {**base, "drift": {"ok": True, "drift": 0.03, "hr1": 135.0}}
    # informational only: the unsourced 「連續 3 次 → 加間歇」 line is gone (engine/quality_gate.py)
    assert any("有氧基礎穩" in ln for ln in R.aerobic_lines("easy", ok, streak=3))
    assert not any("間歇" in ln for ln in R.aerobic_lines("easy", ok, streak=3))
    mid = {**base, "drift": {"ok": True, "drift": 0.07, "hr1": 135.0}}
    assert any("後段心率往上跑" in ln for ln in R.aerobic_lines("easy", mid))
    bad = {**base, "drift": {"ok": True, "drift": 0.12, "hr1": 135.0}}
    assert any("有氧基礎不足" in ln for ln in R.aerobic_lines("easy", bad))
    fast = {**ok, "over_aet_s": 600}
    assert any("下次放慢" in ln for ln in R.aerobic_lines("easy", fast))
    # UA's three bands on the AeT test
    assert any("還在 AeT 以下" in ln for ln in R.aerobic_lines("test_aet", ok))
    at = {**base, "drift": {"ok": True, "drift": 0.042, "hr1": 146.0}}
    assert any("就是 AeT" in ln for ln in R.aerobic_lines("test_aet", at))
    hi = {**base, "drift": {"ok": True, "drift": 0.06, "hr1": 150.0}}
    assert any("放慢 5 bpm" in ln for ln in R.aerobic_lines("test_aet", hi))


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


@pytest.mark.parametrize("kind, levels, gate, allowed", [
    # no method (legacy bool / None): base is no longer held back by a drift streak
    ("base", {"intensity": "good", "drift": "good"}, False, True),
    ("base", {"intensity": "watch", "drift": "watch"}, None, True),
    ("base", {"intensity": "bad", "drift": "good"}, None, False),
    (None, {"intensity": "good", "drift": "good"}, False, True),        # no phase = base
    # a locked method keeps Zone 5 closed only; Zone 3 still goes on (台灣教練, two gates)
    ("base", {"intensity": "good", "drift": "good"}, {"state": "locked", "verdict": "x"}, True),
    ("base", {"intensity": "good", "drift": "good"}, {"state": "unlocked", "dose": {"step": 0}}, True),
    ("specific", {"intensity": "good", "drift": "good"}, False, True),
    ("specific", {"intensity": "bad", "drift": "good"}, False, False),
    ("specific", {"intensity": "good", "drift": "bad"}, False, False),
])
def test_quality_gate(kind, levels, gate, allowed):
    assert R.quality_gate(kind, levels, gate) is allowed


def test_next_quality():
    assert R.next_quality(None) == (3, 8)
    assert R.next_quality({"faded": False, "reps": 3}) == (3, 8)
    assert R.next_quality({"faded": True, "reps": 3}) == (2, 8)
    assert R.next_quality({"faded": True, "reps": 2}) == (2, 8)


# ---------------------------------------------------------------------------
# dataset adapters (FakeDataset)
# ---------------------------------------------------------------------------

# 52 min = the 10′ warm-up + 42′: drift_of wants ≥ 40 min after the warm-up
def _run(day, minutes=52, hr=135.0, hr_end=None, kmh=10.0, power=None, tags=("running",)):
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


def _hr_climb(w, hr_climb: float, climb_s: int):
    """`w` with HR at `hr_climb` for `climb_s` seconds in the middle."""
    h = np.asarray(w.channels["heartrate"], dtype=float)
    a = len(h) // 2 - climb_s // 2
    h[a:a + climb_s] = hr_climb
    w.channels["heartrate"] = list(h)
    return w


def test_hike_reaches_zone3_through_hr_not_power():
    today = dt.date(2026, 9, 30)
    # walking power is not comparable (UA): 15' at 100 % CP with HR under 0.95 LTHR (152) is not Zone 3
    ds = FakeDataset([_hike(today, hr=150.0, watts=120.0, climb_w=200.0, climb_s=900)], today,
                     settings=HIKE_SETTINGS)
    w = ds.workouts[0]
    c = R.classify(ds, w)
    assert c["type"] == "easy" and c["moderate"] and c["type_label"] == "中強度健行"
    # 18' at 158 bpm (≥ 0.95 × 160): 18 − 3 (HR lag) = 15' of Zone 3 → a Z3 hike (< 75 min)
    ds2 = FakeDataset([_hr_climb(_hike(today, hr=150.0), 158.0, 18 * 60)], today, settings=HIKE_SETTINGS)
    c2 = R.classify(ds2, ds2.workouts[0])
    assert c2["type"] == "quality" and c2["stimulus"] == "z3" and c2["type_label"] == "Z3 閾值"
    assert c2["stim"]["z3_s"] == pytest.approx(15 * 60, abs=40)
    # a long day with the same climb: 高強度長天, not an interval session
    ds3 = FakeDataset([_hr_climb(_hike(today, minutes=240, hr=150.0), 158.0, 18 * 60)], today, settings=HIKE_SETTINGS)
    c3 = R.classify(ds3, ds3.workouts[0])
    assert c3["type"] == "hard_long" and c3["type_label"] == "高強度長天"
    # 百岳 never auto-Z5, even with HR near max for a long time
    ds4 = FakeDataset([_hr_climb(_hike(today, minutes=240, hr=150.0), 185.0, 40 * 60)], today, settings=HIKE_SETTINGS)
    assert ds4.workouts and R.classify(ds4, ds4.workouts[0])["stimulus"] != "z5"


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
    hike = _hr_climb(_hike(today - dt.timedelta(days=2), hr=150.0), 158.0, 18 * 60)     # a Z3 hike (HR path)
    ds = FakeDataset([run, hike], today,
                     settings={"runthr": 150.0, "runftp": 220.0, **HIKE_SETTINGS})
    hike_idx = next(w.idx for w in ds.workouts if w.sport == "hike")
    ds.workouts.reverse()                                 # not in date order
    q = R.last_quality(ds, today)
    assert q is not None and q["idx"] == hike_idx
    assert q["date"] == (today - dt.timedelta(days=2)).isoformat()
