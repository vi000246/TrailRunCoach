"""
「負荷」 entered by feel (SP-57): an RPE level + minutes → the step's TSS (then COROS TL as
every 「負荷」 step: engine/coros_tl.py).

Planning only. The load the PMC, the guardrails and every chart use is always the watch's
record (TSS of the activity); RPE never corrects it (the user, 2026-10-04). This module only
turns a feeling into a planned TSS target.

Five levels, each a Borg CR-10 anchor as Foster's session RPE uses it (Foster et al. 2001,
J Strength Cond Res 15:109–115: 2 easy, 4 somewhat hard, 5 hard, 7 very hard, 10 maximal):

  easy       輕鬆   2
  moderate   稍累   4
  hard       累     5
  very_hard  很累   7
  max        極限   10

Conversion (Foster session RPE): sRPE = CR-10 × minutes (AU); TSS = factor × sRPE.

Why RPE + minutes (not a TSS/h rate × the step's estimated time): a 「負荷」 step's time is
itself estimated from its TSS (workout_steps._secs: TSS ÷ IF² × 100), so a rate needs a time
that needs the TSS — circular. Asking for the minutes is what session RPE is (a rating × a
duration) and is the number the user has in mind ("40 min, 很累"). The step keeps its TSS in
`dur.value` like a typed one, so the push, the TL conversion and the closed loop are
unchanged; `dur.rpe` / `dur.min` keep how it was entered (normalize recomputes the TSS with
the factor in effect, so a refit after a sync moves the target — the push's TL hysteresis,
workout_steps.TL_RESEND_MIN, keeps small moves from re-pushing).

Per-athlete factor (`refit`, after each sync through engine/calibrate.calibrate): samples =
synced activities with a watch-recorded RPE (workout_files.rpe, activity_tags.load_recorded;
the same RPE the auto effort uses) and the activity's TSS; r = TSS ÷ (RPE × moving minutes).

  * the activities after the last big threshold change only (coros_tl.since_threshold_change:
    TSS moves with FTP / LTHR, a felt RPE does not), recency weight 0.5^(age / 120 d)
  * individual factor = exp(weighted mean ln r) (ratios clamped to RATIO_RANGE)
  * shrinkage in log space toward DEFAULT_FACTOR: ln f = w·ln f_ind + (1 − w)·ln f_def,
    w = n / (n + SHRINK_K) (the drift_agg.py BETA_K / coros_tl SHRINK_K pattern)
  * error: leave-one-out — refit (with the shrinkage) without one activity, predict its TSS:
    MAPE / MAE / bias, shown on the settings page next to the TL model
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

from backend.i18n import N_, _

log = logging.getLogger(__name__)

KEY = "rpe.load_model"               # settings: the per-athlete factor (None = the default)

# (id, label, Borg CR-10) — the editor's five levels (SP-57, the user 2026-10-04)
LEVELS = (("easy", N_("輕鬆"), 2), ("moderate", N_("稍累"), 4), ("hard", N_("累"), 5),
          ("very_hard", N_("很累"), 7), ("max", N_("極限"), 10))
CR10 = {k: v for k, _l, v in LEVELS}
LABEL = {k: l for k, l, _v in LEVELS}
MIN_RANGE = (1, 360)                 # minutes of one RPE-entered load step

# --- all 推估 -------------------------------------------------------------------------
# TSS per sRPE unit: an hour at threshold (TSS 100) is rated ≈ 7 (very hard) → 100 / 420 ≈ 0.24;
# an easy hour (TSS ≈ 50) ≈ 2–3 → ≈ 0.3–0.4. One factor can't fit both ends; 0.30 sits between
# (推估 — the athlete's own watch RPE pulls it toward his / her scale)
DEFAULT_FACTOR = 0.30
DEFAULT_ERR = 0.30                   # ± fraction shown with the default (推估: sRPE vs TRIMP r ≈ 0.8)
SHRINK_K = 10                        # w = n / (n + 10): 10 rated activities = half personal
RATIO_RANGE = (0.08, 1.2)            # TSS per AU kept (outside: a mis-rated or mis-recorded activity)
MIN_MINUTES, MAX_HOURS = 10.0, 20.0  # as coros_tl (no multi-day trips)
HALF_LIFE_DAYS = 120.0


def num(v) -> Optional[float]:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# the conversion
# ---------------------------------------------------------------------------

@dataclass
class Model:
    factor: float = DEFAULT_FACTOR       # TSS per AU (sRPE = CR-10 × minutes)
    n: int = 0
    w: float = 0.0
    err: Optional[float] = None          # LOO MAPE (fraction)

    @classmethod
    def of(cls, stored: Optional[dict] = None) -> "Model":
        e = stored if isinstance(stored, dict) else {}
        f = num(e.get("factor"))
        if not f or f <= 0:
            return cls()
        loo = e.get("loo") if isinstance(e.get("loo"), dict) else {}
        return cls(f, int(e.get("n") or 0), float(num(e.get("w")) or 0.0), num(loo.get("mape")))

    @property
    def fitted(self) -> bool:
        return self.n > 0 and self.w > 0

    def err_frac(self) -> float:
        if not self.fitted or self.err is None:
            return DEFAULT_ERR
        return self.w * self.err + (1.0 - self.w) * DEFAULT_ERR

    def tss(self, level: str, minutes: float) -> Optional[float]:
        """The planned TSS of `minutes` at an RPE level (None: unknown level / no time)."""
        cr = CR10.get(level)
        m = num(minutes)
        if cr is None or not m or m <= 0:
            return None
        return round(self.factor * cr * m, 1)


def to_tss(level: str, minutes: float, model: Optional[Model] = None) -> Optional[float]:
    return (model or current()).tss(level, minutes)


# ---------------------------------------------------------------------------
# reading the stored fit (sync, read-only; memoised like coros_tl._read)
# ---------------------------------------------------------------------------

_MEMO: dict = {}
_TTL_S = 10.0


def _read(user_id: int = 1):
    from backend.engine.wko5expr.datasource import _db_path, read_setting
    mk = (user_id, str(_db_path()))
    hit = _MEMO.get(mk)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    v = read_setting(KEY, None, user_id)
    v = v if isinstance(v, dict) else None
    _MEMO[mk] = (time.monotonic(), v)
    return v


def forget_reads() -> None:
    _MEMO.clear()


def current(user_id: int = 1) -> Model:
    """The factor in effect (the stored fit; the default without one or without a DB)."""
    try:
        return Model.of(_read(user_id))
    except Exception:                         # noqa: BLE001 — a missing DB: the default
        return Model()


# ---------------------------------------------------------------------------
# the per-athlete refit
# ---------------------------------------------------------------------------

def samples(rows: list[dict]) -> list[dict]:
    """Activity rows {date, rpe, tss, hours, thr} → samples {r, date, thr, sr (sRPE), y (TSS)}."""
    out = []
    for x in rows:
        rpe, tss, h = num(x.get("rpe")), num(x.get("tss")), num(x.get("hours"))
        if rpe is None or not 0 < rpe <= 10 or not tss or tss <= 0 or not h or h * 60 < MIN_MINUTES or h > MAX_HOURS:
            continue
        sr = rpe * h * 60.0
        r = tss / sr
        if not RATIO_RANGE[0] <= r <= RATIO_RANGE[1]:
            continue
        out.append({"r": r, "sr": sr, "y": tss, "date": x.get("date"), "thr": num(x.get("thr"))})
    return out


def _fit(s: list[dict]) -> Optional[tuple[float, float]]:
    """(the shrunk factor, w) of weighted samples; None without any."""
    if not s:
        return None
    sw = sum(d.get("wt", 1.0) for d in s)
    if sw <= 0:
        return None
    ln_ind = sum(d.get("wt", 1.0) * math.log(d["r"]) for d in s) / sw
    n = len(s)
    w = n / (n + SHRINK_K)
    return math.exp(w * ln_ind + (1.0 - w) * math.log(DEFAULT_FACTOR)), w


def loo(s: list[dict]) -> dict:
    """Leave-one-out with the shrinkage: {n, mae (TSS), mape, bias (TSS)}."""
    from backend.engine.coros_tl import errors
    pairs = []
    for i in range(len(s)):
        f = _fit(s[:i] + s[i + 1:])
        pairs.append(((f[0] if f else DEFAULT_FACTOR) * s[i]["sr"], s[i]["y"]))
    return errors(pairs)


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def refit(rows: list[dict], today: dt.date) -> tuple[Optional[dict], dict]:
    """(the value to store — None: the default — , a report) from the activity rows."""
    from backend.engine.coros_tl import recency, since_threshold_change
    s = recency(since_threshold_change(samples(rows)), today)
    rep = {"n": len(s)}
    f = _fit(s)
    if f is None:
        return None, {**rep, "kept": "no activity with a watch RPE"}
    factor, w = f
    raw = math.exp(sum(d["wt"] * math.log(d["r"]) for d in s) / sum(d["wt"] for d in s))
    l = loo(s)
    return {"factor": round(factor, 4), "n": len(s), "w": round(w, 4), "ratio": round(raw, 4),
            "loo": {"n": l["n"], "mae": _r(l["mae"], 1), "mape": _r(l["mape"], 4), "bias": _r(l["bias"], 1)},
            "fitted_at": today.isoformat()}, {**rep, "factor": round(factor, 4)}


def validate(value) -> None:
    if value is None:
        return
    f = num(value.get("factor")) if isinstance(value, dict) else None
    if not f or f <= 0:
        raise ValueError(f"{KEY} must be {{factor (> 0), n, w, ratio, loo, fitted_at}} or null")


def levels() -> list[dict]:
    """The editor's choice: [{id, cr10}] (labels: the editor's i18n)."""
    return [{"id": k, "cr10": v} for k, _l, v in LEVELS]


def describe(stored: Optional[dict] = None) -> dict:
    """The settings page's view: the factor in effect, n, weight, LOO error (推估)."""
    m = Model.of(stored)
    e = stored if isinstance(stored, dict) else {}
    return {"factor": round(m.factor, 4), "default": DEFAULT_FACTOR, "fitted": m.fitted, "n": m.n,
            "w": round(m.w, 4), "ratio": e.get("ratio"), "loo": e.get("loo"), "fitted_at": e.get("fitted_at"),
            "err_pct": round(m.err_frac() * 100), "shrink_k": SHRINK_K, "levels": levels(), "badge": _("推估")}


