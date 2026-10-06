"""跑姿分組 (workout_review form_bins / form_grades / form_work), synthetic data only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

SETTINGS = {"runthr": 165.0, "runftp": 260.0}


def test_form_bins_by_grade_counts_every_moving_step():
    # SP-235: walked / small steps (< 130 spm) count too; their share is shown per bin
    n = 1200
    t = np.arange(n, dtype=float)
    speed = np.full(n, 9.0)
    grade = np.where(t < 600, 0.0, 0.12)                       # 10' flat, 10' at 12 %
    cad = np.where((t >= 600) & (t < 900), 55.0, 85.0)         # 5' of the climb walked (110 spm)
    gct = np.where(t < 600, 250.0, np.where(t < 900, 400.0, 300.0))
    out = R.form_bins(t, speed, {"gct": gct, "ilr": None}, cad, None, grade)
    rows = {r["label"]: r for r in out["grade"]}
    flat = next(r for r in out["grade"] if r["lo"] == -2)
    up = next(r for r in out["grade"] if r["lo"] == 10)
    assert flat["time_s"] == pytest.approx(600, abs=2) and flat["m"]["gct"] == pytest.approx(250)
    assert flat["slow_share"] == pytest.approx(0.0)
    assert up["time_s"] == pytest.approx(600, abs=2)          # the walked 5' counted
    assert up["m"]["gct"] == pytest.approx(350, abs=1) and up["m"]["ilr"] is None
    assert up["slow_share"] == pytest.approx(0.5, abs=0.01)
    assert len(rows) == 2
    assert out["split"] == "time"
    assert sum(r["time_s"] for r in out["work"]["all"]) == pytest.approx(n - 1, abs=2)


def test_form_bins_without_cadence_has_no_slow_share():
    n = 600
    t = np.arange(n, dtype=float)
    out = R.form_bins(t, np.full(n, 9.0), {"gct": np.full(n, 250.0)}, None, None, np.zeros(n))
    assert out["grade"] and all(r["slow_share"] is None for r in out["grade"])


def _downhill_110spm(n=900):
    """15' of a −12 % downhill at 110 spm (55 strides/min), 5 km/h, after 5' flat at 170 spm."""
    t = np.arange(n + 300, dtype=float)
    grade = np.where(t < 300, 0.0, -0.12)
    cad = np.where(t < 300, 85.0, 55.0)
    return t, np.full(len(t), 5.0), grade, cad


def test_slow_steep_downhill_lands_in_its_grade_bin():
    """The SP-235 acceptance case: a −12 % downhill at 110 spm is in the −15～−8 % bin (and in the
    card's default −20 ~ −10 % bin); before SP-235 the 130-spm filter dropped every one of those steps."""
    t, v, grade, cad = _downhill_110spm()
    gct = np.where(t < 300, 240.0, 330.0)
    out = R.form_bins(t, v, {"gct": gct, "cadence": cad * 2.0}, cad, None, grade, edges=(-15, -8, -3, 3))
    row = next(r for r in out["grade"] if r["lo"] == -15 and r["hi"] == -8)
    assert row["label"] == "-15 ~ -8%"
    assert row["time_s"] == pytest.approx(900, abs=2)
    assert row["m"]["cadence"] == pytest.approx(110) and row["m"]["gct"] == pytest.approx(330)
    assert row["slow_share"] == pytest.approx(1.0)
    dflt = R.form_bins(t, v, {"gct": gct}, cad, None, grade)
    assert next(r for r in dflt["grade"] if r["lo"] == -20)["time_s"] == pytest.approx(900, abs=2)
    # the old mask (form_drift's, still used there): none of those steps
    mov, _cum, _split = R._run_work(t, v, cad, None)
    assert not mov[(t >= 301)].any()


def test_form_drift_keeps_the_130_spm_rule():
    """Same data as the form_bins tests: form_drift still compares running steps only — the walked
    steps change form_bins but not form_drift."""
    n = 2400
    t = np.arange(n, dtype=float)
    v = np.full(n, 9.0)
    run = (t // 120) % 4 != 3                                   # every 4th 2' block walked (110 spm)
    cad = np.where(run, 85.0, 55.0)
    gct = np.where(run, 250.0 + t / 100.0, 500.0)               # walking GCT would swamp the drift
    out = R.form_drift(t, v, {"gct": gct}, cad, None)
    m = run & np.r_[False, np.ones(n - 1, bool)]                # moving mask drops the first sample (dt 0)
    half = np.cumsum(np.where(m, 1.0, 0.0))
    a, b = m & (half <= half[-1] / 2), m & (half > half[-1] / 2)
    assert out["_split"] == "time"
    assert out["gct"]["first"] == pytest.approx(gct[a].mean(), rel=1e-6)
    assert out["gct"]["last"] == pytest.approx(gct[b].mean(), rel=1e-6)
    assert max(out["gct"]["first"], out["gct"]["last"]) < 300          # no walking step inside
    # form_bins on the same data does count the walked steps
    fb = R.form_bins(t, v, {"gct": gct}, cad, None, np.zeros(n))
    assert fb["grade"][0]["m"]["gct"] > 300


def test_form_bins_work_deciles_and_bands():
    # 100 W for the first 2000 s, 200 W after: equal work per 1000 s at 200 W = 2 deciles of 10 %
    n = 3000
    t = np.arange(n, dtype=float)
    p = np.where(t < 2000, 100.0, 200.0)
    grade = np.where((t // 100) % 2 == 0, 0.0, 0.06)           # alternate flat / 6 % every 100 s
    x = t.copy()
    out = R.form_bins(t, np.full(n, 9.0), {"x": x}, np.full(n, 85.0), p, grade)
    assert out["split"] == "work"
    al = out["work"]["all"]
    assert [r["k"] for r in al] == list(range(10))
    # total work = 2000·100 + 1000·200 = 400 kJ; the first 40 kJ = the first 400 s
    assert al[0]["time_s"] == pytest.approx(400, abs=2)
    assert al[9]["time_s"] == pytest.approx(200, abs=2)
    assert sum(r["time_s"] for r in al) == pytest.approx(n - 1, abs=2)
    fl, up = out["work"]["flat"], out["work"]["up"]
    assert fl[0]["time_s"] + up[0]["time_s"] == pytest.approx(al[0]["time_s"], abs=2)
    assert "down" not in out["work"]                           # no samples < −3 %
    assert al[0]["m"]["x"] < al[9]["m"]["x"]


def test_form_bins_without_grade_has_only_all():
    n = 1000
    t = np.arange(n, dtype=float)
    out = R.form_bins(t, np.full(n, 9.0), {"gct": np.full(n, 250.0)}, np.full(n, 85.0), None, None)
    assert out["grade"] == [] and list(out["work"]) == ["all"]


def test_impact_per_km_is_impact_times_steps_per_km():
    # 180 spm at 10 km/h = 6 min/km → 1080 steps/km; 1.5 G × 1080 = 1620
    v = R.impact_per_km(np.array([1.5, 1.5]), np.array([180.0, 180.0]), np.array([10.0, 0.5]))
    assert v[0] == pytest.approx(1620.0) and np.isnan(v[1])          # stopped: no value
    assert R.impact_per_km(None, np.array([180.0]), np.array([10.0])) is None


def _b(med):
    return {"ok": True, "median": med, "q1": med * 0.97, "q3": med * 1.03, "n": 5}


def test_cadence_hint_needs_high_impact_and_low_cadence():
    row = lambda ilr, cad, secs=300: {"time_s": secs, "m": {"ilr": ilr, "cadence": cad},
                                      "base": {"ilr": _b(50.0), "cadence": _b(160.0)}}
    h = R.cadence_hint([row(60, 150), row(60, 165), row(45, 150), row(60, 150, secs=30)], ["A", "B", "C", "D"])
    assert h is not None and h.startswith("A：") and "Heiderscheit 2011" in h
    assert "B" not in h and "C" not in h and "D" not in h                # cadence high / impact low / < 60 s
    assert R.cadence_hint([row(60, 165)], ["B"]) is None
    no_base = {"time_s": 300, "m": {"ilr": 60, "cadence": 150}, "base": {"ilr": {"ok": False}, "cadence": _b(160.0)}}
    assert R.cadence_hint([no_base], ["E"]) is None


def _run(day, gct=250.0, ilr=True, ilr_v=70.0, cad=85.0):
    """40 min: 20' flat, 10' up 8 %, 10' down 8 %; cadence 170 spm, power, Stryd ILR when `ilr`."""
    t = np.arange(0, 2401, 1.0)
    kmh = 10.0
    dist = t * kmh / 3600.0
    up, dn = (t >= 1200) & (t < 1800), t >= 1800
    e = np.zeros(len(t))
    e[up] = (dist[up] - dist[1200]) * 1000 * 0.08
    e[dn] = e[up].max() - (dist[dn] - dist[1800]) * 1000 * 0.08
    ch = {"elapsedtime": list(t), "heartrate": [140.0] * len(t), "speed": [kmh] * len(t),
          "elapseddistance": list(dist), "elevation": list(e + 100.0), "power": [220.0] * len(t),
          "cadence": [cad] * len(t), "stancetime": [gct / 1000.0] * len(t),
          "verticaloscillation": [0.08] * len(t)}
    if ilr:
        ch["@impact_loading_rate"] = list(np.where(dn, ilr_v + 20.0, ilr_v))
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       channels=ch, metrics={"duration": 2400.0, "movingduration": 2400.0,
                                             "distance": float(dist[-1]), "climbing": float(e.max())})


def test_form_cards_with_baseline_and_stryd():
    today = dt.date(2026, 9, 30)
    past = [_run(today - dt.timedelta(days=3 * k), gct=240.0) for k in range(1, 6)]
    ds = FakeDataset(past + [_run(today)], today, settings=SETTINGS)
    w = ds.workouts[-1]
    g = R.review(ds, w, "form_grades")
    fp = g["form_profile"]
    assert fp["mode"] == "grade" and "ilr" in fp["keys"] and "gct" in fp["keys"]
    flat = next(b for b in fp["bins"] if b["lo"] == -2)
    assert flat["m"]["gct"] == pytest.approx(250)
    b = flat["base"]["gct"]
    assert b["ok"] and b["n"] == 5 and b["weeks"] == 8 and b["median"] == pytest.approx(240)
    down = [b for b in fp["bins"] if (b["hi"] or 0) <= -5 and b["time_s"] > 120]
    assert down and all(b["m"]["ilr"] == pytest.approx(90) for b in down)
    cols = {s["name"]: s["data"]["values"] for s in g["series"] if s["data"]["kind"] == "values"}
    assert "觸地時間（平常）" in cols and any("平常 240" in v for v in cols["觸地時間（平常）"])
    wk = R.review(ds, w, "form_work")["form_profile"]
    assert wk["mode"] == "work" and wk["split"] == "work"
    assert set(wk["bands"]) >= {"all", "flat", "up", "down"}
    assert len(wk["bands"]["all"]) == 10 and wk["bands"]["all"][0]["base"]["gct"]["ok"]
    assert wk["band_labels"]["flat"] == "平路 −3～+3%"


def _texts(r):
    return " ".join(s["data"]["value"] for s in r["series"] if s["data"]["kind"] == "value")


def test_cadence_hint_on_the_cards_only_when_the_data_supports_it():
    today = dt.date(2026, 9, 30)
    past = [_run(today - dt.timedelta(days=3 * k)) for k in range(1, 6)]           # ILR 70, 170 spm
    # higher impact with a lower cadence (160 spm): the hint shows
    ds = FakeDataset(past + [_run(today, ilr_v=90.0, cad=80.0)], today, settings=SETTINGS)
    for sec in ("form_grades", "form_work"):
        t = _texts(R.review(ds, ds.workouts[-1], sec))
        assert "步頻提高 5–10%" in t and "Heiderscheit 2011" in t
    # higher impact but a higher cadence: no hint
    ds = FakeDataset(past + [_run(today, ilr_v=90.0, cad=90.0)], today, settings=SETTINGS)
    for sec in ("form_grades", "form_work"):
        assert "步頻提高" not in _texts(R.review(ds, ds.workouts[-1], sec))


def _trail(day, title="", gct=250.0, hour=7):
    """30 min trail run: 20' flat at 10 km/h, 170 spm; then 10' down −12 % at 5 km/h, 110 spm,
    where Stryd writes ILR 0 (as it does below ~115 spm) but the watch's stance time is there."""
    t = np.arange(0, 1801, 1.0)
    dn = t >= 1200
    kmh = np.where(dn, 5.0, 10.0)
    dist = np.concatenate([[0.0], np.cumsum(kmh[1:] / 3600.0)])
    e = np.where(dn, -(dist - dist[1200]) * 1000 * 0.12, 0.0) + 500.0
    ch = {"elapsedtime": list(t), "heartrate": [140.0] * len(t), "speed": list(kmh),
          "elapseddistance": list(dist), "elevation": list(e), "power": [220.0] * len(t),
          "cadence": list(np.where(dn, 55.0, 85.0)), "stancetime": list(np.where(dn, 0.33, gct / 1000.0)),
          "verticaloscillation": [0.08] * len(t), "@impact_loading_rate": list(np.where(dn, 0.0, 70.0))}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(hour)), sport="run", tags=["runningtrail"],
                       sport_type="Trail Running", title=title, channels=ch,
                       metrics={"duration": 1800.0, "movingduration": 1800.0, "distance": float(dist[-1]),
                                "climbing": 0.0})


def _cols(r):
    return {s["name"]: s["data"]["values"] for s in r["series"] if s["data"]["kind"] == "values"}


def test_grade_card_shows_slow_share_and_explains_missing_impact():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_trail(today)], today, settings=SETTINGS)
    g = R.review(ds, ds.workouts[-1], "form_grades")
    fp = g["form_profile"]
    steep = next(b for b in fp["bins"] if b["lo"] == -20)        # −12 %: the −20 ~ −10 % bin
    flat = next(b for b in fp["bins"] if b["lo"] == -2)
    assert steep["time_s"] == pytest.approx(600, abs=15)          # the 110-spm steps are in
    assert steep["slow_share"] == pytest.approx(1.0, abs=0.03) and flat["slow_share"] == pytest.approx(0.0, abs=0.03)
    assert steep["m"]["ilr"] is None and steep["m"]["cadence"] == pytest.approx(110, abs=1)
    assert steep["m"]["gct"] == pytest.approx(330, abs=1)
    cols = _cols(g)
    i = [b["label"] for b in fp["bins"]].index(steep["label"])
    assert cols["步頻 < 130 的比例"][i] == "100%"
    assert cols["ILR（平常）"][i] == "–" and cols["觸地時間（平常）"][i] == "330"
    assert "移動時間" in cols and "跑步時間" not in cols
    texts = _texts(g)
    assert "這段沒有衝擊資料（慢速或走路時裝置可能不輸出）" in texts and steep["label"] in texts
    # every bin has ILR → no such note
    ds = FakeDataset([_run(today)], today, settings=SETTINGS)
    assert "沒有衝擊資料" not in _texts(R.review(ds, ds.workouts[-1], "form_grades"))


