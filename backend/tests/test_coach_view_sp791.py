"""SP-791: 代看 — a coach looks at one student's data (the student's tenant, the coach still signed in),
default deny: every route of the app is walked while viewing, and only the reads and writes written out
here go through (a new route stays closed until someone adds it here and in accounts/viewing.py); the
account, the platform connections, deleting, downloads, payment, Cairn, the injury log are closed even to
read; a withdrawal ends the view at the coach's very next request; the coach's plan and counts, not the
student's; who viewed is in the student's log.

Every person is a made-up test account (accounts_fixtures.many, SP-611) in sign-in mode (test_login_sp58's
app): nothing here reads real data."""
from __future__ import annotations

import re
import sqlite3

import pytest

from backend import site_config as SC, site_settings as SS, tenancy
from backend.tests import accounts_fixtures as AF
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    app, browser, c, csrf, env, started)

HTML = {"Accept": "text/html,application/xhtml+xml"}
_IP = iter(f"198.51.100.{i}" for i in range(10, 250))
# SP-822: "injury" (seeing the injury log) is off in both: ON = SP-792's two change switches on
OFF = {"plan": False, "training": False, "injury": False}
ON = {"plan": True, "training": True, "injury": False}

# ---------------------------------------------------------------------------- what 代看 lets through (written out:
# widening accounts/viewing.py without changing these fails the walk below)

READS_OPEN = """
/ /about /achievements /activities /activities/x /overview /legal /legal/x /manifest.webmanifest /manual
/manual/img/x /manual/index.json /manual/terms.json /manual/x /sw.js
/api/v1/session /api/v1/region
/api/v1/achievements /api/v1/achievements/page
/api/v1/app/activities /api/v1/app/activities/auto /api/v1/app/activities/hints /api/v1/app/activities/pack-defaults
/api/v1/app/activities/page /api/v1/app/activities/stats /api/v1/app/activity-compare
/api/v1/app/activity-compare/activities /api/v1/app/activity-compare/page /api/v1/app/athlete
/api/v1/app/dataset/status /api/v1/app/plan-range /api/v1/app/primary-sport /api/v1/app/race-activities
/api/v1/app/races /api/v1/app/races/detail /api/v1/app/races/page /api/v1/app/races/readiness/x
/api/v1/app/render/ready /api/v1/app/settings /api/v1/app/sports /api/v1/app/viewer /api/v1/app/views
/api/v1/app/views/x/dashboards/x/charts/x /api/v1/app/workouts /api/v1/app/workouts/x/activity
/api/v1/app/workouts/x/kind /api/v1/app/workouts/x/review /api/v1/app/workouts/x/samples
/api/v1/app/workouts/x/segments /api/v1/app/workouts/x/zone-sets
/api/v1/intro/breakdown /api/v1/intro/estimate /api/v1/intro/example/x /api/v1/intro/plan /api/v1/intro/templates
/api/v1/overview/b2b /api/v1/overview/feasibility /api/v1/overview/page /api/v1/overview/plan/auto /api/v1/overview/plan/basis
/api/v1/overview/plan/blackouts /api/v1/overview/plan/calendar /api/v1/overview/plan/compliance
/api/v1/overview/plan/compliance/page /api/v1/overview/plan/equivalence /api/v1/overview/plan/fresh
/api/v1/overview/plan/history /api/v1/overview/plan/prefs /api/v1/overview/plan/prefs/gate
/api/v1/overview/plan/push-coros/preview /api/v1/overview/plan/reconcile /api/v1/overview/plan/routes/library
/api/v1/overview/plan/routes/library/x /api/v1/overview/plan/routes/mine /api/v1/overview/plan/routes/mine/x
/api/v1/overview/plan/routes/page /api/v1/overview/plan/schedule/page /api/v1/overview/plan/sessions
/api/v1/overview/plan/sessions/x/coros-preview /api/v1/overview/plan/steps/templates
/api/v1/overview/plan/steps/templates/recs /api/v1/overview/plan/steps/templates/user
/api/v1/overview/plan/steps/templates/user/x/gpx/segments /api/v1/overview/plan/suggestions
/api/v1/overview/plan/suggestions/declined-tests /api/v1/overview/plan/templates/page
/api/v1/overview/plan/test-options /api/v1/overview/plan/test-suggestions /api/v1/overview/plan/test-templates
/api/v1/overview/plan/test-templates/for /api/v1/overview/plan/variants /api/v1/overview/pmc
/api/v1/overview/status /api/v1/overview/status/fresh /api/v1/overview/summary /api/v1/overview/weekplan
/api/v1/overview/z5
/api/v1/plan /api/v1/plan/events/x/gpx /api/v1/plan/events/x/gpx/preview /api/v1/plan/hr-profile /api/v1/plan/page
/api/v1/plan/pmc-start /api/v1/plan/power-state /api/v1/plan/power-zones /api/v1/plan/profile /api/v1/plan/profile/detect
/api/v1/plan/profile/hint-level /api/v1/plan/race-pace /api/v1/plan/threshold-check /api/v1/plan/threshold-estimate
/api/v1/plan/threshold-history /api/v1/plan/thresholds
/api/v1/racepower/altitude-acclimatisation /api/v1/racepower/backtest /api/v1/racepower/cadence-check
/api/v1/racepower/goal-basis /api/v1/racepower/grade-model /api/v1/racepower/heat-status /api/v1/racepower/hike-meta
/api/v1/racepower/inputs /api/v1/racepower/page /api/v1/racepower/peaks /api/v1/racepower/power-state /api/v1/racepower/saved/x
/api/v1/racepower/solo-hikes /api/v1/racepower/surface/x /api/v1/racepower/weather
/api/v1/routes /api/v1/routes/page /api/v1/routes/status /api/v1/routes/uploaded/page /api/v1/routes/x
/api/v1/routes/x/compare
/api/v1/wko5/x
""".split()

