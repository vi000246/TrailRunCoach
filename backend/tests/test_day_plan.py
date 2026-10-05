"""
SP-114 slice 1: every multi-day trip (百岳, a 分站 越野賽／其他) is saved with each day's distance and
climb (Event.day_plan); the totals are their sums; a GPX with its own day ends wins; the equal split
is left for an old event without them, which gets the 「請補每天的距離和爬升」 hint instead of a block.
"""
import datetime as dt

import pytest

from backend.engine import planning as P
from backend.engine import race_feasibility as F
from backend.engine.panels import race_refs as RR

DAYS3 = [{"km": 20.0, "gain_m": 1500, "loss_m": 300}, {"km": 15.0, "gain_m": 1000}, {"km": 30.0, "gain_m": 400, "loss_m": 2600}]


def up(**kw):
    a = {"name": "大小霸", "date": "2027-01-10", "kind": "baiyue", "days": 3}
    a.update(kw)
    return P.Plan().upsert_event(a)


def test_a_multi_day_trip_needs_every_day():
    with pytest.raises(P.EventError, match="每天的距離和爬升"):
        up()                                                        # none at all
    with pytest.raises(P.EventError, match="3 天要填 3 列"):
        up(day_plan=DAYS3[:2])                                      # 少一天不能存
    with pytest.raises(P.EventError, match="第 2 天"):
        up(day_plan=[DAYS3[0], {"km": 15.0, "gain_m": None}, DAYS3[2]])
    with pytest.raises(P.EventError, match="第 1 天"):
        up(day_plan=[{"km": 0, "gain_m": 100}] + DAYS3[1:])


def test_the_totals_are_the_sums_and_the_descent_is_optional():
    e = up(day_plan=DAYS3, distance_km=99, climbing_m=9999)
    assert e.distance_km == 65.0 and e.climbing_m == 2900
    assert e.day_plan[1]["loss_m"] is None and e.day_plan[2]["loss_m"] == 2600
    assert not e.day_plan_missing


def test_who_is_asked():
    # a one-day event, a road race, a 連續 race: no day plan (whatever was sent is dropped)
    assert up(days=1, day_plan=DAYS3[:1]).day_plan is None
    assert up(kind="road", day_plan=None).day_plan is None
    e = up(kind="race", race_format="continuous", distance_km=160, climbing_m=9000)
    assert e.day_plan is None and e.continuous and e.split_days == 1
    # a stage race (the default 賽制 of a ≥ 2 day 越野賽) is asked, like 百岳
    with pytest.raises(P.EventError):
        up(kind="race")
    e = up(kind="other", day_plan=DAYS3)
    assert e.race_format == "stage" and e.split_days == 3
    assert up(kind="baiyue", day_plan=DAYS3).race_format is None    # 百岳 is always split by day: not asked


def test_an_old_event_loads_and_is_hinted_not_blocked(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text('{"events": [{"id": "x", "name": "大小霸", "date": "2027-01-10", "kind": "baiyue", "days": 3,'
                    ' "distance_km": 65, "climbing_m": 3500}]}', "utf-8")
    e = P.Plan.load(path).events[0]
    assert e.day_plan is None and e.day_plan_missing
    assert P.event_json(e, dt.date(2026, 10, 5))["day_plan_missing"] is True
    c = RR.course_of(e, lambda e: None)
    assert c["split_source"] == "equal"
    ln = RR.race_line(e, [5.0, 5.0, 5.0], "x", c)
    assert "請補每天的距離和爬升" in F.split_note(ln)


def gpx(source):
    days = [{"day": 1, "km": 25.0, "gain_m": 1800.0, "loss_m": 500.0}, {"day": 2, "km": 15.0, "gain_m": 900.0, "loss_m": 900.0},
            {"day": 3, "km": 26.0, "gain_m": 300.0, "loss_m": 2700.0}]
    return lambda e: {"totals": {"km": 66.0, "gain_m": 3000.0, "loss_m": 4100.0}, "days": days,
                      "split_source": source, "filename": "dxb.gpx"}


def test_the_course_a_gpx_with_day_ends_then_the_day_plan_then_equal():
    e = up(day_plan=DAYS3)
    c = RR.course_of(e, lambda e: None)
    assert c["split_source"] == "day_plan" and [d["gain_m"] for d in c["days"]] == [1500, 1000, 400]
    assert c["days"][1]["loss_m"] == 1000                           # blank descent = the climb (推估)
    assert c["descent_assumed"]
    assert RR.course_of(e, gpx("stored"))["days"][0]["gain_m"] == 1800      # the GPX's day ends win
    assert RR.course_of(e, gpx("camp"))["days"][0]["gain_m"] == 1800
    c = RR.course_of(e, gpx("equal"))                                       # a GPX cut equally: the user's days
    assert c["split_source"] == "day_plan" and c["filename"] == "dxb.gpx"
    ln = RR.race_line(e, None, "x", c) or RR.race_line(P.Event(**{**e.__dict__, "est_hours": 20.0}), None, "x", c)
    assert ln["hardest_day"] == 1 and F.split_note(ln) is None


def test_a_continuous_race_is_one_piece():
    e = up(kind="race", race_format="continuous", distance_km=100, climbing_m=6000, est_hours=26.0)
    c = RR.course_of(e, lambda e: None)
    assert len(c["days"]) == 1 and c["split_source"] == "single"


def test_the_api_takes_the_gpx_days_when_it_has_day_ends(tmp_path, monkeypatch):
    from backend.api import plan as A
    from backend.engine import event_gpx as EG
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    st = gpx("stored")(None)
    monkeypatch.setattr(EG, "day_stats", lambda eid, days=1, **kw: {**st, "split_source": "stored"} if eid == "g" else None)
    r = A.put_event(A.EventIn(id="g", name="大小霸", date="2027-01-10", kind="baiyue", days=3))
    assert r["event"]["climbing_m"] == 3000 and r["event"]["day_plan"][0]["km"] == 25.0
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as x:
        A.put_event(A.EventIn(id="n", name="合歡北西", date="2027-02-10", kind="baiyue", days=2))
    assert x.value.status_code == 400 and "每天的距離和爬升" in x.value.detail
