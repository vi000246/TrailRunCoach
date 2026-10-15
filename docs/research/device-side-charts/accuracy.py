"""SP-325: how far the 5-s float32 plot data moves the numbers of the activity charts, against the
server's answers (1-s grid, float64). Reads the files perf_sp325.py exported (demo data only)."""
import json
import math
import sys
from pathlib import Path

import numpy as np

D = Path(sys.argv[1])
POINTS, NPW, STOP = 1500, 30, 1.6


def load(idx):
    meta = json.loads((D / f"w{idx}_meta.json").read_text())
    a = np.frombuffer((D / f"w{idx}_1s.f32").read_bytes(), "<f4").astype(float).reshape(6, meta["n"])
    return dict(zip(["t", "hr", "power", "speed", "elev", "dist"], a))


def rolling(x, n):
    ok = np.isfinite(x) & (x >= 0)
    cs = np.concatenate([[0], np.cumsum(np.where(ok, x, 0))])
    cn = np.concatenate([[0], np.cumsum(ok)])
    out = np.full(len(x), np.nan)
    s, c = cs[n:] - cs[:-n], cn[n:] - cn[:-n]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[n - 1:] = np.where(c >= n * 0.5, s / np.maximum(c, 1), np.nan)
    return out


def numbers(d, step, zh, zp):
    s = {k: v[::step] for k, v in d.items()}
    hr, p = s["hr"], s["power"]
    hv, pv = np.isfinite(hr) & (hr > 0), np.isfinite(p) & (p >= 0)
    r30 = rolling(p, max(1, round(NPW / step)))
    nv = np.isfinite(r30)
    np_ = 100 * (np.mean((r30[nv] / 100) ** 4)) ** 0.25 if nv.any() else None
    zt = lambda v, rows: [float(((v >= (r["from"] or 0)) & ((v < r["to"]) if r["to"] is not None else True) & np.isfinite(v) & (v > 0)).sum() * step) for r in rows]
    x, y = s["t"][hv], hr[hv]
    m = np.polyfit(x, y, 1)[0] * 3600
    mov = float((~(np.isfinite(s["speed"]) & (s["speed"] <= STOP))).sum() * step)
    return {"hr_avg": float(hr[hv].mean()), "p_avg": float(p[pv].mean()) if pv.any() else None, "np": np_,
            "hr_zone_s": zt(hr, zh), "p_zone_s": zt(p, zp) if zp else [], "slope_bpm_h": float(m),
            "hr_sd": float(y.std(ddof=1)), "moving_s": mov,
            "hr_max": float(np.nanmax(hr)) if hv.any() else None,
            "p_max30": float(np.nanmax(r30)) if nv.any() else None}


def default(z):
    return next((m for m in z["models"] if m["id"] == z["default"] and m.get("available")), None)


out = []
for f in sorted(D.glob("w*_meta.json")):
    idx = f.name.split("_")[0][1:]
    d = load(idx)
    hz = json.loads((D / f"w{idx}_hrzones.json").read_text())
    pz = json.loads((D / f"w{idx}_powerzones.json").read_text())
    hp = json.loads((D / f"w{idx}_hrpower.json").read_text())
    zh = default(hz)["rows"]
    zpm = default(pz)
    zp = zpm["rows"] if zpm else []
    a, b = numbers(d, 1, zh, zp), numbers(d, 5, zh, zp)
    srv_hz = [r["seconds"] for r in zh]
    tot = sum(srv_hz) or 1
    zone_err = max(abs(x - y) for x, y in zip(b["hr_zone_s"], a["hr_zone_s"]))
    pzone_err = max((abs(x - y) for x, y in zip(b["p_zone_s"], a["p_zone_s"])), default=0)
    out.append({"w": idx, "dur_min": round(len(d["t"]) / 60),
                "hr_avg_1s": round(a["hr_avg"], 2), "hr_avg_5s": round(b["hr_avg"], 2),
                "np_1s": a["np"] and round(a["np"], 1), "np_5s": b["np"] and round(b["np"], 1),
                "hr_max_1s": a["hr_max"], "hr_max_5s": b["hr_max"],
                "p_max30_1s": a["p_max30"] and round(a["p_max30"], 1), "p_max30_5s": b["p_max30"] and round(b["p_max30"], 1),
                "slope_1s": round(a["slope_bpm_h"], 3), "slope_5s": round(b["slope_bpm_h"], 3),
                "hr_zone_max_diff_s": zone_err, "hr_zone_max_diff_pct": round(zone_err / tot * 100, 2),
                "p_zone_max_diff_s": pzone_err,
                "srv_vs_1s_hr_zone_max_diff_s": max(abs(x - y) for x, y in zip(srv_hz, a["hr_zone_s"])),
                "moving_1s": a["moving_s"], "moving_5s": b["moving_s"],
                "plotted_step_s": hp.get("step")})
print(json.dumps(out, indent=1))
