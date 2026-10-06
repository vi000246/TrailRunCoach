"""
「坡度超過約 X % 用走的比較省」 on the hill easy runs and long runs (SP-298;
docs/research/run-walk-threshold.md §5.4 #2, §6 #2 ②–④, §7 #2 — the owner
changed the 2026-10-05 decision on 2026-10-06: show it).

A hint, not a rule: the rule stays the easy-run HR cap (心率到上限就走), the
plan is not changed — the hint is added to the session's view only
(api/plan_sessions._view), never to the stored text, so it never shows up as
a plan change or a re-push.

    N   the athlete's climbing rate at easy HR: the median VAM of the sustained
        climbs (engine/climb_vam.extract_climbs: ≥ 8 %, ≥ 8 min, HR lag dropped)
        of the trail runs in the last LOOKBACK_DAYS whose average HR is at or
        under the easy-run cap; ≥ MIN_CLIMBS climbs, else no number.
    X   the grade above which that climbing rate is walked (runwalk.walk_grade:
        the SP-226 grade × speed curve, the PTS line — 走 below it), with the
        athlete's SP-228 shift when there is one, else the default curve
        (the text says 預設值).

Only climbs (runwalk.MIN_GRADE, 3 %): X is never below it, and only sessions on
hills (trail / 山路 / 越野) get the hint — a flat or road session never does.
Nothing is the owner's own number: N and the shift come from each athlete's data.
"""
from __future__ import annotations

import datetime as dt
import math
from statistics import median
from typing import Optional

from backend.i18n import _

LOOKBACK_DAYS = 90          # 推估: recent enough to be today's fitness, long enough for a few hill runs
MIN_CLIMBS = 3              # 推估: fewer sustained climbs at easy HR → no number
VAM_ROUND = 10.0            # m/h shown (the climbs are ± tens of m/h anyway)
HILL_WORDS = ("山路", "越野")   # the stored titles of the hill easy / long sessions (plan_prefs, overview)


def is_hill_session(s: dict) -> bool:
    """An easy run or long run on hills: terrain trail / hike, or a 山路 / 越野 title when the
    terrain is unspecified. Road / flat sessions (terrain road, 「平路或緩坡」 LSD) → False."""
    if (s or {}).get("kind") not in ("easy", "long"):
        return False
    t = s.get("terrain")
    if t == "road":
        return False
    if t in ("trail", "hike"):
        return True
    title = s.get("title") or ""
    return any(w in title for w in HILL_WORDS)


def easy_vam(climbs: list[dict], cap_hr: Optional[float]) -> Optional[dict]:
    """{"vam" (m/h, median), "n"} of the climbs ({"vam", "hr"}) with average HR ≤ `cap_hr`;
    None without a cap or with fewer than MIN_CLIMBS."""
    if not cap_hr or not math.isfinite(float(cap_hr)):
        return None
    vs = [float(c["vam"]) for c in climbs or ()
          if c.get("vam") and c["vam"] > 0 and c.get("hr") and c["hr"] <= cap_hr]
    if len(vs) < MIN_CLIMBS:
        return None
    return {"vam": float(median(vs)), "n": len(vs)}


def recent_climbs(ds, today: dt.date, days: int = LOOKBACK_DAYS) -> list[dict]:
    """The sustained climbs ({"vam", "hr", "date"}) of the trail runs in (today − days, today]
    (engine/climb_vam via its disk-cached per-activity series)."""
    from backend.engine.panels import climb_vam as PCV
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    out = []
    for w in ds.workouts:
        if not (tday - days < math.floor(w.day) <= tday) or not PCV.is_trail_run(w):
            continue
        for s in (PCV._cached(ds, w).get("segments") or []):
            out.append({"vam": s.get("vam"), "hr": s.get("avg_hr"), "date": w.entry.start.date().isoformat()})
    return out


def personal_shift(ds, today: dt.date) -> dict:
    """The athlete's SP-228 walk–run shift ({"shift", "personal", "n"}) from the same 365-day
    run windows the race calculator fits it on (athlete.grade_samples → runwalk.fit_shift);
    the default curve (shift 0, personal False) when it can't be read."""
    try:
        from backend.engine.racepower import athlete as A
        from backend.engine.racepower import runwalk as RW
        from backend.engine.wko5expr.dataset import date_to_day
        tday = date_to_day(today)
        runs = [w for w in ds.workouts if w.sport == "run" and tday - A.RE_WINDOW_DAYS < w.day <= tday + 1]
        f = RW.fit_shift(A.grade_samples(ds, runs))
        return {"shift": float(f.get("shift") or 0.0), "personal": bool(f.get("personal")), "n": int(f.get("n") or 0)}
    except Exception:                       # noqa: BLE001 — the hint is optional
        return {"shift": 0.0, "personal": False, "n": 0}


def hint(vam: Optional[dict], rw: Optional[dict] = None) -> Optional[dict]:
    """{"vam" (m/h, shown rounded), "grade" (%), "personal", "n"} for the easy climbing rate `vam`
    (easy_vam) and the walk–run shift `rw` (personal_shift — used only when it is personal);
    None without a rate or when that rate is still run at 100 %. Numbers only: text() words it
    in the request's language."""
    from backend.engine.racepower import runwalk as RW
    if not vam or not vam.get("vam"):
        return None
    rw = rw or {}
    personal = bool(rw.get("personal"))
    g = RW.walk_grade(vam["vam"], float(rw.get("shift") or 0.0) if personal else 0.0)
    if g is None:
        return None
    return {"vam": round(vam["vam"] / VAM_ROUND) * VAM_ROUND, "grade": max(RW.MIN_GRADE * 100.0, round(g * 100.0)),
            "personal": personal, "n": vam.get("n")}


def text(h: Optional[dict]) -> Optional[str]:
    """「提示：照你輕鬆心率的爬升速度（約 N m/h），坡度超過約 X% 用走的比較省（預設值）；規則仍是心率上限」."""
    if not h:
        return None
    basis = _("依你的跑走紀錄校正") if h.get("personal") else _("預設值")
    return _("提示：照你輕鬆心率的爬升速度（約 {vam:.0f} m/h），坡度超過約 {grade:.0f}% 用走的比較省"
             "（{basis}）；規則仍是心率上限", vam=h["vam"], grade=h["grade"], basis=basis)


def for_session(s: dict, h: Optional[dict]) -> Optional[str]:
    """The hint's text for a planned (active) hill easy run / long run, else None."""
    if not h or (s or {}).get("state", "active") != "active" or not is_hill_session(s):
        return None
    return text(h)


def compute(ds, today: dt.date, cap_hr: Optional[float]) -> Optional[dict]:
    """The hint for this athlete today (recent_climbs → easy_vam → personal_shift → hint)."""
    v = easy_vam(recent_climbs(ds, today), cap_hr)
    if v is None:
        return None
    return hint(v, personal_shift(ds, today))
