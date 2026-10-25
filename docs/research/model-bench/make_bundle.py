"""Files for the model benchmark page (SP-760). Nothing here is the owner's data:
the tenant folder is the demo athlete (`python -m backend.demo.build`).

    .venv/bin/python docs/research/model-bench/make_bundle.py --demo <demo base folder>

Writes fixtures/ next to this file (git-ignored):

* backend.zip   — backend/ as the page unpacks it into Pyodide: every .py and the
                  data files the engine reads (.json / .gpx / .txt), without tests/,
                  static/ and __pycache__;
* backend-runtime.zip — only the backend modules the CPython run below imported
                  (results/cpython-bundle-run.json), plus the same data files: what a
                  trimmed bundle could be;
* fitdecode.zip — the pure-Python fitdecode package from this venv (not in Pyodide's
                  package index; fitcache.parse_file needs it for a cold build);
* home-<variant>.zip — the demo tenant folder in three states:
    cold   FITs + app DB + plan only: the device parses every FIT itself;
    fit    + the per-file FIT parse cache (cache/fit/<home>: index.json + ch/*.npz
           channels), none of the Dataset-level caches (estimates, series.db, the
           *.json caches at the top): what a device that parsed its FITs once keeps;
    warm   the folder after one CPython run of model_run.py (every cache the
           review fills is on disk): a device handed the server's caches;
* manifest.json — sizes, for the page.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parents[2]
FIX = BENCH / "fixtures"
DATA_SUFFIXES = {".json", ".gpx", ".txt"}
SKIP_DIRS = {"tests", "static", "__pycache__"}


def backend_files() -> list[Path]:
    out = []
    for p in sorted((ROOT / "backend").rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or SKIP_DIRS & set(rel.parts):
            continue
        if p.suffix == ".py" or p.suffix in DATA_SUFFIXES:
            out.append(p)
    return out


def zip_paths(dest: Path, paths: list[Path], base: Path) -> int:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in paths:
            z.write(p, p.relative_to(base).as_posix())
    return dest.stat().st_size


def module_file(mod: str) -> Path | None:
    p = ROOT.joinpath(*mod.split("."))
    for c in (p / "__init__.py", p.with_suffix(".py")):
        if c.is_file():
            return c
    return None


def zip_tree(dest: Path, src: Path, keep=lambda rel: True) -> int:
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and keep(p.relative_to(src))]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, p.relative_to(src).as_posix())
    return dest.stat().st_size


# what the demo build leaves in the tenant folder that is input, not cache
INPUT_TOP = {"fit", "wko5coach.db", "plan.json", "demo_manifest.json", "event_gpx", "demo_courses",
             "racepower_training_env.json"}


def is_input(rel: Path) -> bool:
    return rel.parts[0] in INPUT_TOP


def is_fit_state(rel: Path) -> bool:
    """Inputs + the per-file parse cache only (cache/fit/<home>/{index.json, home.json, ch/*.npz});
    not the estimates (estimate*.json, pd_mftp.json, cp_hold.json) or the series cache (series.db)."""
    if is_input(rel):
        return True
    p = rel.parts
    return p[:2] == ("cache", "fit") and len(p) >= 4 and (p[3] in ("index.json", "home.json") or p[3] == "ch")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", type=Path, required=True, help="a demo base (…/base/<anchor>-<seed>), left untouched")
    a = ap.parse_args()
    FIX.mkdir(exist_ok=True)
    man = {"demo": a.demo.name}

    files = backend_files()
    man["backend_zip"] = {"files": len(files), "raw_bytes": sum(p.stat().st_size for p in files),
                          "zip_bytes": zip_paths(FIX / "backend.zip", files, ROOT)}

    import fitdecode
    pkg = Path(fitdecode.__file__).parent
    man["fitdecode_zip"] = {"zip_bytes": zip_paths(FIX / "fitdecode.zip",
                                                   [p for p in pkg.rglob("*.py")], pkg.parent)}

    with tempfile.TemporaryDirectory(prefix="model-bench-") as tmp:
        home = Path(tmp) / "home"
        shutil.copytree(a.demo, home)
        man["home_cold_zip"] = {"zip_bytes": zip_tree(FIX / "home-cold.zip", home, is_input)}
        man["home_fit_zip"] = {"zip_bytes": zip_tree(FIX / "home-fit.zip", home, is_fit_state)}
        # one CPython run fills every cache the review reads; that folder is "warm"
        out = BENCH / "results"
        out.mkdir(exist_ok=True)
        subprocess.run([sys.executable, str(BENCH / "model_run.py"), "--home", str(home),
                        "--json", str(out / "cpython-bundle-run.json")], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        man["home_warm_zip"] = {"zip_bytes": zip_tree(FIX / "home-warm.zip", home)}
        mods = json.loads((out / "cpython-bundle-run.json").read_text("utf-8"))["modules"]["backend"]
    rt = sorted({module_file(m) for m in mods if module_file(m)})
    data = [p for p in files if p.suffix in DATA_SUFFIXES]
    man["backend_runtime_zip"] = {"files": len(rt), "raw_bytes": sum(p.stat().st_size for p in rt),
                                  "zip_bytes": zip_paths(FIX / "backend-runtime.zip", rt + data, ROOT)}
    (FIX / "manifest.json").write_text(json.dumps(man, indent=1), "utf-8")
    print(json.dumps(man, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
