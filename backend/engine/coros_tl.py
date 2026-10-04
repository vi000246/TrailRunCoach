"""
COROS Training Load (TL) ↔ the app's TSS (SP-37 / SP-38).

The 課表 editor's 「負荷」 end condition is entered as TSS (platform neutral); the COROS
provider sends it as TL. This module is the conversion, its per-athlete refit and the
closed-loop correction from load steps that were actually run.

Models (x = TSS, h = hours, IF = intensity factor), one per TSS source — SP-37's regression
on one runner's completed COROS activities (LOO error ≈ ±20 %). The defaults are only a
prior (推估): every athlete's own data pulls them toward his / her watch.

  power   TL = a·TSS^k                 a = 0.84, k = 1.12     (family C)
  hr      TL = h·(c0 + c1·IF + c2·IF²) c0 = −408, c1 = 1040, c2 = −505   (family D)
          the quadratic is only trusted inside the IF range it was fitted on: IF is clamped
          to HR_IF_RANGE (fitted: the observed 5th–95th percentile, never past the vertex
          when c2 < 0), and a per-hour TL ≤ 0 falls back to `linear`
  linear  TL = 1.37·TSS                (family A) — when neither applies (no IF)

Which one a step uses: a power-target step → power; otherwise → hr with the step's IF
(≈ % CP of its target, workout_steps.resolve), linear without one.

Per-athlete refit (`refit`, after each sync through engine/calibrate.calibrate): samples =
the athlete's synced COROS activities with the list response's `trainingLoad`
(workout_files.coros_training_load) joined to the app's TSS of the same activity.

  * recency: weight 0.5^(age / HALF_LIFE_DAYS), and only activities after the last big
    threshold change (FTP for power, LTHR for hr: > THRESHOLD_SHIFT) — the TSS scale moves
    with the threshold, the watch's TL does not
  * family: the lowest leave-one-out MAE among SMALL_FAMILIES (A, C) below SMALL_N samples,
    among A–D from SMALL_N on (E, zone minutes, needs the HR stream per zone: script only)
  * shrinkage: TL = w·individual + (1 − w)·default, w = n / (n + SHRINK_K) (the drift_agg.py
    BETA_K pattern) — blended in TL, which for the same linear family is the same as
    blending the parameters
  * time-ordered backtest: fit on everything before the last HOLDOUT_DAYS, predict those;
    the new fit replaces the stored one only when its out-of-sample MAE is not worse than
    the stored model's on the same holdout. Without a holdout (< BACKTEST_MIN_N) a new fit
    is accepted only over the defaults.

Closed loop (`LOAD_KEY`, like racepower/tss_calib.py): a pushed load step that was run
(the session done, its activity's laps = the pushed steps one to one) gives one sample
r = TSS accumulated in that step's lap ÷ the step's planned TSS; the correction factor
exp(w·mean ln r), w = n / (n + LOAD_K), divides the TSS before the conversion (r > 1: the
watch let more TSS pass than planned before reaching the TL, so send less TL). Samples
exist only for steps pushed with COROS's real load target (sync/coros_workouts
COROS_TARGET_TYPE_LOAD known), never for the estimated-time fallback.

The fitting core (A–E, LOO) is shared with backend/scripts/fit_tss_tl.py.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

KEY = "coros.tl_model"               # settings: the per-athlete fit (None = the defaults)
LOAD_KEY = "coros.tl_load_calib"      # settings: closed-loop samples of run load steps

# --- defaults (SP-37, one runner's data: a prior, 推估) -------------------------------
POWER_A, POWER_K = 0.84, 1.12
HR_C0, HR_C1, HR_C2 = -408.0, 1040.0, -505.0
HR_IF_RANGE = (0.60, 1.00)            # 推估: where the default quadratic is plausible (vertex ≈ 1.03)
LINEAR_A = 1.37
DEFAULT_ERR = 0.20                    # 推估: ± fraction shown with a default conversion (SP-37 LOO ≈ 20 %)

GROUPS = ("power", "hr", "linear")
GROUP_LABEL = {"power": "功率 TSS", "hr": "hrTSS（依強度）", "linear": "hrTSS（比例）"}
DEFAULTS = {"power": ("C", {"a": POWER_A, "k": POWER_K}),
            "hr": ("D", {"c0": HR_C0, "c1": HR_C1, "c2": HR_C2, "if_lo": HR_IF_RANGE[0], "if_hi": HR_IF_RANGE[1]}),
            "linear": ("A", {"a": LINEAR_A})}

# --- refit (all 推估) -----------------------------------------------------------------
SHRINK_K = 30                         # w = n / (n + 30): 30 activities = half personal
SMALL_N = 60                          # below: only the low-parameter families A, C
SMALL_FAMILIES = ("A", "C")
FAMILIES = ("A", "B", "C", "D")
HALF_LIFE_DAYS = 120.0
THRESHOLD_SHIFT = 0.05                # FTP / LTHR change > 5 %: older activities are dropped
HOLDOUT_DAYS = 30
BACKTEST_MIN_N = 3
MIN_MINUTES, MAX_HOURS = 10.0, 20.0   # as fit_tss_tl's filters (no multi-day trips)
IF_PCT = (5, 95)                      # the fitted D's IF clamp: observed percentiles

# --- closed loop (推估) ----------------------------------------------------------------
LOAD_K = 5                            # w = n / (n + 5)
RATIO_RANGE = (0.5, 2.0)
MAX_LOAD_SESSIONS = 100

# ---------------------------------------------------------------------------
# the fitting core (shared with backend/scripts/fit_tss_tl.py)
# ---------------------------------------------------------------------------

MODELS = ("A", "B", "C", "D", "E")
MIN_N = {"A": 3, "B": 4, "C": 4, "D": 5, "E": 8}
MODEL_TEXT = {"A": "TL = a·TSS", "B": "TL = a·TSS + b", "C": "TL = a·TSS^k",
              "D": "TL = h·(c0 + c1·IF + c2·IF²)", "E": "TL = Σ w_i·zone-minutes"}


def num(v) -> Optional[float]:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _nnls(A: np.ndarray, y: np.ndarray, iters: int = 20) -> np.ndarray:
    """Least squares with non-negative coefficients (drop the negative ones and refit)."""
    active = np.ones(A.shape[1], dtype=bool)
    w = np.zeros(A.shape[1])
    for _ in range(iters):
        if not active.any():
            break
        sol, *_ = np.linalg.lstsq(A[:, active], y, rcond=None)
        w[:] = 0.0
        w[active] = sol
        if (sol >= 0).all():
            break
        active[np.where(active)[0][sol < 0]] = False
    return w


def _wt(s: list[dict]) -> np.ndarray:
    """Sample weights (`wt`, recency) as √w row scales for least squares; 1 without."""
    return np.sqrt(np.array([max(0.0, float(d.get("wt", 1.0))) for d in s]))


def fit(model: str, s: list[dict]) -> Optional[dict]:
    """Fitted parameters, or None when the samples cannot carry the model. A sample's
    optional `wt` weights it (weighted least squares)."""
    if model == "A":
        x = np.array([d["x"] for d in s]); y = np.array([d["y"] for d in s]); w = _wt(s) ** 2
        sxx = float((w * x * x).sum())
        return None if sxx <= 0 else {"a": float((w * x * y).sum()) / sxx}
    if model == "B":
        x = np.array([d["x"] for d in s]); y = np.array([d["y"] for d in s])
        if len(x) < 2 or np.ptp(x) <= 0:
            return None
        a, b = np.polyfit(x, y, 1, w=_wt(s))
        return {"a": float(a), "b": float(b)}
    if model == "C":
        pts = [d for d in s if d["x"] > 0 and d["y"] > 0]
        if len(pts) < MIN_N["C"]:
            return None
        lx = np.log([d["x"] for d in pts]); ly = np.log([d["y"] for d in pts])
        if np.ptp(lx) <= 0:
            return None
        k, la = np.polyfit(lx, ly, 1, w=_wt(pts))
        return {"a": float(math.exp(la)), "k": float(k)}
    if model == "D":
        pts = [d for d in s if d.get("h") and d.get("if") is not None]
        if len(pts) < MIN_N["D"]:
            return None
        sw = _wt(pts)
        A = np.array([[d["h"], d["h"] * d["if"], d["h"] * d["if"] ** 2] for d in pts]) * sw[:, None]
        y = np.array([d["y"] for d in pts]) * sw
        if np.linalg.matrix_rank(A) < 3:
            return None
        c, *_ = np.linalg.lstsq(A, y, rcond=None)
        return {"c0": float(c[0]), "c1": float(c[1]), "c2": float(c[2])}
    if model == "E":
        pts = [d for d in s if d.get("zmin") is not None]
        if len(pts) < MIN_N["E"]:
            return None
        sw = _wt(pts)
        A = np.array([d["zmin"] for d in pts]) * sw[:, None]; y = np.array([d["y"] for d in pts]) * sw
        used = A.sum(axis=0) > 0
        if used.sum() == 0:
            return None
        w = np.zeros(6)
        w[used] = _nnls(A[:, used], y)
        return {f"w{i + 1}": float(v) for i, v in enumerate(w)}
    raise ValueError(model)


def predict(model: str, p: dict, d: dict) -> Optional[float]:
    if model == "A":
        return p["a"] * d["x"]
    if model == "B":
        return p["a"] * d["x"] + p["b"]
    if model == "C":
        return p["a"] * d["x"] ** p["k"] if d["x"] > 0 else None
    if model == "D":
        if not d.get("h") or d.get("if") is None:
            return None
        return d["h"] * (p["c0"] + p["c1"] * d["if"] + p["c2"] * d["if"] ** 2)
    if model == "E":
        if d.get("zmin") is None:
            return None
        return sum(p[f"w{i + 1}"] * m for i, m in enumerate(d["zmin"]))
    raise ValueError(model)


def errors(pairs: list[tuple[float, float]]) -> dict:
    """{n, mae, mape (y > 0 only), bias} of (prediction, truth) pairs."""
    if not pairs:
        return {"n": 0, "mae": None, "mape": None, "bias": None}
    e = [p - y for p, y in pairs]
    pct = [abs(p - y) / y for p, y in pairs if y > 0]
    return {"n": len(pairs), "mae": sum(abs(v) for v in e) / len(e),
            "mape": (sum(pct) / len(pct)) if pct else None, "bias": sum(e) / len(e)}


def loo(model: str, s: list[dict]) -> dict:
    """Leave-one-out: fit on all but one, predict that one."""
    pairs = []
    for i in range(len(s)):
        p = fit(model, s[:i] + s[i + 1:])
        if p is None:
            continue
        v = predict(model, p, s[i])
        if v is not None and math.isfinite(v):
            pairs.append((v, s[i]["y"]))
    return errors(pairs)


def predict_inputs_ok(model: str, d: dict) -> bool:
    if model == "C":
        return d["x"] > 0 and d["y"] > 0
    if model == "D":
        return bool(d.get("h")) and d.get("if") is not None
    if model == "E":
        return d.get("zmin") is not None
    return True


def evaluate(s: list[dict], models=MODELS) -> dict:
    """Per model: the full-sample parameters and the LOO error."""
    out = {}
    for m in models:
        usable = [d for d in s if predict_inputs_ok(m, d)]
        if len(usable) < MIN_N[m]:
            out[m] = {"n": len(usable), "params": None, "loo": None}
            continue
        out[m] = {"n": len(usable), "params": fit(m, usable), "loo": loo(m, usable)}
    return out


def best_model(res: dict) -> Optional[str]:
    """Lowest LOO MAE; a model only competes on ≥ 80 % of the group's largest n
    (so a model fitted on a small subset does not win by being easy)."""
    ok = {m: r for m, r in res.items() if r.get("loo") and r["loo"]["mae"] is not None}
    if not ok:
        return None
    nmax = max(r["loo"]["n"] for r in ok.values())
    ok = {m: r for m, r in ok.items() if r["loo"]["n"] >= 0.8 * nmax}
    return min(ok, key=lambda m: ok[m]["loo"]["mae"])


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _family_tl(fam: str, p: dict, tss: float, if_: Optional[float]) -> Optional[float]:
    """TL of `tss` by one family. D needs IF: hours = TSS / (100·IF²) (the TSS
    definition), the per-hour rate at IF clamped to the fitted range; a rate ≤ 0 → None."""
    if tss <= 0:
        return 0.0
    if fam == "D":
        if not if_ or if_ <= 0:
            return None
        h = tss / (100.0 * if_ * if_)
        lo, hi = p.get("if_lo", HR_IF_RANGE[0]), p.get("if_hi", HR_IF_RANGE[1])
        f = _clamp(if_, lo, hi)
        rate = p["c0"] + p["c1"] * f + p["c2"] * f * f
        return h * rate if rate > 0 and math.isfinite(rate) else None
    try:
        v = predict(fam, p, {"x": tss})
    except (KeyError, ValueError):
        return None
    return v if v is not None and math.isfinite(v) and v >= 0 else None


@dataclass
class GroupModel:
    """One TSS source's conversion: the athlete's family blended with the default."""
    group: str
    family: str = ""
    params: dict = field(default_factory=dict)
    w: float = 0.0                       # 0 = the default only
    n: int = 0
    err: Optional[float] = None          # fraction (MAPE) for the ± shown

    @classmethod
    def of(cls, group: str, entry: Optional[dict]) -> "GroupModel":
        e = entry if isinstance(entry, dict) else {}
        if e.get("family") in FAMILIES and isinstance(e.get("params"), dict) and e.get("w"):
            bt = e.get("backtest") or {}
            err = bt.get("mape") if bt.get("mape") is not None else (e.get("loo") or {}).get("mape")
            return cls(group, e["family"], dict(e["params"]), float(e["w"]), int(e.get("n") or 0), num(err))
        return cls(group)

    @property
    def fitted(self) -> bool:
        return bool(self.family and self.w > 0)

    def tl(self, tss: float, if_: Optional[float] = None) -> Optional[float]:
        dfam, dp = DEFAULTS[self.group]
        base = _family_tl(dfam, dp, tss, if_)
        if not self.fitted:
            return base
        own = _family_tl(self.family, self.params, tss, if_)
        if own is None:
            return base
        if base is None:
            return own
        return self.w * own + (1.0 - self.w) * base

    def err_frac(self) -> float:
        if not self.fitted or self.err is None:
            return DEFAULT_ERR
        return self.w * self.err + (1.0 - self.w) * DEFAULT_ERR


@dataclass
class Model:
    groups: dict
    factor: float = 1.0                  # closed-loop correction (TSS ÷ factor before converting)

    @classmethod
    def of(cls, stored: Optional[dict] = None, load_store: Optional[dict] = None) -> "Model":
        st = stored if isinstance(stored, dict) else {}
        return cls({g: GroupModel.of(g, (st.get("groups") or {}).get(g)) for g in GROUPS},
                   float(load_factor(load_store)["factor"]))

    def pick(self, basis: Optional[str], if_: Optional[float]) -> str:
        if basis == "power":
            return "power"
        return "hr" if if_ else "linear"

    def tl(self, tss: float, basis: Optional[str] = None, if_: Optional[float] = None) -> dict:
        """{"tl", "err" (± TL), "group", "fitted"} of a planned TSS (推估)."""
        x = max(0.0, float(tss)) / (self.factor or 1.0)
        g = self.pick(basis, if_)
        v = self.groups[g].tl(x, if_)
        if v is None and g == "hr":
            g = "linear"
            v = self.groups[g].tl(x, if_)
        if v is None:
            g, v = "linear", LINEAR_A * x
        gm = self.groups[g]
        return {"tl": round(v, 1), "err": round(v * gm.err_frac(), 1), "group": g, "fitted": gm.fitted}

    def tss(self, tl: float, basis: Optional[str] = None, if_: Optional[float] = None) -> float:
        """The inverse: the TSS whose conversion is `tl` (a COROS step's planned load in TSS terms).
        Every family is monotone in TSS: bisection."""
        tl = max(0.0, float(tl))
        if tl == 0:
            return 0.0
        lo, hi = 0.0, 50.0
        while self.tl(hi, basis, if_)["tl"] < tl and hi < 1e5:
            hi *= 2
        f = lambda x: self._raw(x, basis, if_)
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid) < tl:
                lo = mid
            else:
                hi = mid
        return round((lo + hi) / 2, 1)

    def _raw(self, tss: float, basis, if_) -> float:
        x = tss / (self.factor or 1.0)
        g = self.pick(basis, if_)
        v = self.groups[g].tl(x, if_)
        if v is None and g == "hr":
            v = self.groups["linear"].tl(x, if_)
        return LINEAR_A * x if v is None else v


def convert(tss: float, basis: Optional[str] = None, if_: Optional[float] = None,
            model: Optional[Model] = None) -> dict:
    return (model or current()).tl(tss, basis, if_)


def inverse(tl: float, basis: Optional[str] = None, if_: Optional[float] = None,
            model: Optional[Model] = None) -> float:
    return (model or current()).tss(tl, basis, if_)


# ---------------------------------------------------------------------------
# reading the stored fit (sync, read-only; memoised like calibrate.stored_entry)
# ---------------------------------------------------------------------------

_MEMO: dict = {}
_TTL_S = 10.0


def _read(key: str, user_id: int = 1):
    from backend.engine.wko5expr.datasource import _db_path, read_setting
    mk = (key, user_id, str(_db_path()))
    hit = _MEMO.get(mk)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    v = read_setting(key, None, user_id)
    v = v if isinstance(v, dict) else None
    _MEMO[mk] = (time.monotonic(), v)
    return v


def forget_reads() -> None:
    _MEMO.clear()


def current(user_id: int = 1) -> Model:
    """The conversion in effect (stored fit + closed-loop factor; the defaults without)."""
    try:
        return Model.of(_read(KEY, user_id), _read(LOAD_KEY, user_id))
    except Exception:                         # noqa: BLE001 — a missing DB: the defaults
        return Model.of()


# ---------------------------------------------------------------------------
# the per-athlete refit
# ---------------------------------------------------------------------------

def label_of_file(name: str) -> Optional[str]:
    """COROS FIT file names start with the activity labelId (`<labelId>_<date>_<sport>.fit`)."""
    from pathlib import Path
    head = Path(str(name)).name.split("_", 1)[0]
    return head if head.isdigit() else None


def group_samples(rows: list[dict]) -> dict[str, list[dict]]:
    """Activity rows {date, tl, tss, tss_source, if, hrtss, hrif, hours, ftp, lthr} → the
    samples of each group: power (power TSS), hr (hrTSS with its IF on every row that has
    one), linear (rows whose TSS is hrTSS)."""
    out: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for r in rows:
        y, h = num(r.get("tl")), num(r.get("hours"))
        if y is None or y <= 0 or not h or h * 60 < MIN_MINUTES or h > MAX_HOURS:
            continue
        day = r.get("date")
        tss, src = num(r.get("tss")), r.get("tss_source")
        if src == "power" and tss and tss > 0:
            out["power"].append({"x": tss, "y": y, "h": h, "if": num(r.get("if")), "date": day,
                                 "thr": num(r.get("ftp"))})
        if src == "hrtss" and tss and tss > 0:
            out["linear"].append({"x": tss, "y": y, "h": h, "if": num(r.get("hrif")), "date": day,
                                  "thr": num(r.get("lthr"))})
        hx, hi = num(r.get("hrtss")), num(r.get("hrif"))
        if hx and hx > 0 and hi:
            out["hr"].append({"x": hx, "y": y, "h": h, "if": hi, "date": day, "thr": num(r.get("lthr"))})
    return out


def since_threshold_change(s: list[dict]) -> list[dict]:
    """The samples after the last threshold shift > THRESHOLD_SHIFT (sorted by date; the
    newest sample's threshold is the reference; samples without one are kept)."""
    s = sorted(s, key=lambda d: d.get("date") or "")
    ref = next((d["thr"] for d in reversed(s) if d.get("thr")), None)
    if not ref:
        return s
    cut = 0
    for i, d in enumerate(s):
        if d.get("thr") and abs(d["thr"] - ref) / ref > THRESHOLD_SHIFT:
            cut = i + 1
    return s[cut:]


def recency(s: list[dict], today: dt.date) -> list[dict]:
    out = []
    for d in s:
        try:
            age = (today - dt.date.fromisoformat(str(d.get("date"))[:10])).days
        except (TypeError, ValueError):
            age = 0
        out.append({**d, "wt": 0.5 ** (max(0, age) / HALF_LIFE_DAYS)})
    return out


def choose_family(s: list[dict], group: str) -> Optional[str]:
    fams = SMALL_FAMILIES if len(s) < SMALL_N else FAMILIES
    if group != "hr":
        fams = tuple(f for f in fams if f != "D") or fams
    return best_model(evaluate(s, fams))


def _entry(group: str, fam: str, s: list[dict], today: dt.date, n_w: Optional[int] = None) -> Optional[dict]:
    p = fit(fam, [d for d in s if predict_inputs_ok(fam, d)])
    if p is None:
        return None
    if fam == "D":
        ifs = [d["if"] for d in s if d.get("if")]
        lo, hi = (float(np.percentile(ifs, IF_PCT[0])), float(np.percentile(ifs, IF_PCT[1]))) if ifs else HR_IF_RANGE
        if p["c2"] < 0:
            hi = min(hi, -p["c1"] / (2 * p["c2"]))
        p.update(if_lo=round(lo, 4), if_hi=round(max(lo, hi), 4))
    n = len(s) if n_w is None else n_w
    return {"family": fam, "params": {k: round(v, 6) for k, v in p.items()}, "n": len(s),
            "w": round(n / (n + SHRINK_K), 4), "fitted_at": today.isoformat()}


def _gm_errors(gm: GroupModel, test: list[dict]) -> dict:
    pairs = [(v, d["y"]) for d in test if (v := gm.tl(d["x"], d.get("if"))) is not None]
    return errors(pairs)


def refit_group(group: str, s: list[dict], stored: Optional[dict], today: dt.date) -> tuple[Optional[dict], dict]:
    """(the entry to store — the old one when the new fit is not better — , a report)."""
    s = recency(since_threshold_change(s), today)
    rep = {"n": len(s)}
    fam = choose_family(s, group) if len(s) >= MIN_N["A"] else None
    if fam is None:
        return stored, {**rep, "kept": "too few samples"}
    rep["family"] = fam
    # time-ordered backtest
    last = max((d.get("date") or "") for d in s)
    try:
        cut = (dt.date.fromisoformat(last[:10]) - dt.timedelta(days=HOLDOUT_DAYS)).isoformat()
    except ValueError:
        cut = ""
    train = [d for d in s if (d.get("date") or "") < cut]
    test = [d for d in s if (d.get("date") or "") >= cut]
    cur = GroupModel.of(group, stored)
    accept, bt = False, None
    if len(test) >= BACKTEST_MIN_N and len(train) >= MIN_N[fam]:
        cand = _entry(group, fam, recency(train, dt.date.fromisoformat(cut)), today)
        if cand is not None:
            new_e, cur_e = _gm_errors(GroupModel.of(group, cand), test), _gm_errors(cur, test)
            bt = {"n": new_e["n"], "mae": new_e["mae"], "mape": new_e["mape"], "current_mae": cur_e["mae"],
                  "holdout_days": HOLDOUT_DAYS}
            accept = new_e["mae"] is not None and (cur_e["mae"] is None or new_e["mae"] <= cur_e["mae"])
    else:
        accept = not cur.fitted
        rep["backtest"] = "no holdout"
    rep["backtest_result"] = bt
    if not accept:
        kept = dict(stored) if isinstance(stored, dict) else None
        if kept is not None:
            kept["last_check"] = {"at": today.isoformat(), "candidate": bt}
        return kept, {**rep, "kept": "the stored fit is better on the holdout" if bt else "fit kept"}
    e = _entry(group, fam, s, today)
    if e is None:
        return stored, {**rep, "kept": "fit failed"}
    l = loo(fam, [d for d in s if predict_inputs_ok(fam, d)])
    e["loo"] = {"n": l["n"], "mae": _r(l["mae"]), "mape": _r(l["mape"], 4)}
    e["backtest"] = {k: (_r(v, 4) if isinstance(v, float) else v) for k, v in bt.items()} if bt else None
    return e, {**rep, "accepted": True}


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def refit(rows: list[dict], stored: Optional[dict], today: dt.date) -> tuple[dict, dict]:
    """(new stored value, report) from the activity rows (group_samples)."""
    st = stored if isinstance(stored, dict) else {}
    old = st.get("groups") or {}
    groups, report = {}, {}
    for g, s in group_samples(rows).items():
        e, rep = refit_group(g, s, old.get(g), today)
        report[g] = rep
        if e is not None:
            groups[g] = e
    return {"groups": groups, "checked_at": today.isoformat()}, report


def validate(value) -> None:
    if value is None:
        return
    if not (isinstance(value, dict) and isinstance(value.get("groups", {}), dict)):
        raise ValueError(f"{KEY} must be {{groups: {{power|hr|linear: {{family, params, n, w}}}}}} or null")
    for g, e in value.get("groups", {}).items():
        if g not in GROUPS or not isinstance(e, dict) or e.get("family") not in FAMILIES \
                or not isinstance(e.get("params"), dict):
            raise ValueError(f"{KEY}: bad group {g!r}")


def describe(stored: Optional[dict] = None, load_store: Optional[dict] = None) -> dict:
    """The settings page's view: per group the model in effect, n, weight and error (推估)."""
    m = Model.of(stored, load_store)
    st = (stored or {}).get("groups") or {} if isinstance(stored, dict) else {}
    rows = []
    for g in GROUPS:
        gm, e = m.groups[g], st.get(g) or {}
        dfam, _dp = DEFAULTS[g]
        rows.append({"group": g, "label": GROUP_LABEL[g], "fitted": gm.fitted, "family": gm.family or dfam,
                     "text": MODEL_TEXT[gm.family or dfam], "default_text": MODEL_TEXT[dfam], "n": gm.n,
                     "w": gm.w, "loo": e.get("loo"), "backtest": e.get("backtest"),
                     "fitted_at": e.get("fitted_at"), "err_pct": round(gm.err_frac() * 100)})
    lf = load_factor(load_store)
    return {"groups": rows, "checked_at": (stored or {}).get("checked_at") if isinstance(stored, dict) else None,
            "load": lf, "shrink_k": SHRINK_K, "badge": "推估"}


# ---------------------------------------------------------------------------
# closed loop: pushed load steps that were run
# ---------------------------------------------------------------------------

def _sessions(store) -> dict:
    return dict((store or {}).get("sessions") or {}) if isinstance(store, dict) else {}


def record_push(store, uid: str, day: Optional[str], steps: list[dict]) -> dict:
    """At push: the session's load steps sent with COROS's load target [{i (run-order index),
    n (steps in the session), tss (planned), tl (sent)}]. A session already run keeps its
    sample (one per session: re-pushing never counts it twice)."""
    ss = _sessions(store)
    old = ss.get(uid) or {}
    if steps and old.get("actual") is None:
        ss[uid] = {"day": day, "steps": [dict(x) for x in steps], "actual": None}
    keep = sorted(ss.items(), key=lambda kv: kv[1].get("day") or "", reverse=True)[:MAX_LOAD_SESSIONS]
    return {"sessions": dict(keep)}


def window_share(t, x, a: float, b: float) -> Optional[float]:
    """Share of Σ x²·dt (≈ the TSS density: IF² per second) that falls in [a, b) s."""
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    if len(t) < 2 or len(t) != len(x):
        return None
    dt_ = np.clip(np.diff(t, prepend=t[0]), 0, 10.0)
    d = np.where(np.isfinite(x), x, 0.0) ** 2 * dt_
    tot = float(d.sum())
    if tot <= 0:
        return None
    m = (t >= a) & (t < b)
    return float(d[m].sum()) / tot


def step_actual(laps: list[dict], t, x, total_tss: float, i: int, n: int) -> Optional[float]:
    """The TSS accumulated in pushed step `i` of `n`: its lap's share of the activity's load
    density × the activity's TSS. None when the laps don't match the pushed steps one to one."""
    if not laps or len(laps) != n or not 0 <= i < n or not total_tss:
        return None
    lp = laps[i]
    a = float(lp["start_s"])
    sh = window_share(t, x, a, a + float(lp["duration_s"]))
    return None if sh is None else round(sh * float(total_tss), 1)


def refresh_load(store, sessions: list[dict], actual_of: Callable[[dict, dict], Optional[list]]) -> tuple[dict, bool]:
    """Fill the recorded sessions that are now done: actual_of(session, record) → the actual
    TSS per recorded step (None = can't tell). (store, changed)."""
    ss = _sessions(store)
    changed = False
    by_uid = {s.get("uid"): s for s in sessions or []}
    for uid, rec in ss.items():
        s = by_uid.get(uid)
        if not s or s.get("state") != "done" or rec.get("actual") is not None:
            continue
        try:
            act = actual_of(s, rec)
        except Exception as e:               # noqa: BLE001 — one session never stops the others
            log.warning("load step sample %s failed: %s", uid, type(e).__name__)
            act = None
        if act and len(act) == len(rec.get("steps") or []):
            ss[uid] = {**rec, "actual": act}
            changed = True
    return {"sessions": ss}, changed


def load_factor(store) -> dict:
    """{factor, n, k, ratio (unshrunk, None without samples)}."""
    rs = []
    for rec in _sessions(store).values():
        for st, a in zip(rec.get("steps") or [], rec.get("actual") or []):
            p = num((st or {}).get("tss"))
            a = num(a)
            if p and a and p > 0 and a > 0:
                rs.append(_clamp(a / p, *RATIO_RANGE))
    n = len(rs)
    if not n:
        return {"factor": 1.0, "n": 0, "k": LOAD_K, "ratio": None}
    m = sum(math.log(r) for r in rs) / n
    w = n / (n + LOAD_K)
    return {"factor": round(math.exp(w * m), 4), "n": n, "k": LOAD_K, "ratio": round(math.exp(m), 4)}


def validate_load(value) -> None:
    if value is None:
        return
    if not (isinstance(value, dict) and isinstance(value.get("sessions"), dict) and all(
            isinstance(k, str) and isinstance(e, dict) for k, e in value["sessions"].items())):
        raise ValueError(f"{LOAD_KEY} must be {{sessions: {{uid: {{day, steps, actual}}}}}} or null")


# ---------------------------------------------------------------------------
# the Dataset / DB side (calibrate.calibrate runs this after a sync)
# ---------------------------------------------------------------------------

def activity_rows(ds, tl_by_label: dict) -> list[dict]:
    """The Dataset's workouts with a COROS TL (by labelId) as refit rows."""
    from backend.engine.wko5expr.dataset import day_to_date
    out = []
    for w in ds.workouts:
        lab = label_of_file(w.entry.file)
        tl = tl_by_label.get(lab) if lab else None
        if tl is None:
            continue
        m = w.metrics
        secs = num(m.get("movingduration")) or num(m.get("duration"))
        try:
            lthr = num(ds.sport_setting("thr", w))
        except Exception:                    # noqa: BLE001
            lthr = None
        out.append({"date": day_to_date(w.day).isoformat(), "tl": tl, "tss": m.get("tss"),
                    "tss_source": m.get("tss_source"), "if": m.get("if"), "hrtss": m.get("hrtss"),
                    "hrif": m.get("hrif"), "hours": secs / 3600.0 if secs else None,
                    "ftp": m.get("ftp_used"), "lthr": lthr})
    return out


def _actual_from_ds(ds) -> Callable[[dict, dict], Optional[list]]:
    def actual_of(s: dict, rec: dict) -> Optional[list]:
        a = s.get("done_by") if isinstance(s.get("done_by"), dict) else {}
        idx = a.get("index")
        if idx is None or not 0 <= int(idx) < len(ds.workouts):
            return None
        w = ds.workouts[int(idx)]
        from backend.engine.interval_reps import laps_of
        laps = laps_of(ds, w)
        total = num(w.metrics.get("tss"))
        ch = "power" if w.metrics.get("tss_source") == "power" else "heartrate"
        x = ds.channel(w.idx, ch)
        dts = ds.channel(w.idx, "deltatime")
        if x is None or dts is None or total is None:
            return None
        t = np.nancumsum(np.nan_to_num(dts))
        out = []
        for st in rec.get("steps") or []:
            v = step_actual(laps, t, x, total, int(st.get("i", -1)), int(st.get("n", 0)))
            if v is None:
                return None
            out.append(v)
        return out
    return actual_of


async def refit_and_store(db, athlete_id: int, ds, today: Optional[dt.date] = None) -> dict:
    """Refit on the athlete's synced COROS activities and refresh the closed-loop samples."""
    import asyncio
    from sqlalchemy import select
    from backend.db.models import WorkoutFile
    from backend.engine import plan_store as PS
    from backend.settings.repository import SettingsRepository
    today = today or dt.date.today()
    res = await db.execute(select(WorkoutFile.coros_activity_id, WorkoutFile.coros_training_load).where(
        WorkoutFile.athlete_id == athlete_id, WorkoutFile.coros_training_load.is_not(None)))
    tl_by = {str(a): float(v) for a, v in res.all() if a and v}
    repo = SettingsRepository(db, athlete_id)
    out: dict = {"samples": len(tl_by)}
    if tl_by:
        rows = await asyncio.to_thread(activity_rows, ds, tl_by)
        new, rep = await asyncio.to_thread(refit, rows, await repo.get(KEY), today)
        await repo.set(KEY, new)
        out["report"] = rep
    store = await repo.get(LOAD_KEY)
    if _sessions(store):
        sessions = await PS.load(db, athlete_id)
        new_store, changed = await asyncio.to_thread(refresh_load, store, sessions, _actual_from_ds(ds))
        if changed:
            await repo.set(LOAD_KEY, new_store)
        out["load"] = load_factor(new_store)
    await db.commit()
    forget_reads()
    return out
