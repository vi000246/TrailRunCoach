"""
App log (SP-215): exceptions and performance — which request, sync step or
chart rebuild was slow, and the traceback of every unhandled error.

Where it goes
  * stderr (what `docker logs trailruncoach` shows) and
  * <data folder>/logs/app.log (~/.wko5coach/logs: on the NAS volume, so it
    survives a container restart), rotated at LOG_FILE_BYTES × LOG_FILE_KEEP.
  WKO5COACH_LOG_LEVEL (default INFO), WKO5COACH_LOG_FILE=0 turns the file off.

What is logged
  * RequestLogMiddleware: a request slower than SLOW_REQUEST_S (WARNING), a
    5xx answer, and an unhandled exception with its traceback (ERROR). The
    route template (/share/calendar/{token}.ics), never the raw path or the
    query string, so an ID or token in the URL never reaches the log.
  * timed(): how long a step took (dataset build, auto plan, calibration).
  * sync/runner.py: one line per sync run with where the time went.

Never a token, password or personal data. The call sites log counts,
durations and exception types only (no weights, heart rates, e-mail, user
ids); on top of that every line — message and traceback — passes redact(),
which blanks key=value secrets, Bearer tokens, e-mail addresses, URL query
strings, long opaque strings (tokens, keys) and the home folder. redact() is
a safety net, not a licence: personal values must not be logged at all.
"""
from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger("backend.applog")

# Response-time limits (Nielsen, Usability Engineering 1993, ch. 5: 0.1 s feels
# instant, 1 s keeps the flow of thought, 10 s keeps the attention). A request
# over 3 s is logged as slow: past the flow limit with room for the chart pages'
# ordinary 1–2 s computations (推估, tuned to keep the log readable).
SLOW_REQUEST_S = 3.0
# A step timed with timed() (dataset build, auto plan, calibration) is WARNING
# above this; INFO otherwise (推估: the 10 s attention limit above).
SLOW_STEP_S = 10.0
# A sync run over 2 min is WARNING (推估: an incremental sync of a few new
# activities takes seconds to tens of seconds; a first sync of years is slow anyway).
SLOW_SYNC_S = 120.0
# app.log rotation: 1 MB a file, 5 old files kept (≤ 6 MB on disk)
LOG_FILE_BYTES = 1_000_000
LOG_FILE_KEEP = 5

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------

_SECRET_KEYS = (r"access[_-]?token|refresh[_-]?token|id[_-]?token|token|password|passwd|pwd|secret|"
                r"client[_-]?secret|api[_-]?key|authorization|cookie|set-cookie|session[_-]?id|"
                r"email|e-mail|user[_-]?id|userid|account")
_KV = re.compile(rf"(?i)(\b(?:{_SECRET_KEYS})\b[\"']?\s*[:=]\s*[\"']?)([^\s\"',;&}}\]]+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/]+=*")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_QUERY = re.compile(r"(https?://[^\s?#\"'<>]+)\?[^\s\"'<>]*")
# a run of 32+ token characters holding both letters and digits: access tokens,
# Fernet blobs, API keys, hashes, a share / calendar token in a URL path (a long
# snake_case function name in a traceback has no digits and stays readable)
_OPAQUE = re.compile(r"(?<![\w.-])[A-Za-z0-9_\-]{32,}={0,2}")


def _opaque(m: re.Match) -> str:
    s = m.group(0)
    return "***" if any(c.isdigit() for c in s) and any(c.isalpha() for c in s) else s


def redact(text: str) -> str:
    """`text` with secrets and personal identifiers blanked (***)."""
    if not text:
        return text
    s = _URL_QUERY.sub(r"\1?***", text)
    s = _BEARER.sub(r"\1 ***", s)
    s = _KV.sub(r"\1***", s)
    s = _EMAIL.sub("***@***", s)
    s = _OPAQUE.sub(_opaque, s)
    home = str(Path.home())
    if len(home) > 1:
        s = s.replace(home, "~")
    return s


class RedactingFormatter(logging.Formatter):
    """The whole formatted record (message, arguments and traceback) through redact()."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

class _StderrHandler(logging.StreamHandler):
    """sys.stderr at emit time (like logging.lastResort), not the stream of the
    moment the handler was made: a test's captured stream may be closed later."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stderr


_SETUP_LOCK = threading.Lock()


