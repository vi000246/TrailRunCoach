"""SP-124: what someone who hasn't signed in gets — the race calculator's share pages and the
online demo — and the 「用自己的資料算」 entry on both.

- /share/<id> opens signed out, for anyone's share: accounts.db's shares row names the tenant
  that holds the snapshot (tenancy_mw._share_tenant); only the frozen, whitelisted snapshot is
  read there, nothing is computed (the calculator itself stays behind sign-in).
- 「用自己的資料算」 (login.join_link): sign-up open -> /login (then the calculator); full -> the
  waitlist (/login?waitlist=1); before sign-up opens -> the public demo (TRC_DEMO_URL), the 關於
  page (/about) when none is set (Q2, owner 2026-10-19). GET /join (an exact public path) redirects the same way; the demo's banner
  links to it (WKO5COACH_DEMO_CTA_URL, https only).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
BODY = {"type": "road", "distance_km": 10, "course": {"manual": {"km": 10, "split": "km"}},
        "start_time": "07:00", "date": "2027-03-01", "stops": []}
SNAP_KEYS = {"v", "title", "created", "expires", "type", "inputs", "summary", "effort", "cp", "weight",
             "segments", "seg_targets", "days", "profile", "fuel"}


def _fake_plan(tag: str) -> dict:
    """A /plan result carrying things a share must never show (the athlete's records)."""
    return {"type": "road", "course_name": f"{tag} 的比賽",
            "summary": {"km": 10.0, "time_s": 3000.0 if tag == "alice" else 3600.0, "power": 250.0},
            "segments": [{"i": 1, "start_km": 0, "end_km": 10, "power": 250.0, "t": 3000.0}],
            "fuel": {"body": {"weight_kg": 61.5}, "crosscheck": f"{tag}-private"},
            "used": {"cp": {"value": 280.0, "source": f"{tag}-records"}, "weight": {"value": 61.5}},
            "warnings": [f"{tag}-private warning quoting a record"]}


@pytest.fixture
def plans(monkeypatch):
    """make_plan by the signed-in user's name (the calculator's model needs real data); counts calls."""
    from backend.accounts import login as LG
    from backend.api import racepower as RP
    calls = []

    def fake(body):
        u = LG.current_user()
        calls.append(u.email if u else None)
        return _fake_plan(u.email.split("@")[0])

    monkeypatch.setattr(RP, "make_plan", fake)
    return calls


def _share(cl) -> str:
    r = cl.post("/api/v1/racepower/share", json=BODY, headers={"X-TRC-CSRF": csrf(cl)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _join(page: str):
    m = re.search(r"window\.TRC_JOIN = (.*?);</script>", page)
    assert m, "the share page carries no 「用自己的資料算」 link"
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------- anyone's share, signed out

def test_two_tenants_shares_open_signed_out_and_show_only_the_snapshot(c, app, plans):
    from backend import accounts
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    a = _share(c)
    bob = browser(app)
    sign_up(bob, "g-bob", "bob@example.com")
    b = _share(bob)
    ua, ub = store.find_identity("google", "g-alice"), store.find_identity("google", "g-bob")
    assert store.share_owner(a).id == ua.id and store.share_owner(b).id == ub.id
    # each snapshot lives in its own user's folder, none in the owner's
    users = accounts.server_root() / "users"
    assert (users / str(ua.id) / "racepower_shares" / f"{a}.json").is_file()
    assert (users / str(ub.id) / "racepower_shares" / f"{b}.json").is_file()
    assert not (users / str(ub.id) / "racepower_shares" / f"{a}.json").exists()

    anyone = browser(app, ip="198.51.100.7")                     # signed out, another IP
    n = len(plans)
    for sid, who, t in ((a, "alice", 3000.0), (b, "bob", 3600.0)):
        page = anyone.get(f"/share/{sid}", headers=HTML)
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        d = anyone.get(f"/share/{sid}/data")
        assert d.status_code == 200, d.text
        snap = d.json()
        assert snap["title"] == f"{who} 的比賽" and snap["summary"]["time_s"] == t
        assert set(snap) == SNAP_KEYS and snap["weight"] is None
        raw = json.dumps(snap, ensure_ascii=False)
        # nothing past the whitelist: no sources, no warnings quoting records, no body weight
        assert "private" not in raw and "records" not in raw and "61.5" not in raw and "@example.com" not in raw
    assert len(plans) == n                                        # reading a share computes nothing
    # the signed-out page says nothing about anyone (no user, no membership of the tenant it read)
    sess = re.search(r"window\.TRC_SESSION = (.*?);</script>", anyone.get(f"/share/{a}", headers=HTML).text)
    assert json.loads(sess.group(1))["user"] is None and json.loads(sess.group(1))["membership"] is None

    # one's own shares only: Alice can't delete Bob's, Bob can
    assert c.delete(f"/api/v1/racepower/shares/{b}", headers={"X-TRC-CSRF": csrf(c)}).status_code == 404
    assert anyone.get(f"/share/{b}/data").status_code == 200
    assert bob.delete(f"/api/v1/racepower/shares/{b}", headers={"X-TRC-CSRF": csrf(bob)}).status_code == 200
    assert anyone.get(f"/share/{b}/data").status_code == 404 and store.share_owner(b) is None
    assert anyone.get(f"/share/{a}/data").status_code == 200


def test_signed_out_nothing_is_computed(c, app, plans, monkeypatch):
    from backend.api import racepower as RP
    sign_up(c, "g-alice", "alice@example.com")
    sid = _share(c)

    def boom(body):
        raise AssertionError("a signed-out request reached the calculator")

    monkeypatch.setattr(RP, "make_plan", boom)
    anyone = browser(app, ip="198.51.100.8")
    assert anyone.get(f"/share/{sid}", headers=HTML).status_code == 200
    assert anyone.get(f"/share/{sid}/data").status_code == 200
    # the calculator itself (and making a share) stays behind sign-in
    tok = csrf(anyone)
    for path in ("/api/v1/racepower/plan", "/api/v1/racepower/share"):
        r = anyone.post(path, json=BODY, headers={"X-TRC-CSRF": tok})
        assert r.status_code == 401 and r.json()["code"] == "LOGIN_REQUIRED", (path, r.text)
    page = anyone.get("/api/v1/racepower/page", headers=HTML)
    assert page.status_code in (302, 303, 307) and location(page).startswith("/login?")


def test_the_owners_shares_still_open(c, app, monkeypatch, plans):
    """A share with no row (made by the owner before SP-124, or by the owner now) is the owner's."""
    from backend.accounts import store
    o = browser(app)
    sign_in(o, "g-owner", OWNER_EMAIL)
    monkeypatch.setattr("backend.tests.test_free_entry_sp124._fake_plan", lambda tag: _OWNER_PLAN)
    sid = _share(o)
    assert store.share_owner(sid).id == 1
    store.remove_share(sid)                                       # as before SP-124: no row
    assert browser(app, ip="198.51.100.9").get(f"/share/{sid}/data").json()["title"] == "owner 的比賽"


_OWNER_PLAN = {"type": "road", "course_name": "owner 的比賽", "summary": {"km": 5.0, "time_s": 1500.0},
               "segments": [], "fuel": {}}


def test_a_disabled_accounts_share_is_gone(c, app, plans):
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    sid = _share(c)
    store.set_disabled(store.find_identity("google", "g-alice").id, True, "test")
    anyone = browser(app, ip="198.51.100.10")
    assert anyone.get(f"/share/{sid}/data").status_code == 404


def test_an_unknown_or_malformed_share_reads_no_one(c, app):
    anyone = browser(app, ip="198.51.100.11")
    assert anyone.get("/share/" + "A" * 22 + "/data").status_code == 404
    assert anyone.get("/share/short/data").status_code in (400, 404)


# ---------------------------------------------------------------------------- 「用自己的資料算」

def test_join_goes_to_sign_up_then_the_waitlist_when_full(c, app, plans, monkeypatch):
    sign_up(c, "g-alice", "alice@example.com")
    sid = _share(c)
    anyone = browser(app, ip="198.51.100.12")
    j = _join(anyone.get(f"/share/{sid}", headers=HTML).text)
    assert j == {"kind": "signup", "url": "/login?next=%2Fapi%2Fv1%2Fracepower%2Fpage"}
    r = anyone.get("/join", headers=HTML)
    assert r.status_code == 303 and location(r) == j["url"]
    # signing up from there lands on the calculator
    r = sign_in(anyone, "g-carol", "carol@example.com", nxt="/api/v1/racepower/page")
    assert location(r) == "/signup"
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "3")                # the owner, Alice, Carol (signing up)
    later = browser(app, ip="198.51.100.13")
    assert later.get(f"/share/{sid}", headers=HTML).status_code == 200
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "2")                # full
    j = _join(later.get(f"/share/{sid}", headers=HTML).text)
    assert j == {"kind": "waitlist", "url": "/login?waitlist=1"}
    assert location(later.get("/join", headers=HTML)) == "/login?waitlist=1"


def test_before_sign_up_opens_join_goes_to_the_demo(started, monkeypatch):
    """Sign-in on but the owner not linked yet (sign-up refused): the demo, or the 關於 page (Q2)."""
    anyone = browser(started, ip="198.51.100.14")
    page = anyone.get("/share/" + "A" * 22, headers=HTML)
    assert page.status_code == 200 and _join(page.text) == {"kind": "about", "url": "/about"}
    assert location(anyone.get("/join", headers=HTML)) == "/about"
    assert anyone.get("/about", headers=HTML).status_code == 200          # public: where it goes opens signed out
    monkeypatch.setenv("TRC_DEMO_URL", "https://demo.example.org/")
    assert _join(anyone.get("/share/" + "A" * 22, headers=HTML).text) == {"kind": "demo", "url": "https://demo.example.org/"}
    assert location(anyone.get("/join", headers=HTML)) == "https://demo.example.org/"


def test_owner_mode_join_is_the_demo(monkeypatch):
    from backend.accounts import login as LG
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    monkeypatch.setattr(LG, "required", lambda: False)
    monkeypatch.delenv("TRC_DEMO_URL", raising=False)
    assert LG.join_link() == {"kind": "about", "url": "/about"}            # Q2: no demo site yet
    monkeypatch.setenv("TRC_DEMO_URL", "https://demo.example.org/x?y=1")
    assert LG.join_link() == {"kind": "demo", "url": "https://demo.example.org/x?y=1"}
    for bad in ("http://demo.example.org/", "javascript:alert(1)", "https://u:p@demo.example.org/", "/demo",
                "https:///x", "https://demo.example.org/ x"):
        monkeypatch.setenv("TRC_DEMO_URL", bad)
        assert LG.join_link() == {"kind": "about", "url": "/about"}, bad   # never the bad address


def test_the_demo_instance_has_no_join(monkeypatch):
    from backend.accounts import login as LG
    from backend import tenancy
    monkeypatch.setattr(tenancy, "demo_mode", lambda: True)
    monkeypatch.setenv("TRC_DEMO_URL", "https://demo.example.org/")
    assert LG.join_link() is None


def test_join_is_public_by_its_exact_path(c, app):
    from backend.accounts import login as LG
    assert "/join" in LG.PUBLIC_PATHS and LG.is_public("/join")
    for p in ("/join/", "/join/x", "/joinx", "/join.html", "/api/v1/join"):
        assert not LG.is_public(p), p
    anyone = browser(app, ip="198.51.100.15")
    assert anyone.get("/join/x").status_code == 401


@pytest.mark.parametrize("raw,want", [
    ("https://coach.example.org/join", "https://coach.example.org/join"),
    (" https://coach.example.org/join ", "https://coach.example.org/join"),
    (None, None), ("", None), ("http://coach.example.org/join", None), ("javascript:alert(1)", None),
    ('https://x.example.org/"><script>', None), ("https://u:p@x.example.org/", None), ("/join", None),
])
def test_the_demo_banners_entry_is_an_https_address(raw, want):
    from backend.api.session import _https_url
    assert _https_url(raw) == want


def test_the_pages_carry_the_entry_in_both_languages():
    share = (STATIC / "share.html").read_text("utf-8")
    assert "<!--fill:join-->" in share and 'id="join-top"' in share and "joinCard()" in share
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "share.json").read_text("utf-8"))
        assert {"join.button", "join.title", "join.signup", "join.waitlist", "join.demo", "join.about"} <= set(cat), loc
        assert all(v.strip() for v in cat.values())
        assert "waitlist" in json.loads((STATIC / "i18n" / loc / "login.json").read_text("utf-8"))
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "share.json").read_text("utf-8"))
    sh = json.loads((STATIC / "i18n" / "zh-TW" / "shell.json").read_text("utf-8"))
    assert zh["join.button"] == sh["demo.cta"] == "用自己的資料算"      # the same entry on both
    en = json.loads((STATIC / "i18n" / "en" / "share.json").read_text("utf-8"))
    assert en["join.button"] == json.loads((STATIC / "i18n" / "en" / "shell.json").read_text("utf-8"))["demo.cta"]
    assert re.search(r'q\.has\("waitlist"\)', (STATIC / "login.html").read_text("utf-8"))