# writes that store nothing (and signing out / back to my own): open whatever the student allows
WRITES_ANY = {("POST", p) for p in """
/api/v1/account/logout /api/v1/coach/view/stop
/api/v1/racepower/course /api/v1/racepower/estimate /api/v1/racepower/plan /api/v1/racepower/predict
/api/v1/overview/plan/blackouts/preview /api/v1/overview/plan/equivalence/design /api/v1/overview/plan/prefs/conflicts
/api/v1/overview/plan/sessions/suggest /api/v1/overview/plan/steps-preview /api/v1/overview/plan/steps/check
/api/v1/overview/plan/steps/derive /api/v1/overview/plan/steps/from-route /api/v1/overview/plan/steps/route
""".split()}

# SP-792: the writes each switch opens (課表／賽事／不排課日; 門檻與訓練設定)
WRITES_PLAN = {tuple(x.split()) for x in """
PUT /api/v1/overview/plan/blackouts|PUT /api/v1/overview/plan/prefs|DELETE /api/v1/overview/plan/push-coros
POST /api/v1/overview/plan/push-coros|POST /api/v1/overview/plan/reconcile|POST /api/v1/overview/plan/rest-days
POST /api/v1/overview/plan/rest-days/move|DELETE /api/v1/overview/plan/rest-days/x|POST /api/v1/overview/plan/sessions
POST /api/v1/overview/plan/sessions/expired/delete|POST /api/v1/overview/plan/sessions/swap
PATCH /api/v1/overview/plan/sessions/x|DELETE /api/v1/overview/plan/sessions/x
DELETE /api/v1/overview/plan/sessions/x/link|POST /api/v1/overview/plan/sessions/x/link
POST /api/v1/overview/plan/sessions/x/revert|POST /api/v1/overview/plan/suggestions/accept
POST /api/v1/overview/plan/suggestions/dismiss|POST /api/v1/overview/plan/suggestions/restore
POST /api/v1/overview/plan/test-suggestions/schedule|PUT /api/v1/plan/events|DELETE /api/v1/plan/events/x
DELETE /api/v1/plan/events/x/gpx|POST /api/v1/plan/events/x/gpx|PUT /api/v1/plan/events/x/gpx/group
PUT /api/v1/plan/events/x/gpx/splits|PUT /api/v1/plan/phases
""".replace("\n", "|").split("|") if x.strip()}
WRITES_TRAINING = {tuple(x.split()) for x in """
PUT /api/v1/plan/hr-profile|PUT /api/v1/plan/pmc-start|PUT /api/v1/plan/power-zones|PUT /api/v1/plan/race-pace
PUT /api/v1/plan/thresholds|POST /api/v1/plan/thresholds/apply-estimate|PATCH /api/v1/overview/plan/prefs
""".replace("\n", "|").split("|") if x.strip()}

