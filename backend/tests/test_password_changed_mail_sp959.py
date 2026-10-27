"""SP-959: after a password change (設定 › 帳號 › 密碼, SP-858) the address gets a notice.

A change that went through mails one notice (no password in it, the forgot-password page linked, in
the page's language); a refused one (wrong current password, a weak new one, limited) mails nothing;
a mail that can't be handed over leaves the change as it is. The notice isn't held back by the
three-mails-an-hour limit of the address. The mails are captured (the outbox fixture) — nothing is sent."""
from __future__ import annotations

from backend.accounts import email_login as EL, mailer
from backend.tests.test_change_password_sp858 import NEW, change
from backend.tests.test_email_signup_sp610 import PW, outbox, post, signed_up  # noqa: F401 — fixtures
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, started  # noqa: F401 — fixtures

ADDRESS = "runner@example.com"


def notices(box):
    return [m for m in box if m.kind == "changed"]


def test_a_change_mails_one_notice_without_the_password(c, app, outbox):
    signed_up(c, outbox)
    assert change(c, PW, NEW).status_code == 200
    (m,) = notices(outbox)
    assert m.to == ADDRESS and m.subject == "你的 TrailRunCoach 密碼已經改了"
    assert m.text.startswith("山羊 你好：")
    assert "剛在設定頁改了密碼" in m.text and "如果不是你改的" in m.text
    assert "https://testserver/forgot-password" in m.text
    assert PW not in m.text and NEW not in m.text


def test_refused_changes_mail_nothing(c, app, outbox):
    signed_up(c, outbox)
    assert change(c, "wrong password 1", NEW).status_code == 401           # wrong current password
    assert change(c, PW, "short").status_code == 400                       # too short
    assert change(c, PW, ADDRESS).status_code == 400                       # weak
    for i in range(7):                                                     # 10 tries in all, then limited
        assert change(c, f"wrong password {i + 2}", NEW).status_code == 401
    assert change(c, PW, NEW).status_code == 429                           # the right one, but limited
    assert notices(outbox) == []


def test_a_mail_that_fails_leaves_the_change_as_it_is(c, app, outbox, monkeypatch):
    signed_up(c, outbox)

    def broken(mail):
        raise RuntimeError("the mail pool is gone")
    monkeypatch.setattr(mailer, "send", broken)
    r = change(c, PW, NEW)
    assert r.status_code == 200 and r.json() == {"ok": True, "signed_out": 0}, r.text
    b = browser(app, "203.0.113.180")
    assert post(b, "login", {"email": ADDRESS, "password": NEW}).json()["ok"] is True


def test_the_notice_is_in_the_page_language(c, app, outbox):
    signed_up(c, outbox)
    c.cookies.set("lang", "en")
    assert change(c, PW, NEW).status_code == 200
    (m,) = notices(outbox)
    assert m.subject == "Your TrailRunCoach password was changed"
    assert m.text.startswith("Hi 山羊,") and "was just changed in Settings" in m.text
    assert "If it was not you" in m.text and "https://testserver/forgot-password" in m.text


def test_the_address_limit_does_not_hold_the_notice_back(c, app, outbox):
    signed_up(c, outbox)                                                   # 1 mail: the address check
    other = browser(app, "203.0.113.181")
    for _ in range(3):
        post(other, "forgot", {"email": ADDRESS})
    assert [m.kind for m in outbox] == ["verify", "reset", "reset"]        # the third reset dropped
    assert not EL.MAIL_ADDRESS_LIMIT.take(ADDRESS)
    assert change(c, PW, NEW).status_code == 200
    assert len(notices(outbox)) == 1
