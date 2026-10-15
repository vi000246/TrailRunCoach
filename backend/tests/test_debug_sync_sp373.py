"""
SP-373 (debug API redesign) and SP-406 (the debug token in sign-in mode):

  * GET /api/v1/debug/export/plan (scope export:plan): every stored 課表 row, deletion markers too,
    never the COROS push records;
  * a local copy pulls production's settings and 課表 one way (engine/upstream.py, 設定 › 進階 ›
    從正式站同步): preview writes nothing, apply mirrors them, this copy's own switches stay, its
    COROS push goes off; GET only, https only, a failed fetch writes nothing;
  * sign-in mode: a GET under /api/v1/debug/ with the owner's debug token passes the login gate with
    no session, nothing else opens with it, nobody but the owner has the debug API.

Synthetic data only: in-memory DBs (debug_fixtures.debug_env), the sign-in harness of test_login_sp58.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from sqlalchemy import select

from backend import debug_auth as DA
from backend.db.models import ActivityTag, InjuryEvent, PlanSession
from backend.engine import planning as P
from backend.engine import upstream as UP
from backend.settings.repository import SettingsRepository
from backend.tests.debug_fixtures import ADMIN, DBG, bearer, debug_env, enable, make_token
from backend.tests.test_plan_store import API as PLAN_API
from backend.tests.test_plan_store import run

EXPORTS = ["export:config", "export:plan"]


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    from datetime import date
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))       # test_plan_store's inputs


def _set(e, **kv):
    repo = SettingsRepository(e.db)
    for k, v in kv.items():
        run(repo.set(k.replace("__", "."), v))
    run(e.db.commit())


def _get(e, key):
    return run(SettingsRepository(e.db).get(key))


def _sessions(e) -> dict:
    e.db.expire_all()
    return {r.uid: r for r in run(e.db.execute(select(PlanSession))).scalars()}


def _production(monkeypatch, tmp_path) -> dict:
    """A production server with settings, plan.json, an injury, a tag and a 課表 (one session
    deleted, one of the user's own): its two exports, as a local copy would fetch them."""
    with debug_env(monkeypatch, tmp_path / "prod") as e:
        enable(e)
        _set(e, athlete__timezone="Asia/Taipei", plan__auto__push=True, plan__prefs__long_day="sun")
        P.Plan(thresholds=[P.Threshold(date="2026-08-01", lthr=168.0, lthr_method="friel30")],
               weights=[P.Weight(date="2026-09-01", kg=62.5)],
               events=[P.Event(id="ev1", name="Race", date="2026-11-01", distance_km=50.0)],
               profile={"height_cm": 171}).save()
        e.db.add(InjuryEvent(id=7, area="calf", onset_date="2026-09-20", status="active"))
        e.db.add(ActivityTag(start_local="2026-09-20T06:00", activity_type="比賽", activity_type_overridden=True,
                             pain=2, injury_id=7))
        run(e.db.commit())
        ss = e.c.get(f"{PLAN_API}/sessions").json()["sessions"]
        assert e.c.delete(f"{PLAN_API}/sessions/{ss[0]['uid']}").status_code == 200
        r = e.c.post(f"{PLAN_API}/sessions", json={"day": "2026-10-03", "kind": "easy", "minutes": 33})
        assert r.status_code == 200, r.text
        tok = make_token(e, EXPORTS)
        docs = {}
        for part, path in UP.PATHS.items():
            r = e.c.get(path, headers=bearer(tok))
            assert r.status_code == 200, r.text
            docs[path] = r.json()
        rows = {r.uid: r for r in run(e.db.execute(select(PlanSession))).scalars()}
        return {"docs": docs, "uids": set(rows), "deleted": {u for u, r in rows.items() if r.state == "deleted"},
                "custom": {u for u, r in rows.items() if r.origin == "custom"}}


# ---------------------------------------------------------------------------- export/plan

def test_export_plan_has_every_stored_row_and_needs_its_scope(monkeypatch, tmp_path):
    prod = _production(monkeypatch, tmp_path)
    doc = prod["docs"][UP.PATHS["plan"]]
    assert doc["schema_version"] == 1 and doc["count"] == len(prod["uids"]) > 3
    got = {s["uid"]: s for s in doc["sessions"]}
    assert set(got) == prod["uids"]                                   # deletion markers and own sessions too
    assert prod["deleted"] and all(got[u]["state"] == "deleted" for u in prod["deleted"])
    assert any(got[u]["minutes"] == 33 for u in prod["custom"])
    assert all("coros" not in s for s in doc["sessions"])             # no push records
    with debug_env(monkeypatch, tmp_path / "again") as e:
        enable(e)
        tok = make_token(e, ["export:config", "read:plan"])
        r = e.c.get(DBG + "/export/plan", headers=bearer(tok))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE"
        assert "export:plan" not in DA.DEFAULT_SCOPES


# ---------------------------------------------------------------------------- the pull

@pytest.fixture
def pulled(monkeypatch, tmp_path):
    """Production's exports, and a local copy (its own settings, 課表, injury and tag) whose
    fetch_json answers with them."""
    prod = _production(monkeypatch, tmp_path)
    calls = []

    def fake_fetch(path):
        calls.append(path)
        return json.loads(json.dumps(prod["docs"][path]))
    monkeypatch.setattr(UP, "fetch_json", fake_fetch)
    monkeypatch.setenv(UP.URL_ENV, "https://coach.example.org")
    monkeypatch.setenv(UP.TOKEN_ENV, "trcd_" + "x" * 43)
    with debug_env(monkeypatch, tmp_path / "local") as e:
        _set(e, athlete__timezone="Europe/London", debug__api__enabled=False)
        e.db.add(InjuryEvent(id=3, area="knee", onset_date="2026-01-02", status="resolved"))
        e.db.add(ActivityTag(start_local="2026-01-02T07:00", activity_type="練跑", activity_type_overridden=True))
        run(e.db.commit())
        e.c.get(f"{PLAN_API}/sessions")
        assert e.c.post(f"{PLAN_API}/sessions", json={"day": "2026-10-02", "kind": "easy", "minutes": 21}).status_code == 200
        e.prod, e.calls = prod, calls
        yield e


def test_preview_says_what_would_change_and_writes_nothing(pulled):
    e = pulled
    before = (set(_sessions(e)), _get(e, "athlete.timezone"), P.Plan.load().thresholds)
    st = e.c.get(f"{ADMIN}/upstream").json()
    assert st["configured"] is True and st["host"] == "coach.example.org" and st["last_pull"] is None
    assert "trcd_" not in json.dumps(st)                                # never the token
    r = e.c.post(f"{ADMIN}/upstream/pull", json={"what": ["config", "plan"], "apply": False})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["apply"] is False and "athlete.timezone" in b["config"]["settings"]["changed"]
    assert not any(k.startswith("debug.") for k in b["config"]["settings"]["changed"])
    assert b["config"]["plan_json"]["thresholds"] == {"here": 0, "production": 1, "same": False}
    assert b["config"]["injuries"]["production"] == 1 and b["config"]["activity_tags"]["here"] == 1
    assert b["plan"]["production"] == len(e.prod["uids"]) and b["plan"]["removed"] >= 1
    assert (set(_sessions(e)), _get(e, "athlete.timezone"), P.Plan.load().thresholds) == before
    assert _get(e, UP.LAST_KEY) is None and "push_off" not in b
    assert sorted(e.calls) == sorted(UP.PATHS.values())                 # GETs of the two exports only


def test_apply_mirrors_production_and_keeps_this_copys_own_switches(pulled):
    e = pulled
    r = e.c.post(f"{ADMIN}/upstream/pull", json={"what": ["config", "plan"], "apply": True})
    assert r.status_code == 200, r.text
    assert r.json()["push_off"] is True
    # the 課表: production's rows exactly, deletion markers and the user's own sessions included
    rows = _sessions(e)
    assert set(rows) == e.prod["uids"]
    assert {u for u, x in rows.items() if x.state == "deleted"} == e.prod["deleted"]
    assert {u for u, x in rows.items() if x.origin == "custom"} == e.prod["custom"]
    assert not any(x.minutes == 21 for x in rows.values())               # the local-only session is gone
    # settings and plan.json
    assert _get(e, "athlete.timezone") == "Asia/Taipei" and _get(e, "plan.prefs.long_day") == "sun"
    assert _get(e, "debug.api.enabled") is False                       # this copy's own switch stays
    assert _get(e, "plan.auto.push") is False                          # production pushes, this copy never
    plan = P.Plan.load()
    assert [(t.date, t.lthr, t.lthr_method) for t in plan.thresholds] == [("2026-08-01", 168.0, "friel30")]
    assert [(w.date, w.kg) for w in plan.weights] == [("2026-09-01", 62.5)]
    assert [(ev.id, ev.distance_km) for ev in plan.events] == [("ev1", 50.0)] and plan.profile == {"height_cm": 171}
    # injuries (ids kept: the tag points at it) and tags replaced
    e.db.expire_all()
    inj = run(e.db.execute(select(InjuryEvent))).scalars().all()
    tags = run(e.db.execute(select(ActivityTag))).scalars().all()
    assert [(i.id, i.area) for i in inj] == [(7, "calf")]
    assert [(t.start_local, t.pain, t.injury_id) for t in tags] == [("2026-09-20T06:00", 2, 7)]
    last = e.c.get(f"{ADMIN}/upstream").json()["last_pull"]
    assert last["what"] == ["config", "plan"] and last["at"]
    # a second pull changes nothing
    again = e.c.post(f"{ADMIN}/upstream/pull", json={"what": ["config", "plan"], "apply": False}).json()
    assert again["config"]["settings"]["changed"] == [] and again["plan"]["added"] == again["plan"]["removed"] == 0
    assert again["plan"]["changed"] == 0 and again["config"]["injuries"]["differ"] == 0
    assert all(v["same"] for v in again["config"]["plan_json"].values())


def test_one_part_only_and_a_failed_fetch_writes_nothing(pulled, monkeypatch):
    e = pulled
    before = set(_sessions(e))
    r = e.c.post(f"{ADMIN}/upstream/pull", json={"what": ["config"], "apply": True})
    assert r.status_code == 200 and "plan" not in r.json()
    assert set(_sessions(e)) == before and _get(e, "athlete.timezone") == "Asia/Taipei"
    _set(e, athlete__timezone="Europe/London")

    def broken(path):
        if path == UP.PATHS["plan"]:
            raise UP.UpstreamError(502, "UPSTREAM_403", "refused")
        return json.loads(json.dumps(e.prod["docs"][path]))
    monkeypatch.setattr(UP, "fetch_json", broken)
    r = e.c.post(f"{ADMIN}/upstream/pull", json={"what": ["config", "plan"], "apply": True})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "UPSTREAM_403"
    assert _get(e, "athlete.timezone") == "Europe/London" and set(_sessions(e)) == before
    # another version's export is refused before anything is written
    monkeypatch.setattr(UP, "fetch_json", lambda path: {**e.prod["docs"][path], "schema_version": 2})
    r = e.c.post(f"{ADMIN}/upstream/pull", json={"what": ["config"], "apply": True})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "SCHEMA"
    assert _get(e, "athlete.timezone") == "Europe/London"
    for bad in ({"what": ["activities"]}, {"what": []}, {"what": ["plan"], "apply": "yes"}):
        assert e.c.post(f"{ADMIN}/upstream/pull", json=bad).status_code == 400


def test_the_fetch_is_a_get_over_https_with_the_token_header(monkeypatch):
    from backend.scripts import debug_fetch as F
    seen = []

    def fake(url, hdrs, timeout=120.0):
        seen.append((url, hdrs))
        return {"ok": 1}
    monkeypatch.setattr(F, "fetch", fake)
    for v in (UP.URL_ENV, UP.TOKEN_ENV, UP.TOKEN_ENV + "_FILE", UP.PROXY_USER_ENV, UP.PROXY_PASSWORD_ENV):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(UP.UpstreamError) as ex:
        UP.fetch_json(UP.PATHS["plan"])
    assert ex.value.code == "NOT_CONFIGURED" and ex.value.status == 400
    monkeypatch.setenv(UP.URL_ENV, "http://coach.example.org")
    monkeypatch.setenv(UP.TOKEN_ENV, "trcd_" + "t" * 43)
    with pytest.raises(UP.UpstreamError) as ex:
        UP.fetch_json(UP.PATHS["plan"])
    assert ex.value.code == "BAD_URL"                                    # http only for localhost
    monkeypatch.setenv(UP.URL_ENV, "https://coach.example.org/")
    monkeypatch.setenv(UP.PROXY_USER_ENV, "me")
    monkeypatch.setenv(UP.PROXY_PASSWORD_ENV, "proxy-pass")
    assert UP.fetch_json(UP.PATHS["plan"]) == {"ok": 1}
    url, hdrs = seen[-1]
    assert url == "https://coach.example.org/api/v1/debug/export/plan"
    assert hdrs["X-TRC-Debug-Token"] == "trcd_" + "t" * 43 and hdrs["Authorization"].startswith("Basic ")

    def refused(url, hdrs, timeout=120.0):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(F, "fetch", refused)
    with pytest.raises(UP.UpstreamError) as ex:
        UP.fetch_json(UP.PATHS["config"])
    assert ex.value.code == "UPSTREAM_403" and ex.value.status == 502
    # the redirect handler refuses to follow (the token would go along)
    h = F._NoRedirect()
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(urllib.request.Request("https://coach.example.org/x"), None, 302, "Found", {},
                           "https://elsewhere.example/")


def test_without_an_upstream_the_block_says_so(monkeypatch, tmp_path):
    for v in (UP.URL_ENV, UP.TOKEN_ENV, UP.TOKEN_ENV + "_FILE"):
        monkeypatch.delenv(v, raising=False)
    with debug_env(monkeypatch, tmp_path) as e:
        st = e.c.get(f"{ADMIN}/upstream").json()
        assert st["configured"] is False and st["host"] is None
        r = e.c.post(f"{ADMIN}/upstream/pull", json={"what": ["plan"]})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "NOT_CONFIGURED"


# ---------------------------------------------------------------------------- sign-in mode (SP-406)

from backend.tests.test_login_sp58 import (OWNER_EMAIL, app, browser, c, csrf, env, sign_in,  # noqa: E402,F401
                                           sign_up, started)


def test_signin_mode_the_owners_debug_token_opens_only_the_debug_api(app, c):
    from backend.accounts import store
    DA.reset_limits()
    o = browser(app)
    sign_in(o, "g-owner", OWNER_EMAIL)
    h = {"X-TRC-CSRF": csrf(o)}
    assert o.put(ADMIN, json={"enabled": True}, headers=h).status_code == 200
    r = o.post(f"{ADMIN}/tokens", json={"name": "laptop", "scopes": ["read:sync", "export:config"], "days": 0},
               headers=h)
    assert r.status_code == 200, r.text
    tok, tid = r.json()["token"], r.json()["id"]
    agent = browser(app)                                            # no cookie, no CSRF header
    r = agent.get(f"{DBG}/sync", headers=bearer(tok))
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["caller"] == "laptop" and r.headers["cache-control"] == "no-store"
    assert agent.get(f"{DBG}/export/config", headers={"X-TRC-Debug-Token": tok}).status_code == 200
    assert agent.get(f"{DBG}/plan", headers=bearer(tok)).status_code == 403          # its scopes still count
    # nothing else opens with it: pages, other reads, the settings, a write under /api/v1/debug/
    for path in ("/api/v1/plan", ADMIN, "/api/v1/session/x", "/api/v1/settings/debug-api/upstream"):
        assert agent.get(path, headers=bearer(tok)).status_code == 401, path
    assert agent.post(f"{DBG}/sync", headers=bearer(tok)).status_code in (401, 403)
    # unknown → 401 at the gate; a hash registered for someone else's account → 401 too (owner only)
    r = agent.get(f"{DBG}/sync", headers=bearer("trcd_" + "z" * 43))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"
    alice = browser(app)
    sign_up(alice, "g-alice", "alice@example.com")
    uid = alice.get("/api/v1/account/me").json()["id"]
    fake = "trcd_" + "a" * 43
    store.register_token_hash(int(uid), DA.CLIENT, [DA.SCOPE_ALL], DA.hash_token(fake))
    assert agent.get(f"{DBG}/sync", headers=bearer(fake)).status_code == 401
    # nobody but the owner has the debug API's settings
    ah = {"X-TRC-CSRF": csrf(alice)}
    assert alice.get(ADMIN).status_code == 403
    assert alice.post(f"{ADMIN}/tokens", json={}, headers=ah).status_code == 403
    # revoked: refused at the gate at once
    assert o.delete(f"{ADMIN}/tokens/{tid}", headers=h).json()["revoked"] == 1
    assert agent.get(f"{DBG}/sync", headers=bearer(tok)).status_code == 401
    DA.reset_limits()