# never read the session at all (the gate hands them over before it: their own secret / token), so 代看
# never reaches them: the webhooks, Cairn's server-to-server routes, the public shares
NO_SESSION = {("POST", "/api/v1/billing/webhook"), ("POST", "/api/v1/intervals/webhook"),
              ("POST", "/api/v1/link/x/token"), ("GET", "/api/v1/link/account"), ("GET", "/api/v1/link/cairn/curves"),
              ("GET", "/share/x"), ("GET", "/share/x/data"), ("GET", "/share/calendar/x.ics"),
              # SP-805: Cairn's 揪團腳程估算 (its service token) and the public 腳程試算 under /share/
              ("POST", "/api/v1/link/cairn/pace"), ("GET", "/share/pace"), ("POST", "/share/pace/estimate")}

# the ticket's own list, spelled out (each one is also in the walk): closed even to read
BLOCKED_READS = """
/api/v1/account/me /api/v1/account/export.zip /api/v1/account/reauth /api/v1/account/links /api/v1/account/coach
/api/v1/account/coach/edits /api/v1/account/signup
/api/v1/sync/status /api/v1/sync/settings /api/v1/sync/sources /api/v1/sync/upload/fit
/api/v1/auth/coros/status /api/v1/auth/coros/site /api/v1/auth/garmin/status /api/v1/auth/intervals/status
/api/v1/auth/tp/status /api/v1/auth/session-alerts
/api/v1/archive/fit.zip /api/v1/archive/status /api/v1/achievements/export /api/v1/debug/export/plan
/api/v1/plan/events/x/gpx/file /api/v1/overview/plan/routes/library/x/file
/api/v1/billing /plans
/api/v1/account/links/cairn/pushes /api/v1/racepower/saved/x/cairn-curve /api/v1/racepower/shares
/api/v1/racepower/weather/key
/api/v1/app/injuries /api/v1/app/injuries/page /api/v1/app/injuries/meta /api/v1/app/workouts/x/pain
/admin /api/v1/admin/users /api/v1/settings/debug-api /api/v1/weight/status /api/v1/backup/status
/api/v1/plan/calendar /api/v1/calib /api/v1/coach /api/v1/coach/students/x /api/v1/app/config /api/v1/app/views/dirs
/api/v1/static/admin.html /api/v1/static/injuries.html /api/v1/static/login.html
""".split()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from backend.accounts import coaching
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    SS.reset_cache()
    coaching.reset_limits()
    yield
    SS.reset_cache()
    coaching.reset_limits()


def _in(app, user):
    cl = AF.signed_in(app, user, ip=next(_IP))
    cl.tok = {"X-TRC-CSRF": csrf(cl)}
    cl.user = user
    return cl


def _post(cl, url, body=None):
    return cl.post(url, json=body or {}, headers=cl.tok)


def _code(r):
    try:
        j = r.json()
    except ValueError:
        return None
    d = j.get("detail") if isinstance(j, dict) else None
    return (d.get("code") if isinstance(d, dict) else None) or (j.get("code") if isinstance(j, dict) else None)


def _pair(app, perms=None):
    """A coach and their active student (signed in, each in a browser of their own); `perms` the switches."""
    from backend.accounts import coaching, store
    coach_u, stu_u = AF.many(2)
    assert coaching.set_coach(coach_u.id, True, by=1)
    link = store.coach_accept_invite(coach_u.id, stu_u.id, 1, 4)
    if perms is not None:
        store.set_coach_perms(link["id"], stu_u.id, perms)
    return _in(app, coach_u), _in(app, stu_u), link


