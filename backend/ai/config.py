"""The AI 問答's settings (SP-785): owner only for now (SP-306 Q1-1 C; a user's own key later, A).

The key, read in this order:
  1. env TRC_ANTHROPIC_API_KEY, or the file TRC_ANTHROPIC_API_KEY_FILE names (a Docker secret;
     settings/env_secrets.py — so applog.redact blanks it too)
  2. the key pasted on the settings page: sealed (settings/secrets.seal, the server's Fernet key)
     in <owner home>/ai.json; never returned by any API (only its last 4 characters), never logged
     (env_secrets._extra puts it among the values applog.redact blanks)

<owner home>/ai.json also holds the switch (設定 › 進階 › AI 問答, default on). The model and the
daily limit are the server's: TRC_AI_MODEL (default DEFAULT_MODEL), TRC_AI_DAILY_LIMIT (default
DEFAULT_DAILY_LIMIT). Every other tenant (a user, the demo, a coach viewing a student) has none of
it: `ai.owner_key` is in tenancy.OWNER_ONLY_CAPS.

The old athlete_settings.ai_api_key (SP-306 Q1-4, SP-48) is not read: init_db empties and drops it
(db/database.RETIRED_COLUMNS) — the new key lives only here, sealed.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ENV_KEY = "TRC_ANTHROPIC_API_KEY"
ENV_MODEL = "TRC_AI_MODEL"
ENV_LIMIT = "TRC_AI_DAILY_LIMIT"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_DAILY_LIMIT = 30
MAX_DAILY_LIMIT = 1000
FILE_NAME = "ai.json"
USAGE_NAME = "ai_usage.json"
KEY_MIN, KEY_MAX = 20, 300            # an Anthropic key is ~108 characters

_lock = threading.Lock()


class OwnerOnly(PermissionError):
    """The AI key and switch are the owner's (tenancy `ai.owner_key`)."""


def allowed() -> bool:
    from backend import tenancy
    return tenancy.is_owner() and tenancy.can("ai.owner_key") and not tenancy.demo_mode()


def _need_owner() -> None:
    if not allowed():
        raise OwnerOnly("the AI key is the owner's (ai.owner_key)")


def path() -> Path:
    from backend import tenancy
    return tenancy.owner().root / FILE_NAME


def usage_path() -> Path:
    from backend import tenancy
    return tenancy.owner().root / USAGE_NAME


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
    sealed = _read().get("api_key")
    if not sealed:
        return None
    from backend.settings import secrets
    try:
        return secrets.unseal(sealed) or None
    except secrets.SecretError:
        log.warning("the saved AI key can't be decrypted with the current server key: paste it again")
        return None


def api_key() -> Optional[str]:
    """The key to call the model with (owner only; None for anyone else or when none is set)."""
    if not allowed():
        return None
    return _env_key() or _saved_key()


def key_source() -> str:
    """env | saved | none (what the settings page says — never the value)."""
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
    with _lock:
        d = _read()
        if key:
            d["api_key"] = secrets.seal(key)
        else:
            d.pop("api_key", None)
        _write(d)
    log.info("AI key %s", "saved (sealed)" if key else "cleared")


def sealed_saved() -> bool:
    """A sealed key is on disk (settings/secrets.ciphertext_exists: no new server key over it)."""
    from backend.settings.secrets import PREFIX
    return str(_read().get("api_key") or "").startswith(PREFIX)


_busy = threading.local()
_redact_memo: dict = {}                # (path, mtime_ns) -> the value (one unseal per change, not per log line)


def redact_value() -> Optional[str]:
    """The saved key's value for applog.redact (env_secrets._extra), whoever's request logs; never raises."""
    if getattr(_busy, "on", False):      # unseal logging a line itself: no loop
        return None
    _busy.on = True
    try:
        p = path()
        try:
            stamp = (str(p), p.stat().st_mtime_ns)
        except OSError:
            return None
        if stamp not in _redact_memo:
            sealed = _read(p).get("api_key")
            from backend.settings import secrets
            _redact_memo.clear()
            _redact_memo[stamp] = secrets.unseal(sealed) if sealed else None
        return _redact_memo[stamp]
    except Exception:                    # noqa: BLE001 — redaction never breaks a log line
        return None
    finally:
        _busy.on = False


# ------------------------------------------------------------------------- the switch, model, limit

def enabled() -> bool:
    """設定 › 進階 › AI 問答 (default on); False for anyone but the owner."""
    if not allowed():
        return False
    v = _read().get("enabled")
    return True if v is None else bool(v)


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


def used_today() -> int:
    d = _read(usage_path())
    return int(d.get("n") or 0) if d.get("day") == _today() else 0


def count_one() -> int:
    """One model call made today (the cache's answers are not counted); the new count."""
    with _lock:
        n = used_today() + 1
        p = usage_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"day": _today(), "n": n}), "utf-8")
    return n


def state() -> dict:
    """GET /api/v1/ai/settings: never the key itself."""
    return {"enabled": enabled(), "key": {"source": key_source(), "masked": masked()},
            "model": model(), "daily_limit": daily_limit(), "used_today": used_today()}
