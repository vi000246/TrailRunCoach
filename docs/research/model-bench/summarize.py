"""Tables for the SP-760 write-up from results/*.json (run_cpython.py, run_playwright.py).

    .venv/bin/python docs/research/model-bench/summarize.py [--tag ""]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
MB = 2 ** 20


def fmt_s(x) -> str:
    if x is None:
        return "—"
    return f"{x * 1000:.0f} ms" if x < 1 else f"{x:.1f} 秒" if x < 120 else f"{x / 60:.1f} 分"


def reviews(run: dict) -> str:
    rv = run.get("reviews") or {}
    return " · ".join(f"{k} {fmt_s(v['cold']['total_s'])}／{fmt_s(v['warm']['total_s'])}" for k, v in rv.items())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    res = BENCH / "results"
    print("## CPython (median of repeats)")
    for home in ("warm", "fit", "cold"):
        rs = [json.loads(p.read_text("utf-8")) for p in sorted(res.glob(f"cpython-{home}-*.json"))]
        if not rs:
            continue
        med = lambda f: statistics.median(f(r) for r in rs)  # noqa: E731
        print(f"| {home} | import {fmt_s(med(lambda r: r['import_s']))} | hashes {fmt_s(med(lambda r: (r.get('code_hashes') or {}).get('took_s', 0)))}"
              f" | dataset {fmt_s(med(lambda r: r['dataset_s']))} | {reviews(rs[0])} | max RSS {med(lambda r: r['maxrss_bytes']) / MB:.0f} MB |")
    print("\n## Browsers")
    for p in sorted(res.glob(f"{a.tag}chromium-*.json")) + sorted(res.glob(f"{a.tag}firefox-*.json")):
        d = json.loads(p.read_text("utf-8"))
        i, r, h = d.get("init") or {}, d.get("run") or {}, d["harness"]
        ph = i.get("phases", {})
        net = i.get("net_pyodide", {})
        boot = (ph.get("import_mjs_ms", 0) + ph.get("load_pyodide_ms", 0)) / 1000
        print(f"| {p.stem} | boot {fmt_s(boot)} | pkgs {fmt_s(ph.get('load_packages_ms', 0) / 1000)}"
              f" | unpack {fmt_s(ph.get('unpack_ms', 0) / 1000)} | net {net.get('body_bytes', 0) / MB:.1f} MB"
              f" ({net.get('decoded_bytes', 0) / MB:.1f} MB raw) | import {fmt_s(r.get('import_s'))}"
              f" | hashes {fmt_s((r.get('code_hashes') or {}).get('took_s'))} | dataset {fmt_s(r.get('dataset_s'))}"
              f" | {reviews(r)} | wasm {((r.get('wasm_heap_after') or 0) / MB):.0f} MB"
              f" | RSS peak {h['rss_peak_renderer'] / MB:.0f} MB (base {h['rss_baseline_renderer'] / MB:.0f})"
              f" | wall {fmt_s(h['wall_s'])} | err {d.get('error') or r.get('error')} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