def _race(user, name):
    """A race in that person's own 賽季計畫 (plan.json of their tenant)."""
    from backend.engine.planning import Plan
    with tenancy.use(AF.tenant(user)):
        p = Plan.load()
        p.upsert_event({"name": name, "date": "2027-05-01", "priority": "A", "kind": "trail"})
        p.save()


def _races(cl):
    r = cl.get("/api/v1/plan")
    assert r.status_code == 200, r.text
    return [e["name"] for e in r.json()["events"]]


def _view(coach, student_id):
    r = _post(coach, f"/api/v1/coach/view/{student_id}")
    assert r.status_code == 200, r.text
    return r


def _routes(app):
    """Every (method, concrete path) of the app, inside the included routers too ({…} -> x)."""
    stack, out = list(app.routes), set()
    while stack:
        r = stack.pop(0)
        sub = getattr(r, "original_router", None)
        if sub is not None:
            stack.extend(sub.routes)
            continue
        path = getattr(r, "path", None)
        if not path:
            continue
        for m in (getattr(r, "methods", None) or {"GET"}) - {"HEAD", "OPTIONS"}:
            out.add((m, re.sub(r"\{[^}]+\}", "x", path)))
    return sorted(out)


# ---------------------------------------------------------------------------- the acceptance criteria

def test_a_coach_views_the_students_data_with_the_banner_and_stays_themselves(app):
    """驗收 1: the coach opens the student's data — the student's races, not their own; their overview and
    schedule pages; the session says whom they view (the banner) and that they are still the coach; 回到我自己
    gives them their own data back."""
    coach, stu, _link = _pair(app, OFF)
    _race(coach.user, "教練自己的比賽")
    _race(stu.user, "學員的比賽")
    assert _races(coach) == ["教練自己的比賽"]
    r = _view(coach, stu.user.id)
    assert r.json()["go"] == "/api/v1/overview/page"
    assert _races(coach) == ["學員的比賽"]
    s = coach.get("/api/v1/session").json()
    assert s["user"]["id"] == coach.user.id                       # still the coach (the audit's who)
    assert s["viewing"] == {"student_id": stu.user.id, "name": stu.user.display_name, "perms": OFF,
                            "back": "/api/v1/app/settings#coach"}
    assert s["caps"] == []                                        # read-only: the shell locks the controls
    for page in ("/api/v1/overview/page", "/api/v1/overview/plan/schedule/page", "/api/v1/app/activities/page",
                 "/api/v1/app/viewer", "/api/v1/plan/page", "/api/v1/app/settings"):
        p = coach.get(page, headers=HTML)
        assert p.status_code == 200, (page, p.status_code)
        assert '"viewing": {"student_id": %d' % stu.user.id in p.text      # the inlined session: the banner
    assert stu.get("/api/v1/session").json().get("viewing") is None        # the student's own session: nothing
    assert _races(stu) == ["學員的比賽"]
    back = _post(coach, "/api/v1/coach/view/stop")
    assert back.status_code == 200 and back.json()["go"] == "/api/v1/app/settings#coach"
    assert _races(coach) == ["教練自己的比賽"]
    assert "viewing" not in coach.get("/api/v1/session").json()


