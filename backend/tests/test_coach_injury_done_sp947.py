"""SP-947: the injury log is read only to a coach (SP-822) — through the suggestion box too. With 「改課表」 on,
a coach viewing a student could press 「好了」 on the box's 「右膝看起來好了？」 (injury_done) and so mark the
student's injury healed. Now that accept is 403 COACH_VIEW_READONLY (as every other injury write) while
viewing, the injury stays open, and the box's row tells the coach only the student can say so (owner_only,
no 「好了」 button). The student's own 「好了」 works as before.

Every person is a made-up test account (accounts_fixtures.many, SP-611) in sign-in mode (test_login_sp58's
app); the box's row is made up too (no real data, no foot log)."""
from __future__ import annotations

import pytest

from backend.tests.test_coach_injury_sp822 import INJ, SEE, _injury
from backend.tests.test_coach_view_sp791 import _code, _pair, _post, _view
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, started  # noqa: F401 — fixtures

SUG = "/api/v1/overview/plan/suggestions"


def _status(cl, eid):
    return next(e for e in cl.get(INJ).json()["injuries"] if e["id"] == eid)["status"]


@pytest.fixture
def healed_row(monkeypatch):
    """The box offers 「看起來好了？」 for the injury id put in `ids` (whoever asks: the student's data)."""
    from backend.api import plan_sessions as PSA
    ids: list = []

    async def rows(db, inp):
        return [{"id": f"injury_done:{i}", "type": "injury_done", "pick": "confirm", "injury_id": i,
                 "accept_label": "好了", "decline_label": "還沒", "date": "2026-10-20", "title": "右膝看起來好了？"}
                for i in ids]
    monkeypatch.setattr(PSA, "_all_suggestions", rows)
    return ids


@pytest.mark.parametrize("perms", [None, SEE], ids=["plan_on", "plan_and_injury_on"])
def test_a_viewing_coach_cannot_mark_the_injury_healed(app, healed_row, perms):
    """驗收 1: 「改課表」 on (the default), and with 「看傷病紀錄」 on too — the coach's 「好了」 is refused like
    any other injury write, the injury stays open, nothing goes into the student's log."""
    from backend.accounts import store
    coach, stu, _link = _pair(app, None if perms is None else {**perms, "plan": True})
    eid = _injury(stu, "學員的右膝")["id"]
    healed_row.append(eid)
    _view(coach, stu.user.id)
    r = _post(coach, f"{SUG}/accept", {"id": f"injury_done:{eid}"})
    assert r.status_code == 403 and _code(r) == "COACH_VIEW_READONLY", (r.status_code, r.text)
    assert r.json()["detail"]["message"] == "代看學員時只能看，不能改這裡"
    _post(coach, "/api/v1/coach/view/stop")
    assert _status(stu, eid) != "resolved"
    assert not [a for a in store.coach_audit_of(stu.user.id) if a.get("action") == "suggestion"]


def test_the_box_tells_the_coach_only_the_student_can_say_so(app, healed_row):
    """驗收 2: the coach's box row carries owner_only (the page shows it instead of 「好了」); the student's
    own row does not. (SP-948: the coach sees the row at all only with 「看傷病紀錄」 on — off, the box's injury
    rows are left out: test_coach_injury_words_sp948.)"""
    coach, stu, _link = _pair(app, {**SEE, "plan": True})
    eid = _injury(stu, "學員的右膝")["id"]
    healed_row.append(eid)
    mine = next(r for r in stu.get(SUG).json()["suggestions"] if r["type"] == "injury_done")
    assert "owner_only" not in mine
    _view(coach, stu.user.id)
    theirs = next(r for r in coach.get(SUG).json()["suggestions"] if r["type"] == "injury_done")
    assert theirs["owner_only"] == "只有學員本人能標成好了"


def test_the_student_still_marks_it_healed(app, healed_row):
    """驗收 3: the student's own 「好了」 resolves it as before (SP-273)."""
    _coach, stu, _link = _pair(app)
    eid = _injury(stu, "學員的右膝")["id"]
    healed_row.append(eid)
    r = _post(stu, f"{SUG}/accept", {"id": f"injury_done:{eid}"})
    assert r.status_code == 200, r.text
    assert r.json()["resolved"]["id"] == eid
    assert _status(stu, eid) == "resolved"
