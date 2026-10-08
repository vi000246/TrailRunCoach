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

Conversion, per level (decided 2026-10-07 — one Foster factor × CR-10 is too low at the easy
end and too high at the hard end): TSS = the level's TSS per hour × minutes ÷ 60.

  * default: the TSS definition, TSS per hour = IF² × 100, at the level's intensity
    (DEFAULT_IF; the sources and what is 推估 at DEFAULT_IF / IF_SRC). The defaults are a
    general rule for everyone — not tuned to any one athlete.
  * per athlete (`refit`, after each sync through engine/calibrate.calibrate): personalisation
    comes only from the athlete's own rated activities — the FIT's own RPE (Garmin) or COROS's
    post-run rating (SP-231: feel 1–5 → workout_files.rpe 2 / 4 / 5 / 7 / 10, exactly the five
    levels; COROS FIT files carry no RPE — the rating comes from the activity detail,
    engine/coros_rpe.py) — and the activity's TSS ÷ moving hours. A FIT RPE 1–10 maps two
    values per level (`level_of_rpe`: 1–2, 3–4, 5–6, 7–8, 9–10; 6 is 累 because Seiler's
    zone 2 is session RPE 5–6).
  * per level: the activities after the last big threshold change only
    (coros_tl.since_threshold_change: TSS moves with FTP / LTHR, a felt RPE does not), recency
    weight 0.5^(age / 120 d); personal = exp(weighted mean ln TSS/h); shrunk in log space toward
    the default, ln r = w·ln personal + (1 − w)·ln default, w = n_eff / (n_eff + SHRINK_K) with
    n_eff = Σ recency weights (the effective sample size: old ratings count less); below MIN_N
    activities the level keeps its default
  * order: a higher level never gives less TSS per hour — the shrunk rates go through a
    weighted isotonic fit (pool adjacent violators in log space, weight n_eff + SHRINK_K: the
    default counts as SHRINK_K activities); the pooled levels are `adjusted`. The stored value
    keeps each fitted level's rate before the ordering (`shrunk`), so reading it repeats the
    same ordering and gives the stored rates back
  * no rated activity left (none at all, or none after a threshold change): the stored fit is
    cleared — the defaults again
  * error: leave-one-out — refit without one activity, predict its TSS: MAPE / MAE / bias

