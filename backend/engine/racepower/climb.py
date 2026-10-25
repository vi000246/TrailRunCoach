"""
攀爬段 (SP-728): the stretches of a planned course where you may need hands as well as feet.

SP-716 (10/18 北插天山): on some steep ground the same grade is walked two ways — a normal pace, or a
scramble at a fraction of it (a descent of 125–400 m/h where the same −26 … −43 % elsewhere went at
800–1170 m/h). Whether a stretch is a scramble shows in the record afterwards (how fast you went up or
down there); before the race only the ground itself and your own earlier passes are known. So:

  label     on your earlier trail runs, every 50 m window steeper than ±15 % whose vertical speed is
            under 40 % of your median at that grade (5 % bins, up and down apart) AND that you spent more
            than half of below 1 km/h — a scramble (SP-716's definition, 推估)
  features  only what a plan has: the window's grade, the steepest 25 m step within ±100 / ±250 m, how
            much the grade swings within ±250 m, the grade over ±300 m (terrain shape), and whether your
            earlier runs over the same spot (≤ 30 m, same direction) were scrambles there (位置記憶)
  model     a logistic regression on your own windows (standardised, L2), checked by K-fold
            cross-validation grouped by activity (a run never predicts itself, nor its own place memory)
  marks     the plan's 50 m windows with a probability at or above your top 5 % (TOP_PCT) and at least
            MIN_LIFT × the overall share of scrambles, joined into stretches (gaps of one window bridged,
            ≥ MIN_MARK_M kept) — drawn on the elevation chart

The ? says how often the most suspicious 5 % of windows really were scrambles in the cross-validation,
against the share among all windows. Nothing changes the times (the 地形難度 multiplier and the place
memory already do). No marks without enough of your own scrambles (MIN_ACTS / MIN_POS) or when the
cross-validation does not separate them (MIN_AUC). OSM labels (SP-727's dictionary) are not in yet.
Everything here is 推估.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

from backend.engine.algorithms import route_match as RM

STEP_M = 25.0                  # the tracks every 25 m (memory.compact)
WIN = 2                        # a window: 2 steps = 50 m
STEEP = 0.15                   # 推估: only windows steeper than ±15 % can be a scramble
GRADE_BIN = 0.05
SLOW_FRAC = 0.40               # SP-716: vertical speed under 40 % of the grade's median …
SLOW_SHARE = 0.50              # … and more than half of the time below 1 km/h
GAP_S = 30.0                   # a recording gap longer than this is a watch auto-pause, not time spent
MEM_M = 30.0                   # the same spot (route_match.TOL_M)
MEM_DEG = 60.0                 # … in the same direction (bearings within this)
TOP_PCT = 5.0                  # the marks: your top 5 % windows (SP-716 reported its precision there)
MIN_MARK_M = 100.0             # 推估: a stretch shorter than this is not drawn
MIN_LIFT = 2.0                 # 推估: a marked window is at least twice as likely a scramble as an average one
MIN_ACTS = 5                   # 推估: runs with a track
MIN_POS = 20                   # 推估: scramble windows in them
MIN_AUC = 0.65                 # 推估: the cross-validation has to separate them at least this well
FOLDS = 5
L2 = 1.0
FEATURES = ("grade", "abs_grade", "down_steep", "max100", "max250", "swing250", "grade600", "abs_grade600",
            "mem_seen", "mem_rate")


# ---- one earlier run --------------------------------------------------------------------------

def compact(lat, lon, d, z, t, kmh, step_m: float = STEP_M) -> Optional[dict]:
    """One run every ≥ step_m along its distance: lat, lon, d (m), z (m), ce (cumulative time, s — a
    recording gap > GAP_S left out), cs (… of it below 1 km/h). None without ≥ 20 such points."""
    lat, lon, d, z, t, kmh = (np.asarray(x, float) for x in (lat, lon, d, z, t, kmh))
    n = min(len(lat), len(lon), len(d), len(z), len(t), len(kmh))
    if n < 20:
        return None
    lat, lon, d, z, t, kmh = lat[:n], lon[:n], d[:n], z[:n], t[:n], kmh[:n]
    dt = np.diff(t, prepend=t[0])
    dt[~np.isfinite(dt) | (dt < 0) | (dt > GAP_S)] = 0.0
    ce = np.cumsum(dt)
    cs = np.cumsum(np.where(np.nan_to_num(kmh) <= 1.0, dt, 0.0))
    ok = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 0.01) & (np.abs(lon) > 0.01) & np.isfinite(z) & np.isfinite(d)
    idx = np.nonzero(ok)[0]
    if len(idx) < 20:
        return None
    keep, last = [], None
    for i in idx:
        if last is None or d[i] - last >= step_m:
            keep.append(int(i))
            last = d[i]
    if keep[-1] != idx[-1]:
        keep.append(int(idx[-1]))
    if len(keep) < 20:
        return None
    k = np.asarray(keep)
    return {"lat": [round(float(x), 6) for x in lat[k]], "lon": [round(float(x), 6) for x in lon[k]],
            "d": [round(float(x), 1) for x in d[k]], "z": [round(float(x), 1) for x in z[k]],
            "ce": [round(float(x), 1) for x in ce[k]], "cs": [round(float(x), 1) for x in cs[k]]}


# ---- the terrain features of a line (an earlier run or the plan) -------------------------------

def _windows(d: np.ndarray, z: np.ndarray) -> dict:
    """The 50 m windows of a line given every ~25 m: start / end point index and the terrain features."""
    d, z = np.asarray(d, float), np.asarray(z, float)
    n = len(d)
    dd = np.maximum(np.diff(d), 1.0)
    gs = np.diff(z) / dd                                     # each step's grade
    starts = np.arange(0, n - WIN, WIN)
    ends = starts + WIN
    g = (z[ends] - z[starts]) / np.maximum(d[ends] - d[starts], 1.0)
    rows = []
    for a, b, gw in zip(starts, ends, g):
        c = (a + b) // 2
        w100 = gs[max(0, c - 4):min(len(gs), c + 4)]
        w250 = gs[max(0, c - 10):min(len(gs), c + 10)]
        lo, hi = max(0, c - 12), min(n - 1, c + 12)
        g600 = (z[hi] - z[lo]) / max(d[hi] - d[lo], 1.0)
        rows.append([gw, abs(gw), -gw if gw < 0 else 0.0, float(np.max(np.abs(w100))) if len(w100) else abs(gw),
                     float(np.max(np.abs(w250))) if len(w250) else abs(gw), float(np.std(w250)) if len(w250) > 1 else 0.0,
                     g600, abs(g600)])
    X = np.asarray(rows, float).reshape(-1, 8)
    return {"a": starts, "b": ends, "X": X, "grade": g}


def _bearing(lat0, lon0, lat1, lon1) -> float:
    y = math.sin(math.radians(lon1 - lon0)) * math.cos(math.radians(lat1))
    x = math.cos(math.radians(lat0)) * math.sin(math.radians(lat1)) - \
        math.sin(math.radians(lat0)) * math.cos(math.radians(lat1)) * math.cos(math.radians(lon1 - lon0))
    return math.degrees(math.atan2(y, x)) % 360.0


def _spots(lat, lon, a, b) -> np.ndarray:
    """Each window's middle (lat, lon) and bearing."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    out = []
    for i, j in zip(a, b):
        c = (i + j) // 2
        out.append((float(lat[c]), float(lon[c]), _bearing(lat[i], lon[i], lat[j], lon[j])))
    return np.asarray(out, float).reshape(-1, 3)


