"""SP-525: a 中 B race's easy-only days = one per 3 km of the race (Daniels), a trail race's EP,
at least 3 (短's), at most 7 (an A race of the same size); 短 3 days and 馬拉松級+ unchanged. The week
plan and the projected weeks read the same post_race.b_windows."""
from __future__ import annotations

import datetime as dt
import inspect

import pytest

from backend.engine import planning as P
from backend.engine import post_race as PR
from backend.engine import projection as PJ
from backend.engine.planning import Event


@pytest.fixture(autouse=True)
def _pure(monkeypatch):
    # no app hooks: est_hours sizes the race, ITRA's 100 the EP divisor, no downhill bump
    for k in ("HOURS_OF", "DIVISOR_OF", "RACE_DOWNHILL_OF", "ATHLETE_DOWNHILL_OF"):
        monkeypatch.setattr(P, k, None)


def _ev(km, climb, hours, kind="trail", day="2026-10-24", **kw):
    return Event(id="b", name="B", date=day, kind=kind, priority="B", distance_km=km, climbing_m=climb,
                 est_hours=hours, **kw)


@pytest.mark.parametrize("km,climb,kind,hours,days", [
    (15.0, 0, "road", 2.1, 5),            # 15K → 5 (Daniels' own example)
    (21.1, 0, "road", 2.2, 7),            # half → 7
    (30.0, 0, "road", 3.0, 7),            # 30K → 10, capped at the A race's 7
    (12.0, 0, "road", 2.05, 4),           # 12 km → 4
    (16.6, 890, "race", 2.0, 7),          # 貓空 17K: EP 25.5 → 9 → 7
    (10.0, 300, "trail", 2.1, 4),         # EP 13 → 4
    (8.0, 100, "trail", 2.1, 3),          # EP 9 → 3 (the floor is 短's 3)
])
def test_medium_b_race_one_easy_day_per_3_km(km, climb, kind, hours, days):
    e = _ev(km, climb, hours, kind=kind)
    assert P.event_size(e) == P.MEDIUM
    rp = P.recovery_plan(e, b=True)
    assert rp["days"] == days and rp["rebuild"] == 0


def test_trail_counts_ep_road_counts_km():
    assert P.b_rec_km(_ev(16.6, 890, 2.0, kind="race")) == pytest.approx(25.5)
    assert P.b_rec_km(_ev(21.1, 300, 2.0, kind="road")) == pytest.approx(21.1)
    assert P.b_rec_km(_ev(12.0, None, 2.0, kind="trail")) == pytest.approx(12.0)   # no climb → km
    assert P.b_rec_km(_ev(None, None, 2.5)) is None
    assert P.recovery_plan(_ev(None, None, 2.5), b=True)["days"] == 5             # no distance → the old 5


def test_short_and_marathon_size_unchanged():
    assert P.recovery_plan(_ev(10.0, 0, 0.9, kind="road"), b=True)["days"] == 3
    assert P.recovery_plan(_ev(42.2, 0, 4.5, kind="road"), b=True)["days"] == P.REC_BY_SIZE[P.MARATHON][0]
    # an A race of medium size keeps its 7
    a = Event(id="a", name="A", date="2026-10-24", kind="road", priority="A", distance_km=21.1, est_hours=2.2)
    assert P.recovery_plan(a)["days"] == 7


def test_week_rules_make_the_days_after_easy():
    e = _ev(21.1, 0, 2.2, kind="road", day="2026-10-24")               # Sat → easy through Sat 10/31
    mon = dt.date(2026, 10, 26)
    ss = [{"id": "q", "kind": "quality", "title": "間歇", "day": "2026-10-29", "minutes": 60, "tss": 70},
          {"id": "long", "kind": "long", "title": "長跑", "day": "2026-10-31", "minutes": 120, "tss": 100},
          {"id": "q2", "kind": "quality", "title": "間歇", "day": "2026-11-01", "minutes": 60, "tss": 70}]
    notes: list = []
    out = PR.bc_apply(ss, [e], mon, notes)
    kinds = {s["day"]: s["kind"] for s in out}
    assert kinds == {"2026-10-29": "easy", "2026-10-31": "easy", "2026-11-01": "quality"}
    w = PR.b_windows([e], mon)[0]
    assert w["days"] == 7 and w["easy"] == dt.date(2026, 10, 31)
    assert any("後 7 天只排輕鬆跑" in n["text"] and "每 3 km 一天" in n["text"] for n in notes)


def test_week_plan_and_projection_share_the_windows():
    assert "bc_apply(" in inspect.getsource(PJ.week_sessions) or "bc_apply(" in inspect.getsource(PJ)
    from backend.engine import overview as O
    assert "bc_apply(" in inspect.getsource(O.week_plan)
    src = inspect.getsource(P)
    k = src.index("B_REC_KM_PER_DAY = 3.0")
    note = src[src.rindex("# B races (SP-95)", 0, k):k]
    assert "Daniels" in note and "every 3K" in note and "SP-525" in note


def test_english_note():
    from backend.i18n import use_locale
    e = _ev(21.1, 0, 2.2, kind="road")
    ss = [{"id": "q", "kind": "quality", "title": "間歇", "day": "2026-10-27", "minutes": 60, "tss": 70}]
    with use_locale("en"):
        notes: list = []
        PR.bc_apply(ss, [e], dt.date(2026, 10, 26), notes)
    txt = " ".join(n["text"] for n in notes)
    assert "one day per 3 km" in txt and "Daniels" in txt
