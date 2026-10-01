"""engine/zone_events.py — event-driven zone updates (docs/research/
zones-and-thresholds.md §2.5): the cool-day HR-at-power shift (wrist-HR
safe steady points), ≥ 4 weeks off, the first cool spell of the season, and
the suggestion objects in the testing indicator. Suggestions never schedule
a test. Pure inputs only — no WKO5 folder, no real DB, no real weather."""
import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import zone_events as ZE
from backend.engine.planning import Plan, Threshold

TODAY = dt.date(2026, 11, 20)


# ---- steady points: wrist HR ------------------------------------------------------

def _windows(n, hr, p=200.0, v=3.0, t0=600.0, k0=0, g=0.0):
    """n 100-m windows at v m/s from t0 s; hr: a number or a list."""
    hrs = hr if isinstance(hr, list) else [hr] * n
    dur = 100.0 / v
    return [{"k": k0 + i, "t": t0 + i * dur, "v": v, "p": p, "hr": hrs[i], "hr_lag": None, "g": g, "run": 1.0}
            for i in range(n)]


def test_run_point_drops_the_first_two_minutes_after_a_change():
    # 30 windows × 33.3 s = 1000 s; the first 4 (133 s ≥ 120 s) still show the wrist catching up
    hr = [175.0, 170.0, 165.0, 160.0] + [150.0] * 26
    pt = ZE.run_point(_windows(30, hr))
    assert pt["hr"] == 150.0 and pt["p"] == pytest.approx(200.0)
    assert pt["seconds"] == pytest.approx(26 * 100 / 3.0)


def test_run_point_needs_ten_steady_minutes_after_the_settle():
    assert ZE.run_point(_windows(20, 150.0)) is None             # 667 s − 133 s < 600 s
    # a hill in the middle cuts the stretch in two short ones
    w = _windows(40, 150.0)
    for x in w[18:22]:
        x["g"] = 0.06
    assert ZE.run_point(w) is None
    # the first 10 minutes of the run never count
    assert ZE.run_point(_windows(30, 150.0, t0=0.0)) is None      # 1000 s from the start: 400 s after 10 min


def test_run_point_rejects_unsteady_power():
    w = _windows(40, 150.0)
    for i, x in enumerate(w):
        x["p"] = 150.0 if i % 2 else 250.0                          # CV 25 %
    assert ZE.run_point(w) is None


# ---- the heat-adjusted HR shift ----------------------------------------------------------
# Synthetic athlete: HR = 120 + 0.18·P + β·(Hadley − 120) (β = the personal 0.224)
# + a ±0.8 bpm wrist wobble + `offset` (the fitness change to detect).

B = 0.224


def _pts(days_ago, offset=0.0, hadley=110.0, start=None, src="route_weather", sigma_h=0.0, noise=0.8):
    start = start or TODAY
    out = []
    for i, d in enumerate(days_ago):
        p = 160.0 + (i * 7) % 30
        out.append({"date": (start - dt.timedelta(days=d)).isoformat(), "p": p,
                    "hr": 120.0 + 0.18 * p + B * (hadley - 120.0) + offset + (noise if i % 2 else -noise),
                    "hadley": hadley, "src": src, "sigma_h": sigma_h})
    return out


BASE = list(range(300, 100, -20))          # 10 runs, 100–300 days ago
RECENT = [50, 40, 30, 20, 10, 2]


def test_hr_shift_fires_on_a_persistent_rise():
    pts = _pts(BASE) + _pts(RECENT, offset=7.0)
    r = ZE.hr_shift(pts, TODAY)
    assert r["fired"] and r["direction"] == "up" and r["shift_bpm"] == pytest.approx(7.0, abs=1.0)
    assert r["n"] == 6 and r["same_side"] == 6 and r["need_same_side"] == 5 and r["line"]["n"] == 10
    assert r["threshold_bpm"] == pytest.approx(max(5.0, 2 * r["se_bpm"]), abs=0.11)
    assert r["heat"]["adjusted"] and r["heat"]["confidence"] == "high" and r["heat"]["beta"] == pytest.approx(0.224)