def _bin(g: float) -> int:
    return int(math.floor(g / GRADE_BIN))


def history(tracks: dict) -> Optional[dict]:
    """Your earlier runs' windows: X (terrain features), y (scramble), act (which run), spot (lat, lon,
    bearing). None without any steep window."""
    Xs, vr, slow, acts, spots, steep = [], [], [], [], [], []
    for idx, tr in (tracks or {}).items():
        if not tr or len(tr.get("d") or []) < 20:
            continue
        w = _windows(tr["d"], tr["z"])
        if not len(w["a"]):
            continue
        ce, cs, z = np.asarray(tr["ce"], float), np.asarray(tr["cs"], float), np.asarray(tr["z"], float)
        el = ce[w["b"]] - ce[w["a"]]
        sl = cs[w["b"]] - cs[w["a"]]
        dz = np.abs(z[w["b"]] - z[w["a"]])
        vr.append(np.where(el > 0, dz / np.maximum(el, 1e-9) * 3600.0, np.nan))
        slow.append(np.where(el > 0, sl / np.maximum(el, 1e-9), 0.0))
        steep.append(np.abs(w["grade"]) >= STEEP)
        Xs.append(w["X"])
        acts.append(np.full(len(w["a"]), idx, dtype=object))
        spots.append(_spots(tr["lat"], tr["lon"], w["a"], w["b"]))
    if not Xs:
        return None
    X, vr, slow, steep = np.vstack(Xs), np.concatenate(vr), np.concatenate(slow), np.concatenate(steep)
    act, spot = np.concatenate(acts), np.vstack(spots)
    # your median vertical speed in each grade bin (up and down apart), the steep windows only
    bins = np.array([_bin(g) for g in X[:, 0]])
    med = {}
    for k in np.unique(bins[steep]):
        v = vr[steep & (bins == k) & np.isfinite(vr)]
        if len(v) >= 5:
            med[int(k)] = float(np.median(v))
    ref = np.array([med.get(int(k), np.nan) for k in bins])
    y = steep & np.isfinite(vr) & np.isfinite(ref) & (vr < SLOW_FRAC * ref) & (slow > SLOW_SHARE)
    return {"X": X, "y": y.astype(float), "act": act, "spot": spot}


