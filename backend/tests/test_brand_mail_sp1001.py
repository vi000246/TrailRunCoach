"""SP-1001: the mails, their sender and the export's README name the product Trig (SP-971) — 「Trig 基石」
in Chinese, 「Trig」 in English — through the one name key (pages.current_brand()), never the old name.
The mails are captured (the outbox fixture): nothing is sent."""
from __future__ import annotations

import json

from backend import i18n
from backend.accounts import email_login as EL, export as X, mailer
from backend.i18n import pages as P
from backend.tests.test_email_signup_sp610 import REAL_DELIVER, outbox  # noqa: F401 — fixtures
from backend.tests.test_login_sp58 import app, c, env, started  # noqa: F401 — fixtures

OLD = "TrailRunCoach"


def _all_mails(run: str):
    """Each mail once, each to its own address (an address gets 3 mails an hour: MAIL_ADDRESS_LIMIT)."""
    EL._mail_verify(f"a{run}@example.com", "Ann", "tok", "/")           # 註冊確認
    EL._mail_exists(f"b{run}@example.com", google_account=False)       # 已有帳號
    EL._mail_exists(f"c{run}@example.com", google_account=True)        # 已有帳號（Google 註冊的）
    EL._mail_reset(f"d{run}@example.com", "tok2")                      # 重設密碼
    EL._mail_google_only(f"e{run}@example.com")                        # Google 登入提醒
    EL._mail_password_changed(f"f{run}@example.com", "Ann")            # 密碼已改（SP-959）


def test_every_mail_names_trig_in_its_language(outbox, started):
    for loc, name in (("zh-TW", "Trig 基石"), ("en", "Trig")):
        outbox.clear()
        with i18n.use_locale(loc):
            _all_mails(loc)
        assert [m.kind for m in outbox] == ["verify", "exists", "exists", "reset", "google", "changed"], loc
        for m in outbox:
            assert name in m.subject and name in m.text, (loc, m.kind, m.subject)
            assert OLD not in m.subject and OLD not in m.text, (loc, m.kind)
            assert "{brand}" not in m.subject + m.text, (loc, m.kind)
            if loc == "en":
                assert "基石" not in m.subject + m.text, m.kind
    with i18n.use_locale("zh-TW"):
        outbox.clear()
        _all_mails("zh2")
    subjects = [m.subject for m in outbox]
    assert subjects == ["確認你的 email · Trig 基石", "你已經有 Trig 基石帳號", "你已經有 Trig 基石帳號",
                        "重設 Trig 基石密碼", "Trig 基石登入方式", "你的 Trig 基石密碼已經改了"]
    with i18n.use_locale("en"):
        outbox.clear()
        _all_mails("en2")
    assert [m.subject for m in outbox] == [
        "Confirm your email · Trig", "You already have a Trig account", "You already have a Trig account",
        "Reset your Trig password", "How you sign in to Trig", "Your Trig password was changed"]


def test_the_sender_is_trig(monkeypatch):
    assert mailer.DEFAULT_FROM == "Trig <no-reply@yichlin.com>"
    monkeypatch.setattr(mailer, "deliver", REAL_DELIVER)
    calls = []
    monkeypatch.setattr(mailer, "_post", lambda url, headers, body, timeout: calls.append(json.loads(body)) or (200, b"{}"))
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_not_real_123")
    monkeypatch.delenv("TRC_MAIL_FROM", raising=False)            # no override: the default sender
    assert mailer.deliver(mailer.Mail("someone@example.com", "s", "t", "verify"))
    assert calls[0]["from"] == "Trig <no-reply@yichlin.com>"


def test_the_export_readme_names_trig():
    for loc in ("zh-TW", "en"):
        with i18n.use_locale(loc):
            text = X._readme().decode("utf-8")
        assert text.splitlines()[0] == {"zh-TW": "這是你在 Trig 基石的資料（格式 export-1）。",
                                        "en": "Your data at Trig (format export-1)."}[loc]
        assert OLD not in text, loc
    assert P.current_brand() == "Trig 基石"
    with i18n.use_locale("en"):
        assert P.current_brand() == "Trig"
