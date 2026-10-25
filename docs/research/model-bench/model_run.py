"""Run the single-activity review model (backend/engine/workout_review.py) on a
demo tenant folder and time it (SP-760). The same file runs in CPython (the
baseline) and inside Pyodide (bench-worker.js imports it):

    .venv/bin/python docs/research/model-bench/model_run.py --home <demo base> [--json out.json]

<demo base> is a tenant folder from `python -m backend.demo.build` (the made-up
athlete, never the owner's data). Steps, each timed:

1. import  — `backend.engine.workout_review` and the Dataset code;
2. dataset — the FIT-folder Dataset of the tenant (dataset_for_source, as the
   server's chart / review routes build it, hot-first off: every file). With
   the folder's FIT cache present (fitcache index + npz channels) this reads
   the cache; without it it parses every FIT with fitdecode first;
3. review  — workout_review.review(ds, w, section) for the six SECTIONS of a
   few representative activities (the median / 90th percentile / longest run
   or hike of the last 90 days by duration, plus the last interval session if
   there is one), twice: the first call per activity is cold (the per-activity
   measure() memo is empty), the second warm.

Prints one JSON object (and writes it with --json). Under CPython also lists
every module the run imported (`modules`): the runtime closure, which is much
smaller than the static upper bound of import_closure.py.
"""
from __future__ import annotations

import json
import os
import sys
import time

SECTIONS = ("summary", "aerobic", "intervals", "climbs", "durability", "form")


def setup(home: str) -> None:
    """Point the app at the demo tenant only (owner mode, no WKO5 folder, no background work)."""
    os.environ.update({
        "TRC_HOME": home, "WKO5COACH_HOME": home,
        "TRC_ATHLETE_DIR": os.path.join(home, "_no_wko5"), "WKO5_ATHLETE_DIR": os.path.join(home, "_no_wko5"),
        "TRC_NO_SCHEDULER": "1", "TRC_NO_AUTO_BACKUP": "1", "TRC_NO_WARMUP": "1",
        "TRC_DEFAULT_LOCALE": "zh-TW",
    })
    for k in ("WKO5COACH_MODE", "TRC_MODE", "WKO5COACH_FIT_CACHE", "TRC_FIT_CACHE"):
        os.environ.pop(k, None)


def _now() -> float:
    return time.perf_counter()


def mem() -> int | None:
    """Memory so far: the wasm heap in Pyodide (it only grows, so this is the peak), the max RSS
    in CPython (bytes on macOS)."""
    try:
        import pyodide_js
        return int(pyodide_js._module.HEAP8.buffer.byteLength)
    except Exception:  # noqa: BLE001 — not Pyodide
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        return None


def import_model() -> dict:
    t0 = _now()
    import backend.engine.workout_review  # noqa: F401
    from backend.engine.wko5expr import fitdataset  # noqa: F401
    return {"import_s": _now() - t0}


def code_hashes() -> dict:
    """The code-version hashes the disk caches are keyed on (engine/codehash.py): when the
    page's differ from the server's, every cache the server wrote is a miss on the device."""
    from backend.engine.wko5expr import dataset as D, fitdataset as FD
    t0 = _now()
    out = {}
    for name, fn in (("estimate", FD.estimate_code), ("pd", FD.pd_code),
                     ("workout_curves", lambda: D.per_workout_code("workout_curves")),
                     ("channel_peaks", lambda: D.per_workout_code("channel_peaks"))):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            out[name] = f"{type(e).__name__}: {e}"[:200]
    out["took_s"] = _now() - t0
    return out


def build_dataset():
    from backend.engine.wko5expr import datasource as DSRC
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import dataset_for_source, hot_first_builds
    from backend.settings.paths import athlete_dir, for_tenant
    t0 = _now()
    cfg = EngineConfig.load()
    src = DSRC.current_source()
    with hot_first_builds(False):
        ds = dataset_for_source(src, for_tenant(athlete_dir()), config=cfg)
    n = len(ds.workouts)
    return ds, {"dataset_s": _now() - t0, "source": src, "workouts": n}


def _dur(w) -> float:
    m = w.metrics or {}
    for k in ("duration", "moving_time", "elapsed_time", "time"):
        v = m.get(k)
        if v:
            return float(v)
    return 0.0


