"""Real-data half of backend/tests/test_equivalence.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine import equivalence as E


@pytest.mark.golden
def test_backtest_on_the_athletes_own_trail_and_hike_activities():
    from backend.settings.paths import athlete_dir
    d = athlete_dir()
    if not any(d.glob("*.wko5athlete")):
        pytest.skip("no WKO5 athlete folder")
    from backend.api.overview import _dataset, _status
    from backend.engine import overview as O
    ds = _dataset()
    today = O.day_to_date(ds.today)
    aet = O.week_plan(ds, _status(ds, today), today)["thresholds"]["aet"]
    s = E.summary(ds, today, aet)
    t = s["backtest"]["trail"]
    if t["n"] < E.MIN_SAMPLES:
        pytest.skip(f"only {t['n']} easy trail runs in the window")
    assert t["mape_pct"] is not None and len(t["rows"]) == t["n"]
    assert t["estimate"] == (t["mape_pct"] > E.ESTIMATE_MAPE)          # the 推估 label follows the error
