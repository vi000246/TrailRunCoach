"""
Stable ids for dashboards and charts.

Translations (views/i18n/<locale>.json) and the WKO5 chart fixes
(views/wko5_fixes.json) address a chart by id, not by its title, so a
translated or reworded title can't break them. The bundled views write their
ids (views/*.json, hand-picked slugs); a view without them — a WKO5 binary, a
user's own JSON — gets ids derived from the titles: the ASCII part as a slug,
plus a short hash of the full title when it has other characters (Chinese),
"-2", "-3"… when two charts of a view would share one.
"""
from __future__ import annotations

import hashlib
import re

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slug(title: str) -> str:
    t = str(title or "")
    base = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:40].strip("-")
    if t.isascii() and base:
        return base
    h = hashlib.sha1(t.encode("utf-8")).hexdigest()[:6]
    return f"{base}-{h}" if base else f"c-{h}"


def valid_id(s) -> bool:
    return isinstance(s, str) and bool(_SLUG_RE.match(s))


def _unique(want: str, taken: set) -> str:
    out, k = want, 2
    while out in taken:
        out, k = f"{want}-{k}", k + 1
    taken.add(out)
    return out


def ensure_ids(view: dict) -> dict:
    """Fills in missing dashboard / chart ids in place (and returns the view).
    Chart ids are unique within the view, dashboard ids among its dashboards;
    ids already given are kept (a duplicate one gets a suffix)."""
    dtaken: set = set()
    ctaken: set = set()
    for d in view.get("dashboards", []):
        d["id"] = _unique(d["id"] if valid_id(d.get("id")) else slug(d.get("title")), dtaken)
        for c in d.get("charts", []):
            c["id"] = _unique(c["id"] if valid_id(c.get("id")) else slug(c.get("title")), ctaken)
    return view
