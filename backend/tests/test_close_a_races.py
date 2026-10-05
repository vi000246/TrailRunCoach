"""
SP-90: two A races close together (engine/planning.auto_phases). The first race's 恢復期 yields
to the second race's 減量期 (recovery_and_taper), the second race keeps its event days, the
phases say what was cut short, and a < 12-week gap gets TrainerRoad's hint. Synthetic plans only.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import planning as P
from backend.engine import projection as PJ


def ev(name, date_, pri="A", **kw):
    return P.Event(id=name, name=name, date=date_, priority=pri, **kw)


B, E = date(2026, 6, 1), date(2027, 12, 31)
SHORT = dict(distance_km=21, climbing_m=0, est_hours=2.5)      # 7-day recovery
LONG = dict(distance_km=50, climbing_m=3000, est_hours=9.0)    # 14-day recovery


def of(ph, kind, eid):
    return [p for p in ph if p.kind == kind and p.event_id == eid]


def days(p):
    return (P._d(p.end) - P._d(p.start)).days + 1


def contiguous(ph):
    for p, q in zip(ph, ph[1:]):
        assert P._d(q.start) == P._d(p.end) + dt.timedelta(days=1), (p, q)


@pytest.mark.parametrize("free, rec, taper, out", [
    (40, 7, 14, (7, 14)),          # room for both
    (27, 14, 14, (13, 14)),        # the recovery shrinks first
    (20, 14, 14, (7, 13)),         # recovery floor 7, the taper gets the rest
    (9, 7, 14, (4, 5)),            # split, the taper gets the odd day
    (0, 7, 14, (0, 0)),
])
def test_recovery_and_taper(free, rec, taper, out):
    assert P.recovery_and_taper(free, rec, taper) == out


def test_ten_days_apart_the_second_race_keeps_its_event_and_a_taper():
    r1, r2 = ev("r1", "2027-03-06", **SHORT), ev("r2", "2027-03-16", **SHORT)
    ph = P.auto_phases([r1, r2], B, E)
    contiguous(ph)
    assert [(p.start, p.end) for p in of(ph, "event", "r2")] == [("2027-03-16", "2027-03-16")]
    rec, tap = of(ph, "recovery", "r1")[0], of(ph, "taper", "r2")[0]
    assert (rec.start, rec.end, tap.start, tap.end) == ("2027-03-07", "2027-03-10", "2027-03-11", "2027-03-15")
    assert "恢復期縮短為 4 天" in rec.note and "r2" in rec.note and "3/16" in rec.note
    assert "減量期縮短為 5 天" in tap.note and "r1" in tap.note
    assert "只隔 10 天" in tap.note and "12 週" in tap.note and "B 賽" in tap.note
    assert not of(ph, "specific", "r2") and not of(ph, "transition", "r1")


def test_four_weeks_apart_after_an_ultra_full_taper_recovery_13_days():
    r1, r2 = ev("r1", "2027-03-06", **LONG), ev("r2", "2027-04-03", **SHORT)
    ph = P.auto_phases([r1, r2], B, E)
    contiguous(ph)
    rec, tap = of(ph, "recovery", "r1")[0], of(ph, "taper", "r2")[0]
    assert days(rec) == 13 and "恢復期縮短為 13 天" in rec.note
    assert days(tap) == P.TAPER_DAYS and "減量期縮短" not in tap.note
    assert "只隔 4 週" in tap.note
    # the 轉換期 note never cites a 專項期 start in the past
    assert "專項期" not in rec.note


def test_eight_weeks_apart_shortened_specific_and_the_hint():
    r1, r2 = ev("r1", "2027-03-06", **SHORT), ev("r2", "2027-05-01", **SHORT)
    ph = P.auto_phases([r1, r2], B, E)
    contiguous(ph)
    rec = of(ph, "recovery", "r1")[0]
    assert days(rec) == 7
    sp = of(ph, "specific", "r2")[0]
    assert sp.start == "2027-03-14" and days(sp) == 34
    assert "專項期縮短為 34 天（原本 8 週）" in sp.note and "只隔 8 週" in sp.note
    # no 轉換期: its note says how long until the next race, not a past 專項期 date
    assert rec.note.startswith("沒有轉換期") and "只剩 6 週" in rec.note and "2027-0" not in rec.note
    assert days(of(ph, "taper", "r2")[0]) == P.TAPER_DAYS


def test_twenty_weeks_apart_unchanged():
    r1, r2 = ev("r1", "2027-03-06", **SHORT), ev("r2", "2027-07-24", **SHORT)
    both = P.auto_phases([r1, r2], B, E)
    alone = P.auto_phases([r1], B, E)
    assert [(p.kind, p.start, p.end) for p in both[:6]] == [(p.kind, p.start, p.end) for p in alone[:6]]
    assert not any(p.note for p in both if p.event_id == "r2")
    assert days(of(both, "specific", "r2")[0]) == P.SPECIFIC_WEEKS * 7


def test_overlapping_a_races_do_not_crash():
    r1 = ev("r1", "2027-03-06", days=3, distance_km=60, climbing_m=4000)
    r2 = ev("r2", "2027-03-07", **SHORT)
    ph = P.auto_phases([r1, r2], B, E)
    contiguous(ph)
    assert of(ph, "event", "r1")


def test_downstream_helpers_stay_sane():
    r1, r2 = ev("r1", "2027-03-06", **SHORT), ev("r2", "2027-03-16", **SHORT)
    plan = P.Plan(events=[r1, r2])
    ph = P.auto_phases([r1, r2], B, E)
    # the recovery after r1 and after r2 are post-race days; r2's taper is not
    post = P.post_race_days(plan, date(2027, 3, 1), date(2027, 4, 30), 0)
    assert date(2027, 3, 8) in post and date(2027, 3, 12) not in post and date(2027, 3, 18) in post
    # the pre-race weeks of r2's recovery: complete weeks before its (shortened) taper
    mons = P.pre_race_mondays(ph, date(2027, 3, 18))
    assert len(mons) == 4 and all(m.weekday() == 0 for m in mons) and mons[-1] < date(2027, 3, 11)


def test_week_notes_show_a_phase_starting_mid_week():
    r1, r2 = ev("r1", "2027-03-06", **SHORT), ev("r2", "2027-03-16", **SHORT)
    ph = [P.phase_json(p) for p in P.auto_phases([r1, r2], B, E)]
    # the week of 3/8: the recovery (Monday) and r2's taper from Thursday 3/11
    notes = P.week_phase_notes(ph, date(2027, 3, 8))
    kinds = [k for k, _t in notes]
    assert "recovery" in kinds and "taper" in kinds
    assert any("只隔 10 天" in t for _k, t in notes)
    assert PJ._phase_notes(ph, date(2027, 3, 8)) == notes