def test_every_write_but_the_open_ones_is_refused_while_viewing(app):
    """驗收 2 (writes): every route of the app, every method but GET: while viewing a student who allows
    nothing, only WRITES_ANY gets past the gate; the switches' routes say COACH_PERM_OFF, the rest
    COACH_VIEW_READONLY — and the student's data is as it was."""
    coach, stu, _link = _pair(app, OFF)
    _race(stu.user, "學員的比賽")
    _view(coach, stu.user.id)
    writes = [(m, p) for m, p in _routes(app) if m != "GET" and (m, p) not in NO_SESSION]
    assert len(writes) > 150
    bad, perm_off = [], set()
    for m, p in writes:
        if (m, p) in {("POST", "/api/v1/account/logout"), ("POST", "/api/v1/coach/view/stop")}:
            continue                                              # they end the view: below
        r = coach.request(m, p, json={}, headers=coach.tok)
        code = _code(r)
        if (m, p) in WRITES_ANY:
            if r.status_code == 403 and str(code).startswith("COACH_"):
                bad.append((m, p, r.status_code, code))
        elif code == "COACH_PERM_OFF" and r.status_code == 403:
            perm_off.add((m, p))
        elif not (r.status_code == 403 and code == "COACH_VIEW_READONLY"):
            bad.append((m, p, r.status_code, code))
    assert bad == []
    assert perm_off == WRITES_PLAN | WRITES_TRAINING
    assert _races(coach) == ["學員的比賽"]
    assert coach.get("/api/v1/session").json()["viewing"]["student_id"] == stu.user.id       # still viewing


def test_every_read_but_the_open_ones_is_refused_while_viewing(app):
    """驗收 2 (reads): every GET route of the app while viewing — READS_OPEN pass the gate, everything else
    403 COACH_VIEW_BLOCKED (the ticket's own list, BLOCKED_READS, among them); a page of theirs goes back
    to the overview, which says why."""
    coach, stu, _link = _pair(app, ON)
    _view(coach, stu.user.id)
    gets = [p for m, p in _routes(app) if m == "GET" and (m, p) not in NO_SESSION]
    assert set(READS_OPEN) <= set(gets), sorted(set(READS_OPEN) - set(gets))
    bad = []
    for p in gets + [x for x in BLOCKED_READS if x not in gets]:
        r = coach.get(p)
        code = _code(r) if r.headers.get("content-type", "").startswith("application/json") else None
        if p in READS_OPEN:
            if r.status_code == 403 and str(code).startswith("COACH_"):
                bad.append((p, r.status_code, code))
        elif not (r.status_code == 403 and code == "COACH_VIEW_BLOCKED"):
            bad.append((p, r.status_code, code))
    assert bad == []
    assert not set(BLOCKED_READS) & set(READS_OPEN)
    for page in ("/api/v1/app/injuries/page", "/admin", "/plans", "/api/v1/static/admin.html"):
        r = coach.get(page, headers=HTML)
        assert r.status_code == 302 and r.headers["location"] == "/api/v1/overview/page?coach_view=blocked", page
    # the student themselves: their own account, as always
    assert stu.get("/api/v1/account/me").status_code == 200
    assert stu.get("/api/v1/app/injuries").status_code == 200


@pytest.mark.parametrize("how", ["student_withdraws", "coach_lets_go", "admin_takes_coach_away", "student_disabled",
                                 "student_deleted"])
def test_the_view_ends_at_the_very_next_request(app, how):
    """驗收 2 (撤回): whatever ends the link, the coach's next request reads nothing of the student's
    (403 COACH_VIEW_ENDED, nothing cached), a page goes back to their own 教練 tab, and from then on
    they read their own data; they cannot start again."""
    from backend.accounts import coaching, store
    coach, stu, link = _pair(app, ON)
    _race(coach.user, "教練自己的比賽")
    _race(stu.user, "學員的比賽")
    _view(coach, stu.user.id)
    assert _races(coach) == ["學員的比賽"]
    if how == "student_withdraws":
        assert _post(stu, f"/api/v1/account/coach/{link['id']}/end").status_code == 200
    elif how == "coach_lets_go":
        store.coach_end(link["id"], "coach", coach.user.id)
    elif how == "admin_takes_coach_away":
        coaching.set_coach(coach.user.id, False, by=1)
    elif how == "student_disabled":
        store.set_disabled(stu.user.id, True)
    else:
        store.delete_user(stu.user.id)
        # the account and its view row are gone together: the coach's next request is their own
        assert _races(coach) == ["教練自己的比賽"]
        assert "viewing" not in coach.get("/api/v1/session").json()
        return
    r = coach.get("/api/v1/plan")
    assert r.status_code == 403 and _code(r) == "COACH_VIEW_ENDED" and "學員的比賽" not in r.text
    w = coach.put("/api/v1/plan/events", json={"name": "x", "date": "2027-06-01", "priority": "A", "kind": "trail"},
                  headers=coach.tok)
    assert w.status_code == 403 and _code(w) == "COACH_VIEW_ENDED"
    s = coach.get("/api/v1/session")                              # the shell's session still answers (own)
    assert s.status_code == 200 and "viewing" not in s.json()
    p = coach.get("/api/v1/overview/page", headers=HTML)
    assert p.status_code == 302 and p.headers["location"] == "/api/v1/app/settings?coach_view=ended#coach"
    assert _races(coach) == ["教練自己的比賽"]
    again = _post(coach, f"/api/v1/coach/view/{stu.user.id}")
    assert again.status_code == 403 and _code(again) in ("NOT_YOUR_STUDENT", "NOT_A_COACH")