def test_hr_shift_small_or_mixed_does_not_fire():
    assert not ZE.hr_shift(_pts(BASE) + _pts(RECENT, offset=3.0), TODAY)["fired"]
    mixed = _pts(BASE) + _pts([50, 40, 30], offset=9.0) + _pts([20, 10, 2], offset=-9.0)
    r = ZE.hr_shift(mixed, TODAY)
    assert not r["fired"] and r["reason"]


def test_hr_shift_one_wrist_outlier_is_allowed():
    rec = _pts(RECENT, offset=-8.0)
    rec[2]["hr"] += 12.0                                             # one optical glitch the other way
    r = ZE.hr_shift(_pts(BASE) + rec, TODAY)
    assert r["fired"] and r["direction"] == "down" and r["same_side"] == 5


def test_hot_season_runs_give_no_false_shift():
    # a cool-season baseline, then a humid summer (Hadley 158): the raw HR is
    # β·38 ≈ 8.5 bpm higher at the same power, but fitness has not changed
    hot = _pts(RECENT, hadley=158.0)
    r = ZE.hr_shift(_pts(BASE) + hot, TODAY)
    assert not r["fired"] and abs(r["shift_bpm"]) < 1.0
    assert all(x["hr"] - x["hr_adj"] == pytest.approx(B * 38.0, abs=0.6) for x in r["recent"])
    # without the heat adjustment the same runs would look like a 8.5 bpm shift
    raw = ZE.hr_shift(_pts(BASE) + hot, TODAY, beta=0.0, beta_se=0.0)
    assert raw["fired"] and raw["shift_bpm"] > 8.0
    # β's own uncertainty grows with the heat gap between the windows
    assert r["heat"]["se_parts"]["beta"] == pytest.approx(0.036 * 48.0, abs=0.05)


def test_a_real_shift_is_detected_in_the_hot_season():
    base = _pts(BASE[:5], hadley=155.0) + _pts(BASE[5:], hadley=112.0)   # a year of both seasons
    r = ZE.hr_shift(base + _pts(RECENT, hadley=158.0, offset=8.0), TODAY)
    assert r["fired"] and r["direction"] == "up" and r["shift_bpm"] == pytest.approx(8.0, abs=1.0)
    # the joint fit on the baseline (a cross-check only) finds the same β
    assert r["heat"]["beta_check"]["beta"] == pytest.approx(B, abs=0.05) and r["heat"]["beta_check"]["n"] == 10


def test_hr_shift_threshold_follows_the_se():
    # a noisy baseline (±8 bpm wrist scatter): 2·SE is above 5 bpm, a clean 7 bpm shift is not enough
    base = _pts(BASE, noise=8.0)
    for i, p in enumerate(base):                                     # a wide power range keeps the slope positive
        p["hr"] += 0.18 * (140.0 + 8 * i - p["p"])
        p["p"] = 140.0 + 8 * i
    r = ZE.hr_shift(base + _pts(RECENT, offset=7.0, noise=0.0), TODAY)
    assert r["se_bpm"] > 2.5 and r["threshold_bpm"] > 7.0 and not r["fired"]
    assert "max(5, 2·SE)" in r["reason"]


def test_hr_shift_needs_a_baseline():
    r = ZE.hr_shift(_pts([150, 140]) + _pts(RECENT, offset=9.0), TODAY)
    assert not r["fired"] and "基準線" in r["reason"]


def test_hr_shift_compares_only_at_the_same_power():
    far = _pts(RECENT, offset=9.0)
    for p in far:
        p["p"] += 80.0                                              # far outside the baseline's 160–189 W
    r = ZE.hr_shift(_pts(BASE) + far, TODAY)
    assert not r["fired"] and "功率範圍" in r["reason"]


