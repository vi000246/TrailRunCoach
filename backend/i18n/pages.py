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
ALWAYS_NS = ("common", "shell")
# page file stem -> its catalog namespaces (default: the stem itself)
# races: the event editor moved there from the season plan page (SP-691) and keeps its plan.ev.* text
# pricing: 方案與價格 (SP-846) shows the plans page's comparison (plans.json row.*, billing/plan_table.py)
PAGE_NS = {"charts": ("viewer",), "share": ("share", "racepower"), "races": ("races", "plan"),
           "pricing": ("pricing", "plans")}
I18N_JS = "/api/v1/static/i18n/i18n.js"
I18N_JS_FILE = CATALOG_DIR / "i18n.js"
# pages a visitor opens without signing in, through a gate that lets only their own path through (the
# share link: Cloudflare Access lets /share/* by, not /api/v1/static/*): i18n.js goes inline, else
# t() is missing and the page shows its keys — 「join.title」 (SP-698)
INLINE_I18N_PAGES = frozenset({"share", "pace"})     # pace: the 腳程試算 (SP-805, /share/pace)
# the same gate, for a page's own shared scripts (static/<file>): written into the page at its
# `<!--inline:<file>-->` marker instead of loaded by address — the share page draws its 分段目標 with
# the calculator's component (SP-784)
INLINE_SCRIPTS = {"share": ("seg_profile.js",)}
# 外觀 (SP-974, docs/spec/ui-theme.spec.md): the Trig palettes and the script that picks one, inline
# first in every page's <head> — before the page's own <style>, so the colours never flash; inline
# because a visitor's /share/* gets no /api/v1/static
THEME_CSS_FILE = STATIC / "theme.css"
THEME_JS_FILE = STATIC / "theme.js"


