"""爬坡段 elevation profile and 坡度分組 rows (workout_review climb_profile / grade_profile), synthetic data."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.engine.panels.workout import grade_bins
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

SETTINGS = {"runthr": 165.0, "runftp": 260.0}


def _trail(day, climb_m=200.0, kmh=8.0, up_hr=155.0, watts=230.0, elev=True):
    """60 min: 10' flat, 20' up `climb_m`, 15' down the same, 15' flat (1 Hz)."""
    t = np.arange(0, 3601, 1.0)
    e = np.zeros(len(t))
    up = (t >= 600) & (t < 1800)
    e[up] = (t[up] - 600) / 1200 * climb_m
    dn = (t >= 1800) & (t < 2700)
    e[dn] = climb_m - (t[dn] - 1800) / 900 * climb_m
    speed = np.where(up, kmh * 0.6, kmh)
    dist = np.concatenate([[0.0], np.cumsum(speed[1:] / 3600.0)])
    hr = np.where(up, up_hr, 135.0)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": list(speed), "elapseddistance": list(dist),
          "power": list(np.where(up, watts, 200.0))}
    if elev:
        ch["elevation"] = list(e + 100.0)
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running", "runningtrail"],
                       sport_type="trail running", channels=ch,
                       metrics={"duration": 3600.0, "movingduration": 3600.0, "distance": float(dist[-1]),
                                "climbing": climb_m})


def test_grade_bins_vam_is_the_net_vertical_rate():
    n = 600
    rows = grade_bins([1.0] * n, [0.1] * n, list(np.arange(n) * 0.001), elev_m=list(np.arange(n) * 0.1))
    assert len(rows) == 1 and rows[0]["vam"] == pytest.approx(360.0, rel=0.01)    # 0.1 m/s
    assert grade_bins([1.0] * n, [0.1] * n)[0]["vam"] is None


def test_profile_series_follows_distance_and_rates():
    w = _trail(dt.date(2026, 9, 30))
    ch = w.channels
    p = R.profile_series(ch["elapsedtime"], ch["elapseddistance"], ch["elevation"], hr=ch["heartrate"],
                         power=ch["power"], n=200)
    assert p is not None and len(p["x"]) == len(p["alt"]) == len(p["vam"])
    assert p["x"] == sorted(p["x"])
    i = int(np.argmin(np.abs(np.array(p["x"]) - 2.0)))      # 2 km: on the climb (1.33 km flat, then 1.6 km up at 4.8 km/h)
    assert p["vam"][i] == pytest.approx(600.0, rel=0.05)      # 200 m in 20'
    assert p["hr"][i] == pytest.approx(155, abs=1) and p["power"][i] == pytest.approx(230, abs=1)
    assert p["gap"][i] < p["pace"][i]                          # uphill: GAP is faster than the pace
    assert R.profile_series(ch["elapsedtime"], ch["elapseddistance"], None) is None


def test_descents_are_the_mirrored_climbs():
    w = _trail(dt.date(2026, 9, 30))
    ch = w.channels
    d = R.descents_of(ch["elapsedtime"], ch["elapseddistance"], ch["elevation"])
    assert len(d) == 1
    assert d[0]["drop_m"] == pytest.approx(200, abs=2) and d[0]["grade"] < 0 and d[0]["rate"] < 0
    assert d[0]["start_km"] < d[0]["end_km"]


def test_climb_card_has_profile_numbers_and_baseline():
    today = dt.date(2026, 9, 30)
    past = [_trail(today - dt.timedelta(days=3 * k), climb_m=180.0) for k in range(1, 7)]   # VAM 540
    ds = FakeDataset(past + [_trail(today)], today, settings=SETTINGS)
    w = ds.workouts[-1]
    r = R.review(ds, w, "climbs")
    cp = r["climb_profile"]
    assert cp["profile"] and cp["note"] is None
    (c,) = cp["climbs"]
    assert c["no"] == "①" and c["start_km"] < c["end_km"]
    assert c["gain_m"] == pytest.approx(200, abs=2) and c["avg_power"] == pytest.approx(230, abs=1)
    assert c["pct_cp"] == pytest.approx(230 / 260, rel=0.01)
    b = c["base"]["vam"]
    assert b["ok"] and b["n"] == 6 and b["weeks"] == 8 and b["median"] == pytest.approx(540, rel=0.02)
    assert c["vs"]["vam"] == pytest.approx(600 / 540 - 1, abs=0.02)
    assert len(cp["descents"]) == 1
    cols = {s["name"]: s["data"]["values"] for s in r["series"] if s["data"]["kind"] == "values"}
    assert cols["段"] == ["①"] and cols["VAM 平常"] == ["540"]
    text = " ".join(s["data"]["value"] for s in r["series"] if s["data"]["kind"] == "value")
    assert "VAM 高於平常：①" in text


def test_climb_baseline_skips_climbs_of_another_grade():
    today = dt.date(2026, 9, 30)
    # 600 m over 1.6 km ≈ 37 % grade vs today's 12.5 %: never "similar"
    past = [_trail(today - dt.timedelta(days=3 * k), climb_m=600.0) for k in range(1, 7)]
    ds = FakeDataset(past + [_trail(today)], today, settings=SETTINGS)
    (c,) = R.review(ds, ds.workouts[-1], "climbs")["climb_profile"]["climbs"]
    assert not c["base"]["vam"]["ok"] and c["vs"]["vam"] is None


def test_climb_card_without_altitude_says_so():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_trail(today, elev=False)], today, settings=SETTINGS)
    r = R.review(ds, ds.workouts[0], "climbs")
    assert r["empty"] and "海拔" in r["empty"]
    g = R.review(ds, ds.workouts[0], "grades")
    assert g["empty"]


def test_grade_card_rows_with_baseline():
    today = dt.date(2026, 9, 30)
    past = [_trail(today - dt.timedelta(days=3 * k)) for k in range(1, 6)]
    ds = FakeDataset(past + [_trail(today)], today, settings=SETTINGS)
    r = R.review(ds, ds.workouts[-1], "grades")
    gp = r["grade_profile"]
    labels = [b["label"] for b in gp["bins"]]
    assert labels and len(labels) == len({*labels})
    flat = next(b for b in gp["bins"] if b["lo"] == -2)
    assert flat["metrics"]["pace_s_per_km"] == pytest.approx(450, rel=0.05)        # 8 km/h
    assert flat["base"]["pace_s_per_km"]["ok"] and flat["vs"]["pace_s_per_km"] == pytest.approx(0, abs=0.01)
    up = [b for b in gp["bins"] if (b["lo"] or 0) >= 10]
    assert up and all(b["metrics"]["vam"] > 0 for b in up if b["time_s"] > 120)
    assert gp["min_s"] == R.BIN_MIN_S


# ---- SP-218: the 爬坡與地形 card's map / table numbers ------------------------------------

def _with_cadence(w, up_spm=(110.0, 170.0), flat_spm=170.0):
    """Cadence (strides/min, the channel's unit) on `w`: the first half of the climb walked
    at up_spm[0], the second half run at up_spm[1]; flat at flat_spm."""
    t = np.asarray(w.channels["elapsedtime"])
    cad = np.where((t >= 600) & (t < 1200), up_spm[0] / 2, np.where((t >= 1200) & (t < 1800), up_spm[1] / 2, flat_spm / 2))
    w.channels["cadence"] = list(cad)
    return w


def test_climb_run_share_from_cadence_and_the_summary():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_with_cadence(_trail(today))], today, settings=SETTINGS)
    r = R.review(ds, ds.workouts[0], "climbs")
    cp = r["climb_profile"]
    (c,) = cp["climbs"]
    assert c["run_share"] == pytest.approx(0.5, abs=0.06)          # walked half the climb, ran the other half
    sm = cp["summary"]
    assert sm["n"] == 1 and sm["ascent_m"] == pytest.approx(200)    # the activity's own climbing
    assert sm["gain_m"] == pytest.approx(c["gain_m"]) and sm["time_s"] == pytest.approx(c["duration_s"])
    assert sm["share"] == pytest.approx(c["duration_s"] / 3600, rel=0.02)
    assert sm["vam"] == pytest.approx(600, rel=0.05)                # 200 m in 20'
    assert sm["hr_per_100m"] == pytest.approx(c["hr_per_100m"])     # one climb: the median is that climb
    assert sm["run_share"] == pytest.approx(c["run_share"])
    assert cp["workout"] == 0                                      # the viewer's samples / map / hover


def test_climb_run_share_needs_cadence():
    today = dt.date(2026, 9, 30)
    ds = FakeDataset([_trail(today)], today, settings=SETTINGS)
    cp = R.review(ds, ds.workouts[0], "climbs")["climb_profile"]
    assert cp["climbs"][0]["run_share"] is None and cp["summary"]["run_share"] is None
    s = {"t": list(range(10)), "dist": [0.1 * i for i in range(10)], "cadence": None}
    assert R.climb_run_share(s, 0.0, 1.0) is None
    # cadence on less than half the climb's moving seconds: not judged
    s = {"t": list(range(100)), "dist": [0.01 * i for i in range(100)], "speed": [8.0] * 100,
         "cadence": [80.0] * 30 + [None] * 70}
    assert R.climb_run_share(s, 0.0, 1.0) is None
    s["cadence"] = [80.0] * 60 + [30.0] * 40
    assert R.climb_run_share(s, 0.0, 1.0) == pytest.approx(0.6, abs=0.02)


def test_climb_summary_without_climbs():
    w = _trail(dt.date(2026, 9, 30))
    ds = FakeDataset([w], dt.date(2026, 9, 30), settings=SETTINGS)
    sm = R.climb_summary(ds.workouts[0], {"moving_s": 3600.0, "hr_per_100m": None}, [])
    assert sm["n"] == 0 and sm["gain_m"] is None and sm["share"] is None and sm["vam"] is None
    assert sm["ascent_m"] == pytest.approx(200)


def test_profile_uses_the_smoothed_elevation():
    """The drawn profile is on WKO5's smoothed elevation (the evaluator's _elevation), not the raw channel."""
    today = dt.date(2026, 9, 30)
    w = _trail(today)
    rng = np.random.default_rng(1)
    w.channels["elevation"] = list(np.asarray(w.channels["elevation"]) + rng.normal(0, 3.0, len(w.channels["elevation"])))
    ds = FakeDataset([w], today, settings=SETTINGS)
    alt = np.asarray([v for v in R.review(ds, ds.workouts[0], "climbs")["climb_profile"]["profile"]["alt"] if v is not None])
    ch = w.channels
    raw = R.profile_series(ch["elapsedtime"], ch["elapseddistance"], ch["elevation"])["alt"]
    raw = np.asarray([v for v in raw if v is not None])
    # the noise (σ 3 m sample to sample) is smoothed away: the profile's steps are far smaller than on the raw channel
    assert np.median(np.abs(np.diff(alt))) < np.median(np.abs(np.diff(raw))) / 3
