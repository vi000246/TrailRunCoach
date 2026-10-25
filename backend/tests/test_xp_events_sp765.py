"""SP-765: cross-product events and the sign-up source (accounts/xp.py, api/xp.py, scripts/xp_funnel.py).

  - a sign-up that started from a page carrying from=cairn keeps signup_source = 'cairn' (Google and the
    terms in between); any other sign-up keeps none, and an existing value is never replaced
  - the 疊石 Cairn card's xp-card-shown / xp-card-click (POST /api/v1/app/xp/event), xp-card-dismiss (its
    「不用了」) and xp-linked (the account linked with Cairn) are counted per person and day; a page may
    not report xp-linked or an unknown event; a day's count stops at the cap
  - nothing is counted signed out; 下載我的資料 has the source and the counts; deleting the account
    removes them; an older accounts.db gets the table and the column without losing a row
  - the monthly funnel and the 30-day retention (with the control group) from accounts.db alone
Synthetic data only; SP-58's fake Google, nothing reaches the network."""
from __future__ import annotations

import datetime as dt
import sqlite3

from backend.tests.test_free_entry_sp124 import _join, _share, plans  # noqa: F401 — fixture used by name
from backend.tests.test_link_sp329 import SECRET, _limits  # noqa: F401 — autouse fixture used by name
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    _clean_env, _client, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

EVENT_URL = "/api/v1/app/xp/event"
CALC = "/api/v1/racepower/page"


def _uid(sub: str) -> int:
    from backend.accounts import store
    return store.find_identity("google", sub).id


def _events(uid: int) -> dict:
    from backend.accounts import store
    return {(r["event"], r["day"]): r["n"] for r in store.xp_events_of(uid)}


def _post(cl, event: str):
    return cl.post(EVENT_URL, json={"event": event}, headers={"X-TRC-CSRF": csrf(cl)})


# ---------------------------------------------------------------------------- the sign-up source

def test_a_sign_up_from_a_cairn_link_keeps_where_it_came_from(c, app):
    from backend.accounts import store
    nxt = CALC + "?" + "cairn=https%3A%2F%2Fmap.example%2F%3Fgpx%3Dabc%26share%3Dtok12345&from=cairn"
    out = sign_up(c, "g-a", "alice@example.com", nxt=nxt)
    assert out["next"] == nxt                                   # back where the link pointed
    assert store.signup_source(_uid("g-a")) == "cairn"
    # any other sign-up: no source
    sign_up(browser(app, ip="203.0.113.21"), "g-b", "bob@example.com", nxt=CALC)
    assert store.signup_source(_uid("g-b")) is None
    # written once: an existing source is never replaced
    assert store.set_signup_source(_uid("g-a"), "cairn") is False
    assert store.set_signup_source(_uid("g-b"), None) is False


def test_a_sign_up_from_a_share_page_is_marked_share(c, app, plans):
    """SP-389 Q1-2 (owner 2026-10-20): 「用自己的資料算」 on a race share page → signup_source 'share'."""
    from urllib.parse import parse_qs, urlsplit
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    sid = _share(c)
    anyone = browser(app, ip="198.51.100.31")
    j = _join(anyone.get(f"/share/{sid}", headers={"Accept": "text/html"}).text)
    assert j["kind"] == "signup"
    nxt = parse_qs(urlsplit(j["url"]).query)["next"][0]
    assert nxt == "/api/v1/racepower/page?from=share"
    out = sign_up(anyone, "g-carol", "carol@example.com", nxt=nxt)
    assert out["next"] == nxt                                   # lands on the calculator
    assert store.signup_source(_uid("g-carol")) == "share"
    assert store.signup_source(_uid("g-alice")) is None
    # the demo banner's /join is not a share page: no source there
    assert location(anyone.get("/join", headers={"Accept": "text/html"})).endswith("racepower%2Fpage")


def test_the_source_is_read_from_the_next_only_for_known_sources():
    from backend.accounts import xp as XP
    assert XP.source_of("/api/v1/racepower/page?cairn=x&from=cairn") == "cairn"
    assert XP.source_of("/x?from=CAIRN") == "cairn"
    assert XP.source_of("/api/v1/racepower/page?from=share") == "share"
    for nxt in (None, "", "/", "/x?from=other", "/x?cairn=1", "/x?from=", 12):
        assert XP.source_of(nxt) is None, nxt


def test_full_from_a_cairn_link_says_so_on_the_sign_in_page(c, app, monkeypatch):
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "1")              # only the owner
    r = sign_in(c, "g-full", "full@example.com", nxt=CALC + "?cairn=x&from=cairn")
    assert location(r) == "/login?error=full&from=cairn"
    r = sign_in(browser(app, ip="203.0.113.22"), "g-full2", "full2@example.com", nxt=CALC)
    assert location(r) == "/login?error=full"                   # unchanged for everyone else


# ---------------------------------------------------------------------------- the counts

