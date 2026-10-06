"""
基線測試 (engine/baseline_test.py, SP-71): the fixed 12′ + 3′ CP test and the AeT test, suggested
in the floating box when there is no valid test on record and again every 6–8 weeks; a test that
was not all-out is no baseline; none before an A race, in a 恢復期 / 轉換期 / 回量期, a re-entry
block or with an injury / illness; 排入 stores the session, 不要 holds for the week.
Synthetic data only: hand-built inputs (test_plan_store.Env), in-memory DB.
"""
from datetime import date

import pytest

from backend.engine import baseline_test as BT
from backend.engine import plan_prefs as PP
from backend.engine import suggestions as SG
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env

TODAY = "2026-09-30"


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs(aet_test_protocol="ua40"))


def _test(day, title, state="done", protocol=None):
    return {"uid": day, "day": day, "kind": "test", "title": title, "state": state, "protocol": protocol}


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------

def test_latest_takes_the_review_and_the_plan_but_not_a_rejected_run():
    found = {"cp": {"date": "2026-09-01", "method": "2pt", "protocol": "standard", "ok": True},
             "aet": {"date": "2026-08-20", "ok": True}}
    t = BT.latest(found, [], TODAY)
    assert t == {"cp": {"last": "2026-09-01", "rejected": None}, "aet": {"last": "2026-08-20", "rejected": None}}
    # a done stored 12′ + 3′ session later than the review's test counts
    stored = [_test("2026-09-15", "CP 測試 12 分 + 3 分", protocol="standard"),
              _test("2026-09-20", "CP 測試 20 分全力", protocol="quick"),            # another method: not a baseline
              _test("2026-09-25", "AeT 測試 40 分", state="missed"),                  # not done
              _test("2026-10-02", "CP 測試 12 分 + 3 分", state="active", protocol="standard")]   # ahead
    t = BT.latest(found, stored, TODAY)
    assert t["cp"]["last"] == "2026-09-15" and t["aet"]["last"] == "2026-08-20"
    # the review rejected the last CP test (not all-out): that very day doesn't count from the plan either
    bad = {"cp": {"date": "2026-09-15", "method": "2pt", "protocol": "standard", "ok": False}}
    t = BT.latest(bad, stored[:1], TODAY)
    assert t["cp"] == {"last": None, "rejected": "2026-09-15"}
    # a 20′ test the review found is another method: neither a baseline nor a rejected one
    t = BT.latest({"cp": {"date": "2026-09-15", "method": "tt20", "protocol": "quick", "ok": True}}, [], TODAY)
    assert t["cp"] == {"last": None, "rejected": None}
    # an AeT test the review could not read
    assert BT.latest({"aet": {"date": "2026-09-10", "ok": False}}, [], TODAY)["aet"] == \
        {"last": None, "rejected": "2026-09-10"}


def test_due_first_then_every_six_weeks():
    none = {"cp": {"last": None}, "aet": {"last": None}}
    assert [(d["kind"], d["reason"]) for d in BT.due(TODAY, none)] == [("cp", "first"), ("aet", "first")]
    t = {"cp": {"last": "2026-08-19"}, "aet": {"last": "2026-08-20"}}            # 42 and 41 days ago
    (d,) = BT.due(TODAY, t)
    assert (d["kind"], d["reason"], d["weeks"], d["late"]) == ("cp", "repeat", 6, False)
    d = BT.due(TODAY, {"cp": {"last": "2026-08-05"}, "aet": {"last": "2026-09-29"}})[0]
    assert d["days"] == 56 and d["late"] is True
    assert BT.next_due({"cp": {"last": "2026-09-01"}, "aet": {"last": None}}) == {"cp": "2026-10-13", "aet": None}


@pytest.mark.parametrize("ctx, why", [
    ({"days_to_a": 7}, "race"), ({"days_to_a": 10}, "race"), ({"mode": "reentry"}, "reentry"),
    ({"phase": "transition"}, "phase"), ({"phase": "base", "mode": "rebuild"}, "phase"), ({"phase": "recovery"}, "phase"),
    ({"injuries": [{"category": "injury", "severity": "moderate"}]}, "injury"),
    ({"injuries": [{"category": "illness", "illness": "cold"}]}, "injury")])
def test_no_baseline_test_when_it_doesnt_fit(ctx, why):
    assert BT.blocked(ctx) == why
    assert BT.due(TODAY, {"cp": {"last": None}, "aet": {"last": None}}, ctx) == []


def test_still_suggested_with_a_mild_injury_or_a_race_far_away():
    ctx = {"days_to_a": 30, "phase": "base", "injuries": [{"category": "injury", "severity": "mild"}]}
    assert BT.blocked(ctx) is None and len(BT.due(TODAY, {"cp": {"last": None}, "aet": {"last": None}}, ctx)) == 2


