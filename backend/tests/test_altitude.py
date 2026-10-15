"""
高山症預防提醒 (SP-546; engine/altitude.py + suggestions.altitude_rows): a 百岳 trip (GPX or not) or an
event whose GPX reaches ≥ 3,000 m gets one information row in the floating box 1–7 days before its
start — unless the athlete's activities reached 3,000 m in the last 3 months. Short prevention
points; the GPX nights add a high first night / a big jump / 行前一晚 to its ?. Synthetic only.
"""
import datetime as dt

import pytest

from backend.engine import altitude as AL
from backend.engine import event_gpx as EG
from backend.engine import suggestions as SG
from backend.engine.planning import Event
from backend.tests.test_event_gpx import synth_gpx
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 5)


@pytest.fixture
def store(tmp_path, monkeypatch):
    db, root = tmp_path / "app.db", tmp_path / "gpx"
    monkeypatch.setattr(EG, "_default_db", lambda: db)
    monkeypatch.setattr(EG, "ROOT", root)
    EG._memo.clear()
    return db, root


# 玉山-like: 1,000 m start, up to ≈ 3,450 m at km 8 (camp), summit ≈ 3,950 m at km 11, down again
YUSHAN = synth_gpx([(8, 2450), (3, 500), (11, -2950)], camps=(8,))


def _act(day, top, hours=6.0, base=1500.0):
    """An activity climbing from `base` to `top` m and back, `hours` long, 1 sample a minute."""
    n = int(hours * 60)
    up = [base + (top - base) * min(1.0, 2 * i / n) for i in range(n // 2)]
    z = up + up[::-1]
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(6)), sport="run",
                       channels={"elapsedtime": [i * 60.0 for i in range(len(z))], "elevation": z},
                       metrics={"duration": hours * 3600})


def _ds(acts):
    return FakeDataset(acts, TODAY, settings={"runthr": 165.0})


def test_event_altitude_from_the_gpx(store):
    assert AL.event_altitude("none") is None                      # no GPX: nothing
    EG.save("yu", YUSHAN, "yushan.gpx")
    a = AL.event_altitude("yu", 2)
    assert a["max_m"] == pytest.approx(3950, abs=40)
    assert a["nights"] is None                                    # SP-533: the camp waypoint is a candidate
    EG.set_splits("yu", [8.0])                                    # … the night once the user sets it
    a = AL.event_altitude("yu", 2)
    assert a["nights_source"] == "stored" and a["nights"][0] == pytest.approx(3450, abs=40)
    assert AL.event_altitude("yu", 1)["nights"] is None           # a day trip has no night
    assert AL.event_altitude("yu", 3)["nights"] is None           # 3 days, 1 camp: equal km → unknown


def test_day_altitudes_and_exposure():
    acts = [_act(TODAY - dt.timedelta(days=5), 3100, hours=30),     # over a night: 2 days high
            _act(TODAY - dt.timedelta(days=4), 3000), _act(TODAY - dt.timedelta(days=3), 2800),
            _act(TODAY - dt.timedelta(days=40), 3200), _act(TODAY - dt.timedelta(days=41), 3300),
            _act(TODAY - dt.timedelta(days=200), 3500),
            _act(TODAY - dt.timedelta(days=2), 1200)]
    alts = AL.day_altitudes(_ds(acts), TODAY)
    assert alts[TODAY - dt.timedelta(days=5)] == pytest.approx(3100, abs=5)
    assert alts[TODAY - dt.timedelta(days=4)] >= 3000                 # the 30-h activity and the next one
    ex = AL.exposure(alts, TODAY, TODAY + dt.timedelta(days=6))
    assert ex["nights"] == 2                                           # the nights −5→−4 and −4→−3
    assert ex["pre_days"] == 4 and ex["recent"] and ex["ever"]


def test_exposure_counts_only_nights_between_two_high_days():
    alts = {TODAY - dt.timedelta(days=3): 2800.0, TODAY - dt.timedelta(days=1): 2900.0}
    ex = AL.exposure(alts, TODAY, TODAY + dt.timedelta(days=5))
    assert ex["nights"] == 0 and not ex["recent"] and not ex["ever"]


