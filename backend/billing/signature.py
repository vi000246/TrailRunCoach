"""
Polar's webhook signature (Standard Webhooks; the same rules as inReachLiveTrack's
lib/polar-signature.ts): HMAC-SHA256 over `{webhook-id}.{webhook-timestamp}.{raw body}`, keyed with
the secret string's UTF-8 bytes as Polar shows it (no base64 decoding, no prefix stripped);
`webhook-signature` is `v1,<base64>`, possibly several separated by spaces (a secret being rotated),
any one of them may match. A timestamp more than TOLERANCE_S away from now is refused (a captured
webhook replayed later). Every doubt is a refusal: no secret, a missing header, a bad timestamp,
bad base64, a wrong length. The caller passes the raw body, before any parsing.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from typing import Optional

TOLERANCE_S = 300


def sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    """The `webhook-signature` value Polar would send (tests and the local fake Polar)."""
    mac = hmac.new(secret.encode("utf-8"), f"{msg_id}.{timestamp}.".encode("utf-8") + body, hashlib.sha256)
    return "v1," + base64.b64encode(mac.digest()).decode("ascii")


def verify(secret: Optional[str], body: bytes, msg_id: Optional[str], timestamp: Optional[str],
           signature: Optional[str], now: Optional[float] = None) -> bool:
    if not secret or not msg_id or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - ts) > TOLERANCE_S:
        return False
    want = hmac.new(secret.encode("utf-8"), f"{msg_id}.{timestamp}.".encode("utf-8") + body,
                    hashlib.sha256).digest()
    for part in signature.split(" "):
        version, _, b64 = part.partition(",")
        if version != "v1" or not b64:
            continue
        try:
            got = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(got) == len(want) and hmac.compare_digest(got, want):
            return True
    return False
