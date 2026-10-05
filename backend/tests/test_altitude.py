"""
高度適應提醒 (SP-100, engine/altitude.py + suggestions.altitude_rows): an event whose
GPX reaches ≥ 3,000 m gets an information row in the floating box 1–14 days before
its start; the athlete's own altitude (activities) says acclimatised / pre-exposure /
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
    assert _row(alt, NONE, days_to=15) is None and _row(alt, NONE, days_to=0) is None
    assert _row(alt, NONE, days_to=14) is not None and _row(alt, NONE, days_to=1) is not None
    assert _row({"max_m": 2900.0}, NONE) is None


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