def pick(ds) -> tuple[dict, dict]:
    """({label: workout idx}, {idx: samples}) — representative activities of the last 90 days."""
    from backend.engine import workout_review as WR  # noqa: F401
    last = max(w.day for w in ds.workouts)
    recent = [w for w in ds.workouts if w.day > last - 90 and w.sport in ("run", "walk", "hike")]
    recent = [w for w in recent if ds.wko4(w.idx) is not None]
    out = {}
    if not recent:
        return out, {}
    t = {w.idx: (len(ds.wko4(w.idx).channels["elapsedtime"].values)
                 if "elapsedtime" in ds.wko4(w.idx).channels else 0) for w in recent}
    by = sorted(recent, key=lambda w: t[w.idx])
    out["median"] = by[len(by) // 2].idx
    out["p90"] = by[min(len(by) - 1, int(len(by) * 0.9))].idx
    out["longest"] = by[-1].idx
    for w in sorted(recent, key=lambda w: -w.day):
        title = (getattr(w.entry, "title", "") or "") + " " + " ".join(w.tags or [])
        if "間歇" in title or "interval" in title.lower() or "×" in title:
            out["interval"] = w.idx
            break
    return out, t


def review_one(ds, idx: int) -> dict:
    from backend.engine import workout_review as WR
    w = ds.workouts[idx]
    res = {"idx": idx, "file": w.entry.file, "sport_type": w.sport_type,
           "samples": len(ds.wko4(idx).channels["elapsedtime"].values)}
    for rnd in ("cold", "warm"):
        per, t_all = {}, _now()
        for s in SECTIONS:
            t0 = _now()
            card = WR.review(ds, w, s)
            per[s] = round(_now() - t0, 4)
            if rnd == "warm":
                res.setdefault("cards", {})[s] = card       # compare.py: the same answer as CPython?
            if rnd == "cold" and s == "summary":
                res["type"] = (card.get("classification") or {}).get("type")
                res["empty"] = card.get("empty")
        res[rnd] = {"total_s": round(_now() - t_all, 4), "sections_s": per}
    return res


class WhoImports:
    """A meta-path hook that records, for the first import of each top-level package in
    `names`, the chain of backend files (file:line) on the stack: who pulls it in."""

    def __init__(self, names):
        self.names, self.seen = set(names), {}

    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top in self.names and top not in self.seen:
            import traceback
            chain = [f"{os.path.relpath(f.filename)}:{f.lineno}" for f in traceback.extract_stack()
                     if "/backend/" in f.filename]
            self.seen[top] = chain[-6:]
        return None


def run(home: str, tracemem: bool = False, who=None) -> dict:
    setup(home)
    hook = None
    if who:
        hook = WhoImports(who)
        sys.meta_path.insert(0, hook)
    before = set(sys.modules)
    out = {"python": sys.version.split()[0], "platform": sys.platform}
    if tracemem:
        import tracemalloc
        tracemalloc.start()
    m = out["mem"] = {"start": mem()}
    out.update(import_model())
    m["import"] = mem()
    out["code_hashes"] = code_hashes()
    m["hashes"] = mem()
    ds, info = build_dataset()
    m["dataset"] = mem()
    out.update(info)
    picked, samples = pick(ds)
    out["picked"] = picked
    out["reviews"] = {}
    for label, idx in picked.items():
        out["reviews"][label] = review_one(ds, idx)
        m[f"review_{label}"] = mem()
    if tracemem:
        import tracemalloc
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        out["tracemalloc_peak_bytes"] = peak
    if hook is not None:
        sys.meta_path.remove(hook)
        out["who_imports"] = hook.seen
    mods = sorted(set(sys.modules) - before)
    out["modules"] = {
        "backend": [m for m in mods if m.split(".")[0] == "backend"],
        "third_party": sorted({m.split(".")[0] for m in mods if m.split(".")[0] not in sys.stdlib_module_names
                               and m.split(".")[0] not in ("backend", "_distutils_hack")
                               and not m.startswith("_")}),
        "stdlib": sorted({m.split(".")[0] for m in mods if m.split(".")[0] in sys.stdlib_module_names}),
    }
    return out


def main() -> int:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", required=True)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--tracemalloc", action="store_true")
    ap.add_argument("--who", default="", help="comma list of packages: record which backend code imports each")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    res = run(str(Path(a.home).resolve()), a.tracemalloc, [x for x in a.who.split(",") if x])
    try:
        import resource
        res["maxrss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS
    except ImportError:
        pass
    text = json.dumps(res, indent=1, ensure_ascii=False, default=str)
    if a.json:
        a.json.write_text(text, "utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