def test_the_card_events_are_counted_per_person_and_day(c, app, monkeypatch):
    from backend.accounts import xp as XP
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    day = XP.today()
    assert _post(c, XP.SHOWN).json() == {"recorded": True}
    assert _post(c, XP.SHOWN).json() == {"recorded": True}
    assert _post(c, XP.CLICK).json() == {"recorded": True}
    # xp-linked is the server's own (when the link is made); 「不用了」 counts through its own route
    for bad in (XP.LINKED, XP.DISMISS, "xp-other", ""):
        assert _post(c, bad).status_code == 400, bad
    r = c.post("/api/v1/app/xp/cairn/dismiss", headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 200 and r.json()["off"] is True
    assert _events(uid) == {(XP.SHOWN, day): 2, (XP.CLICK, day): 1, (XP.DISMISS, day): 1}
    # 設定's 「重新顯示」 is not a count
    assert c.put("/api/v1/app/xp/cairn", json={"off": False}, headers={"X-TRC-CSRF": csrf(c)}).json()["off"] is False
    assert _events(uid)[(XP.DISMISS, day)] == 1
    # a page in a loop stops at the day's cap
    monkeypatch.setattr(XP, "MAX_PER_DAY", 3)
    assert _post(c, XP.SHOWN).json() == {"recorded": True}
    assert _post(c, XP.SHOWN).json() == {"recorded": False}
    assert _events(uid)[(XP.SHOWN, day)] == 3


def test_linking_cairn_counts_once(c, app, monkeypatch):
    from backend.accounts import xp as XP
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    link = lambda on: c.post("/api/v1/account/links/cairn", json={"linked": on},   # noqa: E731
                             headers={"X-TRC-CSRF": csrf(c)})
    assert link(True).json()["linked"] is True
    assert link(True).status_code == 200                        # already linked: not counted again
    assert _events(uid) == {(XP.LINKED, XP.today()): 1}
    link(False)
    link(True)                                                  # linked again later: counted again
    assert _events(uid) == {(XP.LINKED, XP.today()): 2}


def test_nothing_is_counted_signed_out(c):
    assert c.post(EVENT_URL, json={"event": "xp-card-shown"}, headers={"X-TRC-CSRF": csrf(c)}).status_code == 401


def test_owner_mode_counts_nothing_and_never_fails(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    _clean_env(monkeypatch, tmp_path)
    with TestClient(build_app(demo=False)) as cl:
        assert cl.post(EVENT_URL, json={"event": "xp-card-shown"}).json() == {"recorded": False}


# ---------------------------------------------------------------------------- the person's own data

def test_the_export_has_them_and_deleting_the_account_removes_them(c, app):
    from backend.accounts import export as EX, store, xp as XP
    sign_up(c, "g-a", "alice@example.com", nxt="/api/v1/racepower/page?from=cairn")
    uid = _uid("g-a")
    _post(c, XP.SHOWN)
    rec = EX.account_record(store.get_user(uid))
    assert rec["signup_source"] == "cairn"
    assert rec["cross_product_events"] == [{"event": XP.SHOWN, "day": XP.today(), "n": 1}]
    out = store.delete_user(uid)
    assert out["xp_events"] == 1
    con = sqlite3.connect(str(store.path()))
    try:
        assert con.execute("SELECT count(*) FROM xp_events WHERE user_id = ?", (uid,)).fetchone()[0] == 0
    finally:
        con.close()


def test_an_older_accounts_db_gets_the_table_and_the_column(tmp_path, monkeypatch):
    from backend.accounts import store
    db = tmp_path / "accounts.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL DEFAULT 'user',
            google_sub TEXT UNIQUE, email TEXT, email_verified INTEGER NOT NULL DEFAULT 0, display_name TEXT,
            tenant_dir TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_login_at TEXT, disabled_at TEXT,
            disabled_reason TEXT, plan TEXT NOT NULL DEFAULT 'free', plan_expires_at TEXT);
        INSERT INTO users(id, role, created_at, tenant_dir) VALUES (1, 'owner', '2026-01-01', '/x'),
                                                                   (2, 'user', '2026-02-01', 'users/2');
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(store, "DB_PATH", db)
    monkeypatch.setattr(store, "_READY", set())
    store.init()
    assert store.signup_source(2) is None and store.get_user(2) is not None
    assert store.xp_add(2, "xp-card-shown", "2026-10-20", 5) == 1
    assert store.set_signup_source(2, "cairn") and store.signup_source(2) == "cairn"


# ---------------------------------------------------------------------------- the monthly query

def _fixture_db(path):
    from backend.accounts import store
    store.DB_PATH = path
    store._READY.clear()
    store.init()
    con = sqlite3.connect(str(path))
    rows = [  # id, created, last login, source, test
        (1, "2026-01-01T00:00:00+00:00", "2026-12-30T00:00:00+00:00", None, 0),       # the owner: left out
        (2, "2026-10-02T00:00:00+00:00", "2026-11-20T00:00:00+00:00", "cairn", 0),    # from Cairn, linked, kept
        (3, "2026-09-01T00:00:00+00:00", "2026-10-10T00:00:00+00:00", None, 0),       # saw, never pressed: gone
        (4, "2026-09-01T00:00:00+00:00", "2026-10-05T00:00:00+00:00", None, 0),       # saw, pressed, dismissed
        (5, "2026-09-01T00:00:00+00:00", None, None, 0),                              # saw (control), seen by session
        (6, "2026-09-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00", None, 1),       # a test account: left out
        (7, "2026-11-25T00:00:00+00:00", "2026-11-26T00:00:00+00:00", "cairn", 0),    # from Cairn, 30 days not over
        (8, "2026-10-06T00:00:00+00:00", "2026-10-20T00:00:00+00:00", "share", 0),    # from a share page: gone
        (9, "2026-10-07T00:00:00+00:00", "2026-11-30T00:00:00+00:00", "share", 0),    # from a share page: kept
    ]
    for uid, created, last, src, test in rows:
        con.execute("INSERT INTO users(id, role, created_at, last_login_at, tenant_dir, signup_source, is_test) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (uid, "owner" if uid == 1 else "user", created, last,
                                                    f"users/{uid}", src, test))
    con.execute("INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at) "
                "VALUES ('h5', 5, '2026-10-03', '2026-11-15T03:00:00+00:00', '2026-12-15')")
    ev = [(1, "xp-card-shown", "2026-10-01", 9),
          (2, "xp-card-shown", "2026-10-02", 1), (2, "xp-card-click", "2026-10-02", 1),
          (2, "xp-linked", "2026-10-03", 1),
          (3, "xp-card-shown", "2026-10-01", 3), (3, "xp-card-shown", "2026-11-02", 2),
          (4, "xp-card-shown", "2026-10-04", 1), (4, "xp-card-click", "2026-10-04", 1),
          (4, "xp-card-dismiss", "2026-10-04", 1),
          (5, "xp-card-shown", "2026-10-05", 1),
          (6, "xp-card-shown", "2026-10-05", 4)]
    con.executemany("INSERT INTO xp_events(user_id, event, day, n) VALUES (?, ?, ?, ?)", ev)
    con.commit()
    con.close()


