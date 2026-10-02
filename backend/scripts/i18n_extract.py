"""
i18n string extraction and coverage report (docs/plans/i18n.plan.md §3.1).

    python -m backend.scripts.i18n_extract                     # summary per file
    python -m backend.scripts.i18n_extract --write             # + add new _() msgids to locales/en.json (null)
                                                               #   and their source positions to en.meta.json
    python -m backend.scripts.i18n_extract --report out.json   # the full report (every file, samples, orphans)
    python -m backend.scripts.i18n_extract --update-baseline   # backend/i18n/baseline.json = today's counts
    python -m backend.scripts.i18n_extract --check             # exit 1 when a file has more unwrapped
                                                               #   Chinese strings than its baseline

What it reads (one process, no subprocesses):
  * Python (backend/**, not tests / scripts / i18n): the msgids of _() / N_()
    calls, and the Chinese strings NOT wrapped in them — literals and
    f-strings (one f-string = one string), docstrings and bare string
    statements excluded.
  * HTML (backend/static/*.html): Chinese text nodes and title / placeholder /
    aria-label / alt / value / content / label attributes outside
    data-i18n / data-i18n-attr, and the string literals of inline <script>s.
  * JS (backend/static/*.js): Chinese string / template literals (comments and
    regex literals skipped).
  * Catalogs: placeholders equal between zh and en, en without Chinese,
    en msgids the code no longer has (orphans, with a "maybe renamed" hint).

backend/i18n/allow_cjk.txt lists Chinese that is intentional (not UI text).
"""
from __future__ import annotations

import argparse
import ast
import difflib
import fnmatch
import json
import re
import string
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
I18N_DIR = BACKEND / "i18n"
LOCALES = I18N_DIR / "locales"
BASELINE = I18N_DIR / "baseline.json"
ALLOW = I18N_DIR / "allow_cjk.txt"
GLOSSARY = I18N_DIR / "glossary.json"
STATIC = BACKEND / "static"
CATALOG_DIR = STATIC / "i18n"
VIEWS = ROOT / "views"

PY_SKIP = ("backend/tests/*", "backend/scripts/*", "backend/i18n/*")
HTML_ATTRS = ("title", "placeholder", "aria-label", "alt", "value", "content", "label")
GETTEXT = ("_", "N_")

# CJK ideographs + kana (コース定数), not CJK punctuation alone (・（）「」 etc.)
CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿぀-ゟ゠-ヺ]")


def has_cjk(s: Optional[str]) -> bool:
    return bool(s) and bool(CJK.search(s))


def rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


# ---- allow list -------------------------------------------------------------

@dataclass
class Allow:
    files: list[str] = field(default_factory=list)
    patterns: list[re.Pattern] = field(default_factory=list)

    def file_ok(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, g) for g in self.files)

    def text_ok(self, s: str) -> bool:
        return any(p.search(s) for p in self.patterns)


def load_allow(path: Path = ALLOW) -> Allow:
    a = Allow()
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:
        return a
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        kind, _, val = ln.partition(" ")
        val = val.strip()
        if kind == "file" and val:
            a.files.append(val)
        elif kind == "re" and val:
            a.patterns.append(re.compile(val))
    return a


# ---- Python -----------------------------------------------------------------

