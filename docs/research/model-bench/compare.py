"""Does Pyodide give the same review cards as CPython? (SP-760)

    .venv/bin/python docs/research/model-bench/compare.py results/cpython-warm-1.json results/chromium-desktop-warm.json ...

The first file is the reference (a run_cpython.py result); every other one (a run_playwright.py
result, or another CPython one) is compared card by card for the same activities: every leaf
the same, numbers within a relative 1e-9 (or 1e-9 absolute near zero). Prints the differing
paths (first few) per file and exits 1 when any differs.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def load(p: Path) -> dict:
    d = json.loads(p.read_text("utf-8"))
    return (d.get("run") or d)["reviews"]


def diff(a, b, path="", out=None, tol=1e-9):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'ref' if k in a else 'other'}")
            else:
                diff(a[k], b[k], f"{path}/{k}", out, tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, tol)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if not (math.isclose(a, b, rel_tol=tol, abs_tol=tol) or (a != a and b != b)):
            out.append(f"{path}: {a!r} vs {b!r}")
    elif a != b:
        out.append(f"{path}: {str(a)[:80]!r} vs {str(b)[:80]!r}")
    return out


def main(argv: list[str]) -> int:
    ref = load(Path(argv[0]))
    bad = 0
    for f in argv[1:]:
        other = load(Path(f))
        n_cards, diffs = 0, []
        for label, r in ref.items():
            o = other.get(label)
            if o is None or o["file"] != r["file"]:
                diffs.append(f"{label}: not the same activity")
                continue
            for s, card in (r.get("cards") or {}).items():
                n_cards += 1
                diffs += [f"{label}/{s}{d}" for d in diff(card, (o.get("cards") or {}).get(s))]
        print(f"{Path(f).name}: {n_cards} cards, {len(diffs)} differences")
        for d in diffs[:12]:
            print("   ", d)
        bad += bool(diffs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