def test_back_to_my_own_works_after_a_withdrawal_too(app):
    coach, stu, link = _pair(app, ON)
    _view(coach, stu.user.id)
    _post(stu, f"/api/v1/account/coach/{link['id']}/end")
    r = _post(coach, "/api/v1/coach/view/stop")
    assert r.status_code == 200
    assert coach.get("/api/v1/plan").status_code == 200


# ---------------------------------------------------------------------------- who may start one

def test_only_an_active_students_coach_starts_a_view(app):
    from backend.accounts import coaching, store
    coach, stu, _link = _pair(app, ON)
    other_u, nobody_u, pend_u = AF.many(3)
    other = _in(app, other_u)
    # someone who is not a coach
    r = _post(other, f"/api/v1/coach/view/{stu.user.id}")
    assert r.status_code == 403 and _code(r) == "NOT_A_COACH"
    # a coach, but not theirs / nobody's / pending / odd ids: the same answer
    coaching.set_coach(other_u.id, True, by=1)
    store.coach_request(coach.user.id, pend_u.id, 1)
    for sid in (stu.user.id, nobody_u.id, 999999, "0", "-1", "abc", "1e9", str(2 ** 63)):
        r = _post(other if sid == stu.user.id else coach, f"/api/v1/coach/view/{sid}")
        assert r.status_code == 403 and _code(r) == "NOT_YOUR_STUDENT", (sid, r.status_code, r.text)
    r = _post(coach, f"/api/v1/coach/view/{pend_u.id}")
    assert r.status_code == 403 and _code(r) == "NOT_YOUR_STUDENT"
    assert "viewing" not in coach.get("/api/v1/session").json()
    # viewing already: closed until 回到我自己
    _view(coach, stu.user.id)
    r = _post(coach, f"/api/v1/coach/view/{stu.user.id}")
    assert r.status_code == 403 and _code(r) == "COACH_VIEW_READONLY"


def test_the_owners_data_is_never_viewed(app):
    from backend.accounts import coaching, store
    coach_u, = AF.many(1)
    coaching.set_coach(coach_u.id, True, by=1)
    store.coach_accept_invite(coach_u.id, 1, 1, 4)              # the owner as someone's student
    coach = _in(app, coach_u)
    r = _post(coach, "/api/v1/coach/view/1")
    assert r.status_code == 403 and _code(r) == "COACH_VIEW_OWNER"
    # a row made by hand still opens nothing of the owner's
    tok = coach.cookies.get("__Host-trc_session")
    con = sqlite3.connect(store.path())
    con.execute("INSERT INTO coach_views(token_hash, coach_id, student_id, started_at) VALUES (?, ?, 1, 'x')",
                (store._hash(tok), coach_u.id))
    con.commit()
    con.close()
    r = coach.get("/api/v1/plan")
    assert r.status_code == 403 and _code(r) == "COACH_VIEW_ENDED"


