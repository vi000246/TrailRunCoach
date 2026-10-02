"""
Merge machine-drafted translations into the catalogs, marked draft
(docs/plans/i18n.plan.md §4).

    python -m backend.scripts.i18n_merge_frags FRAG.json ... [--draft-ns overview,autoplan]

  * FRAG.json = {"<msgid>": "English" | {"one": …, "other": …}}: fills
    locales/en.json (a msgid already translated keeps its text unless it is
    null) and sets en.meta.json {"draft": true, "src": "<file>:<line>"}.
  * --draft-ns: every key of these frontend namespaces that has English is
    marked draft in locales/en.frontend.meta.json ({"<ns>.<key>": {"draft": true}}).

The owner reviews on the demo page and removes `draft` (per msgid / key).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.scripts import i18n_extract as X

FRONT_META = X.LOCALES / "en.frontend.meta.json"


def _dump(p: Path, d: dict) -> None:
    p.write_text(json.dumps(dict(sorted(d.items())), ensure_ascii=False, indent=2) + "\n", "utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frags", nargs="*")
    ap.add_argument("--draft-ns", default="")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    en = X.load_json(X.LOCALES / "en.json", {})
    meta = X.load_json(X.LOCALES / "en.meta.json", {})
    src = {}
    for path, fs in X.scan_repo().items():
        for ln, m in fs.msgids:
            src.setdefault(m, f"{path}:{ln}")
    added = 0
    for f in a.frags:
        for m, v in X.load_json(Path(f), {}).items():
            if not v or en.get(m):
                continue
            en[m] = v
            e = meta.setdefault(m, {})
            e["draft"] = True
            if m in src:
                e["src"] = src[m]
            added += 1
    for m in src:                                   # wrapped but not translated yet
        if m not in en:
            en[m] = None
            meta.setdefault(m, {"draft": True, "src": src[m]})
    _dump(X.LOCALES / "en.json", en)
    _dump(X.LOCALES / "en.meta.json", meta)
    print(f"en.json: {added} draft translation(s) merged, {sum(1 for v in en.values() if not v)} still null")
    nss = [x for x in a.draft_ns.split(",") if x]
    if nss:
        fm = X.load_json(FRONT_META, {})
        cats = X.frontend_catalogs().get("en", {})
        n = 0
        for ns in nss:
            for k, v in cats.get(ns, {}).items():
                if v and f"{ns}.{k}" not in fm:
                    fm[f"{ns}.{k}"] = {"draft": True}
                    n += 1
        _dump(FRONT_META, fm)
        print(f"en.frontend.meta.json: {n} key(s) marked draft")
    return 0


if __name__ == "__main__":
    sys.exit(main())
