"""
Per-activity metric comparison between two chart data sources (wko5 / coros /
tp), built on the same pairing as backend/scripts/compare_sources.py
(start time within ±2 min, nearest first).
"""
from __future__ import annotations

import datetime as dt
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.datasource import source_stamp
from backend.engine.wko5expr.fitdataset import dataset_for_source
from backend.scripts.compare_sources import TOLERANCE, Act, pair

FIELDS = ("duration", "distance", "climbing", "np", "tss")
# how each side computed its TSS (Dataset._metrics): shown, not compared
BASIS = ("tss_source", "ftp_used", "ftp_source")
TOL = {"duration": TOLERANCE["duration_s"], "distance": TOLERANCE["distance_km"],
       "climbing": TOLERANCE["gain_m"], "np": TOLERANCE["np"], "tss": TOLERANCE["tss"]}


@lru_cache(maxsize=2)
def _ds(source: str, wko5_dir: str, cfg_json: str, stamp: str):
    import json
    return dataset_for_source(source, Path(wko5_dir), config=EngineConfig.from_dict(json.loads(cfg_json)))


def _acts(ds, source: str, since: Optional[dt.date]) -> list[Act]:
    out = []
    for w in ds.workouts:
        s = w.entry.start
        if s is None or (since and s.date() < since):
            continue
        a = Act(source, str(w.idx), s, w.sport_type, {f: w.metrics.get(f) for f in FIELDS + BASIS})
        out.append(a)
    return out


def _f(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else v


def compare(a: str, b: str, wko5_dir: Path, since: Optional[dt.date] = None,
            config: Optional[EngineConfig] = None) -> dict:
    import json
    cfg = config or EngineConfig.load()
    cj = json.dumps(cfg.to_dict(), sort_keys=True)
    da = _ds(a, str(wko5_dir), cj, source_stamp(a, wko5_dir))
    db = _ds(b, str(wko5_dir), cj, source_stamp(b, wko5_dir))
    pairs, only_a, only_b = pair(_acts(da, a, since), _acts(db, b, since))
    rows, agg = [], {f: [] for f in FIELDS}
    for x, y, d in sorted(pairs, key=lambda p: p[0].start_utc):
        cells, flags = {}, []
        for f in FIELDS:
            va, vb = _f(x.values.get(f)), _f(y.values.get(f))
            rel = None
            if va is not None and vb is not None:
                base = max(abs(va), abs(vb))
                rel = abs(vb - va) / base if base > 0 else 0.0
                agg[f].append(rel)
                if rel > TOL[f]:
                    flags.append(f)
            cells[f] = {"a": va, "b": vb, "rel": rel}
        rows.append({"start": x.start_utc.isoformat(timespec="minutes"), "sport": x.sport,
                     "dt_s": d, "metrics": cells, "flags": flags,
                     "tss_basis": {"a": {k: x.values.get(k) for k in BASIS},
                                   "b": {k: y.values.get(k) for k in BASIS}}})
    return {"a": a, "b": b, "since": since.isoformat() if since else None,
            "matched": len(rows), "only_a": len(only_a), "only_b": len(only_b),
            "only_a_starts": [x.start_utc.isoformat(timespec="minutes") for x in only_a[:50]],
            "only_b_starts": [y.start_utc.isoformat(timespec="minutes") for y in only_b[:50]],
            "mean_rel": {f: (sum(v) / len(v) if v else None) for f, v in agg.items()},
            "tolerance": TOL, "rows": rows}
