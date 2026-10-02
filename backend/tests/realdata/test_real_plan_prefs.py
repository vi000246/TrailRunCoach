"""Real-data half of backend/tests/test_plan_prefs.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import pytest
from backend.engine import plan_prefs as PP


@pytest.mark.golden
def test_week_plan_with_prefs_on_the_athletes_data():
    """week_plan() itself (not only the projection): defaults identical, a
    50-min cap keeps the long / quality / strength sessions and caps them."""
    from backend.settings.paths import athlete_dir
    if not any(athlete_dir().glob("*.wko5athlete")):
        pytest.skip("no WKO5 athlete folder")
    from backend.api.overview import _dataset, _status
    from backend.engine import overview as O
    ds = _dataset()
    today = O.day_to_date(ds.today)
    st = _status(ds, today)
    base = O.week_plan(ds, st, today)
    assert O.week_plan(ds, st, today, prefs=PP.Prefs()) == base
    capped = O.week_plan(ds, st, today, prefs=PP.Prefs(cap_weekday=50, cap_mode="hard"))
    # a hot A/B race in the plan adds heat sessions; a hard cap turns them into run + bath (heat_plan.py)
    kinds = lambda wp: sorted({s["id"].rstrip("0123456789") for s in wp["sessions"]
                               if s["kind"] != "heat_passive"} - {"easy", "steep"})
    # (steep = an easy run turned into the 陡坡健走 session, engine/steep_hill.py:
    # whether one fits depends on where the cap puts the easy runs)
    assert kinds(capped) == kinds(base)                   # nothing but easy runs is lost
    for s in capped["sessions"]:
        if s["kind"] != "test" and not s["done"]:
            assert s["minutes"] <= 50, s