# ---- the same spot on other runs (位置記憶) -------------------------------------------------------

def _grid(spot: np.ndarray) -> dict:
    g = {}
    for i, (la, lo, _b) in enumerate(spot):
        g.setdefault((int(la / 0.0003), int(lo / 0.0003)), []).append(i)
    return g


def _near(spot_q: np.ndarray, spot_h: np.ndarray, grid: dict) -> tuple[np.ndarray, np.ndarray, int]:
    """The (query window, history window) pairs within MEM_M of each other in the same direction, and
    how many query windows there are."""
    qs, hs = [], []
    for q, (la, lo, br) in enumerate(spot_q):
        ci, cj = int(la / 0.0003), int(lo / 0.0003)
        cand = [i for di in (-1, 0, 1) for dj in (-1, 0, 1) for i in grid.get((ci + di, cj + dj), ())]
        if not cand:
            continue
        c = np.asarray(cand)
        h = spot_h[c]
        dy = (h[:, 0] - la) * 111_195.0
        dx = (h[:, 1] - lo) * 111_195.0 * math.cos(math.radians(la))
        db = np.abs((h[:, 2] - br + 180.0) % 360.0 - 180.0)
        c = c[(np.hypot(dx, dy) <= MEM_M) & (db <= MEM_DEG)]
        if len(c):
            qs.append(np.full(len(c), q))
            hs.append(c)
    if not qs:
        return np.zeros(0, int), np.zeros(0, int), len(spot_q)
    return np.concatenate(qs), np.concatenate(hs), len(spot_q)


