"""
Encryption at rest for tokens and other secrets (Fernet, AES-128-CBC + HMAC).

Key: WKO5COACH_SECRET_KEY (a Fernet key: 32 url-safe base64 bytes), else a
key generated once into ~/.wko5coach/secret.key (never inside the repo;
`*.key` is git-ignored anyway). Losing the key only means logging in to
COROS / TP again — nothing else is encrypted with it.

Stored form: "enc:v1:<fernet token>". Values without the prefix are legacy
plaintext and are returned as-is, so existing databases keep working and are
re-sealed on the next write.

Generate a key for a server deployment:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

PREFIX = "enc:v1:"
KEY_FILE = Path.home() / ".wko5coach" / "secret.key"


class SecretError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.getenv("WKO5COACH_SECRET_KEY")
    if not key:
        if KEY_FILE.exists():
            key = KEY_FILE.read_text("ascii").strip()
        else:
            key = Fernet.generate_key().decode()
            KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            KEY_FILE.write_text(key, "ascii")
            try:
                os.chmod(KEY_FILE, 0o600)
            except OSError:
                pass
            log.warning("WKO5COACH_SECRET_KEY not set; generated %s (back it up or set the env var)",
                        KEY_FILE)
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise SecretError(f"invalid WKO5COACH_SECRET_KEY / {KEY_FILE}: {e}")


def reset_cache() -> None:
    """For tests / key rotation."""
    _fernet.cache_clear()


def seal(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return value
    if value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def unseal(value: Optional[str]) -> Optional[str]:
    if value is None or not value.startswith(PREFIX):
        return value          # None or legacy plaintext
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise SecretError("stored secret can't be decrypted with the current key — log in again")
