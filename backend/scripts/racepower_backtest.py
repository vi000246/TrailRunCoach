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
    a = ap.parse_args(argv)
    from backend.api.wko5views import _dataset
    from backend.engine.racepower import backtest as BT
    ds = _dataset()

    def prog(i, n, c):
        print(f"[{i + 1}/{n}] {c['category']:5s} {c.get('intensity') or '-':6s} {c.get('label')}", flush=True)
    res = BT.backtest(ds, progress=prog)
    if not a.no_save:
        BT.save(res)
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
    print("validated:", res["validated"], "effort:", res["effort_validated"])
    hh = res["hike_hr"]
    print("hike HR windows:", {k: v.get("n") for k, v in hh["uses"].items()},
          "altitude", hh["uses"]["altitude"].get("pct_per_km"), "fatigue", hh["uses"]["fatigue"].get("days"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
