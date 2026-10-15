"""
SP-504 R1: a 百岳 trip (planning event kind baiyue, e.g. 大小霸 3 天) on the 課表 is a 行程, not a 賽 / 比賽. The
calendar API tags the activity on its days with the event's `kind`, so schedule.html writes 「B 行程：X」／
「C 行程：X」 on the day and 「行程：X」 on the activity (race.trip_* strings, zh / en) instead of 「B 賽：X」／「比賽：X」.
The browser side: e2e/test_schedule_trip_mark_sp504r1_e2e.py. Synthetic.
"""
import json
import re
from pathlib import Path

from backend.tests.test_race_day_sp504 import ACT_RACE, EV, EV_A, _cal

I18N = Path(__file__).resolve().parents[1] / "static" / "i18n"
TRIP = {"id": "t1", "name": "大小霸 3 日", "priority": "C", "kind": "baiyue", "start": "2026-09-29",
        "end": "2026-10-01"}
TRIP_KEYS = ("race.trip_mark", "race.trip_tip_a", "race.trip_tip_b", "race.trip_tip_c", "race.trip_act",
             "race.trip_act_tip", "race.trip_act_note")         # (R2: + trip_tip_a, the A trip's mark)


def _cat(loc: str) -> dict:
    return json.loads((I18N / loc / "schedule.json").read_text(encoding="utf-8"))


def test_calendar_tags_a_trip_day_activity_with_its_kind(monkeypatch):
    """Each day of a 3-day trip: the hike is the trip (kind baiyue); a trail race's run keeps kind race."""
    from backend.tests.test_plan_store import API
    acts = [{**ACT_RACE, "index": 30 + i, "date": f"2026-09-{29 + i}" if i < 2 else "2026-10-01",
             "category": "hike", "category_label": "健行"} for i in range(3)]
    run = {**ACT_RACE, "index": 40, "date": "2026-10-10"}
    with _cal(monkeypatch, acts=(*acts, run), events=(TRIP, EV)) as e:
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-11").json()
        by = {a["index"]: a for a in b["activities"]}
        for i in (30, 31, 32):
            assert by[i]["race"] == {"id": "t1", "name": "大小霸 3 日", "priority": "C", "kind": "baiyue"}
        assert by[40]["race"] == {"id": "e1", "name": "北插天山", "priority": "B", "kind": "race"}
        assert [(r["name"], r["kind"], r["start"], r["end"]) for r in b["races"]] == \
            [("大小霸 3 日", "baiyue", "2026-09-29", "2026-10-01"), ("北插天山", "race", "2026-10-10", "2026-10-10")]


def test_an_a_trip_activity_is_tagged_too(monkeypatch):
    from backend.tests.test_plan_store import API
    a_trip = {**EV_A, "kind": "baiyue", "name": "A 百岳"}
    with _cal(monkeypatch, acts=({**ACT_RACE, "category": "hike"},), events=(a_trip,)) as e:
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        assert b["activities"][0]["race"] == {"id": "e0", "name": "A 百岳", "priority": "A", "kind": "baiyue"}


def test_trip_strings_say_trip_not_race_in_both_languages():
    zh, en = _cat("zh-TW"), _cat("en")
    for k in TRIP_KEYS:
        assert k in zh and k in en, k
        assert set(re.findall(r"\{(\w+)\}", zh[k])) == set(re.findall(r"\{(\w+)\}", en[k])), k
        assert "行程" in zh[k] and "比賽" not in zh[k] and "賽「" not in zh[k] and " 賽：" not in zh[k], zh[k]
        assert "trip" in en[k].lower() and "race" not in en[k].lower(), en[k]
    assert zh["race.trip_mark"].format(p="C", name="大小霸 3 日") == "C 行程：大小霸 3 日"
    assert zh["race.trip_act"].format(name="大小霸 3 日") == "行程：大小霸 3 日"
    assert en["race.trip_mark"].format(p="B", name="X") == "B trip: X"
    assert en["race.trip_act"].format(name="X") == "Trip: X"
    # the race strings stay as they were (a 越野賽 / 路跑賽 is still 「B 賽：X」／「比賽：X」)
    assert zh["race.mark"] == "{p} 賽：{name}" and zh["race.act"] == "比賽：{name}"
    assert en["race.mark"] == "{p} race: {name}" and en["race.act"] == "Race: {name}"


def test_schedule_picks_the_trip_strings_by_kind():
    """Every place schedule.html writes a race mark / race activity goes through raceT (kind baiyue → trip)."""
    src = (Path(__file__).resolve().parents[1] / "static" / "schedule.html").read_text(encoding="utf-8")
    assert re.search(r'raceT = \(r, k, p\) => tt\(r && r\.kind === "baiyue" \? RACE_TRIP\[k\] : k, p\)', src)
    for k in ("race.mark", "race.mark_tip_a", "race.mark_tip_b", "race.mark_tip_c", "race.act", "race.act_tip",
              "race.act_note"):
        assert f'tt("{k}"' not in src, k                          # never the race string straight
    trip = dict(re.findall(r'"(race\.[a-z_]+)": "(race\.trip_[a-z_]+)"', src))
    assert sorted(trip.values()) == sorted(TRIP_KEYS)
