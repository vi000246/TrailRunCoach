"""
LTHR and AeT estimated from the athlete's own runs (see
backend/engine/algorithms/threshold_estimate.py for the methods).

Per-run numbers are memoised on disk per .wko4 (keyed by file stamp and the
thresholds in effect, via Dataset.cached_series), so a re-estimate only reads
new runs. The estimate is a suggestion: applying it is a separate, explicit
step on the season-plan page.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict
from typing import Optional

from backend.engine.algorithms.threshold_estimate import (
    DriftPoint, RunThreshold, estimate_aet, estimate_lthr, run_threshold, steady_drift,
)
from backend.engine.wko5expr.dataset import Dataset, date_to_day

WINDOWS = (90, 180)          # try 90 days, widen to 180 if too few runs
_KEY = "thresholds_v1"


_KEY_ASOF = "thresholds_asof_v1"   # per-run values computed with an explicit (as-of) CP


def _per_run(ds: Dataset, w, cp: Optional[float] = None, use_ds_cp: bool = True) -> Optional[dict]:
    t = ds.channel(w.idx, "elapsedtime")
    hr = ds.channel(w.idx, "heartrate")
    pw = ds.channel(w.idx, "power")
    if t is None or hr is None or pw is None:
        return None
    cp = ds.cp(w) if use_ds_cp else cp
    t, hr, pw = list(t), list(hr), list(pw)
    rt = run_threshold(t, hr, pw, cp)
    dp = steady_drift(t, hr, pw, cp) if "runningtrail" not in w.tags else None
    return {"lt": asdict(rt), "dp": None if dp is None else asdict(dp), "cp": cp}


def _nan_free(x):
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: _nan_free(v) for k, v in x.items()}
    return x


def estimate(ds: Dataset, today: Optional[dt.date] = None, cp_of=None) -> dict:
    """`cp_of(day) -> CP or None`: the CP in effect on a date, for a back-test
    that must not see later values (racepower.athlete.cp_as_of). Each run is
    then measured against the CP of its own date and the estimate against the
    CP of `today`. Default: Dataset.cp (the plan test on or before the run's
    date, else WKO5's current mFTP snapshot)."""
    today = today or dt.date.today()
    tday = int(math.floor(date_to_day(today)))
    runs = [w for w in ds.workouts if w.sport == "run" and tday - WINDOWS[-1] < math.floor(w.day) <= tday]
    data = {}
    for w in runs:
        if cp_of is None:
            v = ds.cached_series(_KEY, w, lambda w=w: _nan_free(_per_run(ds, w)))
        else:
            cp_w = cp_of(w.entry.start.date())
            mk = (_KEY_ASOF, w.idx, cp_w)          # in-memory: many as-of dates share a run's CP
            memo = getattr(ds, "memo", None)
            if isinstance(memo, dict) and mk in memo:
                v = memo[mk]
            else:
                v = ds.cached_series(_KEY_ASOF, w, lambda w=w, c=cp_w: _nan_free(_per_run(ds, w, c, False)))
                if v is not None and v.get("cp") != cp_w:  # cached with another CP: recompute, don't store
                    v = _nan_free(_per_run(ds, w, cp_w, False))
                if isinstance(memo, dict):
                    memo[mk] = v
        if v:
            data[w.idx] = v
    ds.flush_series()
    if cp_of is None:
        # the CP in effect on `today` (a plan test dated today counts even when
        # the last WKO5 run is older), else the last run's
        plan = getattr(ds, "plan", None)
        cp_now = plan.threshold_on("cp", today) if plan is not None else None
        if cp_now is None:
            cp_now = ds.cp(runs[-1]) if runs else None
    else:
        cp_now = cp_of(today)

    def pick(days):
        ws = [w for w in runs if math.floor(w.day) > tday - days and w.idx in data]
        lts = [RunThreshold(**data[w.idx]["lt"]) for w in ws]
        dps = [DriftPoint(**data[w.idx]["dp"]) for w in ws if data[w.idx]["dp"]]
        return ws, lts, dps

    out = {"cp": cp_now, "today": today.isoformat()}
    for days in WINDOWS:
        ws, lts, dps = pick(days)
        lt = estimate_lthr(lts, cp_now) if cp_now else None
        if lt and lt.value is not None or days == WINDOWS[-1]:
            out["lthr"] = None if lt is None else {**asdict(lt), "days": days}
            break
    lthr_v = (out.get("lthr") or {}).get("value")
    for days in WINDOWS:
        ws, lts, dps = pick(days)
        ae = estimate_aet(dps, lthr=lthr_v)
        if ae.value is not None or days == WINDOWS[-1]:
            out["aethr"] = {**asdict(ae), "days": days,
                            "points": [[round(p.hr1, 1), round(p.drift, 4)] for p in dps]}
            break
    return out
