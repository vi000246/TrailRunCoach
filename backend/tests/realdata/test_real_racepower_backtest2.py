"""Real-data half of backend/tests/test_racepower_backtest2.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
from __future__ import annotations
import numpy as np
import pytest
from pytest import approx
from backend.engine.racepower import backtest as BT


@pytest.mark.golden
def test_classification_recomputed_independently_on_three_real_activities():
    """For one easy, one steady and one race-like run: recompute the moving
    mask, the HR zone shares and the average HR straight from the raw
    channels with plain numpy, re-apply the class rule by hand, and compare
    with athlete.classify_runs."""
    import datetime as dt
    from backend.settings.paths import athlete_dir
    if not any(athlete_dir().glob("*.wko5athlete")):
        pytest.skip("no WKO5 athlete folder")
    from backend.api.wko5views import _dataset
    from backend.engine.racepower import athlete as A
    ds = _dataset()
    runs = [w for w in ds.workouts if BT.outdoor_run(w) and (w.metrics.get("movingduration") or 0) >= 1800][-120:]
    cls = A.classify_runs(ds, runs)
    picked = {}
    for w in runs:
        c = cls[w.idx]
        if c["cls"] and c["cls"] not in picked and c.get("shares") and not c.get("conflict"):
            picked[c["cls"]] = w
    if len(picked) < 3:
        pytest.skip(f"only classes {sorted(picked)} in the data")
    for name, w in picked.items():
        c = cls[w.idx]
        t = np.asarray(ds.channel(w.idx, "elapsedtime"), float)
        hr = np.asarray(ds.channel(w.idx, "heartrate"), float)[:len(t)]
        v = np.asarray(ds.channel(w.idx, "speed"), float)[:len(t)]
        dt_ = np.diff(t, prepend=t[0])
        dt_[(dt_ < 0) | (dt_ > 30) | ~np.isfinite(dt_)] = 0
        m = (dt_ > 0) & (np.nan_to_num(v) > 1.0) & np.isfinite(hr) & (hr > 40)
        tot = dt_[m].sum()
        hr_r = np.round(hr[m])
        low = dt_[m][hr_r < c["aet"]].sum() / tot
        high = dt_[m][hr_r >= 0.95 * c["lthr"]].sum() / tot
        avg = (hr[m] * dt_[m]).sum() / tot
        assert c["shares"]["low"] == approx(low, abs=0.01), name
        assert c["shares"]["high"] == approx(high, abs=0.01), name
        assert c["hr_avg"] == approx(avg, rel=0.01), name
        th = A.thresholds_as_of(ds, w.entry.start.date())
        assert (c["lthr"], c["aet"]) == (th["lthr"], th["aet"])       # own-date thresholds
        assert th["day"] <= w.entry.start.date().isoformat()
        if high >= 0.5:
            want = "race" if (c.get("f_power") is None or c["f_power"] >= 0.9) else "steady"
        elif low >= 0.5 and avg < c["aet"] + 3:
            want = "easy" if not (abs(avg - c["aet"]) <= 3 and (c["drift"] or 0) > 0.05) else "steady"
        else:
            want = "race" if (c.get("if") or 0) >= 0.95 and not c.get("cp_is_floor") else "steady"
        assert c["cls"] == want == name
    assert dt.date.today() >= dt.date(2026, 9, 30)


@pytest.mark.golden
def test_group_hikes_are_out_of_the_equivalence_hike_fit():
    import datetime as dt
    from backend.settings.paths import athlete_dir
    if not any(athlete_dir().glob("*.wko5athlete")):
        pytest.skip("no WKO5 athlete folder")
    from backend.api.wko5views import _dataset
    from backend.engine import equivalence as E
    ds = _dataset()
    ss = E.samples_from(ds, dt.date.today(), 140.0, solo=set())
    assert not [s for s in ss if s.mode == "hike"]
    m = E.fit(ss, 140.0)
    assert m.modes["hike"].method == "ep"                     # too few solo hikes → EP, 推估
