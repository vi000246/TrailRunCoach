"""
SP-273 app 提議「好了」 (engine/injuries.done_check, engine/suggestions.injury_done_rows / visible,
api/plan_sessions accept / dismiss): an open injury looks healed when the last 7 days' running is ≥ 75 % of
the 4 weeks before the onset and the last 3 runs are all marked 沒痛 / 痠 over ≥ 14 days. Only proposed —
「好了」 resolves it (then the condition rules and the light are gone), 「還沒」 hides it for 7 days; never
automatic. Synthetic data and tmp SQLite only (docs/research/injury-graded-return.md §4.6, §6.1 point 8).
"""
from __future__ import annotations

import datetime as dt
from datetime import date

import pytest

from backend.engine import injuries as INJ
from backend.engine import suggestions as SG
from backend.tests.test_injuries import ev


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    INJ._memo.clear()
    yield
    INJ._memo.clear()


def run(day, minutes, pain=None, eid=None):
    return {"date": day, "key": day + "T07:00", "cat": "run", "minutes": float(minutes), "pain": pain, "score": None,
            "area": None, "injury_id": eid}


def marks(today: date, last3=((15, 0), (6, 1), (0, 0)), recent_min=(60.0, 96.0, 96.0)):
    """4 h a week in the 4 weeks before the onset (8 × 120 min); after it, three runs `last3` = (days before
    today, pain), with `recent_min` minutes — by default the last week = 96 + 96 = 192 min = 3.2 h."""
    onset = today - dt.timedelta(days=40)
    pre = [run((onset - dt.timedelta(days=3 + 3 * i)).isoformat(), 120) for i in range(8)]
    post = [run((today - dt.timedelta(days=k)).isoformat(), m, p) for (k, p), m in zip(last3, recent_min)]
    return onset, pre + post


T = date(2026, 9, 30)


def test_proposed_when_both_conditions_hold():
    onset, m = marks(T)
    e = ev(1, onset.isoformat(), side="right")
    c = INJ.done_check(e, m, T)
    assert c and c["pct"] == 80 and c["pre_h"] == 4.0 and c["recent_h"] == 3.2 and c["span"] == 15
    rows = SG.injury_done_rows([e], m, T.isoformat())
    r = rows[0]
    assert r["type"] == "injury_done" and r["pick"] == "confirm" and r["id"] == "injury_done:1"
    assert r["title"] == "右膝看起來好了？最近 3 次沒痛、跑量回到傷前 80%"
    assert r["accept_label"] == "好了" and r["decline_label"] == "還沒" and "這不是醫療診斷" in r["help"]


def test_not_proposed_with_a_painful_run_short_span_low_volume():
    onset, m = marks(T, last3=((15, 0), (6, 2), (0, 0)))
    assert INJ.done_check(ev(1, onset.isoformat()), m, T) is None                   # one 痛
    onset, m = marks(T, last3=((10, 0), (6, 1), (0, 0)))
    assert INJ.done_check(ev(1, onset.isoformat()), m, T) is None                   # only 10 days
    onset, m = marks(T, last3=((15, 0), (6, None), (0, 0)))
    assert INJ.done_check(ev(1, onset.isoformat()), m, T) is None                   # unmarked ≠ 沒痛
    onset, m = marks(T, recent_min=(60.0, 80.0, 80.0))
    assert INJ.done_check(ev(1, onset.isoformat()), m, T) is None                   # 2.7 h < 75 % of 4 h


def test_not_proposed_for_illness_resolved_red_or_no_pre_injury_running():
    onset, m = marks(T)
    assert INJ.done_check({**ev(1, onset.isoformat()), "category": "illness", "illness": "cold"}, m, T) is None
    assert INJ.done_check(ev(1, onset.isoformat(), status="resolved", resolved="2026-09-29"), m, T) is None
    # red (severity 重) and only 痠 runs since: still red, waiting for the walk check / a 沒痛 run
    onset_s, sore = marks(T, last3=((15, 1), (6, 1), (0, 1)))
    assert INJ.done_check(ev(1, onset_s.isoformat(), severity="severe"), sore, T) is None
    # owner's decision (SP-273, 2026-10-06): a run marked 沒痛 brings it straight back to green — proposed
    assert INJ.done_check(ev(1, onset.isoformat(), severity="severe"), m, T) is not None
    post_only = [x for x in m if x["date"] >= onset.isoformat()]
    assert INJ.done_check(ev(1, onset.isoformat()), post_only, T) is None