def test_usual_keeps_trail_runs_and_hikes_apart():
    """The 「平常」 of a trail run comes from trail runs only, a hike's (activity type 爬山) from hikes."""
    today = dt.date(2026, 9, 30)
    runs = [_trail(today - dt.timedelta(days=3 * k), gct=240.0) for k in range(1, 6)]
    hikes = [_trail(today - dt.timedelta(days=3 * k), title="郊山爬山", gct=300.0, hour=15) for k in range(1, 6)]
    ds = FakeDataset(runs + hikes + [_trail(today, hour=7), _trail(today, title="郊山爬山", hour=15)],
                     today, settings=SETTINGS)
    run_w = next(w for w in ds.workouts if w.day >= R.math.floor(ds.today) and not w.entry.title)
    hike_w = next(w for w in ds.workouts if w.day >= R.math.floor(ds.today) and w.entry.title)
    assert not R.hike_like(run_w, []) and R.hike_like(hike_w, [])
    for w, want in ((run_w, 240.0), (hike_w, 300.0)):
        for sec in ("form_grades", "form_work"):
            fp = R.review(ds, w, sec)["form_profile"]
            rows = fp["bins"] if sec == "form_grades" else fp["bands"]["all"]
            r = next(r for r in rows if r["base"]["gct"]["ok"])
            assert r["base"]["gct"]["n"] == 5
            if sec == "form_grades":
                flat = next(b for b in rows if b["lo"] == -2)
                assert flat["base"]["gct"]["median"] == pytest.approx(want)


