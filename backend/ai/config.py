"""The AI 問答's settings (SP-785): the owner's key, and a member's own key (SP-883).

Who may ask (allowed()):
  * the owner (tenancy `ai.owner_key`), with the owner's key — as in SP-785 (SP-306 Q1-1 C);
  * a signed-in member (a USER tenant with the `ai` cap) with **their own** key (SP-883; SP-306 Q1-1
    A; SP-883 Q1-1 A: every member, free ones too) — only while the server's switch
    TRC_AI_MEMBER_KEYS is on (default off; SP-883 Q1-2 A: until SP-881 says COROS / Garmin data may
    go to a model). The demo, a coach viewing a student (the 代看 middleware never lets
    /api/v1/ai/* through, accounts/viewing.py) and the signed-out have none of it.

The key, read in this order:
  1. the owner only: env TRC_ANTHROPIC_API_KEY, or the file TRC_ANTHROPIC_API_KEY_FILE names (a
     Docker secret; settings/env_secrets.py — so applog.redact blanks it too). A member never gets
     it: the server's key is the owner's money
  2. the key pasted on the settings page: sealed (settings/secrets.seal, the server's Fernet key)
     in <their own folder>/ai.json (the owner's folder for the owner, users/<id>/ for a member);
     never returned by any API (only its last 4 characters), never logged (env_secrets._extra puts
     the owner's, and a member's once unsealed in this request — a contextvar, no tenant read per
     log line — among the values applog.redact blanks), never in an export or a backup
     (data_registry: SECRET)

<their folder>/ai.json also holds the switch (設定 › 進階 › AI 問答; the owner's default on, a
member's on once they save a key). The model and the daily limit are the server's: TRC_AI_MODEL
(default DEFAULT_MODEL), TRC_AI_DAILY_LIMIT (default DEFAULT_DAILY_LIMIT) — each person's count in
their own <folder>/ai_usage.json (with the day's 「測試」 count, KEY_TESTS_PER_DAY).

The old athlete_settings.ai_api_key (SP-306 Q1-4, SP-48) is not read: init_db empties and drops it
(db/database.RETIRED_COLUMNS) — the new key lives only here, sealed.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ENV_KEY = "TRC_ANTHROPIC_API_KEY"
ENV_MODEL = "TRC_AI_MODEL"
ENV_LIMIT = "TRC_AI_DAILY_LIMIT"
ENV_MEMBERS = "TRC_AI_MEMBER_KEYS"    # SP-883: members may use their own key (1 / true / yes / on); default off
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_DAILY_LIMIT = 30
MAX_DAILY_LIMIT = 1000
FILE_NAME = "ai.json"
USAGE_NAME = "ai_usage.json"
KEY_MIN, KEY_MAX = 20, 300            # an Anthropic key is ~108 characters
KEY_TESTS_PER_DAY = 10                # 「測試」 on the settings page, a person's checks a day (no key-checking oracle)

_lock = threading.Lock()


class OwnerOnly(PermissionError):
    """The AI key and switch are the owner's (tenancy `ai.owner_key`) — or, with TRC_AI_MEMBER_KEYS
    on, a signed-in member's own (SP-883)."""


def members_on() -> bool:
    """The server's switch for members' own keys (SP-883 Q1-2 A; default off)."""
    return (os.environ.get(ENV_MEMBERS) or "").strip().lower() in ("1", "true", "yes", "on")


def is_member() -> bool:
    """The request is a signed-in member's, using their own key (SP-883)."""
    from backend import tenancy
    t = tenancy.current()
    return t.kind == tenancy.USER and t.can("ai") and not tenancy.demo_mode() and members_on()


def _is_owner() -> bool:
    from backend import tenancy
    return tenancy.is_owner() and tenancy.can("ai.owner_key") and not tenancy.demo_mode()


def allowed() -> bool:
    return _is_owner() or is_member()


def _need_owner() -> None:
    if not allowed():
        raise OwnerOnly("the AI key is the owner's (ai.owner_key) or a member's own (TRC_AI_MEMBER_KEYS)")


def owner_path() -> Path:
    from backend import tenancy
    return tenancy.owner().root / FILE_NAME


def _home() -> Path:
    """The folder of whoever asks: a member's own (users/<id>/), else the owner's."""
    from backend import tenancy
    return tenancy.current().root if is_member() else tenancy.owner().root


def path() -> Path:
    return _home() / FILE_NAME


def usage_path() -> Path:
    return _home() / USAGE_NAME