def _mem(near: tuple, y: np.ndarray, code: np.ndarray, allowed: np.ndarray, own=None) -> np.ndarray:
    """[seen, rate] per query window: whether some allowed run passed the spot, and how often it was a
    scramble there (each run counted once: a scramble if any of its windows there was one). `code`: each
    history window's run as 0…n−1, `allowed`: a bool per run, `own`: each query window's own run (never
    its own memory)."""
    qi, hi, nq = near
    out = np.zeros((nq, 2))
    if not len(qi):
        return out
    c = code[hi]
    keep = allowed[c] if own is None else allowed[c] & (c != np.asarray(own)[qi])
    qi, c, v = qi[keep], c[keep], y[hi][keep] > 0
    if not len(qi):
        return out
    R = int(code.max()) + 1
    uk, inv = np.unique(qi.astype(np.int64) * R + c, return_inverse=True)
    hit = np.zeros(len(uk))
    np.maximum.at(hit, inv, v.astype(float))
    uq = (uk // R).astype(int)
    runs = np.bincount(uq, minlength=nq)
    hits = np.bincount(uq, weights=hit, minlength=nq)
    seen = runs > 0
    out[seen, 0] = 1.0
    out[seen, 1] = hits[seen] / runs[seen]
    return out


# ---- the model ----------------------------------------------------------------------------------

def _fit(X: np.ndarray, y: np.ndarray) -> dict:
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.zeros(Z.shape[1])
    R = np.eye(Z.shape[1]) * L2
    R[0, 0] = 0.0
    for _ in range(30):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))
        g = Z.T @ (p - y) + R @ w
        H = (Z * (p * (1 - p))[:, None]).T @ Z + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-7:
            break
    return {"w": w, "mu": mu, "sd": sd}


def _prob(m: dict, X: np.ndarray) -> np.ndarray:
    Z = np.column_stack([np.ones(len(X)), (X - m["mu"]) / m["sd"]])
    return 1.0 / (1.0 + np.exp(-np.clip(Z @ m["w"], -30, 30)))


def auc(p: np.ndarray, y: np.ndarray) -> Optional[float]:
    """Mann–Whitney AUC (ties count half); None with one class only."""
    y = np.asarray(y, bool)
    npos, nneg = int(y.sum()), int((~y).sum())
    if not npos or not nneg:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    ps = np.asarray(p)[order]
    i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y].sum() - npos * (npos + 1) / 2.0) / (npos * nneg))


def top_share(p: np.ndarray, y: np.ndarray, pct: float = TOP_PCT) -> Optional[float]:
    """How many of the top pct % windows by p really were scrambles."""
    if not len(p):
        return None
    k = max(1, int(round(len(p) * pct / 100.0)))
    top = np.argsort(-np.asarray(p), kind="mergesort")[:k]
    return float(np.mean(np.asarray(y)[top]))


def fit(tracks: dict) -> dict:
    """Your scramble model and its grouped cross-validation: {usable, reason, cv: {auc, top_share,
    base_share, top_pct, n_acts, n_pos, n_windows}, model, thr, hist}. reason: few (not enough runs or
    scrambles) | weak (the cross-validation does not separate them)."""
    h = history(tracks)
    if h is None:
        return {"usable": False, "reason": "few", "cv": {"n_acts": 0, "n_pos": 0, "n_windows": 0}}
    X, y, act = h["X"], h["y"], h["act"]
    acts = list(dict.fromkeys(act.tolist()))
    pos = {a: i for i, a in enumerate(acts)}
    code = np.array([pos[a] for a in act], dtype=int)
    h["code"] = code
    pos_acts = set(code[y > 0].tolist())
    cv = {"n_acts": len(acts), "n_pos": int(y.sum()), "n_windows": int(len(y)), "top_pct": TOP_PCT,
          "base_share": float(y.mean()) if len(y) else None}
    if len(acts) < MIN_ACTS or y.sum() < MIN_POS or len(pos_acts) < 2:
        return {"usable": False, "reason": "few", "cv": cv}
    near = _near(h["spot"], h["spot"], _grid(h["spot"]))
    # K folds by run, each run's windows in one fold; the place memory of a test fold only from the others
    k = min(FOLDS, len(acts))
    run_fold = np.arange(len(acts)) % k
    fold = run_fold[code]
    oof = np.zeros(len(y))
    for f in range(k):
        te, tr = fold == f, fold != f
        mem = _mem(near, y, code, run_fold != f, code)       # the test fold's runs never as memory
        mem_tr, mem_te = mem[tr], mem[te]
        if y[tr].sum() == 0:
            oof[te] = y[tr].mean()
            continue
        m = _fit(np.hstack([X[tr], mem_tr]), y[tr])
        oof[te] = _prob(m, np.hstack([X[te], mem_te]))
    cv.update(auc=auc(oof, y), top_share=top_share(oof, y))
    mem_all = _mem(near, y, code, np.ones(len(acts), bool), code)
    m = _fit(np.hstack([X, mem_all]), y)
    p_in = _prob(m, np.hstack([X, mem_all]))
    # your top TOP_PCT % — and never a window the model rates under twice the share of scrambles overall
    # (推估: when scrambles are rare the top 5 % holds windows the model itself calls near-impossible)
    thr = max(float(np.percentile(p_in, 100.0 - TOP_PCT)), MIN_LIFT * float(y.mean()))
    ok = cv["auc"] is not None and cv["auc"] >= MIN_AUC
    return {"usable": ok, "reason": None if ok else "weak", "cv": cv, "model": m, "thr": thr, "hist": h}


