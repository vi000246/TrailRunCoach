"""SP-325: plot-data size per period (90 d / 1 y) on the demo athlete, for several formats.
Every activity (any sport) with elapsed time; gzip of each activity's binary, summed."""
import gzip
import json
import math
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def child():
    import numpy as np
    from backend.api import wko5views as WV
    ds = WV._dataset()
    today = math.floor(ds.today)
    rows = []
    for w in ds.workouts:
        t = ds.channel(w.idx, "elapsedtime")
        if t is None or len(t) < 2:
            continue
        t = np.asarray(t, float)
        ok = np.isfinite(t)
        if ok.sum() < 2:
            continue
        t0 = t[ok][0]
        g = np.arange(0.0, math.floor(t[ok][-1] - t0) + 1.0)
        ch = {}
        for name in ("heartrate", "power", "speed", "elevation", "elapseddistance", "latitude", "longitude", "cadence"):
            x = ds.channel(w.idx, name)
            if x is None or len(x) != len(t):
                continue
            x = np.asarray(x, float)
            m = ok & np.isfinite(x)
            if m.sum() < 2:
                continue
            y = np.interp(g, t[m] - t0, x[m], left=np.nan, right=np.nan)
            j = np.clip(np.searchsorted(t[m] - t0, g), 1, m.sum() - 1)
            tt = t[m] - t0
            y[(tt[j] - tt[j - 1]) > 30] = np.nan        # keep the gaps (activity_charts._on_grid)
            ch[name] = y
        n = len(g)
        fmt = {}
        for label, step, names, dt in (
                ("5s_t+4", 5, ["heartrate", "power", "speed", "elevation"], "<f4"),
                ("5s_t+5+gps", 5, ["heartrate", "power", "speed", "elevation", "elapseddistance", "latitude", "longitude"], "<f4"),
                ("adaptive3000_t+5+gps", max(1, math.ceil(n / 3000)), ["heartrate", "power", "speed", "elevation", "elapseddistance", "latitude", "longitude"], "<f4"),
                ("adaptive3000_t+5", max(1, math.ceil(n / 3000)), ["heartrate", "power", "speed", "elevation", "elapseddistance"], "<f4"),
                ("1s_t+5", 1, ["heartrate", "power", "speed", "elevation", "elapseddistance"], "<f4")):
            cols = [g[::step]] + [ch[k][::step] for k in names if k in ch]
            b = np.stack(cols).astype(dt).tobytes()
            fmt[label] = (len(b), len(gzip.compress(b, 6)))
        rows.append({"age": today - math.floor(w.day), "dur": n, "sport": w.sport, "fmt": fmt})
    print("@@" + json.dumps(rows))


if __name__ == "__main__":
    if sys.argv[1] == "--child":
        child()
        sys.exit(0)
    sys.path.insert(0, str(REPO))
    from backend.scripts.perf_sp394 import _env
    home = Path(sys.argv[1])
    env = _env(home, {"WKO5COACH_NO_WARMUP": "1"})
    p = subprocess.run([sys.executable, __file__, "--child"], env=env, cwd=str(REPO), stdout=subprocess.PIPE, text=True)
    rows = json.loads(next(x for x in p.stdout.splitlines() if x.startswith("@@"))[2:])
    for lim in (90, 365):
        sel = [r for r in rows if r["age"] < lim]
        print(f"== last {lim} d: {len(sel)} activities, {sum(r['dur'] for r in sel)/3600:.0f} h, median {sorted(r['dur'] for r in sel)[len(sel)//2]/60:.0f} min")
        for k in sel[0]["fmt"]:
            raw = sum(r["fmt"][k][0] for r in sel) / 1e6
            gz = sum(r["fmt"][k][1] for r in sel) / 1e6
            print(f"   {k:24} raw {raw:6.2f} MB  gzip {gz:6.2f} MB   per activity raw {raw*1e3/len(sel):5.0f} KB gz {gz*1e3/len(sel):5.0f} KB")
    Path(sys.argv[2]).write_text(json.dumps(rows))