Why RPE + minutes (not a rate × the step's estimated time): a 「負荷」 step's time is itself
estimated from its TSS (workout_steps._secs: TSS ÷ IF² × 100), so it needs a time from the
user — the minutes, which is what session RPE is (a rating × a duration). The step keeps its
TSS in `dur.value` like a typed one, so the push, the TL conversion and the closed loop are
unchanged; `dur.rpe` / `dur.min` keep how it was entered. normalize recomputes the TSS with the
rates in effect, so new rates (the first read after the per-level change, every refit) move the
step's TSS — and with it the TL: workout_steps.sent_key holds the step's TSS, so the hysteresis
TL_RESEND_MIN only damps TL refits at an unchanged TSS; a moved TSS is pushed again once.
`sync_sessions` rewrites the stored upcoming sessions with an RPE step to the same TSS (steps and
the session's `tss`) whenever the rates change, so the plan, the weekly TSS and the guardrails
agree with what the watch gets. A default level's step implies exactly that level's IF
(√(TSS ÷ (100 × h))); a fitted rate above 169 TSS/h is converted at the step clamp IF 1.3.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import math
import time
from typing import Optional

from backend.i18n import N_, _

log = logging.getLogger(__name__)

KEY = "rpe.load_model"               # settings: the per-athlete per-level fit (None = the defaults)
STAMP_KEY = "rpe.load_stamp"         # settings: the rates the stored sessions were last normalised with

# (id, label, Borg CR-10) — the editor's five levels (SP-57, the user 2026-10-04)
LEVELS = (("easy", N_("輕鬆"), 2), ("moderate", N_("稍累"), 4), ("hard", N_("累"), 5),
          ("very_hard", N_("很累"), 7), ("max", N_("極限"), 10))
IDS = tuple(k for k, _l, _v in LEVELS)
CR10 = {k: v for k, _l, v in LEVELS}
LABEL = {k: l for k, l, _v in LEVELS}
MIN_RANGE = (1, 360)                 # minutes of one RPE-entered load step

# --- the default intensity of each level (IF ≈ the fraction of CP / FTP held) ------------------
# A general rule for everyone. Sources for the bands (the points inside them are 推估):
#   Seiler & Kjerland 2006 (Scand J Med Sci Sports 16:49–56) count sessions by session RPE:
#     ≤ 4 zone 1 (below VT1 / LT1), 5–6 zone 2 (VT1–VT2), ≥ 7 zone 3 (above VT2) — the same
#     cut-offs as workout_steps.RPE_EASY_MAX / RPE_HARD_MIN.
#   Coggan's power levels (TrainingPeaks, "Power Training Levels"; RPE on Borg CR-10):
#     L2 56–75 % FTP RPE 2–3, L3 76–90 % RPE 3–4, L4 91–105 % RPE 4–5, L5 106–120 % RPE 6–7,
#     L6 > 120 % RPE > 7, L7 maximal. Coggan rates the moment, not a whole block: a 20–60 min
#     block can't be held at L5, so for a whole main set the session-RPE zones win.
#   Running power (Stryd / Palladino, % CP): easy 65–80, moderate 80–90, threshold 90–100,
#     interval 100–115 (workout_steps' easy anchor EASY_F 0.78 sits at the top of easy).
#   TSS definition (Coggan): an hour at threshold = 100 TSS, TSS per hour = IF² × 100.
DEFAULT_IF = {
    "easy": 0.74,        # zone 1, running-power easy 65–80 % CP, easy runs in its upper half; point 推估
    "moderate": 0.82,    # CR-10 4 = top of zone 1 ≈ VT1 ≈ 80–82 % CP (Coggan L3 RPE 3–4: 76–90 %); point 推估
    "hard": 0.88,        # zone 2 (sRPE 5–6, VT1–VT2), running-power moderate 80–90 % CP; point 推估
    "very_hard": 0.95,   # session RPE rates the whole main set; 7 = start of zone 3 (≈ CP); 20–60 min can't
                         # be held at 106–120 % FTP, so just under CP; point 推估
    "max": 1.10,         # CR-10 10 maximal: above CP (Coggan L5 106–120 %, running-power interval 100–115 %);
                         # an all-out 10–20 min ≈ 1.05–1.15; = workout_steps.RPE_FRAC[10]; point 推估
}
IF_SRC = {
    "easy": N_("session RPE ≤ 4 是 Seiler 第一區（低於 VT1）；跑步功率輕鬆區 65–80 % CP，輕鬆跑多在上半段，取 0.74（範圍有出處，取點推估）"),
    "moderate": N_("CR-10 4 是第一區上緣（約 VT1）；Coggan 節奏區 RPE 3–4 對 76–90 % FTP，取 VT1 附近的 0.82（範圍有出處，取點推估）"),
    "hard": N_("session RPE 5–6 是 Seiler 第二區（VT1 到 VT2）；跑步功率中等區 80–90 % CP，取 0.88（範圍有出處，取點推估）"),
    "very_hard": N_("session-RPE 評整段主課；7 分是第三區的起點（約 CP），整段 20–60 分鐘撐不住 106–120 % FTP，所以取 CP 略下的 0.95（範圍有出處，取點推估）"),
    "max": N_("CR-10 10 是極限，高於 CP（Coggan VO2max 區 106–120 % FTP）；全力撐 10–20 分鐘約 1.05–1.15，取 1.10（範圍有出處，取點推估）"),
}
DEFAULT_TSS_H = {k: 100.0 * f * f for k, f in DEFAULT_IF.items()}     # the TSS definition

# --- the fit (all 推估) -------------------------------------------------------------------------
DEFAULT_ERR = 0.30                   # ± fraction shown with the defaults (推估: sRPE vs TRIMP r ≈ 0.8)
SHRINK_K = 10                        # w = n_eff / (n_eff + 10): 10 recent rated activities = half personal
MIN_N = 3                            # below this many activities a level keeps its default
RATE_RANGE = (10.0, 250.0)           # TSS per hour kept (outside: a mis-rated or mis-recorded activity)
MIN_MINUTES, MAX_HOURS = 10.0, 20.0  # as coros_tl (no multi-day trips)
# FIT RPE 1–10 → level: two values each; 6 → 累 (Seiler zone 2 = session RPE 5–6), 8 → 很累, 9 → 極限
FIT_LEVEL = {1: "easy", 2: "easy", 3: "moderate", 4: "moderate", 5: "hard", 6: "hard",
             7: "very_hard", 8: "very_hard", 9: "max", 10: "max"}


def num(v) -> Optional[float]:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def level_of_rpe(rpe) -> Optional[str]:
    """A 10-point RPE (a FIT's own, or COROS's 1–5 mapped by SP-231 to 2 / 4 / 5 / 7 / 10) → its
    level (FIT_LEVEL, the RPE rounded half up)."""
    r = num(rpe)
    if r is None or not 0 < r <= 10:
        return None
    return FIT_LEVEL[min(10, max(1, int(math.floor(r + 0.5))))]


def monotone(rates: dict, weights: dict) -> tuple[dict, dict]:
    """({level: rate}, {level: adjusted}) — the rates made non-decreasing over the levels by a
    weighted isotonic fit in log space (pool adjacent violators); in order already = unchanged."""
    blocks = []                                   # [sum w·ln r, sum w, [levels]]
    for k in IDS:
        w = max(float(weights.get(k) or 0.0), 1e-9)
        blocks.append([w * math.log(rates[k]), w, [k]])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1] + 1e-12:
            b = blocks.pop()
            blocks[-1][0] += b[0]
            blocks[-1][1] += b[1]
            blocks[-1][2] += b[2]
    out, adj = {}, {}
    for s, w, ks in blocks:
        for k in ks:
            out[k] = math.exp(s / w) if len(ks) > 1 else rates[k]
            adj[k] = len(ks) > 1
    return out, adj


def _ordered(raw: dict) -> dict:
    """raw {level: {shrunk, n_eff, fitted, …}} → the same with tss_h / adjusted after the ordering:
    a fitted level enters with its shrunk rate, any other with its default; weight n_eff + SHRINK_K."""
    rates, adj = monotone({k: (v["shrunk"] if v["fitted"] else DEFAULT_TSS_H[k]) for k, v in raw.items()},
                          {k: (v["n_eff"] if v["fitted"] else 0.0) + SHRINK_K for k, v in raw.items()})
    return {k: {**raw[k], "tss_h": rates[k], "adjusted": adj[k]} for k in IDS}


# ---------------------------------------------------------------------------
# the conversion
# ---------------------------------------------------------------------------

class Model:
    """The TSS per hour of each level in effect (the stored fit, put in order; else the defaults)."""

    def __init__(self, levels: Optional[dict] = None, n: int = 0, err: Optional[float] = None):
        raw = {}
        for k in IDS:
            e = levels.get(k) if isinstance(levels, dict) else None
            e = e if isinstance(e, dict) else {}
            n_k = int(num(e.get("n")) or 0)
            n_eff = num(e.get("n_eff"))
            w_k = float(num(e.get("w")) or 0.0)
            r = num(e.get("shrunk")) or num(e.get("tss_h"))
            fitted = r is not None and r > 0 and n_k >= MIN_N and w_k > 0
            # a level without a fit of its own reads its default (never a stored copy)
            raw[k] = {"shrunk": r if fitted else DEFAULT_TSS_H[k], "n": n_k,
                      "n_eff": (n_eff if n_eff is not None else float(n_k)) if fitted else (n_eff or 0.0),
                      "w": w_k if fitted else 0.0, "personal": num(e.get("personal")), "fitted": fitted}
        self.levels = _ordered(raw)
        self.n = int(n or 0)
        self.err = err

    @classmethod
    def of(cls, stored: Optional[dict] = None) -> "Model":
        e = stored if isinstance(stored, dict) else {}
        lv = e.get("levels")
        if not isinstance(lv, dict):                  # none, or the old single factor: the defaults
            return cls()
        loo = e.get("loo") if isinstance(e.get("loo"), dict) else {}
        return cls(lv, int(num(e.get("n")) or 0), num(loo.get("mape")))

    @property
    def fitted(self) -> bool:
        return any(v["fitted"] for v in self.levels.values())

    @property
    def w(self) -> float:
        return self.n / (self.n + SHRINK_K) if self.fitted else 0.0

    def err_frac(self) -> float:
        if not self.fitted or self.err is None:
            return DEFAULT_ERR
        return self.w * self.err + (1.0 - self.w) * DEFAULT_ERR

    def rate(self, level: str) -> Optional[float]:
        v = self.levels.get(level)
        return v["tss_h"] if v else None

    def level(self, level: str) -> Optional[dict]:
        return self.levels.get(level)

    def tss(self, level: str, minutes: float) -> Optional[float]:
        """The planned TSS of `minutes` at an RPE level (None: unknown level / no time)."""
        r = self.rate(level)
        m = num(minutes)
        if r is None or not m or m <= 0:
            return None
        return round(r * m / 60.0, 1)


def to_tss(level: str, minutes: float, model: Optional[Model] = None) -> Optional[float]:
    return (model or current()).tss(level, minutes)


def source_of(e: dict) -> str:
    """fitted (本人) | adjusted (moved only by the ordering, no data of its own) | default (推估)."""
    return "fitted" if e.get("fitted") else "adjusted" if e.get("adjusted") else "default"


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
    """The rates in effect (the stored fit; the defaults without one or without a DB)."""
    try:
        return Model.of(_read(user_id))
    except Exception:                         # noqa: BLE001 — a missing DB: the defaults
        return Model()


# ---------------------------------------------------------------------------
# the per-athlete, per-level refit
# ---------------------------------------------------------------------------

def samples(rows: list[dict]) -> list[dict]:
    """Activity rows {date, rpe, tss, hours, thr} → samples {level, y (TSS/h), h, date, thr}."""
    out = []
    for x in rows:
        lvl = level_of_rpe(x.get("rpe"))
        tss, h = num(x.get("tss")), num(x.get("hours"))
        if lvl is None or not tss or tss <= 0 or not h or h * 60 < MIN_MINUTES or h > MAX_HOURS:
            continue
        y = tss / h
        if not RATE_RANGE[0] <= y <= RATE_RANGE[1]:
            continue
        out.append({"level": lvl, "y": y, "h": h, "date": x.get("date"), "thr": num(x.get("thr"))})
    return out


def _fit(s: list[dict]) -> dict:
    """{level: {tss_h, shrunk, n, n_eff, w, personal, fitted, adjusted}} of weighted samples (every
    level; < MIN_N samples: the default, w 0), put in order."""
    raw = {}
    for k in IDS:
        mine = [d for d in s if d["level"] == k]
        n = len(mine)
        n_eff = sum(d.get("wt", 1.0) for d in mine)               # the effective sample size
        if n < MIN_N or n_eff <= 0:
            raw[k] = {"shrunk": DEFAULT_TSS_H[k], "n": n, "n_eff": n_eff, "w": 0.0, "personal": None, "fitted": False}
            continue
        ln_ind = sum(d.get("wt", 1.0) * math.log(d["y"]) for d in mine) / n_eff
        w = n_eff / (n_eff + SHRINK_K)
        raw[k] = {"shrunk": math.exp(w * ln_ind + (1.0 - w) * math.log(DEFAULT_TSS_H[k])), "n": n, "n_eff": n_eff,
                  "w": w, "personal": math.exp(ln_ind), "fitted": True}
    return _ordered(raw)


def loo(s: list[dict]) -> dict:
    """Leave-one-out with the shrinkage and the order: {n, mae (TSS), mape, bias (TSS)}."""
    from backend.engine.coros_tl import errors
    pairs = []
    for i in range(len(s)):
        f = _fit(s[:i] + s[i + 1:])
        pairs.append((f[s[i]["level"]]["tss_h"] * s[i]["h"], s[i]["y"] * s[i]["h"]))
    return errors(pairs)


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def refit(rows: list[dict], today: dt.date) -> tuple[Optional[dict], dict]:
    """(the value to store — None: no rated activity, the defaults — , a report) from the rows."""
    from backend.engine.coros_tl import recency, since_threshold_change
    s = recency(since_threshold_change(samples(rows)), today)
    rep = {"n": len(s)}
    if not s:
        return None, {**rep, "kept": "no rated activity: the defaults"}
    f = _fit(s)
    l = loo(s)
    lv = {k: {"tss_h": round(v["tss_h"], 4), "shrunk": round(v["shrunk"], 6), "n": v["n"],
              "n_eff": round(v["n_eff"], 4), "w": round(v["w"], 4), "personal": _r(v["personal"]),
              "adjusted": v["adjusted"]} for k, v in f.items()}
    return {"levels": lv, "n": len(s),
            "loo": {"n": l["n"], "mae": _r(l["mae"], 1), "mape": _r(l["mape"], 4), "bias": _r(l["bias"], 1)},
            "fitted_at": today.isoformat()}, {**rep, "tss_h": {k: round(v["tss_h"], 2) for k, v in lv.items()}}


def validate(value) -> None:
    if value is None:
        return
    lv = value.get("levels") if isinstance(value, dict) else None
    ok = isinstance(lv, dict) and all(
        k in CR10 and isinstance(e, dict) and (num(e.get("tss_h")) or 0) > 0 for k, e in lv.items())
    if not ok:
        raise ValueError(f"{KEY} must be {{levels: {{easy|moderate|hard|very_hard|max: {{tss_h (> 0), shrunk, n, "
                         f"n_eff, w, personal, adjusted}}}}, n, loo, fitted_at}} or null")


def levels(model: Optional[Model] = None) -> list[dict]:
    """The editor's choice: [{id, cr10, tss_h, fitted, adjusted, n}] (labels: the editor's i18n)."""
    m = model or Model()
    return [{"id": k, "cr10": v, "tss_h": round(m.rate(k)), "fitted": m.level(k)["fitted"],
             "adjusted": bool(m.level(k)["adjusted"] and not m.level(k)["fitted"]), "n": m.level(k)["n"]}
            for k, _l, v in LEVELS]


def _chip(k: str, e: dict) -> dict:
    """「本人 n=12」 / 「依相鄰檔調整」 / 「預設（推估）」 and the hover help of one level (calib_chip.js)."""
    lbl, rate = _(LABEL[k]), round(e["tss_h"])
    src = source_of(e)
    if src == "fitted":
        text = _("本人 n={n}", n=e["n"])
        tip = _("{label}（CR-10 {cr}）每小時 {rate} TSS：本人 {n} 筆有自評的活動擬合 {personal}，權重 {w:.0%}，"
                "其餘用預設 {default}（TSS 定義 IF² × 100，IF {if_}）。",
                label=lbl, cr=CR10[k], rate=rate, n=e["n"], personal=round(e["personal"] or e["tss_h"]),
                w=float(e["w"]), default=round(DEFAULT_TSS_H[k]), if_=f"{DEFAULT_IF[k]:.2f}")
    else:
        text = _("依相鄰檔調整") if src == "adjusted" else _("預設（推估）")
        tip = _("{label}（CR-10 {cr}）預設每小時 {rate} TSS = IF² × 100（TSS 定義），IF {if_}：{src}。"
                "預設是通用規則，不是照哪個人調的；每個人的值只從他自己有自評的活動擬合，"
                "這一檔目前 {n} 筆，滿 {min_n} 筆才開始。",
                label=lbl, cr=CR10[k], rate=round(DEFAULT_TSS_H[k]), if_=f"{DEFAULT_IF[k]:.2f}", src=_(IF_SRC[k]),
                n=e["n"], min_n=MIN_N)
    if e.get("adjusted"):
        tip += " " + _("為了讓高一檔不會比低一檔少，已和相鄰的檔平均成每小時 {rate} TSS。", rate=rate)
    return {"text": text, "tip": tip}


def describe(stored: Optional[dict] = None) -> dict:
    """The settings page's view: each level's TSS per hour, IF, n, default / adjusted / fitted (推估)."""
    m = Model.of(stored)
    e = stored if isinstance(stored, dict) else {}
    lv = []
    for k, _l, cr in LEVELS:
        x = m.level(k)
        lv.append({"id": k, "label": _(LABEL[k]), "cr10": cr, "tss_h": round(x["tss_h"]),
                   "if": round(math.sqrt(x["tss_h"] / 100.0), 2), "default": round(DEFAULT_TSS_H[k]),
                   "default_if": DEFAULT_IF[k], "n": x["n"], "w": round(x["w"], 4), "personal": x["personal"],
                   "fitted": x["fitted"], "adjusted": x["adjusted"], "source": source_of(x), "chip": _chip(k, x)})
    return {"levels": lv, "fitted": m.fitted, "n": m.n, "w": round(m.w, 4), "loo": e.get("loo"),
            "fitted_at": e.get("fitted_at"), "err_pct": round(m.err_frac() * 100), "shrink_k": SHRINK_K,
            "min_n": MIN_N, "badge": _("推估")}


# ---------------------------------------------------------------------------
# stored sessions follow the rates in effect
# ---------------------------------------------------------------------------

def stamp(model: Model) -> str:
    """The rates a stored session's RPE steps were normalised with."""
    return ",".join(f"{k}:{model.rate(k):.4f}" for k in IDS)


def _steps_of(items: list):
    for it in items or []:
        if it.get("kind") == "repeat":
            yield from _steps_of(it.get("items"))
        else:
            yield it


def _rpe_tss(steps: dict) -> float:
    """Σ the TSS the session total (workout_steps.totals) counts for its RPE 「負荷」 steps (repeats
    unrolled; the step's minutes at its clamped IF — independent of the thresholds)."""
    from backend.engine import workout_steps as WS
    out = 0.0
    for row in WS.flat(steps.get("items") or []):
        st = row["st"]
        d = st.get("dur") or {}
        if d.get("type") == "load" and d.get("rpe") and d.get("min"):
            f = WS.load_if(st, None)
            out += float(d["min"]) * 60.0 * f * f * 100.0 / 3600.0
    return out


def renormalize(steps: Optional[dict], model: Model) -> Optional[tuple[dict, float]]:
    """(the steps with every RPE 「負荷」 step's TSS from `model`, Δ of the session TSS) — None when
    no RPE step's TSS moves. The structure (levels, minutes, everything else) is kept as saved."""
    from backend.engine import workout_steps as WS
    if not isinstance(steps, dict) or not steps.get("items"):
        return None
    new = copy.deepcopy(steps)
    moved = False
    for st in _steps_of(new["items"]):
        d = st.get("dur") or {}
        if d.get("type") != "load" or d.get("rpe") not in CR10 or not d.get("min"):
            continue
        v = model.tss(d["rpe"], d["min"])
        if v is None or v > WS.LOAD_RANGE[1]:
            continue                               # normalize refuses it; the push says so
        v = max(float(WS.LOAD_RANGE[0]), v)
        if v != d.get("value"):
            d["value"] = v
            moved = True
    if not moved:
        return None
    return new, _rpe_tss(new) - _rpe_tss(steps)


async def sync_sessions(db, athlete_id: int = 1, today: Optional[str] = None, model: Optional[Model] = None) -> dict:
    """When the rates in effect differ from those the stored sessions were normalised with
    (STAMP_KEY: the first run after the per-level change, every refit that moved them), rewrite the
    upcoming active sessions with an RPE step: the steps' TSS and the session's `tss` (by the
    difference, so the rest of the session's estimate is untouched). No plan change log row — the
    structure is the user's, only the derived TSS follows; the push then sends them again once.
    {changed: n}."""
    from sqlalchemy import select
    from backend.db.models import PlanSession
    from backend.engine import plan_store as PS
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db, athlete_id)
    model = model or Model.of(await repo.get(KEY))
    st = stamp(model)
    if await repo.get(STAMP_KEY) == st:
        return {"changed": 0}
    today = today or dt.date.today().isoformat()
    rows = (await db.execute(select(PlanSession).where(PlanSession.athlete_id == athlete_id,
                                                       PlanSession.state == "active",
                                                       PlanSession.day >= today))).scalars().all()
    changed = 0
    for r in rows:
        if not r.steps or '"rpe"' not in r.steps:
            continue
        res = renormalize(PS._steps_of(r.steps), model)
        if res is None:
            continue
        steps, delta = res
        sig_ok = bool(r.ext_key and r.ext_sig) and not PS.user_edited(PS.to_dict(r))
        r.steps = json.dumps(steps, ensure_ascii=False)
        if r.tss is not None:
            r.tss = round(max(0.0, float(r.tss) + delta), 1)
        if sig_ok:                                 # an exported session stays "not edited by the user"
            r.ext_sig = PS.ext_signature(PS.to_dict(r))
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if r.updated_at is not None and now < r.updated_at + dt.timedelta(seconds=1):
            now = r.updated_at + dt.timedelta(seconds=1)
        r.updated_at = now                         # the 課表訂閱 feed's LAST-MODIFIED / SEQUENCE
        changed += 1
    await repo.set(STAMP_KEY, st)
    await db.commit()
    if changed:
        log.info("RPE load: %d stored session(s) re-normalised to the new rates", changed)
    return {"changed": changed}


# ---------------------------------------------------------------------------
# the Dataset / DB side (calibrate.calibrate runs this after a sync)
# ---------------------------------------------------------------------------

def activity_rows(ds, recorded: Optional[list] = None) -> list[dict]:
    """The Dataset's workouts with a recorded rating (FIT RPE or COROS's, SP-231) as refit rows."""
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
    """Refit the per-level rates on the athlete's rated activities and store them (no rated
    activity left: the stored fit is cleared); then the stored sessions follow the new rates."""
    import asyncio
    from backend.settings.repository import SettingsRepository
    today = today or dt.date.today()
    rows = await asyncio.to_thread(activity_rows, ds)
    new, rep = refit(rows, today)
    repo = SettingsRepository(db, athlete_id)
    if new is not None or await repo.get(KEY) is not None:
        await repo.set(KEY, new)
        await db.commit()
    forget_reads()
    try:
        rep["sessions"] = await sync_sessions(db, athlete_id, today.isoformat(), Model.of(new))
    except Exception as e:                   # noqa: BLE001 — the push re-tries it (coros_workouts)
        await db.rollback()
        log.warning("RPE load: stored sessions not re-normalised: %s", type(e).__name__)
    return rep
