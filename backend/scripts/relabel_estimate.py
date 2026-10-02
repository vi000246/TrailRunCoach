"""Relabel the old 「self-made」 label -> 「推估」 across the repo's own text files.

Owner rule: a rule / number without a primary source is labelled 「推估」, never
the old self-made label (徐國峰 himself counts as a source). Other branches keep landing text
with the old word, so this is an idempotent sweep you re-run after merges:

    python -m backend.scripts.relabel_estimate            # rewrite in place
    python -m backend.scripts.relabel_estimate --check    # list, exit 1 if any remain
    python -m backend.scripts.relabel_estimate --dry-run  # show each change, write nothing

Scope: .py .html .js .json .md under the repo root, skipping .git, virtualenvs,
.claude (agent worktrees), node_modules / build output, lock files, minified
files, and anything that is not valid UTF-8. Bytes other than the replaced
characters (BOM, CRLF) are left exactly as they were.

The old word is built from code points here so this file never matches itself.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OLD = chr(0x81EA) + chr(0x7D44)   # spelled by code point so this file never matches itself
NEW = "推估"

ROOT = Path(__file__).resolve().parents[2]
EXTS = {".py", ".html", ".js", ".json", ".md"}
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", ".claude", "node_modules", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "site-packages",
    "vendor", "third_party",
}
SKIP_FILES = {"package-lock.json"}

# Ordered context rules; the generic one goes last. Each was picked from the
# distinct contexts found in the repo (2026-10-02). OLD stands for the old word:
#  - 「OLD、推估」/「OLD，推估」 would become 「推估、推估」 -> one 推估.
#  - 「OLD推導」 means "derived by us" -> 「自行推導、推估」, not 「推估推導」.
#  - 「不跨週OLD」 (a table cell naming the rule) -> 「不跨週（推估）」.
#  - 「來OLD織」 ("comes from tissue", 來自 + 組織) is not the label -> untouched.
#  - everything else (（OLD）, OLD：, OLD的, 是OLD。, [OLD], **OLD**, OLD門檻,
#    OLD公式, OLD數字, English comments "# OLD: ...") reads naturally with a
#    straight swap.
#  - a test asserting the old word is absent ('"OLD" not in page') keeps its
#    meaning: the literal becomes the code-point escape of the old word (same
#    string at runtime, no literal left), not '"推估" not in', which would flip it.
_BS = chr(92)
OLD_ESCAPED = "".join(f"{_BS}u{ord(c):04x}" for c in OLD)
RULES: list[tuple[re.Pattern[str], object]] = [
    (re.compile("([\"'])" + OLD + "\\1(\\s+not\\s+in\\b)"),
     lambda m: m.group(1) + OLD_ESCAPED + m.group(1) + m.group(2)),
    (re.compile(OLD + "[、，,]\\s*" + NEW), NEW),
    (re.compile(NEW + "[、，,]\\s*" + OLD), NEW),
    (re.compile(OLD + "推導"), "自行推導、" + NEW),
    (re.compile("不跨週" + OLD), "不跨週（" + NEW + "）"),
    (re.compile("(?<!來)" + OLD + "(?!織)"), NEW),
]


def relabel(text: str) -> str:
    for pat, rep in RULES:
        text = pat.sub(rep, text)
    return text


def iter_files(root: Path = ROOT):
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_dir():
                if p.name not in SKIP_DIRS and not p.is_symlink():
                    stack.append(p)
            elif (p.suffix.lower() in EXTS and p.name not in SKIP_FILES
                  and not p.name.endswith((".min.js", ".min.css"))):
                yield p


def _read(p: Path) -> str | None:
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def remaining(text: str) -> list[int]:
    """Line numbers still carrying the label (the 來+OLD+織 exception excluded)."""
    pat = re.compile("(?<!來)" + OLD + "(?!織)")
    return [i for i, line in enumerate(text.splitlines(), 1) if pat.search(line)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="report only; exit 1 if any remain")
    ap.add_argument("--dry-run", action="store_true", help="print each changed line; write nothing")
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args(argv)

    total = files = 0
    for p in iter_files(a.root):
        text = _read(p)
        if text is None or OLD not in text:
            continue
        rel = p.relative_to(a.root).as_posix()
        hits = remaining(text)
        if not hits:
            continue
        n = len(re.findall("(?<!來)" + OLD + "(?!織)", text))
        total += n
        files += 1
        if a.check:
            print(f"{rel}: {n} (lines {', '.join(map(str, hits[:12]))}{' ...' if len(hits) > 12 else ''})")
            continue
        new = relabel(text)
        if a.dry_run:
            for old_l, new_l in zip(text.splitlines(), new.splitlines()):
                if old_l != new_l:
                    print(f"{rel}:\n  - {old_l.strip()[:160]}\n  + {new_l.strip()[:160]}")
            continue
        p.write_bytes(new.encode("utf-8"))
        print(f"{rel}: {n}")

    verb = "remaining" if a.check else ("would change" if a.dry_run else "replaced")
    print(f"{verb}: {total} in {files} files")
    return 1 if (a.check and total) else 0


if __name__ == "__main__":
    sys.exit(main())
