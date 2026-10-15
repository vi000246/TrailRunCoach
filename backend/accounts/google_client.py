"""
The Google OAuth client set on the admin page (SP-351 R2): its id and secret, in site.json.

Where the sign-in's client comes from, each part on its own (google.client_id / client_secret):
  1. the admin page (site.json `google.client_id`, `google.client_secret`)     source "site"
  2. GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (or *_FILE), read once at start-up  source "env"
Clearing the page's value falls back to the environment. site.json is read through its mtime
cache, so a change is used from the next sign-in on — no restart.

The secret is stored sealed with the server key (settings/secrets.py, "enc:v1:" + Fernet) and is
never sent back: the page and the API only say whether it is set and where it comes from. The
change log says "the secret was updated / cleared" — never a value. The client id is not a secret
(every sign-in sends it to the browser in Google's URL), so the page shows it.

Start-up (accounts/login.py _decide): a part the page holds counts as set, so a server whose
environment has no Google client but whose page has one starts in sign-in mode; with neither
the rule is as before (LoginConfigError).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from backend import site_settings as SS

log = logging.getLogger(__name__)

KEY_ID = "google.client_id"
KEY_SECRET = "google.client_secret"
ENV_ID, ENV_SECRET = "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"
MAX_LEN = 300
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_WARNED: set = set()


class ClientError(ValueError):
    def __init__(self, key: str, code: str):
        super().__init__(f"{key}: {code}")
        self.key, self.code = key, code


def site_id() -> Optional[str]:
    v = SS.get(KEY_ID)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _sealed() -> Optional[str]:
    v = SS.get(KEY_SECRET)
    return v if isinstance(v, str) and v else None


def site_secret() -> Optional[str]:
    """The page's secret, unsealed; None when there is none or it can't be opened with this
    server's key (logged once; the environment's is used then)."""
    v = _sealed()
    if v is None:
        return None
    from backend.settings import secrets
    try:
        out = secrets.unseal(v)
    except Exception as e:                       # noqa: BLE001 — a wrong / missing key: as if unset
        if v not in _WARNED:
            _WARNED.add(v)
            log.warning("the admin page's Google client secret can't be opened with this server's key (%s): "
                        "the environment's is used; set it again on the admin page", type(e).__name__)
        return None
    return out or None


def _env_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip() or (os.environ.get(name + "_FILE") or "").strip())


def _env_parts() -> tuple:
    """(id, secret) the environment gave at start-up (sign-in mode), else whether it names them."""
    from backend.accounts import google, login
    if login.mode() == login.SIGNIN:
        return google.env_client()
    return (os.environ.get(ENV_ID) or "").strip() or None, _env_set(ENV_SECRET) or None


def view() -> dict:
    """What the admin page shows: the id in force and where it comes from; the secret only as
    set / not set (and where from) — never its value."""
    from backend.accounts import google, login
    env_id, env_secret = _env_parts()
    sid, sealed = site_id(), _sealed()
    readable = sealed is not None and site_secret() is not None
    return {
        "signin": login.mode() == login.SIGNIN,
        "redirect_uri": google.redirect_uri() if google.public_url() else None,
        "client_id": {"value": sid or env_id, "source": "site" if sid else "env" if env_id else None,
                      "site": sid is not None, "env": bool(env_id or _env_set(ENV_ID))},
        "client_secret": {"set": bool(readable or env_secret),
                          "source": "site" if readable else "env" if env_secret else None,
                          "site": sealed is not None, "unreadable": sealed is not None and not readable,
                          "env": bool(env_secret)},
    }


def _clean(key: str, v) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ClientError(key, "BAD_VALUE")
    v = v.strip()
    if not v:
        return None
    if len(v) > MAX_LEN:
        raise ClientError(key, "TOO_LONG")
    if key == KEY_ID and not _ID_RE.match(v):
        raise ClientError(key, "BAD_VALUE")
    if key == KEY_SECRET and any(c.isspace() for c in v):
        raise ClientError(key, "BAD_VALUE")
    return v


def set_client(changes: dict, actor: Optional[int]) -> list:
    """Apply {"client_id"?: str | None, "client_secret"?: str | None} (None / "" = clear the page's
    value: back to the environment). Every part is checked before anything is written; in sign-in
    mode the result must still name a whole client (ClientError GOOGLE_INCOMPLETE otherwise: the
    sign-in would stop working for everyone). Logged: the id's old and new value; the secret as
    "updated" / "cleared" only. Returns the log rows written."""
    from backend import site_config as SC
    from backend.accounts import login
    from backend.settings import secrets
    unknown = set(changes) - {"client_id", "client_secret"}
    if unknown:
        raise ClientError(sorted(unknown)[0], "UNKNOWN_KEY")
    new_id = _clean(KEY_ID, changes["client_id"]) if "client_id" in changes else site_id()
    has_secret = "client_secret" in changes
    new_secret = _clean(KEY_SECRET, changes["client_secret"]) if has_secret else None
    if login.mode() == login.SIGNIN:
        env_id, env_secret = _env_parts()
        secret_after = (new_secret if has_secret else site_secret()) or env_secret
        if not ((new_id or env_id) and secret_after):
            raise ClientError("client_secret" if new_id or env_id else "client_id", "GOOGLE_INCOMPLETE")
    writes: dict = {}
    rows = []
    old_id = site_id()
    if "client_id" in changes and new_id != old_id:
        writes[KEY_ID] = new_id
    old_sealed = _sealed()
    secret_action = None
    if has_secret:
        if new_secret is None and old_sealed is not None:
            writes[KEY_SECRET], secret_action = None, "google.secret_cleared"
        elif new_secret is not None and (old_sealed is None or site_secret() != new_secret):
            writes[KEY_SECRET], secret_action = secrets.seal(new_secret), "google.secret"
    if not writes:
        return []
    SS.update(writes)
    if KEY_ID in writes:
        rows.append(SC.audit("google.client_id", "google", old_id, new_id, actor))
    if secret_action:
        rows.append(SC.audit(secret_action, "google", None, None, actor))
    log.info("the admin page changed the Google client (%s)", ", ".join(
        k for k, on in (("id", KEY_ID in writes), ("secret", secret_action)) if on))
    return rows
