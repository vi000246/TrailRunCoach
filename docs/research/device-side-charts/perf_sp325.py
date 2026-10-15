"""
SP-325 measurement (research only, demo data): the single-activity analysis page (views/workout.json)
on the server — per chart: CPU time on a first open (render cache empty, a fresh process: ds.memo cold)
and on a re-open (render cache hit), JSON size raw / gzip; the /samples payload; and the size of a
proposed "plot data" binary (every 5 s / 1 s, float32, t + channels), raw / gzip.

    python perf_sp325.py --base <demo base> --work <dir> [--out r.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
NAS_RATIO = 8.5


def _cpu() -> float:
    s = resource.getrusage(resource.RUSAGE_SELF)
    c = resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime


def _gz(b: bytes) -> int:
    return len(gzip.compress(b, 6))


def _child(a) -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    import numpy as np
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import render_cache as RC

    app = build_app(demo=False)
    out = {"loadavg_start": os.getloadavg(), "workouts": []}
    with TestClient(app) as c:
        def get(path, **params):
            r = c.get(path, params=params or None)
            if r.status_code >= 400:
                raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
            return r

        views = get("/api/v1/app/views").json()
        wv = next(v for v in views if {"highlights", "aerobic-drift"} <= {d.get("id") for d in v.get("dashboards", [])})
        src = {c["id"]: c for d in json.loads((REPO / "views" / "workout.json").read_text())["dashboards"] for c in d["charts"]}
        charts = []
        for dsh in wv["dashboards"]:
            for ch in dsh.get("charts", []):
                ch = {**ch, **{k: v for k, v in src.get(ch.get("id"), {}).items() if k in ("kind", "chart", "section")}}
                charts.append({"id": ch.get("id"), "kind": ch.get("kind"), "chart": ch.get("chart") or ch.get("section"),
                               "url": f"/api/v1/app/views/{wv['name']}/dashboards/{dsh['index']}/charts/{ch['index']}"})
        out["view"] = wv["name"]
        ds = WV._dataset()
        # pick: runs/trail runs with HR, shortest-ish, median, longest
        rows = []
        for w in ds.workouts:
            t = ds.channel(w.idx, "elapsedtime")
            hr = ds.channel(w.idx, "heartrate")
            if t is None or hr is None or len(t) < 600 or w.sport not in ("run",):
                continue
            rows.append((float(np.nanmax(t)), w.idx, w.sport_type))
        rows.sort()
        pick = [rows[len(rows) // 10], rows[len(rows) // 2], rows[-2], rows[-1]]
        out["n_candidates"] = len(rows)
        RC.CACHE.clear()
        first = True
        for dur, idx, st in pick:
            w = ds.workouts[idx]
            rec = {"idx": idx, "sport_type": st, "duration_s": dur, "n_samples": len(ds.channel(idx, "elapsedtime")),
                   "charts": []}
            for ch in charts:
                # first open: render cache empty
                c0, w0 = _cpu(), time.perf_counter()
                r = get(ch["url"], workout=idx)
                c1, w1 = _cpu(), time.perf_counter()
                body = r.content
                # re-open: render cache hit
                c2, w2 = _cpu(), time.perf_counter()
                get(ch["url"], workout=idx)
                c3, w3 = _cpu(), time.perf_counter()
                rec["charts"].append({**{k: ch[k] for k in ("id", "kind", "chart")},
                                      "cold_cpu": round(c1 - c0, 4), "cold_wall": round(w1 - w0, 4),
                                      "hit_cpu": round(c3 - c2, 4), "hit_wall": round(w3 - w2, 4),
                                      "bytes": len(body), "gzip": _gz(body)})
            # /samples (not render-cached)
            c0, w0 = _cpu(), time.perf_counter()
            r = get(f"/api/v1/app/workouts/{idx}/samples")
            c1, w1 = _cpu(), time.perf_counter()
            rec["samples"] = {"cpu": round(c1 - c0, 4), "wall": round(w1 - w0, 4), "bytes": len(r.content),
                              "gzip": _gz(r.content), "n": r.json()["n"], "step": r.json()["step"]}
            # proposed plot data: t (s, from 0) + hr, power, speed (km/h), altitude, distance on a 1-s grid,
            # then every 5 s; float32 little-endian, column-major
            t = np.asarray(ds.channel(idx, "elapsedtime"), float)
            ok = np.isfinite(t)
            t0 = t[ok][0]
            g = np.arange(0.0, math.floor(t[ok][-1] - t0) + 1.0)
            chans = {}
            for name in ("heartrate", "power", "speed", "elevation", "elapseddistance", "cadence", "latitude", "longitude"):
                x = ds.channel(idx, name)
                if x is None or len(x) != len(t):
                    continue
                x = np.asarray(x, float)
                m = ok & np.isfinite(x)
                if m.sum() < 2:
                    continue
                chans[name] = np.interp(g, t[m] - t0, x[m])
            rec["channels_present"] = sorted(chans)

            def pack(step, names, dtype):
                cols = [g[::step]] + [chans[n][::step] for n in names if n in chans]
                return np.stack(cols).astype(dtype).tobytes()
            four = ["heartrate", "power", "speed", "elevation"]
            five = four + ["elapseddistance"]
            gps = ["latitude", "longitude"]
            rec["plotdata"] = {}
            for label, step, names, dt in (("5s_t+4_f32", 5, four, "<f4"), ("5s_t+5_f32", 5, five, "<f4"),
                                           ("1s_t+5_f32", 1, five, "<f4"), ("5s_t+5+gps_f32", 5, five + gps, "<f4"),
                                           ("5s_t+5+gps_f64", 5, five + gps, "<f8")):
                b = pack(step, names, dt)
                rec["plotdata"][label] = {"bytes": len(b), "gzip": _gz(b)}
            # float32 precision of GPS at 5 s (metres): max error vs float64
            if "latitude" in chans:
                la = chans["latitude"][::5]
                err = np.nanmax(np.abs(la.astype("<f4").astype(float) - la)) * 111_320
                rec["gps_f32_max_err_m"] = round(float(err), 3)
            out["workouts"].append(rec)
            print(json.dumps({"idx": idx, "dur_min": round(dur / 60)}), file=sys.stderr, flush=True)
            first = False
        # export plot data for the device prototype (every pick, 1-s grid; the page decimates)
        exp = Path(a.work) / "proto"
        exp.mkdir(parents=True, exist_ok=True)
        for dur, idx, st in pick:
            t = np.asarray(ds.channel(idx, "elapsedtime"), float)
            ok = np.isfinite(t)
            t0 = t[ok][0]
            g = np.arange(0.0, math.floor(t[ok][-1] - t0) + 1.0)
            cols = [g]
            for name in ("heartrate", "power", "speed", "elevation", "elapseddistance"):
                x = ds.channel(idx, name)
                if x is None or len(x) != len(t):
                    cols.append(np.full(len(g), np.nan))
                    continue
                x = np.asarray(x, float)
                m = ok & np.isfinite(x)
                y = np.interp(g, t[m] - t0, x[m], left=np.nan, right=np.nan) if m.sum() > 1 else np.full(len(g), np.nan)
                cols.append(y)
            (exp / f"w{idx}_1s.f32").write_bytes(np.stack(cols).astype("<f4").tobytes())
            # the server's answers for the same workout (for the "draw only" comparison)
            for ch in charts:
                if ch["kind"] == "activity":
                    (exp / f"w{idx}_{ch['chart']}.json").write_bytes(get(ch["url"], workout=idx).content)
            (exp / f"w{idx}_meta.json").write_text(json.dumps({"n": len(g), "cols": ["t", "hr", "power", "speed", "elev", "dist"]}))
    out["loadavg_end"] = os.getloadavg()
    print("@@" + json.dumps(out), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--work", required=True)
    ap.add_argument("--out")
    ap.add_argument("--child", action="store_true")
    a = ap.parse_args(argv)
    if a.child:
        return _child(a)
    sys.path.insert(0, str(REPO))
    from backend.scripts.perf_sp394 import _copy_base, _env
    work = Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    home = _copy_base(Path(a.base).resolve(), work / "home")
    env = _env(home, {"WKO5COACH_NO_WARMUP": "1", "WKO5COACH_FIT_WORKERS": "2"})
    cmd = [sys.executable, __file__, "--child", "--work", str(work)]
    p = subprocess.run(cmd, env=env, cwd=str(REPO), stdout=subprocess.PIPE, text=True)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    if p.returncode or line is None:
        print(p.stdout[-3000:])
        return p.returncode or 1
    res = json.loads(line[2:])
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1), "utf-8")
    print(json.dumps({k: res[k] for k in ("view", "n_candidates", "loadavg_start", "loadavg_end")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
