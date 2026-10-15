"""Markdown tables from benchmark results (SP-313).

    python docs/research/fit-parse-bench/summarize.py results/*.json      # run_playwright.py output
    python docs/research/fit-parse-bench/summarize.py phone.json          # what the page's 「複製結果」 gave

Accepts both shapes: {"config", "parts": {part: page result}} and a bare page result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MB = 2 ** 20
NAME = {"js": "JS SDK", "py": "Pyodide"}


def load(path: Path) -> list[tuple[str, dict]]:
    d = json.loads(path.read_text())
    if "parts" in d:
        return [(d["config"], r) for r in d["parts"].values()]
    return [(path.stem, d)]


def main(argv: list[str]) -> int:
    runs = [x for p in argv if not p.endswith("-dump.json") for x in load(Path(p))]
    print("| 設定 | 解析器 | 第一次載入 ms | 第二次載入 ms | 下載 MB |")
    print("|---|---|---|---|---|")
    for cfg, r in runs:
        ins = r.get("inits") or []
        if not ins:
            continue
        a = next(i for i in ins if i["nth"] == 1)
        b = next((i for i in ins if i["nth"] == 2), None)
        print(f"| {cfg} | {NAME[a['parser']]} | {a['ms']:.0f} | {b['ms']:.0f} | {a['net']['transfer_bytes'] / MB:.1f} |"
              if b else f"| {cfg} | {NAME[a['parser']]} | {a['ms']:.0f} | — | {a['net']['transfer_bytes'] / MB:.1f} |")
    print()
    print("| 設定 | 解析器 | 檔案 | KB | 筆數 | 第一次 ms | 中位數 ms | NAS s | 比對 | 記憶體增加 MB |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for cfg, r in runs:
        h = r.get("harness") or {}
        grow = (h["rss_peak_renderer"] - h["rss_baseline_renderer"]) / MB if h else None
        for e in r.get("reps") or []:
            print(f"| {cfg} | {NAME[e['parser']]} | {e['label']} | {e['size'] / 1024:.0f} | {e['records']} | {e['first_ms']:.0f}"
                  f" | {e['median_ms']:.0f} | {e['owner_nas_s']} | {'一致' if e['compare']['ok'] else '不一致'}"
                  f" | {'' if grow is None else f'{grow:.0f}（整段）'} |")
    print()
    print("| 設定 | 解析器 | 檔數 | MB | 解析 s | 最慢一檔 s | OPFS 寫 MB/s | OPFS 讀 MB/s | 記憶體增加 MB | wasm MB | 畫面最久卡 ms |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for cfg, r in runs:
        h = r.get("harness") or {}
        for f in r.get("full") or []:
            mbs = lambda ms: f"{f['total_bytes'] / MB / (ms / 1000):.0f}" if ms else "—"  # noqa: E731
            grow = f"{(h['rss_peak_renderer'] - h['rss_baseline_renderer']) / MB:.0f}" if h else "—"
            wasm = f"{f['peak_wasm_heap'] / MB:.0f}" if f.get("peak_wasm_heap") else "—"
            print(f"| {cfg} | {NAME[f['parser']]} | {f['n']} | {f['total_bytes'] / MB:.0f} | {f['parse_ms'] / 1000:.1f}"
                  f" | {f['slowest_file_ms'] / 1000:.2f} | {mbs(f.get('opfs_write_ms'))} | {mbs(f.get('opfs_read_ms'))}"
                  f" | {grow} | {wasm} | {f['max_frame_gap_ms']:.0f} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
