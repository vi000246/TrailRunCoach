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
# Each denied word is split by a one-letter class ([c]) or string concatenation so this
# file never contains it literally: the history rewrite (backend/scripts/history_rewrite.md)
# replaces those words everywhere, and its post-check greps the whole tree for them.
DENY = re.compile(r"Yi[c]h|Obsi[d]ian|私[訊]|私[信]|私[下]|私[人]|回[信]|本人回[覆]|本人的回[覆]|親自回[覆]|來[信]"
                  r"|xu-guofeng-[r]eply|docs/research|\.md\b|擁有者|\bowner\b|Main[R]epo|may[o]hr"
                  r"|\b[A]thlete_\d{4}_\d\d_\d\d|\bthe athlete's\b", re.I)   # a real activity file stamp; the owner as "the athlete"


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
    for s in ("WKO5「Yi" "ch's Workout」→ 間歇", "徐國峰（私" "訊，2026-10-01）", "見 docs/research/x.md",
              "Obsi" "dian vault", "擁有者決定", "the owner", "Main" "Repo/notes",
              "徐國峰（私" "信）", "徐國峰私" "下說", "徐國峰本人回" "覆", "徐國峰回" "信", "私" "人訊息",
              "徐國峰來" "信", "見 xu-guofeng-" "reply", "someone@may" "ohr.com",
              "A" "thlete_2025_07_26_07_30.wko4", "the athlete's half marathon"):
        assert DENY.search(s), s
    for s in ("徐國峰（教練）", "Monod & Scherrer 1965", "健行筆記 EP", "WKO5 的 dFRC 模型",
              "90 分鐘測試（台灣教練）", "徐國峰《跑者都該懂的跑步科學》"):
        assert not DENY.search(s), s


def test_views_and_catalogs_have_no_internal_notes():
    texts = list(_json_texts())
    assert len(texts) > 100                         # the scan really read the views and catalogs
    assert _hits(texts) == []


def test_static_pages_have_no_internal_notes():
    texts = list(_html_texts())
    assert len(texts) > 100
    assert _hits(texts) == []