def _row(alt, ex=None, days_to=6, name="玉山", kind="baiyue"):
    return AL.reminder({"id": "yu", "name": name, "start": TODAY + dt.timedelta(days=days_to), "kind": kind},
                       alt, NONE if ex is None else ex, TODAY)


NONE = {"nights": 0, "pre_days": 0, "recent": False, "ever": False}
OLD = {"nights": 0, "pre_days": 0, "recent": False, "ever": True}         # up high once, > 3 months ago
PLACES = ("松雪樓", "合歡山", "塔塔加", "大禹嶺", "排雲", "滑雪山莊")


def test_reminder_is_short_prevention():
    r = _row({"max_m": 3952.0, "nights": [3402]})
    assert r["title"] == "「玉山」最高約 3,952 m：預防高山症"
    assert r["reason"] == ("近 3 個月沒有到 3,000 m 以上：高山症風險較高（Shen 2024）。"
                           "上升放慢，出現頭痛、噁心等症狀就不再往上，變嚴重就下撤（CDC）。")
    for s in ("第一次上 3,000 m", "第一晚睡在約 3,402 m", "行前一晚住 2,500 m 左右", "體能好壞不影響高山症風險",
              "出發前 7 天內提醒", "不會改課表", "CDC", "Shen 2024", "推估", "玉山國家公園"):
        assert s in r["help"], s
    assert r["flags"] == ["first", "n1"] and "玉山國家公園" in r["src"]
    # nothing about sleeping nights up high beforehand (the old 適應週末 / 2-night advice), no place
    for s in ("2,750", "適應週末", "睡在高處", "Schneider", "Beidleman") + PLACES:
        assert s not in r["title"] + r["reason"] + r["help"], s
    # up high once before, a night-to-night jump, no 行前一晚 (first night under 3,000 m)
    r = _row({"max_m": 3800.0, "nights": [2900, 3600]}, OLD)
    assert r["flags"] == ["jump"] and "不超過 500 m" in r["help"] and "第一次" not in r["help"]
    assert "行前一晚" not in r["help"] and "玉山國家公園" not in r["src"]


def test_a_baiyue_without_gpx_still_gets_it():
    r = _row(None)
    assert r["title"] == "「玉山」：預防高山症" and r["max_m"] is None and r["nights"] is None
    assert _row(None, kind="race") is None                              # a race without a GPX: nothing known
    assert _row({"max_m": 3100.0}, kind="race")["title"].startswith("「玉山」最高約 3,100 m")
    assert _row({"max_m": 2900.0}, kind="race") is None                 # under 3,000 m, not a 百岳


def test_only_the_week_before():
    alt = {"max_m": 3952.0}
    for n in (1, 7):
        assert _row(alt, days_to=n) is not None, n
    for n in (0, 8, 14, 20, 28, -1):
        assert _row(alt, days_to=n) is None, n


def test_no_reminder_after_3000_m_in_the_last_3_months():
    assert _row({"max_m": 3952.0}, {**OLD, "recent": True}) is None
    # from the activities: 3,050 m 80 days ago → none; 120 days ago → shown (no first-time line)
    alts = {TODAY - dt.timedelta(days=80): 3050.0}
    assert AL.exposure(alts, TODAY, TODAY + dt.timedelta(days=5))["recent"]
    alts = {TODAY - dt.timedelta(days=120): 3050.0, TODAY - dt.timedelta(days=10): 2990.0}
    ex = AL.exposure(alts, TODAY, TODAY + dt.timedelta(days=5))
    assert not ex["recent"] and ex["ever"]
    r = _row({"max_m": 3952.0}, ex)
    assert r is not None and "first" not in r["flags"]


def test_english():
    from backend.i18n import use_locale
    with use_locale("en"):
        r = _row({"max_m": 3952.0, "nights": [3402]})
        bare = _row(None)
    assert r["title"] == "\"玉山\" tops out at about 3,952 m: preventing altitude sickness"
    assert r["reason"].endswith("descend if worse (CDC).") and "around 2,500 m" in r["help"]
    # SP-546 R1: the box's English text leaves out the Shen 2024 half (3 lines at most); the ? keeps it, first
    assert r["reason"] == "Go up slowly; with a headache or nausea go no higher, descend if worse (CDC)."
    assert "Shen" not in r["reason"] and len(r["reason"]) <= 90, r["reason"]
    assert r["help"].startswith("Nothing above 3,000 m in 3 months: higher risk of altitude sickness (Shen 2024).")
    assert "Shen 2024" in r["src"] and "the day before" in r["help"]
    assert "。" not in r["reason"] + r["help"] and "；" not in r["src"].split("1161）")[-1]
    assert "7 days before the trip" in r["help"]
    assert bare["title"].endswith(": preventing altitude sickness")
    for txt in (r["reason"], r["help"].replace("玉山", "")):
        assert not any("一" <= ch <= "鿿" for ch in txt), txt