def test_hike_like_follows_the_users_activity_type():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_trail(today), _trail(today, title="郊山爬山", hour=15),
                      _trail(today, title="五寮尖越野賽 爬山", hour=18)], today, settings=SETTINGS)
    w_run, w_hike, w_race = ds.workouts
    assert [R.hike_like(w, []) for w in ds.workouts] == [False, True, False]      # a race word wins
    key = lambda w: {"start_local": w.entry.start.strftime("%Y-%m-%dT%H:%M"), "file": None,
                     "activity_type_overridden": True}
    rows = [{**key(w_run), "activity_type": "hike"}, {**key(w_hike), "activity_type": "training"}]
    assert R.hike_like(w_run, rows) and not R.hike_like(w_hike, rows)


def _steady_run(seed, n_win=40, slope=-0.3):
    """n_win steady 30-s windows; cadence and speed vary window to window independently;
    ILR = 50 + 6·(km/h − 8) + slope·(spm − 160) + noise."""
    rng = np.random.default_rng(seed)
    cad = np.repeat(160 + rng.normal(0, 4, n_win), 30)
    spd = np.repeat(8 + rng.normal(0, 0.4, n_win), 30)
    ilr = (50 + 6 * (spd - 8) + slope * (cad - 160) + np.repeat(rng.normal(0, 1.5, n_win), 30)
           + rng.normal(0, 0.5, len(cad)))
    t = np.arange(len(cad), dtype=float)
    return t, spd, cad / 2.0, ilr, np.zeros(len(cad))


