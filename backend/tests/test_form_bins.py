"""跑姿分組 (workout_review form_bins / form_grades / form_work), synthetic data only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import workout_review as R
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

SETTINGS = {"runthr": 165.0, "runftp": 260.0}


def test_form_bins_by_grade_counts_running_steps_only():
    n = 1200
    t = np.arange(n, dtype=float)
    speed = np.full(n, 9.0)
    grade = np.where(t < 600, 0.0, 0.12)                       # 10' flat, 10' at 12 %
    cad = np.where((t >= 600) & (t < 900), 55.0, 85.0)         # 5' of the climb walked (110 spm)
    gct = np.where(t < 600, 250.0, 300.0)
    out = R.form_bins(t, speed, {"gct": gct, "ilr": None}, cad, None, grade)
    rows = {r["label"]: r for r in out["grade"]}
    flat = next(r for r in out["grade"] if r["lo"] == -2)
    up = next(r for r in out["grade"] if r["lo"] == 10)
    assert flat["time_s"] == pytest.approx(600, abs=2) and flat["m"]["gct"] == pytest.approx(250)
    assert up["time_s"] == pytest.approx(300, abs=2)          # the walked 5' left out
    assert up["m"]["gct"] == pytest.approx(300) and up["m"]["ilr"] is None
    assert len(rows) == 2
    assert out["split"] == "time"


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


def _run(day, gct=250.0, ilr=True):
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
          "cadence": [85.0] * len(t), "stancetime": [gct / 1000.0] * len(t),
          "verticaloscillation": [0.08] * len(t)}
    if ilr:
        ch["@impact_loading_rate"] = list(np.where(dn, 90.0, 70.0))
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
