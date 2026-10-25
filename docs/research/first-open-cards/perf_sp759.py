"""
SP-759 measurement (research only, demo data): why the 「重點數字」 and 「跑姿」 cards of the single-activity
page are slow the first time after a server start. A fresh process per scenario on a copy of the demo base:

    restart   the series store (cache/fit/<id>/series.db: workout_review.measure's rows) as the demo build
              left it — what a plain restart sees
    nostore   series.db deleted — what a deploy sees when the code measure reaches changed (its code hash
              is part of every row's key, dataset.cached_series), or a first visit after an import

In each: the Dataset is built first (timed apart: the warm-up does that before anyone opens a page), the
render cache is emptied, then the newest run's cards are asked in page order (highlights: summary first;
then the 跑姿 tab), then the second-newest run's. Each request: wall and process CPU; the first summary and
the first form card also under cProfile (top functions by cumulative time).

    python perf_sp759.py --base <demo base> --work <dir> --scenario restart|nostore [--out r.json]
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
NAS_RATIO = 8.5
ORDER = ["summary", "hr-power", "route-map", "route-difficulty", "cp-test", "wprime-battery",
         "form-halves", "form-grades", "form-work", "form-cadence"]
PROFILED = {"summary", "form-halves", "form-grades"}


def _cpu() -> float:
    s = resource.getrusage(resource.RUSAGE_SELF)
    return s.ru_utime + s.ru_stime


def _top(prof: cProfile.Profile, n: int = 25) -> dict:
    out = {}
    for key in ("cumulative", "tottime"):
        buf = io.StringIO()
        st = pstats.Stats(prof, stream=buf)
        st.sort_stats(key).print_stats(n)
        lines = [ln.rstrip().replace(str(REPO) + "/", "") for ln in buf.getvalue().splitlines() if ln.strip()]
        i = next((k for k, ln in enumerate(lines) if ln.lstrip().startswith("ncalls")), 0)
        out[key] = lines[i:i + n + 1]
    return out


def _child(a) -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    import numpy as np
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import render_cache as RC
    from backend.engine import workout_review as WR

    # the card is computed in a worker thread: profile there (wko5views._render), not around the request
    PROF = {"on": False, "p": None}
    real_render = WV._render

    def profiled_render(*args, **kw):
        if not PROF["on"]:
            return real_render(*args, **kw)
        p = cProfile.Profile()
        p.enable()
        try:
            return real_render(*args, **kw)
        finally:
            p.disable()
            PROF["p"] = p
    if a.profile:                     # only then: the wrapper is part of every chart's code signature (glue)
        WV._render = profiled_render
    if a.stable_defaults:
        # experiment (not the app's code): a default value that is a function / an object is hashed by its
        # name instead of its repr (`<function f at 0x…>` changes with every process), codehash._stable
        import types
        from backend.engine import codehash as CH
        real_stable = CH._stable

        def stable(x):
            if isinstance(x, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
                return f"<fn {getattr(x, '__module__', '')}.{getattr(x, '__qualname__', '')}>"
            if isinstance(x, (set, frozenset, dict, list, tuple)):
                return real_stable(x) if not _has_obj(x) else _walk(x)
            r = repr(x)
            return f"<{type(x).__module__}.{type(x).__qualname__}>" if " at 0x" in r else r

        def _has_obj(x):
            return " at 0x" in repr(x)

        def _walk(x):
            if isinstance(x, (set, frozenset)):
                return f"{type(x).__name__}({sorted(stable(v) for v in x)})"
            if isinstance(x, dict):
                return "{" + ",".join(sorted(f"{stable(k)}:{stable(v)}" for k, v in x.items())) + "}"
            return f"{type(x).__name__}[" + ",".join(stable(v) for v in x) + "]"
        CH._stable = stable

    app = build_app(demo=False)
    out = {"scenario": a.scenario, "loadavg_start": os.getloadavg(), "runs": []}
    with TestClient(app) as c:
        def get(path, **params):
            r = c.get(path, params=params or None)
            if r.status_code >= 400:
                raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
            return r

        c0, w0 = _cpu(), time.perf_counter()
        ds = WV._dataset()
        out["dataset"] = {"cpu": round(_cpu() - c0, 3), "wall": round(time.perf_counter() - w0, 3),
                          "n_workouts": len(ds.workouts)}
        if a.warm:
            # SP-774: what the server's warm-up does before anyone opens a page, timed apart — the chart
            # code signatures (_warm_chart_code) and, where it exists, the review cards' warm-up
            c0, w0 = _cpu(), time.perf_counter()
            WV._warm_chart_code(ds)
            out["warm_chart_code"] = {"cpu": round(_cpu() - c0, 3), "wall": round(time.perf_counter() - w0, 3)}
            if hasattr(WV, "_warm_review"):
                c0, w0 = _cpu(), time.perf_counter()
                WV._warm_review(ds)
                out["warm_review"] = {"cpu": round(_cpu() - c0, 3), "wall": round(time.perf_counter() - w0, 3)}
        views = get("/api/v1/app/views").json()
        wv = next(v for v in views if {"highlights", "form-knee"} <= {d.get("id") for d in v.get("dashboards", [])})
        urls = {}
        for dsh in wv["dashboards"]:
            for ch in dsh.get("charts", []):
                urls[ch.get("id")] = f"/api/v1/app/views/{wv['name']}/dashboards/{dsh['index']}/charts/{ch['index']}"
        runs = [w for w in ds.workouts if w.sport == "run" and ds.channel(w.idx, "heartrate") is not None]
        pick = [runs[-1], runs[-2]]
        if not a.keep_render:
            RC.CACHE.clear()
        for w in pick:
            rec = {"idx": w.idx, "date": str(w.entry.start), "sport_type": w.sport_type, "cards": []}
            for cid in ORDER:
                if cid not in urls:
                    continue
                PROF["on"], PROF["p"] = a.profile and cid in PROFILED, None
                c0, w0 = _cpu(), time.perf_counter()
                r = get(urls[cid], workout=w.idx)
                c1, w1 = _cpu(), time.perf_counter()
                PROF["on"] = False
                item = {"id": cid, "cpu": round(c1 - c0, 3), "wall": round(w1 - w0, 3), "bytes": len(r.content),
                        "stale": bool((r.json() or {}).get("stale"))}
                if PROF["p"] is not None:
                    item["top"] = _top(PROF["p"], 40)
                rec["cards"].append(item)
                print(json.dumps({"idx": w.idx, "id": cid, "wall": item["wall"]}), file=sys.stderr, flush=True)
            out["runs"].append(rec)
    out["loadavg_end"] = os.getloadavg()
    print("@@" + json.dumps(out), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--work", required=True)
    ap.add_argument("--scenario", choices=["restart", "nostore"], default="restart")
    ap.add_argument("--out")
    ap.add_argument("--profile", action="store_true", help="cProfile the summary and 跑姿 cards (slower)")
    ap.add_argument("--times", type=int, default=1, help="start the server this many times on the same home")
    ap.add_argument("--stable-defaults", action="store_true",
                    help="experiment: hash a function / object default by its name, not its per-process repr")
    ap.add_argument("--keep-render", action="store_true",
                    help="keep the render cache (cards opened by an earlier start are on disk)")
    ap.add_argument("--warm", action="store_true",
                    help="SP-774: run the warm-up's chart-code and review-card steps first (timed apart)")
    ap.add_argument("--child", action="store_true")
    a = ap.parse_args(argv)
    if a.child:
        return _child(a)
    sys.path.insert(0, str(REPO))
    from backend.scripts.perf_sp394 import _copy_base, _env
    work = Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    home = _copy_base(Path(a.base).resolve(), work / "home")
    if a.scenario == "nostore":
        for p in (home / "cache" / "fit").glob("*/series.db*"):
            p.unlink()
    env = _env(home, {"WKO5COACH_NO_WARMUP": "1", "WKO5COACH_FIT_WORKERS": "2"})
    cmd = [sys.executable, __file__, "--child", "--work", str(work), "--scenario", a.scenario] + \
        (["--profile"] if a.profile else []) + (["--keep-render"] if a.keep_render else []) + \
        (["--stable-defaults"] if a.stable_defaults else []) + (["--warm"] if a.warm else [])
    every = []
    for i in range(a.times):          # 2nd start on: the same home, so what the 1st wrote to disk is there
        p = subprocess.run(cmd, env=env, cwd=str(REPO), stdout=subprocess.PIPE, text=True)
        line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
        if p.returncode or line is None:
            print(p.stdout[-3000:])
            return p.returncode or 1
        res = json.loads(line[2:])
        every.append(res)
        print(f"start {i + 1}:", json.dumps({k: res[k] for k in ("scenario", "dataset", "warm_chart_code", "warm_review",
                                                                    "loadavg_start", "loadavg_end") if k in res}))
        for r in res["runs"]:
            print(" ", r["idx"], r["date"], r["sport_type"], " ".join(f"{c['id']}={c['wall']}s{'(stale)' if c.get('stale') else ''}" for c in r["cards"]))
    if a.out:
        Path(a.out).write_text(json.dumps(every, indent=1, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