@dataclass
class FileScan:
    path: str
    kind: str                                   # py | html | js
    unwrapped: list[tuple[int, str]] = field(default_factory=list)
    msgids: list[tuple[int, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _docstring_ids(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for st in body:            # every bare string statement (docstrings, string "comments")
                if isinstance(st, ast.Expr) and isinstance(st.value, (ast.Constant, ast.JoinedStr)):
                    out.add(id(st.value))
    return out


def _fstring_text(node: ast.JoinedStr) -> str:
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        else:
            parts.append("{…}")
    return "".join(parts)


def scan_python(path: Path, src: Optional[str] = None) -> FileScan:
    fs = FileScan(rel(path), "py")
    src = src if src is not None else path.read_text("utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        fs.problems.append(f"syntax error: {e}")
        return fs
    skip = _docstring_ids(tree)
    wrapped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in GETTEXT and node.args:
            a = node.args[0]
            wrapped.add(id(a))
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                fs.msgids.append((node.lineno, a.value))
            elif isinstance(a, ast.JoinedStr):
                fs.problems.append(f"line {node.lineno}: f-string inside {node.func.id}() — use named params")
    fparts: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                fparts.add(id(v))
            if id(node) in skip or id(node) in wrapped or id(node) in fparts:
                continue
            text = _fstring_text(node)
            if has_cjk(text):
                fs.unwrapped.append((node.lineno, text))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip or id(node) in wrapped or id(node) in fparts:
                continue
            if has_cjk(node.value):
                fs.unwrapped.append((node.lineno, node.value))
    fs.unwrapped.sort()
    return fs


# ---- JS ---------------------------------------------------------------------

_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KW = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "instanceof", "yield", "await"}


def js_strings(src: str) -> list[tuple[int, str]]:
    """(line, text) of every string / template literal (a template's literal
    parts joined, `${…}` for the expressions); comments and regexes skipped."""
    out: list[tuple[int, str]] = []
    n = len(src)

    def line_of(i: int) -> int:
        return src.count("\n", 0, i) + 1

    def code(i: int, close: Optional[str]) -> int:
        depth = 0
        prev = ""                 # previous significant char
        word = ""                 # previous identifier
        while i < n:
            c = src[i]
            if c in " \t\r\n":
                i += 1
                continue
            if c == "/" and i + 1 < n and src[i + 1] == "/":
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if c == "/" and i + 1 < n and src[i + 1] == "*":
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c in "'\"":
                start, j, buf = i, i + 1, []
                while j < n and src[j] != c and src[j] != "\n":
                    if src[j] == "\\" and j + 1 < n:
                        buf.append(src[j:j + 2])
                        j += 2
                        continue
                    buf.append(src[j])
                    j += 1
                out.append((line_of(start), "".join(buf)))
                i, prev, word = j + 1, "s", ""
                continue
            if c == "`":
                i = template(i)
                prev, word = "s", ""
                continue
            if c == "/" and (prev == "" or prev in _REGEX_PREV or word in _REGEX_KW):
                j, in_cls = i + 1, False
                while j < n and src[j] != "\n":
                    ch = src[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_cls = True
                    elif ch == "]":
                        in_cls = False
                    elif ch == "/" and not in_cls:
                        break
                    j += 1
                j += 1
                while j < n and (src[j].isalnum()):
                    j += 1
                i, prev, word = j, "r", ""
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if close == "}" and depth == 0:
                    return i + 1
                depth -= 1
            if c.isalnum() or c in "_$":
                j = i
                while j < n and (src[j].isalnum() or src[j] in "_$"):
                    j += 1
                word, prev, i = src[i:j], "w", j
                continue
            prev, word = c, ""
            i += 1
        return i

    def template(i: int) -> int:
        start, j, buf = i, i + 1, []
        while j < n:
            ch = src[j]
            if ch == "\\":
                buf.append(src[j:j + 2])
                j += 2
                continue
            if ch == "`":
                j += 1
                break
            if ch == "$" and j + 1 < n and src[j + 1] == "{":
                buf.append("${…}")
                j = code(j + 2, "}")
                continue
            buf.append(ch)
            j += 1
        out.append((line_of(start), "".join(buf)))
        return j

    code(0, None)
    return out


def scan_js_source(path_s: str, src: str, base_line: int = 0, kind: str = "js") -> FileScan:
    fs = FileScan(path_s, kind)
    for ln, s in js_strings(src):
        if has_cjk(s):
            fs.unwrapped.append((ln + base_line, s))
    return fs


def scan_js(path: Path) -> FileScan:
    return scan_js_source(rel(path), path.read_text("utf-8"))


# ---- HTML -------------------------------------------------------------------

class _HTMLScan(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, fs: FileScan):
        super().__init__(convert_charrefs=True)
        self.fs = fs
        self.stack: list[tuple[str, bool]] = []     # (tag, translated text)
        self.in_script = False
        self.script_line = 0
        self.in_style = False

    def _translated(self) -> bool:
        return any(t for _tag, t in self.stack)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        names = {p.split(":", 1)[0].strip() for p in (a.get("data-i18n-attr") or "").split(";") if ":" in p}
        for k, v in attrs:
            if k in HTML_ATTRS and k not in names and has_cjk(v):
                self.fs.unwrapped.append((self.getpos()[0], v))
        if tag == "script":
            self.in_script = not a.get("src")
            self.script_line = self.getpos()[0]
            return
        if tag == "style":
            self.in_style = True
            return
        if tag not in self.VOID:
            self.stack.append((tag, "data-i18n" in a))

    def handle_startendtag(self, tag, attrs):
        a = dict(attrs)
        names = {p.split(":", 1)[0].strip() for p in (a.get("data-i18n-attr") or "").split(";") if ":" in p}
        for k, v in attrs:
            if k in HTML_ATTRS and k not in names and has_cjk(v):
                self.fs.unwrapped.append((self.getpos()[0], v))

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False
            return
        if tag == "style":
            self.in_style = False
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.in_style:
            return
        if self.in_script:
            sub = scan_js_source(self.fs.path, data, self.script_line - 1, "html")
            self.fs.unwrapped.extend(sub.unwrapped)
            return
        t = data.strip()
        if has_cjk(t) and not self._translated():
            self.fs.unwrapped.append((self.getpos()[0], t))


def scan_html(path: Path, src: Optional[str] = None) -> FileScan:
    fs = FileScan(rel(path), "html")
    p = _HTMLScan(fs)
    p.feed(src if src is not None else path.read_text("utf-8"))
    p.close()
    fs.unwrapped.sort()
    return fs


# ---- the repo ---------------------------------------------------------------

def source_files() -> Iterable[Path]:
    for p in sorted(BACKEND.rglob("*.py")):
        r = rel(p)
        if not any(fnmatch.fnmatch(r, g) for g in PY_SKIP):
            yield p
    yield from sorted(STATIC.glob("*.html"))
    yield from sorted(STATIC.glob("*.js"))


def scan_file(p: Path) -> FileScan:
    if p.suffix == ".py":
        return scan_python(p)
    if p.suffix == ".html":
        return scan_html(p)
    return scan_js(p)


def scan_repo(allow: Optional[Allow] = None) -> dict[str, FileScan]:
    allow = allow or load_allow()
    out = {}
    for p in source_files():
        r = rel(p)
        fs = scan_file(p)
        if allow.file_ok(r):
            fs.unwrapped = []
        else:
            fs.unwrapped = [(ln, s) for ln, s in fs.unwrapped if not allow.text_ok(s)]
        out[r] = fs
    return out


# ---- catalogs -----------------------------------------------------------------

def py_fields(s: str) -> set[str]:
    """str.format field names ({ramp:.1f} -> ramp)."""
    out = set()
    try:
        for _lit, name, _spec, _conv in string.Formatter().parse(s):
            if name is not None:
                out.add(re.split(r"[.\[]", name, maxsplit=1)[0])
    except ValueError:
        out.add("<malformed>")
    return out


def js_fields(s: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", s))


def _texts(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [x for x in v.values() if isinstance(x, str)]
    return []


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def frontend_catalogs() -> dict[str, dict[str, dict]]:
    """{locale: {ns: {key: value}}}"""
    out: dict[str, dict[str, dict]] = {}
    if not CATALOG_DIR.is_dir():
        return out
    for d in sorted(CATALOG_DIR.iterdir()):
        if d.is_dir():
            out[d.name] = {p.stem: load_json(p, {}) for p in sorted(d.glob("*.json"))}
    return out


def glossary_terms() -> list[dict]:
    return [t for t in load_json(GLOSSARY, {}).get("terms", []) if isinstance(t, dict)]


def glossary_violations(src: str, en: str, terms: Optional[list[dict]] = None) -> list[str]:
    bad = []
    for t in terms if terms is not None else glossary_terms():
        if not t.get("check"):
            continue
        if any(z in src for z in t.get("zh", [])):
            if not any(a.lower() in en.lower() for a in t.get("accept", [])):
                bad.append(f"{'/'.join(t['zh'])} -> {t.get('en')}")
    return bad


def catalog_problems(scans: dict[str, FileScan]) -> dict[str, list[str]]:
    """Placeholder mismatches, Chinese in en, glossary misses — backend and frontend."""
    terms = glossary_terms()
    out: dict[str, list[str]] = {"placeholders": [], "cjk_in_en": [], "glossary": []}
    en = load_json(LOCALES / "en.json", {})
    for msgid, v in en.items():
        for t in _texts(v):
            if py_fields(t) != py_fields(msgid):
                out["placeholders"].append(f"backend en {msgid[:40]!r}: {sorted(py_fields(msgid))} vs {sorted(py_fields(t))}")
            if has_cjk(t):
                out["cjk_in_en"].append(f"backend en {msgid[:40]!r}")
            for g in glossary_violations(msgid, t, terms):
                out["glossary"].append(f"backend en {msgid[:40]!r}: {g}")
    cats = frontend_catalogs()
    base = cats.get("zh-TW", {})
    for loc, nss in cats.items():
        if loc == "zh-TW":
            continue
        for ns, keys in nss.items():
            for k, v in keys.items():
                if not isinstance(v, str) or not v:
                    continue
                zh = base.get(ns, {}).get(k)
                if isinstance(zh, str) and js_fields(zh) != js_fields(v):
                    out["placeholders"].append(f"{loc}/{ns}.json {k}: {sorted(js_fields(zh))} vs {sorted(js_fields(v))}")
                if has_cjk(v) and not k.startswith("lang."):      # a language's own name may stay as written
                    out["cjk_in_en"].append(f"{loc}/{ns}.json {k}")
                if isinstance(zh, str):
                    for g in glossary_violations(zh, v, terms):
                        out["glossary"].append(f"{loc}/{ns}.json {k}: {g}")
    return out


def frontend_coverage() -> dict[str, dict]:
    cats = frontend_catalogs()
    base = cats.get("zh-TW", {})
    fmeta = load_json(LOCALES / "en.frontend.meta.json", {})
    out = {}
    for ns, keys in base.items():
        en = cats.get("en", {}).get(ns, {})
        out[ns] = {"keys": len(keys), "en_missing": sorted(k for k in keys if not en.get(k)),
                   "en_extra": sorted(k for k in en if k not in keys),
                   "draft": sum(1 for k in keys if (fmeta.get(f"{ns}.{k}") or {}).get("draft"))}
    return out


def views_coverage() -> dict[str, dict]:
    """Per bundled view: ids, translated titles, sidecar ids that point nowhere."""
    sys.path.insert(0, str(ROOT))
    from backend.engine.wko5expr.customviews import parse_view
    sc = load_json(VIEWS / "i18n" / "en.json", {})
    out = {}
    for p in sorted(VIEWS.glob("*.json")):
        if p.name == "wko5_fixes.json":
            continue
        raw = load_json(p, {})
        v = parse_view(raw, p)
        dids = [d.get("id") for d in raw.get("dashboards", [])]
        cids = [c.get("id") for d in raw.get("dashboards", []) for c in d.get("charts", [])]
        tr = sc.get(p.stem) or {}
        tc = tr.get("charts") or {}
        td = tr.get("dashboards") or {}
        out[p.stem] = {
            "charts": len(cids), "dashboards": len(dids),
            "missing_ids": sum(1 for x in dids + cids if not x),
            "duplicate_chart_ids": sorted({x for x in cids if x and cids.count(x) > 1}),
            "duplicate_dashboard_ids": sorted({x for x in dids if x and dids.count(x) > 1}),
            "translated_titles": sum(1 for c in tc.values() if isinstance(c, dict) and c.get("title")),
            "unknown_sidecar_ids": sorted(set(tc) - {c["id"] for d in v["dashboards"] for c in d["charts"]})
                                   + sorted(set(td) - {d["id"] for d in v["dashboards"]}),
        }
    return out


def orphans(scans: dict[str, FileScan]) -> list[dict]:
    code = {m for fs in scans.values() for _ln, m in fs.msgids}
    en = load_json(LOCALES / "en.json", {})
    out = []
    for msgid in sorted(set(en) - code):
        near = difflib.get_close_matches(msgid, list(code), n=1, cutoff=0.6)
        out.append({"msgid": msgid, "maybe_renamed_to": near[0] if near else None})
    return out


def report(scans: dict[str, FileScan]) -> dict:
    en = load_json(LOCALES / "en.json", {})
    meta = load_json(LOCALES / "en.meta.json", {})
    msgids = {m for fs in scans.values() for _ln, m in fs.msgids}
    files = {}
    for path, fs in scans.items():
        mine = {m for _ln, m in fs.msgids}
        files[path] = {"kind": fs.kind, "unwrapped": len(fs.unwrapped), "wrapped": len(fs.msgids),
                       "en_missing": sum(1 for m in mine if not en.get(m)),
                       "draft": sum(1 for m in mine if (meta.get(m) or {}).get("draft")),
                       "samples": [f"{ln}: {s[:60]}" for ln, s in fs.unwrapped[:5]],
                       "problems": fs.problems}
    return {
        "totals": {"unwrapped": sum(f["unwrapped"] for f in files.values()),
                   "msgids": len(msgids), "en_missing": sum(1 for m in msgids if not en.get(m)),
                   "files_with_unwrapped": sum(1 for f in files.values() if f["unwrapped"])},
        "files": files,
        "orphans": orphans(scans),
        "catalog_problems": catalog_problems(scans),
        "frontend": frontend_coverage(),
        "views": views_coverage(),
    }


def write_catalogs(scans: dict[str, FileScan]) -> tuple[int, int]:
    """New msgids -> en.json as null; en.meta.json src = first position. (added, total)"""
    en = load_json(LOCALES / "en.json", {})
    meta = load_json(LOCALES / "en.meta.json", {})
    added = 0
    for path, fs in sorted(scans.items()):
        for ln, m in fs.msgids:
            if m not in en:
                en[m] = None
                added += 1
            entry = meta.setdefault(m, {})
            entry.setdefault("draft", en.get(m) is None)
            src = f"{path}:{ln}"
            if not entry.get("src") or entry["src"].split(":")[0] not in scans:
                entry["src"] = src
    LOCALES.mkdir(parents=True, exist_ok=True)
    (LOCALES / "en.json").write_text(json.dumps(dict(sorted(en.items())), ensure_ascii=False, indent=2) + "\n", "utf-8")
    (LOCALES / "en.meta.json").write_text(json.dumps(dict(sorted(meta.items())), ensure_ascii=False, indent=2) + "\n", "utf-8")
    return added, len(en)


def load_baseline() -> dict:
    b = load_json(BASELINE, {})
    b.setdefault("enforce", False)
    b.setdefault("enforce_files", [])
    b.setdefault("complete", [])
    b.setdefault("files", {})
    return b


def enforced(path: str, baseline: dict) -> bool:
    """Is the ratchet a failure (not just a report) for this file? enforce=true
    = every file; else the finished pages' files listed in enforce_files."""
    return bool(baseline.get("enforce")) or any(fnmatch.fnmatch(path, g) for g in baseline.get("enforce_files", []))


def write_baseline(scans: dict[str, FileScan]) -> dict:
    b = load_baseline()
    b["files"] = {p: len(fs.unwrapped) for p, fs in sorted(scans.items()) if fs.unwrapped}
    BASELINE.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return b


def over_baseline(scans: dict[str, FileScan], baseline: Optional[dict] = None) -> list[tuple[str, int, int]]:
    """(file, now, allowed) for files with more unwrapped Chinese strings than the baseline."""
    b = (baseline or load_baseline())["files"]
    return [(p, len(fs.unwrapped), b.get(p, 0)) for p, fs in sorted(scans.items()) if len(fs.unwrapped) > b.get(p, 0)]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="add new msgids to locales/en.json + en.meta.json")
    ap.add_argument("--report", metavar="PATH", help="write the full JSON report here")
    ap.add_argument("--update-baseline", action="store_true", help="baseline.json = the current counts")
    ap.add_argument("--check", action="store_true", help="exit 1 when a file is over its baseline")
    ap.add_argument("--top", type=int, default=25, help="files to list in the summary")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    scans = scan_repo()
    if a.write:
        added, total = write_catalogs(scans)
        print(f"en.json: {added} new msgid(s), {total} in all")
    rep = report(scans)
    if a.report:
        Path(a.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", "utf-8")
        print(f"report: {a.report}")
    if a.update_baseline:
        b = write_baseline(scans)
        print(f"baseline: {sum(b['files'].values())} unwrapped string(s) in {len(b['files'])} file(s)")

    t = rep["totals"]
    print(f"unwrapped Chinese strings: {t['unwrapped']} in {t['files_with_unwrapped']} files; "
          f"msgids: {t['msgids']} ({t['en_missing']} without en); orphans: {len(rep['orphans'])}")
    rows = sorted(((f["unwrapped"], p) for p, f in rep["files"].items() if f["unwrapped"]), reverse=True)
    for n, p in rows[:a.top]:
        print(f"  {n:6d}  {p}")
    for k, v in rep["catalog_problems"].items():
        if v:
            print(f"{k}: {len(v)}")
            for x in v[:10]:
                print(f"  {x}")
    over = over_baseline(scans)
    if over:
        print(f"over the baseline ({len(over)} file(s)):")
        for p, now, allowed in over:
            print(f"  {p}: {now} > {allowed}")
    return 1 if a.check and over else 0


if __name__ == "__main__":
    sys.exit(main())
