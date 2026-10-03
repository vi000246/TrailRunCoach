"""Validate core metric formulas against WKO5 reverse-engineered constants.

Authoritative constants come from docs/spec/wko5-training-load-charts.spec.md
(extracted verbatim from a WKO5 Season View export (`.wko5chart`)):
  - CTL = tl(tss, ctlconstant)  EWMA tau = 42 days
  - ATL = tl(tss, atlconstant)  EWMA tau = 7 days
  - TSB = CTL - ATL (shifted 1 day)
  - ACWR = ATL / CTL
  - rTSS = (duration_h) * IF^2 * 100, IF = threshold_pace / avg_pace

These are property/identity checks the implementation must satisfy. The climb
load metric is intentionally excluded — it is an original metric with no WKO5
source to validate against.
"""
from datetime import date, timedelta

from backend.engine.algorithms.metrics import compute_run_pmc, pace_rtss


def validate_core_formulas() -> list[dict]:
    out: list[dict] = []

    # Steady 50 TSS/day for 365 days → CTL & ATL both converge to 50, TSB ≈ 0,
    # ACWR ≈ 1.0 (EWMA tau 42 / 7 from the WKO5 chart definition).
    series = [(date(2025, 1, 1) + timedelta(days=i), 50.0) for i in range(365)]
    last = compute_run_pmc(series)[-1]
    out.append({
        "name": "CTL_tau42_converges_to_50",
        "pass": abs(last["ctl"] - 50.0) < 0.5,
        "expected": 50.0, "actual": round(last["ctl"], 3),
    })
    out.append({
        "name": "ATL_tau7_converges_to_50",
        "pass": abs(last["atl"] - 50.0) < 0.5,
        "expected": 50.0, "actual": round(last["atl"], 3),
    })
    out.append({
        "name": "TSB_converges_to_0",
        "pass": abs(last["tsb"]) < 0.5,
        "expected": 0.0, "actual": round(last["tsb"], 3),
    })
    out.append({
        "name": "ACWR_steady_approx_1",
        "pass": abs(last["acwr"] - 1.0) < 0.02,
        "expected": 1.0, "actual": round(last["acwr"], 3),
    })

    # rTSS: running at exactly threshold pace for 1 hour → 100.
    # threshold 5.0 s/m, 10 km in 50 000 s = 5.0 s/m → IF = 1 → rTSS = (50000/3600)*100.
    expected_rtss = round((50_000.0 / 3600.0) * 100.0, 1)
    actual_rtss = pace_rtss(10_000.0, 50_000.0, 5.0)
    out.append({
        "name": "rTSS_at_threshold_pace",
        "pass": abs(actual_rtss - expected_rtss) < 1.0,
        "expected": expected_rtss, "actual": actual_rtss,
    })

    return out


def render_report(report: list[dict]) -> str:
    lines = ["# Formula Validation Report (vs WKO5)", ""]
    lines.append("| Check | Expected | Actual | Result |")
    lines.append("|---|---|---|---|")
    for r in report:
        mark = "✅ pass" if r["pass"] else "❌ FAIL"
        lines.append(f"| {r['name']} | {r['expected']} | {r['actual']} | {mark} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render_report(validate_core_formulas()))
