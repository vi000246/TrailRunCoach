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
  * uvicorn's access log (docker logs only; it never reaches app.log) keeps
    its one line per request, with the keys in the address masked (SP-232):
    a share link or 課表訂閱 feed token after /share/ (GET /share/calendar/***.ics),
    secret-named query parameters (token, key, code, state, …: ?code=***) and
    anything else that looks like a key. See mask_url().

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
from urllib.parse import unquote, unquote_plus

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


# ---- URLs (SP-232) ------------------------------------------------------------
# uvicorn's access log prints every request's path and query string. A share
# link (/share/<id>, /share/<id>/data, engine/racepower/share.py) and the 課表訂閱
# feed (/share/calendar/<token>.ics, engine/calendar_feed.py) are keys in the
# path: whoever has the address reads the data. Every segment after a share /
# shares segment (any case, %-encoded or not) is masked, except these route
# words and a route template ({sid}); a .ics ending stays so a feed fetch is
# still recognisable in the log.
_SHARE_SEGMENTS = frozenset({"share", "shares"})
_SHARE_WORDS = frozenset({"calendar", "data"})
_TEMPLATE_SEG = re.compile(r"\{[A-Za-z_][\w:]*\}(?:\.\w+)?")
# A query parameter whose name (any case, %-encoded or not, with a prefix or a
# suffix: access_token, X-Api-Key, oauth_state) holds one of these has its value
# masked. code / state: the TrainingPeaks OAuth callback (/api/v1/auth/tp/callback).
# Erring on the masking side: the app's own parameters (day, uid, view, period, …)
# match none of them; ?key= (an activity's start minute) is masked with the rest.
_SECRET_PARAM = re.compile(r"token|secret|passw|pwd|key|code|state|sig|auth|session|cookie|credential|"
                           r"jwt|ticket|nonce|otp|mail|user|account")
# Any other path segment or query value that looks like a key: 20+ token
# characters holding both letters and digits (a share id is 22, a feed token 32;
# the app's own ids — plan uid 12 hex, workout index, dates — are shorter or
# carry other characters)
_OPAQUE_PART = re.compile(r"[A-Za-z0-9_\-]{20,}={0,2}")
_PATH_IN_TEXT = re.compile(r"/[^\s\"'<>]*")


def _looks_opaque(s: str) -> bool:
    return bool(_OPAQUE_PART.fullmatch(s)) and any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


def _mask_segment(seg: str) -> str:
    if not seg or _TEMPLATE_SEG.fullmatch(seg):
        return seg
    low = unquote(seg).lower()
    if low in _SHARE_WORDS:
        return seg
    return "***.ics" if low.endswith(".ics") else "***"


def _mask_param(p: str, guess: bool) -> str:
    k, eq, v = p.partition("=")
    if eq and _SECRET_PARAM.search(unquote_plus(k).lower()):
        return f"{k}=***"
    if guess and _looks_opaque(unquote_plus(v if eq else k)):
        return f"{k}=***" if eq else "***"
    return p


def mask_url(url: str, guess: bool = True) -> str:
    """A request target (path?query, as uvicorn's access log prints it) with
    its keys masked (***): the segments after /share/ or /shares/, the values
    of secret-named query parameters and, with `guess`, any other segment or
    value that looks like a key. The rest (route, ordinary parameters) stays
    readable."""
    if not url:
        return url
    path, q, query = url.partition("?")
    segs = path.split("/")
    after_share = False
    for i, seg in enumerate(segs):
        if after_share:
            segs[i] = _mask_segment(seg)
        elif guess and _looks_opaque(unquote(seg)):
            segs[i] = "***"
        if unquote(seg).strip().lower() in _SHARE_SEGMENTS:
            after_share = True
    out = "/".join(segs)
    if q:
        out += "?" + "".join(part if part in ("&", ";") else _mask_param(part, guess)
                             for part in re.split(r"([&;])", query))
    return out


# ---- the home folder (SP-357) ---------------------------------------------------
# The home folder (it holds the user's name) becomes "~", and the rest of that
# path is written with forward slashes on every platform:
# C:\Users\<name>\.wko5coach\fit\x.fit → ~/.wko5coach/fit/x.fit, the same line
# a mac / Linux install logs, so one log reads (and one test checks) the same
# everywhere. Only the path that starts at the home folder is rewritten: any
# other backslash in the message (a regex, a path elsewhere) stays as it was.
# On Windows the home is found in either spelling (C:\Users\<name>,
# C:/Users/<name>, a JSON-escaped C:\\Users\\<name>) and in any case, as the
# file system ignores case; it must end at a separator or a non-name character
# (C:\Users\<name>2 is someone else's folder). A path segment stops at
# whitespace, a quote or a character no file name holds.
_SEP = "[" + re.escape(os.sep + (os.altsep or "")) + "]"
_SEG = "[^\\s\"'<>|*?" + re.escape(os.sep + (os.altsep or "")) + "]*"


def _home_pattern(home: str) -> re.Pattern:
    head = (_SEP + "+").join(re.escape(p) for p in re.split(_SEP, home))
    return re.compile(rf"{head}(?![\w.-])((?:{_SEP}+{_SEG})*)", re.IGNORECASE if os.name == "nt" else 0)


_HOME_RE: dict[str, re.Pattern] = {}


def _redact_home(s: str) -> str:
    home = str(Path.home())
    if len(home) <= 1:
        return s
    pat = _HOME_RE.get(home)
    if pat is None:
        pat = _HOME_RE[home] = _home_pattern(home)
    return pat.sub(lambda m: "~" + re.sub(_SEP + "+", "/", m.group(1)), s)


def redact(text: str) -> str:
    """`text` with secrets and personal identifiers blanked (***)."""
    if not text:
        return text
    s = _redact_home(text)
    s = _URL_QUERY.sub(r"\1?***", s)
    # share paths and secret-named parameters in any path (SP-232); no guessing
    # here: a file path in a traceback keeps its folders
    s = _PATH_IN_TEXT.sub(lambda m: mask_url(m.group(0), guess=False), s)
    s = _BEARER.sub(r"\1 ***", s)
    s = _KV.sub(r"\1***", s)
    s = _EMAIL.sub("***@***", s)
    s = _OPAQUE.sub(_opaque, s)
    return s


class RedactingFormatter(logging.Formatter):
    """The whole formatted record (message, arguments and traceback) through redact()."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class UvicornRedactFilter(logging.Filter):
    """On uvicorn's own loggers (SP-232), which keep their handlers and
    formats (docker logs). uvicorn.access: each string argument of the request
    line — (client, method, path?query, HTTP version, status), unpacked as a
    tuple by uvicorn's AccessFormatter, so the tuple keeps its shape — through
    redact(), the path?query through mask_url() first. uvicorn.error: the
    message and the traceback through redact()."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            record.args = tuple(redact(mask_url(a) if a.startswith("/") else a) if isinstance(a, str) else a
                                for a in record.args)
            return True
        try:
            msg = record.getMessage()
        except Exception:                 # noqa: BLE001 — a bad format string: logging reports it
            return True
        if redact(msg) != msg:
            record.msg, record.args = redact(msg), None
            record.__dict__.pop("color_message", None)     # its own %s copy of the message (TTY colours)
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return True


UVICORN_LOGGERS = ("uvicorn.access", "uvicorn.error")


def install_uvicorn_filter() -> None:
    """UvicornRedactFilter on uvicorn's loggers, once each."""
    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, UvicornRedactFilter) for f in lg.filters):
            lg.addFilter(UvicornRedactFilter())


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
    changed data folder). uvicorn's own loggers keep their handlers and
    formats; they only get UvicornRedactFilter (SP-232)."""
    root = logging.getLogger("backend")
    level = getattr(logging, os.getenv("WKO5COACH_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
    fmt = RedactingFormatter(FORMAT)
    with _SETUP_LOCK:
        install_uvicorn_filter()
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


def phases(what: str, t0: float, parts: dict, slow_s: float = SLOW_REQUEST_S,
           logger: Optional[logging.Logger] = None, **fields) -> float:
    """One line with where a request's time went (SP-362: the 課表 calendar):
    「calendar took 4.2 s | inputs 2.9 s, lock_wait 0.0 s, …, other 0.3 s」. `parts` =
    {phase: seconds}; `other` = the rest of the time since `t0`. INFO, WARNING from `slow_s`
    on (every call is logged, so the production timing can be read from app.log).
    Returns the total seconds."""
    total = time.perf_counter() - t0
    rest = total - sum(parts.values())
    steps = ", ".join(f"{k} {v:.1f} s" for k, v in [*parts.items(), ("other", rest)] if v >= 0.05)
    lg = logger or log
    lg.log(logging.WARNING if total >= slow_s else logging.INFO, "%s%s took %.1f s%s",
           "slow: " if total >= slow_s else "", what, total, _fields(fields) + (f" | {steps}" if steps else ""))
    return total


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
