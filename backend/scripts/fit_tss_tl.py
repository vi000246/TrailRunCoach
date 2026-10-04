"""
Fit COROS Training Load (TL) from the app's TSS — the five models of SP-37's
regression plan, compared by leave-one-out error, per TSS type. Offline: it
only reads the CSV written by backend/scripts/probe_coros_tl.py.

    python -m backend.scripts.fit_tss_tl <out>/activities.csv
    python -m backend.scripts.fit_tss_tl <out>/activities.csv --planned <out>/planned.csv --json fit.json
    python -m backend.scripts.fit_tss_tl activities.csv --tl detail.trainingLoad --lthr-max-diff 3

Models (x = the group's TSS, h = hours, IF = the group's intensity factor):
  A  TL = a·x                      (through the origin)
  B  TL = a·x + b
  C  TL = a·x^k                    (log-log least squares)
  D  TL = h·(c0 + c1·IF + c2·IF²)  (does TL grow with IF² like TSS?)
  E  TL = Σ w_i · minutes in COROS LTHR zone i  (TRIMP-like; non-negative weights)

Groups: each tss_source on the `tss` column (power / rtss / hrtss / ...), plus
"hrtss(all)" = the `hrtss` column on every row that has one (COROS TL is
probably HR based). Rows: TL present, ≥ --min-minutes, ≤ --max-hours (no
multi-day), HR on ≥ --min-hr-coverage of the time when known.

--planned checks the models (fitted on the activities) against the TL COROS
gives the planned workouts in planned.csv (the number SP-38 has to send):
x = the app's planned TSS, h = COROS estimated time, IF = √(TSS / (100·h)).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np

MODELS = ("A", "B", "C", "D", "E")
MIN_N = {"A": 3, "B": 4, "C": 4, "D": 5, "E": 8}
ZONES = ("z1_s", "z2_s", "z3_s", "z4_s", "z5_s", "z6_s")
MODEL_TEXT = {"A": "TL = a·TSS", "B": "TL = a·TSS + b", "C": "TL = a·TSS^k",
              "D": "TL = h·(c0 + c1·IF + c2·IF²)", "E": "TL = Σ w_i·zone-minutes"}


def num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pick_tl_column(rows: list[dict], prefer: Optional[str] = None) -> Optional[str]:
    """The TL column: `prefer` when given, else the list./detail. column with
    'trainingload' in its name that has the most non-zero values."""
    if prefer:
        return prefer
    if not rows:
        return None
    cands = [c for c in rows[0] if c.startswith(("list.", "detail.")) and "trainingload" in c.lower()]
    best, best_n = None, 0
    for c in cands:
        n = sum(1 for r in rows if (num(r.get(c)) or 0) > 0)
        if n > best_n:
            best, best_n = c, n
    return best


# ---------------------------------------------------------------------------
# samples and models
# ---------------------------------------------------------------------------

def sample(r: dict, tl_col: str, x_col: str, if_col: str) -> Optional[dict]:
    y = num(r.get(tl_col))
    x = num(r.get(x_col))
    if y is None or x is None:
        return None
    secs = num(r.get("moving_s")) or num(r.get("duration_s"))
    zones = [num(r.get(z)) for z in ZONES]
    return {"y": y, "x": x, "h": secs / 3600.0 if secs else None, "if": num(r.get(if_col)),
            "zmin": [z / 60.0 for z in zones] if all(z is not None for z in zones) else None}


def _nnls(A: np.ndarray, y: np.ndarray, iters: int = 20) -> np.ndarray:
    """Least squares with non-negative coefficients (drop the negative ones and refit)."""
    active = np.ones(A.shape[1], dtype=bool)
    w = np.zeros(A.shape[1])
    for _ in range(iters):
        if not active.any():
            break
        sol, *_ = np.linalg.lstsq(A[:, active], y, rcond=None)
        w[:] = 0.0
        w[active] = sol
        if (sol >= 0).all():
            break
        active[np.where(active)[0][sol < 0]] = False
    return w


def fit(model: str, s: list[dict]) -> Optional[dict]:
    """Fitted parameters, or None when the samples cannot carry the model."""
    if model == "A":
        x = np.array([d["x"] for d in s]); y = np.array([d["y"] for d in s])
        sxx = float((x * x).sum())
        return None if sxx <= 0 else {"a": float((x * y).sum()) / sxx}
    if model == "B":
        x = np.array([d["x"] for d in s]); y = np.array([d["y"] for d in s])
        if np.ptp(x) <= 0:
            return None
        a, b = np.polyfit(x, y, 1)
        return {"a": float(a), "b": float(b)}
    if model == "C":
        pts = [(d["x"], d["y"]) for d in s if d["x"] > 0 and d["y"] > 0]
        if len(pts) < MIN_N["C"]:
            return None
        lx = np.log([p[0] for p in pts]); ly = np.log([p[1] for p in pts])
        if np.ptp(lx) <= 0:
            return None
        k, la = np.polyfit(lx, ly, 1)
        return {"a": float(math.exp(la)), "k": float(k)}
    if model == "D":
        pts = [d for d in s if d["h"] and d["if"] is not None]
        if len(pts) < MIN_N["D"]:
            return None
        A = np.array([[d["h"], d["h"] * d["if"], d["h"] * d["if"] ** 2] for d in pts])
        y = np.array([d["y"] for d in pts])
        if np.linalg.matrix_rank(A) < 3:
            return None
        c, *_ = np.linalg.lstsq(A, y, rcond=None)
        return {"c0": float(c[0]), "c1": float(c[1]), "c2": float(c[2])}
    if model == "E":
        pts = [d for d in s if d["zmin"] is not None]
        if len(pts) < MIN_N["E"]:
            return None
        A = np.array([d["zmin"] for d in pts]); y = np.array([d["y"] for d in pts])
        used = A.sum(axis=0) > 0
        if used.sum() == 0:
            return None
        w = np.zeros(6)
        w[used] = _nnls(A[:, used], y)
        return {f"w{i + 1}": float(v) for i, v in enumerate(w)}
    raise ValueError(model)


def predict(model: str, p: dict, d: dict) -> Optional[float]:
    if model == "A":
        return p["a"] * d["x"]
    if model == "B":
        return p["a"] * d["x"] + p["b"]
    if model == "C":
        return p["a"] * d["x"] ** p["k"] if d["x"] > 0 else None
    if model == "D":
        if not d.get("h") or d.get("if") is None:
            return None
        return d["h"] * (p["c0"] + p["c1"] * d["if"] + p["c2"] * d["if"] ** 2)
    if model == "E":
        if d.get("zmin") is None:
            return None
        return sum(p[f"w{i + 1}"] * m for i, m in enumerate(d["zmin"]))
    raise ValueError(model)


def errors(pairs: list[tuple[float, float]]) -> dict:
    """{n, mae, mape (y > 0 only), bias} of (prediction, truth) pairs."""
    if not pairs:
        return {"n": 0, "mae": None, "mape": None, "bias": None}
    e = [p - y for p, y in pairs]
    pct = [abs(p - y) / y for p, y in pairs if y > 0]
    return {"n": len(pairs), "mae": sum(abs(v) for v in e) / len(e),
            "mape": (sum(pct) / len(pct)) if pct else None, "bias": sum(e) / len(e)}


def loo(model: str, s: list[dict]) -> dict:
    """Leave-one-out: fit on all but one, predict that one."""
    pairs = []
    for i in range(len(s)):
        p = fit(model, s[:i] + s[i + 1:])
        if p is None:
            continue
        v = predict(model, p, s[i])
        if v is not None and math.isfinite(v):
            pairs.append((v, s[i]["y"]))
    return errors(pairs)


def evaluate(s: list[dict]) -> dict:
    """Per model: the full-sample parameters and the LOO error."""
    out = {}
    for m in MODELS:
        usable = [d for d in s if predict_inputs_ok(m, d)]
        if len(usable) < MIN_N[m]:
            out[m] = {"n": len(usable), "params": None, "loo": None}
            continue
        out[m] = {"n": len(usable), "params": fit(m, usable), "loo": loo(m, usable)}
    return out


def predict_inputs_ok(model: str, d: dict) -> bool:
    if model == "C":
        return d["x"] > 0 and d["y"] > 0
    if model == "D":
        return bool(d.get("h")) and d.get("if") is not None
    if model == "E":
        return d.get("zmin") is not None
    return True


def best_model(res: dict) -> Optional[str]:
    """Lowest LOO MAE; a model only competes on ≥ 80 % of the group's largest n
    (so a model fitted on a small subset does not win by being easy)."""
    ok = {m: r for m, r in res.items() if r.get("loo") and r["loo"]["mae"] is not None}
    if not ok:
        return None
    nmax = max(r["loo"]["n"] for r in ok.values())
    ok = {m: r for m, r in ok.items() if r["loo"]["n"] >= 0.8 * nmax}
    return min(ok, key=lambda m: ok[m]["loo"]["mae"])


# ---------------------------------------------------------------------------
# grouping / filters
# ---------------------------------------------------------------------------

def keep_row(r: dict, min_minutes: float, max_hours: float, min_hr_cov: float,
             lthr_max_diff: Optional[float]) -> bool:
    secs = num(r.get("moving_s")) or num(r.get("duration_s"))
    if secs is None or secs < min_minutes * 60 or secs > max_hours * 3600:
        return False
    cov = num(r.get("hr_coverage"))
    if cov is not None and cov < min_hr_cov:
        return False
    if lthr_max_diff is not None:
        a, c = num(r.get("app_lthr")), num(r.get("coros_lthr"))
        if a is not None and c is not None and abs(a - c) > lthr_max_diff:
            return False
    return True


def groups(rows: list[dict], tl_col: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        src = r.get("tss_source")
        if src:
            d = sample(r, tl_col, "tss", "if")
            if d is not None:
                out.setdefault(src, []).append(d)
        d = sample(r, tl_col, "hrtss", "hrif")
        if d is not None:
            out.setdefault("hrtss(all)", []).append(d)
    return out


def planned_samples(rows: list[dict]) -> list[dict]:
    """planned.csv rows with both a COROS TL and an app planned TSS."""
    out = []
    for r in rows:
        y = num(r.get("tl_detail")) or num(r.get("tl_schedule"))
        x = num(r.get("app_planned_tss"))
        secs = num(r.get("estimated_time_s")) or num(r.get("duration_s"))
        if not secs and num(r.get("app_minutes")):
            secs = num(r.get("app_minutes")) * 60
        if y is None or y <= 0 or x is None or x <= 0:
            continue
        h = secs / 3600.0 if secs else None
        out.append({"y": y, "x": x, "h": h, "if": math.sqrt(x / (100 * h)) if h else None, "zmin": None})
    return out


def _f(v, nd=1, pct=False) -> str:
    if v is None:
        return "–"
    return f"{v * 100:.{nd}f}%" if pct else f"{v:.{nd}f}"


def _params(p: Optional[dict]) -> str:
    return "–" if not p else " ".join(f"{k}={v:.4g}" for k, v in p.items())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fit COROS TL from the app's TSS (offline).")
    ap.add_argument("csv", help="activities.csv from probe_coros_tl")
    ap.add_argument("--tl", help="TL column (default: the list./detail. *trainingLoad* column with most values)")
    ap.add_argument("--planned", help="planned.csv from probe_coros_tl: check the models on COROS planned TL")
    ap.add_argument("--min-minutes", type=float, default=10.0)
    ap.add_argument("--max-hours", type=float, default=20.0, help="drop longer (multi-day) activities")
    ap.add_argument("--min-hr-coverage", type=float, default=0.9)
    ap.add_argument("--lthr-max-diff", type=float, default=None,
                    help="only rows where app and COROS LTHR differ by ≤ this many bpm")
    ap.add_argument("--json", help="also write the results here")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    rows = read_csv(Path(a.csv))
    tl_col = pick_tl_column(rows, a.tl)
    if not tl_col:
        cols = [c for c in (rows[0] if rows else {}) if c.startswith(("list.", "detail.", "fit."))]
        print("no COROS TL column found; pick one with --tl from:", ", ".join(cols) or "(none)")
        return 2
    kept = [r for r in rows if keep_row(r, a.min_minutes, a.max_hours, a.min_hr_coverage, a.lthr_max_diff)]
    print(f"TL column {tl_col}; {len(kept)}/{len(rows)} rows after filters")
    result: dict = {"tl_column": tl_col, "rows": len(rows), "kept": len(kept), "groups": {}}
    fitted: dict[str, dict] = {}
    for g, s in sorted(groups(kept, tl_col).items(), key=lambda kv: -len(kv[1])):
        res = evaluate(s)
        best = best_model(res)
        fitted[g] = res
        result["groups"][g] = {"n": len(s), "best": best, "models": res}
        print(f"\n[{g}] n={len(s)}  best: {best or '–'}" + (f"  ({MODEL_TEXT[best]})" if best else ""))
        print(f"  {'model':6s} {'n':>4s} {'LOO MAE':>8s} {'MAPE':>7s} {'bias':>7s}  params")
        for m in MODELS:
            r = res[m]
            lo = r.get("loo") or {}
            print(f"  {m:6s} {r['n']:4d} {_f(lo.get('mae')):>8s} {_f(lo.get('mape'), pct=True):>7s} "
                  f"{_f(lo.get('bias')):>7s}  {_params(r.get('params'))}")
    if a.planned:
        ps = planned_samples(read_csv(Path(a.planned)))
        result["planned"] = {"n": len(ps), "by_group": {}}
        print(f"\nplanned workouts with a COROS TL and an app TSS: {len(ps)}")
        if ps:
            ratio = sorted(d["y"] / d["x"] for d in ps)
            print(f"  median planned TL / planned TSS = {ratio[len(ratio) // 2]:.3f}")
        for g, res in fitted.items():
            line = {}
            for m in ("A", "B", "C", "D"):
                p = res[m].get("params")
                if not p or not ps:
                    continue
                pairs = [(v, d["y"]) for d in ps if (v := predict(m, p, d)) is not None]
                line[m] = errors(pairs)
            if line:
                result["planned"]["by_group"][g] = line
                print(f"  [{g}] " + "  ".join(f"{m}: MAE {_f(e['mae'])} ({_f(e['mape'], pct=True)})"
                                               for m, e in line.items()))
    if a.json:
        Path(a.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
