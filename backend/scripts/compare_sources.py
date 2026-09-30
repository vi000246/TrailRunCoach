"""
Read-only source-consistency report.

Pairs the same activity across sources by start time (± WINDOW) and prints
the differences:

  * synced rows in the app DB (COROS and TrainingPeaks FITs) against each other
  * synced rows against the WKO5 athlete folder (.wko5athlete + .wko4), which is
    what the WKO5-aligned charts read

Fields: duration, distance, elevation gain, avg power, NP, avg HR, TSS.
Nothing is written — not the DB, not the WKO5 folder.

Usage:
    python -m backend.scripts.compare_sources --since 2026-08-01
    python -m backend.scripts.compare_sources --athlete-dir "D:/WKO5/Me" --csv out.csv

The WKO5 folder comes from --athlete-dir or WKO5COACH_ATHLETE_DIR (no default:
there is no machine-specific path in the code). Without it only the
COROS vs TP comparison runs.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

WINDOW_S = 120.0
FIELDS = ("duration_s", "distance_km", "gain_m", "avg_power", "np", "avg_hr", "tss")
# relative differences above these are flagged
TOLERANCE = {"duration_s": 0.02, "distance_km": 0.02, "gain_m": 0.10, "avg_power": 0.03,
             "np": 0.03, "avg_hr": 0.02, "tss": 0.05}


@dataclass
class Act:
    source: str
    key: str                       # db id / wko4 file
    start_utc: dt.datetime         # naive UTC
    sport: Optional[str] = None
    values: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# pure helpers (unit tested)
# ---------------------------------------------------------------------------

def pair(a: Sequence[Act], b: Sequence[Act], window_s: float = WINDOW_S) -> tuple[list, list, list]:
    """Greedy nearest-start matching. Returns (pairs, only_a, only_b)."""
    cands = []
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            d = abs((x.start_utc - y.start_utc).total_seconds())
            if d <= window_s:
                cands.append((d, i, j))
    cands.sort()
    used_a, used_b, pairs = set(), set(), []
    for d, i, j in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i); used_b.add(j)
        pairs.append((a[i], b[j], d))
    only_a = [x for i, x in enumerate(a) if i not in used_a]
    only_b = [y for j, y in enumerate(b) if j not in used_b]
    return pairs, only_a, only_b


def diff(x: Act, y: Act) -> dict:
    out = {}
    for f in FIELDS:
        va, vb = x.values.get(f), y.values.get(f)
        if va is None or vb is None:
            out[f] = {"a": va, "b": vb, "delta": None, "rel": None, "flag": False}
            continue
        delta = vb - va
        base = max(abs(va), abs(vb))
        rel = abs(delta) / base if base > 0 else 0.0
        out[f] = {"a": va, "b": vb, "delta": delta, "rel": rel, "flag": rel > TOLERANCE[f]}
    return out


def normalized_power(power: Sequence[float]) -> Optional[float]:
    import numpy as np
    p = np.asarray(power, dtype=float)
    p = np.where(np.isfinite(p), p, 0.0)
    if len(p) < 30 or not (p > 0).any():
        return None
    roll = np.convolve(p, np.ones(30) / 30.0, mode="valid")
    return float(np.mean(roll ** 4) ** 0.25)


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------

def _fit_values(path: str) -> dict:
    import numpy as np
    from backend.files.fit_reader import parse_fit
    raw = parse_fit(path)
    hr = raw.heart_rate_bpm[raw.heart_rate_bpm > 0] if raw.has_hr else []
    pw = raw.power_w if raw.has_power else None
    ta = raw.session.get("total_ascent")
    return {
        "duration_s": raw.duration_s or None,
        "distance_km": (raw.total_distance_m or 0) / 1000.0 or None,
        "gain_m": float(ta) if ta is not None else None,
        "avg_power": float(np.mean(pw)) if pw is not None else None,
        "np": normalized_power(pw) if pw is not None else None,
        "avg_hr": float(np.mean(hr)) if len(hr) else None,
    }


async def load_synced(since: Optional[dt.date]) -> list[Act]:
    """Plain SQL, so it also works on a DB the app hasn't migrated yet (the
    script never alters the schema)."""
    from sqlalchemy import text
    from backend.db.database import AsyncSessionLocal
    out = []
    async with AsyncSessionLocal() as db:
        cols = {r[1] for r in (await db.execute(text("PRAGMA table_info(workout_files)"))).all()}
        if not cols:
            return out
        has_start = "start_time_utc" in cols
        sql = ("SELECT id, source, sport, file_path, workout_date"
               + (", start_time_utc" if has_start else ", NULL")
               + " FROM workout_files WHERE source IN ('coros','trainingpeaks') AND file_format='fit'")
        params = {}
        if since:
            sql += " AND workout_date >= :since"
            params["since"] = since.isoformat()
        rows = (await db.execute(text(sql), params)).all()
        for rid, source, sport, path, _wd, start in rows:
            ms = dict((await db.execute(text(
                "SELECT metric_key, value FROM workout_metrics WHERE workout_id=:i"), {"i": rid})).all())
            try:
                vals = _fit_values(path)
            except Exception as e:  # missing / unreadable file: still list it
                vals = {"error": str(e)}
            if isinstance(start, str):
                start = dt.datetime.fromisoformat(start)
            if start is None:
                try:
                    from backend.files.fit_reader import parse_fit
                    start = parse_fit(path).start_time
                except Exception:
                    continue
            if start is None:
                continue
            vals["tss"] = ms.get("tss") if ms.get("tss") is not None else ms.get("hr_tss")
            out.append(Act(source, f"db#{rid}", start.replace(tzinfo=None), sport, vals))
    return out


def load_wko5(athlete_dir: Path, since: Optional[dt.date], tz) -> list[Act]:
    import numpy as np
    from backend.engine.wko5expr.dataset import Dataset, day_to_date
    ds = Dataset(athlete_dir)
    out = []
    for w in ds.workouts:
        start = w.entry.start
        if start is None or (since and start.date() < since):
            continue
        utc = start.replace(tzinfo=tz).astimezone(dt.timezone.utc).replace(tzinfo=None)
        m = w.metrics
        act = Act("wko5", w.entry.file, utc, w.sport_type, {
            "duration_s": m.get("duration"), "distance_km": m.get("distance"),
            "gain_m": m.get("climbing"), "np": m.get("np"), "tss": m.get("tss")})
        act._ds, act._idx = ds, w.idx   # lazily read channels for matched rows only
        out.append(act)
    return out


def _fill_wko5_channels(act: Act) -> None:
    import numpy as np
    ds = getattr(act, "_ds", None)
    if ds is None:
        return
    for name, key in (("heartrate", "avg_hr"), ("power", "avg_power")):
        c = ds.channel(act._idx, name)
        if c is not None:
            v = c[np.isfinite(c) & (c > 0)]
            act.values[key] = float(v.mean()) if len(v) else None


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _fmt(v, f):
    if v is None:
        return "—"
    if f == "duration_s":
        return f"{int(v // 3600)}:{int(v % 3600 // 60):02d}:{int(v % 60):02d}"
    return f"{v:.1f}" if abs(v) < 1000 else f"{v:.0f}"


def report(title: str, pairs, only_a, only_b, la: str, lb: str, rows_out: list) -> None:
    print(f"\n=== {title}: {len(pairs)} matched, {len(only_a)} only {la}, {len(only_b)} only {lb} ===")
    if not pairs:
        return
    agg = {f: [] for f in FIELDS}
    for a, b, d in sorted(pairs, key=lambda p: p[0].start_utc):
        df = diff(a, b)
        flags = [f for f in FIELDS if df[f]["flag"]]
        line = " ".join(f"{f}={_fmt(df[f]['a'], f)}/{_fmt(df[f]['b'], f)}" for f in FIELDS
                        if df[f]["a"] is not None or df[f]["b"] is not None)
        mark = "!!" if flags else "  "
        print(f"{mark} {a.start_utc:%Y-%m-%d %H:%M}Z Δt={d:.0f}s {a.key} ↔ {b.key}  {line}"
              + (f"  [差異: {', '.join(flags)}]" if flags else ""))
        for f in FIELDS:
            if df[f]["rel"] is not None:
                agg[f].append(df[f]["rel"])
        rows_out.append({"compare": title, "start_utc": a.start_utc.isoformat(), "a": a.key, "b": b.key,
                         "dt_s": d, **{f"{f}_{la}": df[f]["a"] for f in FIELDS},
                         **{f"{f}_{lb}": df[f]["b"] for f in FIELDS}, "flags": ",".join(flags)})
    print("  平均相對差異: " + ", ".join(
        f"{f} {100 * sum(v) / len(v):.1f}% (n={len(v)})" for f, v in agg.items() if v))
    for x in only_a[:20]:
        print(f"  only {la}: {x.start_utc:%Y-%m-%d %H:%M}Z {x.key}")
    for x in only_b[:20]:
        print(f"  only {lb}: {x.start_utc:%Y-%m-%d %H:%M}Z {x.key}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=dt.date.fromisoformat)
    ap.add_argument("--athlete-dir", default=os.getenv("WKO5COACH_ATHLETE_DIR"))
    ap.add_argument("--tz", default=None, help="IANA zone of WKO5 start times (default: settings / system)")
    ap.add_argument("--window", type=float, default=WINDOW_S)
    ap.add_argument("--csv")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from backend.settings.repository import resolve_tz
    tz = resolve_tz(args.tz)
    synced = asyncio.run(load_synced(args.since))
    coros = [a for a in synced if a.source == "coros"]
    tp = [a for a in synced if a.source == "trainingpeaks"]
    rows: list[dict] = []
    report("COROS vs TrainingPeaks", *pair(coros, tp, args.window), "coros", "tp", rows)
    if args.athlete_dir:
        wk = load_wko5(Path(args.athlete_dir), args.since, tz)
        for label, side in (("COROS vs WKO5", coros), ("TrainingPeaks vs WKO5", tp)):
            pairs, oa, ob = pair(side, wk, args.window)
            for _, b, _ in pairs:
                _fill_wko5_channels(b)
            report(label, pairs, oa, ob if side else [], label.split()[0].lower(), "wko5", rows)
    else:
        print("\n(未指定 --athlete-dir / WKO5COACH_ATHLETE_DIR：略過與 WKO5 的比對)")
    if args.csv and rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