def log_file() -> Optional[Path]:
    """<data folder>/logs/app.log; None when WKO5COACH_LOG_FILE=0."""
    if os.getenv("WKO5COACH_LOG_FILE", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    from backend import tenancy
    return tenancy.home_root() / "logs" / "app.log"


def setup() -> logging.Logger:
    """The `backend` logger's handlers: stderr and app.log, both redacting.
    Idempotent (the app's start-up calls it; a second call only follows a
    changed data folder). uvicorn's own loggers are left alone."""
    root = logging.getLogger("backend")
    level = getattr(logging, os.getenv("WKO5COACH_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    fmt = RedactingFormatter(FORMAT)
    with _SETUP_LOCK:
        root.setLevel(level)
        if not any(getattr(h, "_trc", None) == "stderr" for h in root.handlers):
            h = _StderrHandler()
            h._trc = "stderr"
            h.setFormatter(fmt)
            root.addHandler(h)
        path = log_file()
        old = [h for h in root.handlers if getattr(h, "_trc", None) == "file"]
        if old and path is not None and all(Path(h.baseFilename) == path for h in old):
            return root
        for h in old:
            root.removeHandler(h)
            h.close()
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fh = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_FILE_BYTES,
                                                          backupCount=LOG_FILE_KEEP, encoding="utf-8")
            except OSError as e:          # a read-only data folder: stderr only
                log.warning("log file unavailable: %s", type(e).__name__)
            else:
                fh._trc = "file"
                fh.setFormatter(fmt)
                root.addHandler(fh)
    return root


# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------

def _fields(fields: dict) -> str:
    return "".join(f" {k}={v}" for k, v in fields.items() if v is not None)


def took(what: str, t0: float, slow_s: float = SLOW_STEP_S, logger: Optional[logging.Logger] = None,
         **fields) -> float:
    """Log the time since `t0` (time.perf_counter()): INFO, WARNING from
    `slow_s` on. `fields` are appended as key=value: counts and names only.
    Returns the seconds."""
    dt = time.perf_counter() - t0
    lg = logger or log
    if dt >= slow_s:
        lg.warning("slow: %s took %.1f s%s", what, dt, _fields(fields))
    else:
        lg.info("%s took %.1f s%s", what, dt, _fields(fields))
    return dt


@contextlib.contextmanager
def timed(what: str, slow_s: float = SLOW_STEP_S, logger: Optional[logging.Logger] = None,
          **fields) -> Iterator[None]:
    """took() around a block; 「failed after」 when it raised (the exception
    itself is the caller's to log)."""
    t0 = time.perf_counter()
    try:
        yield
    except BaseException:
        (logger or log).warning("%s failed after %.1f s%s", what, time.perf_counter() - t0, _fields(fields))
        raise
    took(what, t0, slow_s, logger, **fields)


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------

def route_label(scope) -> str:
    """The matched route's template (/api/v1/plan/sessions/{sid}); a mount's
    prefix + /…; "(no route)" for a 404. Never the raw path or query."""
    r = scope.get("route")
    path = getattr(r, "path", None)
    if not path:
        return "(no route)"
    from starlette.routing import Mount
    return f"{path}/…" if isinstance(r, Mount) else path


class RequestLogMiddleware:
    """Pure ASGI (streams pass through untouched). Logs a slow request, a 5xx
    answer and an unhandled exception (with traceback, then re-raised).
    Server-sent event streams (the sync's progress) are not timed: their
    length is the sync's, logged by sync/runner.py."""

    def __init__(self, app, slow_s: Optional[float] = None):
        self.app = app
        self.slow_s = slow_s

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        resp = {"status": None, "stream": False}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                resp["status"] = message.get("status")
                for k, v in message.get("headers") or []:
                    if k.lower() == b"content-type" and v.lower().startswith(b"text/event-stream"):
                        resp["stream"] = True
            await send(message)

        method = scope.get("method", "")
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled error: %s %s after %.2f s", method, route_label(scope),
                          time.perf_counter() - t0)
            raise
        dt = time.perf_counter() - t0
        status = resp["status"] or 0
        slow = SLOW_REQUEST_S if self.slow_s is None else self.slow_s
        if status >= 500:
            log.warning("server error: %s %s -> %s in %.2f s", method, route_label(scope), status, dt)
        elif dt >= slow and not resp["stream"]:
            log.warning("slow request: %s %s -> %s in %.1f s", method, route_label(scope), status, dt)
