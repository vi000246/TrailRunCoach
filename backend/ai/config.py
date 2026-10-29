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

<their folder>/ai.json also holds the switch (設定 › 進階 › AI 問答; default on for the owner and a
member alike — a member without a key gets 「問 AI」 whose dialog asks for the key first). The model and the daily limit are the server's: TRC_AI_MODEL
(default DEFAULT_MODEL), TRC_AI_DAILY_LIMIT (default DEFAULT_DAILY_LIMIT) — each person's count in
their own <folder>/ai_usage.json (with the day's 「測試」 count, KEY_TESTS_PER_DAY).

More sources, in an order (SP-997; providers.py): Qwen (or any model) on the owner's own machine
behind an OpenAI-compatible endpoint (ai.json `local` {url, model}, an optional sealed `local_key`;
the owner only, SP-997 Q1-1), a Gemini key (the owner's: env TRC_GEMINI_API_KEY / _FILE, else sealed
in ai.json `gemini_key`; a member's own, sealed in theirs, SP-997 Q2-2; the model TRC_GEMINI_MODEL)
and the Anthropic key above. sources() lists the ones set up, in ai.json `order` (the owner's; default
DEFAULT_ORDER: the local model first); qa.ask tries them in turn. A member's chain is their own keys
only — Anthropic's, then Gemini's, no order to set — whatever the owner has set up.

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
ENV_GEMINI_KEY = "TRC_GEMINI_API_KEY"   # SP-997: the owner's Gemini key (or _FILE)
ENV_GEMINI_MODEL = "TRC_GEMINI_MODEL"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ORDER = ("local", "anthropic", "gemini")   # SP-997: the owner's own model first (the owner 2026-10-28)
LOCAL_MODEL_MAX = 80
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


def _need_site_owner() -> None:
    """The local model and the order (SP-997 Q1-1): the owner's only."""
    if not _is_owner():
        raise OwnerOnly("the local model and the order are the owner's (SP-997)")


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


def _saved_key(field: str = "api_key") -> Optional[str]:
    p = path()
    sealed = _read(p).get(field)
    if not sealed:
        return None
    from backend.settings import secrets
    try:
        v = secrets.unseal(sealed) or None
    except secrets.SecretError:
        log.warning("the saved AI key (%s) can't be decrypted with the current server key: paste it again", field)
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


def _mask(k: Optional[str]) -> Optional[str]:
    return ("…" + k[-4:]) if k and len(k) > 8 else None


def masked() -> Optional[str]:
    return _mask(api_key())


def _check_key(key: str) -> str:
    key = (key or "").strip()
    if key and (not (KEY_MIN <= len(key) <= KEY_MAX) or any(c.isspace() for c in key)):
        raise ValueError("bad key")
    return key


def _seal_field(field: str, key: str) -> None:
    from backend.settings import secrets
    with _lock:
        d = _read()
        if key:
            d[field] = secrets.seal(key)
        else:
            d.pop(field, None)
        _write(d)


def save_key(key: str) -> None:
    """Seal and keep the pasted key ('' clears it). The value is never logged."""
    _need_owner()
    key = _check_key(key)
    member = is_member()
    _seal_field("api_key", key)
    log.info("AI key %s (%s)", "saved (sealed)" if key else "cleared", "member" if member else "owner")


# --------------------------------------------------------------- more sources, the order (SP-997)

def gemini_key() -> Optional[str]:
    """The Gemini key: the owner's (env TRC_GEMINI_API_KEY or _FILE, else the sealed one) or a
    member's own sealed one (SP-997 Q2-2, as SP-883's Anthropic key: never the server's env key for
    a member); None for anyone else."""
    if is_member():
        return _saved_key("gemini_key")
    if not _is_owner():
        return None
    from backend.settings.env_secrets import secret_env
    return secret_env(ENV_GEMINI_KEY) or _saved_key("gemini_key")


def gemini_source() -> str:
    """env | saved | none (the Gemini key's, for the settings page)."""
    if is_member():
        return "saved" if _saved_key("gemini_key") else "none"
    if not _is_owner():
        return "none"
    from backend.settings.env_secrets import secret_env
    if secret_env(ENV_GEMINI_KEY):
        return "env"
    return "saved" if _saved_key("gemini_key") else "none"


def save_gemini_key(key: str) -> None:
    """Seal and keep the Gemini key ('' clears it) in the asker's own ai.json — the owner's, or a
    member's own (SP-997 Q2-2). Never logged."""
    _need_owner()
    key = _check_key(key)
    member = is_member()
    _seal_field("gemini_key", key)
    log.info("AI Gemini key %s (%s)", "saved (sealed)" if key else "cleared", "member" if member else "owner")


def gemini_model() -> str:
    v = (os.environ.get(ENV_GEMINI_MODEL) or "").strip()
    return v if v and len(v) <= 80 and all(c.isalnum() or c in "-._" for c in v) else DEFAULT_GEMINI_MODEL


def _local_model_ok(m: str) -> bool:
    return 0 < len(m) <= LOCAL_MODEL_MAX and all(c.isalnum() or c in "-._:/" for c in m)


def local() -> Optional[tuple]:
    """(the endpoint's base …/v1, the model) of the owner's local model, or None."""
    if not _is_owner():
        return None
    from backend.ai.providers import local_base
    d = _read().get("local")
    if not isinstance(d, dict):
        return None
    base, m = local_base(str(d.get("url") or "")), str(d.get("model") or "")
    return (base, m) if base and _local_model_ok(m) else None


def set_local(url: str, model_name: str, key: Optional[str] = None) -> None:
    """The owner's local model: an OpenAI-compatible endpoint, its model and (optional) a key the
    endpoint wants — Ollama wants none ('' url clears all three; key None keeps the saved one, ''
    clears it). ValueError on a URL that is not http(s), has user:password in it, or a model name
    that is not letters, digits and -._:/ (qwen2.5:32b, qwen/qwen3-14b)."""
    _need_site_owner()
    from backend.ai.providers import local_base
    from backend.settings import secrets
    url, model_name = (url or "").strip(), (model_name or "").strip()
    base = None
    if url:
        base = local_base(url)
        if not base or not _local_model_ok(model_name):
            raise ValueError("bad local endpoint")
    if key is not None:
        key = (key or "").strip()
        if key and (len(key) > KEY_MAX or any(c.isspace() for c in key)):
            raise ValueError("bad local key")
    with _lock:
        d = _read()
        if base:
            d["local"] = {"url": base, "model": model_name}
            if key:
                d["local_key"] = secrets.seal(key)
            elif key == "":
                d.pop("local_key", None)
        else:
            d.pop("local", None)
            d.pop("local_key", None)
        _write(d)
    log.info("AI local model %s", "set" if base else "cleared")


def local_key() -> Optional[str]:
    """The owner's local endpoint's key, when it wants one (Ollama doesn't)."""
    return _saved_key("local_key") if _is_owner() else None


def _clean_order(v) -> list:
    out = list(dict.fromkeys(k for k in (v if isinstance(v, list) else []) if k in DEFAULT_ORDER))
    return out + [k for k in DEFAULT_ORDER if k not in out]


def order() -> list:
    """The order the sources are tried in (every kind, once): the saved one, else DEFAULT_ORDER."""
    return _clean_order(_read().get("order") if _is_owner() else None)


def set_order(kinds: list) -> None:
    """ValueError unless a list of known kinds, each once (missing ones go last, in DEFAULT_ORDER)."""
    _need_site_owner()
    if not isinstance(kinds, list) or not all(isinstance(k, str) and k in DEFAULT_ORDER for k in kinds) \
            or len(set(kinds)) != len(kinds):
        raise ValueError("bad order")
    with _lock:
        d = _read()
        d["order"] = _clean_order(kinds)
        _write(d)


def sources() -> list:
    """The providers.Source list qa.ask tries, in order: the ones set up only. A member: their own
    keys (purpose ask_own_key) — Anthropic's, then Gemini's (SP-997 Q2-2: no order to set, no local
    model) — nothing of the owner's."""
    from backend.ai.providers import Source
    if is_member():
        out = []
        k = _saved_key()
        if k:
            out.append(Source("anthropic", model(), k, purpose="ask_own_key"))
        g = _saved_key("gemini_key")
        if g:
            out.append(Source("gemini", gemini_model(), g, purpose="ask_own_key"))
        return out
    if not _is_owner():
        return []
    have = {}
    lc = local()
    if lc:
        have["local"] = Source("local", lc[1], local_key(), lc[0])
    k = api_key()
    if k:
        have["anthropic"] = Source("anthropic", model(), k)
    g = gemini_key()
    if g:
        have["gemini"] = Source("gemini", gemini_model(), g)
    return [have[kind] for kind in order() if kind in have]


def source_state() -> list:
    """GET /api/v1/ai/settings `sources`: each kind in order with what the page shows — never a key."""
    if is_member():
        g = gemini_key()
        return [{"kind": "anthropic", "ready": api_key() is not None, "source": key_source(),
                 "masked": masked(), "model": model()},
                {"kind": "gemini", "ready": g is not None, "source": gemini_source(),
                 "masked": _mask(g), "model": gemini_model()}]
    if not _is_owner():
        return []
    out = []
    for kind in order():
        if kind == "local":
            lc = local()
            lk = local_key() if lc else None
            out.append({"kind": kind, "ready": lc is not None, "url": lc[0] if lc else None,
                        "model": lc[1] if lc else None, "masked": _mask(lk) or ("…" if lk else None)})
        elif kind == "anthropic":
            out.append({"kind": kind, "ready": api_key() is not None, "source": key_source(),
                        "masked": masked(), "model": model()})
        else:
            g = gemini_key()
            out.append({"kind": kind, "ready": g is not None, "source": gemini_source(),
                        "masked": _mask(g), "model": gemini_model()})
    return out


def sealed_saved() -> bool:
    """The owner's sealed key is on disk (settings/secrets.ciphertext_exists: no new server key over
    it). A member's key exists only in multi-user mode, where the server key must come from the
    environment (secrets.require_server_key): no key file is ever generated over it."""
    from backend.settings.secrets import PREFIX
    d = _read(owner_path())
    return any(str(d.get(f) or "").startswith(PREFIX) for f in SEALED_FIELDS)


_busy = threading.local()
_redact_memo: dict = {}                # str(path) -> (mtime_ns, the values): one unseal per change, not per log line
REDACT_MEMO_MAX = 256
SEALED_FIELDS = ("api_key", "gemini_key", "local_key")   # the sealed keys in an ai.json (SP-997: Gemini's, the local endpoint's)


def _redact_one(p: Path) -> tuple:
    """The values of the sealed keys in that ai.json (SEALED_FIELDS order; None for one not there)."""
    try:
        stamp = p.stat().st_mtime_ns
    except OSError:
        return ()
    hit = _redact_memo.get(str(p))
    if hit is not None and hit[0] == stamp:
        return hit[1]
    d = _read(p)
    from backend.settings import secrets
    vs = []
    for f in SEALED_FIELDS:
        try:
            vs.append(secrets.unseal(d[f]) if d.get(f) else None)
        except secrets.SecretError:
            vs.append(None)
    if len(_redact_memo) >= REDACT_MEMO_MAX:
        _redact_memo.clear()
    _redact_memo[str(p)] = (stamp, tuple(vs))
    return tuple(vs)


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
    return set(_member_keys.get()) | _owner_saved()


def _owner_saved() -> set:
    """The owner's saved keys: Anthropic's (SP-785) and Gemini's (SP-997). Never raises."""
    if getattr(_busy, "on", False):
        return set()
    _busy.on = True
    try:
        return {v for v in _redact_one(owner_path()) if v}
    except Exception:                    # noqa: BLE001
        return set()
    finally:
        _busy.on = False


def redact_value() -> Optional[str]:
    """The owner's saved Anthropic key (SP-785's single value); redact_values has the rest."""
    if getattr(_busy, "on", False):
        return None
    _busy.on = True
    try:
        vs = _redact_one(owner_path())
        return vs[0] if vs else None
    except Exception:                    # noqa: BLE001
        return None
    finally:
        _busy.on = False


# ------------------------------------------------------------------------- the switch, model, limit

def enabled() -> bool:
    """設定 › 進階 › AI 問答: default on, the owner's and a member's alike — a member without a key
    sees 「問 AI」 too, and its dialog asks for their key first (SP-883, the owner 2026-10-28: 「填完
    API key 就能用，還沒填就在對話框裡提醒」); False for anyone else."""
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
            "who": "member" if is_member() else "owner", "sources": source_state()}
