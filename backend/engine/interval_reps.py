"""
Finding the reps of an interval session (docs/research/interval-prescription.md
§B3 「找趟的方法」, §A5.2-2).

Order:
  1. laps: a pushed COROS workout writes one lap per timed step
     (sync/coros_workouts.py). The laps whose duration is the planned rep's
     (±5 s, or ±3 % on long reps) and whose average power is near the band are
     the reps, in order (interval-adaptation.md §4.2).
  2. no laps: 10-s power ≥ REP_TOL × the planned lower bound × CP for at least
     MIN_SHARE of the planned rep — not detect_efforts' session median, which
     sits above a 92 % CP rep when the reps are half the session (bug b).
  3. no plan: count_reps (≥ 95 % CP, ≥ 40 s) for short reps, else the Zone 3
     floor (0.95 × 88 % CP) for reps ≥ Z3_MIN_S.

auto_reps (SP-519, display only): the set of ≥ 3 similar efforts at ≥ 95 % CP
with rests between that lets an easy / long run's 間歇 page show itself; efforts
starting in the run's first 10 minutes do not count (SP-519 R1).

Laps come from the activity's FIT: `ds.laps(idx)` when the dataset offers it
(tests), else the FIT blob WKO5 keeps inside the .wko4 (`original_bytes`), else
a .fit path. Lap start = lap.start_time − session.start_time (the elapsedtime
origin in files/fit_to_channels.py).
"""
from __future__ import annotations

import io
import math
from typing import Optional

import numpy as np

REP_TOL = 0.95          # doc §B3: threshold = 0.95 × the planned lower bound (推估)
MIN_SHARE = 0.6         # 推估: a detected bout counts as a rep when ≥ 60 % of the planned length
LAP_TOL_S = 5.0         # interval-adaptation.md §4.2: duration ±5 s
LAP_TOL_FRAC = 0.03     # 推估: ±3 % on reps longer than ~3 min (a late lap press)
LAP_POWER = 0.85        # 推估: a matched lap's average ≥ 85 % of the planned lower bound
Z3_FLOOR = 0.88         # the lowest Zone 3 band (interval-prescription.md §C2 Z3sub)
Z3_MIN_S = 150          # 推估: an unplanned Zone 3 rep is ≥ 2.5 min (the library's are ≥ 3 min)
BRIDGE_S = 5            # gaps < 5 s inside a rep are bridged (count_reps' rule)


def _grid(t, x):
    from backend.engine.workout_review import _grid1
    return _grid1(t, x)


# ---------------------------------------------------------------------------
# laps
# ---------------------------------------------------------------------------

def _fit_laps(raw: bytes) -> list[dict]:
    import gzip
    import fitdecode
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    import warnings
    laps, start = [], None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # COROS writes odd field sizes; fitdecode warns on each
        with fitdecode.FitReader(io.BytesIO(raw), error_handling=fitdecode.ErrorHandling.IGNORE) as fr:
            for fm in fr:
                if not isinstance(fm, fitdecode.FitDataMessage):
                    continue
                if fm.name == "lap":
                    laps.append({f.name: f.value for f in fm.fields})
                elif fm.name == "session" and start is None:
                    start = next((f.value for f in fm.fields if f.name == "start_time"), None)
    if not laps:
        return []
    t0 = start or laps[0].get("start_time")
    out = []
    for lp in laps:
        st, dur = lp.get("start_time"), lp.get("total_timer_time") or lp.get("total_elapsed_time")
        if st is None or not dur or t0 is None:
            continue
        try:
            a = (st - t0).total_seconds()
        except TypeError:
            continue
        out.append({"start_s": float(a), "duration_s": float(dur),
                    "power": float(lp["avg_power"]) if lp.get("avg_power") is not None else None})
    return out


def laps_of(ds, w) -> list[dict]:
    """[{"start_s", "duration_s", "power"}] of the activity's laps; [] without any.
    Memoised in ds.memo."""
    memo = getattr(ds, "memo", None)
    key = ("interval_laps", w.idx)
    if isinstance(memo, dict) and key in memo:
        return memo[key]
    out: list[dict] = []
    try:
        fn = getattr(ds, "laps", None)
        if callable(fn):
            out = list(fn(w.idx) or [])
        else:
            f = ds.wko4(w.idx) if hasattr(ds, "wko4") else None
            raw = None
            if f is not None and getattr(f, "original_bytes", None) and (f.original_type or "").lower() == "fit":
                raw = f.original_bytes
            elif f is not None and str(getattr(f, "path", "")).lower().endswith((".fit", ".fit.gz")):
                with open(f.path, "rb") as fh:
                    raw = fh.read()
            out = _fit_laps(raw) if raw else []
    except Exception:                       # noqa: BLE001 — no laps, use the power pattern
        out = []
    if isinstance(memo, dict):
        memo[key] = out
    return out