def test_not_yet_hides_it_for_seven_days():
    row = [{"id": "injury_done:1"}]
    dis = SG.record({}, "injury_done:1", "declined", dt.datetime(2026, 9, 30, 8, 0))
    assert SG.visible(row, dis, now=dt.datetime(2026, 10, 6, 23, 0)) == []
    assert SG.visible(row, dis, now=dt.datetime(2026, 10, 7, 8, 0)) == row
    # other kinds stay dismissed
    assert SG.visible([{"id": "zone:x"}], SG.record({}, "zone:x", "dismissed", dt.datetime(2026, 1, 1))) == []


def test_after_resolving_the_rules_and_the_light_are_gone():
    onset, m = marks(T)
    e = {**ev(1, onset.isoformat()), "condition": "pfp"}
    assert INJ.condition_rule([e], T) is not None and INJ.light([e], m, T) is not None
    done = {**e, "status": "resolved", "resolved_date": T.isoformat()}
    assert INJ.condition_rule([done], T) is None and INJ.light([done], m, T) is None
    assert SG.injury_done_rows([done], m, T.isoformat()) == []
    # the same area within 42 days later = a recurrence (unchanged rule)
    nxt = {**ev(2, (T + dt.timedelta(days=20)).isoformat()), "condition": "pfp"}
    assert INJ.recurrence(nxt, [{**done, "id": 1}])["recurrence_of"] == 1


def test_english():
    from backend.i18n import use_locale
    onset, m = marks(T)
    with use_locale("en"):
        r = SG.injury_done_rows([ev(1, onset.isoformat(), side="right")], m, T.isoformat())[0]
        assert "80%" in r["title"] and r["accept_label"] == "Healed" and r["decline_label"] == "Not yet"


# ---------------------------------------------------------------------------
# the suggestion box API: proposed, 還沒, 好了 (never automatic)
# ---------------------------------------------------------------------------

def test_box_api(monkeypatch):
    from sqlalchemy import select
    from backend.api import plan_sessions as PSA
    from backend.db.models import InjuryEvent
    from backend.tests.test_plan_store import API, Env, run as arun
    with Env(monkeypatch) as e:
        today = date.fromisoformat(PSA._today(e.inp))
        onset, m = marks(today)
        row = InjuryEvent(athlete_id=1, area="knee", side="right", kind="overuse", severity="moderate",
                          onset_date=onset.isoformat(), status="active", condition="pfp")
        e.db.add(row)
        arun(e.db.commit())
        evs = [{c: getattr(row, c) for c in INJ.EVENT_COLS}]
        monkeypatch.setattr(INJ, "load_events", lambda *a, **k: [dict(x) for x in evs])
        monkeypatch.setattr(INJ, "foot_log", lambda *a, **k: m)
        import backend.api.overview as OV
        monkeypatch.setattr(OV, "_dataset", lambda *a, **k: None)
        rows = e.c.get(f"{API}/suggestions").json()["suggestions"]
        sg = next(r for r in rows if r["type"] == "injury_done")
        assert sg["id"] == f"injury_done:{row.id}"
        arun(e.db.refresh(row))
        assert row.status == "active"                                                  # never automatic
        # 還沒 → hidden now
        assert e.c.post(f"{API}/suggestions/dismiss", json={"id": sg["id"], "action": "declined"}).status_code == 200
        assert not any(r["type"] == "injury_done" for r in e.c.get(f"{API}/suggestions").json()["suggestions"])
        # back after 7 days (the dismissal's time moved back), then 好了 resolves it today
        from backend.settings.repository import SettingsRepository
        dis = arun(SettingsRepository(e.db).get(SG.KEY))
        dis[sg["id"]]["at"] = (dt.datetime.now() - dt.timedelta(days=8)).isoformat(timespec="seconds")
        arun(SettingsRepository(e.db).set(SG.KEY, dis))
        arun(e.db.commit())
        assert any(r["type"] == "injury_done" for r in e.c.get(f"{API}/suggestions").json()["suggestions"])
        out = e.c.post(f"{API}/suggestions/accept", json={"id": sg["id"]})
        assert out.status_code == 200, out.text
        assert out.json()["resolved"]["date"] == today.isoformat() and "好了" in out.json()["message"]
        r2 = arun(e.db.execute(select(InjuryEvent).where(InjuryEvent.id == row.id))).scalar_one()
        assert r2.status == "resolved" and r2.resolved_date == today.isoformat()
