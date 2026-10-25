"""SP-858: 設定 › 帳號 › 密碼 — a signed-in e-mail account changes its password.

The current password first, the new one under the sign-up's rules; afterwards the new password signs
in and the old one doesn't; every other session of the account ends, the one that asked stays; an
unused reset link stops working. A Google account has no password here (409)."""
from __future__ import annotations

import json

from backend.accounts import store
from backend.tests.test_email_signup_sp610 import PW, link, outbox, post, signed_up  # noqa: F401 — fixtures
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, sign_up, started  # noqa: F401 — fixtures

NEW = "a brand new passphrase"


def change(cl, current, password):
    return cl.post("/api/v1/account/email/password", json={"current": current, "password": password},
                   headers={"X-TRC-CSRF": csrf(cl)})


def test_an_email_account_changes_its_password_and_the_new_one_signs_in(c, app, outbox):
    u = signed_up(c, outbox)
    other = browser(app, "203.0.113.170")                       # another device of the same account
    assert post(other, "login", {"email": "runner@example.com", "password": PW}).json()["ok"] is True
    assert other.get("/api/v1/plan").status_code == 200

    r = change(c, "wrong password 1", NEW)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "PASSWORD_WRONG"
    r = change(c, PW, "short")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "PASSWORD_TOO_SHORT"
    r = change(c, PW, "runner@example.com")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "PASSWORD_WEAK"
    assert other.get("/api/v1/plan").status_code == 200          # refused changes end nothing

    r = change(c, PW, NEW)
    assert r.status_code == 200 and r.json() == {"ok": True, "signed_out": 1}, r.text
    assert c.get("/api/v1/session").json()["user"]["id"] == u.id   # this device stays signed in
    assert other.get("/api/v1/plan").status_code == 401           # the other one is signed out

    b = browser(app, "203.0.113.171")
    r = post(b, "login", {"email": "runner@example.com", "password": PW})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "LOGIN_FAILED"
    r = post(b, "login", {"email": "runner@example.com", "password": NEW})
    assert r.json() == {"ok": True, "next": "/"} and b.get("/api/v1/session").json()["user"]["id"] == u.id


def test_a_pending_reset_link_stops_working_after_a_change(c, app, outbox):
    signed_up(c, outbox)
    post(browser(app, "203.0.113.172"), "forgot", {"email": "runner@example.com"})
    kind, token = link(outbox[-1])
    assert kind == "reset-password"
    assert change(c, PW, NEW).status_code == 200
    r = post(browser(app, "203.0.113.173"), "reset", {"token": token, "password": "someone else's passphrase"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "LINK_INVALID"
    assert store.email_account("runner@example.com")["reset_hash"] is None


def test_a_google_account_has_no_password_to_change(c, app, outbox):
    sign_up(c, "g-gwen", "gwen@example.com")
    r = change(c, "anything long", NEW)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_EMAIL_SIGN_IN"
    # signed out: the gate answers first
    assert browser(app, "203.0.113.174").post("/api/v1/account/email/password", json={},
                                              headers={"X-TRC-CSRF": csrf(c)}).status_code in (401, 403)


def test_guessing_the_current_password_is_limited(c, app, outbox):
    signed_up(c, outbox)
    codes = [change(c, f"wrong password {i}", NEW).status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_owner_mode_has_no_password_change(monkeypatch, tmp_path):
    from backend.tests.test_login_sp58 import _clean_env, _client
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        assert cl.post("/api/v1/account/email/password", json={}).status_code == 404


def test_the_password_block_texts_are_in_both_languages():
    from backend.i18n.pages import CATALOG_DIR
    zh = json.loads((CATALOG_DIR / "zh-TW" / "settings.json").read_text("utf-8"))
    en = json.loads((CATALOG_DIR / "en" / "settings.json").read_text("utf-8"))
    for k in ("pwc_title", "pwc_intro", "pwc_current", "pwc_new", "pwc_again", "pwc_go", "pwc_fill",
              "pwc_mismatch", "pwc_done", "pwc_done_out"):
        k = "acct." + k
        assert zh[k] and en[k] and zh[k] != en[k], k