REST_TOL_S = 10.0       # 推估: the planned rest between two matched work laps, ±10 s …
REST_TOL_FRAC = 0.15    # … or ±15 % — 1-km auto laps (back to back, ≈ the rep length) don't chain


def lap_bouts(laps: list[dict], works: list[int], lo: Optional[float], cp: Optional[float],
              rest_s: Optional[float] = None) -> list[dict]:
    """The laps matching the planned work durations `works` (seconds, in order).
    With `rest_s`, consecutive reps must be the planned rest apart (a pushed
    workout's rest step is its own lap); the longest such chain wins."""
    floor = LAP_POWER * lo * cp if lo and cp else None

    def fits(lp, want) -> bool:
        if abs(lp["duration_s"] - want) > max(LAP_TOL_S, LAP_TOL_FRAC * want):
            return False
        return floor is None or lp.get("power") is None or lp["power"] >= floor

    def gap_ok(prev, lp) -> bool:
        if not rest_s:
            return True
        gap = lp["start_s"] - (prev["start_s"] + prev["duration_s"])
        return abs(gap - rest_s) <= max(REST_TOL_S, REST_TOL_FRAC * rest_s)

    best: list[dict] = []
    for i, first in enumerate(laps):
        if not works or not fits(first, works[0]):
            continue
        chain = [first]
        for lp in laps[i + 1:]:
            if len(chain) >= len(works):
                break
            if fits(lp, works[len(chain)]) and gap_ok(chain[-1], lp):
                chain.append(lp)
        if len(chain) > len(best):
            best = chain
        if len(best) == len(works):
            break
    if len(works) > 1 and len(best) < 2 and rest_s:
        return []                            # one lap of the right length is not a set of reps
    return [{"start_s": lp["start_s"], "duration_s": lp["duration_s"], "power": lp.get("power"), "source": "lap"}
            for lp in best]


# ---------------------------------------------------------------------------
# the power pattern
# ---------------------------------------------------------------------------

def band_bouts(t, power, cp: Optional[float], lo: float, min_s: float) -> list[dict]:
    """Bouts of 10-s power ≥ REP_TOL × lo × CP lasting ≥ min_s (gaps < 5 s bridged)."""
    if power is None or not cp:
        return []
    grid, p = _grid(t, power)
    if grid is None or len(p) < 60:
        return []
    pz = np.nan_to_num(p)
    p10 = np.convolve(pz, np.ones(10) / 10, "same")
    on = p10 >= REP_TOL * lo * cp
    edges = np.diff(np.concatenate([[0], on.astype(int), [0]]))
    segs: list[list[int]] = []
    for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
        if segs and a - segs[-1][1] < BRIDGE_S:
            segs[-1][1] = int(b)
        else:
            segs.append([int(a), int(b)])
    return [{"start_s": float(a), "duration_s": float(b - a), "power": float(pz[a:b].mean()), "source": "power"}
            for a, b in segs if b - a >= min_s]


def works_of(spec) -> list[int]:
    """Planned work seconds per rep of a ladder tuple or a library variant."""
    if spec is None:
        return []
    pat = getattr(spec, "pattern", None)
    if pat:
        return [int(x) for x in pat]
    if hasattr(spec, "work_s"):
        return [int(spec.work_s)] * int(spec.reps)
    reps, work = int(spec[2]), spec[3]
    return [int(round(float(work) * 60))] * reps


def rest_of(spec) -> Optional[float]:
    if spec is None:
        return None
    if hasattr(spec, "rest_s"):
        return float(spec.rest_s)
    return float(spec[4]) * 60.0


def lo_of(spec) -> Optional[float]:
    if spec is None:
        return None
    return getattr(spec, "lo", None) if hasattr(spec, "lo") else spec[5]


