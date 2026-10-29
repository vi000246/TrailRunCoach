"""SP-610: signing up and in with an e-mail address and a password, next to Google's sign-in.

Sign up -> a mail with a link to confirm the address -> the link + the password -> the terms -> the
account (the same cap, waitlist, owner and terms rules as Google's: test_login_sp58). Sign in, forgot
/ reset the password. Whether an address has an account is never told; passwords are Argon2id hashes;
the links are one-time, expire, and only their sha256 is stored; the limits are the sign-in's
windows. The mails are captured (mailer.deliver replaced) — nothing reaches the network or Resend."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

import pytest

from backend.accounts import email_login as EL, mailer, passwords, store
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    CSRF, SESSION, SIGNUP, accept, app, browser, c, csrf, env, location, sign_in, sign_up, started)

PW = "correct horse battery"
REAL_DELIVER = mailer.deliver                 # the outbox fixture replaces it; the mailer tests put it back
LINK = re.compile(r"/(verify-email|reset-password)(?:\?[^#\s]*)?#([A-Za-z0-9_\-]+)")


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """Every mail the app hands over, sent at once (mailer.SYNC) into this list."""
    box: list = []
    monkeypatch.setattr(mailer, "SYNC", True)
    monkeypatch.setattr(mailer, "deliver", lambda m: box.append(m) or True)
    return box


def post(cl, path: str, body: dict):
    return cl.post("/api/v1/account/email/" + path, json=body, headers={"X-TRC-CSRF": csrf(cl)})


def link(mail) -> tuple[str, str]:
    m = LINK.search(mail.text)
    assert m, mail.text
    return m.group(1), m.group(2)


def register(cl, email="runner@example.com", password=PW, name="山羊", **kw):
    return post(cl, "register", {"email": email, "password": password, "name": name, **kw})


def signed_up(cl, outbox, email="runner@example.com", password=PW, name="山羊"):
    """Register, confirm with the mailed link, accept the terms: signed in as the new account."""
    r = register(cl, email, password, name)
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/login?sent=register"}, r.text
    kind, token = link(outbox[-1])
    assert kind == "verify-email"
    r = post(cl, "verify", {"token": token, "password": password})
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/signup"}, r.text
    r = accept(cl)
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    return store.find_identity("email", email.lower())


# ---------------------------------------------------------------------------- passwords

def test_passwords_are_argon2id_hashes_and_verify():
    h = passwords.hash_password(PW)
    assert h.startswith("$argon2id$v=19$m=19456,t=2,p=1$") and PW not in h
    assert h != passwords.hash_password(PW)                          # a salt of its own each time
    assert passwords.verify(PW, h)
    assert not passwords.verify("correct horse battery!", h)
    assert not passwords.verify(PW, None) and not passwords.verify(PW, "plain") and not passwords.verify("", h)
    assert not passwords.verify(PW, "$argon2id$broken")
    assert not passwords.needs_rehash(h)
    assert passwords.needs_rehash("$argon2id$v=19$m=4096,t=1,p=1$c2FsdHNhbHQ$aGFzaA")


@pytest.mark.parametrize("pw,email,want", [
    ("short", None, "too_short"), ("x" * 129, None, "too_long"), ("aaaaaaaaaaaa", None, "weak"),
    ("abababababab", None, "weak"), ("password123", None, "weak"), ("1234567890", None, "weak"),
    ("me@example.com", "me@example.com", "weak"), ("mylongname1", "mylongname1@example.com", "weak"),
    (PW, "me@example.com", None), ("跑山的人每天都要練習", None, None),
])
def test_password_strength_floor(pw, email, want):
    assert passwords.problem(pw, email) == want


# ---------------------------------------------------------------------------- the mailer

def test_without_a_key_the_mail_is_printed_not_sent(monkeypatch, capsys, caplog):
    monkeypatch.setattr(mailer, "deliver", REAL_DELIVER)
    calls = []
    monkeypatch.setattr(mailer, "_post", lambda *a: calls.append(a) or (200, b"{}"))
    caplog.set_level(logging.INFO, logger="backend.mail")
    assert mailer.deliver(mailer.Mail("someone@example.com", "主旨", "內文 https://x/verify-email#tok123", "verify"))
    err = capsys.readouterr().err
    assert "DEV MODE — NOT SENT" in err and "someone@example.com" in err and "#tok123" in err
    assert calls == []
    assert "someone@example.com" not in caplog.text and "tok123" not in caplog.text
    assert "dev mode" in caplog.text


def test_with_a_key_resend_gets_one_json_post(monkeypatch, caplog):
    monkeypatch.setattr(mailer, "deliver", REAL_DELIVER)
    calls = []

    def fake(url, headers, body, timeout):
        calls.append((url, headers, json.loads(body), timeout))
        return 200, b'{"id": "abc"}'
    monkeypatch.setattr(mailer, "_post", fake)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_not_real_123")
    monkeypatch.setenv("TRC_MAIL_FROM", "TrailRunCoach <no-reply@coach.example.com>")
    caplog.set_level(logging.INFO, logger="backend.mail")
    assert mailer.deliver(mailer.Mail("someone@example.com", "主旨", "link #tok123", "verify"))
    (url, headers, body, timeout), = calls
    assert url == "https://api.resend.com/emails" and timeout == mailer.TIMEOUT_S
    assert headers["Authorization"] == "Bearer re_test_key_not_real_123" and headers["Content-Type"] == "application/json"
    assert body == {"from": "TrailRunCoach <no-reply@coach.example.com>", "to": ["someone@example.com"],
                    "subject": "主旨", "text": "link #tok123"}
    for s in ("someone@example.com", "tok123", "re_test_key_not_real_123"):
        assert s not in caplog.text
    # Resend refuses / the network fails: False, logged by status or type only
    monkeypatch.setattr(mailer, "_post", lambda *a: (422, b'{"message": "someone@example.com"}'))
    assert not mailer.deliver(mailer.Mail("someone@example.com", "s", "t", "verify"))

    def boom(*a):
        raise TimeoutError("someone@example.com")
    monkeypatch.setattr(mailer, "_post", boom)
    assert not mailer.deliver(mailer.Mail("someone@example.com", "s", "t", "verify"))
    assert "someone@example.com" not in caplog.text and "422" in caplog.text and "TimeoutError" in caplog.text


def test_the_tests_never_reach_resend(monkeypatch):
    """conftest's guard: with a key set and the real deliver, the POST refuses (no network) and the
    mail is reported not sent."""
    monkeypatch.setattr(mailer, "deliver", REAL_DELIVER)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_not_real_123")
    assert mailer.deliver(mailer.Mail("someone@example.com", "s", "t", "verify")) is False


def test_the_mail_is_in_the_page_language(outbox, started):
    from backend.i18n import use_locale
    with use_locale("en"):
        EL._mail_verify("a@example.com", "Ann", "tok", "/")
    with use_locale("zh-TW"):
        EL._mail_reset("b@example.com", "tok2")
    en, zh = outbox
    assert en.subject == "Confirm your email · Trig" and en.text.startswith("Hi Ann,")
    assert "within 24 hours" in en.text and en.text.count("https://testserver/verify-email#tok") == 1
    assert zh.subject == "重設 Trig 基石密碼" and "https://testserver/reset-password#tok2" in zh.text


# ---------------------------------------------------------------------------- sign up, confirm, terms

def test_sign_up_confirm_and_accept_the_terms_makes_the_account(c, app, outbox):
    from backend import accounts
    r = c.get("/register")
    assert r.status_code == 200 and 'id="lg-register"' in r.text and 'id="lg-email-form"' in r.text
    assert c.get("/verify-email").status_code == 200 and c.get("/reset-password").status_code == 200
    assert c.get("/forgot-password").status_code == 200
    r = register(c, "Runner@Example.com ", name="  山羊\x07 ")
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/login?sent=register"}
    assert store.count_users() == 1 and store.find_identity("email", "runner@example.com") is None
    row = store.email_account("runner@example.com")
    assert row["verified_at"] is None and row["user_id"] is None and row["display_name"] == "山羊"
    assert row["password_hash"].startswith("$argon2id$") and PW not in json.dumps(row)
    mail, = outbox
    assert mail.to == "runner@example.com" and mail.kind == "verify"
    kind, token = link(mail)
    assert kind == "verify-email" and token not in json.dumps(row)            # only its sha256 is kept
    assert row["verify_hash"] == store._hash(token)
    # the wrong password: refused, the link still usable
    r = post(c, "verify", {"token": token, "password": "not the password"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "PASSWORD_WRONG"
    r = post(c, "verify", {"token": token, "password": PW})
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/signup"}
    assert SIGNUP in c.cookies and SESSION not in c.cookies
    info = c.get("/api/v1/account/signup").json()
    assert info["kind"] == "signup" and info["name"] == "山羊" and info["email"] == "runner@example.com"
    assert store.count_users() == 1                                        # no account before the terms
    r = accept(c)
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/"}
    u = store.find_identity("email", "runner@example.com")
    assert u is not None and u.email == "runner@example.com" and u.display_name == "山羊" and u.google_sub is None
    assert store.email_account_of(u.id)["email"] == "runner@example.com"
    assert store.consent_version(u.id) is not None
    assert c.get("/api/v1/session").json()["user"]["id"] == u.id
    assert c.get("/api/v1/plan").status_code == 200
    assert (accounts.server_root() / "users" / str(u.id)).is_dir()
    # the link is used up
    r = post(browser(app), "verify", {"token": token, "password": PW})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "LINK_INVALID"


def test_sign_in_with_the_password(c, app, outbox):
    u = signed_up(c, outbox)
    b = browser(app, "203.0.113.20")
    r = post(b, "login", {"email": " RUNNER@example.com", "password": PW, "next": "/api/v1/plan/page"})
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/api/v1/plan/page"}
    assert b.get("/api/v1/session").json()["user"]["id"] == u.id
    # off-site next: never followed
    r = post(browser(app, "203.0.113.21"), "login", {"email": "runner@example.com", "password": PW,
                                                    "next": "https://evil.example/"})
    assert r.json()["next"] == "/"


def test_a_wrong_password_and_an_unknown_address_answer_alike(c, app, outbox):
    signed_up(c, outbox)
    b = browser(app, "203.0.113.30")
    wrong = post(b, "login", {"email": "runner@example.com", "password": "wrong password 1"})
    unknown = post(b, "login", {"email": "nobody@example.com", "password": "wrong password 1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() and wrong.json()["detail"]["code"] == "LOGIN_FAILED"
    assert SESSION not in b.cookies


def test_signing_up_never_tells_whether_the_address_has_an_account(c, app, outbox):
    """A confirmed e-mail account, a Google account's address, the owner's: the same answer as a
    new address; the person who owns it is told by mail, and nothing about the account changes."""
    from backend.tests.test_login_sp58 import OWNER_EMAIL
    u = signed_up(c, outbox)
    old_hash = store.email_account("runner@example.com")["password_hash"]
    sign_up(browser(app, "203.0.113.41"), "g-gina", "gina@example.com")
    n = len(outbox)
    answers = [register(browser(app, f"203.0.113.{50 + i}"), e, "another long password").json()
               for i, e in enumerate(("runner@example.com", "gina@example.com", OWNER_EMAIL, "fresh@example.com"))]
    assert all(a == {"ok": True, "next": "/login?sent=register"} for a in answers)
    exists, google_mail, owner_mail, fresh = outbox[n:]
    assert exists.kind == "exists" and "/forgot-password" in exists.text and not LINK.search(exists.text)
    assert google_mail.kind == "exists" and "Google" in google_mail.text and not LINK.search(google_mail.text)
    assert owner_mail.kind == "exists" and "Google" in owner_mail.text
    assert fresh.kind == "verify"
    assert store.email_account("runner@example.com")["password_hash"] == old_hash
    assert store.email_account("gina@example.com") is None and store.email_account(OWNER_EMAIL) is None
    assert store.get_user(u.id).email == "runner@example.com"


def test_an_unconfirmed_sign_up_taken_over_kills_the_older_link(c, app, outbox):
    """A second sign-up of the same unconfirmed address replaces the password and the link: the
    first link stops working, and a link only confirms with the password it was sent for."""
    register(c, "same@example.com", "first long password")
    _, first = link(outbox[-1])
    register(browser(app, "203.0.113.60"), "same@example.com", "second long password")
    _, second = link(outbox[-1])
    assert post(c, "verify", {"token": first, "password": "first long password"}).json()["detail"]["code"] == "LINK_INVALID"
    assert post(c, "verify", {"token": second, "password": "first long password"}).json()["detail"]["code"] == "PASSWORD_WRONG"
    assert post(c, "verify", {"token": second, "password": "second long password"}).json()["next"] == "/signup"


def test_signing_in_before_confirming_mails_the_link_again(c, app, outbox):
    register(c)
    n = len(outbox)
    r = post(browser(app, "203.0.113.70"), "login", {"email": "runner@example.com", "password": PW})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_UNVERIFIED"
    assert len(outbox) == n + 1 and outbox[-1].kind == "verify"
    # the wrong password does not (whether it is unconfirmed is not told either)
    r = post(browser(app, "203.0.113.71"), "login", {"email": "runner@example.com", "password": "wrong password 1"})
    assert r.status_code == 401 and len(outbox) == n + 1


def test_the_confirmation_link_expires_after_24_hours(c, outbox, monkeypatch):
    register(c)
    _, token = link(outbox[-1])
    later = store._now() + dt.timedelta(hours=24, seconds=1)
    monkeypatch.setattr(store, "_now", lambda: later)
    r = post(c, "verify", {"token": token, "password": PW})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "LINK_INVALID"


@pytest.mark.parametrize("body,code", [
    ({"email": "not-an-address", "password": PW}, "EMAIL_INVALID"),
    ({"email": "a b@example.com", "password": PW}, "EMAIL_INVALID"),
    ({"email": "x@example.com", "password": "short"}, "PASSWORD_TOO_SHORT"),
    ({"email": "x@example.com", "password": "password123"}, "PASSWORD_WEAK"),
    ({"email": "x@example.com", "password": "p" * 200}, "PASSWORD_TOO_LONG"),
])
def test_what_was_typed_is_checked(c, outbox, body, code):
    r = post(c, "register", body)
    assert r.status_code == 400 and r.json()["detail"]["code"] == code and outbox == []


# ---------------------------------------------------------------------------- the same rules as Google's

def test_nobody_signs_up_with_email_before_the_owner_has_signed_in(started, outbox):
    cl = started.main
    register(cl)
    _, token = link(outbox[-1])
    r = post(cl, "verify", {"token": token, "password": PW})
    assert r.json() == {"ok": False, "next": "/login?error=setup"}
    assert store.count_users() == 1 and SESSION not in cl.cookies


def test_when_full_an_email_sign_up_goes_to_the_waitlist(c, app, outbox, monkeypatch):
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "2")                         # the owner + one
    signed_up(c, outbox, "alice@example.com")
    b = browser(app, "203.0.113.80")
    register(b, "carol@example.com")
    _, token = link(outbox[-1])
    r = post(b, "verify", {"token": token, "password": PW})
    assert r.json() == {"ok": False, "next": "/login?error=full"}
    assert "carol@example.com" in [w["email"] for w in store.waitlist()]
    assert store.find_identity("email", "carol@example.com") is None and store.count_users() == 2
    # full between the confirmation and the terms: the waitlist, no account
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "3")
    d = browser(app, "203.0.113.81")
    register(d, "dave@example.com")
    _, token = link(outbox[-1])
    assert post(d, "verify", {"token": token, "password": PW}).json()["next"] == "/signup"
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "2")
    r = accept(d)
    assert r.json() == {"ok": False, "next": "/login?error=full"}
    assert store.find_identity("email", "dave@example.com") is None and SESSION not in d.cookies
    # a place frees up: the confirmed address signs in and goes to the terms
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "3")
    r = post(browser(app, "203.0.113.82"), "login", {"email": "dave@example.com", "password": PW})
    assert r.json()["next"] == "/signup"


def test_an_admitted_waitlisted_person_takes_their_account_with_email(c, app, outbox):
    store.add_waitlist("wendy@example.com")
    wid = store.waitlist()[0]["id"]
    waiting = store.admit_waitlisted(wid)
    signed_up(c, outbox, "wendy@example.com", name="Wendy")
    u = store.find_identity("email", "wendy@example.com")
    assert u.id == waiting.id and u.display_name == "Wendy" and store.count_users() == 2


def test_an_email_account_accepts_new_terms_like_anyone(c, app, outbox, monkeypatch):
    from backend.tests.test_login_sp58 import new_terms
    signed_up(c, outbox)
    new_terms(monkeypatch)
    b = browser(app, "203.0.113.90")
    r = post(b, "login", {"email": "runner@example.com", "password": PW})
    assert r.json()["next"] == "/signup"
    assert b.get("/api/v1/account/signup").json()["kind"] == "reconsent"
    assert accept(b).json()["ok"] is True


def test_a_disabled_email_account_cannot_sign_in(c, app, outbox):
    u = signed_up(c, outbox)
    store.set_disabled(u.id, True, "test")
    r = post(browser(app, "203.0.113.91"), "login", {"email": "runner@example.com", "password": PW})
    assert r.json() == {"ok": False, "next": "/login?error=disabled"}


def test_an_address_google_took_meanwhile_stays_googles(c, app, outbox):
    register(c, "both@example.com")
    _, token = link(outbox[-1])
    sign_up(browser(app, "203.0.113.92"), "g-both", "both@example.com")
    r = post(c, "verify", {"token": token, "password": PW})
    assert r.json() == {"ok": False, "next": "/login?error=google_account"}
    assert store.find_identity("email", "both@example.com") is None


# ---------------------------------------------------------------------------- forgot / reset

def test_forgot_and_reset_the_password(c, app, outbox):
    u = signed_up(c, outbox)
    assert c.get("/api/v1/plan").status_code == 200
    other = browser(app, "203.0.113.100")
    n = len(outbox)
    for e in ("runner@example.com", "nobody@example.com"):
        r = post(other, "forgot", {"email": e})
        assert r.status_code == 200 and r.json() == {"ok": True, "next": "/login?sent=reset"}
    assert len(outbox) == n + 1 and outbox[-1].kind == "reset" and outbox[-1].to == "runner@example.com"
    kind, token = link(outbox[-1])
    assert kind == "reset-password" and store.email_account("runner@example.com")["reset_hash"] == store._hash(token)
    r = post(other, "reset", {"token": token, "password": "short"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "PASSWORD_TOO_SHORT"
    r = post(other, "reset", {"token": token, "password": "a brand new passphrase"})
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/login?reset=1"}
    assert c.get("/api/v1/plan").status_code == 401                       # signed out everywhere
    assert post(other, "reset", {"token": token, "password": "another new passphrase"}).json()["detail"]["code"] == "LINK_INVALID"
    b = browser(app, "203.0.113.101")
    assert post(b, "login", {"email": "runner@example.com", "password": PW}).status_code == 401
    r = post(b, "login", {"email": "runner@example.com", "password": "a brand new passphrase"})
    assert r.json() == {"ok": True, "next": "/"} and b.get("/api/v1/session").json()["user"]["id"] == u.id


def test_a_reset_link_expires_after_an_hour(c, outbox, monkeypatch):
    signed_up(c, outbox)
    post(c, "forgot", {"email": "runner@example.com"})
    _, token = link(outbox[-1])
    later = store._now() + dt.timedelta(hours=1, seconds=1)
    monkeypatch.setattr(store, "_now", lambda: later)
    assert post(c, "reset", {"token": token, "password": "a brand new passphrase"}).json()["detail"]["code"] == "LINK_INVALID"


def test_forgot_for_a_google_account_says_to_use_google(c, app, outbox):
    sign_up(c, "g-gina", "gina@example.com")
    post(browser(app, "203.0.113.110"), "forgot", {"email": "gina@example.com"})
    assert outbox[-1].kind == "google" and not LINK.search(outbox[-1].text)


def test_a_reset_confirms_an_unconfirmed_address(c, app, outbox):
    """Forgot the password before confirming: the reset link came to the inbox, so it confirms it."""
    register(c)
    post(c, "forgot", {"email": "runner@example.com"})
    _, token = link(outbox[-1])
    post(c, "reset", {"token": token, "password": "a brand new passphrase"})
    assert store.email_account("runner@example.com")["verified_at"]
    r = post(browser(app, "203.0.113.111"), "login", {"email": "runner@example.com", "password": "a brand new passphrase"})
    assert r.json()["next"] == "/signup"


# ---------------------------------------------------------------------------- limits, logs, deletion, Cairn

def test_the_11th_email_sign_in_from_one_client_in_15_minutes_is_refused(c, app, outbox):
    b = browser(app, "203.0.113.120")
    for _ in range(10):
        assert post(b, "login", {"email": "x@example.com", "password": "wrong password 1"}).status_code == 401
    r = post(b, "login", {"email": "x@example.com", "password": "wrong password 1"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED"


def test_one_address_is_limited_whatever_the_client(c, app, outbox):
    for i in range(10):
        post(browser(app, f"198.51.100.{i + 1}"), "login", {"email": "victim@example.com", "password": "guess guess 1"})
    r = post(browser(app, "198.51.100.200"), "login", {"email": "victim@example.com", "password": "guess guess 1"})
    assert r.status_code == 429


def test_mails_to_one_address_are_capped_quietly(c, app, outbox):
    for i in range(5):
        r = register(browser(app, f"198.51.100.{i + 10}"), "flood@example.com")
        assert r.json() == {"ok": True, "next": "/login?sent=register"}
    assert len(outbox) == 3                                               # 3 an hour, the rest dropped


def test_sign_ups_from_one_client_are_limited(c, app, outbox):
    b = browser(app, "203.0.113.130")
    for i in range(10):
        assert register(b, f"r{i}@example.com").status_code == 200
    assert register(b, "r11@example.com").status_code == 429


def test_passwords_tokens_and_addresses_never_reach_the_log(c, app, outbox, caplog):
    caplog.set_level(logging.DEBUG)
    signed_up(c, outbox)
    post(c, "forgot", {"email": "runner@example.com"})
    _, token = link(outbox[-1])
    post(c, "reset", {"token": token, "password": "a brand new passphrase"})
    post(browser(app, "203.0.113.140"), "login", {"email": "runner@example.com", "password": "wrong guess 12"})
    text = caplog.text + "".join(str(r.args) for r in caplog.records)
    for s in ("runner@example.com", PW, "a brand new passphrase", "wrong guess 12", token, "山羊"):
        assert s not in text, s
    tokens = [link(m)[1] for m in outbox if LINK.search(m.text)]
    assert tokens and not any(t in text for t in tokens)


def test_the_data_download_has_the_address_never_the_hash(c, outbox):
    from backend.accounts import export as EX
    u = signed_up(c, outbox)
    rec = EX.account_record(u)
    assert rec["email_sign_in"]["email"] == "runner@example.com" and rec["email_sign_in"]["confirmed_at"]
    assert "argon2" not in json.dumps(rec) and "hash" not in json.dumps(rec)


def test_an_email_account_confirms_with_its_password_then_deletes_itself(c, app, outbox):
    """刪除帳號 asks to confirm who it is (SP-386: Google). An e-mail account has no Google account:
    its password confirms it, on this session only."""
    u = signed_up(c, outbox)
    me = c.get("/api/v1/account/me").json()
    assert me["sign_in"] == "email" and me["reauth_ok"] is False and me["can_delete"] is True
    tok = csrf(c)
    r = c.post("/api/v1/account/delete", json={"confirm": True}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "REAUTH_REQUIRED"
    r = c.post("/api/v1/account/email/reauth", json={"password": "wrong password 1"}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "PASSWORD_WRONG"
    assert c.get("/api/v1/account/me").json()["reauth_ok"] is False
    other = browser(app, "203.0.113.160")                          # another session of the same account
    assert post(other, "login", {"email": "runner@example.com", "password": PW}).json()["ok"] is True
    r = c.post("/api/v1/account/email/reauth", json={"password": PW}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 200 and c.get("/api/v1/account/me").json()["reauth_ok"] is True
    assert other.get("/api/v1/account/me").json()["reauth_ok"] is False      # this session only
    r = c.post("/api/v1/account/delete", json={"confirm": True}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 200, r.text
    assert store.get_user(u.id) is None and store.email_account("runner@example.com") is None


def test_a_google_account_has_no_password_to_confirm(c, app, outbox):
    sign_up(c, "g-gina", "gina@example.com")
    assert c.get("/api/v1/account/me").json()["sign_in"] == "google"
    r = c.post("/api/v1/account/email/reauth", json={"password": "anything long"}, headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_EMAIL_SIGN_IN"
    # signed out: the gate answers first
    assert browser(app, "203.0.113.161").post("/api/v1/account/email/reauth", json={"password": "x"},
                                              headers={"X-TRC-CSRF": csrf(c)}).status_code in (401, 403)


def test_deleting_the_account_deletes_its_email_sign_in(c, app, outbox):
    u = signed_up(c, outbox)
    register(browser(app, "203.0.113.150"), "pending@example.com")
    store.delete_user(u.id)
    assert store.email_account("runner@example.com") is None
    assert store.find_identity("email", "runner@example.com") is None
    assert store.email_account("pending@example.com") is not None        # someone else's sign-up stays


def test_an_email_account_cannot_link_cairn_and_is_told_why(c, app, outbox, monkeypatch):
    from backend.accounts import links as LK
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", "cairn-link-secret-not-real")
    u = signed_up(c, outbox)
    st = LK.state(u, LK.CAIRN)
    assert st["available"] is False and st["reason"] == "no_google"
    r = c.post("/api/v1/account/links/cairn", json={"linked": True}, headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_GOOGLE"
    sign_up(browser(app, "203.0.113.151"), "g-gil", "gil@example.com")
    assert LK.state(store.find_identity("google", "g-gil"), LK.CAIRN)["reason"] is None


def test_owner_mode_has_no_email_sign_in(monkeypatch, tmp_path):
    from backend.tests.test_login_sp58 import _clean_env, _client
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        for p in ("/register", "/verify-email", "/reset-password", "/forgot-password"):
            assert cl.get(p).status_code == 404, p
        for p in ("register", "verify", "login", "forgot", "reset"):
            assert cl.post("/api/v1/account/email/" + p, json={}).status_code == 404, p


def test_the_new_login_page_texts_are_in_both_languages():
    from backend.i18n.pages import CATALOG_DIR
    zh = json.loads((CATALOG_DIR / "zh-TW" / "login.json").read_text("utf-8"))
    en = json.loads((CATALOG_DIR / "en" / "login.json").read_text("utf-8"))
    assert set(zh) == set(en)
    for k in ("email.signin", "register.h", "forgot.h", "reset.h", "verify.h", "email.sent.register",
              "email.sent.reset", "email.reset_done", "error.google_account", "register.cairn"):
        assert zh[k] and en[k] and zh[k] != en[k], k
