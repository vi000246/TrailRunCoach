"""
Run the race-power v2 leave-one-out back-tests on the athlete's own data
(docs/research/racepower-v2.md §3B, backend/engine/racepower/backtest.py)
and store them where the page reads them (backtest.STORE).

    python -m backend.scripts.racepower_backtest [--no-save]
"""
from __future__ import annotations

import argparse
import sys


def _pct(x):
    return "–" if x is None else f"{x * 100:+.1f}%"


def _abs(x):
    return "–" if x is None else f"{x * 100:.1f}%"


def _st(s):
    return (f"n={s['n']:3d} |err| {_abs(s['median_abs'])} bias {_pct(s['bias'])} "
            f"p10…p90 {_pct(s['p10'])}…{_pct(s['p90'])}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--out", help="also write the result JSON here (e.g. a scratch path)")
    ap.add_argument("--quiet", action="store_true", help="no per-case progress lines")
    ap.add_argument("--source", choices=("wko5", "coros", "tp"),
                    help="data source (default: the charts.data_source setting)")
    a = ap.parse_args(argv)
    from pathlib import Path
    from backend.api.wko5views import _dataset
    from backend.engine.racepower import backtest as BT
    ds = _dataset(source=a.source)
    print(f"source {a.source or 'setting'}: {len(ds.workouts)} workouts", flush=True)

    def prog(i, n, c):
        if not a.quiet:
            print(f"[{i + 1}/{n}] {c['category']:5s} {c.get('intensity') or '-':6s} {c.get('label')}", flush=True)
    res = BT.backtest(ds, progress=prog)
    if not a.no_save:
        BT.save(res)
    if a.out:
        BT.save(res, Path(a.out))
    print(f"\n{len(res['rows'])} cases in {res['seconds']} s; classes {res['intensity_counts']}")
    for r in res["rows"]:
        if "error" in r:
            print(f"  {r['date']} {r['category']:5s} {r.get('label')}: {r['error']}")
    te = res["terrain"]
    print("\n== 地形模型回測 (mode B, actual power) ==")
    for cat, c in te["categories"].items():
        print(f"{c['label']}: time {_st(c['time'])}  v1 {_abs(c['time_v1']['median_abs'])}  downhill bias {_pct(c['downhill']['bias'])}")
        for g, s in (c.get("groups") or {}).items():
            print(f"    {s['label']}: {_st(s)}")
    print("by class:")
    for k, c in te["classes"].items():
        print(f"  {c['label']:5s} acts {c['activities']:3d}  time {_st(c['time'])}  segs {_st(c['segments'])}  "
              f"class-model segs |err| {_abs(c['segments_class_model']['median_abs'])}")
    print("class × grade bin (segment speed error: bias |err| (n)):")
    print("  " + " | ".join(f"{b:>18s}" for b in res["grade_bins"]))
    for k in list(te["classes"]) + ["all"]:
        row = te["grid"][k]
        print(f"  {k:6s} " + " | ".join(f"{_pct(row[b]['bias']):>7s} {_abs(row[b]['median_abs']):>6s} ({row[b]['n']:4d})"
                                        for b in res["grade_bins"]))
    print("models:", {k: _abs(v["median_abs"]) for k, v in te["models"].items()}, "class differs:", te["class_differs"])
    ca = res["capacity"]
    print("\n== 能力模型回測 (race-like + tests) ==")
    if ca["message"]:
        print("  " + ca["message"])
    for cat, c in ca["categories"].items():
        print(f"{c['label']}: n={c['n']} time {_st(c['time'])} power {_st(c['power'])} f med {c['f_median']} "
              f"{'PASS' if c['passed'] else 'FAIL: ' + '；'.join(c['reasons'])}")
    print("tests:", ca["tests"]["n"], _st(ca["tests"]["power"]))
    for r in ca["tests"]["rows"]:
        print(f"   {r['label']}: P_sus {r['capacity']['p_sus']:.1f} vs {r['p_act']:.1f} W "
              f"(CP {r['capacity']['cp']:.1f} {r['capacity']['cp_source']})")
    lb = ca["lower_bound"]
    print(f"lower bound: {lb['violations']} of {lb['n']} runs above the model's sustainable power")
    for r in lb["worst"][:6]:
        print(f"   {r['date']} {r['label']}: {r['t_s'] / 60:.0f} min {r['p_train']:.0f} W vs P_sus {r['p_sus']:.0f} W "
              f"(f {r['f']:.2f}, CP {r['cp']:.0f} {r['cp_source']})")
    print("effort:", ca["effort"])
    print("\n== capacity samples (365 d) ==", res["capacity_samples"]["counts"])
    for s in res["capacity_samples"]["rows"]:
        print(f"  {'OK ' if s['ok'] else '-- '}{s['label']} [{s['kind']}] {s['reason']}")
    hc = res.get("hr_capacity") or {}
    print("\n== HR capacity now ==", {k: hc.get(k) for k in ("p_lthr", "range", "lthr", "n_runs", "span_bpm",
                                                             "extrap_bpm", "valid", "reasons", "dropped_drift")},
          "slope", (hc.get("fit") or {}).get("b"), "r2", (hc.get("fit") or {}).get("r2"))
    dist = hc.get("distribution") or {}
    if dist:
        print("  training % of HR capacity: median", round(dist["median"], 3), "p90", round(dist["p90"], 3),
              [(b["label"], b["runs"], round(b["share_time"], 3)) for b in dist["bands"]])
    dc = hc.get("distribution_cp") or {}
    if dc.get("bands"):
        print(f"  training % of CP {dc['cp']:.1f}: median", round(dc["median"], 3), "p90", round(dc["p90"], 3),
              [(b["label"], b["runs"], round(b["share_time"], 3)) for b in dc["bands"]])
    print("HR vs power envelope (capacity cases):")
    for name, blk in list(ca["categories"].items()) + [("tests", ca["tests"])]:
        for k, v in (blk.get("hr") or {}).items():
            print(f"  {name:5s} {k:15s} power {_st(v['power'])}  time {_st(v['time'])}")
    for r in res["rows"]:
        if r.get("cap_sample") and r.get("hr"):
            hh = r["hr"]
            print(f"   {r['date']} {r.get('label')}: P act {(r.get('p_train') or 0):.1f}  envelope "
                  f"{(r.get('capacity') or {}).get('p_sus') or 0:.1f}  HR tt30 {hh.get('p_sus_tt30') or 0:.1f} "
                  f"tte {hh.get('p_sus_tte') or 0:.1f} comb {hh.get('p_sus_comb') or 0:.1f} (P_LTHR "
                  f"{hh.get('p_lthr') or 0:.1f}, n_max {hh.get('n_max')}, valid {hh.get('valid')} {hh.get('reasons')})"
                  f" err_c {_pct(r.get('err_c'))} hr_tte {_pct(r.get('err_c_hr_tte'))} hr_tt30 {_pct(r.get('err_c_hr_tt30'))}")
    print("validated:", res["validated"], "effort:", res["effort_validated"])
    hh = res["hike_hr"]
    print("hike HR windows:", {k: v.get("n") for k, v in hh["uses"].items()},
          "altitude", hh["uses"]["altitude"].get("pct_per_km"), "fatigue", hh["uses"]["fatigue"].get("days"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
