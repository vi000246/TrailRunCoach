"""
SP-359 交換課表: two sessions trade days in one operation (plan_store.swap, POST /sessions/swap) —
both become the user's own move (like a drag; reconcile never undoes it), done / past / same-day
sessions are refused, a failed second move leaves the first untouched, and the watch follows on
both days right away (the SP-358 sync). COROS is the scripted FakeHub; synthetic plan, no WKO5 data.
"""
from datetime import date

import pytest

from backend.engine import plan_store as PS
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import run
from backend.tests.test_plan_store import API, Env


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def _by_day(e):
    return {s["day"]: s for s in e.c.get(f"{API}/sessions").json()["sessions"]
            if s["state"] in ("active", "done") and s["kind"] != "strength"}


def _watch(e):
    """happenDay -> the pushed workout's name (「TRC <title> m/d」)."""
    return {x["happenDay"]: x["program"]["name"] for x in e.fake.entities}


def test_swap_in_the_week_trades_days_and_the_watch_follows(monkeypatch):
    with Env(monkeypatch) as e:
        by = _by_day(e)
        q, lng = by["2026-10-01"], by["2026-10-04"]
        assert e.c.post(f"{API}/push-coros?scope=week").status_code == 200
        assert "3×10" in _watch(e)[20261001] and "LSD" in _watch(e)[20261004]
        r = e.c.post(f"{API}/sessions/swap", json={"a": q["uid"], "b": lng["uid"]})
        assert r.status_code == 200, r.text
        body = r.json()
        got = {s["uid"]: s for s in body["sessions"]}
        assert got[q["uid"]]["day"] == "2026-10-04" and got[lng["uid"]]["day"] == "2026-10-01"
        assert all(s["edited"] for s in got.values())
        # the watch: the old entries are gone, each day has the other session now
        w = _watch(e)
        assert sorted(w) == [20261001, 20261002, 20261004]
        assert "LSD" in w[20261001] and "10/1" in w[20261001]
        assert "3×10" in w[20261004] and "10/4" in w[20261004]
        assert len(e.fake.live()) == 3
        assert body["coros"]["status"] == "ok"
        # the user's own move: a reconcile changes neither
        e.c.post(f"{API}/reconcile", json={})
        after = _by_day(e)
        assert after["2026-10-01"]["uid"] == lng["uid"] and after["2026-10-04"]["uid"] == q["uid"]


def test_swap_across_weeks_is_kept_by_reconcile(monkeypatch):
    with Env(monkeypatch) as e:
        by = _by_day(e)
        easy, nq = by["2026-10-02"], by["2026-10-06"]
        r = e.c.post(f"{API}/sessions/swap", json={"a": easy["uid"], "b": nq["uid"]})
        assert r.status_code == 200, r.text
        # like a drag into another week: the auto sessions become the user's own (tombstones keep
        # the generator from refilling the old days)
        assert {s["origin"] for s in r.json()["sessions"]} == {"custom"}
        e.c.post(f"{API}/reconcile", json={})
        after = _by_day(e)
        assert after["2026-10-02"]["uid"] == nq["uid"] and after["2026-10-06"]["uid"] == easy["uid"]
        assert [s["kind"] for s in e.c.get(f"{API}/sessions").json()["sessions"]
                if s["day"] == "2026-10-02" and s["state"] == "active" and s["kind"] != "strength"] == ["quality"]


def test_swap_refusals(monkeypatch):
    with Env(monkeypatch) as e:
        by = _by_day(e)
        done, q, lng = by["2026-09-29"], by["2026-10-01"], by["2026-10-04"]
        assert done["state"] == "done"
        bad = [{"a": done["uid"], "b": q["uid"]},          # done: not swappable (nor draggable)
               {"a": q["uid"], "b": q["uid"]},             # itself
               {"a": q["uid"], "b": "nope"},
               {"a": q["uid"]}]
        for b in bad:
            assert e.c.post(f"{API}/sessions/swap", json=b).status_code == 400, b
        assert _by_day(e)["2026-10-01"]["uid"] == q["uid"] and _by_day(e)["2026-10-04"]["uid"] == lng["uid"]


def test_swap_is_atomic(monkeypatch):
    with Env(monkeypatch) as e:
        by = _by_day(e)
        q, lng = by["2026-10-01"], by["2026-10-04"]
        # 10/1 is blocked: the second move (onto 10/1) is refused, so the first is undone too
        with pytest.raises(PS.PlanError):
            run(PS.swap(e.db, q["uid"], lng["uid"], "2026-09-30", blocked={"2026-10-01": "出遊"}))
        ss = {s["uid"]: s for s in run(PS.load(e.db))}
        assert ss[q["uid"]]["day"] == "2026-10-01" and ss[lng["uid"]]["day"] == "2026-10-04"
        assert not ss[q["uid"]]["edited"] and not ss[lng["uid"]]["edited"]