def find_reps(ds, w, s: Optional[dict], cp: Optional[float], spec=None) -> dict:
    """{"bouts": [...], "source": "lap" | "power" | "short" | "z3" | None}. `s` =
    workout_review._samples(ds, w); `spec` = the planned ladder tuple / variant."""
    t = s["t"] if s is not None else None
    pw = s["power"] if s is not None else None
    works, lo = works_of(spec), lo_of(spec)
    if works:
        lb = lap_bouts(laps_of(ds, w), works, lo, cp, rest_of(spec))
        if lb:
            for b in lb:                      # the lap's mean from the samples when there is power
                if pw is not None and t is not None:
                    grid, p = _grid(t, pw)
                    if grid is not None:
                        a = int(round(b["start_s"]))
                        e = int(round(b["start_s"] + b["duration_s"]))
                        seg = np.nan_to_num(p[max(0, a):max(0, e)])
                        if len(seg):
                            b["power"] = float(seg.mean())
            return {"bouts": lb, "source": "lap"}
        if lo and cp and pw is not None:
            need = MIN_SHARE * min(works)
            pb = band_bouts(t, pw, cp, lo, need)
            if pb:
                return {"bouts": pb, "source": "power"}
        return {"bouts": [], "source": None}
    if pw is None or not cp:
        return {"bouts": [], "source": None}
    from backend.engine.quality_gate import DOSE_MIN_REPS, count_reps
    short = count_reps(t, pw, cp)
    if len(short) >= DOSE_MIN_REPS:
        return {"bouts": [{**r, "source": "short"} for r in short], "source": "short"}
    z3 = band_bouts(t, pw, cp, Z3_FLOOR, Z3_MIN_S)
    if len(z3) >= 2:
        return {"bouts": [{**r, "source": "z3"} for r in z3], "source": "z3"}
    return {"bouts": [{**r, "source": "short"} for r in short], "source": "short" if short else None}



# ---------------------------------------------------------------------------
# SP-519: an easy / long run with a set of efforts — the 間歇 page shows itself
# ---------------------------------------------------------------------------
# The user (2026-10-14): 「偵測到 3 趟以上、長度差不多、強度到閾值以上的用力段，就直接顯示間歇圖表」,
# trail climbs included (no grade filter: a climb at the intensity counts). Display only —
# find_reps / count_reps (dose_history, the ladder, the hard-day count) are not touched.
# 「一組」: the reps follow each other with an easier stretch between (a jog, a walk, the way back
# down) — the owner's runs (2026-07..10) sit at 85–100 % of the fitted CP with surges ≥ 95 % CP
# every few minutes; without the rest rule 30 of 46 runs would have shown the 間歇 page.
AUTO_MIN_REPS = 3          # the user's rule
AUTO_PCT = 0.95            # 推估: 強度到閾值以上 = a rep's mean ≥ 95 % CP (CP = the threshold power; the
                           #   count_reps / 用力段 line, so a 98–101 % CP fartlek rep with a soft 10 s still counts)
AUTO_LEN_RATIO = 1.5       # 推估: 長度差不多 = the longest rep of the set ≤ 1.5 × the shortest (detected edges
                           #   move ±5–10 s; a 2-3-2 pyramid is one set, 1′ strides next to 5′ reps are not)
AUTO_REST_RATIO = 0.80     # 推估: a rest averages ≤ 80 % of the power of the reps on either side of it
AUTO_REST_X = 3.0          # 推估: a rest ≤ 3 × the longer rep next to it …
AUTO_REST_MIN_S = 180.0    # … or ≤ 3 min — longer, and the efforts are separate climbs / surges, not a set
AUTO_LAP_MIN_S = 30.0      # 推估: a lap shorter than this is a stride or a stray press, not a rep
AUTO_SKIP_START_S = 600.0  # SP-519 R1 (user 2026-10-14, 「排除開跑前 10 分」): an effort starting in the first
                           #   10 min (elapsed) is the start, a traffic light, HR still climbing — not a rep
                           #   (the owner's 9/2: three surges in minutes 1–6 showed the page); 推估


def similar_set(bouts: list[dict], ratio: float = AUTO_LEN_RATIO) -> list[dict]:
    """The largest set of bouts whose longest ≤ `ratio` × its shortest (ties: the more time
    in them), in time order."""
    by_len = sorted(bouts, key=lambda b: b["duration_s"])
    best: list[dict] = []
    for i, b in enumerate(by_len):
        grp = [x for x in by_len[i:] if x["duration_s"] <= ratio * b["duration_s"]]
        if (len(grp), sum(x["duration_s"] for x in grp)) > (len(best), sum(x["duration_s"] for x in best)):
            best = grp
    return sorted(best, key=lambda b: b["start_s"])


