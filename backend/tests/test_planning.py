import datetime as dt

import pytest

from backend.engine import planning as P


def ev(name, date, pri="A", **kw):
    return P.Event(id=name, name=name, date=date, priority=pri, **kw)


def test_auto_phases_backwards_from_a_event():
    e = ev("race", "2026-12-20", distance_km=50, climbing_m=3000)
    ph = P.auto_phases([e], dt.date(2026, 6, 1), dt.date(2027, 2, 1))
    kinds = [(p.kind, p.start, p.end) for p in ph]
    assert kinds[0] == ("base", "2026-06-01", "2026-10-10")
    assert kinds[1] == ("specific", "2026-10-11", "2026-12-05")     # 8 weeks
    assert kinds[2] == ("taper", "2026-12-06", "2026-12-19")        # 14 days
    assert kinds[3] == ("event", "2026-12-20", "2026-12-20")
    assert kinds[4] == ("recovery", "2026-12-21", "2027-01-03")     # 50 km: long -> 14 d
    assert kinds[5] == ("base", "2027-01-04", "2027-02-01")


def test_phases_are_contiguous_and_non_overlapping():
    evs = [ev("a", "2026-11-01", est_hours=4), ev("b", "2026-12-15", days=3), ev("x", "2026-11-20", "B")]
    ph = P.auto_phases(evs, dt.date(2026, 6, 1), dt.date(2027, 3, 1))
    for p, q in zip(ph, ph[1:]):
        assert P._d(q.start) == P._d(p.end) + dt.timedelta(days=1)
    # second A event too close for a full 8-week specific block: clipped, never overlaps
    assert [p.kind for p in ph if p.event_id == "b"][0] == "specific"
    # B events never create phases
    assert all(p.event_id != "x" for p in ph)
    # multi-day event
    evb = [p for p in ph if p.kind == "event" and p.event_id == "b"][0]
    assert (evb.start, evb.end) == ("2026-12-15", "2026-12-17")


def test_short_event_recovers_one_week():
    ph = P.auto_phases([ev("a", "2026-11-01", est_hours=3)], dt.date(2026, 6, 1), dt.date(2026, 12, 31))
    rec = [p for p in ph if p.kind == "recovery"][0]
    assert (rec.start, rec.end) == ("2026-11-02", "2026-11-08")


def test_goals_take_hardest_upcoming_a_b_and_ignore_c():
    plan = P.Plan(events=[
        ev("steep", "2026-11-01", distance_km=20, climbing_m=2000),          # 100 m/km
        ev("long", "2026-12-01", "B", distance_km=60, climbing_m=3000, est_hours=12),
        ev("c", "2026-10-15", "C", distance_km=100, climbing_m=9000),
        ev("past", "2026-01-01", distance_km=200, climbing_m=1),
    ])
    g = P.goals(plan, dt.date(2026, 10, 1))
    t = g["targets"]
    assert t["distance_km"]["value"] == 60 and t["distance_km"]["event"] == "long"
    assert t["climb_per_km"]["value"] == 100 and t["climb_per_km"]["event"] == "steep"
    assert t["est_hours"]["value"] == 12
    assert g["next_a"] == "steep" and g["days_to_next_a"] == 31
    assert "c" not in g["events"] and "past" not in g["events"]


def test_threshold_history(tmp_path):
    plan = P.Plan(thresholds=[P.Threshold("2026-03-01", lthr=165), P.Threshold("2026-08-01", aethr=142),
                              P.Threshold("2026-09-01", lthr=168)])
    assert plan.threshold_on("lthr", dt.date(2026, 1, 1)) == 165   # before first test: earliest
    assert plan.threshold_on("lthr", dt.date(2026, 8, 15)) == 165
    assert plan.threshold_on("lthr", dt.date(2026, 9, 2)) == 168
    assert plan.threshold_on("aethr", dt.date(2026, 9, 2)) == 142
    assert plan.threshold_on("mhr", dt.date(2026, 9, 2)) is None
    path = tmp_path / "plan.json"
    plan.upsert_event({"name": "玉山", "date": "2026-11-01", "kind": "baiyue", "priority": "Z", "days": 2})
    plan.save(path)
    back = P.Plan.load(path)
    assert back.events[0].name == "玉山" and back.events[0].priority == "A"   # bad priority -> A
    assert back.threshold_on("lthr", dt.date(2026, 9, 2)) == 168


def test_weight_history_and_profile_roundtrip(tmp_path):
    plan = P.Plan(weights=[P.Weight("2026-03-01", 70.0), P.Weight("2026-08-01", 67.5)],
                  profile={"sex": "male", "height_cm": 172, "power_meter": "coros"})
    assert plan.weight_on(dt.date(2026, 1, 1)) == 70.0        # before first: earliest
    assert plan.weight_on(dt.date(2026, 8, 15)) == 67.5
    path = tmp_path / "plan.json"
    plan.save(path)
    back = P.Plan.load(path)
    assert back.weight_on(dt.date(2026, 5, 1)) == 70.0
    assert back.profile["power_meter"] == "coros"
    assert P.Plan().weight_on(dt.date(2026, 1, 1)) is None


def test_bad_date_rejected():
    with pytest.raises(ValueError):
        P.Plan().upsert_event({"name": "x", "date": "2026-13-40"})
