"""
Passwords of the e-mail sign-in (SP-610): Argon2id from `cryptography` (already a dependency, the
Fernet of the sealed credentials) — no new package. Only the PHC string is stored
("$argon2id$v=19$m=…,t=…,p=…$salt$hash": the parameters travel with each hash, so they can be raised
later and old hashes still verify; needs_rehash says when a sign-in should store a new one).

    h = hash_password("a long passphrase")
    verify("a long passphrase", h)          # True; a wrong one, a broken hash, None: False
    problem("short", "me@example.com")      # the reason a new password is refused, or None

Cost (OWASP's Argon2id baseline): 19 MiB, 2 passes, 1 lane — about 15 ms here, ~0.13 s on the NAS
(8.5x slower). verify() of an unknown account runs against DUMMY, so a wrong e-mail takes as long as
a wrong password (whether an account exists is not told by the time it takes).
"""
from __future__ import annotations

import os
from typing import Optional

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

MEMORY_KIB = 19 * 1024
ITERATIONS = 2
LANES = 1
LENGTH = 32
MIN_LEN = 10          # SP-610: a floor, not a recipe (no "one digit, one symbol" rules: length matters)
MAX_LEN = 128         # longer is refused before hashing (a megabyte "password" costs the server, not the guesser)

# the passwords leaked most often, and the ones a person picks for this site (lower-cased; a few
# dozen of the ten-character-or-longer ones, the shorter ones are refused by length already)
COMMON = frozenset({
    "1234567890", "0123456789", "12345678910", "123456789a", "1q2w3e4r5t", "1qaz2wsx3edc", "qwertyuiop",
    "asdfghjkl;", "zxcvbnm123", "password12", "password123", "password1234", "passw0rd12", "iloveyou12",
    "qwerty1234", "qwerty12345", "abcdefghij", "abcd123456", "abc1234567", "aa12345678", "a123456789",
    "1111111111", "0000000000", "9876543210", "11223344556", "letmein123", "welcome123", "football12",
    "trailrunning", "trailruncoach", "trailrun123", "marathon123", "runner1234", "running123",
})


def _kdf(salt: bytes) -> Argon2id:
    return Argon2id(salt=salt, length=LENGTH, iterations=ITERATIONS, lanes=LANES, memory_cost=MEMORY_KIB)


def hash_password(password: str) -> str:
    """A new random salt and the PHC string (the only thing stored)."""
    return _kdf(os.urandom(16)).derive_phc_encoded(password.encode("utf-8"))


DUMMY = hash_password("not a password: compared when the account does not exist")


def verify(password: Optional[str], stored: Optional[str]) -> bool:
    """`password` matches the stored PHC string. Never raises: a missing or broken hash is False
    (after the same work as a real check, against DUMMY)."""
    pw = (password or "").encode("utf-8")
    if len(pw) > MAX_LEN * 4:
        pw = pw[: MAX_LEN * 4]
    ok_hash = isinstance(stored, str) and stored.startswith("$argon2id$")
    try:
        Argon2id.verify_phc_encoded(pw, stored if ok_hash else DUMMY)
    except (InvalidKey, ValueError, TypeError):
        return False
    return ok_hash and bool(password)


def needs_rehash(stored: str) -> bool:
    """The stored hash was made with weaker parameters than today's."""
    try:
        params = dict(p.split("=", 1) for p in stored.split("$")[3].split(","))
        return (int(params["m"]), int(params["t"]), int(params["p"])) < (MEMORY_KIB, ITERATIONS, LANES)
    except (IndexError, KeyError, ValueError):
        return True


def problem(password: Optional[str], email: Optional[str] = None) -> Optional[str]:
    """Why a new password is refused (a code the API turns into a message), None when it is fine:
    too_short (< MIN_LEN characters), too_long (> MAX_LEN), weak (one character over and over, a
    common password, or the e-mail address / its name part)."""
    if not isinstance(password, str) or len(password) < MIN_LEN:
        return "too_short"
    if len(password) > MAX_LEN:
        return "too_long"
    low = password.strip().lower()
    if len(set(low)) <= 2 or low in COMMON:
        return "weak"
    if email:
        e = email.strip().lower()
        if low in (e, e.split("@", 1)[0]):
            return "weak"
    return None