# ---------------------------------------------------------------------------
# the Dataset / DB side (calibrate.calibrate runs this after a sync)
# ---------------------------------------------------------------------------

def activity_rows(ds, recorded: Optional[list] = None) -> list[dict]:
    """The Dataset's workouts with a watch-recorded RPE as refit rows."""
    from backend.engine import activity_tags as AT
    from backend.engine.wko5expr.dataset import day_to_date
    recorded = AT.load_recorded() if recorded is None else recorded
    if not recorded:
        return []
    out = []
    for w in ds.workouts:
        rec = AT.recorded_of(recorded, w.entry.start, getattr(w.entry, "file", None))
        rpe = num((rec or {}).get("rpe"))
        if rpe is None:
            continue
        m = w.metrics
        secs = num(m.get("movingduration")) or num(m.get("duration"))
        thr = num(m.get("ftp_used")) if m.get("tss_source") == "power" else None
        if thr is None:
            try:
                thr = num(ds.sport_setting("thr", w))
            except Exception:                # noqa: BLE001
                thr = None
        out.append({"date": day_to_date(w.day).isoformat(), "rpe": rpe, "tss": m.get("tss"),
                    "hours": secs / 3600.0 if secs else None, "thr": thr})
    return out


async def refit_and_store(db, athlete_id: int, ds, today: Optional[dt.date] = None) -> dict:
    """Refit the factor on the athlete's activities with a watch RPE and store it."""
    import asyncio
    from backend.settings.repository import SettingsRepository
    today = today or dt.date.today()
    rows = await asyncio.to_thread(activity_rows, ds)
    new, rep = refit(rows, today)
    repo = SettingsRepository(db, athlete_id)
    if new is not None:
        await repo.set(KEY, new)
        await db.commit()
    forget_reads()
    return rep
