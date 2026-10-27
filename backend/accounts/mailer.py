"""
Sending the e-mail sign-in's mails (SP-610): the address check, "you already have an account" and
the password reset. One small interface, two ways out:

    mailer.send(Mail(to, subject, text, kind="verify"))

  RESEND_API_KEY set (env or RESEND_API_KEY_FILE; owner 2026-10-20: Resend)
      POST https://api.resend.com/emails with the standard library's HTTP (no SDK), from
      TRC_MAIL_FROM (default DEFAULT_FROM; the domain must be verified at Resend: its SPF / DKIM
      records in the DNS). The log says only that a mail of that kind went out or failed — never
      the address, the subject's link or the token.
  not set (development, or before the key is on the server)
      nothing is sent: the whole mail is printed to stderr (docker logs) under a "DEV MODE — NOT
      SENT" banner, so the flow can be followed without a key. stderr on purpose: the app log
      (applog.redact) would blank the link, which is the point of printing it. The app log gets one
      line, without the address or the link.

The mail goes out in a worker thread (send returns at once): a request answers in the same time
whether a mail was sent or not, so the time taken never tells whether an address has an account.
Tests set SYNC = True (sent before send returns) and replace `deliver` or `_post`; nothing in the
tests reaches the network.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("backend.mail")

ENV_KEY = "RESEND_API_KEY"
ENV_FROM = "TRC_MAIL_FROM"
DEFAULT_FROM = "TrailRunCoach <no-reply@yichlin.com>"
API_URL = "https://api.resend.com/emails"
TIMEOUT_S = 10.0

SYNC = False                       # tests: deliver before send() returns
_POOL: Optional[ThreadPoolExecutor] = None
_POOL_LOCK = threading.Lock()


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str
    kind: str = "mail"             # verify | exists | reset | google | changed — what the log line says, never the content


def api_key() -> Optional[str]:
    from backend.settings.env_secrets import secret_env
    return secret_env(ENV_KEY)


def sender() -> str:
    import os
    v = (os.environ.get(ENV_FROM) or "").strip()
    return v if v and "\n" not in v and "\r" not in v else DEFAULT_FROM


def configured() -> bool:
    """Mails really go out (the key is set)."""
    return bool(api_key())


def warn_if_dev() -> None:
    """At start-up in sign-in mode: say once that the mails only reach the log."""
    if not configured():
        log.warning("%s is not set: the e-mail sign-in's mails are printed to stderr (DEV MODE), "
                    "not sent", ENV_KEY)


# ---------------------------------------------------------------------------- the ways out

def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple:
    """(status, response body) of one HTTPS POST (tests replace this)."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:          # noqa: S310 — a fixed https URL
            return r.status, r.read(4096)
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096) if e.fp else b""


def _resend(mail: Mail, key: str) -> bool:
    body = json.dumps({"from": sender(), "to": [mail.to], "subject": mail.subject, "text": mail.text}).encode()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": "TrailRunCoach-mailer"}
    try:
        status, _ = _post(API_URL, headers, body, TIMEOUT_S)
    except Exception as e:                   # noqa: BLE001 — a network error: logged by type only
        log.warning("mail (%s) not sent: %s", mail.kind, type(e).__name__)
        return False
    if 200 <= status < 300:
        log.info("mail (%s) sent", mail.kind)
        return True
    log.warning("mail (%s) not sent: Resend answered %s", mail.kind, status)
    return False


def _print(mail: Mail) -> bool:
    bar = "=" * 72
    print(f"\n{bar}\nDEV MODE — NOT SENT ({ENV_KEY} is not set). The mail that would go out:\n"
          f"To: {mail.to}\nFrom: {sender()}\nSubject: {mail.subject}\n\n{mail.text}\n{bar}\n",
          file=sys.stderr, flush=True)
    log.info("mail (%s) not sent: dev mode, printed to stderr", mail.kind)
    return True


def deliver(mail: Mail) -> bool:
    """Send one mail now (Resend with a key, else printed). Never raises."""
    try:
        key = api_key()
        return _resend(mail, key) if key else _print(mail)
    except Exception as e:                   # noqa: BLE001 — a mail never breaks the request that asked
        log.warning("mail (%s) not sent: %s", mail.kind, type(e).__name__)
        return False


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="trc-mail")
        return _POOL


def send(mail: Mail) -> None:
    """Hand the mail over and return (SYNC: sent first)."""
    if SYNC:
        deliver(mail)
    else:
        _pool().submit(deliver, mail)