def test_runs_without_any_temperature_are_left_out():
    rec = _pts(RECENT + [5], offset=7.0)
    rec[-1]["hadley"] = None                                         # no archive, no watch, no season
    rec[-1]["hr"] += 30.0                                            # it would have pulled the median
    r = ZE.hr_shift(_pts(BASE) + rec, TODAY)
    assert r["fired"] and r["heat"]["n_no_temp"] == 1 and r["n"] == 6
    none = _pts(BASE) + [{**p, "hadley": None} for p in _pts(RECENT, offset=7.0)]
    r = ZE.hr_shift(none, TODAY)
    assert not r["fired"] and "沒有任何溫度" in r["reason"]


def test_watch_only_runs_count_with_lower_confidence_and_a_wider_se():
    good = ZE.hr_shift(_pts(BASE) + _pts(RECENT, offset=7.0), TODAY)
    watch = ZE.hr_shift(_pts(BASE) + _pts(RECENT, offset=7.0, src="watch", sigma_h=10.0), TODAY)
    assert watch["heat"]["confidence"] == "low" and watch["heat"]["recent_sources"] == {"watch": 6}
    assert watch["se_bpm"] > good["se_bpm"]
    mixed = _pts(RECENT, offset=7.0)
    mixed[0].update(src="watch", sigma_h=10.0)
    assert ZE.hr_shift(_pts(BASE) + mixed, TODAY)["heat"]["confidence"] == "medium"


def test_same_season_last_year_explains_a_seasonal_residual():
    # the same +7 bpm already showed last autumn (365 ± 30 days before): a season the heat
    # adjustment did not remove, not a fitness change
    ly = _pts([400, 395, 390, 385, 380, 375], offset=7.0)
    base = _pts(list(range(360, 60, -5)))
    r = ZE.hr_shift(ly + base + _pts([45, 35, 25, 20, 10, 2], offset=7.0), TODAY)
    assert r["seasonal"]["used"] and r["seasonal"]["residual_bpm"] > 4.0
    assert not r["fired"] and "去年同季" in r["reason"]
    # last year's same season on the line: the shift stands
    r = ZE.hr_shift(_pts([400, 395, 390, 385, 380, 375]) + base + _pts([45, 35, 25, 20, 10, 2], offset=7.0), TODAY)
    assert r["fired"] and abs(r["seasonal"]["net_bpm"] - r["shift_bpm"]) < 1.5


# ---- temperature source per run ----------------------------------------------------------

def _run(i, day, file=None):
    return SimpleNamespace(idx=i, sport="run", tags=[], day=0,
                           entry=SimpleNamespace(file=file or f"{i}.fit", start=dt.datetime(day.year, day.month,
                                                                                           day.day, 6)))


def _ds(runs, watch: dict):
    def channel(idx, name):
        if name == "temperature" and idx in watch:
            return [watch[idx]] * 60
        if name == "elapsedtime" and idx in watch:
            return [float(s * 20) for s in range(60)]
        return None
    return SimpleNamespace(workouts=runs, channel=channel)


