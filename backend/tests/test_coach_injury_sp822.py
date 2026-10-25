"""SP-822: a student may let their coach see the injury log. The switch is one more of the link's
(coaching.PERMS "injury"), off by default and off again for a coach who comes back after the link ended;
it opens reads only (the injury log and a run's pain; every injury write stays COACH_VIEW_READONLY), for
that link only — another coach, another student, the same coach's other students are not touched.
While it is off the activity answers carry no pain mark either (viewing.hides_injury).

Every person is a made-up test account (accounts_fixtures.many, SP-611) in sign-in mode (test_login_sp58's
app): nothing here reads real data."""
from __future__ import annotations

import pytest

from backend.tests import accounts_fixtures as AF
from backend.tests.test_coach_view_sp791 import (  # noqa: F401 — fixtures, helpers and the route lists
    BLOCKED_READS, HTML, NO_SESSION, OFF, READS_OPEN, WRITES_ANY, WRITES_PLAN, WRITES_TRAINING, _code, _fresh,
    _in, _pair, _post, _routes, _view)
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, started  # noqa: F401 — fixtures

INJ = "/api/v1/app/injuries"
# what the switch opens (written out: widening viewing.READ_PERM without changing this fails the walk)
INJURY_READS = f"""
{INJ} {INJ}/page {INJ}/meta {INJ}/analysis {INJ}/timeline {INJ}/x/days /api/v1/app/workouts/x/pain
""".split()
SEE = {**OFF, "injury": True}


def _injury(cl, note):
    r = cl.post(INJ, json={"area": "knee", "side": "right", "severity": "mild", "onset_date": "2026-10-01",
                           "note": note}, headers=cl.tok)
    assert r.status_code == 200, r.text
    return r.json()


def _notes(cl):
    r = cl.get(INJ)
    assert r.status_code == 200, (r.status_code, r.text)
    return [e.get("note") for e in r.json()["injuries"]]


def _switch(stu, link_id, on: bool):
    r = stu.put(f"/api/v1/account/coach/{link_id}/perms", json={"injury": on}, headers=stu.tok)
    assert r.status_code == 200, r.text
    return r.json()


def _blocked(r):
    return r.status_code == 403 and _code(r) == "COACH_VIEW_BLOCKED"


def _closed_to(coach):
    """Every injury read refused to `coach` (viewing), the page back to the overview."""
    bad = [p for p in INJURY_READS if not _blocked(coach.get(p))]
    page = coach.get(f"{INJ}/page", headers=HTML)
    return bad == [] and page.status_code == 302 \
        and page.headers["location"] == "/api/v1/overview/page?coach_view=blocked"


# ---------------------------------------------------------------------------- the acceptance criteria

def test_off_by_default_the_coach_reads_no_injury(app):
    """驗收 1: a coach the student accepted (the switches never set) reads nothing of the injury log or a
    run's pain while viewing; the session says the switch is off (the shell keeps 傷病紀錄 out of the menu);
    the student reads their own as always and sees the switch, off, with its words."""
    coach, stu, _link = _pair(app)
    _injury(stu, "學員的右膝")
    _view(coach, stu.user.id)
    assert _closed_to(coach)
    assert coach.get("/api/v1/session").json()["viewing"]["perms"]["injury"] is False
    assert _notes(stu) == ["學員的右膝"]
    mine = stu.get("/api/v1/account/coach").json()
    assert mine["coaches"][0]["perms"]["injury"] is False
    assert mine["perm_labels"]["injury"] == "看傷病紀錄與每次活動的疼痛（只能看）"


