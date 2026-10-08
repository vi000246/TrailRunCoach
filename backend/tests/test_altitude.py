"""
高度適應提醒 (SP-100, engine/altitude.py + suggestions.altitude_rows): an event whose
GPX reaches ≥ 3,000 m gets an information row in the floating box 1–14 days before
its start (15–28 days: 安排適應週末, SP-258); the athlete's own altitude (activities) says acclimatised / pre-exposure /
first time; the GPX nights say a high first night or a big jump. Synthetic only.
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
    assert a["nights_source"] == "camp" and a["nights"][0] == pytest.approx(3450, abs=40)
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


def _row(alt, ex, days_to=6, name="玉山"):
    return AL.reminder({"id": "yu", "name": name, "start": TODAY + dt.timedelta(days=days_to), "days": 2},
                       alt, ex, TODAY)


NONE = {"nights": 0, "pre_days": 0, "recent": False, "ever": False}
# SP-258 follow-up (owner 2026-10-07): the 適應週末 row names no place
PLACES = ("松雪樓", "合歡山", "塔塔加", "大禹嶺", "排雲", "滑雪山莊")


def test_reminder_texts():
    alt = {"max_m": 3952.0, "nights": [3402]}
    r = _row(alt, NONE)
    assert r["title"] == "「玉山」最高約 3,952 m：出發前的高度適應"
    assert "2,750 m 以上睡至少 2 晚" in r["reason"] and "體能好壞不影響高山症風險" in r["reason"]
    for s in ("近 3 個月沒有到 3,000 m 以上", "第一次上 3,000 m", "第一晚睡在約 3,402 m", "CDC", "Schneider",
              "Shen 2024", "推估"):
        assert s in r["help"], s
    assert r["flags"] == ["first", "n1"]
    # acclimatised + pre-exposed
    r = _row(alt, {"nights": 2, "pre_days": 6, "recent": True, "ever": True})
    assert "已有部分適應" in r["reason"] and "有事前暴露" in r["help"] and "第一次" not in r["help"]
    assert r["flags"] == ["acc", "pre", "n1"]
    # one night, some days but under Schneider's > 4
    r = _row({"max_m": 3500.0}, {"nights": 1, "pre_days": 2, "recent": True, "ever": True})
    assert "1 晚" in r["reason"] and "還不算事前暴露" in r["help"] and r["flags"] == ["one", "recent"]
    # a night-to-night jump above 3,000 m
    r = _row({"max_m": 3800.0, "nights": [2900, 3600]}, NONE)
    assert "jump" in r["flags"] and "不超過 500 m" in r["help"]


def test_reminder_window_and_height():
    alt = {"max_m": 3952.0}
    assert _row(alt, NONE, days_to=29) is None and _row(alt, NONE, days_to=0) is None
    assert _row(alt, NONE, days_to=14) is not None and _row(alt, NONE, days_to=1) is not None
    assert _row({"max_m": 2900.0}, NONE) is None
    assert _row({"max_m": 2900.0}, NONE, days_to=20) is None          # under 3,000 m: never, also not early


def test_sp258_boundaries_28_15_14():
    """SP-258: 28 and 15 days out = 安排適應週末; 14 days out = the check as before (unchanged)."""
    alt = {"max_m": 3952.0}
    for n in (28, 15):
        r = _row(alt, None, days_to=n)                                 # the early row reads no activities
        assert r["flags"] == ["plan"] and "安排適應週末" in r["title"], n
        assert "2,750 m 以上睡 2 晚" in r["reason"] and "Beidleman 2018" in r["help"] and "推估" in r["help"]
        assert r["weekends"]
        # owner 2026-10-07: no place at all (松雪樓 gone too) — only the reminder
        for place in PLACES:
            assert place not in r["title"] + r["reason"] + r["help"], (n, place)
        assert "✕" in r["help"]                                        # it can be closed for good
    r14 = _row(alt, NONE, days_to=14)
    assert r14["title"] == "「玉山」最高約 3,952 m：出發前的高度適應" and r14["flags"] == ["first"]
    assert "安排適應週末" not in r14["help"] and "weekends" not in r14
    # the 14-day row is exactly the SP-100 one (no 行前一晚 line without a high first night)
    assert "行前一晚" not in r14["help"]


def test_sp258_weekends_inside_the_14_days_and_the_taper():
    # start Monday 11/2: CDC window 10/19–11/1 → the weekends 10/24–25 and 10/31–11/1 (Fri + Sat nights)
    start = dt.date(2026, 11, 2)
    ws = AL.weekends(start, TODAY, taper_days=7)
    assert [(w["sat"], w["sun"], w["taper"]) for w in ws] == [("2026-10-24", "2026-10-25", False),
                                                              ("2026-10-31", "2026-11-01", True)]
    r = AL.reminder({"id": "yu", "name": "玉山", "start": start, "days": 2, "taper_days": 7},
                    {"max_m": 3952.0}, None, TODAY)
    assert "可選：10/24–25、10/31–11/1（減量期：走輕鬆路線）" in r["reason"]
    assert "走輕鬆的路線" in r["help"]
    # a Saturday start: the only weekend whose Friday is inside the 14 days and that ends before the start
    ws = AL.weekends(dt.date(2026, 10, 24), TODAY, taper_days=7)
    assert [(w["sat"], w["taper"]) for w in ws] == [("2026-10-17", True)]
    # no taper given: nothing marked
    assert not any(w["taper"] for w in AL.weekends(start, TODAY))


def test_sp258_the_weekend_ending_on_a_sunday_departure_counts():
    """Owner 2026-10-07: a trip leaving on a Sunday — that weekend's Friday and Saturday nights are
    before it, so it counts (the Saturday start still has one weekend only)."""
    sun = dt.date(2026, 11, 1)                                          # a Sunday
    ws = AL.weekends(sun, TODAY)
    # CDC window 10/18–10/31: Fridays 10/23 and 10/30 → 10/24–25 and 10/31–11/1 (the departure day)
    assert [(w["sat"], w["sun"]) for w in ws] == [("2026-10-24", "2026-10-25"), ("2026-10-31", "2026-11-01")]
    r = AL.reminder({"id": "yu", "name": "玉山", "start": sun, "days": 2}, {"max_m": 3952.0}, None, TODAY)
    assert "可選：10/24–25、10/31–11/1" in r["reason"]
    # the boundary days: a Monday departure has the same two; a Saturday one only the weekend before
    assert [w["sat"] for w in AL.weekends(sun + dt.timedelta(days=1), TODAY)] == ["2026-10-24", "2026-10-31"]
    sat = sun - dt.timedelta(days=1)                                    # Saturday 10/31 departure
    assert [(w["sat"], w["sun"]) for w in AL.weekends(sat, TODAY)] == [("2026-10-24", "2026-10-25")]
    # a weekend already begun (today is its Saturday) is not offered any more
    assert [w["sat"] for w in AL.weekends(sun, dt.date(2026, 10, 24))] == ["2026-10-31"]


def test_sp258_eve_only_for_a_first_night_above_3000():
    hi = {"max_m": 3952.0, "nights": [3402]}                           # 玉山: 排雲山莊
    r = _row(hi, None, days_to=20)
    assert r["flags"] == ["plan", "eve"]
    # owner 2026-10-07: 行前一晚 only in the ? (help), never in the row's text; no place named
    assert "行前一晚" not in r["reason"] and "2,500 m" not in r["reason"]
    assert "行前一晚先住約 2,500 m" in r["help"] and "玉山國家公園" in r["help"]
    for place in PLACES:
        assert place not in r["reason"] + r["help"], place
    lo = _row({"max_m": 3600.0, "nights": [2800]}, None, days_to=20)   # first night under 3,000 m
    assert lo["flags"] == ["plan"] and "行前一晚" not in lo["reason"] + lo["help"]
    day = _row({"max_m": 3600.0}, None, days_to=20)                    # a day trip: no night
    assert "行前一晚" not in day["reason"] + day["help"]
    # 1–14 days: only the 說明 gets it; title, text, flags (= the id) as SP-100
    r = _row(hi, NONE, days_to=6)
    assert r["flags"] == ["first", "n1"] and "行前一晚" in r["help"] and "行前一晚" not in r["reason"]


def test_box_rows_info_only_and_the_id_follows_the_conditions():
    evs = [Event("yu", "玉山", (TODAY + dt.timedelta(days=6)).isoformat(), kind="baiyue", days=2),
           Event("lo", "合歡山", (TODAY + dt.timedelta(days=6)).isoformat(), kind="baiyue"),
           Event("far", "雪山", (TODAY + dt.timedelta(days=30)).isoformat(), kind="baiyue", days=3)]
    alt = {"yu": {"max_m": 3952.0, "nights": [3402]}, "lo": {"max_m": 3417.0 - 600}, "far": {"max_m": 3886.0}}
    calls = []

    def alts():
        calls.append(1)
        return {}
    rows = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: alt.get(e.id), alts)
    assert [r["event_id"] for r in rows] == ["yu"] and len(calls) == 1
    r = rows[0]
    assert r["type"] == "altitude" and r["pick"] is None and r["id"] == f"altitude:yu:{r['start']}:3952:first-n1"
    # dismissed: hidden; a change of conditions (e.g. two nights up high) is a new id → shows again
    dis = SG.record({}, r["id"], "dismissed", dt.datetime(2026, 10, 5, 8))
    assert SG.visible(rows, dis) == []
    hi = {TODAY - dt.timedelta(days=3): 2900.0, TODAY - dt.timedelta(days=2): 2950.0,
          TODAY - dt.timedelta(days=1): 2800.0}
    rows2 = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: alt.get(e.id), lambda: hi)
    assert rows2[0]["id"] != r["id"] and SG.visible(rows2, SG.prune(dis, rows2, TODAY.isoformat())) == rows2
    # no event needing it: the activities are never read
    calls.clear()
    assert SG.altitude_rows(evs[1:], TODAY.isoformat(), lambda e: alt.get(e.id), alts) == [] and not calls


def test_box_row_15_to_28_days_out_reads_no_activities_and_has_its_own_id():
    evs = [Event("yu", "玉山", (TODAY + dt.timedelta(days=20)).isoformat(), kind="baiyue", days=2)]
    calls = []

    def alts():
        calls.append(1)
        return {}
    rows = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: {"max_m": 3952.0, "nights": [3402]}, alts)
    assert len(rows) == 1 and not calls
    r = rows[0]
    assert r["id"] == f"altitude_plan:yu:{r['start']}" and r["src"].startswith(AL.SRC_PLAN) and "玉山國家公園" in r["src"]
    # a 2-day 百岳 A event tapers 7 days (planning.taper_days): its last weekend is marked
    # (the trip leaves on Sunday 10/25: that weekend counts too, owner 2026-10-07)
    assert [(w["sat"], w["taper"]) for w in r["weekends"]] == [("2026-10-17", True), ("2026-10-24", True)]
    # the 14-day row of the same event has another id: dismissing the early one doesn't hide it
    dis = SG.record({}, r["id"], "dismissed", dt.datetime(2026, 10, 5, 8))
    later = (TODAY + dt.timedelta(days=8)).isoformat()
    rows14 = SG.altitude_rows(evs, later, lambda e: {"max_m": 3952.0, "nights": [3402]}, lambda: {})
    assert rows14 and not rows14[0]["id"].split(":")[-1].startswith("plan")
    assert SG.visible(rows14, SG.prune(dis, rows14, later)) == rows14


def test_api_rows_from_the_plan_and_the_stored_gpx(store, monkeypatch):
    from backend.api import overview as OV
    from backend.api import plan_sessions as API
    from backend.engine.planning import Plan
    EG.save("yu", YUSHAN, "yushan.gpx")
    start = (TODAY + dt.timedelta(days=6)).isoformat()
    monkeypatch.setattr(Plan, "load", classmethod(lambda cls, path=None: Plan(events=[
        Event("yu", "玉山", start, kind="baiyue", days=2), Event("x", "沒有 GPX", start, kind="baiyue", days=2)])))
    monkeypatch.setattr(OV, "_dataset", lambda: _ds([_act(TODAY - dt.timedelta(days=30), 3300)]))
    rows = API._altitude_suggestions(TODAY.isoformat())
    assert [r["event_id"] for r in rows] == ["yu"]
    assert rows[0]["flags"] == ["recent", "n1"] and "第一晚睡在約" in rows[0]["help"]


def test_sp258_plan_row_once_closed_never_comes_back_for_that_trip():
    """Owner 2026-10-07: the 適應週末 reminder can be closed (✕); once closed it does not show again
    for that trip — not the next day, not when its GPX / first night changes, not after a day the
    row was not computed. The 14-day check is another row and still shows."""
    start = TODAY + dt.timedelta(days=20)
    evs = [Event("yu", "玉山", start.isoformat(), kind="baiyue", days=2)]
    alt = {"max_m": 3952.0, "nights": [3402]}
    rows = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: alt, lambda: {})
    r = rows[0]
    assert r["id"] == f"altitude_plan:yu:{start.isoformat()}"
    dis = SG.record({}, r["id"], "dismissed", dt.datetime(2026, 10, 5, 8))
    assert SG.visible(rows, SG.prune(dis, rows, TODAY.isoformat())) == []
    for k in (1, 5, 13):                                                # days 19, 15, 7…: every later day
        day = (TODAY + dt.timedelta(days=k)).isoformat()
        alt2 = {"max_m": 3960.0, "nights": [2900]} if k == 5 else alt  # a new GPX: other height, no 行前一晚
        rows_k = SG.altitude_rows(evs, day, lambda e, a=alt2: a, lambda: {})
        dis = SG.prune(dis, rows_k, day)
        assert not [x for x in SG.visible(rows_k, dis) if x["id"].startswith("altitude_plan:")], k
    # a day the row is not computed at all (GPX removed): the close is kept while the trip is ahead
    day = (TODAY + dt.timedelta(days=2)).isoformat()
    dis = SG.prune(dis, [], day)
    assert f"altitude_plan:yu:{start.isoformat()}" in dis
    rows_back = SG.altitude_rows(evs, day, lambda e: alt, lambda: {})
    assert SG.visible(rows_back, dis) == []
    # the 14-day check of the same trip is another row: shown
    day14 = (start - dt.timedelta(days=10)).isoformat()
    rows14 = SG.altitude_rows(evs, day14, lambda e: alt, lambda: {})
    assert rows14 and SG.visible(rows14, SG.prune(dis, rows14, day14)) == rows14
    # the trip is over: the close is dropped
    assert SG.prune(dis, [], (start + dt.timedelta(days=1)).isoformat()) == {}
    # another trip (or the same one moved to another date) is a new reminder
    moved = [Event("yu", "玉山", (start + dt.timedelta(days=3)).isoformat(), kind="baiyue", days=2)]
    rows_m = SG.altitude_rows(moved, TODAY.isoformat(), lambda e: alt, lambda: {})
    assert SG.visible(rows_m, SG.prune(dis, rows_m, TODAY.isoformat())) == rows_m


def test_sp258_english_plan_row_has_no_place():
    from backend.i18n import use_locale
    with use_locale("en"):
        r = _row({"max_m": 3952.0, "nights": [3402]}, None, days_to=20)
    txt = r["title"] + r["reason"] + r["help"]
    assert "acclimatisation weekend" in r["title"] and "2,500 m" in r["help"] and "2,500 m" not in r["reason"]
    for place in ("Songxue", "Hehuan", "Tataka", "Dayuling"):
        assert place not in txt, place
    assert not any("一" <= ch <= "鿿" for ch in r["reason"])
