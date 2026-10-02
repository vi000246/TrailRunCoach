"""
render_page(): the app's HTML pages in the request's language.

The pages used to be `FileResponse(STATIC / "x.html")`. render_page:
  1. sets `<html lang>` to the locale;
  2. replaces, server side, the text of `data-i18n="ns.key"` elements and the
     attributes named in `data-i18n-attr="title:ns.key;placeholder:ns.key"`
     — so a translated page never flashes Chinese first. A data-i18n element
     holds plain text only (no child elements);
  3. inlines `window.__I18N__ = {locale, catalog}` (this page's namespaces +
     common + shell) and loads i18n.js (t(), fmt.*) before anything else in
     <head>, so page scripts and shell.js can call t() synchronously.

Frontend catalogs: backend/static/i18n/<locale>/<ns>.json, flat
{"key": "text"}; the full key is "<ns>.<key>". zh-TW is the original text;
a key missing (or null) in another locale falls back to zh-TW's. With
?i18n_debug=1 the fallbacks are shown as ⟦…⟧.

Rendered pages are cached per (page, locale, debug) and the files' mtimes.
"""
from __future__ import annotations

import html
import json
import re
import threading
from pathlib import Path
from typing import Iterable, Optional

from fastapi.responses import HTMLResponse

from backend import i18n

STATIC = Path(__file__).resolve().parents[1] / "static"
CATALOG_DIR = STATIC / "i18n"
# shell.js loads suggestions.js (the floating suggestion box) on every page
ALWAYS_NS = ("common", "shell", "suggestions")
# page file stem -> its catalog namespaces (default: the stem itself);
# autoplan.js is included by the overview and the schedule page
PAGE_NS = {"wko5_viewer": ("viewer",), "share": ("share", "racepower"),
           "overview": ("overview", "autoplan"), "schedule": ("schedule", "autoplan")}
I18N_JS = "/api/v1/static/i18n/i18n.js"


class PageResponse(HTMLResponse):
    """An HTMLResponse that remembers which file it rendered (`.path`, like FileResponse)."""

    def __init__(self, content: str, path: Path, headers: Optional[dict] = None):
        super().__init__(content, headers=headers)
        self.path = str(path)


def _read_ns(loc: str, ns: str) -> dict:
    p = CATALOG_DIR / loc / f"{ns}.json"
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {f"{ns}.{k}": v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def page_catalog(namespaces: Iterable[str], loc: str, debug: bool = False) -> dict[str, str]:
    """{full key: text} — the locale's text, else zh-TW's (⟦…⟧ in debug mode)."""
    out: dict[str, str] = {}
    for ns in namespaces:
        base = _read_ns(i18n.DEFAULT_LOCALE, ns)
        tr = _read_ns(loc, ns) if loc != i18n.DEFAULT_LOCALE else {}
        for k, v in base.items():
            t = tr.get(k)
            out[k] = t if t else (f"⟦{v}⟧" if debug and loc != i18n.DEFAULT_LOCALE else v)
        for k, v in tr.items():            # keys only the translation has (shouldn't happen; harmless)
            out.setdefault(k, v)
    return out


def namespaces_for(name: str) -> tuple[str, ...]:
    return ALWAYS_NS + tuple(PAGE_NS.get(name, (name,)))


_ELEM = re.compile(r'<([a-zA-Z][\w-]*)(\s[^<>]*?\bdata-i18n="([^"]+)"[^<>]*)>(.*?)</\1\s*>', re.S)
_ATTR_TAG = re.compile(r'<[a-zA-Z][\w-]*\s[^<>]*?\bdata-i18n-attr="([^"]+)"[^<>]*>', re.S)
_HTML_LANG = re.compile(r'(<html\b[^>]*?\blang=")[^"]*(")', re.I)
_HEAD = re.compile(r"<head\b[^>]*>", re.I)


def apply_catalog(src: str, cat: dict[str, str]) -> str:
    """Server-side replacement of data-i18n / data-i18n-attr (keys not in `cat` are left as written)."""
    def elem(m: re.Match) -> str:
        t = cat.get(m.group(3))
        if t is None:
            return m.group(0)
        return f"<{m.group(1)}{m.group(2)}>{html.escape(t, quote=False)}</{m.group(1)}>"

    def attrs(m: re.Match) -> str:
        tag = m.group(0)
        for pair in m.group(1).split(";"):
            if ":" not in pair:
                continue
            name, key = (x.strip() for x in pair.split(":", 1))
            t = cat.get(key)
            if t is None or not re.fullmatch(r"[\w-]+", name):
                continue
            val = html.escape(t, quote=True)
            new, n = re.subn(rf'(\s{re.escape(name)}=")[^"]*(")', lambda mm: mm.group(1) + val + mm.group(2), tag, count=1)
            tag = new if n else tag[:-1].rstrip("/") + f' {name}="{val}"' + (" />" if tag.endswith("/>") else ">")
        return tag

    # markup only: a <script>'s template strings are left to t() at run time
    parts = _SCRIPT.split(src)
    return "".join(p if i % 2 else _ATTR_TAG.sub(attrs, _ELEM.sub(elem, p)) for i, p in enumerate(parts))


_SCRIPT = re.compile(r"(<script\b[^>]*>.*?</script\s*>)", re.S | re.I)


def render(name: str, loc: Optional[str] = None, debug: Optional[bool] = None) -> str:
    """The page `static/<name>.html` in `loc` (default: the request's)."""
    loc = loc or i18n.current_locale()
    debug = i18n.debug_missing() if debug is None else debug
    path = STATIC / f"{name}.html"
    nss = namespaces_for(name)
    stamp = [path.stat().st_mtime_ns]
    for l in {i18n.DEFAULT_LOCALE, loc}:
        for ns in nss:
            try:
                stamp.append((CATALOG_DIR / l / f"{ns}.json").stat().st_mtime_ns)
            except OSError:
                stamp.append(None)
    key = (name, loc, bool(debug))
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    src = path.read_text("utf-8")
    cat = page_catalog(nss, loc, bool(debug))
    out = apply_catalog(src, cat)
    out = _HTML_LANG.sub(lambda m: m.group(1) + loc + m.group(2), out, count=1)
    boot = json.dumps({"locale": loc, "debug": bool(debug), "catalog": cat}, ensure_ascii=False).replace("</", "<\\/")
    inject = f'<script>window.__I18N__ = {boot};</script><script src="{I18N_JS}"></script>'
    m = _HEAD.search(out)
    out = out[:m.end()] + inject + out[m.end():] if m else inject + out
    with _LOCK:
        _CACHE[key] = (stamp, out)
    return out


_CACHE: dict[tuple, tuple[list, str]] = {}
_LOCK = threading.Lock()


def render_page(name: str, loc: Optional[str] = None, headers: Optional[dict] = None) -> PageResponse:
    """A page route's response: `return render_page("overview")`."""
    return PageResponse(render(name, loc), STATIC / f"{name}.html", headers=headers)