def test_a_view_row_never_opens_someone_who_is_not_their_student(app):
    """The gate reads the student id from accounts.db and checks the link again: a row naming someone
    else's student (made by hand) is refused, never read."""
    from backend.accounts import store
    coach, stu, _link = _pair(app, ON)
    other_coach, other_stu, _l2 = _pair(app, ON)
    _race(other_stu.user, "別人的學員")
    tok = coach.cookies.get("__Host-trc_session")
    con = sqlite3.connect(store.path())
    con.execute("INSERT INTO coach_views(token_hash, coach_id, student_id, started_at) VALUES (?, ?, ?, 'x')",
                (store._hash(tok), coach.user.id, other_stu.user.id))
    con.commit()
    con.close()
    r = coach.get("/api/v1/plan")
    assert r.status_code == 403 and "別人的學員" not in r.text
    # another coach's view row is not this session's either
    with pytest.raises(KeyError):
        store.coach_view_put(tok, other_coach.user.id, other_stu.user.id)


def test_a_view_is_one_sessions_and_ends_with_it(app):
    from backend.accounts import store
    coach, stu, _link = _pair(app, ON)
    _race(coach.user, "教練自己的比賽")
    _race(stu.user, "學員的比賽")
    _view(coach, stu.user.id)
    other_browser = _in(app, coach.user)                          # the same coach on another device
    assert _races(other_browser) == ["教練自己的比賽"]
    assert _post(coach, "/api/v1/account/logout").status_code == 200
    con = sqlite3.connect(store.path())
    try:
        assert con.execute("SELECT count(*) FROM coach_views").fetchone()[0] == 0
    finally:
        con.close()


def test_signed_out_and_owner_mode_and_demo_have_no_view(app, monkeypatch, tmp_path):
    c2 = browser(app, "198.51.100.251")
    assert c2.post("/api/v1/coach/view/stop", headers={"X-TRC-CSRF": csrf(c2)}).status_code == 401


def test_owner_mode_and_the_demo_have_no_view_routes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.tests.test_login_sp58 import _clean_env
    _clean_env(monkeypatch, tmp_path)
    with TestClient(build_app(demo=False)) as cl:
        assert cl.post("/api/v1/coach/view/stop").status_code == 404
        assert cl.post("/api/v1/coach/view/2").status_code == 404
        assert cl.get("/api/v1/account/coach/edits").status_code == 404
    assert not [p for _m, p in _routes(build_app(demo=True)) if "/coach" in p]


def test_answers_while_viewing_are_marked_for_the_offline_copy(app):
    coach, stu, _link = _pair(app, ON)
    assert "x-trc-viewing" not in coach.get("/api/v1/plan").headers
    _view(coach, stu.user.id)
    assert coach.get("/api/v1/plan").headers.get("x-trc-viewing") == "1"
    assert "x-trc-viewing" not in stu.get("/api/v1/plan").headers


def test_the_offline_copy_never_keeps_a_viewed_students_data():
    """The coach's browser keeps an offline copy of the coach's own data (SP-314): while viewing, the
    student's answers (X-TRC-Viewing) are never kept, the worker leaves the requests alone, and the coach's
    own copy is still there afterwards."""
    from backend.tests import test_offline_pwa as OP
    if OP.NODE is None:
        pytest.skip("node not installed")
    out = OP.run(r"""
person("coach", 7);
const E = mk();
await E.sync({});
const own = (await (await caches.match(ORIGIN + "/api/v1/overview/status")).json()).who;
server.routes["/api/v1/overview/status"] = () => new Response(JSON.stringify({ who: "student", today: "2026-10-14" }),
  { headers: { "content-type": "application/json", "x-trc-viewing": "1" } });
server.routes["/api/v1/session"] = () => json({ mode: "user", user: { id: 7, name: "coach" },
  login: { required: true, signed_in: true }, viewing: { student_id: 9, name: "student" } });
const synced = await E.sync({});
const online = await (await get(E, req("/api/v1/overview/status"))).json();
const keptAfterOnline = (await (await caches.match(ORIGIN + "/api/v1/overview/status")).json()).who;
await E.setSession({ mode: "user", user: { id: 7 }, login: { required: true, signed_in: true }, viewing: true });
const left = E.handleFetch(req("/api/v1/overview/status"), ctx);
await E.setSession({ mode: "user", user: { id: 7 }, login: { required: true, signed_in: true } });
server.down = true;
const after = (await (await get(E, req("/api/v1/overview/status"))).json()).who;
OUT = { own, synced, online: online.who, keptAfterOnline, left, after };
""")
    assert out == {"own": "coach", "synced": {"ok": True, "viewing": True, "done": []}, "online": "student",
                   "keptAfterOnline": "coach", "left": None, "after": "coach"}