def _uncomment(src: str) -> str:
    """theme.css / theme.js without their comments (every page carries them; the files keep no `//` or
    `/*` inside a string — test_theme_sp974 checks)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)(^|\s)//[^\n]*$", "", src)
    return re.sub(r"\n\s*\n+", "\n", src).strip()


def theme_head() -> str:
    """The <style> and <script> of the 外觀 setting (theme.css, theme.js) as written into a page."""
    css = re.sub(r"</(style)", r"<\\/\1", _uncomment(THEME_CSS_FILE.read_text("utf-8")), flags=re.I)
    js = re.sub(r"</(script)", r"<\\/\1", _uncomment(THEME_JS_FILE.read_text("utf-8")), flags=re.I)
    return f'<style id="trc-theme">{css}</style><script id="trc-theme-js">{js}</script>'


def _script_tag(path: Path) -> str:
    """A file as an inline <script> (a `</script` in it can't close the tag)."""
    return "<script>" + re.sub(r"</(script)", r"<\\/\1", path.read_text("utf-8"), flags=re.I) + "</script>"


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
    if not isinstance(data, dict):
        return {}
    # a plural ({"one": …, "other": …}) goes to the page as it is: t() picks the form by {n}
    return {f"{ns}.{k}": v for k, v in data.items()
            if isinstance(v, str) or (isinstance(v, dict) and v and all(isinstance(x, str) for x in v.values()))}


def _flat(t):
    """A catalog entry as one text (markup replaced server side has no {n}: a plural gives its "other")."""
    t = t.get("other") or next(iter(t.values()), None) if isinstance(t, dict) else t
    # SP-762: a `{n|day|days}` word with no parameter here reads its plural form
    return i18n.plural_words(t, {}) if isinstance(t, str) else t


def page_catalog(namespaces: Iterable[str], loc: str, debug: bool = False) -> dict[str, str]:
    """{full key: text} — the locale's text (a plural: {"one", "other"}), else zh-TW's (⟦…⟧ in debug mode)."""
    out: dict[str, str] = {}
    for ns in namespaces:
        base = _read_ns(i18n.DEFAULT_LOCALE, ns)
        tr = _read_ns(loc, ns) if loc != i18n.DEFAULT_LOCALE else {}
        for k, v in base.items():
            t = tr.get(k)
            out[k] = t if t else (f"⟦{v}⟧" if debug and loc != i18n.DEFAULT_LOCALE else v)
        for k, v in tr.items():            # keys only the translation has (shouldn't happen; harmless)
            out.setdefault(k, v)
    name = brand(loc)
    return {k: _with_brand(v, name) for k, v in out.items()}


BRAND_KEY = "shell.brand"
BRAND_MARK = "{brand}"


def brand(loc: str) -> str:
    """The product's name in `loc` (SP-971): one key, shell.brand. Every other catalog text that names
    the product writes `{brand}` instead, filled in here — renaming it is a one-line change."""
    t = _read_ns(loc, "shell").get(BRAND_KEY) if loc != i18n.DEFAULT_LOCALE else None
    return t or _read_ns(i18n.DEFAULT_LOCALE, "shell").get(BRAND_KEY) or "Trig"


def _with_brand(v, name: str):
    if isinstance(v, str):
        return v.replace(BRAND_MARK, name) if BRAND_MARK in v else v
    if isinstance(v, dict):
        return {k: _with_brand(x, name) for k, x in v.items()}
    return v


def namespaces_for(name: str) -> tuple[str, ...]:
    return ALWAYS_NS + tuple(PAGE_NS.get(name, (name,)))


_ELEM = re.compile(r'<([a-zA-Z][\w-]*)(\s[^<>]*?\bdata-i18n="([^"]+)"[^<>]*)>(.*?)</\1\s*>', re.S)
_ATTR_TAG = re.compile(r'<[a-zA-Z][\w-]*\s[^<>]*?\bdata-i18n-attr="([^"]+)"[^<>]*>', re.S)
_HTML_LANG = re.compile(r'(<html\b[^>]*?\blang=")[^"]*(")', re.I)
_HEAD = re.compile(r"<head\b[^>]*>", re.I)


def apply_catalog(src: str, cat: dict[str, str]) -> str:
    """Server-side replacement of data-i18n / data-i18n-attr (keys not in `cat` are left as written)."""
    def elem(m: re.Match) -> str:
        t = _flat(cat.get(m.group(3)))
        if t is None:
            return m.group(0)
        return f"<{m.group(1)}{m.group(2)}>{html.escape(t, quote=False)}</{m.group(1)}>"

    def attrs(m: re.Match) -> str:
        tag = m.group(0)
        for pair in m.group(1).split(";"):
            if ":" not in pair:
                continue
            name, key = (x.strip() for x in pair.split(":", 1))
            t = _flat(cat.get(key))
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
    stamp = [path.stat().st_mtime_ns, THEME_CSS_FILE.stat().st_mtime_ns, THEME_JS_FILE.stat().st_mtime_ns]
    inline = name in INLINE_I18N_PAGES
    if inline:
        stamp.append(I18N_JS_FILE.stat().st_mtime_ns)
    scripts = [STATIC / f for f in INLINE_SCRIPTS.get(name, ())]
    stamp += [p.stat().st_mtime_ns for p in scripts]
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
    for p in scripts:
        out = out.replace(f"<!--inline:{p.name}-->", _script_tag(p))
    boot = json.dumps({"locale": loc, "debug": bool(debug), "catalog": cat}, ensure_ascii=False).replace("</", "<\\/")
    if inline:
        js = re.sub(r"</(script)", r"<\\/\1", I18N_JS_FILE.read_text("utf-8"), flags=re.I)
        loader = f"<script>{js}</script>"
    else:
        loader = f'<script src="{I18N_JS}"></script>'
    inject = theme_head() + f'<script>window.__I18N__ = {boot};</script>{loader}'
    m = _HEAD.search(out)
    out = out[:m.end()] + inject + out[m.end():] if m else inject + out
    with _LOCK:
        _CACHE[key] = (stamp, out)
    return out


_CACHE: dict[tuple, tuple[list, str]] = {}
_LOCK = threading.Lock()


def render_page(name: str, loc: Optional[str] = None, headers: Optional[dict] = None,
                fill: Optional[dict[str, str]] = None) -> PageResponse:
    """A page route's response: `return render_page("overview")`. `fill` {key: html} replaces the
    page's `<!--fill:key-->` markers per request (the caller escapes what goes in: the /legal
    pages put a document there, SP-60)."""
    out = render(name, loc)
    for k, v in (fill or {}).items():
        out = out.replace(f"<!--fill:{k}-->", v)
    # who the page is for (mode, caps, demo sandbox): per request, so outside the cache;
    # the shell reads it synchronously (banner, hidden pages, locked buttons)
    try:
        from backend.api.session import session_info
        # every "<" escaped: a user's own text (a Google display name) can never close the script
        sess = json.dumps(session_info(), ensure_ascii=False).replace("<", "\\u003c")
        m = _HEAD.search(out)
        tag = f"<script>window.TRC_SESSION = {sess};</script>"
        out = out[:m.end()] + tag + out[m.end():] if m else tag + out
    except Exception:              # noqa: BLE001 — the shell falls back to GET /api/v1/session
        pass
    return PageResponse(out, STATIC / f"{name}.html", headers=headers)