def test_switched_on_the_coach_reads_it_and_changes_nothing(app):
    """驗收 2: the student turns 「看傷病紀錄」 on — from the coach's next request the injury log reads as
    the student reads it (theirs, not the coach's own), the page opens; every write of the injury log is
    still refused (read only) and the log is as it was; the change is a consents row of the student."""
    from backend.accounts import store
    coach, stu, link = _pair(app)
    _injury(coach, "教練自己的腳踝")
    _injury(stu, "學員的右膝")
    eid = stu.get(INJ).json()["injuries"][0]["id"]
    _view(coach, stu.user.id)
    assert _blocked(coach.get(INJ))
    after = _switch(stu, link["id"], True)
    assert after["coaches"][0]["perms"] == {"plan": True, "training": True, "injury": True}   # the rest as they were
    assert _notes(coach) == ["學員的右膝"]
    for p in INJURY_READS:
        q = p.replace("/x/", f"/{eid}/") if p.startswith(INJ) else p.replace("/x/", "/0/")
        assert coach.get(q).status_code == stu.get(q).status_code, q          # what the student reads
    assert coach.get(f"{INJ}/page", headers=HTML).status_code == 200
    s = coach.get("/api/v1/session").json()
    assert s["viewing"]["perms"]["injury"] is True
    assert "injury.write" not in s["caps"]                                    # opens no write
    for m, p, body in (("POST", INJ, {"area": "foot", "onset_date": "2026-10-02"}),
                       ("PATCH", f"{INJ}/{eid}", {"note": "教練改的"}), ("POST", f"{INJ}/{eid}/resolve", {}),
                       ("POST", f"{INJ}/{eid}/walkrun", {}), ("DELETE", f"{INJ}/{eid}", None),
                       ("PUT", f"{INJ}/settings", {"reentry_step_up": True}), ("POST", f"{INJ}/areas", {"label": "x"}),
                       ("DELETE", f"{INJ}/areas/x", None), ("PATCH", "/api/v1/app/workouts/0/activity", {"pain": 2}),
                       ("PATCH", "/api/v1/app/activities", {"items": [{"key": "x"}], "pain": 2})):
        r = coach.request(m, p, json=body, headers=coach.tok)
        assert r.status_code == 403 and _code(r) == "COACH_VIEW_READONLY", (m, p, r.status_code, _code(r))
    _post(coach, "/api/v1/coach/view/stop")
    assert _notes(stu) == ["學員的右膝"] and _notes(coach) == ["教練自己的腳踝"]
    rows = [c for c in store.consents(stu.user.id) if c.get("action") == "coach_perms"]
    assert len(rows) == 1


def test_the_walk_with_the_injury_switch_on(app):
    """The SP-791 read walk with only 「看傷病紀錄」 on: every GET route — READS_OPEN and INJURY_READS pass,
    the rest (the other BLOCKED_READS among them) 403 COACH_VIEW_BLOCKED."""
    coach, stu, _link = _pair(app, SEE)
    _view(coach, stu.user.id)
    gets = [p for m, p in _routes(app) if m == "GET" and (m, p) not in NO_SESSION]
    assert set(INJURY_READS) <= set(gets), sorted(set(INJURY_READS) - set(gets))
    opened = set(READS_OPEN) | set(INJURY_READS)
    bad = []
    for p in gets + [x for x in BLOCKED_READS if x not in gets]:
        r = coach.get(p)
        code = _code(r) if r.headers.get("content-type", "").startswith("application/json") else None
        if p in opened:
            if r.status_code == 403 and str(code).startswith("COACH_"):
                bad.append((p, r.status_code, code))
        elif not (r.status_code == 403 and code == "COACH_VIEW_BLOCKED"):
            bad.append((p, r.status_code, code))
    assert bad == []


def test_the_injury_switch_opens_no_write(app):
    """The SP-791 write walk with only 「看傷病紀錄」 on: as with nothing allowed (its own walk: the reads'
    one would run into the rate limit)."""
    coach, stu, _link = _pair(app, SEE)
    _view(coach, stu.user.id)
    bad = []
    for m, p in _routes(app):
        if m == "GET" or (m, p) in NO_SESSION or (m, p) in {("POST", "/api/v1/account/logout"),
                                                             ("POST", "/api/v1/coach/view/stop")}:
            continue
        r = coach.request(m, p, json={}, headers=coach.tok)
        code = _code(r)
        if (m, p) in WRITES_ANY:
            if r.status_code == 403 and str(code).startswith("COACH_"):
                bad.append((m, p, r.status_code, code))
        elif (m, p) in WRITES_PLAN | WRITES_TRAINING:
            if not (r.status_code == 403 and code == "COACH_PERM_OFF"):
                bad.append((m, p, r.status_code, code))
        elif not (r.status_code == 403 and code == "COACH_VIEW_READONLY"):
            bad.append((m, p, r.status_code, code))
    assert bad == []