def test_the_monthly_funnel_and_the_30_day_retention(tmp_path, monkeypatch):
    from backend.accounts import store
    from backend.scripts import xp_funnel as F
    monkeypatch.setattr(store, "DB_PATH", None)
    monkeypatch.setattr(store, "_READY", set())
    db = tmp_path / "accounts.db"
    _fixture_db(db)
    rows = F.report(F.read(db), today=dt.date(2026, 12, 10))
    by = {r["month"]: r for r in rows}
    assert sorted(by) == ["2026-10", "2026-11"]
    oct_ = by["2026-10"]
    assert oct_["funnel"] == {"saw": 4, "shown_times": 6, "clicked": 2, "dismissed": 1, "linked": 1,
                              "click_rate": 0.5, "link_rate": 0.25}
    assert oct_["from_cairn"] == 1 and oct_["from_share"] == 2
    assert oct_["retained_30d"]["from_share"] == {"n": 2, "kept": 1, "waiting": 0}
    # linked: user 2 (seen 11-20 ≥ 10-03 + 30) kept; from Cairn: user 2 (created 10-02) kept
    assert oct_["retained_30d"]["linked"] == {"n": 1, "kept": 1, "waiting": 0}
    assert oct_["retained_30d"]["from_cairn"] == {"n": 1, "kept": 1, "waiting": 0}
    # control = saw, never pressed nor linked: user 3 (last 10-10 < 10-31: gone), user 5 (session 11-15 ≥ 11-04)
    assert oct_["retained_30d"]["control"] == {"n": 2, "kept": 1, "waiting": 0}
    nov = by["2026-11"]
    assert nov["funnel"]["saw"] == 0 and nov["funnel"]["shown_times"] == 2   # user 3's later shows: times only
    assert nov["from_cairn"] == 1
    assert nov["retained_30d"]["from_cairn"] == {"n": 1, "kept": 0, "waiting": 1}
    # one month, as text and JSON from the command line
    assert F.report(F.read(db), today=dt.date(2026, 12, 10), month="2026-10") == [oct_]
    out = F.text(rows)
    assert "看到小卡 4 人（出現 6 次）" in out and "連結 Cairn 1 人（25%）" in out and "還有 1 人未滿 30 天" in out
    assert "從 Cairn 來 1 個、從賽事分享頁來 2 個" in out and "從賽事分享頁來的新帳號：1 / 2（50%）" in out
    assert F.main(["--accounts-db", str(db), "--month", "2026-10", "--json", "--today", "2026-12-10"]) == 0
    assert F.main(["--accounts-db", str(tmp_path / "missing.db")]) == 2
