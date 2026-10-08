"""
SP-358: a session moved on the 課表 page (dragged, or pushed off a 休息日 / 不排課日期) or deleted
left its old copy on the watch's calendar. The store was right; nothing took the pushed copy off:
an edit never pushed, and a push of a range never touched a pushed session that had moved out of
that range. Now the user's change syncs the watch right away (api/plan_sessions._sync_watch →
plan_auto.push_window), a range push also fixes the copies that sit in the range
(plan_sessions.on_watch). Review follow-ups: an edit syncs only the sessions it touched, and a
delete COROS accepts but still lists is a reminder (check_days), not a failure
(coros_workouts._remove_remote). COROS is the scripted FakeHub; no WKO5 data.
"""
import json
from datetime import date

import pytest
from sqlalchemy import select

from backend.db.models import PlanChangeLog
from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.settings.repository import SettingsRepository
from backend.sync import coros_workouts as CW
from backend.tests.test_blackouts import BEnv
from backend.tests.test_coros_workouts import FakeHub, run
from backend.tests.test_plan_store import API, Env


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def _days(fake):
    return sorted(e["happenDay"] for e in fake.entities)


def _setup(e):
    ss = e.c.get(f"{API}/sessions").json()["sessions"]
    r = e.c.post(f"{API}/push-coros?scope=week")
    assert r.status_code == 200, r.text
    assert _days(e.fake) == [20261001, 20261002, 20261004]
    return {s["day"]: s for s in ss if s["state"] == "active" and s["kind"] != "strength"}


def _auto_push(e, on):
    async def go():
        await SettingsRepository(e.db).set("plan.auto.push", on)
        await e.db.commit()
    run(go())


def _log(e):
    return run(e.db.execute(select(PlanChangeLog).order_by(PlanChangeLog.id))).scalars().all()


def test_drag_moves_the_watch_copy(monkeypatch):
    with Env(monkeypatch) as e:
        by = _setup(e)
        easy = by["2026-10-02"]
        r = e.c.patch(f"{API}/sessions/{easy['uid']}", json={"day": "2026-10-03"})
        assert r.status_code == 200, r.text
        assert _days(e.fake) == [20261001, 20261003, 20261004]          # off Fri, on Sat
        assert r.json()["coros"]["status"] == "ok"
        assert len(e.fake.live()) == 3                                  # the old program is deleted too


def test_drag_out_of_the_window_still_moves_the_watch_copy(monkeypatch):
    with Env(monkeypatch) as e:
        by = _setup(e)
        r = e.c.patch(f"{API}/sessions/{by['2026-10-04']['uid']}", json={"day": "2026-10-09"})
        assert r.status_code == 200, r.text
        # 10/9 is past the 7-day window (9/30–10/6), but the session was on the watch: it follows
        assert _days(e.fake) == [20261001, 20261002, 20261009]


def test_auto_push_off_still_fixes_the_copy_on_the_watch_but_pushes_nothing_new(monkeypatch):
    with Env(monkeypatch) as e:
        by = _setup(e)
        _auto_push(e, False)
        r = e.c.patch(f"{API}/sessions/{by['2026-10-02']['uid']}", json={"day": "2026-10-03"})
        assert r.status_code == 200
        assert _days(e.fake) == [20261001, 20261003, 20261004]
        # a session that was never pushed: no COROS call at all
        n = len(e.fake.calls)
        nxt = next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["day"] == "2026-10-06")
        r = e.c.patch(f"{API}/sessions/{nxt['uid']}", json={"day": "2026-10-05"})
        assert r.status_code == 200 and len(e.fake.calls) == n
        assert r.json()["coros"]["status"] == "unchanged"


def test_range_push_fixes_a_copy_whose_session_moved_out_of_the_range(monkeypatch):
    # the store was changed while the watch wasn't synced (an older build / an expired login):
    # the week push must still take the old copy off 10/4
    with Env(monkeypatch) as e:
        by = _setup(e)
        run(PS.edit(e.db, by["2026-10-04"]["uid"], {"day": "2026-10-09"}, "2026-09-30"))
        r = e.c.post(f"{API}/push-coros?scope=week")
        assert r.status_code == 200, r.text
        assert _days(e.fake) == [20261001, 20261002, 20261009]


def test_auto_run_fixes_a_copy_whose_session_moved_out_of_the_window(monkeypatch):
    with Env(monkeypatch) as e:
        by = _setup(e)
        run(PS.edit(e.db, by["2026-10-04"]["uid"], {"day": "2026-10-09"}, "2026-09-30"))
        new = run(PS.load(e.db))
        res = run(PA.push_window(e.db, new, e.inp, "2026-09-30", 7))
        assert res["status"] == "ok", res
        # (10/6 is next week's quality: in the window, pushed by the same run)
        assert _days(e.fake) == [20261001, 20261002, 20261006, 20261009]


