"""Test files for the FIT parse benchmark page (SP-313). Nothing here is the
owner's data: every file comes from the demo athlete generator.

    .venv/bin/python docs/research/fit-parse-bench/make_fixtures.py

Writes fixtures/ next to this file (git-ignored):

* demo/fit/coros/<year>/demo_*.fit — one demo year (backend.demo.generate, fixed
  seed and anchor, so the bytes are the same on every machine);
* reps: the demo files closest in size to the owner's median (93 KB) and 90th
  percentile (296 KB) files, and a synthetic 2.17 MB file (the demo's longest
  hike tiled until the file is as large as the owner's largest);
* reference/<key>.json — the per-second fields of each rep as the server's
  fitdecode reads them (compare.reference), for the page's own check;
* fitdecode.zip — the pure-Python fitdecode package from this venv, which the
  page unpacks into Pyodide (it is not in Pyodide's package index);
* manifest.json — what the page reads.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import zipfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BENCH))

import numpy as np  # noqa: E402

import compare  # noqa: E402

KB = 1024
# the owner's files (local-first-sync.md §3.5, measured on the NAS 2026-10-07)
OWNER = {
    "median": {"label": "中位數", "size": 93 * KB, "records": 3695, "nas_s": 0.65},
    "p90": {"label": "第 90 百分位", "size": 296 * KB, "records": 5472, "nas_s": 1.76},
    "max": {"label": "最大", "size": round(2.17 * KB * KB), "records": 21873, "nas_s": 12.3},
}
SEED, ANCHOR = 20261002, dt.date(2026, 10, 3)


def tiled_activity(src: Path, target: int) -> tuple[bytes, int]:
    """The activity in `src`, played forward / backward until the file is ~target bytes."""
    import fitdecode
    from backend.demo import fitwrite as FW

    cols = {k: [] for k in ("lat", "lon", "alt", "dist", "speed", "hr", "cadence", "power", "temp",
                            "fp", "ap", "lss")}
    names = {"position_lat": "lat", "position_long": "lon", "altitude": "alt", "distance": "dist",
             "speed": "speed", "heart_rate": "hr", "cadence": "cadence", "power": "power",
             "temperature": "temp", "Form Power": "fp", "Air Power": "ap", "Leg Spring Stiffness": "lss"}
    start = None
    with fitdecode.FitReader(str(src)) as fr:
        for fm in fr:
            if isinstance(fm, fitdecode.FitDataMessage) and fm.name == "record":
                v = {f.name: f.value for f in fm.fields}
                start = start or v["timestamp"]
                for n, k in names.items():
                    x = v.get(n)
                    cols[k].append(np.nan if x is None else float(x))
    a = {k: np.asarray(v) for k, v in cols.items()}
    a["lat"] *= compare.SEMI_TO_DEG
    a["lon"] *= compare.SEMI_TO_DEG
    step = np.diff(a["dist"], prepend=a["dist"][0])

    def build(n: int) -> bytes:
        reps = -(-n // len(step))
        idx = np.concatenate([np.arange(len(step)) if i % 2 == 0 else np.arange(len(step))[::-1]
                              for i in range(reps)])[:n]
        dist = np.cumsum(np.tile(step, reps)[:n])
        laps = [{"start_s": s, "duration_s": min(3600, n - s), "distance_m": float(dist[min(s + 3599, n - 1)]
                 - (dist[s - 1] if s else 0.0)), "avg_power": None} for s in range(0, n, 3600)]
        return FW.encode_activity(start=start, lat=a["lat"][idx], lon=a["lon"][idx], alt=a["alt"][idx],
                                  dist=dist, speed=a["speed"][idx], hr=a["hr"][idx], cadence=a["cadence"][idx],
                                  power=a["power"][idx], temp=a["temp"][idx], stryd=True, laps=laps,
                                  sport=FW.SPORT_HIKING, form_power=a["fp"][idx], air_power=a["ap"][idx],
                                  lss=a["lss"][idx])

    n = len(step)
    for _ in range(4):                      # size is affine in n (+ one lap per hour)
        size = len(build(n))
        per = 32
        n = max(1, n + round((target - size) / per))
    raw = build(n)
    return raw, n


def fitdecode_zip() -> bytes:
    import fitdecode
    pkg = Path(fitdecode.__file__).parent
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob("*.py")):
            z.write(p, Path("fitdecode") / p.relative_to(pkg))
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=BENCH / "fixtures")
    args = ap.parse_args()
    out: Path = args.out
    from backend.demo import generate as G

    demo = out / "demo"
    if not (demo / "fit").is_dir():
        G.generate(demo, seed=SEED, anchor=ANCHOR)
    demo_files = sorted(p for p in (demo / "fit").rglob("*.fit"))
    sizes = {p: p.stat().st_size for p in demo_files}

    reps = {}
    for key in ("median", "p90"):
        p = min(demo_files, key=lambda q: abs(sizes[q] - OWNER[key]["size"]))
        reps[key] = p.relative_to(out).as_posix()
    longest = max(demo_files, key=lambda q: sizes[q])
    raw, n = tiled_activity(longest, OWNER["max"]["size"])
    big = out / "synthetic_max.fit"
    big.write_bytes(raw)
    reps["max"] = big.relative_to(out).as_posix()

    (out / "reference").mkdir(exist_ok=True)
    rep_rows = []
    for key, rel in reps.items():
        ref = compare.reference(out / rel)
        (out / "reference" / f"{key}.json").write_text(json.dumps(ref["fields"]))
        rep_rows.append({"key": key, "label": OWNER[key]["label"], "file": rel, "size": (out / rel).stat().st_size,
                         "records": len(ref["fields"]["ts"]), "frames": ref["frames"],
                         "mac_cpython_s": round(ref["seconds"], 3), "owner": OWNER[key]})
        print(f"{key:<6} {rel:<40} {(out / rel).stat().st_size:>9} B  {len(ref['fields']['ts']):>6} records"
              f"  fitdecode here {ref['seconds']:.2f} s")
    (out / "fitdecode.zip").write_bytes(fitdecode_zip())
    manifest = {"seed": SEED, "anchor": ANCHOR.isoformat(), "reps": rep_rows,
                "demo": [{"file": p.relative_to(out).as_posix(), "size": sizes[p]} for p in demo_files],
                "fitdecode_zip": "fitdecode.zip"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(demo_files)} demo files, {sum(sizes.values()) / 1e6:.1f} MB; synthetic max from {longest.name}"
          f" ({n} s); manifest {out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