def test_run_heat_prefers_route_weather_then_watch_then_season():
    d = dt.date(2026, 7, 10)
    runs = [_run(0, d), _run(1, d + dt.timedelta(days=1)), _run(2, d + dt.timedelta(days=2)),
            _run(3, d + dt.timedelta(days=3))]
    acts = [{"file": "0.fit", "date": d.isoformat(), "temp_c": 29.0, "rh_pct": 75.0, "hadley": 156.0}]
    # last summer's archive days: the season fallback and the RH for the watch
    acts += [{"file": f"old{k}.fit", "date": (d - dt.timedelta(days=365 + k)).isoformat(), "temp_c": 29.0,
              "rh_pct": 80.0, "hadley": 150.0 + k} for k in range(-5, 6)]
    ds = _ds(runs, {0: 34.0, 1: 33.7, 2: float("nan")})
    h = ZE.run_heat(ds, runs, acts)
    assert h[0]["src"] == "route_weather" and h[0]["hadley"] == 156.0 and h[0]["sigma_h"] == 0.0
    w = h[1]
    assert w["src"] == "watch" and w["temp_c"] == pytest.approx(33.7 - ZE.WATCH_BIAS_C, abs=0.05)
    assert w["rh_pct"] == 80 and w["sigma_h"] > 5.0 and w["bias"]["src"] == "route_efforts"
    from backend.engine.heat import hadley_sum
    assert w["hadley"] == pytest.approx(hadley_sum(30.0, 80.0), abs=0.2)
    assert h[2]["src"] == "season" and 149.0 <= h[2]["hadley"] <= 152.0 and h[2]["sigma_h"] > 2.0
    assert h[3]["src"] == "season"
    # nothing at all (no archive season either): left out
    assert ZE.run_heat(_ds(runs[3:], {}), runs[3:], [])  == {}


def test_watch_bias_from_the_datasets_own_pairs():
    b = ZE.watch_bias([(20.0 + i, 22.0 + i) for i in range(12)])
    assert b["src"] == "dataset" and b["bias_c"] == pytest.approx(2.0) and b["n"] == 12
    assert ZE.watch_bias([(20.0, 25.0)])["bias_c"] == ZE.WATCH_BIAS_C


# ---- the first cool spell -------------------------------------------------------------

def _days(start, n, temp):
    return [{"date": (start + dt.timedelta(days=i)).isoformat(), "temp_c": temp} for i in range(n)]


def test_cool_season_after_summer():
    s = dt.date(2026, 9, 1)
    acts = _days(s, 40, 28.0) + _days(s + dt.timedelta(days=40), 3, 22.0)
    cs = ZE.cool_season(acts, TODAY)
    assert cs["start"] == "2026-10-11" and cs["summer_days"] >= 10
    # a spell with one warm day inside is not 3 in a row
    acts = _days(s, 40, 28.0) + _days(s + dt.timedelta(days=40), 2, 22.0) + _days(s + dt.timedelta(days=42), 1, 26.0)
    assert ZE.cool_season(acts, TODAY) is None


def test_no_cool_season_without_a_summer():
    acts = _days(dt.date(2026, 1, 5), 30, 18.0)
    assert ZE.cool_season(acts, dt.date(2026, 2, 20)) is None


def test_cool_season_reads_the_dawn_not_the_run_hour():
    # evening runs after rain at 24 °C in September, while the dawns are still in the hot band
    s = dt.date(2026, 8, 1)
    runs = [{**x, "hadley": 156.0} for x in _days(s, 40, 28.0)]
    runs += [{**x, "hadley": 145.0} for x in _days(s + dt.timedelta(days=40), 3, 24.0)]
    hot_dawn = {x["date"]: {"temp_c": 25.6, "hadley": 153.0} for x in runs}
    assert ZE.cool_season(runs, TODAY) is not None                   # the run hour alone says cool
    assert ZE.cool_season(runs, TODAY, hot_dawn) is None             # the dawn says still summer
    # a humid 24 °C dawn at Hadley ≥ 150 is not cool either; a 22 °C dawn below 150 is
    cool = dict(hot_dawn)
    for x in runs[40:]:
        cool[x["date"]] = {"temp_c": 22.0, "hadley": 140.0}
    cs = ZE.cool_season(runs, TODAY, cool)
    assert cs["start"] == runs[40]["date"] and cs["basis"] == ["morning"] * 3
    humid = dict(hot_dawn)
    for x in runs[40:]:
        humid[x["date"]] = {"temp_c": 24.5, "hadley": 151.0}
    assert ZE.cool_season(runs, TODAY, humid) is None


