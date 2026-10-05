"""
Post-race calibration of the exported race TSS (「匯出至課表」, watch_export.tss_info).

The race session's planned TSS is an estimate (legs × IF², the open legs at the predicted
race HR). Once the race is done — the exported session (ext_key racecalc:<event id>) matched
to an activity by plan_match, done_by carrying the activity's TSS — the actual / estimated
ratio of that race is one sample of a per-athlete correction factor applied to the next race
estimates:

    r_i      = actual TSS ÷ the raw (uncorrected) estimate of race i, clipped to RATIO_RANGE
    factor   = exp(w · mean ln r_i), w = n / (n + CALIB_K)      (shrunk toward 1 with few races,
                                                                  the drift_agg.py BETA_K pattern)

Stored in the setting `racepower.race_tss_calib` as {"races": {ext_key: {raw, day, actual}}}:
one entry per race (keyed by ext_key), so re-exporting or recomputing never counts a race
twice. The raw estimate is recorded when the export is written; the actual TSS is (re)read
from the stored plan every time the factor is computed (refresh). Pure functions.
"""
from __future__ import annotations

import math
from typing import Optional

from backend.i18n import _

KEY = "racepower.race_tss_calib"
PREFIX = "racecalc:"
CALIB_K = 3                      # 推估: w = n / (n + 3) — 3 races = half the personal ratio
RATIO_RANGE = (0.5, 2.0)         # 推估: a ratio outside is a mismatched activity / a DNF, clipped
MAX_RACES = 50                   # the newest kept


def _races(store) -> dict:
    return dict((store or {}).get("races") or {}) if isinstance(store, dict) else {}


def record(store, ext_key: str, raw: Optional[float], day: str) -> dict:
    """The raw estimate of an export just written (a re-export before the race replaces it;
    a race already done keeps its sample)."""
    races = _races(store)
    old = races.get(ext_key) or {}
    if raw and raw > 0 and old.get("actual") is None:
        races[ext_key] = {"raw": round(float(raw), 1), "day": day, "actual": None}
    keep = sorted(races.items(), key=lambda kv: kv[1].get("day") or "", reverse=True)[:MAX_RACES]
    return {"races": dict(keep)}


def refresh(store, sessions: list[dict]) -> tuple[dict, bool]:
    """The stored plan's done race exports → each recorded race's actual TSS (the matched
    activity's, done_by.tss); an export still in the plan but no longer done (unlinked) loses
    it, one no longer in the plan keeps its sample. (store, changed)."""
    races = _races(store)
    done, seen = {}, set()
    for s in sessions or []:
        k = s.get("ext_key")
        if not k or not str(k).startswith(PREFIX):
            continue
        seen.add(k)
        if s.get("state") != "done":
            continue
        a = s.get("done_by") if isinstance(s.get("done_by"), dict) else {}
        try:
            t = float(a.get("tss"))
        except (TypeError, ValueError):
            continue
        if t > 0:
            done[k] = round(t, 1)
    changed = False
    for k, e in races.items():
        if k not in seen:
            continue
        act = done.get(k)
        if e.get("actual") != act:
            races[k] = {**e, "actual": act}
            changed = True
    return {"races": races}, changed


def factor(store) -> dict:
    """{factor, n, k, ratio (the unshrunk geometric mean, None without races), badge}."""
    rs = []
    for e in _races(store).values():
        raw, act = e.get("raw"), e.get("actual")
        if raw and act and raw > 0 and act > 0:
            rs.append(min(RATIO_RANGE[1], max(RATIO_RANGE[0], act / raw)))
    n = len(rs)
    if not n:
        return {"factor": 1.0, "n": 0, "k": CALIB_K, "ratio": None, "badge": _("推估")}
    m = sum(math.log(r) for r in rs) / n
    w = n / (n + CALIB_K)
    return {"factor": round(math.exp(w * m), 3), "n": n, "k": CALIB_K, "ratio": round(math.exp(m), 3),
            "badge": _("推估")}


def validate(value) -> None:
    if value is None:
        return
    if not (isinstance(value, dict) and isinstance(value.get("races"), dict) and all(
            isinstance(k, str) and isinstance(e, dict) for k, e in value["races"].items())):
        raise ValueError(f"{KEY} must be {{races: {{ext_key: {{raw, day, actual}}}}}} or null")
