"""SP-654 (SP-570 TRC-4): Cairn reads this person's saved calculations and time curves.

GET /api/v1/link/cairn/curves?gpx_sha256= with a linked user's service token (link:calc): every
calculation this person saved for that same GPX, each with its cairn-curve (SP-653). On top of the
link (SP-329) the person allows this one feature on its own (設定 › 已連結的服務; SP-329 Q1-3: each
feature asks when it launches) and can switch it off there; it needs a paid plan (SP-570 Q1-5).

The curve itself is SP-653's (backend.engine.cairn_curve.for_saved): faked here by a stand-in that
reads the saved row of the request's own tenant, so a leak between tenants would show."""
from __future__ import annotations

import json

import pytest

from backend.tests.test_link_sp329 import (  # noqa: F401 — fixtures used by name
    SECRET, _limits, _link, _read, _server, _token, _uid, app, browser, c, cairn, csrf, env, sign_up, started,
)

CURVES_URL = "/api/v1/link/cairn/curves"
LINKS_URL = "/api/v1/account/links"
SHA_X = "a" * 64
SHA_Y = "b" * 64


@pytest.fixture
def saved(monkeypatch):
    """The race calculator's saved rows in each tenant's own DB, and SP-653's curve faked: built
    from the saved row of the current tenant (its inputs carry the GPX hash for the test)."""
    from backend import tenancy
    from backend.accounts import link_calc as LC
    from backend.engine import race_calc_store as RC
    monkeypatch.setattr(RC, "_default_db", lambda: tenancy.db_path())

    def fake_curve(eid):
        row = RC.get(eid)
        if row is None or not (row.get("inputs") or {}).get("sha"):
            return None
        return {"v": 1, "source": "trailruncoach", "gpx_sha256": row["inputs"]["sha"], "total_m": 1000.0,
                "points": [[0, 0], [1000, row["inputs"].get("secs", 600)]], "who": row["inputs"].get("who")}
    monkeypatch.setattr(LC, "curve_for_saved", fake_curve)

    def put(sub: str, eid: str, sha: str, secs: int = 600):
        from backend.accounts import store, tenants as TN
        with tenancy.use(TN.tenant_of(store.get_user(_uid(sub)))):
            RC.save(eid, {"sha": sha, "secs": secs, "who": sub}, {"total_s": secs})
    return put


def _subscribe(sub: str):
    from backend.accounts import store
    store.set_plan(_uid(sub), "subscriber", "2099-01-01T00:00:00+00:00")


def _allow(cl, allowed: bool = True, scope: str = "link:calc"):
    return cl.post(f"{LINKS_URL}/cairn/scopes", json={"scope": scope, "allowed": allowed},
                   headers={"X-TRC-CSRF": csrf(cl)})


def _ready(cl, sub: str, email: str):
    """Signed up, linked, link:calc allowed, subscribed."""
    sign_up(cl, sub, email)
    assert _link(cl).status_code == 200
    r = _allow(cl)
    assert r.status_code == 200, r.text
    _subscribe(sub)


def _curves(srv, tok: str, sha: str = SHA_X):
    return _read(srv, tok, CURVES_URL, params={"gpx_sha256": sha})


# ---------------------------------------------------------------------------- the route