def test_morning_weather_from_the_archive_cache(tmp_path):
    import json
    from backend.engine import heat_data as HD
    wdir = tmp_path / "weather"
    wdir.mkdir()

    def day(cell, d, pts):
        doc = {"cell": list(cell), "date": d, "points": {}}
        for k, (elev, temps) in enumerate(pts):
            doc["points"][f"p{k}"] = {"elevation": elev, "hourly": {
                "time": [f"{d}T{h:02d}:00" for h in range(24)], "temperature_2m": temps,
                "relative_humidity_2m": [80.0] * 24}}
        (wdir / f"{cell[0]:.3f}_{cell[1]:.3f}_{d}.json").write_text(json.dumps(doc), "utf-8")
    town = [22.0] * 5 + [21.0, 21.0, 22.0] + [28.0] * 16
    hill = [15.0] * 24
    day((25.125, 121.625), "2026-10-01", [(1000.0, hill), (20.0, town)])
    day((25.125, 121.625), "2026-10-02", [(20.0, town)])
    day((24.125, 120.625), "2026-10-01", [(10.0, [30.0] * 24)])     # a trip elsewhere the same day
    mw = HD.morning_weather(["2026-10-01", "2026-10-02", "2026-10-03"], root=tmp_path)
    assert set(mw) == {"2026-10-01", "2026-10-02"}                  # no file for the 3rd
    assert mw["2026-10-01"]["temp_c"] == pytest.approx(21.3, abs=0.05)   # the town, 05–07 h
    assert mw["2026-10-01"]["min_c"] == 21.0 and mw["2026-10-01"]["cell"] == "25.125_121.625"
    from backend.engine.heat import hadley_sum
    assert mw["2026-10-01"]["hadley"] == pytest.approx((2 * hadley_sum(21.0, 80) + hadley_sum(22.0, 80)) / 3, abs=0.1)
    assert HD.morning_weather([], root=tmp_path) == {}


# ---- suggestions ------------------------------------------------------------------------

DS = SimpleNamespace(workouts=[])


def test_break_suggests_retests_after_the_block():
    brk = {"days": 35, "return": "2026-10-20", "end": "2026-11-24", "aet_stale": True}
    out = ZE.suggestions(DS, Plan(), TODAY, acts=[], brk=brk, points=[])
    s = next(x for x in out["suggestions"] if x["id"] == "break")
    assert s["tests"] == ["cp", "tt30", "aet"] and s["earliest"] == "2026-11-24"
    assert s["kind"] == "test_suggestion" and s["estimate"] is True and "腕" in s["caveat"]
    assert not any("胸帶" in c for c in s["conditions"])             # the athlete has no chest strap
    # a CP test after the return covers cp; an applied LTHR estimate is not a test
    plan = Plan(thresholds=[Threshold("2026-11-10", cp=200, lthr=150, lthr_method="estimate")])
    s = next(x for x in ZE.suggestions(DS, plan, TODAY, acts=[], brk=brk, points=[])["suggestions"]
             if x["id"] == "break")
    assert s["tests"] == ["tt30", "aet"]
    # a 3-week break is not ≥ 4 weeks
    assert not ZE.suggestions(DS, Plan(), TODAY, acts=[], brk={**brk, "days": 21}, points=[])["suggestions"]


def test_hr_shift_suggestion_and_tests_done_since():
    pts = _pts(BASE) + _pts([50, 40, 30, 20, 10, 2], offset=7.0)
    out = ZE.suggestions(DS, Plan(), TODAY, acts=[], brk={}, points=pts)
    s = next(x for x in out["suggestions"] if x["id"] == "hr_shift")
    assert s["tests"] == ["tt30", "aet"] and s["earliest"] is None and "升高" in s["title"]
    assert "已熱校正" in s["title"] and "已依熱指數校正（β 0.22 ± 0.04 bpm/Hadley" in s["text"]
    assert "校正信心高" in s["text"] and "推估" in s["text"] and "±" in s["text"] and "腕" in s["caveat"]
    assert "自組" not in s["text"]
    assert out["checks"]["hr_shift"]["fired"] and out["checks"]["hr_shift"]["heat"]["confidence"] == "high"
    # watch-only recent runs say so, with the lower confidence
    wpts = _pts(BASE) + _pts([50, 40, 30, 20, 10, 2], offset=9.0, src="watch", sigma_h=8.0)
    s = next(x for x in ZE.suggestions(DS, Plan(), TODAY, acts=[], brk={}, points=wpts)["suggestions"]
             if x["id"] == "hr_shift")
    assert "手錶溫度" in s["text"] and "校正信心低" in s["text"]
    done = Plan(thresholds=[Threshold("2026-11-01", lthr=160, lthr_method="friel30", aethr=145, aethr_method="test")])
    assert not ZE.suggestions(DS, done, TODAY, acts=[], brk={}, points=pts)["suggestions"]