# ---- the plan -----------------------------------------------------------------------------------

def plan_line(profile: dict, step_m: float = STEP_M) -> Optional[dict]:
    """The course every step_m along its coordinates: lat, lon, d (m), z, km. None without coordinates."""
    lat, lon, km, z = (profile or {}).get("lat"), (profile or {}).get("lon"), (profile or {}).get("km"), (profile or {}).get("z")
    if not lat or not lon or not km or not z or len(lat) < 2 or not (len(lat) == len(lon) == len(km) == len(z)):
        return None
    lat0, lon0 = float(lat[0]), float(lon[0])
    s = RM.path_length(RM.project(lat, lon, lat0, lon0))
    if s[-1] <= 2 * step_m * WIN:
        return None
    se = np.append(np.arange(0.0, s[-1], step_m), s[-1])
    return {"lat": np.interp(se, s, np.asarray(lat, float)), "lon": np.interp(se, s, np.asarray(lon, float)),
            "d": se, "z": np.interp(se, s, np.asarray(z, float)), "km": np.interp(se, s, np.asarray(km, float))}


def marks(fitted: Optional[dict], profile: dict) -> Optional[dict]:
    """The plan's 攀爬段: {marks: [{start_km, end_km, p}], cv, usable, reason}; None when there is no
    model at all (no runs with a track) or the course has no coordinates."""
    if not fitted:
        return None
    out = {"marks": [], "cv": dict(fitted.get("cv") or {}), "usable": bool(fitted.get("usable")),
           "reason": fitted.get("reason"), "badge": "推估"}
    if not fitted.get("usable"):
        return out
    line = plan_line(profile)
    if line is None:
        return None
    w = _windows(line["d"], line["z"])
    if not len(w["a"]):
        return out
    h = fitted["hist"]
    spot = _spots(line["lat"], line["lon"], w["a"], w["b"])
    mem = _mem(_near(spot, h["spot"], _grid(h["spot"])), h["y"], h["code"], np.ones(int(h["code"].max()) + 1, bool))
    p = _prob(fitted["model"], np.hstack([w["X"], mem]))
    hit = p >= fitted["thr"]
    km = line["km"]
    spans, i, n = [], 0, len(hit)
    while i < n:
        if not hit[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (hit[j + 1] or (j + 2 < n and hit[j + 2])):
            j += 1 if hit[j + 1] else 2
        k0, k1 = float(km[w["a"][i]]), float(km[w["b"][j]])
        if (k1 - k0) * 1000.0 >= MIN_MARK_M:
            spans.append({"start_km": round(k0, 3), "end_km": round(k1, 3), "p": round(float(p[i:j + 1].max()), 3)})
        i = j + 1
    out["marks"] = spans
    return out


def summary(c: Optional[dict]) -> Optional[dict]:
    """What a share snapshot keeps: the marks and the cross-validation's shares (not how many runs)."""
    if not c or not c.get("marks"):
        return None
    cv = c.get("cv") or {}
    return {"marks": c["marks"], "cv": {k: cv.get(k) for k in ("auc", "top_share", "base_share", "top_pct")},
            "badge": c.get("badge")}


def mark_list(c: Optional[dict]) -> Sequence[dict]:
    return (c or {}).get("marks") or []
