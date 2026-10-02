"""
Check a set of files against translations (a translator's working check).

    python -m backend.scripts.i18n_check_files FILE... [--frag PATH.json ...]

For each Python file: the Chinese strings still unwrapped, and the _() / N_()
msgids that have no English in locales/en.json nor in any --frag file
(a fragment = {"<msgid>": "English" | {"one": …, "other": …}}, merged into
en.json later by `i18n_merge_frags`). For HTML / JS: the unwrapped Chinese
strings. Then, for every msgid / fragment entry: placeholders equal, no
Chinese in the English, the glossary's terms. Exit 1 when anything is left.
One process, no subprocesses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.scripts import i18n_extract as X


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--frag", action="append", default=[])
    ap.add_argument("--max", type=int, default=40, help="lines to print per file")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    en = dict(X.load_json(X.LOCALES / "en.json", {}))
    frag: dict = {}
    for f in a.frag:
        frag.update(X.load_json(Path(f), {}))
    have = {k: v for k, v in {**en, **frag}.items() if v}
    allow = X.load_allow()
    terms = X.glossary_terms()
    bad = 0
    for f in a.files:
        p = (X.ROOT / f).resolve() if not Path(f).is_absolute() else Path(f)
        fs = X.scan_file(p)
        r = X.rel(p)
        if allow.file_ok(r):
            fs.unwrapped = []
        else:
            fs.unwrapped = [(ln, s) for ln, s in fs.unwrapped if not allow.text_ok(s)]
        missing = sorted({m for _ln, m in fs.msgids if not have.get(m)})
        print(f"{r}: unwrapped {len(fs.unwrapped)}, msgids {len(fs.msgids)}, no en {len(missing)}"
              + (f", problems {len(fs.problems)}" if fs.problems else ""))
        for ln, s in fs.unwrapped[:a.max]:
            print(f"   U {ln}: {s[:90]!r}")
        for m in missing[:a.max]:
            print(f"   M {m[:90]!r}")
        for pr in fs.problems[:a.max]:
            print(f"   P {pr}")
        bad += len(fs.unwrapped) + len(missing) + len(fs.problems)
        for _ln, m in fs.msgids:
            v = have.get(m)
            for t in X._texts(v):
                if X.py_fields(t) != X.py_fields(m):
                    print(f"   F placeholders {m[:60]!r}: {sorted(X.py_fields(m))} vs {sorted(X.py_fields(t))}")
                    bad += 1
                if X.has_cjk(t):
                    print(f"   C Chinese in en: {m[:60]!r}")
                    bad += 1
                for g in X.glossary_violations(m, t, terms):
                    print(f"   G glossary {m[:60]!r}: {g}")
                    bad += 1
    # frontend catalogs (all namespaces): placeholders / Chinese / glossary
    probs = X.catalog_problems({})
    for k, v in probs.items():
        for x in v:
            if not x.startswith("backend en"):
                print(f"   {k}: {x}")
                bad += 1
    for ns, cov in X.frontend_coverage().items():
        if cov["en_extra"]:
            print(f"   en keys not in zh-TW/{ns}.json: {cov['en_extra'][:10]}")
            bad += 1
    print("OK" if not bad else f"{bad} item(s) left")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