@pytest.mark.parametrize("how", ["switched_off", "student_withdraws", "coach_lets_go"])
def test_switching_off_or_ending_the_link_closes_it_again(app, how):
    """驗收 3: the switch off — the coach's next request reads nothing again; the link ended — the view
    ends, and the same coach taken back later (a new link) finds the switch off."""
    from backend.accounts import store
    coach, stu, link = _pair(app, SEE)
    _injury(stu, "學員的右膝")
    _view(coach, stu.user.id)
    assert _notes(coach) == ["學員的右膝"]
    if how == "switched_off":
        assert _switch(stu, link["id"], False)["coaches"][0]["perms"]["injury"] is False
        assert _closed_to(coach)
        return
    if how == "student_withdraws":
        assert _post(stu, f"/api/v1/account/coach/{link['id']}/end").status_code == 200
    else:
        store.coach_end(link["id"], "coach", coach.user.id)
    r = coach.get(INJ)
    assert r.status_code == 403 and _code(r) == "COACH_VIEW_ENDED" and "學員的右膝" not in r.text
    _post(coach, "/api/v1/coach/view/stop")
    again = store.coach_accept_invite(coach.user.id, stu.user.id, 1, 4)        # taken back: a new link
    assert again["id"] != link["id"]
    assert stu.get("/api/v1/account/coach").json()["coaches"][0]["perms"]["injury"] is False
    _view(coach, stu.user.id)
    assert _closed_to(coach)


def test_one_links_switch_only(app):
    """驗收 4: student A lets coach A see it; coach A's other student and coach B's student are untouched —
    the same coach reads nothing of the student who did not allow it, the other coach nothing of theirs,
    and neither reads A's through a student of their own."""
    from backend.accounts import coaching, store
    coach_a, stu_a, link_a = _pair(app, SEE)
    _injury(stu_a, "A 的右膝")
    other_u, = AF.many(1)
    link_o = store.coach_accept_invite(coach_a.user.id, other_u.id, 1, 4)    # coach A's second student
    other = _in(app, other_u)
    _injury(other, "另一位學員的腳踝")
    coach_b, stu_b, link_b = _pair(app)
    _injury(stu_b, "B 的髖")
    assert coaching.perms_of(store.coach_link(link_o["id"]))["injury"] is False
    assert coaching.perms_of(store.coach_link(link_b["id"]))["injury"] is False
    _view(coach_a, stu_a.user.id)
    assert _notes(coach_a) == ["A 的右膝"]
    _post(coach_a, "/api/v1/coach/view/stop")
    _view(coach_a, other_u.id)
    assert _closed_to(coach_a)
    _post(coach_a, "/api/v1/coach/view/stop")
    _view(coach_b, stu_b.user.id)
    assert _closed_to(coach_b)
    _post(coach_b, "/api/v1/coach/view/stop")
    no = _post(coach_b, f"/api/v1/coach/view/{stu_a.user.id}")                # not their student at all
    assert no.status_code == 403 and _code(no) == "NOT_YOUR_STUDENT"
    assert coach_b.get(INJ).status_code == 200 and _notes(coach_b) == []      # their own (empty) log
    assert not coaching.allows(coach_b.user, stu_a.user.id, "injury")
    assert coaching.allows(coach_a.user, stu_a.user.id, "injury")
    assert not coaching.allows(coach_a.user, other_u.id, "injury")


def test_the_pain_marks_of_activities_follow_the_switch(app):
    """While viewing with the switch off, the activity answers leave out the pain mark (the activity list's
    _pain_part, an activity card's pain_state and its tag fields), the way the demo mode does; on, or the
    student themselves, they are there."""
    from backend.accounts import viewing as VW
    from backend.api import wko5views as WV
    u = {"pain": 2, "pain_area": "knee", "injury_id": 7, "pain_score": 5}
    assert WV._pain_part(u)["pain"] == 2 and not VW.hides_injury()           # nobody viewed
    for perms, hidden in ((OFF, True), ({**OFF, "plan": True, "training": True}, True), (SEE, False)):
        tok = VW.set_view(VW.View(coach=None, student=None, link={}, perms=perms, tenant=None))
        try:
            assert VW.hides_injury() is hidden
            assert (WV._pain_part(u) == {}) is hidden
            if hidden:
                assert WV._pain_state(None, None, {}) is None
        finally:
            VW.reset(tok)
    assert set(VW.INJURY_FIELDS) >= set(u)