def test_rest_day_takes_the_session_off_the_watch(monkeypatch):
    with BEnv(monkeypatch) as e:
        _setup(e)
        r = e.c.post(f"{API}/rest-days", json={"day": "2026-10-02"})
        assert r.status_code == 200, r.text
        e.stored_bl = r.json()["blackouts"]
        assert 20261002 not in _days(e.fake)
        assert r.json()["coros"]["status"] == "ok"


def test_blackout_takes_the_sessions_off_the_watch(monkeypatch):
    with BEnv(monkeypatch) as e:
        _setup(e)
        assert e.c.post(f"{API}/push-coros?scope=phase").status_code == 200
        q = next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["day"] == "2026-10-06")
        assert 20261006 in _days(e.fake)
        e.c.patch(f"{API}/sessions/{q['uid']}", json={"minutes": 50})        # the user's own, kept as a conflict
        bl = [{"start": "2026-10-04", "end": "2026-10-06", "label": "出遊"}]
        r = e.c.put(f"{API}/blackouts", json={"blackouts": bl})
        assert r.status_code == 200, r.text
        e.stored_bl = r.json()["blackouts"]
        # BEnv's generator moves the long run to 10/3; the edited 10/6 session stays in the
        # app (a conflict for the user to decide) but is off the watch
        days = _days(e.fake)
        assert 20261003 in days and not set(days) & {20261004, 20261005, 20261006}, days
        assert r.json()["coros"]["status"] == "ok"


def test_delete_takes_the_copy_off_right_away(monkeypatch):
    with Env(monkeypatch) as e:
        by = _setup(e)
        r = e.c.delete(f"{API}/sessions/{by['2026-10-02']['uid']}")
        assert r.status_code == 200, r.text
        assert _days(e.fake) == [20261001, 20261004]
        assert r.json()["coros"]["status"] == "ok"


class DeafHub(FakeHub):
    """COROS answers OK to the calendar delete but keeps the entry."""

    def __call__(self, req):
        if req.url.path == "/training/schedule/update" and req.content:
            body = json.loads(req.content)
            if any(v.get("status") == 3 for v in body.get("versionObjects") or []):
                self.calls.append((req.method, req.url.path))
                return self.ok()
        return super().__call__(req)


def test_a_delete_coros_ignores_is_a_reminder_not_a_failure(monkeypatch):
    # owner (SP-358 review): until a real drag shows whether COROS lags, an entry still listed
    # after the delete is a reminder to check the COROS app — not failed, not retried, logged
    env = Env(monkeypatch)
    env.fake = DeafHub()
    with env as e:
        by = _setup(e)
        uid = by["2026-10-02"]["uid"]
        n_del = lambda: sum(1 for m, p in e.fake.calls if p == "/training/schedule/update")     # noqa: E731
        before = n_del()
        r = e.c.patch(f"{API}/sessions/{uid}", json={"day": "2026-10-03"})
        assert r.status_code == 200
        c = r.json()["coros"]
        assert c["status"] == "ok" and c["check_days"] == ["2026-10-02"] and not c.get("error")
        # one delete + one schedule of the new day: no retry of the delete
        assert n_del() - before == 2
        assert not [x for x in _log(e) if x.status == "failed"]
        s = next(x for x in e.c.get(f"{API}/sessions").json()["sessions"] if x["uid"] == uid)
        assert s["coros"]["status"] == "pushed" and s["coros"]["pushed_day"] == "2026-10-03"


def test_a_failed_delete_of_the_touched_session_is_reported(monkeypatch):
    with Env(monkeypatch) as e:
        by = _setup(e)
        e.fake.fail["/training/schedule/update"] = {"result": "5001", "message": "boom"}
        r = e.c.patch(f"{API}/sessions/{by['2026-10-02']['uid']}", json={"day": "2026-10-03"})
        assert r.status_code == 200
        c = r.json()["coros"]
        assert c["status"] == "partial" and "boom" in c["error"]
        rows = [x for x in _log(e) if x.status == "failed"]
        assert len(rows) == 1 and "手錶" in rows[0].summary


def test_an_edit_syncs_only_what_it_touched(monkeypatch):
    # owner (SP-358 review): another stale copy (here a session deleted while the watch couldn't
    # follow) is left to the automatic run / the manual push — untouched, and no failure shown
    with Env(monkeypatch) as e:
        by = _setup(e)
        _auto_push(e, False)
        run(PS.delete(e.db, by["2026-10-02"]["uid"], today="2026-09-30"))      # stale, unrelated
        live = len(e.fake.live())
        r = e.c.patch(f"{API}/sessions/{by['2026-10-04']['uid']}", json={"day": "2026-10-03"})
        assert r.status_code == 200
        assert r.json()["coros"]["status"] == "ok" and r.json()["coros"]["removed"] == 0
        assert _days(e.fake) == [20261001, 20261002, 20261003]                # 10/2 still there
        assert len(e.fake.live()) == live                                       # its program too
        assert by["2026-10-02"]["uid"] in {x.session_key for x in e.pushed()}
        assert not [x for x in _log(e) if x.status == "failed"]
        # the next push of the week cleans it up
        e.c.post(f"{API}/push-coros?scope=week")
        assert _days(e.fake) == [20261001, 20261003]