def test_rows_say_why_and_what_to_keep_the_same():
    s = {"kind": "test", "title": "CP 測試 12 分 + 3 分", "minutes": 70, "protocol": "standard"}
    due = [{"kind": "cp", "reason": "repeat", "last": "2026-08-05", "days": 56, "weeks": 8, "late": True,
            "rejected": "2026-09-20"}]
    (r,) = SG.baseline_rows(due, "2026-09-28", lambda k: s, lambda k, rl: [{"day": "2026-10-01"}])
    assert r["id"] == "baseline:cp:2026-09-28" and r["type"] == "baseline" and r["pick"] == "day"
    assert r["title"] == "該重測了：CP 測試 12 分 + 3 分（70 分）"
    assert "8 週前（8/5）" in r["reason"] and "超過 8 週" in r["reason"] and "9/20 那次沒有算進來" in r["reason"]
    assert "不會自動排進課表" in r["help"] and "推估" in r["help"] and "同一種測法" in r["help"]
    assert r["options"][0]["day"] == "2026-10-01" and r["session"]["protocol"] == "standard"
    first = SG.baseline_rows([{"kind": "aet", "reason": "first", "last": None}], "2026-09-28",
                             lambda k: {**s, "title": "AeT 測試", "replaces_long": True}, lambda k, rl: [])
    assert first[0]["title"].startswith("建議做一次基線測試") and "起點" in first[0]["reason"]
    assert first[0]["replaces_long"] and "replaces_long" not in first[0]["session"]
    assert SG.baseline_rows(due, "2026-09-28", lambda k: None, lambda k, rl: []) == []   # nothing to schedule


# ---------------------------------------------------------------------------
# the floating box
# ---------------------------------------------------------------------------

def _box(e):
    return {s["id"]: s for s in e.c.get(f"{API}/suggestions").json()["suggestions"]}


def test_box_suggests_both_baseline_tests_and_stands_in_for_the_weekly_test(monkeypatch):
    with Env(monkeypatch) as e:
        e.inp["tests"] = {"cp": None, "aet": None}
        e.inp["cur"]["test_suggestions"] = [{"kind": "cp", "protocol": "quick", "title": "CP 測試", "minutes": 37,
                                             "reason": "門檻過期", "replaces_long": False,
                                             "session": {"kind": "test", "title": "CP 測試 20 分全力",
                                                         "minutes": 37, "protocol": "quick"}}]
        e.c.get(f"{API}/sessions")
        by = _box(e)
        assert "test:cp:2026-09-28" not in by                                   # one CP row, not two
        cp, aet = by["baseline:cp:2026-09-28"], by["baseline:aet:2026-09-28"]
        assert cp["session"]["protocol"] == "standard" and "12" in cp["title"]
        assert aet["session"]["kind"] == "test" and "40" in aet["title"]        # 課表偏好 UA 40
        assert cp["options"] and all(o["day"] != "2026-10-04" for o in cp["options"])   # not on the long run's day
        # the calendar's 排入測試 menu lists it too
        day = cp["options"][0]["day"]
        opts = e.c.get(f"{API}/test-options?day={day}").json()["options"]
        assert any(o["id"] == cp["id"] and o["ok"] for o in opts)
        # 排入: the 12′ + 3′ test is stored; the CP row is gone, a later CP retest isn't offered on top
        out = e.c.post(f"{API}/suggestions/accept", json={"id": cp["id"], "day": day}).json()
        s = out["sessions"][0]
        assert s["kind"] == "test" and s["day"] == day and s["protocol"] == "standard"
        by = _box(e)
        assert cp["id"] not in by and "test:cp:2026-09-28" not in by and aet["id"] in by
        # 不要 on the AeT test: gone for this week
        e.c.post(f"{API}/suggestions/dismiss", json={"id": aet["id"], "action": "declined"})
        assert aet["id"] not in _box(e)


def test_box_after_a_recent_valid_test_and_without_the_review(monkeypatch):
    with Env(monkeypatch) as e:
        e.inp["tests"] = {"cp": {"date": "2026-09-10", "method": "2pt", "protocol": "standard", "ok": True},
                          "aet": {"date": "2026-09-12", "ok": True}}
        e.c.get(f"{API}/sessions")
        assert not [k for k in _box(e) if k.startswith("baseline:")]
        e.inp.pop("tests")                                                      # no review numbers: nothing guessed
        assert not [k for k in _box(e) if k.startswith("baseline:")]


def test_box_quiet_before_an_a_race(monkeypatch):
    with Env(monkeypatch) as e:
        e.inp["tests"] = {"cp": None, "aet": None}
        e.inp["days_to_next_a"] = 6
        e.c.get(f"{API}/sessions")
        assert not [k for k in _box(e) if k.startswith("baseline:")]
