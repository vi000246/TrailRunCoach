"""The CPython baseline of the model benchmark (SP-760): the same model_run.py on
the same three tenant-folder states the page uses (fixtures/home-<variant>.zip).

    .venv/bin/python docs/research/model-bench/run_cpython.py [--homes warm,fit,cold] [--repeat 3]

Each run unpacks the zip into a fresh temporary folder (so no run sees another's
caches), runs model_run.py in a child process with --tracemalloc off (it slows numpy
code) and writes results/cpython-<home>-<n>.json (with the child's max RSS).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--homes", default="warm,fit,cold")
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    out = BENCH / "results"
    out.mkdir(exist_ok=True)
    for home in [h for h in a.homes.split(",") if h]:
        for n in range(1, a.repeat + 1):
            with tempfile.TemporaryDirectory(prefix="model-bench-") as tmp:
                with zipfile.ZipFile(BENCH / "fixtures" / f"home-{home}.zip") as z:
                    z.extractall(tmp)
                dest = out / f"cpython-{home}-{n}.json"
                subprocess.run([sys.executable, str(BENCH / "model_run.py"), "--home", tmp, "--json", str(dest)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            r = json.loads(dest.read_text("utf-8"))
            rv = {k: (v["cold"]["total_s"], v["warm"]["total_s"]) for k, v in r["reviews"].items()}
            print(f"[cpython/{home}#{n}] import {r['import_s']:.2f} s, dataset {r['dataset_s']:.2f} s, "
                  f"reviews (cold, warm) {rv}, max RSS {r.get('maxrss_bytes', 0) / 2**20:.0f} MB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