def test_box_rows_one_id_per_trip_and_the_activities_read_once():
    evs = [Event("yu", "玉山", (TODAY + dt.timedelta(days=6)).isoformat(), kind="baiyue", days=2),
           Event("ng", "沒有 GPX 的百岳", (TODAY + dt.timedelta(days=3)).isoformat(), kind="baiyue"),
           Event("lo", "低的越野賽", (TODAY + dt.timedelta(days=6)).isoformat(), kind="race"),
           Event("far", "雪山", (TODAY + dt.timedelta(days=10)).isoformat(), kind="baiyue", days=3)]
    alt = {"yu": {"max_m": 3952.0, "nights": [3402]}, "lo": {"max_m": 2800.0}, "far": {"max_m": 3886.0}}
    calls = []

    def alts():
        calls.append(1)
        return {}
    rows = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: alt.get(e.id), alts)
    assert [r["event_id"] for r in rows] == ["yu", "ng"] and len(calls) == 1
    r = rows[0]
    assert r["type"] == "altitude" and r["pick"] is None and r["id"] == f"altitude:yu:{r['start']}"
    assert rows[1]["id"] == f"altitude:ng:{rows[1]['start']}"
    # nothing high this close: the activities are never read
    calls.clear()
    assert SG.altitude_rows(evs[2:], TODAY.isoformat(), lambda e: alt.get(e.id), alts) == [] and not calls
    # 3,000 m+ in the last 3 months: no row at all
    assert SG.altitude_rows(evs, TODAY.isoformat(), lambda e: alt.get(e.id),
                            lambda: {TODAY - dt.timedelta(days=30): 3300.0}) == []


def test_closed_or_snoozed_stays_hidden_for_that_trip():
    start = TODAY + dt.timedelta(days=6)
    evs = [Event("yu", "玉山", start.isoformat(), kind="baiyue", days=2)]
    alt = {"max_m": 3952.0, "nights": [3402]}
    rows = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: alt, lambda: {})
    sid = rows[0]["id"]
    dis = SG.record({}, sid, "dismissed", dt.datetime(2026, 10, 5, 8))
    assert SG.visible(rows, SG.prune(dis, rows, TODAY.isoformat())) == []
    # the next days — also with another GPX (other height / nights) — still closed
    for k in (1, 3, 5):
        day = (TODAY + dt.timedelta(days=k)).isoformat()
        a2 = {"max_m": 3960.0, "nights": [2900]} if k == 3 else alt
        rows_k = SG.altitude_rows(evs, day, lambda e, a=a2: a, lambda: {})
        dis = SG.prune(dis, rows_k, day)
        assert rows_k and SG.visible(rows_k, dis) == [], k
    # a day the row is not computed (the activities failed): the close is kept until the trip starts
    dis = SG.prune(dis, [], (TODAY + dt.timedelta(days=2)).isoformat())
    assert sid in dis
    assert SG.prune(dis, [], (start + dt.timedelta(days=1)).isoformat()) == {}
    # the trip moved to another date: a new reminder
    moved = [Event("yu", "玉山", (start - dt.timedelta(days=2)).isoformat(), kind="baiyue", days=2)]
    rows_m = SG.altitude_rows(moved, TODAY.isoformat(), lambda e: alt, lambda: {})
    assert SG.visible(rows_m, SG.prune(dis, rows_m, TODAY.isoformat())) == rows_m
    # 稍後提醒 (SP-507) works on it like on every row
    sn = SG.record({}, sid, "snoozed", dt.datetime(2026, 10, 5, 8))
    assert SG.visible(rows, SG.prune(sn, rows, TODAY.isoformat()), now=dt.datetime(2026, 10, 5, 9)) == []
    # 稍後提醒 is a separate test below (SP-546 R1: back the day before the trip)
    # old-format closes (`altitude:<e>:<start>:<m>:<flags>`, `altitude_plan:…`) are not kept forever
    old = {f"altitude:yu:{start.isoformat()}:3952:first-n1": {"action": "dismissed", "at": "2026-10-01T08:00:00"},
           f"altitude_plan:yu:{start.isoformat()}": {"action": "dismissed", "at": "2026-10-01T08:00:00"}}
    assert SG.prune(old, rows, TODAY.isoformat()) == {}