def test_cadence_windows_and_within_run_fit_recover_the_slope():
    runs = []
    for k in range(6):
        t, spd, stride, ilr, g = _steady_run(k)
        cw = R.cadence_windows(t, spd, stride, ilr, g)
        assert len(cw) >= 35 and cw[0][1] == pytest.approx(stride[0] * 2, abs=0.2)
        runs.append(cw)
    fit = R.cadence_fit(runs)
    assert fit["n_runs"] == 6 and fit["b_cad"] == pytest.approx(-0.3, abs=0.05)
    assert fit["b_speed"] == pytest.approx(6.0, abs=0.3)
    assert fit["b_cad"] + 1.96 * fit["se_cad"] < 0 and fit["partial_r"] < -0.5
    assert R.cadence_fit(runs[:4]) is None                       # < 5 runs: no personal trend
    # walking (< 130 spm) and unsteady windows are left out
    t, spd, stride, ilr, g = _steady_run(9)
    assert R.cadence_windows(t, spd, np.full(len(t), 60.0), ilr, g) == []
    wobbly = spd * np.where(np.arange(len(t)) % 2 == 0, 0.8, 1.2)
    assert R.cadence_windows(t, wobbly, stride, ilr, g) == []


def _fake_steady(day, seed, slope=-0.3):
    t, spd, stride, ilr, _ = _steady_run(seed, slope=slope)
    ch = {"elapsedtime": list(t), "heartrate": [140.0] * len(t), "speed": list(spd), "cadence": list(stride),
          "elapseddistance": list(np.cumsum(spd) / 3600.0), "@impact_loading_rate": list(ilr),
          "stancetime": [0.25] * len(t)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"], channels=ch,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": float(np.sum(spd) / 3600.0)})


