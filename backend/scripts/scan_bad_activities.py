"""
Read-only scan for bad activity files (backend/engine/bad_activity.py):
every foot-sport activity of the 資料來源 in use (COROS or TP FIT folder,
backend/sync/primary.py; --source picks another one or the WKO5 folder), with its features, the auto verdict, and the closest calls (the
highest speed ratio to the limit) so the thresholds can be reviewed.

    python -m backend.scripts.scan_bad_activities [--source primary|wko5|coros|tp|all] [--top 15]

Nothing is written (no cache, no DB). The app DB is only read for the 資料來源 setting.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from backend.engine import bad_activity as B


def _ratio(f: dict) -> float:
    """The highest speed / limit ratio over the rules (1.0 = at the limit)."""
    r = 0.0
    if f.get("avg_kmh") is not None and (f.get("moving_s") or 0) >= B.MIN_MOVING_S:
        r = max(r, f["avg_kmh"] / B.limit_kmh(f["moving_s"]))
    for w in B.WINDOWS_S:
        b = (f.get("best") or {}).get(str(w))
        if b:
            r = max(r, b[0] / B.limit_kmh(w))
    return r


def scan_fit(source: str) -> list[dict]:
    from backend.engine.wko5expr.fitdataset import sport_of
    from backend.files.fit_to_channels import fit_to_channels
    from backend.sync import storage
    root = storage.source_dir(source)
    out = []
    for p in sorted(list(root.rglob("*.fit")) + list(root.rglob("*.fit.gz"))):
        try:
            fc = fit_to_channels(p.read_bytes())
        except Exception:                   # noqa: BLE001
            continue
        sport_raw, sub = (fc.sport or "", getattr(fc, "sub_sport", None))
        if isinstance(sport_raw, str) and "/" in sport_raw:
            sport_raw, sub = sport_raw.split("/", 1)
        group, stype = sport_of(sport_raw, sub)
        if group not in B.FOOT_GROUPS:
            continue
        f = B.features(fc.elapsedtime, fc.channels.get("elapseddistance"), fc.channels.get("power"))
        out.append({"source": source, "file": p.name, "start": fc.start_time, "sport": stype, "f": f,
                    "group": group})
    return out


def scan_wko5() -> list[dict]:
    from backend.files.wko4_file import read_wko4
    from backend.files.wko5_athlete import read_athlete
    from backend.settings.paths import athlete_dir
    d = Path(athlete_dir())
    f = next(d.glob("*.wko5athlete"), None)
    if f is None:
        return []                       # no WKO5 folder: only the synced sources are scanned
    a = read_athlete(f)
    out = []
    for e in a.workouts:
        group = (e.sport_group or "").lower()
        if e.start is None or group not in B.FOOT_GROUPS:
            continue
        p = d / e.file
        if not p.exists():
            continue
        try:
            w = read_wko4(p)
        except Exception:                   # noqa: BLE001
            continue
        ch = w.channels
        tv = ch.get("elapsedtime")
        dv = ch.get("elapseddistance")
        pv = ch.get("power")
        f = B.features(tv.values if tv else None, dv.values if dv else None, pv.values if pv else None)
        out.append({"source": "wko5", "file": e.file, "start": e.start, "sport": (e.sport or "").lower(),
                    "f": f, "group": group})
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="primary", choices=("primary", "all", "wko5", "coros", "tp"))
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--weight", type=float, default=None)
    a = ap.parse_args(argv)
    rows = []
    if a.source == "primary":
        from backend.engine.wko5expr.datasource import primary_folder
        a.source = primary_folder()
    for src in (("wko5", "coros", "tp") if a.source == "all" else (a.source,)):
        rows += scan_wko5() if src == "wko5" else scan_fit(src)
    groups = {}
    for r in rows:
        r["verdict"] = B.judge(r["f"], r["group"], a.weight)
        r["ratio"] = _ratio(r["f"])
        groups.setdefault((r["source"], r["group"]), 0)
        groups[(r["source"], r["group"])] += 1
    print("scanned:", ", ".join(f"{s}/{g} {n}" for (s, g), n in sorted(groups.items())))
    bad = [r for r in rows if r["verdict"]]
    print(f"\nFLAGGED ({len(bad)}):")
    for r in sorted(bad, key=lambda r: str(r["start"])):
        f, v = r["f"], r["verdict"]
        print(f"  {r['source']:5} {str(r['start'])[:16]} {r['sport']:16} {f.get('distance_km') or 0:6.2f} km "
              f"moving {f['moving_s'] / 60:5.1f} min avg {f.get('avg_kmh') or 0:5.1f} km/h "
              f"P {f.get('avg_power') or 0:4.0f} W  {r['file']}\n        -> {v['rule']}: {v['reason']}")
    print(f"\nclosest calls (not flagged), highest speed / limit:")
    for r in sorted((r for r in rows if not r["verdict"]), key=lambda r: -r["ratio"])[:a.top]:
        f = r["f"]
        best = " ".join(f"{w}s {f['best'][str(w)][0]:.1f}" for w in B.WINDOWS_S if str(w) in f["best"])
        print(f"  {r['ratio']:.2f} {r['source']:5} {str(r['start'])[:16]} {r['sport']:16} "
              f"{f.get('distance_km') or 0:6.2f} km avg {f.get('avg_kmh') or 0:5.1f} km/h  [{best}]  "
              f"P {f.get('avg_power') or 0:4.0f} W  {r['file']}")
    pw = sorted((r for r in rows if r["f"].get("avg_power")), key=lambda r: -r["f"]["avg_power"])[:5]
    print("\nhighest average power:")
    for r in pw:
        print(f"  {r['f']['avg_power']:5.0f} W {r['source']:5} {str(r['start'])[:16]} {r['sport']} {r['file']}")


if __name__ == "__main__":
    main()
