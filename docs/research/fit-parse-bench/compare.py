"""Per-second reference from the server's parser (fitdecode) and the check of a
browser run against it (SP-313).

    python docs/research/fit-parse-bench/compare.py <dump.json> [<dump.json> ...]

A dump is what run_playwright.py saves (or what the page's "下載逐秒資料" button
gives): {"files": {key: {"file": <path under fixtures/>, "parser": "js"|"py",
"fields": {name: [values, null = missing]}}}}.

The reference reads the file the way the NAS benchmark did (fitdecode, every
frame) and keeps the record messages' fields that the app uses: time, heart
rate, power, speed, distance, altitude, position. enhanced_speed /
enhanced_altitude win over speed / altitude when both are there (both parsers
expand the components the same way).
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

FIELDS = ("ts", "hr", "power", "speed", "dist", "alt", "lat", "lon")
SEMI_TO_DEG = 180.0 / 2 ** 31
# absolute tolerances: the FIT scale of each field is coarser than these
TOL = {"ts": 0.0, "hr": 0.0, "power": 0.0, "speed": 1e-6, "dist": 1e-6, "alt": 1e-6,
       "lat": 1e-9, "lon": 1e-9}


def reference(path: Path) -> dict:
    """Read every frame with fitdecode; per-second arrays of the record messages."""
    import fitdecode

    out = {k: [] for k in FIELDS}
    t0 = time.perf_counter()
    frames = 0
    with fitdecode.FitReader(str(path)) as fr:
        for fm in fr:
            frames += 1
            if not isinstance(fm, fitdecode.FitDataMessage) or fm.name != "record":
                continue
            v = {f.name: f.value for f in fm.fields}
            ts = v.get("timestamp")
            out["ts"].append(ts.timestamp() if ts is not None else None)
            out["hr"].append(_num(v.get("heart_rate")))
            out["power"].append(_num(v.get("power")))
            out["speed"].append(_num(_first(v, "enhanced_speed", "speed")))
            out["dist"].append(_num(v.get("distance")))
            out["alt"].append(_num(_first(v, "enhanced_altitude", "altitude")))
            lat, lon = v.get("position_lat"), v.get("position_long")
            out["lat"].append(lat * SEMI_TO_DEG if lat is not None else None)
            out["lon"].append(lon * SEMI_TO_DEG if lon is not None else None)
    return {"fields": out, "frames": frames, "seconds": time.perf_counter() - t0}


def _first(v: dict, *names):
    for n in names:
        if v.get(n) is not None:
            return v[n]
    return None


def _num(x):
    return None if x is None else float(x)


def compare_fields(ref: dict, got: dict) -> dict:
    """{field: {n_ref, n_got, missing_ref, missing_got, mismatches, max_abs_diff}} and ok."""
    res, ok = {}, True
    n_ref = len(ref["ts"])
    for k in FIELDS:
        a, b = ref.get(k) or [], got.get(k) or []
        mism, maxd = 0, 0.0
        for x, y in zip(a, b):
            if x is None or y is None or (isinstance(y, float) and math.isnan(y)):
                if (x is None) != (y is None or (isinstance(y, float) and math.isnan(y))):
                    mism += 1
                continue
            d = abs(float(x) - float(y))
            maxd = max(maxd, d)
            if d > TOL[k]:
                mism += 1
        r = {"n_ref": len(a), "n_got": len(b), "missing_ref": sum(x is None for x in a),
             "missing_got": sum(y is None for y in b), "mismatches": mism, "max_abs_diff": maxd}
        if len(a) != len(b) or mism or len(a) != n_ref:
            ok = False
        res[k] = r
    return {"fields": res, "ok": ok}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    bench = Path(__file__).resolve().parent
    bad = 0
    for dump_path in argv:
        dump = json.loads(Path(dump_path).read_text())
        print(f"== {dump_path} ({dump.get('config', '')})")
        for key, d in sorted(dump["files"].items()):
            ref = reference(bench / "fixtures" / d["file"])
            r = compare_fields(ref["fields"], d["fields"])
            bad += 0 if r["ok"] else 1
            n = len(ref["fields"]["ts"])
            worst = max(r["fields"].items(), key=lambda kv: kv[1]["mismatches"])
            print(f"{'OK ' if r['ok'] else 'BAD'} {d['parser']:>2} {key:<8} records {n:>6} vs {len(d['fields']['ts']):>6}"
                  f"  mismatches {sum(f['mismatches'] for f in r['fields'].values())}"
                  f" (worst {worst[0]}: {worst[1]['mismatches']})"
                  f"  max|diff| " + ", ".join(f"{k} {f['max_abs_diff']:.2g}" for k, f in r["fields"].items()))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