def _read(p: Optional[Path] = None) -> dict:
    try:
        d = json.loads((p or path()).read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


# ---------------------------------------------------------------------------------------- the key

def _env_key() -> Optional[str]:
    from backend.settings.env_secrets import secret_env
    return secret_env(ENV_KEY)


def _saved_key() -> Optional[str]:
    p = path()
    sealed = _read(p).get("api_key")
    if not sealed:
        return None
    from backend.settings import secrets
    try:
        v = secrets.unseal(sealed) or None
    except secrets.SecretError:
        log.warning("the saved AI key can't be decrypted with the current server key: paste it again")
        return None
    if v and p != owner_path():
        _remember(v)                     # a member's key, now in this request's memory: the log mask knows it
    return v


def api_key() -> Optional[str]:
    """The key to call the model with: the owner's (env, else saved) or a member's own saved one —
    never the server's env key for a member; None for anyone else or when none is set."""
    if is_member():
        return _saved_key()
    if not allowed():
        return None
    return _env_key() or _saved_key()


def key_source() -> str:
    """env | saved | none (what the settings page says — never the value)."""
    if is_member():
        return "saved" if _saved_key() else "none"
    if not allowed():
        return "none"
    if _env_key():
        return "env"
    return "saved" if _saved_key() else "none"


def masked() -> Optional[str]:
    k = api_key()
    return ("…" + k[-4:]) if k and len(k) > 8 else None


def save_key(key: str) -> None:
    """Seal and keep the pasted key ('' clears it). The value is never logged."""
    _need_owner()
    key = (key or "").strip()
    if key and (not (KEY_MIN <= len(key) <= KEY_MAX) or any(c.isspace() for c in key)):
        raise ValueError("bad key")
    from backend.settings import secrets
    member = is_member()
    with _lock:
        d = _read()
        if key:
            d["api_key"] = secrets.seal(key)
            if member and "enabled" not in d:      # a member's first key turns 「問 AI」 on
                d["enabled"] = True
        else:
            d.pop("api_key", None)
        _write(d)
    log.info("AI key %s (%s)", "saved (sealed)" if key else "cleared", "member" if member else "owner")


def sealed_saved() -> bool:
    """The owner's sealed key is on disk (settings/secrets.ciphertext_exists: no new server key over
    it). A member's key exists only in multi-user mode, where the server key must come from the
    environment (secrets.require_server_key): no key file is ever generated over it."""
    from backend.settings.secrets import PREFIX
    return str(_read(owner_path()).get("api_key") or "").startswith(PREFIX)


_busy = threading.local()
_redact_memo: dict = {}                # str(path) -> (mtime_ns, the value): one unseal per change, not per log line
REDACT_MEMO_MAX = 256


def _redact_one(p: Path) -> Optional[str]:
    try:
        stamp = p.stat().st_mtime_ns
    except OSError:
        return None
    hit = _redact_memo.get(str(p))
    if hit is not None and hit[0] == stamp:
        return hit[1]
    sealed = _read(p).get("api_key")
    from backend.settings import secrets
    v = secrets.unseal(sealed) if sealed else None
    if len(_redact_memo) >= REDACT_MEMO_MAX:
        _redact_memo.clear()
    _redact_memo[str(p)] = (stamp, v)
    return v


# A member's key in clear exists only inside the request that unsealed it (_saved_key: to call the
# model, test it, or show its last 4): it is noted here for that context — a request's context is
# its own copy (Starlette / AnyIO), so it is gone when the request ends. The log mask reads only
# this and the owner's file: a log line never reads a tenant or a member's folder (a public page's
# request touches no tenant, test_intro_sp637).
_member_keys: ContextVar[frozenset] = ContextVar("trc_ai_member_keys", default=frozenset())


def _remember(key: str) -> None:
    cur = _member_keys.get()
    if key not in cur:
        _member_keys.set(cur | {key})


def redact_values() -> set:
    """The saved keys applog.redact blanks (env_secrets._extra): the owner's (whoever's request logs)
    and any member's key unsealed in this request (SP-883: a member's key only travels in their own
    requests). Never reads a tenant; never raises."""
    out = set(_member_keys.get())
    v = redact_value()
    if v:
        out.add(v)
    return out


def redact_value() -> Optional[str]:
    """The owner's saved key (SP-785's single value); redact_values has the member's too."""
    if getattr(_busy, "on", False):
        return None
    _busy.on = True
    try:
        return _redact_one(owner_path())
    except Exception:                    # noqa: BLE001
        return None
    finally:
        _busy.on = False


# ------------------------------------------------------------------------- the switch, model, limit

def enabled() -> bool:
    """設定 › 進階 › AI 問答: the owner's default on; a member's off until their first key (SP-883: no
    「問 AI」 on every page of a member without a key); False for anyone else."""
    if not allowed():
        return False
    d = _read()
    v = d.get("enabled")
    if v is None:
        return bool(d.get("api_key")) if is_member() else True
    return bool(v)


def set_enabled(on: bool) -> None:
    _need_owner()
    with _lock:
        d = _read()
        d["enabled"] = bool(on)
        _write(d)


def model() -> str:
    v = (os.environ.get(ENV_MODEL) or "").strip()
    return v if v and len(v) <= 80 and all(c.isalnum() or c in "-._:" for c in v) else DEFAULT_MODEL


def daily_limit() -> int:
    try:
        n = int((os.environ.get(ENV_LIMIT) or "").strip() or DEFAULT_DAILY_LIMIT)
    except ValueError:
        return DEFAULT_DAILY_LIMIT
    return max(1, min(MAX_DAILY_LIMIT, n))


# -------------------------------------------------------------------------------- the daily count

def _today() -> str:
    import datetime as dt
    return dt.date.today().isoformat()


def _usage() -> dict:
    d = _read(usage_path())
    return d if d.get("day") == _today() else {"day": _today()}


def _write_usage(d: dict) -> None:
    p = usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), "utf-8")


def used_today() -> int:
    return int(_usage().get("n") or 0)


def count_one() -> int:
    """One model call made today (the cache's answers are not counted); the new count."""
    with _lock:
        d = _usage()
        d["n"] = int(d.get("n") or 0) + 1
        _write_usage(d)
    return d["n"]


def count_key_test() -> bool:
    """One 「測試」 of the key today; False past KEY_TESTS_PER_DAY (and then no call is made)."""
    with _lock:
        d = _usage()
        n = int(d.get("tests") or 0)
        if n >= KEY_TESTS_PER_DAY:
            return False
        d["tests"] = n + 1
        _write_usage(d)
    return True


def state() -> dict:
    """GET /api/v1/ai/settings: never the key itself. `who`: owner | member (the page's wording)."""
    return {"enabled": enabled(), "key": {"source": key_source(), "masked": masked()},
            "model": model(), "daily_limit": daily_limit(), "used_today": used_today(),
            "who": "member" if is_member() else "owner"}
