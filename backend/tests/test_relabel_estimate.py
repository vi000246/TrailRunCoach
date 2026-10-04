"""Unsourced rules are labelled 「推估」, never the old self-made label (owner rule).

Fails when the old word shows up in anything the user can see: the static
pages / scripts, the view JSON, and Python string literals
under backend/ (API / engine text ends up in the UI). Fix: run
`python -m backend.scripts.relabel_estimate`.

The old word is built from code points so this file never matches itself.
Reads only files inside the repo (no ~/WKO5, no ~/.wko5coach).
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest

from backend.scripts.relabel_estimate import NEW, OLD, relabel, remaining

ROOT = Path(__file__).resolve().parents[2]
FIX = "run: python -m backend.scripts.relabel_estimate"


@pytest.mark.parametrize("before, after", [
    (f"（{OLD}）", "（推估）"),
    (f"{OLD}：門檻", "推估：門檻"),
    (f"{OLD}的固定值", "推估的固定值"),
    (f"0.75 是{OLD}。", "0.75 是推估。"),
    (f"**[{OLD}]**", "**[推估]**"),
    (f"（{OLD}門檻）", "（推估門檻）"),
    (f"# {OLD}: a sanity range", "# 推估: a sanity range"),
    (f"S 的模型是{OLD}、推估；", "S 的模型是推估；"),
    (f"（泰勒展開，{OLD}推導）", "（泰勒展開，自行推導、推估）"),
    (f"| 不跨週{OLD} |", "| 不跨週（推估） |"),
    (f"限制來{OLD}織，不是", f"限制來{OLD}織，不是"),   # 來自 + 組織: not the label
])
def test_relabel_contexts(before, after):
    assert relabel(before) == after


def test_negative_assertion_keeps_checking_the_old_word():
    src = f'assert "{OLD}" not in page'
    out = relabel(src)
    assert OLD not in out and NEW not in out
    ns: dict = {"page": "ok"}
    exec(out, ns)                       # still passes on clean text
    with pytest.raises(AssertionError):
        exec(out, {"page": f"x{OLD}x"})  # and still catches the old word
    assert relabel(out) == out


def test_relabel_is_idempotent():
    s = f"a（{OLD}）b {OLD}、推估 c {OLD}推導 d 不跨週{OLD} e 來{OLD}織"
    once = relabel(s)
    assert relabel(once) == once
    assert not remaining(once)
    assert NEW + NEW not in once and f"{NEW}、{NEW}" not in once


def _ui_text_files():
    pats = [("backend/static", "*.html"), ("backend/static", "*.js"), ("backend/static", "*.css"),
            ("views", "*.json")]
    for base, pat in pats:
        d = ROOT / base
        if d.is_dir():
            yield from (p for p in d.rglob(pat) if "node_modules" not in p.parts)


def test_no_old_label_in_ui_files():
    bad = []
    for p in _ui_text_files():
        hits = remaining(p.read_text(encoding="utf-8", errors="replace"))
        if hits:
            bad.append(f"{p.relative_to(ROOT).as_posix()}: lines {hits[:10]}")
    assert not bad, f"old label in UI files ({FIX}):\n  " + "\n  ".join(bad)


def _py_sources():
    for p in (ROOT / "backend").rglob("*.py"):
        rel = p.relative_to(ROOT).parts
        if "tests" in rel or "__pycache__" in rel:
            continue
        yield p


# Python 3.12+ splits f-strings into FSTRING_* tokens; the text is in FSTRING_MIDDLE.
_STRING_TOKENS = {tokenize.STRING} | ({tokenize.FSTRING_MIDDLE} if hasattr(tokenize, "FSTRING_MIDDLE") else set())


def test_no_old_label_in_python_strings():
    bad = []
    for p in _py_sources():
        src = p.read_text(encoding="utf-8", errors="replace")
        if OLD not in src:
            continue
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        except (tokenize.TokenError, SyntaxError):
            continue
        for t in toks:
            if t.type in _STRING_TOKENS and remaining(t.string):
                bad.append(f"{p.relative_to(ROOT).as_posix()}:{t.start[0]}")
    assert not bad, f"old label in Python strings ({FIX}):\n  " + "\n  ".join(bad[:30])
