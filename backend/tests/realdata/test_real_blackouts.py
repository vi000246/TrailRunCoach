"""Real-data half of backend/tests/test_blackouts.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import datetime as dt
from datetime import date
import pytest
from backend.engine import blackouts as BL
from backend.tests.test_blackouts import bo


@pytest.mark.golden
def test_week_plan_with_blackouts_on_the_athletes_data():
    """week_plan() itself: nothing on a blocked day, hours x the kept share, the note."""
    from backend.settings.paths import athlete_dir
    if not any(athlete_dir().glob("*.wko5athlete")):
        pytest.skip("no WKO5 athlete folder")
    from backend.api.overview import _dataset, _status
    from backend.engine import overview as O
    ds = _dataset()
    today = O.day_to_date(ds.today)
    st = _status(ds, today)
    base = O.week_plan(ds, st, today)
    assert O.week_plan(ds, st, today, blackouts=()) == base
    monday = date.fromisoformat(base["week"]["start"])
    sunday = monday + dt.timedelta(days=6)
    a, b = max(today, monday + dt.timedelta(days=4)), sunday
    if a > b:
        a = b
    bos = [bo(a.isoformat(), b.isoformat())]
    wp = O.week_plan(ds, st, today, blackouts=bos)
    days = set(BL.blocked(bos))
    assert not [s for s in wp["sessions"] if not s["done"] and s["day"] in days]
    trained = {date.fromisoformat(x["date"]) for x in wp["done"]["activities"]}
    lost = [d for d in (date.fromisoformat(x) for x in sorted(days)) if d not in trained]
    assert wp["blackout_days"] == [d.isoformat() for d in lost]
    if lost:
        assert wp["target"]["hours"] == pytest.approx(base["target"]["hours"] * (7 - len(lost)) / 7)
        assert any(n["src"] == "blackout" and "不排課（連假出遊），本週少" in n["text"] for n in wp["notes"])
