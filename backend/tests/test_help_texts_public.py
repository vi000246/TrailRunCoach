"""User-facing help texts must not leak internal notes.

Scans what a user can read — the bundled views (chart titles / descriptions),
the i18n catalogs (backend and static, every locale, plus the views sidecar)
and the static pages' text nodes and title attributes — for a denylist of
internal references: personal WKO5 view names, notes apps, private messages,
repo docs paths, the owner. Sources stay as public citations (author, book,
paper, year). Reads only repo files.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DENY = re.compile(r"Athlete|notes|私訊|docs/research|\.md\b|擁有者|\bowner\b|notes", re.I)


def _strings(obj, path=""):
    """Every string value (and Chinese msgid key) in a JSON tree; `_`-keys are metadata."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).startswith("_"):
                continue
            yield f"{path}.{k}<key>", str(k)
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _json_texts():
    for p in sorted((ROOT / "views").glob("*.json")):
        data = json.loads(p.read_text("utf-8"))
        if p.name == "wko5_fixes.json":
            # config keyed on the imported WKO5 views' own names; only the notes are shown
            for i, f in enumerate(data.get("fixes", [])):
                if f.get("note"):
                    yield p, f"fixes[{i}].note", f["note"]
            continue
        for k, s in _strings(data):
            yield p, k, s
    catalogs = [*sorted((ROOT / "views" / "i18n").glob("*.json")),
                *sorted((ROOT / "backend" / "i18n" / "locales").glob("*.json")),
                *sorted((ROOT / "backend" / "static" / "i18n").glob("*/*.json"))]
    for p in catalogs:
        if p.name.endswith(".meta.json"):          # translator metadata (source file:line), not shown
            continue
        for k, s in _strings(json.loads(p.read_text("utf-8"))):
            yield p, k, s


class _Texts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        for name, val in attrs:
            if name in ("title", "aria-label", "placeholder", "alt", "data-tip") and val:
                self.out.append((self.getpos()[0], f"{tag}[{name}]", val))

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.out.append((self.getpos()[0], "text", data.strip()))


def _html_texts():
    for p in sorted((ROOT / "backend" / "static").glob("*.html")):
        parser = _Texts()
        parser.feed(p.read_text("utf-8"))
        for line, where, s in parser.out:
            yield p, f"{line} {where}", s


def _hits(texts):
    return [f"{p.relative_to(ROOT).as_posix()} {k}: …{s[max(0, m.start() - 30):m.end() + 30]}…"
            for p, k, s in texts for m in [DENY.search(s)] if m]


def test_denylist_matches_the_known_leaks():
    for s in ("WKO5「WKO5 Workout View」→ 間歇", "台灣教練", "見 docs/research/x.md",
              "notes", "擁有者決定", "the owner", "notes/notes"):
        assert DENY.search(s), s
    for s in ("徐國峰（教練）", "Monod & Scherrer 1965", "健行筆記 EP", "WKO5 的 dFRC 模型"):
        assert not DENY.search(s), s


def test_views_and_catalogs_have_no_internal_notes():
    texts = list(_json_texts())
    assert len(texts) > 100                         # the scan really read the views and catalogs
    assert _hits(texts) == []


def test_static_pages_have_no_internal_notes():
    texts = list(_html_texts())
    assert len(texts) > 100
    assert _hits(texts) == []
