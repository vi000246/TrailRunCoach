"""
Validate the app's drift rules (backend/engine/workout_review.py drift_of v2,
backend/engine/drift_agg.py; docs/research/drift-algorithm.md) on other
athletes: GoldenCheetah OpenData runs (CC0; sampled by gc_fetch.py). Report:
docs/research/validation-goldencheetah.md.

The app's functions are called as they are; nothing is reimplemented. The only
preprocessing is turning GC's CSV (secs, km, power, hr, cad, alt) into the
app's channels: speed (km/h) = a centred 5-sample difference of km, power /
HR 0 -> missing. To see every gate's number on every run, drift_of is run a
second time with its gate thresholds set to infinity ("relaxed"; patched for
that call only, restored after) — the metrics are then judged against the
app's thresholds here.

    python -m backend.scripts.validation.gc_drift run     # -> <root>/results/{runs,trunc,grade}.jsonl
    python -m backend.scripts.validation.gc_drift stats   # -> <root>/results/stats.json + markdown on stdout

One process, one run at a time (no workers). Never touches the app DB or port 8000.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import random
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from backend.engine import drift_agg as DA
from backend.engine import workout_review as WR
from backend.engine.algorithms.minetti import grade_factor

DEFAULT_ROOT = Path.home() / "Datasets" / "goldencheetah"
TRUNC_L = (30, 35, 40, 50)           # minutes of measured time after the start (item 3)
TRUNC_MIN_S = 52 * 60                # runs whose relaxed window is >= this get truncated
GRADE_BINS = (-0.15, -0.10, -0.06, -0.03, -0.01, 0.01, 0.03, 0.06, 0.10, 0.15)
SEG_S = 30                           # grade segments (item 4)

RELAX = {"DRIFT_MAX_VI": math.inf, "WALK_MAX_S": math.inf, "DRIFT_HALVES_DIFF": math.inf,
         "DRIFT_FAST_FINISH": math.inf}


@contextlib.contextmanager
def relaxed():
    old = {k: getattr(WR, k) for k in RELAX}
    try:
        for k, v in RELAX.items():
            setattr(WR, k, v)
        yield
    finally:
        for k, v in old.items():
            setattr(WR, k, v)


REASONS = (("nohr", "沒有心率"), ("hilly", "有坡"), ("stops", "中途停了"), ("walk", "明顯放慢"),
           ("vi", "功率起伏大"), ("cp", "% CP"), ("halves", "比前半"), ("fastfinish", "最後 10%"),
           ("data", "有效資料不夠"), ("short", "暖身後只有"))


def reason_code(dr: dict) -> str:
    if dr.get("ok"):
        return "pass_test"
    if dr.get("ref_ok"):
        return "pass_ref"
    r = dr.get("reason") or ""
    for code, key in REASONS:
        if key in r:
            return code
    return "other"


# ---------------------------------------------------------------------------
# GC CSV -> app channels
# ---------------------------------------------------------------------------

def parse_csv(raw: bytes) -> dict | None:
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) < 100:
        return None
    head = [h.strip() for h in lines[0].split(",")]
    idx = {h: i for i, h in enumerate(head)}
    if "secs" not in idx:
        return None
    rows = [ln.split(",") for ln in lines[1:]]
    cols = {}
    for k in ("secs", "km", "power", "hr", "alt"):
        i = idx.get(k)
        if i is None:
            cols[k] = np.full(len(rows), np.nan)
            continue
        v = np.empty(len(rows))
        for j, r in enumerate(rows):
            try:
                v[j] = float(r[i]) if i < len(r) and r[i] != "" else np.nan
            except ValueError:
                v[j] = np.nan
        cols[k] = v
    t = cols["secs"]
    ok = np.isfinite(t)
    if ok.sum() < 100:
        return None
    for k in cols:
        cols[k] = cols[k][ok]
    t = cols["secs"]
    o = np.argsort(t, kind="stable")
    for k in cols:
        cols[k] = cols[k][o]
    return cols


def speed_of(t: np.ndarray, km: np.ndarray) -> np.ndarray:
    """km/h, centred 5-sample difference of the distance (GC's CSV has no speed column)."""
    n = len(t)
    s = np.full(n, np.nan)
    if n < 6 or not np.isfinite(km).any():
        return s
    k = WR._ffill(km.astype(float))
    a, b = np.arange(n) - 2, np.arange(n) + 2
    a, b = np.clip(a, 0, n - 1), np.clip(b, 0, n - 1)
    dt_ = t[b] - t[a]
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.where((dt_ > 0) & (dt_ <= 10), (k[b] - k[a]) * 3600.0 / dt_, np.nan)
    return np.clip(s, 0, 40)


def channels(cols: dict) -> dict:
    t = cols["secs"].astype(float)
    hr = np.where(cols["hr"] > 30, cols["hr"], np.nan)
    p = np.where(cols["power"] > 0, cols["power"], np.nan)
    sp = speed_of(t, cols["km"])
    alt = cols["alt"] if np.isfinite(cols["alt"]).sum() > 0.5 * len(t) and np.nanstd(cols["alt"]) > 0 else None
    return {"t": t, "hr": hr, "speed": sp, "power": p, "dist": cols["km"], "elev": alt}


def summarize(ch: dict) -> dict:
    t, s, h, p = ch["t"], ch["speed"], ch["hr"], ch["power"]
    mov = np.isfinite(s) & (s > WR.STOP_KMH)
    return {"elapsed": float(t[-1] - t[0]), "med_speed": float(np.nanmedian(s[mov])) if mov.any() else None,
            "hr_cover": float(np.isfinite(h).mean()),
            "p_cover": float(np.isfinite(p[mov]).mean()) if mov.any() else 0.0,
            "med_power": float(np.nanmedian(p[mov])) if mov.any() and np.isfinite(p[mov]).any() else None,
            "avg_hr": float(np.nanmean(h)) if np.isfinite(h).any() else None}


KEEP = ("drift", "pw_drift", "drift_se", "pw_drift_se", "measured_s", "vi", "cv30", "cv30_w1", "walk_max_s",
        "halves_diff", "halves_basis", "finish", "hr1", "hr2", "pw_hr1", "pw_hr2", "p1", "p2", "v1", "v2",
        "warmup_s", "end_s", "idle_s", "tier", "ok", "ref_ok", "pw_ok", "pw_ref_ok", "noisy")


def slim(dr: dict) -> dict:
    out = {k: WR._nan_free(dr.get(k)) for k in KEEP}
    r = dr.get("ramps")
    if r:
        out["ramps"] = WR._nan_free({k: r.get(k) for k in ("n1", "n2", "climb1", "climb2", "excluded_s",
                                                            "drift", "pw_drift")})
    out["code"] = reason_code(dr)
    out["reason"] = dr.get("reason")
    return out


def call_drift(ch: dict, cpm, power, n_end=None) -> dict:
    sl = slice(0, n_end)
    return WR.drift_of(ch["t"][sl], ch["hr"][sl], ch["speed"][sl], power[sl] if power is not None else None,
                       None, cpm, dist=ch["dist"][sl], elev=ch["elev"][sl] if ch["elev"] is not None else None)


# ---------------------------------------------------------------------------
# item 4: power / HR by grade
# ---------------------------------------------------------------------------

def grade_rows(ch: dict) -> list | None:
    """Per grade bin, the run's medians over 30-s segments (moving, after 10 min,
    all channels valid): P/v (W per km/h) and P_f/HR (power through the app's
    lowpass, τ = 60 s, over HR) — both relative to the run's flat bin."""
    t, s, h, p, d, e = ch["t"], ch["speed"], ch["hr"], ch["power"], ch["dist"], ch["elev"]
    if e is None or not np.isfinite(p).any():
        return None
    grid = np.arange(t[0], t[-1] + 1.0)
    G = {k: WR._at_grid(t, x, grid) for k, x in (("s", s), ("h", h), ("p", p), ("d", d), ("e", e))}
    es = np.convolve(WR._ffill(G["e"]), np.ones(15) / 15, "same")
    stopped = ~(np.isfinite(G["s"]) & (G["s"] > WR.STOP_KMH))
    pf = WR.lowpass(np.where(stopped, 0.0, WR._ffill(G["p"])))
    rel = grid - grid[0]
    rows = defaultdict(list)
    for a in range(int(WR.WARMUP_S), len(grid) - SEG_S, SEG_S):
        b = a + SEG_S
        seg = slice(a, b)
        if stopped[seg].any() or not (np.isfinite(G["h"][seg]).all() and np.isfinite(G["p"][seg]).all()):
            continue
        dd = (G["d"][b - 1] - G["d"][a]) * 1000.0
        if not np.isfinite(dd) or dd < 50:
            continue
        g = (es[b - 1] - es[a]) / dd
        if not np.isfinite(g) or abs(g) > 0.30:
            continue
        k = int(np.searchsorted(GRADE_BINS, g))
        v = float(np.mean(G["s"][seg]))
        pm = float(np.mean(G["p"][seg]))
        hm = float(np.mean(G["h"][seg]))
        rows[k].append((g, pm / v, float(np.mean(pf[seg])) / hm, pm / hm))
    flat = rows.get(int(np.searchsorted(GRADE_BINS, 0.0)))
    if not flat or len(flat) < 6:
        return None
    f_pv = float(np.median([r[1] for r in flat]))
    f_ph = float(np.median([r[2] for r in flat]))
    f_ph0 = float(np.median([r[3] for r in flat]))
    out = []
    for k, rs in sorted(rows.items()):
        if len(rs) < 3:
            continue
        out.append({"bin": k, "n": len(rs), "g": float(np.median([r[0] for r in rs])),
                    "pv": float(np.median([r[1] for r in rs])) / f_pv,
                    "pfh": float(np.median([r[2] for r in rs])) / f_ph,
                    "ph": float(np.median([r[3] for r in rs])) / f_ph0})
    return out


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def cmd_run(root: Path, limit: int | None) -> None:
    scan = {}
    for line in (root / "scan.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        scan[r["name"]] = r
    zips = sorted((root / "zips").glob("*.zip"))
    res = root / "results"
    res.mkdir(exist_ok=True)
    fr = open(res / "runs.jsonl", "w", encoding="utf-8")
    ft = open(res / "trunc.jsonl", "w", encoding="utf-8")
    fg = open(res / "grade.jsonl", "w", encoding="utf-8")
    t0 = time.time()
    n = 0
    for zi, zp in enumerate(zips):
        meta = scan.get(zp.name)
        if not meta:
            continue
        ath = meta["athlete"]
        with zipfile.ZipFile(zp) as z:
            names = set(z.namelist())
            for run in meta["runs"]:
                if run["csv"] not in names:
                    continue
                cols = parse_csv(z.read(run["csv"]))
                if cols is None:
                    continue
                ch = channels(cols)
                sm = summarize(ch)
                row = {"ath": ath, "csv": run["csv"], "date": run["date"], "sport": run["sport"],
                       "km": run["km"], "gain": run["gain"], **sm}
                cpm = (run["gain"] / run["km"]) if run.get("km") and run["km"] > 0.5 and run.get("gain") is not None \
                    else None
                row["cpm"] = cpm
                # a run, not a ride mislabelled: moving speed 5–22 km/h, power (if any) < 500 W, HR mostly there
                runlike = sm["med_speed"] is not None and 5.0 <= sm["med_speed"] <= 22.0 and \
                    (sm["med_power"] is None or sm["med_power"] < 500) and sm["hr_cover"] >= 0.8
                row["runlike"] = runlike
                if not runlike:
                    fr.write(json.dumps(row) + "\n")
                    continue
                power = ch["power"] if sm["p_cover"] >= 0.5 else None
                row["has_power"] = power is not None
                row["has_alt"] = ch["elev"] is not None
                try:
                    row["app"] = slim(call_drift(ch, cpm, power))
                    with relaxed():
                        rx = call_drift(ch, cpm, power)
                    row["rlx"] = slim(rx)
                except Exception as e:      # noqa: BLE001 — record and go on
                    row["err"] = f"{type(e).__name__}: {e}"[:200]
                    fr.write(json.dumps(row) + "\n")
                    continue
                fr.write(json.dumps(row) + "\n")
                n += 1
                # item 3: truncate long passing runs to L minutes of window
                ms = rx.get("measured_s") or 0
                if row["app"]["code"] == "pass_test" and ms >= TRUNC_MIN_S:
                    rel = ch["t"] - ch["t"][0]
                    tr = {"ath": ath, "csv": run["csv"], "full": {k: row["app"][k] for k in
                                                                   ("drift", "pw_drift", "drift_se", "pw_drift_se",
                                                                    "measured_s", "p1", "v1", "hr1")}}
                    for L in TRUNC_L:
                        cut = float(rx["warmup_s"]) + (L + 1.5) * 60.0
                        ne = int(np.searchsorted(rel, cut))
                        dr = call_drift(ch, cpm, power, ne)
                        tr[str(L)] = {k: WR._nan_free(dr.get(k)) for k in
                                      ("drift", "pw_drift", "drift_se", "pw_drift_se", "measured_s", "tier")}
                        tr[str(L)]["code"] = reason_code(dr)
                    ft.write(json.dumps(tr) + "\n")
                # item 4
                if power is not None and ch["elev"] is not None:
                    g = grade_rows({**ch, "power": power})
                    if g:
                        fg.write(json.dumps({"ath": ath, "csv": run["csv"], "cpm": cpm, "bins": g}) + "\n")
                if limit and n >= limit:
                    break
        if zi % 10 == 0:
            print(f"{zi + 1}/{len(zips)} athletes, {n} runs, {time.time() - t0:.0f} s", flush=True)
        if limit and n >= limit:
            break
    for f in (fr, ft, fg):
        f.close()
    print(f"done: {n} runs in {time.time() - t0:.0f} s")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def _load(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def wpct(by_ath: dict[str, list[float]], q, rng=None) -> float:
    """Percentile with every athlete weighted equally (each run 1 / n_athlete_runs)."""
    vals, w = [], []
    keys = list(by_ath)
    if rng is not None:
        keys = [keys[i] for i in rng.integers(0, len(keys), len(keys))]
    for a in keys:
        xs = by_ath[a]
        if not xs:
            continue
        vals.extend(xs)
        w.extend([1.0 / len(xs)] * len(xs))
    if not vals:
        return float("nan")
    o = np.argsort(vals)
    v, ww = np.asarray(vals)[o], np.asarray(w)[o]
    c = np.cumsum(ww) / ww.sum()
    return float(v[min(len(v) - 1, int(np.searchsorted(c, q / 100.0)))])


def wpct_ci(by_ath, q, B=400, seed=1):
    rng = np.random.default_rng(seed)
    est = wpct(by_ath, q)
    bs = [wpct(by_ath, q, rng) for _ in range(B)]
    return est, float(np.nanpercentile(bs, 2.5)), float(np.nanpercentile(bs, 97.5))


def boot_ath(by_ath: dict, fn, B=400, seed=2):
    """fn(list of athlete keys) -> float; athlete-clustered bootstrap 95 % CI."""
    rng = np.random.default_rng(seed)
    keys = list(by_ath)
    est = fn(keys)
    bs = []
    for _ in range(B):
        ks = [keys[i] for i in rng.integers(0, len(keys), len(keys))]
        bs.append(fn(ks))
    bs = [b for b in bs if np.isfinite(b)]
    return est, (float(np.percentile(bs, 2.5)) if bs else float("nan")), \
        (float(np.percentile(bs, 97.5)) if bs else float("nan"))


def f3(x, d=3):
    return "–" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def cmd_stats(root: Path) -> None:
    res = root / "results"
    rows = _load(res / "runs.jsonl")
    S: dict = {}
    out_md: list[str] = []
    P = out_md.append

    run_rows = [r for r in rows if r.get("runlike") and "app" in r]
    athletes = sorted({r["ath"] for r in run_rows})
    S["inventory"] = {"csv_rows": len(rows), "runlike": len(run_rows),
                      "not_runlike": sum(1 for r in rows if not r.get("runlike")),
                      "errors": sum(1 for r in rows if "err" in r), "athletes": len(athletes),
                      "with_power": sum(1 for r in run_rows if r["has_power"]),
                      "ath_with_power": len({r["ath"] for r in run_rows if r["has_power"]}),
                      "with_alt": sum(1 for r in run_rows if r["has_alt"]),
                      "elapsed_ge_50": sum(1 for r in run_rows if r["elapsed"] >= 50 * 60)}
    P("## inventory\n" + json.dumps(S["inventory"], indent=1))

    # ---- item 1: verdicts ----
    codes = defaultdict(int)
    for r in run_rows:
        codes[r["app"]["code"]] += 1
    S["codes_all"] = dict(codes)
    P("## app verdicts (all run-like)\n" + json.dumps(S["codes_all"], indent=1))
    # per athlete pass rate
    pa = defaultdict(list)
    for r in run_rows:
        pa[r["ath"]].append(r["app"]["code"] in ("pass_test", "pass_ref"))
    rates = [np.mean(v) for v in pa.values() if len(v) >= 5]
    S["ath_pass_rate"] = {"n_ath": len(rates), "p25": float(np.percentile(rates, 25)),
                          "p50": float(np.percentile(rates, 50)), "p75": float(np.percentile(rates, 75)),
                          "zero": float(np.mean(np.asarray(rates) == 0))}
    P("## per-athlete pass rate (>= 5 runs)\n" + json.dumps(S["ath_pass_rate"], indent=1))

    # candidate pool: relaxed run produced a drift (= flat, stops ok, >= 30 min, data ok)
    cand = [r for r in run_rows if r["rlx"]["drift"] is not None]
    S["cand"] = {"runs": len(cand), "athletes": len({r["ath"] for r in cand}),
                 "power_runs": sum(1 for r in cand if r["has_power"] and r["rlx"]["vi"] is not None)}
    P("## candidates (relaxed drift computed)\n" + json.dumps(S["cand"], indent=1))

    def g_walk(x):
        return (x["walk_max_s"] or 0) < WR.WALK_MAX_S if "walk_max_s" in x else True

    def g_vi(x):
        return x["vi"] is None or x["vi"] <= WR.DRIFT_MAX_VI

    def g_half(x):
        return x["halves_diff"] is None or abs(x["halves_diff"]) <= WR.DRIFT_HALVES_DIFF

    def g_ff(x):
        return x["finish"] is None or x["finish"] <= WR.DRIFT_FAST_FINISH

    gates = {"walk": g_walk, "vi": g_vi, "halves": g_half, "fastfinish": g_ff}

    def g_cv(x):
        return x["cv30"] is None or x["cv30"] <= 0.15

    tab = []
    for name, fn, pool in (("VI ≤ 1.04", g_vi, [r for r in cand if r["rlx"]["vi"] is not None]),
                           ("walk < 180 s", g_walk, cand),
                           ("|halves| ≤ 5 % (power basis)", g_half,
                            [r for r in cand if r["rlx"]["halves_basis"] == "power"]),
                           ("|halves| ≤ 5 % (pace basis)", g_half,
                            [r for r in cand if r["rlx"]["halves_basis"] == "pace"]),
                           ("fast finish ≤ 5 %", g_ff, cand),
                           ("old CV30 ≤ 15 %", g_cv, [r for r in cand if r["rlx"]["cv30"] is not None]),
                           ("all v2 gates", lambda x: all(f(x) for f in gates.values()), cand)):
        by = defaultdict(list)
        for r in pool:
            by[r["ath"]].append(1.0 if fn(r["rlx"]) else 0.0)
        pooled = float(np.mean([v for vs in by.values() for v in vs])) if by else float("nan")
        e, lo, hi = boot_ath(by, lambda ks: float(np.mean([np.mean(by[k]) for k in ks])))
        tab.append({"gate": name, "runs": len(pool), "athletes": len(by), "pooled": pooled,
                    "ath_mean": e, "ci": [lo, hi]})
    S["gate_pass"] = tab
    P("## gate pass rates (candidates)\n| gate | runs | athletes | pooled | athlete-mean (95% CI) |\n|---|---|---|---|---|")
    for t in tab:
        P(f"| {t['gate']} | {t['runs']} | {t['athletes']} | {t['pooled'] * 100:.0f}% | "
          f"{t['ath_mean'] * 100:.0f}% ({t['ci'][0] * 100:.0f}–{t['ci'][1] * 100:.0f}) |")

    # leave-one-gate-out distributions ("steady-looking" for that gate = passes the others)
    def others(r, skip):
        return all(f(r["rlx"]) for k, f in gates.items() if k != skip)

    dist = {}
    specs = (("vi", "vi", lambda r: r["rlx"]["vi"], "vi"),
             ("cv30_w1", "cv30_w1", lambda r: r["rlx"]["cv30_w1"], "vi"),
             ("cv30_old", "cv30", lambda r: r["rlx"]["cv30"], "vi"),
             ("walk_max_s", "walk_max_s", lambda r: r["rlx"]["walk_max_s"], "walk"),
             ("halves_power", "halves_diff", lambda r: abs(r["rlx"]["halves_diff"])
              if r["rlx"]["halves_basis"] == "power" and r["rlx"]["halves_diff"] is not None else None, "halves"),
             ("halves_pace", "halves_diff", lambda r: abs(r["rlx"]["halves_diff"])
              if r["rlx"]["halves_basis"] == "pace" and r["rlx"]["halves_diff"] is not None else None, "halves"))
    P("## steady-looking (leave-one-gate-out) distributions, athlete-weighted\n"
      "| metric | runs | athletes | p50 | p75 | p90 (95% CI) | p95 (95% CI) | athlete p90: p25–p50–p75 (n ath ≥ 5 runs) |"
      "\n|---|---|---|---|---|---|---|---|")
    for name, _, get, skip in specs:
        by = defaultdict(list)
        for r in cand:
            v = get(r)
            if v is None or not others(r, skip):
                continue
            by[r["ath"]].append(float(v))
        nr = sum(len(v) for v in by.values())
        if nr < 10:
            continue
        p50, p75 = wpct(by, 50), wpct(by, 75)
        p90 = wpct_ci(by, 90)
        p95 = wpct_ci(by, 95)
        ath90 = [float(np.percentile(v, 90)) for v in by.values() if len(v) >= 5]
        a = np.percentile(ath90, [25, 50, 75]) if ath90 else [np.nan] * 3
        dist[name] = {"runs": nr, "athletes": len(by), "p50": p50, "p75": p75, "p90": p90, "p95": p95,
                      "ath_p90_q": [float(x) for x in a], "n_ath5": len(ath90)}
        P(f"| {name} | {nr} | {len(by)} | {f3(p50)} | {f3(p75)} | {f3(p90[0])} ({f3(p90[1])}–{f3(p90[2])}) | "
          f"{f3(p95[0])} ({f3(p95[1])}–{f3(p95[2])}) | {f3(a[0])}–{f3(a[1])}–{f3(a[2])} ({len(ath90)}) |")
    S["steady_dist"] = dist
    # all-candidates distributions too (not conditioned)
    alld = {}
    for name, _, get, _ in specs:
        by = defaultdict(list)
        for r in cand:
            v = get(r)
            if v is not None:
                by[r["ath"]].append(float(v))
        if sum(len(v) for v in by.values()) >= 10:
            alld[name] = {q: wpct(by, q) for q in (10, 25, 50, 75, 90)}
    S["cand_dist"] = alld
    P("## all candidates (unconditioned) athlete-weighted quantiles\n" + json.dumps(alld, indent=1))
    # VI vs CV30_w1 relation (VI ≈ 1 + 1.5 CV²)
    xs = [(r["rlx"]["cv30_w1"], r["rlx"]["vi"]) for r in cand if r["rlx"]["vi"] is not None
          and r["rlx"]["cv30_w1"] is not None]
    if xs:
        cv, vi = np.array(xs).T
        pred = 1 + 1.5 * cv ** 2
        S["vi_cv_check"] = {"n": len(xs), "median_abs_err": float(np.median(np.abs(vi - pred))),
                            "corr": float(np.corrcoef(vi, pred)[0, 1])}
        # VI equivalent of CV30 = 15 % on the window
        P("## VI vs 1+1.5CV²\n" + json.dumps(S["vi_cv_check"]))
    # agreement old CV30 rule vs new VI rule
    both = [r for r in cand if r["rlx"]["vi"] is not None and r["rlx"]["cv30"] is not None]
    if both:
        a = np.array([g_vi(r["rlx"]) for r in both])
        b = np.array([g_cv(r["rlx"]) for r in both])
        S["vi_vs_cv"] = {"n": len(both), "both_pass": int((a & b).sum()), "vi_only": int((a & ~b).sum()),
                         "cv_only": int((~a & b).sum()), "both_fail": int((~a & ~b).sum())}
        P("## VI rule vs old CV rule\n" + json.dumps(S["vi_vs_cv"]))
    # threshold sweep on VI and halves (pass rate among LOO-steady)
    sweep = {}
    for name, get, skip, ths in (("vi", lambda r: r["rlx"]["vi"], "vi", (1.02, 1.03, 1.04, 1.05, 1.06, 1.08)),
                                 ("halves_power", specs[4][2], "halves", (0.03, 0.05, 0.07, 0.10)),
                                 ("halves_pace", specs[5][2], "halves", (0.03, 0.05, 0.07, 0.10)),
                                 ("walk_max_s", lambda r: r["rlx"]["walk_max_s"], "walk", (120, 180, 240, 300))):
        by = defaultdict(list)
        for r in cand:
            v = get(r)
            if v is not None and others(r, skip):
                by[r["ath"]].append(float(v))
        sweep[name] = {str(t): float(np.mean([np.mean(np.asarray(v) <= t) for v in by.values()])) for t in ths}
    S["sweep"] = sweep
    P("## threshold sweep (athlete-mean pass rate among LOO-steady)\n" + json.dumps(sweep, indent=1))

    # ---- item 2: SE vs run-to-run variance ----
    def pts_of(r, basis):
        a = r["app"]
        if a["code"] not in ("pass_test", "pass_ref"):
            return None
        if basis == "power":
            if a["pw_drift"] is None or a["pw_drift_se"] is None or not (a["pw_ok"] or a["pw_ref_ok"]):
                return None
            return {"drift": a["pw_drift"], "se": a["pw_drift_se"], "x1": a["p1"], "hr1": a["pw_hr1"],
                    "date": r["date"], "tier": a["tier"], "measured": a["measured_s"]}
        if a["drift"] is None or a["drift_se"] is None:
            return None
        return {"drift": a["drift"], "se": a["drift_se"], "x1": a["v1"], "hr1": a["hr1"], "date": r["date"],
                "tier": a["tier"], "measured": a["measured_s"]}

    import datetime as dt

    def dd(s):
        return dt.datetime.strptime(s.replace(" UTC", ""), "%Y/%m/%d %H:%M:%S")

    S["item2"] = {}
    for basis in ("power", "pace"):
        by = defaultdict(list)
        for r in run_rows:
            p = pts_of(r, basis)
            if p:
                p["t"] = dd(p["date"])
                by[r["ath"]].append(p)
        for a in by:
            by[a].sort(key=lambda p: p["t"])
        npts = sum(len(v) for v in by.values())
        ses = [p["se"] for v in by.values() for p in v]
        # pairs at similar intensity: output ±5 %, HR1 ±5 bpm, ≤ 90 days apart
        pairs = defaultdict(list)
        for a, v in by.items():
            for i in range(len(v)):
                for j in range(i + 1, len(v)):
                    if (v[j]["t"] - v[i]["t"]).days > 90:
                        break
                    x, y = v[i], v[j]
                    if x["x1"] and y["x1"] and abs(x["x1"] / y["x1"] - 1) <= 0.05 and abs(x["hr1"] - y["hr1"]) <= 5:
                        z = (x["drift"] - y["drift"]) / math.sqrt(x["se"] ** 2 + y["se"] ** 2)
                        pairs[a].append((x["drift"] - y["drift"], z, x["se"], y["se"]))
        pk = {a: v for a, v in pairs.items() if v}

        def sdz(ks):
            zs = [z for k in ks for _, z, _, _ in pk[k]]
            return float(np.std(zs)) if len(zs) > 2 else float("nan")

        def robust_sdz(ks):
            zs = np.array([z for k in ks for _, z, _, _ in pk[k]])
            return float(1.4826 * np.median(np.abs(zs - np.median(zs)))) if len(zs) > 2 else float("nan")

        def sd_diff(ks):   # observed SD of a single run's drift = SD(Δ)/√2
            ds_ = [d for k in ks for d, _, _, _ in pk[k]]
            return float(np.std(ds_) / math.sqrt(2)) if len(ds_) > 2 else float("nan")

        def rms_se(ks):
            s_ = [s for k in ks for _, _, a_, b_ in pk[k] for s in (a_, b_)]
            return float(math.sqrt(np.mean(np.square(s_)))) if s_ else float("nan")

        item = {"points": npts, "athletes": len(by), "se_q": [float(x) for x in np.percentile(ses, [25, 50, 75])]
                if ses else None,
                "pairs": sum(len(v) for v in pk.values()), "pair_athletes": len(pk)}
        if pk:
            item["sd_z"] = boot_ath(pk, sdz)
            item["robust_sd_z"] = boot_ath(pk, robust_sdz)
            item["obs_sd_single"] = boot_ath(pk, sd_diff)
            item["rms_se"] = boot_ath(pk, rms_se)
        # per tier SE
        for tier in ("test", "ref"):
            s_ = [p["se"] for v in by.values() for p in v if p["tier"] == tier]
            item[f"se_med_{tier}"] = float(np.median(s_)) if s_ else None
            item[f"n_{tier}"] = len(s_)
        # one-way ICC across athletes (≥ 3 points), Spearman-Brown n for 0.8
        g = [np.array([p["drift"] for p in v]) for v in by.values() if len(v) >= 3]
        if len(g) >= 5:
            k_ = len(g)
            N = sum(len(x) for x in g)
            gm = np.mean(np.concatenate(g))
            msb = sum(len(x) * (x.mean() - gm) ** 2 for x in g) / (k_ - 1)
            msw = sum(((x - x.mean()) ** 2).sum() for x in g) / (N - k_)
            n0 = (N - sum(len(x) ** 2 for x in g) / N) / (k_ - 1)
            icc = (msb - msw) / (msb + (n0 - 1) * msw)
            item["icc"] = {"icc": float(icc), "athletes": k_, "runs": N,
                           "n_for_0.8": float(0.8 * (1 - icc) / (0.2 * icc)) if icc > 0 else None,
                           "within_sd": float(math.sqrt(msw)), "between_sd": float(math.sqrt(max(0.0, (msb - msw) / n0)))}
        # 6-run aggregate (drift_agg.aggregate), consecutive non-overlapping blocks
        zs, agg_rows = defaultdict(list), []
        for a, v in by.items():
            if len(v) < 2 * DA.AGG_N:
                continue
            blocks = [v[i:i + DA.AGG_N] for i in range(0, len(v) - DA.AGG_N + 1, DA.AGG_N)]
            ag = [DA.aggregate([{"drift": p["drift"], "se": p["se"]} for p in b]) for b in blocks]
            for b, x in zip(blocks, ag):
                agg_rows.append((a, x["se"], x["se_iv"], x["se_emp"] or 0.0,
                                 (b[-1]["t"] - b[0]["t"]).days))
            for i in range(len(ag) - 1):
                if (blocks[i + 1][0]["t"] - blocks[i][-1]["t"]).days > 120:
                    continue
                zs[a].append((ag[i]["mean"] - ag[i + 1]["mean"]) / math.sqrt(ag[i]["se"] ** 2 + ag[i + 1]["se"] ** 2))
        zs = {a: v for a, v in zs.items() if v}
        if zs:
            item["agg"] = {"athletes": len(zs), "adjacent_pairs": sum(len(v) for v in zs.values()),
                           "sd_z": boot_ath(zs, lambda ks: float(np.std([z for k in ks for z in zs[k]]))),
                           "se_med": float(np.median([r[1] for r in agg_rows])),
                           "se_iv_med": float(np.median([r[2] for r in agg_rows])),
                           "se_emp_med": float(np.median([r[3] for r in agg_rows])),
                           "emp_wins": float(np.mean([r[3] > r[2] for r in agg_rows])),
                           "block_days_med": float(np.median([r[4] for r in agg_rows]))}
        S["item2"][basis] = item
    P("## item 2\n" + json.dumps(S["item2"], indent=1, default=str))

    # ---- item 3: truncation ----
    tr = _load(res / "trunc.jsonl")
    S["item3"] = {}
    for basis, dk, sk in (("power", "pw_drift", "pw_drift_se"), ("pace", "drift", "drift_se")):
        rows3 = [t for t in tr if t["full"][dk] is not None and all(t[str(L)][dk] is not None for L in TRUNC_L)]
        it = {"runs": len(rows3), "athletes": len({t["ath"] for t in rows3})}
        if len(rows3) >= 10:
            by = defaultdict(list)
            for t in rows3:
                by[t["ath"]].append(t)
            for L in list(map(str, TRUNC_L)) + ["full"]:
                d_ = np.array([t[L][dk] for t in rows3])
                se_ = np.array([t[L][sk] for t in rows3 if t[L][sk] is not None])
                # pooled within-athlete SD over the same runs (athletes with ≥ 3)
                w = [np.array([t[L][dk] for t in v]) for v in by.values() if len(v) >= 3]
                wsd = math.sqrt(sum(((x - x.mean()) ** 2).sum() for x in w) / max(1, sum(len(x) - 1 for x in w))) \
                    if w else float("nan")
                it[L] = {"mean": float(d_.mean()), "median": float(np.median(d_)), "sd": float(d_.std()),
                         "se_med": float(np.median(se_)) if len(se_) else None, "within_sd": wsd,
                         "measured_med": float(np.median([t[L]["measured_s"] for t in rows3])) / 60}
                if L != "full":
                    diff = np.array([t[L][dk] - t["full"][dk] for t in rows3])
                    it[L]["vs_full_mean"] = float(diff.mean())
                    it[L]["vs_full_sd"] = float(diff.std())
                    it[L]["corr_full"] = float(np.corrcoef(d_, [t["full"][dk] for t in rows3])[0, 1])
            # how often a 30-min value lands in the same UA band as the full run
            def band(x):
                return 0 if x < 0.035 else (1 if x <= 0.05 else 2)
            it["band_agree_30"] = float(np.mean([band(t["30"][dk]) == band(t["full"][dk]) for t in rows3]))
            it["band_agree_40"] = float(np.mean([band(t["40"][dk]) == band(t["full"][dk]) for t in rows3]))
            it["codes_30"] = dict(_count(t["30"]["code"] for t in tr))
        S["item3"][basis] = it
    P("## item 3\n" + json.dumps(S["item3"], indent=1))

    # ---- item 4: grade ----
    gr = _load(res / "grade.jsonl")
    by_bin = defaultdict(lambda: defaultdict(list))
    for g in gr:
        for b in g["bins"]:
            by_bin[b["bin"]][g["ath"]].append(b)
    edges = (-math.inf,) + GRADE_BINS + (math.inf,)
    t4 = []
    for k in sorted(by_bin):
        ath = by_bin[k]
        # athlete medians first, then across athletes
        am = {a: (np.median([b["pv"] for b in v]), np.median([b["pfh"] for b in v]), np.median([b["ph"] for b in v]),
                  np.median([b["g"] for b in v])) for a, v in ath.items()}
        if len(am) < 5:
            continue
        pv = np.array([x[0] for x in am.values()])
        pfh = np.array([x[1] for x in am.values()])
        gmed = float(np.median([x[3] for x in am.values()]))
        t4.append({"bin": f"{edges[k] * 100:+.0f}…{edges[k + 1] * 100:+.0f}%", "g_med": gmed,
                   "athletes": len(am), "runs": sum(len(v) for v in ath.values()),
                   "pv": [float(x) for x in np.percentile(pv, [25, 50, 75])],
                   "pfh": [float(x) for x in np.percentile(pfh, [25, 50, 75])],
                   "minetti": float(grade_factor(gmed, downhill_floor=None))})
    S["item4_grade"] = {"runs": len(gr), "athletes": len({g["ath"] for g in gr}), "bins": t4}
    P("## item 4 grade\n| bin | athletes | runs | P/v rel flat (p25–p50–p75) | Minetti | P_f/HR rel flat |\n|---|---|---|---|---|---|")
    for t in t4:
        P(f"| {t['bin']} | {t['athletes']} | {t['runs']} | {t['pv'][0]:.2f}–{t['pv'][1]:.2f}–{t['pv'][2]:.2f} | "
          f"{t['minetti']:.2f} | {t['pfh'][0]:.2f}–{t['pfh'][1]:.2f}–{t['pfh'][2]:.2f} |")
    # ramps: drift with ramps − ramp-free, on passing runs
    rb = defaultdict(list)
    rb_pace = defaultdict(list)
    asym = []
    for r in run_rows:
        a = r["app"]
        if a["code"] not in ("pass_test", "pass_ref") or not a.get("ramps"):
            continue
        rp = a["ramps"]
        if a["pw_drift"] is not None and rp.get("pw_drift") is not None and (rp["n1"] + rp["n2"]) > 0:
            rb[r["ath"]].append(a["pw_drift"] - rp["pw_drift"])
            asym.append((rp["n2"] - rp["n1"], a["pw_drift"] - rp["pw_drift"]))
        if a["drift"] is not None and rp.get("drift") is not None and (rp["n1"] + rp["n2"]) > 0:
            rb_pace[r["ath"]].append(a["drift"] - rp["drift"])
    it4 = {}
    for name, by in (("power", rb), ("pace", rb_pace)):
        if sum(len(v) for v in by.values()) >= 10:
            it4[name] = {"runs": sum(len(v) for v in by.values()), "athletes": len(by),
                         "ath_mean": boot_ath(by, lambda ks: float(np.mean([np.mean(by[k]) for k in ks]))),
                         "pooled_median": float(np.median([x for v in by.values() for x in v])),
                         "q": [float(x) for x in np.percentile([x for v in by.values() for x in v], [10, 25, 50, 75, 90])]}
    if len(asym) >= 10:
        a_ = np.array(asym)
        it4["asym_slope_pp_per_ramp"] = float(np.polyfit(a_[:, 0], a_[:, 1], 1)[0] * 100)
        it4["asym_corr"] = float(np.corrcoef(a_[:, 0], a_[:, 1])[0, 1])
    # ramps present at all in flat-passing runs
    nr = [r["app"]["ramps"]["n1"] + r["app"]["ramps"]["n2"] for r in run_rows
          if r["app"]["code"] in ("pass_test", "pass_ref") and r["app"].get("ramps")]
    it4["ramps_per_run_q"] = [float(x) for x in np.percentile(nr, [25, 50, 75])] if nr else None
    it4["runs_with_ramps"] = float(np.mean(np.asarray(nr) > 0)) if nr else None
    S["item4_ramps"] = it4
    P("## item 4 ramps\n" + json.dumps(it4, indent=1))

    (res / "stats.json").write_text(json.dumps(S, indent=1, default=str), encoding="utf-8")
    print("\n".join(out_md))


def cmd_diag(root: Path, n: int, seed: int) -> None:
    """Follow-ups on stats: (a) why the regression SE is small here — ρ₁ / n_eff of
    drift_regression, captured by wrapping the app's function during drift_of (not
    reimplemented); (b) drift vs the halves output difference among otherwise-steady
    runs (the bias a looser halves gate would let in); (c) the stop refusals: how
    much of the 'stopped' time is recording gaps (auto-pause) vs standing."""
    res = root / "results"
    rows = [r for r in _load(res / "runs.jsonl") if r.get("runlike") and "app" in r]
    S: dict = {}
    # (b) from the stored relaxed results
    for basis, dk in (("power", "pw_drift"), ("pace", "drift")):
        xs = [(r["rlx"]["halves_diff"], r["rlx"][dk]) for r in rows
              if r["rlx"][dk] is not None and r["rlx"]["halves_basis"] == basis and r["rlx"]["halves_diff"] is not None
              and (r["rlx"]["walk_max_s"] or 0) < WR.WALK_MAX_S and (r["rlx"]["vi"] is None or r["rlx"]["vi"] <= WR.DRIFT_MAX_VI)
              and (r["rlx"]["finish"] is None or r["rlx"]["finish"] <= WR.DRIFT_FAST_FINISH)
              and abs(r["rlx"]["halves_diff"]) <= 0.15]
        if len(xs) > 20:
            a = np.array(xs)
            sl = np.polyfit(a[:, 0], a[:, 1], 1)[0]
            band = {}
            for lo, hi in ((0, 0.03), (0.03, 0.05), (0.05, 0.07), (0.07, 0.10)):
                for sgn, name in ((1, "+"), (-1, "-")):
                    m = (sgn * a[:, 0] > lo) & (sgn * a[:, 0] <= hi)
                    if m.sum() >= 10:
                        band[f"{name}{lo:.2f}-{hi:.2f}"] = [int(m.sum()), float(np.median(a[m, 1]))]
            S[f"halves_vs_drift_{basis}"] = {"n": len(xs), "slope": float(sl), "median_drift_by_band": band}
    # (a) and (c) on a sample, re-reading the CSVs
    scan = {json.loads(x)["name"]: json.loads(x) for x in (root / "scan.jsonl").read_text(encoding="utf-8").splitlines()}
    by_zip = {}
    for zp in (root / "zips").glob("*.zip"):
        m = scan.get(zp.name)
        if m:
            by_zip[m["athlete"]] = zp
    rng = random.Random(seed)
    passing = [r for r in rows if r["app"]["code"] in ("pass_test", "pass_ref")]
    stops = [r for r in rows if r["app"]["code"] == "stops"]
    cap = []
    orig = WR.drift_regression

    def wrap(*a, **k):
        out = orig(*a, **k)
        if out:
            cap.append(out)
        return out

    rho = defaultdict(list)
    gaps = []
    for kind, pool in (("pass", rng.sample(passing, min(n, len(passing)))),
                       ("stops", rng.sample(stops, min(n, len(stops))))):
        for r in pool:
            with zipfile.ZipFile(by_zip[r["ath"]]) as z:
                cols = parse_csv(z.read(r["csv"]))
            ch = channels(cols)
            if kind == "pass":
                power = ch["power"] if r["has_power"] else None
                cap.clear()
                WR.drift_regression = wrap
                try:
                    call_drift(ch, r["cpm"], power)
                finally:
                    WR.drift_regression = orig
                for i, c in enumerate(cap):
                    basis = "pace" if i == 0 else "power"
                    rho[basis].append((c["rho1"], c["n_eff"], c["se"]))
            else:
                t = ch["t"]
                d = np.diff(t, prepend=t[0])
                after = (t - t[0]) >= WR.WARMUP_S
                gap = float(d[after & (d > WR.MAX_DT)].sum())
                stand = float(d[after & (d <= WR.MAX_DT) & np.isfinite(ch["speed"]) & (ch["speed"] <= WR.STOP_KMH)].sum())
                gaps.append((gap, stand))
    for b, v in rho.items():
        a = np.array(v)
        S[f"rho_{b}"] = {"n": len(v), "rho1_q": [float(x) for x in np.percentile(a[:, 0], [25, 50, 75])],
                         "n_eff_q": [float(x) for x in np.percentile(a[:, 1], [25, 50, 75])],
                         "se_q": [float(x) for x in np.percentile(a[:, 2], [25, 50, 75])]}
    if gaps:
        g = np.array(gaps)
        S["stops"] = {"n": len(g), "gap_share_of_stopped": float(g[:, 0].sum() / max(1e-9, g.sum())),
                      "runs_mostly_gaps": float(np.mean(g[:, 0] > g[:, 1]))}
    (res / "diag.json").write_text(json.dumps(S, indent=1), encoding="utf-8")
    print(json.dumps(S, indent=1))


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x] += 1
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "stats", "diag"))
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--limit", type=int, help="stop after this many runs (smoke test)")
    ap.add_argument("--n", type=int, default=400, help="diag: runs sampled per question")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    root = Path(a.root)
    if a.cmd == "run":
        cmd_run(root, a.limit)
    else:
        cmd_stats(root) if a.cmd == "stats" else cmd_diag(root, a.n, a.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
