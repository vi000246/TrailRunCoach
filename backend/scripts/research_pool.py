"""
The research pool's operator commands (SP-585 / SP-586 / SP-587; backend/research). Server-internal:
run on the server, by hand; nothing here is an API route and nothing is sent anywhere.

    python -m backend.scripts.research_pool rules                 the source allow-list in force (JSON)
    python -m backend.scripts.research_pool export [--rules F] [--k 5]
                                                                  the B layer + grid into research/pool.db
                                                                  (refused unless TRC_RESEARCH_POOL=1)
    python -m backend.scripts.research_pool sweep                 withdrawals now (also hourly, scheduler)
    python -m backend.scripts.research_pool expire                retention now (also hourly, scheduler)
    python -m backend.scripts.research_pool withdraw <user id>    one person's linked rows and link
    python -m backend.scripts.research_pool report-a [--k N | --audience internal|public] [--write] [--out F]
                                                                  the A layer's k-threshold report (SP-587);
                                                                  --write: the groups into pool.db a_params

Prints counts only — never a person, a donor id or a value.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _policy(path: Optional[Path]):
    from backend.research import sources as S
    return S.load_rules(path) if path else S.SourcePolicy()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="research_pool", description="the research pool (server-internal)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rules")
    r.add_argument("--rules", type=Path)
    e = sub.add_parser("export")
    e.add_argument("--rules", type=Path, help="a JSON allow-list replacing the defaults whole")
    e.add_argument("--k", type=int, default=5, help="grid cells need at least k distinct people (>= 5)")
    sub.add_parser("sweep")
    sub.add_parser("expire")
    w = sub.add_parser("withdraw")
    w.add_argument("user_id", type=int)
    ra = sub.add_parser("report-a")
    ra.add_argument("--rules", type=Path)
    ra.add_argument("--audience", choices=("internal", "public"), default="internal")
    ra.add_argument("--k", type=int, help="overrides the audience's k (internal 5, public 10); never below 5")
    ra.add_argument("--write", action="store_true", help="write the groups into research/pool.db (a_params)")
    ra.add_argument("--out", type=Path, help="also save the report (JSON) here")
    a = ap.parse_args(argv)
    from backend.research import pool as P
    if a.cmd == "rules":
        print(_policy(a.rules).to_json())
        return 0
    if a.cmd == "export":
        if a.k < 5:
            print("k below 5 is refused (SP-326 Q1-7)", file=sys.stderr)
            return 2
        try:
            res = P.export(policy=_policy(a.rules), k=a.k)
        except P.PoolDisabled as ex:
            print(str(ex), file=sys.stderr)
            return 2
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "sweep":
        print(json.dumps({"withdrawn": P.sweep()}))
        return 0
    if a.cmd == "expire":
        print(json.dumps(P.expire()))
        return 0
    if a.cmd == "withdraw":
        print(json.dumps(P.withdraw(a.user_id)))
        return 0
    if a.cmd == "report-a":
        from backend import research
        from backend.research import a_layer as A
        k = a.k if a.k is not None else A.K_BY_AUDIENCE[a.audience]
        if k < 5:
            print("k below 5 is refused (SP-326 Q1-7)", file=sys.stderr)
            return 2
        if not research.enabled():                       # it reads every tenant: off like the export
            print(f"set {research.ENV_ENABLED}=1 to compute the A layer", file=sys.stderr)
            return 2
        rep, params, _grid = A.evaluate(A.collect(policy=_policy(a.rules)), k)
        if a.write:
            rep["written_rows"] = A.write(params)
        txt = json.dumps(rep, ensure_ascii=False, indent=2)
        print(txt)
        if a.out:
            a.out.write_text(txt, "utf-8")
        return 0
    return 2


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
