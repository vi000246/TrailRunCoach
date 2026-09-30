"""
Run the 百岳 walking-capacity back-test (docs/research/baiyue-from-running.md
§7.1, backtest.capacity_backtest) and merge it into the stored back-test
(validated["hike_capacity"]).

    python -m backend.scripts.baiyue_capacity_backtest [--no-save] [--no-days]
"""
from __future__ import annotations

import argparse


def _p(x):
    return "–" if x is None else f"{x * 100:+.1f}%"


def _a(x):
    return "–" if x is None else f"{x * 100:.1f}%"


def _st(s):
    return f"n={s['n']:3d} |err| {_a(s['median_abs'])} bias {_p(s['bias'])} p10…p90 {_p(s['p10'])}…{_p(s['p90'])}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--no-days", action="store_true")
    a = ap.parse_args(argv)
    from backend.api.wko5views import _dataset
    from backend.engine.racepower import backtest as BT
    res = BT.capacity_backtest(_dataset(), days=not a.no_days,
                               progress=lambda i, n, c: print(c["label"], flush=True))
    if not a.no_save:
        BT.store_capacity(res)
    print(f"\n百岳能力回測（{res['seconds']} s）  passed = {res['passed']}")
    for k, lab in (("trail", "A 越野走路窗（留一活動）"), ("hike", "B 百岳心率窗（留一趟）")):
        r = res[k]
        print(f"{lab}: {_st(r['time'])} groups {r['groups']} passed {r['passed']} {r['reasons']}")
        print(f"    先驗 {_st(r['prior'])}\n    Tobler {_st(r['tobler'])}")
    h = res["high"]
    print(f"B-高 z ≥ 2500 m: {_st(h['time'])} trips {h['trips']}")
    print(f"    個人 α {_st(h['personal'])}\n    收縮 α {_st(h['shrunk'])}\n    Wehrlin {_st(h['wehrlin'])}"
          f"\n    收縮不比最差者差: {h['shrunk_not_worst']}  |bias| ≤ 5 %: {h['passed']}")
    print("C 先驗對照:", res["prior_order"])
    print("σ_LOO (log, segments):", res["sigma_loo"])
    print("α:", res["alpha"])
    print("β:", res["beta"])
    print("γ:", res["gamma"])
    print("v_run:", res["v_run"])
    for k, v in (res["altitude"] or {}).get("versions", {}).items():
        print(f"  altitude {k}: {v}")
    print("  distribution:", {k: v for k, v in (res["altitude"] or {}).get("distribution", {}).items() if k != "per_trip"})
    if res.get("days"):
        d = res["days"]
        print(f"D 整天百岳（跟團） n {d['n']} 在 {d['band']} 的比例 {_a(d['share_in_band'])} passed {d['passed']} "
              f"ratio {_st(d['ratio'])}")
        for r in d["rows"]:
            print(f"   {r['date']} day {r['day']} {r['km']:.1f} km +{r['gain_m']:.0f} m  cap {r['t_cap'] / 3600:.2f} h / "
                  f"act {r['t_act'] / 3600:.2f} h = {r['ratio']:.2f} {'solo' if r['solo'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
