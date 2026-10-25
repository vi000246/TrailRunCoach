"""
SP-866: the A race taper's one short maintenance strength session (SP-671, overview.drop_strength_before_a)
is never the day after a general strength session — the week's (done or planned, before the taper), or
the previous week's (projection passes them as `before`). It moves to a later day of the taper's first 3
days; none when no day there fits. Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import balance_plan as BP
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.tests.test_b2b import _phases
from backend.tests.test_strength_stop import _plan
from backend.tests.test_taper_strength_sp671 import _easy, _maint, _st, _stop


def _m(out):
    return [s for s in _maint(out) if "減量期維持" in s["title"]]


def test_strength_the_day_before_the_taper_moves_the_maintenance_a_day_later():
    # race Sat 10/17, 10-day taper from Wed 10/07 (keep Wed–Fri); the week's strength Tue 10/06 (kept) and
    # Sat 10/10 (in the window → the maintenance): Wed 10/07 would follow Tue's → Thu 10/08
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-06"), _easy("2026-10-07"), _easy("2026-10-08"), _st("strength2", "2026-10-10")]
    notes = []
    out = O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, notes)
    m = _m(out)
    assert len(m) == 1 and m[0]["day"] == "2026-10-08"
    assert {s["id"]: s["day"] for s in out if s["kind"] == "strength"} == {"strength1": "2026-10-06",
                                                                          "strength2": "2026-10-08"}
    assert "不連兩天做肌力" in notes[0]["text"] and "往後挪" in notes[0]["text"]


def test_the_note_in_english():
    from backend import i18n
    mon = date(2026, 10, 5)
    for ss, want in (([_st("strength1", "2026-10-06"), _easy("2026-10-07"), _easy("2026-10-08"),
                       _st("strength2", "2026-10-10")], "it moves a day later"),
                     ([_st("strength1", "2026-10-06"), _easy("2026-10-07"), _st("strength2", "2026-10-10")],
                      "no maintenance session this time")):
        notes = []
        with i18n.use_locale("en"):
            O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, notes)
        assert want in notes[0]["text"] and "no two strength days in a row" in notes[0]["text"]


def test_a_done_strength_the_day_before_counts_too():
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-06", done=True), _easy("2026-10-07"), _easy("2026-10-09"),
          _st("strength2", "2026-10-10")]
    m = _m(O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, []))
    assert len(m) == 1 and m[0]["day"] == "2026-10-09"


def test_a_strength_already_on_the_first_day_moves_off_it():
    # the week's strength on Wed 10/07 (the taper's first day) right after Tue 10/06's: it moves to Thu
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-06"), _st("strength2", "2026-10-07"), _easy("2026-10-08")]
    out = O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, [])
    m = _m(out)
    assert len(m) == 1 and m[0]["id"] == "strength2" and m[0]["day"] == "2026-10-08"


def test_no_later_day_in_the_first_3_no_maintenance():
    # only Wed 10/07 is an easy day of the window and it follows Tue's strength: no maintenance session
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-06"), _easy("2026-10-07"), _st("strength2", "2026-10-10")]
    notes = []
    out = O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, notes)
    assert not _m(out) and [s["id"] for s in _maint(out)] == ["strength1"]
    assert "這次不留維持課" in notes[0]["text"]


def test_previous_weeks_strength_on_the_sunday_before_a_monday_taper():
    # race Thu 10/15, 10-day taper from Mon 10/05 (keep Mon–Wed, none from Thu 10/08); the previous
    # week's strength on Sun 10/04 (`before`): the Monday strength moves to Tue's easy run
    mon = date(2026, 10, 5)
    stop = _stop("2026-10-15", 10)
    assert O.taper_strength_days(stop) == ["2026-10-05", "2026-10-06", "2026-10-07"]
    ss = [_st("strength1", "2026-10-05"), _easy("2026-10-06")]
    assert _m(O.drop_strength_before_a(ss, [stop], mon, []))[0]["day"] == "2026-10-05"          # without: as before
    ss = [_st("strength1", "2026-10-05"), _easy("2026-10-06")]
    m = _m(O.drop_strength_before_a(ss, [stop], mon, [], before=["2026-10-04"]))
    assert len(m) == 1 and m[0]["day"] == "2026-10-06"


def test_no_strength_the_day_before_unchanged():
    """SP-671 as before: the first easy day of the window when the day before has no strength."""
    mon = date(2026, 10, 5)
    ss = [_st("strength1", "2026-10-05"), _easy("2026-10-07"), _easy("2026-10-08"), _st("strength2", "2026-10-10")]
    notes = []
    m = _m(O.drop_strength_before_a(ss, [_stop("2026-10-17", 10)], mon, notes))
    assert len(m) == 1 and m[0]["day"] == "2026-10-07"
    assert "不連兩天做肌力" not in notes[0]["text"]


def _no_two_in_a_row(sessions):
    st = {s["day"] for s in sessions if s["kind"] == "strength" and s.get("id") != BP.ID and s.get("day")}
    for s in sessions:
        if s["kind"] == "strength" and "減量期維持" in (s.get("title") or ""):
            d = date.fromisoformat(s["day"])
            assert (d - dt.timedelta(days=1)).isoformat() not in st, (s["day"], sorted(st))


@pytest.mark.parametrize("race", ["2026-11-07", "2026-11-08", "2026-11-05", "2026-11-12"])
@pytest.mark.parametrize("days", [(1, 5), (2, 6), (0, 3)])
def test_week_plan_and_projection_never_put_it_the_day_after_strength(race, days):
    prefs = PP.Prefs(strength=2, strength_days=days)
    plan, wp = _plan(race, prefs=prefs)
    weeks = PJ.project_weeks(wp, _phases(plan, date(2026, 9, 30)), date.fromisoformat(race) + dt.timedelta(days=1),
                             prefs=prefs)
    _no_two_in_a_row(list(wp["sessions"]) + [s for w in weeks for s in w["sessions"]])
