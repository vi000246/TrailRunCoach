"""
The sync event log (SP-385; SP-91 part D writes Garmin's into the same file): one JSON line per
login / sync result / 429 / 403 / pause / breaker / switch, for the owner to see how often COROS
(and later Garmin) pushes back and how long it took to recover.

    events.log("coros", "http_429", tenant="u12")

<server-root>/logs/sync_events.jsonl, rotated at MAX_BYTES (one .1 kept). Nothing personal:
tenants by id only (u<n>, "owner"), never an e-mail, a token or a password; free-text fields
are cut to 120 characters. A failure to write never fails the caller.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

PATH: Optional[Path] = None          # fixed file (tests)
MAX_BYTES = 5 * 1024 * 1024
FORBIDDEN = ("email", "password", "token", "pwd", "cookie")
_LOCK = threading.Lock()


def path() -> Path:
    if PATH is not None:
        return Path(PATH)
    from backend import accounts
    return accounts.server_root() / "logs" / "sync_events.jsonl"


def log(source: str, event: str, **fields) -> None:
    rec = {"at": round(time.time(), 1), "source": source, "event": event}
    for k, v in fields.items():
        if any(f in k.lower() for f in FORBIDDEN):
            continue
        if isinstance(v, str):
            v = v[:120]
        elif not isinstance(v, (int, float, bool, type(None))):
            v = str(v)[:120]
        rec[k] = v
    try:
        p = path()
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > MAX_BYTES:
                os.replace(p, p.with_name(p.name + ".1"))
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:                       # noqa: BLE001 — never fails the caller
        logging.getLogger(__name__).warning("sync event not written: %s", type(e).__name__)


SYNCED = ("sync_ok", "sync_partial")   # a sync that worked (a failed / skipped one is not a sync)


def last_syncs(since: float = 0.0) -> dict[str, float]:
    """tenant id -> the time (epoch s) of its newest sync that worked at or after `since`, from
    this log and its rotated copy (accounts/tenants.active_tenants, SP-394). COROS and Garmin
    only: the sources that write here."""
    out: dict[str, float] = {}
    p = path()
    for f in (p.with_name(p.name + ".1"), p):
        try:
            lines = f.read_text("utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            at, tid = r.get("at"), r.get("tenant")
            if r.get("event") in SYNCED and tid and isinstance(at, (int, float)) and at >= since:
                out[tid] = max(out.get(tid, 0.0), float(at))
    return out


def read(source: Optional[str] = None, limit: int = 200) -> list[dict]:
    """The newest `limit` events (oldest first), optionally of one source."""
    try:
        lines = path().read_text("utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[::-1]:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if source is None or r.get("source") == source:
            out.append(r)
            if len(out) >= limit:
                break
    return out[::-1]
