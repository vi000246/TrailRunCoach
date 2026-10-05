"""
planning.event_size (SP-111): 賽事大小 from the predicted time → EP → km, never the horizontal
km alone when more is known; multi-day trips are 超馬級. The calculator / personal divisor hooks
(install_size_inputs) are unset in tests unless a test sets them.
"""
import datetime as dt

import pytest

from backend.engine import planning as P
from backend.engine.planning import Event


def ev(**kw):
    a = dict(id="e", name="測試", date="2027-03-06", kind="race", priority="A")
    a.update(kw)
    return Event(**a)


@pytest.mark.parametrize("hours, size", [(1.5, P.SHORT), (2.0, P.MEDIUM), (3.9, P.MEDIUM), (4.0, P.MARATHON),
                                         (5.9, P.MARATHON), (6.0, P.ULTRA), (19.9, P.ULTRA), (20.0, P.HUNDRED)])
def test_time_cuts(hours, size):
    assert P.event_size(ev(distance_km=10, climbing_m=0, est_hours=hours)) == size


@pytest.mark.parametrize("km, climb, size", [(20, 0, P.SHORT), (15, 600, P.MEDIUM), (30, 2000, P.MARATHON),
                                             (42, 0, P.MARATHON), (50, 1500, P.ULTRA), (100, 6000, P.HUNDRED)])
def test_ep_when_no_time(km, climb, size):
    assert P.event_size(ev(distance_km=km, climbing_m=climb)) == size


def test_km_only_when_not_even_the_climb_is_known():
    assert P.event_size(ev(distance_km=42)) == P.MARATHON          # was 「長賽」 before SP-111
    assert P.event_size(ev(distance_km=60)) == P.ULTRA
    assert P.event_size(ev()) == P.SHORT


def test_time_wins_over_ep():
    # 30 km ↑2000 m: EP 50 (馬拉松級) but 7 h → 超馬級
    assert P.event_size(ev(distance_km=30, climbing_m=2000, est_hours=7.0)) == P.ULTRA
    assert P.event_size(ev(distance_km=30, climbing_m=2000), hours=3.0) == P.MEDIUM


def test_multi_day_is_ultra():
    assert P.event_size(ev(kind="baiyue", days=2, distance_km=20, climbing_m=1500, est_hours=5)) == P.ULTRA
    assert ev(kind="baiyue", days=3).is_long


def test_is_long_is_ultra_or_bigger():
    assert ev(distance_km=30, climbing_m=2000, est_hours=7.0).is_long
    assert not ev(distance_km=30, climbing_m=2000).is_long          # EP 50, no time
    assert ev(distance_km=30, climbing_m=2000).size == "marathon"


def test_hooks_calculator_then_divisor(monkeypatch):
    e = ev(distance_km=30, climbing_m=2000, est_hours=3.0)
    monkeypatch.setattr(P, "HOURS_OF", lambda x: 8.0)              # the calculator wins over est_hours
    assert P.event_size(e) == P.ULTRA
    monkeypatch.setattr(P, "HOURS_OF", lambda x: None)             # no prediction → est_hours
    assert P.event_size(e) == P.MEDIUM
    monkeypatch.setattr(P, "HOURS_OF", lambda x: 1 / 0)            # a broken calculator → est_hours
    assert P.event_size(e) == P.MEDIUM
    e = ev(distance_km=30, climbing_m=2000)
    monkeypatch.setattr(P, "DIVISOR_OF", lambda: 200.0)            # EP 40 with a personal 200
    assert P.event_size(e) == P.MEDIUM
    monkeypatch.setattr(P, "DIVISOR_OF", lambda: 1 / 0)
    assert P.event_size(e) == P.MARATHON                            # ITRA's 100


def test_recovery_follows_the_size():
    """A 30 km ↑2000 m race without est_hours used to be a short race (7-day recovery); with a
    7 h prediction it is 超馬級 → 14 days."""
    begin, end = dt.date(2026, 10, 1), dt.date(2027, 6, 1)

    def rec_days(e):
        r = next(p for p in P.auto_phases([e], begin, end, 0) if p.kind == "recovery")
        return (P._d(r.end) - P._d(r.start)).days + 1
    assert rec_days(ev(distance_km=30, climbing_m=2000)) == 7
    assert rec_days(ev(distance_km=30, climbing_m=2000, est_hours=7.0)) == 14
