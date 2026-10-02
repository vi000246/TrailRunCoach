"""
Localisation: the request's language and the gettext-style `_()`.

    from backend.i18n import _, N_

    verdict = _("每週 +{ramp:.1f}，可長期維持的增幅", ramp=ramp)
    TYPE_LABEL = {"road": N_("路跑")}            # marked only; _(TYPE_LABEL[k]) when used

The msgid is the Chinese original (zh-TW is the source language, so it needs
no catalog). Other languages live in `locales/<locale>.json`:
``{"<msgid>": "English" | {"one": "...", "other": "..."} | null}``; null or a
missing msgid falls back to the msgid itself. Placeholders are `str.format`
fields, so `{ramp:.1f}` keeps its format spec in every language. A plural
entry picks "one" when the `n` (or `count`) parameter is 1.

The locale comes from a contextvar that the request middleware sets
(backend/i18n/middleware.py); background threads wrap their work in
`use_locale(...)`. Default: zh-TW, so code that never sees a request (the
tests, CLI scripts) keeps producing the original Chinese.

`?i18n_debug=1` marks every untranslated string as ⟦…⟧ (debug_missing).
See docs/plans/i18n.plan.md.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

DEFAULT_LOCALE = "zh-TW"                 # the source language (msgids are written in it)
SUPPORTED = ("zh-TW", "en")
FALLBACK_FOREIGN = "en"                  # an unsupported language asked for explicitly (owner decision)
LOCALES_DIR = Path(__file__).resolve().parent / "locales"

_locale: contextvars.ContextVar[str] = contextvars.ContextVar("trc_locale", default=DEFAULT_LOCALE)
_debug: contextvars.ContextVar[bool] = contextvars.ContextVar("trc_i18n_debug", default=False)

log = logging.getLogger(__name__)


def instance_default() -> str:
    """TRC_DEFAULT_LOCALE (self-hosted: zh-TW; the demo instance sets en)."""
    return normalize(os.getenv("TRC_DEFAULT_LOCALE") or "") or DEFAULT_LOCALE


def normalize(tag: Optional[str]) -> Optional[str]:
    """A BCP 47 tag -> a supported locale, or None when it isn't one.
    zh, zh-TW, zh-Hant*, zh-HK, zh-MO -> zh-TW; zh-CN / zh-Hans* -> zh-TW for
    now (an OpenCC zh-CN may come later); en* -> en."""
    if not tag:
        return None
    t = str(tag).strip().replace("_", "-").lower()
    if not t or t == "*":
        return None
    if t == "zh" or t.startswith("zh-"):
        return "zh-TW"
    if t == "en" or t.startswith("en-"):
        return "en"
    return None


def current_locale() -> str:
    return _locale.get()


def set_locale(loc: Optional[str]) -> contextvars.Token:
    """Set the context's locale (the middleware does this); returns the token for reset."""
    return _locale.set(normalize(loc) or DEFAULT_LOCALE)


def reset_locale(token: contextvars.Token) -> None:
    _locale.reset(token)


@contextmanager
def use_locale(loc: Optional[str], debug: bool = False) -> Iterator[str]:
    """Run a block in a locale (background threads: warm-up, auto-sync, COROS push)."""
    t1 = set_locale(loc)
    t2 = _debug.set(debug)
    try:
        yield current_locale()
    finally:
        _debug.reset(t2)
        _locale.reset(t1)


def set_debug(on: bool) -> contextvars.Token:
    return _debug.set(bool(on))


def debug_missing() -> bool:
    return _debug.get()


# ---- catalogs ------------------------------------------------------------

_CAT_LOCK = threading.Lock()
_CATALOGS: dict[str, tuple[float, dict]] = {}      # locale -> (mtime, catalog)
_MISSED: set[tuple[str, str]] = set()              # (locale, msgid) logged once


def catalog(loc: str) -> dict:
    """The backend catalog of `loc` ({} for the source language or no file),
    reloaded when its file changes."""
    if loc == DEFAULT_LOCALE:
        return {}
    path = LOCALES_DIR / f"{loc}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _CAT_LOCK:
        hit = _CATALOGS.get(loc)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        log.warning("i18n: ignoring %s: %s", path, e)
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _CAT_LOCK:
        _CATALOGS[loc] = (mtime, data)
    return data


def _format(s: str, params: dict) -> str:
    if not params:
        return s
    try:
        return s.format(**params)
    except (KeyError, IndexError, ValueError):
        return s


def _plural(entry: dict, params: dict) -> Optional[str]:
    n = params.get("n", params.get("count"))
    form = "one" if n == 1 else "other"
    return entry.get(form) or entry.get("other")


def translate(msgid: str, loc: Optional[str] = None, **params: Any) -> str:
    """`msgid` in `loc` (default: the context's), formatted with `params`."""
    loc = loc or current_locale()
    if loc == DEFAULT_LOCALE:
        return _format(msgid, params)
    entry = catalog(loc).get(msgid)
    text = _plural(entry, params) if isinstance(entry, dict) else entry
    if not isinstance(text, str) or not text:
        key = (loc, msgid)
        if key not in _MISSED:                       # count it, once per msgid
            _MISSED.add(key)
            log.debug("i18n: no %s for %r", loc, msgid[:80])
        out = _format(msgid, params)
        return f"⟦{out}⟧" if debug_missing() else out
    return _format(text, params)


def _(msgid: str, /, **params: Any) -> str:
    """gettext-style: the request locale's text for the Chinese `msgid`."""
    return translate(msgid, None, **params)


def N_(msgid: str) -> str:
    """Marks a msgid for extraction without translating it (module-level
    tables); translate where it is shown: `_(TABLE[k])`."""
    return msgid


def missing_count() -> int:
    """How many distinct msgids were shown untranslated since start (the production log metric)."""
    return len(_MISSED)


class UserError(Exception):
    """An error meant for the user: the message is a msgid, translated when
    the response is built (main.py's handler answers
    {"detail": {"code", "message", "params"}} with `status`)."""

    def __init__(self, msgid: str, code: Optional[str] = None, status: int = 400, **params: Any):
        super().__init__(msgid)
        self.msgid = msgid
        self.code = code
        self.status = status
        self.params = params

    def message(self) -> str:
        return translate(self.msgid, None, **self.params)

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message(),
                "params": {k: v for k, v in self.params.items() if isinstance(v, (str, int, float, bool)) or v is None}}


__all__ = ["_", "N_", "DEFAULT_LOCALE", "SUPPORTED", "UserError", "current_locale", "instance_default",
           "normalize", "set_locale", "reset_locale", "use_locale", "translate", "catalog"]