def test_api_rows_from_the_plan_and_the_stored_gpx(store, monkeypatch):
    from backend.api import overview as OV
    from backend.api import plan_sessions as API
    from backend.engine.planning import Plan
    EG.save("yu", YUSHAN, "yushan.gpx")
    EG.set_splits("yu", [8.0])                                    # the first night: the camp, set by the user
    start = (TODAY + dt.timedelta(days=6)).isoformat()
    monkeypatch.setattr(Plan, "load", classmethod(lambda cls, path=None: Plan(events=[
        Event("yu", "玉山", start, kind="baiyue", days=2), Event("x", "沒有 GPX", start, kind="baiyue", days=2),
        Event("r", "沒有 GPX 的越野賽", start, kind="race")])))
    monkeypatch.setattr(OV, "_dataset", lambda: _ds([_act(TODAY - dt.timedelta(days=200), 3300)]))
    rows = API._altitude_suggestions(TODAY.isoformat())
    assert [r["event_id"] for r in rows] == ["yu", "x"]
    assert rows[0]["flags"] == ["n1"] and "第一晚睡在約" in rows[0]["help"]
    # climbed 3,300 m a month ago: nothing
    monkeypatch.setattr(OV, "_dataset", lambda: _ds([_act(TODAY - dt.timedelta(days=30), 3300)]))
    assert API._altitude_suggestions(TODAY.isoformat()) == []


def test_snooze_brings_it_back_the_day_before_the_trip():
    """SP-546 R1 (the owner, Q1): 稍後提醒 on an altitude reminder = the day before the trip, not 7 days later
    (inside the 7-day window that was the same as closing it)."""
    start = TODAY + dt.timedelta(days=6)
    evs = [Event("yu", "玉山", start.isoformat(), kind="baiyue", days=2)]
    alt = {"max_m": 3952.0, "nights": [3402]}
    rows_on = lambda day: SG.altitude_rows(evs, day.isoformat(), lambda e: alt, lambda: {})  # noqa: E731
    sid = rows_on(TODAY)[0]["id"]
    at = dt.datetime.combine(TODAY, dt.time(8))
    sn = SG.record({}, sid, "snoozed", at)
    eve = start - dt.timedelta(days=1)
    assert sn[sid]["until"] == eve.isoformat()
    for k in range(0, (eve - TODAY).days):          # hidden every day until the day before …
        day = TODAY + dt.timedelta(days=k)
        dis = SG.prune(sn, rows_on(day), day.isoformat())
        now = dt.datetime.combine(day, dt.time(20))
        assert rows_on(day) and SG.visible(rows_on(day), dis, now=now) == [], day
        assert SG.snoozed(rows_on(day), [], dis) == [{"id": sid, "type": "altitude", "until": eve.isoformat()}]
    rows = rows_on(eve)                             # … and back on it
    assert SG.visible(rows, SG.prune(sn, rows, eve.isoformat()), now=dt.datetime.combine(eve, dt.time(7))) == rows
    # snoozed on the day before itself: held until the trip starts (then it is not computed any more)
    sn2 = SG.record({}, sid, "snoozed", dt.datetime.combine(eve, dt.time(8)))
    assert sn2[sid]["until"] == start.isoformat()
    assert SG.visible(rows, sn2, now=dt.datetime.combine(eve, dt.time(21))) == []
    assert rows_on(start) == []
    # other rows keep SP-507's 7 days
    assert SG.record({}, "b2b:2026-10-05", "snoozed", at)["b2b:2026-10-05"]["until"] == \
        (TODAY + dt.timedelta(days=SG.SNOOZE_DAYS)).isoformat()