def test_cadence_card_with_trend_and_suggested_range():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_fake_steady(today - dt.timedelta(days=2 * k), k) for k in range(1, 7)]
                     + [_fake_steady(today, 99)], today, settings=SETTINGS)
    r = R.review(ds, ds.workouts[-1], "form_cadence")
    cp = r["cadence_profile"]
    assert cp["fit"]["b_cad"] == pytest.approx(-0.3, abs=0.08) and cp["fit"]["n_runs"] == 6
    assert cp["suggest"][0] == pytest.approx(cp["median_cad"] * 1.05)
    assert len(cp["points"]) >= 35
    t = _texts(r)
    assert "每 +5 spm" in t and "Heiderscheit 2011" in t and "推估" in t
    # no relation in the past runs: said so, no chart data
    ds = FakeDataset([_fake_steady(today - dt.timedelta(days=2 * k), k, slope=0.0) for k in range(1, 7)]
                     + [_fake_steady(today, 99, slope=0.0)], today, settings=SETTINGS)
    r = R.review(ds, ds.workouts[-1], "form_cadence")
    assert r["cadence_profile"]["weak"] and r["cadence_profile"]["points"] == [] and "關係不明顯" in _texts(r)


def test_cadence_fit_says_nothing_when_there_is_no_relation():
    runs = [R.cadence_windows(*_steady_run(k, slope=0.0)) for k in range(6)]
    fit = R.cadence_fit(runs)
    assert abs(fit["b_cad"]) < 0.05 and fit["b_cad"] - 1.96 * fit["se_cad"] < 0 < fit["b_cad"] + 1.96 * fit["se_cad"]


def test_form_cards_without_stryd_drop_ilr_lss_and_say_why():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_run(today, ilr=False)], today, settings=SETTINGS)
    for sec in ("form_grades", "form_work"):
        r = R.review(ds, ds.workouts[0], sec)
        fp = r["form_profile"]
        assert "ilr" not in fp["keys"] and "lss" not in fp["keys"] and "gct" in fp["keys"]
        assert not fp["stryd"]
        text = " ".join(s["data"]["value"] for s in r["series"] if s["data"]["kind"] == "value")
        assert "沒有 Stryd" in text
        # a single run: no usual
        rows = fp["bins"] if sec == "form_grades" else fp["bands"]["all"]
        assert not any(x["base"]["gct"]["ok"] for x in rows)
