"""
Heat-acclimation back-test on the athlete's own route efforts —
docs/research/heat-acclimation.md §6.2. Reads the route index (efforts with
`wx`, filled by a weather-enabled routes build) and activity_weather.json
(per-activity exposure → S), both under routes.HOME (WKO5COACH_ROUTES_DIR).

Model (OLS, route fixed effects by demeaning, 自組):
    HR = route FE + b_p·P + b_m·moving min + time-of-day + β_season·(Hadley − 120)
Test A: β > 0 (heat raises HR at a given power — context only).
Test B (the main one): the HR–Hadley slope in late summer (Aug–Sep) is
    smaller than in early summer (May–Jun), and summer vs winter;
    and β·(1 − a_hr·S) against a constant β by AIC.
CTL is not in the model (not stored on efforts): a stated limitation.

    python -m backend.scripts.heat_backtest [--min-min 20] [--json out.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections import defaultdict

import numpy as np

SEASONS = {"winter": (12, 1, 2), "spring": (3, 4), "early_summer": (5, 6), "july": (7,), "late_summer": (8, 9),
           "autumn": (10, 11)}
H0 = 120.0


def season_of(m: int) -> str:
    return next(k for k, v in SEASONS.items() if m in v)


def tod(h: int) -> str:
    return "morning" if h < 11 else "midday" if h < 16 else "evening"


def load(root):
    from backend.engine import route_weather as RW
    idx = json.loads((root / "index.json").read_text("utf-8"))
    aw = RW.load_activity_weather(root)
    return idx, aw


def efforts(idx: dict, min_min: float) -> list[dict]:
    out, seen = [], set()
    for kind in ("routes", "segments"):
        for r in idx.get(kind, []):
            for e in r.get("efforts", []):
                wx = e.get("wx")
                if e.get("sport") != "run" or not wx or e.get("avg_hr") is None or not e.get("avg_power") or (e.get("moving_s") or 0) < min_min * 60:
                    continue
                key = (e["file"], r["id"])
                if key in seen:
                    continue
                seen.add(key)
                st = dt.datetime.fromisoformat(e["start"])
                out.append({"route": f"{kind}:{r['id']}", "file": e["file"], "date": st.date(), "hour": st.hour,
                            "hr": float(e["avg_hr"]), "p": float(e["avg_power"]), "mv": e["moving_s"] / 60.0,
                            "hadley": float(wx["hadley"]), "temp_c": wx["temp_c"]})
    return out


def ols_fe(rows, cols, group="route"):
    """Demean y and X within groups, OLS; returns coefficients, SE, RSS, n, k."""
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[r[group]].append(i)
    keep = [i for g, ix in by.items() if len(ix) >= 2 for i in ix]
    y = np.array([rows[i]["hr"] for i in keep])
    X = np.array([[c(rows[i]) for c in cols.values()] for i in keep], float)
    gid = [rows[i][group] for i in keep]
    for g in set(gid):
        m = np.array([x == g for x in gid])
        y[m] -= y[m].mean()
        X[m] -= X[m].mean(axis=0)
    ok = X.std(axis=0) > 1e-12
    Xk = X[:, ok]
    beta, *_ = np.linalg.lstsq(Xk, y, rcond=None)
    res = y - Xk @ beta
    n, k = len(y), Xk.shape[1] + len(set(gid))
    s2 = (res @ res) / max(1, n - k)
    cov = s2 * np.linalg.pinv(Xk.T @ Xk)
    names = [nm for nm, o in zip(cols, ok) if o]
    return {"coef": dict(zip(names, beta.tolist())), "se": dict(zip(names, np.sqrt(np.diag(cov)).tolist())),
            "rss": float(res @ res), "n": n, "k": k, "groups": len(set(gid))}


def aic(fit):
    return fit["n"] * math.log(fit["rss"] / fit["n"]) + 2 * fit["k"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-min", type=float, default=20.0)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    from backend.engine import heat as HT
    from backend.engine import routes as R
    root = R.HOME
    idx, aw = load(root)
    rows = efforts(idx, a.min_min)
    acts = [dict(v, file=f) for f, v in (aw.get("activities") or {}).items() if v]
    doses = HT.doses_from(acts)
    if doses:
        ser = dict(HT.status_series(doses, min(doses), max(max(doses), max(r["date"] for r in rows))))
    else:
        ser = {}
    for r in rows:
        r["S"] = ser.get(r["date"] - dt.timedelta(days=1), 0.0)
        r["season"] = season_of(r["date"].month)
        r["tod"] = tod(r["hour"])
    base = {"p": lambda r: r["p"], "mv": lambda r: r["mv"],
            "midday": lambda r: 1.0 if r["tod"] == "midday" else 0.0,
            "evening": lambda r: 1.0 if r["tod"] == "evening" else 0.0}
    out = {"n_efforts": len(rows), "routes": len({r["route"] for r in rows}),
           "activities_with_weather": len(acts), "min_minutes": a.min_min}
    # A: one β
    fa = ols_fe(rows, {**base, "heat": lambda r: r["hadley"] - H0})
    out["A"] = {"beta": fa["coef"].get("heat"), "se": fa["se"].get("heat"), "n": fa["n"], "aic": aic(fa)}
    # B: β by season
    cols = dict(base)
    for s in SEASONS:
        cols[f"heat_{s}"] = (lambda s: lambda r: (r["hadley"] - H0) if r["season"] == s else 0.0)(s)
    fb = ols_fe(rows, cols)
    out["B_season"] = {s: {"beta": fb["coef"].get(f"heat_{s}"), "se": fb["se"].get(f"heat_{s}"),
                           "n": sum(1 for r in rows if r["season"] == s),
                           "hadley_range": [min((r["hadley"] for r in rows if r["season"] == s), default=None),
                                            max((r["hadley"] for r in rows if r["season"] == s), default=None)]}
                       for s in SEASONS}
    e, l = out["B_season"]["early_summer"], out["B_season"]["late_summer"]
    if e["beta"] is not None and l["beta"] is not None:
        d = l["beta"] - e["beta"]
        se = math.sqrt((e["se"] or 0) ** 2 + (l["se"] or 0) ** 2)
        out["B_late_minus_early"] = {"diff": d, "se": se, "z": d / se if se else None,
                                     "supports_acclimation": d < 0 and se > 0 and d / se < -1.645}
    # summer (May–Sep) vs winter (Dec–Feb)
    summer = {"early_summer", "july", "late_summer"}
    fsw = ols_fe(rows, {**base, "heat_summer": lambda r: (r["hadley"] - H0) if r["season"] in summer else 0.0,
                        "heat_winter": lambda r: (r["hadley"] - H0) if r["season"] == "winter" else 0.0,
                        "heat_other": lambda r: (r["hadley"] - H0) if r["season"] not in summer | {"winter"} else 0.0})
    bs, bw = fsw["coef"].get("heat_summer"), fsw["coef"].get("heat_winter")
    ss, sw = fsw["se"].get("heat_summer"), fsw["se"].get("heat_winter")
    out["B_summer_vs_winter"] = {"summer": {"beta": bs, "se": ss}, "winter": {"beta": bw, "se": sw},
                                 "diff": (bs - bw) if bs is not None and bw is not None else None,
                                 "se": math.sqrt(ss ** 2 + sw ** 2) if ss and sw else None}
    # B: β·(1 − a_hr·S), grid over a_hr
    grid = []
    for ahr in np.linspace(0.0, 1.0, 21):
        f = ols_fe(rows, {**base, "heat": (lambda ahr: lambda r: (r["hadley"] - H0) * (1 - ahr * r["S"]))(ahr)})
        grid.append({"a_hr": float(ahr), "aic": aic(f), "beta": f["coef"].get("heat"), "se": f["se"].get("heat")})
    best = min(grid, key=lambda g: g["aic"])
    out["B_S_model"] = {"best": best, "constant": grid[0], "delta_aic": best["aic"] - grid[0]["aic"],
                        "grid": grid}
    out["S_by_season"] = {s: float(np.mean([r["S"] for r in rows if r["season"] == s])) if any(
        r["season"] == s for r in rows) else None for s in SEASONS}
    print(json.dumps({k: v for k, v in out.items() if k != "B_S_model"}, indent=1, default=str, ensure_ascii=False))
    print("S model: best a_hr", best["a_hr"], "ΔAIC vs constant β", round(out["B_S_model"]["delta_aic"], 2),
          "β", best["beta"], "±", best["se"])
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, default=str, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
