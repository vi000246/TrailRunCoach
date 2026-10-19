"""
Server secrets from the environment only (SP-387 第 5 條): the server key (WKO5COACH_SECRET_KEY,
SP-384), the Google OAuth client (SP-58), the payment keys (SP-328), the token a local copy pulls
from production with (SP-373), the TP client and the CWA key. Never committed, never in a tenant folder, never in a log line.

    secret_env("WKO5COACH_SECRET_KEY")

reads NAME, else the file named by NAME_FILE (Docker / compose secrets mount them at
/run/secrets/<name>: `WKO5COACH_SECRET_KEY_FILE=/run/secrets/trc_secret_key`), stripped.
`values()` = every such secret that is set: applog.redact() blanks them wherever they appear,
a last line of defence behind its pattern rules.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# every secret the server may read from its environment (deploy/commercial.md lists them)
NAMES = (
    "WKO5COACH_SECRET_KEY",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    # Polar (SP-328, backend/billing): the organization access token and the webhook secret
    "TRC_POLAR_TOKEN", "TRC_POLAR_WEBHOOK_SECRET",
    "TP_CLIENT_ID", "TP_CLIENT_SECRET",
    "CWA_API_KEY",
    # intervals.icu's OAuth app and its webhook secret (SP-346, sync/intervals_client.py)
    "TRC_INTERVALS_CLIENT_ID", "TRC_INTERVALS_CLIENT_SECRET", "TRC_INTERVALS_WEBHOOK_SECRET",
    "TRC_DEBUG_PIN",                  # no longer read (SP-373); an old .env may still set it
    "TRC_UPSTREAM_TOKEN", "TRC_PROXY_PASSWORD",   # a local copy pulling from production (SP-373)
    "TRC_CAIRN_LINK_SECRET",          # 疊石 Cairn's calls here (SP-329, accounts/links.py)
    "TRC_CAIRN_OUT_SECRET",           # this app's calls to Cairn (SP-329 Q1-1: one secret per direction)
)
MIN_REDACT_LEN = 6        # shorter values would blank ordinary words


def secret_env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    if v and v.strip():
        return v.strip()
    f = os.environ.get(name + "_FILE")
    if f:
        try:
            v = Path(f).read_text("utf-8").strip()
        except OSError:
            return None
        return v or None
    return None


def values() -> list[str]:
    """The secrets currently set (for redaction), longest first."""
    out = {v for n in NAMES if (v := secret_env(n)) and len(v) >= MIN_REDACT_LEN}
    return sorted(out, key=len, reverse=True)
