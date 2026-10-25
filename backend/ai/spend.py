"""What each AI 問答 model call cost, for the NAS's shared「💸 API 使用量」Grafana (SP-882).

The shared dashboard reads InfluxDB measurement `api_call` in bucket `feeder` (the grafana-api-usage
schema — not this app's to change):

    api_call,provider=anthropic,model=<id>,project=trailruncoach,purpose=ask,status=success|error
        input_tokens=<i>,output_tokens=<i>,cost_usd=<float>,latency_ms=<i>,error_message="<code>" <ns>

The app does not hold an InfluxDB token and sends nothing out (the rule of deploy/ops-monitoring,
SP-388: 「app 不拿 InfluxDB token、不對外寫東西」). Each call appends that line to

    <data folder>/logs/ops/api_calls.lp

and the Telegraf next to the app (deploy/ops-monitoring/telegraf.conf, `inputs.tail`, read-only
mount) sends it on to `feeder`. The file is cut over to api_calls.lp.1 past MAX_BYTES (one line is
~250 bytes; 30 calls a day is years of lines).

Never in the way of the answer: record() never raises (a full disk, a read-only folder, the log
file off → nothing written, one warning). Only the model, the token counts, the time and the
error *code* go in — never the key, the question or the answer. A cached answer is not a call:
nothing is recorded for it.

cost_usd is worked out here, from PRICE (USD per million tokens). A model not in the table is
recorded with cost 0 — add it.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("backend.ai")

MEASUREMENT = "api_call"
PROVIDER = "anthropic"
PROJECT = "trailruncoach"
PURPOSE = "ask"
FILE_NAME = "api_calls.lp"
MAX_BYTES = 2_000_000

# USD / million tokens (input, output). claude-sonnet-5-5 at the Sonnet line's 3 / 15 (推估: every
# Sonnet so far; not checked against a price page); the rest as the grafana-api-usage skill's table
# (2026Q1), so the shared dashboard adds up the same. Check it when TRC_AI_MODEL changes.
PRICE = {
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
}

_lock = threading.Lock()
_warned = {"once": False}
_TAG_BAD = re.compile(r"[ ,=\\\n\r\t\"]")


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICE.get(model)
    if not p:
        return 0.0
    return (max(0, input_tokens) * p[0] + max(0, output_tokens) * p[1]) / 1_000_000


def _tag(v: str) -> str:
    return _TAG_BAD.sub("_", str(v or "")) or "_"


def _field_str(v: str) -> str:
    return str(v or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:200]


def line(model: str, status: str, input_tokens: int, output_tokens: int, latency_ms: float,
         error: str = "", purpose: str = PURPOSE, at_ns: Optional[int] = None) -> str:
    """One InfluxDB line-protocol line (the shared schema; cost_usd always a float literal)."""
    i, o = int(max(0, input_tokens or 0)), int(max(0, output_tokens or 0))
    tags = ",".join(f"{k}={_tag(v)}" for k, v in (
        ("provider", PROVIDER), ("model", model), ("project", PROJECT), ("purpose", purpose),
        ("status", "success" if status == "success" else "error")))
    fields = (f"input_tokens={i}i,output_tokens={o}i,cost_usd={cost_usd(model, i, o):.6f},"
              f"latency_ms={int(max(0, latency_ms or 0))}i,error_message=\"{_field_str(error)}\"")
    return f"{MEASUREMENT},{tags} {fields} {at_ns if at_ns is not None else time.time_ns()}"


def path() -> Optional[Path]:
    """<data folder>/logs/ops/api_calls.lp (next to metrics.json); None when the log file is off."""
    from backend import ops_daily
    where = ops_daily.folder()
    return None if where is None else where / FILE_NAME


def record(model: str, status: str, input_tokens: int = 0, output_tokens: int = 0,
           latency_ms: float = 0, error: str = "", purpose: str = PURPOSE) -> None:
    """Append one call's line for Telegraf. Never raises: the answer does not depend on it."""
    try:
        p = path()
        if p is None:
            return
        text = line(model, status, input_tokens, output_tokens, latency_ms, error, purpose) + "\n"
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                if p.stat().st_size > MAX_BYTES:
                    p.replace(p.with_name(FILE_NAME + ".1"))
            except FileNotFoundError:
                pass
            with p.open("a", encoding="utf-8") as f:
                f.write(text)
    except Exception as e:                    # noqa: BLE001 — the cost line never breaks an answer
        if not _warned["once"]:
            _warned["once"] = True
            log.warning("AI cost line not written (%s): the Grafana usage misses it", type(e).__name__)