# ---------------------------------------------------------------------------- the coach's plan, the record

def test_viewing_works_with_the_coachs_plan_and_counts(app):
    """SP-56 Q5: the coach's plan and quotas while viewing — the student a subscriber, the coach free: the
    pages get the free tier's scopes and the calculator's count goes on the coach."""
    from backend import membership as MS
    from backend.accounts import store, viewing as VW
    coach, stu, _link = _pair(app, ON)
    store.set_plan(stu.user.id, "subscriber")
    _view(coach, stu.user.id)
    ms = coach.get("/api/v1/session").json()["membership"]
    assert ms["role"] == "user" and ms["plan"] == "free" and "charts:365d" not in ms["scopes"]
    assert stu.get("/api/v1/session").json()["membership"]["plan"] == "subscriber"
    view = VW.resolve(store.get_user(coach.user.id), coach.cookies.get("__Host-trc_session"))
    tok = VW.set_view(view)
    try:
        with tenancy.use(view.tenant):
            got = MS.consume("calc", item="route-a")
    finally:
        VW.reset(tok)
    assert got["used"] == 1
    month = MS.month_key()
    assert store.usage_get(coach.user.id, "calc", month) == 1
    assert store.usage_get(stu.user.id, "calc", month) == 0


def test_the_owner_viewing_has_no_owner_scopes(app):
    from backend import membership as MS
    from backend.accounts import store, viewing as VW
    owner = store.get_user(1)
    m = MS.coach_member(owner)
    assert (m.role, m.plan, m.admin) == ("subscriber", "subscriber", False)
    stu_u, = AF.many(1)
    t = AF.tenant(stu_u)
    tok = VW.set_view(VW.View(coach=owner, student=stu_u, link={}, perms=ON, tenant=t))
    try:
        sc = MS.scopes(t)
    finally:
        VW.reset(tok)
    assert not sc & {"admin", "admin:config", "admin:users", "wko5", "tp:wko5_client", "sync:tp_web"}


def test_the_student_sees_who_viewed_and_when(app):
    coach, stu, _link = _pair(app, ON)
    _view(coach, stu.user.id)
    _post(coach, "/api/v1/coach/view/stop")
    r = stu.get("/api/v1/account/coach/edits")
    assert r.status_code == 200, r.text
    rows = [(e["action"], e["coach"]) for e in r.json()["edits"]]
    assert rows == [("view_end", coach.user.display_name), ("view_start", coach.user.display_name)]
    # nobody else's log: the coach's own is empty
    assert coach.get("/api/v1/account/coach/edits").json()["edits"] == []


def test_deleting_either_account_leaves_no_view_and_keeps_the_students_log(app):
    from backend.accounts import store
    coach, stu, _link = _pair(app, ON)
    _view(coach, stu.user.id)
    out = store.delete_user(coach.user.id)
    assert out.get("coach_views") == 1
    rows = store.coach_audit_of(stu.user.id)
    assert rows and all(r["coach_id"] is None and r["coach_name"] is None for r in rows)
    coach2, stu2, _l = _pair(app, ON)
    _view(coach2, stu2.user.id)
    store.delete_user(stu2.user.id)
    con = sqlite3.connect(store.path())
    try:
        assert con.execute("SELECT count(*) FROM coach_views").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM coach_audit WHERE student_id = ?", (stu2.user.id,)).fetchone()[0] == 0
    finally:
        con.close()