def _rest_ok(a: dict, b: dict, pg: np.ndarray) -> bool:
    """The stretch between bouts a and b is a rest: not longer than AUTO_REST_X × the longer of
    the two (≥ AUTO_REST_MIN_S allowed) and averaging ≤ AUTO_REST_RATIO × their power."""
    e0, s1 = int(round(a["start_s"] + a["duration_s"])), int(round(b["start_s"]))
    gap = s1 - e0
    if gap > max(AUTO_REST_MIN_S, AUTO_REST_X * max(a["duration_s"], b["duration_s"])):
        return False
    seg = pg[max(0, e0):max(0, s1)]
    if not len(seg):
        return False                          # back to back: one effort cut in two, not a rep and a rest
    return float(seg.mean()) <= AUTO_REST_RATIO * (a["power"] + b["power"]) / 2


def best_set(bouts: list[dict], pg: np.ndarray) -> list[dict]:
    """The largest set of similar-length bouts (similar_set) inside one run of bouts with a
    rest between each two (_rest_ok)."""
    chains: list[list[dict]] = []
    for b in sorted(bouts, key=lambda x: x["start_s"]):
        if chains and _rest_ok(chains[-1][-1], b, pg):
            chains[-1].append(b)
        else:
            chains.append([b])
    best: list[dict] = []
    for ch in chains:
        st = similar_set(ch)
        if (len(st), sum(x["duration_s"] for x in st)) > (len(best), sum(x["duration_s"] for x in best)):
            best = st
    return best


def _after_start(bouts: list[dict]) -> list[dict]:
    """The bouts that start AUTO_SKIP_START_S or later into the run."""
    return [b for b in bouts if b["start_s"] >= AUTO_SKIP_START_S]


def _auto_laps(ds, w, pg: np.ndarray, cp: float) -> list[dict]:
    """The laps run at ≥ AUTO_PCT × CP (their mean from the samples) and ≥ AUTO_LAP_MIN_S."""
    out = []
    for lp in laps_of(ds, w):
        a, e = int(round(lp["start_s"])), int(round(lp["start_s"] + lp["duration_s"]))
        seg = pg[max(0, a):max(0, e)]
        v = float(seg.mean()) if len(seg) else None
        if v is not None and v >= AUTO_PCT * cp and lp["duration_s"] >= AUTO_LAP_MIN_S:
            out.append({"start_s": lp["start_s"], "duration_s": lp["duration_s"], "power": v, "source": "auto_lap"})
    return out


def auto_reps(ds, w, s: Optional[dict], cp: Optional[float]) -> Optional[dict]:
    """SP-519: {"bouts", "source": "auto_lap" | "auto"} when the run has a set of
    ≥ AUTO_MIN_REPS efforts at ≥ AUTO_PCT × CP, about the same length, with a rest between
    each two (best_set) — the watch's laps first (a pushed or pressed rep is its own lap:
    exact edges, 30-s reps too), else the power pattern (count_reps: 10-s power ≥ 95 % CP
    for ≥ 40 s). Only efforts starting AUTO_SKIP_START_S or later count (R1). None otherwise,
    and without power or CP: HR lags a 1-min rep by most of it and pace on a climb says
    nothing of the effort, so a run without power keeps its one sentence."""
    if s is None or s.get("power") is None or not cp:
        return None
    t, pw = s["t"], s["power"]
    grid, p = _grid(t, pw)
    if grid is None or len(p) < 60:
        return None
    pg = np.nan_to_num(p)
    laps = best_set(_after_start(_auto_laps(ds, w, pg, cp)), pg)
    if len(laps) >= AUTO_MIN_REPS:
        return {"bouts": laps, "source": "auto_lap"}
    from backend.engine.quality_gate import count_reps
    found = best_set(_after_start([{**r, "source": "auto"} for r in count_reps(t, pw, cp)
                                   if r["power"] >= AUTO_PCT * cp]), pg)
    if len(found) >= AUTO_MIN_REPS:
        return {"bouts": found, "source": "auto"}
    return None