def test_lists_the_persons_saved_calculations_for_the_same_gpx(c, app, cairn, saved):
    _ready(c, "g-a", "alice@example.com")
    saved("g-a", "race-1", SHA_X, 700)
    saved("g-a", "race-2", SHA_X, 900)
    saved("g-a", "race-3", SHA_Y)
    with _server(app) as srv:
        r = _curves(srv, _token(srv, "g-a").json()["token"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gpx_sha256"] == SHA_X
    got = {x["event_id"]: x for x in body["calcs"]}
    assert set(got) == {"race-1", "race-2"}
    assert got["race-1"]["curve"]["points"][-1] == [1000, 700] and got["race-1"]["curve"]["gpx_sha256"] == SHA_X
    assert got["race-1"]["uid"] and got["race-1"]["saved_at"]
    assert "inputs" not in got["race-1"] and "result" not in got["race-1"]      # the curve, not the form
    assert "no-store" in r.headers["cache-control"]


def test_an_upper_case_hash_is_the_same_gpx_and_a_bad_one_is_refused(c, app, cairn, saved):
    _ready(c, "g-a", "alice@example.com")
    saved("g-a", "race-1", SHA_X)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        assert [x["event_id"] for x in _curves(srv, tok, SHA_X.upper()).json()["calcs"]] == ["race-1"]
        for bad in ("", "xyz", "a" * 63, "g" * 64):
            r = _curves(srv, tok, bad)
            assert r.status_code == 400 and r.json()["detail"]["code"] == "BAD_GPX_SHA256", bad
        r = _read(srv, tok, CURVES_URL)
        assert r.status_code == 400


def test_nothing_saved_for_that_gpx_is_an_empty_list(c, app, cairn, saved):
    _ready(c, "g-a", "alice@example.com")
    with _server(app) as srv:
        r = _curves(srv, _token(srv, "g-a").json()["token"])
    assert r.status_code == 200 and r.json()["calcs"] == []


def test_the_curve_comes_from_sp653s_for_saved(monkeypatch):
    """The contract with SP-653: backend.engine.racepower.cairn_curve.for_saved(eid) -> the curve;
    its CurveError (no result, no / changed GPX, …) leaves that calculation out."""
    from backend.accounts import link_calc as LC
    from backend.engine.racepower import cairn_curve as CC
    monkeypatch.setattr(CC, "for_saved", lambda eid: {"v": 1, "gpx_sha256": SHA_X, "eid": eid})
    assert LC.curve_for_saved("race-1") == {"v": 1, "gpx_sha256": SHA_X, "eid": "race-1"}
    for code in ("NO_RESULT", "BAD_ID", "NO_GPX", "GPX_CHANGED", "GPX_MISMATCH", "NO_TRACK", "NO_SEGMENTS"):
        def boom(eid, code=code):
            raise CC.CurveError(code, "x")
        monkeypatch.setattr(CC, "for_saved", boom)
        assert LC.curve_for_saved("race-1") is None, code


def test_a_calculation_without_a_curve_is_left_out_and_a_crash_hides_no_other(c, app, cairn, saved, monkeypatch):
    from backend.accounts import link_calc as LC
    _ready(c, "g-a", "alice@example.com")
    saved("g-a", "race-1", SHA_X)
    saved("g-a", "race-2", SHA_X)
    saved("g-a", "race-3", SHA_X)
    fake = LC.curve_for_saved

    def some(eid):
        if eid == "race-2":
            return None
        if eid == "race-3":
            raise RuntimeError("bug")
        return fake(eid)
    monkeypatch.setattr(LC, "curve_for_saved", some)
    with _server(app) as srv:
        r = _curves(srv, _token(srv, "g-a").json()["token"])
    assert [x["event_id"] for x in r.json()["calcs"]] == ["race-1"]


# ---------------------------------------------------------------------------- who may read

def test_no_token_or_a_wrong_scope_is_refused(c, app, cairn, saved):
    from backend.accounts import store
    _ready(c, "g-a", "alice@example.com")
    saved("g-a", "race-1", SHA_X)
    with _server(app) as srv:
        r = srv.get(CURVES_URL, params={"gpx_sha256": SHA_X})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"
        r = _curves(srv, "trc_" + "x" * 40)
        assert r.status_code == 401
        tok = store.create_service_token(_uid("g-a"), "cairn", ["link:account"])
        r = _curves(srv, tok)
        assert r.status_code == 403 and r.json()["detail"] == {"code": "SCOPE_REQUIRED", "scope": "link:calc"}
        # the session cookie never stands in for a token
        assert c.get(CURVES_URL, params={"gpx_sha256": SHA_X}).status_code == 401


def test_the_feature_needs_its_own_consent_and_can_be_switched_off(c, app, cairn, saved):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    _subscribe("g-a")
    _link(c)
    saved("g-a", "race-1", SHA_X)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        # linked, but this feature not allowed yet: Cairn is told where the person can allow it
        r = _curves(srv, tok)
        assert r.status_code == 403, r.text
        d = r.json()["detail"]
        assert d["code"] == "CONSENT_REQUIRED" and d["scope"] == "link:calc"
        assert d["link_url"].endswith("/api/v1/app/settings?link=cairn&allow=link%3Acalc#links")
        f = _allow(c).json()["features"][0]
        assert {k: f[k] for k in ("scope", "text", "allowed", "plan")} == {
            "scope": "link:calc", "text": d["text"], "allowed": True, "plan": "subscriber"}
        assert f["kind"] == "read" and f["detail"]                    # SP-666: what it lets Cairn read
        assert _curves(srv, tok).status_code == 200                   # the same token: allowed now
        assert _allow(c, False).status_code == 200
        r = _curves(srv, tok)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
        # the account route is not this feature's: it still answers
        assert _read(srv, tok).status_code == 200
        # unlinking and linking again asks again
        _allow(c)
        _link(c, False)
        _link(c)
        tok = _token(srv, "g-a").json()["token"]
        assert _curves(srv, tok).json()["detail"]["code"] == "CONSENT_REQUIRED"
    acts = [(x["action"], x["client"], json.loads(x["documents"] or "{}").get("scopes"))
            for x in store.consents(_uid("g-a"))]
    assert acts[1:] == [("link", "cairn", ["link:account", "link:weather"]),
                        ("allow", "cairn", ["link:calc"]), ("disallow", "cairn", ["link:calc"]),
                        ("allow", "cairn", ["link:calc"]), ("unlink", "cairn", []),
                        ("link", "cairn", ["link:account", "link:weather"])]
    # the terms read as before (none of these rows is a terms row)
    assert store.consent_version(_uid("g-a")) is not None


def test_without_a_paid_plan_a_clear_error(c, app, cairn, saved):
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    _allow(c)
    saved("g-a", "race-1", SHA_X)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        r = _curves(srv, tok)
        assert r.status_code == 402, r.text
        assert r.json()["detail"] == {"code": "PLAN_REQUIRED", "plan": "subscriber", "role": "user", "upgrade": True}
        _subscribe("g-a")
        assert _curves(srv, tok).status_code == 200
        # an expired subscription is the free plan again
        from backend.accounts import store
        store.set_plan(_uid("g-a"), "subscriber", "2000-01-01T00:00:00+00:00")
        assert _curves(srv, tok).json()["detail"]["code"] == "PLAN_REQUIRED"


def test_the_owner_needs_no_plan(c, app, cairn, saved):
    from backend.tests.test_link_sp329 import OWNER_EMAIL, location, sign_in
    assert location(sign_in(c, "g-owner", OWNER_EMAIL)) == "/"
    _link(c)
    _allow(c)
    with _server(app) as srv:
        r = _curves(srv, _token(srv, "g-owner").json()["token"])
    assert r.status_code == 200, r.text


def test_only_this_persons_data(c, app, cairn, saved):
    _ready(c, "g-a", "alice@example.com")
    b = browser(app, "203.0.113.21")
    _ready(b, "g-b", "bob@example.com")
    saved("g-a", "race-1", SHA_X, 700)
    saved("g-b", "race-1", SHA_X, 999)          # the same event id and GPX in another tenant
    saved("g-b", "race-b", SHA_X, 999)
    with _server(app) as srv:
        tok_a = _token(srv, "g-a").json()["token"]
        tok_b = _token(srv, "g-b").json()["token"]
        a = _curves(srv, tok_a).json()["calcs"]
        assert [(x["event_id"], x["curve"]["who"], x["curve"]["points"][-1][1]) for x in a] == [("race-1", "g-a", 700)]
        got_b = sorted((x["event_id"], x["curve"]["who"]) for x in _curves(srv, tok_b).json()["calcs"])
        assert got_b == [("race-1", "g-b"), ("race-b", "g-b")]
        # B switching the feature off changes nothing for A
        _allow(b, False)
        assert _curves(srv, tok_b).status_code == 403 and _curves(srv, tok_a).status_code == 200
        # nothing the caller sends picks another person
        r = _read(srv, tok_a, CURVES_URL, params={"gpx_sha256": SHA_X, "sub": "g-b", "user_id": _uid("g-b")})
        assert [x["curve"]["who"] for x in r.json()["calcs"]] == ["g-a"]


# ---------------------------------------------------------------------------- the settings side

def test_the_settings_api_lists_and_switches_the_feature(c, app, cairn):
    sign_up(c, "g-a", "alice@example.com")
    row = c.get(LINKS_URL).json()["clients"][0]
    # link:gpx_sync joined in SP-655 (推到 Cairn)
    assert [f["scope"] for f in row["features"]] == ["link:calc", "link:gpx_sync"]
    assert not any(f["allowed"] for f in row["features"])
    assert "link:calc" not in [s["scope"] for s in row["scopes"]]        # asked on its own, not by the link
    r = _allow(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_LINKED"
    _link(c)
    assert c.post(f"{LINKS_URL}/cairn/scopes", json={"scope": "link:calc", "allowed": True}).status_code == 403  # CSRF
    for bad in ({"scope": "link:account", "allowed": True}, {"scope": "link:participant_ability", "allowed": True},
                {"scope": "link:calc"}):
        r = c.post(f"{LINKS_URL}/cairn/scopes", json=bad, headers={"X-TRC-CSRF": csrf(c)})
        assert r.status_code == 400, bad
    assert _allow(c).json()["features"][0]["allowed"] is True
    assert c.get(LINKS_URL).json()["clients"][0]["features"][0]["allowed"] is True


def test_link_scopes_have_weather():
    from backend import membership as MS
    from backend.accounts import links as LK
    assert "link:weather" in MS.LINK_SCOPES and "link:weather" in MS.SCOPES
    assert "link:weather" in LK.CAIRN.scopes and "link:weather" in LK.SCOPE_TEXT
    assert LK.ROUTES[CURVES_URL] == "link:calc"
    assert set(LK.FEATURES) <= set(LK.CAIRN.scopes) <= set(MS.LINK_SCOPES)


def test_the_settings_page_has_the_feature_switch(c, app, cairn):
    from backend.tests.test_link_sp329 import HTML
    sign_up(c, "g-a", "alice@example.com")
    page = c.get("/api/v1/app/settings", headers=HTML).text
    assert "settings.acct.link_feature_ask" in page and "/scopes" in page
    # not data-scope: shell.js locks every [data-scope] the plan lacks, and link:* is no role's scope
    assert "dataset.linkScope = f.scope" in page and "dataset.scope = f.scope" not in page


# ---------------------------------------------------------------------------- with SP-653's real curve

def test_end_to_end_with_sp653s_curve(c, app, cairn, monkeypatch, tmp_path):
    """No stand-in: a saved calculation and the event's GPX in the person's own tenant, the curve
    built by cairn_curve.for_saved; the hash Cairn sends is the GPX file's sha256."""
    import hashlib
    from backend import tenancy
    from backend.accounts import store, tenants as TN
    from backend.engine import event_gpx as EG, race_calc_store as RC
    from backend.tests.test_cairn_curve_sp653 import GPX, _trail_result
    monkeypatch.setattr(RC, "_default_db", lambda: tenancy.db_path())
    monkeypatch.setattr(EG, "_default_db", lambda: tenancy.db_path())
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    EG._memo.clear()
    _ready(c, "g-a", "alice@example.com")
    with tenancy.use(TN.tenant_of(store.get_user(_uid("g-a")))):
        RC.save("ev1", {"form": {"start": "06:30"}}, _trail_result())
        EG.save("ev1", GPX, "route.gpx")
        RC.save("ev2", {"form": {}}, _trail_result())                 # no GPX with it: not listed
    sha = hashlib.sha256(GPX).hexdigest()
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        r = _curves(srv, tok, sha)
        assert r.status_code == 200, r.text
        calcs = r.json()["calcs"]
        assert [x["event_id"] for x in calcs] == ["ev1"]
        curve = calcs[0]["curve"]
        assert curve["v"] == 1 and curve["gpx_sha256"] == sha and curve["start_times"] == ["06:30"]
        assert _curves(srv, tok, SHA_Y).json()["calcs"] == []
