"""
Run the race-power v2 leave-one-out back-test on the athlete's own data
(docs/research/racepower-v2.md §3B) and store it where the page reads it
(backend/engine/racepower/backtest.py STORE).

    python -m backend.scripts.racepower_backtest [--no-save]
"""
from __future__ import annotations

import argparse
import sys


def _pct(x):
    return "–" if x is None else f"{x * 100:+.1f}%"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)
    from backend.api.wko5views import _dataset
    from backend.engine.racepower import backtest as BT
    ds = _dataset()

    def prog(i, n, c):
        print(f"[{i + 1}/{n}] {c['category']:5s} {c.get('label')} day={c.get('day')}", flush=True)
    res = BT.backtest(ds, progress=prog)
    if not a.no_save:
        BT.save(res)
    print(f"\n{len(res['rows'])} cases in {res['seconds']} s")
    for r in res["rows"]:
        if "error" in r:
            print(f"  {r['date']} {r['category']:5s} {r.get('label')}: {r['error']}")
            continue
        eff = r.get("effort") or {}
        print(f"  {r['date']} {r['category']:5s} {r['km']:5.1f} km ↑{r['gain_m']:5.0f}  act {r['t_act'] / 60:6.1f} min  "
              f"v2 {_pct(r['err_v2'])}  v1 {_pct(r.get('err_v1'))}  f {eff.get('f', float('nan')):.3f} {eff.get('label', '')}")
    print()
    for cat in ("road", "trail", "hike"):
        s = res["summary"][cat]
        print(f"{s['label']}: n={s['n']}  v2 |err| med {_pct(s['v2_median_abs_err'])} (signed {_pct(s['v2_median_err'])})  "
              f"v1 |err| med {_pct(s['v1_median_abs_err'])}  downhill bias {_pct(s['downhill_bias'])}  "
              f"up/flat {s['up_over_flat']}  2nd/1st {s['second_over_first']}  "
              f"{'PASS' if s['passed'] else 'FAIL: ' + '；'.join(s['reasons'])}")
        for cls, c in s["classes"].items():
            if c["n"]:
                print(f"    {cls:10s} n={c['n']:4d}  speed err med {_pct(c['median_err'])}  |err| {_pct(c['median_abs_err'])}")
    e = res["summary"]["effort"]
    print(f"effort bar: A races {e['n_a_races']}, long-run labels {e['long_run_labels']}, "
          f"long-run median f {e['long_run_median_f']}  {'PASS' if e['passed'] else 'FAIL: ' + '；'.join(e['reasons'])}")
    print("validated:", res["validated"], "effort:", res["effort_validated"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