def test_cool_season_suggestion_from_road_runs_only():
    s = dt.date(2026, 9, 1)
    days = _days(s, 40, 28.0) + _days(s + dt.timedelta(days=40), 3, 22.0)
    ws, acts = [], []
    for i, a in enumerate(days):
        d = dt.date.fromisoformat(a["date"])
        ws.append(SimpleNamespace(idx=i, sport="run", tags=[], day=0,
                                  entry=SimpleNamespace(file=f"{i}.fit", start=dt.datetime(d.year, d.month, d.day, 6))))
        acts.append({**a, "file": f"{i}.fit"})
    ds = SimpleNamespace(workouts=ws)
    out = ZE.suggestions(ds, Plan(), TODAY, acts=acts, brk={}, points=[])
    s = next(x for x in out["suggestions"] if x["id"] == "cool_season")
    assert s["tests"] == ["tt30", "aet"] and s["detected"] == "2026-10-11"
    assert all("< 25 °C" in c for c in s["conditions"])
    # the same days on trail runs (a mountain is cool in August) do not count
    for w in ws:
        w.tags = ["runningtrail"]
    assert not ZE.suggestions(ds, Plan(), TODAY, acts=acts, brk={}, points=[])["suggestions"]
    # older than 60 days: no longer the first cool spell
    assert not any(x["id"] == "cool_season" for x in
                   ZE.suggestions(SimpleNamespace(workouts=[]), Plan(), dt.date(2027, 1, 30), acts=acts, brk={},
                                  points=[])["suggestions"])


def test_applied_test_is_a_zone_update_event():
    plan = Plan(thresholds=[Threshold("2026-11-15", cp=210, cp_method="tt20")])
    ev = ZE.applied_events(plan, TODAY)
    assert ev and ev[0]["field"] == "cp" and "區間已重算" in ev[0]["text"]
    assert not ZE.applied_events(plan, TODAY + dt.timedelta(days=30))


# ---- the testing indicator ---------------------------------------------------------------

def test_testing_indicator_carries_suggestions_but_never_cp_due(monkeypatch):
    from backend.engine import status as S
    sug = {"suggestions": [ZE._suggestion("cool_season", ["tt30", "aet"], "天氣轉涼了", "x", TODAY)],
           "events": [], "checks": {}}
    monkeypatch.setattr(ZE, "suggestions", lambda *a, **k: sug)
    st = S.Status.__new__(S.Status)
    st.ds = SimpleNamespace(workouts=[], plan=None)
    st.plan = Plan(thresholds=[Threshold(TODAY.isoformat(), cp=204, lthr=155,
                                         note="LTHR 自動估算（7 次跑步）；CP 204 W")])
    st.today, st.goals, st.indicators, st.cp_protocol = TODAY, {"days_to_next_a": None}, [], "quick"
    ind = st.i_testing()
    assert ind.extra["test_suggestions"] == sug["suggestions"] and ind.extra["cp_due"] is False
    assert ind.level == S.WATCH and "不會自動排課" in ind.action
    assert "LTHR 自動估算（已套用" in ind.why and "不是測試" in ind.why
