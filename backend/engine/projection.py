"""
Project the weekly plan past this week, up to a horizon (the phase end,
capped at MAX_WEEKS), so a whole training phase can be scheduled.

Structural companion to overview.week_plan(): it does not recompute anything
from samples. It starts from this week's week_plan() output (target hours,
TSS per hour, CTL, the 8-week history, long-session weekday, thresholds) and
rolls the same rules forward week by week:

  * base / specific: CTL ramp goal (RAMP_GOAL) capped at +10 % (at least
    +0.5 h) of max(4-week mean, last week); after 3 build weeks a recovery
    week at 65 % of those 3 (3:1)
  * taper 40–50 % of the 6-week mean, event week 30 %, recovery 50 %,
    transition 65 % of the 4-week mean
  * the same session template: long, one quality session, strength, easy
    runs filling the rest

Everything beyond next week is provisional: reconcile recalculates it from
the real state as the weeks arrive.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Optional

from backend.engine import overview as O
from backend.engine.zones import WORKOUT_TARGETS

MAX_WEEKS = 8                 # never schedule further ahead than this
MODE_LABELS = {"base": "基礎期", "specific": "專項期", "taper": "減量期", "event": "比賽週",
               "recovery": "恢復期", "transition": "轉換期", "recovery_week": "恢復週"}


def _d(s) -> dt.date:
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def target_texts(th: dict) -> dict[str, str]:
    """Same strings as overview._targets(), from the thresholds in week_plan()['thresholds']."""
    cp, lthr, aet = th.get("cp"), th.get("lthr"), th.get("aet")
    rows = []
    for tid, name, plo, phi, hlo, hhi, primary, example, src in WORKOUT_TARGETS:
        def hr(x):
            if x is None or lthr is None:
                return None
            return aet if x == "aet" else x * lthr
        rows.append({"id": tid, "primary": primary,
                     "power": [None if plo is None or not cp else plo * cp, None if phi is None or not cp else phi * cp],
                     "hr": [hr(hlo), hr(hhi)]})
    return O._targets({"rows": rows})


def phase_kind(phases: list, day: dt.date) -> str:
    for p in phases:
        s, e = _d(p["start"] if isinstance(p, dict) else p.start), _d(p["end"] if isinstance(p, dict) else p.end)
        if s <= day <= e:
            return p["kind"] if isinstance(p, dict) else p.kind
    return "base"


def _next_event_start(phases: list, day: dt.date) -> Optional[dt.date]:
    for p in phases:
        kind = p["kind"] if isinstance(p, dict) else p.kind
        s = _d(p["start"] if isinstance(p, dict) else p.start)
        if kind == "event" and s >= day:
            return s
    return None


def week_hours(kind: str, hist: list[float], build: list[bool], ctl0: float, r: float,
               cc: float, days_to_a: Optional[int]) -> tuple[float, str, list[str]]:
    """(hours, mode, why) for one projected week; `hist` = weekly hours, oldest first."""
    base4 = statistics.mean(hist[-4:]) if hist else 0.0
    last = hist[-1] if hist else 0.0
    ref = max(base4, last)
    why: list[str] = []
    if kind in ("base", "specific"):
        if len(build) >= 3 and all(build[-3:]):
            h = 0.65 * statistics.mean(hist[-3:])
            return h, "recovery_week", ["連續 3 週加量後的恢復週（前 3 週平均的 65%）"]
        f7 = 1.0 - (1.0 - 1.0 / cc) ** 7
        need_h = 7.0 * (ctl0 + O.RAMP_GOAL[kind] / f7) / max(r, 1.0)
        cap = max(1.10 * ref, ref + 0.5)
        h = min(max(need_h, base4), cap)
        why.append(f"CTL {ctl0:.0f} 每週 +{O.RAMP_GOAL[kind]:.0f}，上限 +10%（至少 +0.5 h）→ {h:.1f} h")
        return h, kind, why
    if kind == "taper":
        base6 = statistics.mean(hist[-6:]) if hist else 0.0
        share = 0.4 if days_to_a is not None and days_to_a <= 7 else 0.5
        return base6 * share, kind, [f"減量期：平常 {base6:.1f} h × {share:.0%}"]
    if kind == "event":
        return 0.3 * base4, kind, ["比賽週：短、輕鬆"]
    share = 0.5 if kind == "recovery" else 0.65
    return share * base4, kind, [f"{MODE_LABELS.get(kind, kind)}：近 4 週的 {share:.0%}"]


def week_sessions(monday: dt.date, kind: str, mode: str, hours: float, tph: float,
                  tgt: dict, long_wd: int, longest: float, mountain: bool,
                  allow_quality: bool, strength_tss: float, aet: Optional[float],
                  base_quality: Optional[dict] = None, prefs=None, rates: Optional[dict] = None,
                  notes: Optional[list] = None) -> list[dict]:
    """The week_plan() session template for a projected week, placed on days.
    `base_quality`: this week's base-phase quality session (week_plan picks
    閾值下 N×8 分 when the drift streak first unlocks intervals or the last one
    faded); projected base weeks repeat it instead of 閾值 3×10.
    `prefs` (課表偏好, engine/plan_prefs.py): shaped and placed like week_plan();
    `rates` = TSS / h per category for it, `notes` collects its notes."""
    total = hours * 60.0
    ss: list[dict] = []

    def add(**kw):
        ss.append({"target": "", "detail": "", "source": "", "tss": 0.0, "day": None,
                   "done": False, "done_by": None, **kw})

    if kind in ("base", "specific") and mode != "recovery_week":
        long_min = max(60.0, min(0.30 * total, max(longest, 60.0) * 1.15))
        long_min = min(long_min, 0.5 * total) if total >= 120 else long_min
        add(id="long", kind="long", title="長時間輕鬆" + ("（山路）" if mountain else ""),
            minutes=int(round(long_min / 5) * 5), target=tgt.get("long", ""),
            detail=("有山路就走山路，陡坡用走的" if mountain else "平路或緩坡")
            + f"；全程心率壓在 AeT{f' {aet:.0f} bpm' if aet else ''} 以下，爬坡可以走",
            source=O.SRC_KOOP if kind == "specific" else O.SRC_UA, tss=long_min / 60.0 * tph)
        if allow_quality and kind == "specific":
            add(id="quality", kind="quality", title="爬坡間歇 5×4 分", minutes=60, target=tgt.get("supra", ""),
                detail="上坡 4 分鐘（6–10% 坡），慢跑或走下來恢復；暖身 15 分、緩和 10 分",
                source=O.SRC_PALLADINO + "（Supra-threshold）", tss=75.0)
        elif allow_quality and kind == "base" and base_quality:
            add(id="quality", kind="quality", title=base_quality["title"], minutes=base_quality["minutes"],
                target=base_quality.get("target", ""), detail=base_quality.get("detail", ""),
                source=base_quality.get("source", ""), tss=float(base_quality.get("tss") or 65.0))
        elif allow_quality:
            add(id="quality", kind="quality", title="閾值 3×10 分", minutes=60, target=tgt.get("threshold", ""),
                detail="休 2–3 分鐘；暖身 15 分、緩和 10 分", source=O.SRC_PALLADINO + "（3B）", tss=70.0)
    elif kind == "taper":
        add(id="quality", kind="quality", title="短強度 4×3 分", minutes=45, target=tgt.get("threshold", ""),
            detail="保留強度、不累積疲勞（98–102% CP）", source=O.SRC_BOSQUET, tss=45 / 60 * 65)
    n_strength = 2 if kind in ("base", "transition", "recovery") else 1
    for i in range(n_strength):
        add(id=f"strength{i + 1}", kind="strength", title="肌力（下肢單腳＋核心）", minutes=35,
            detail="膝主導＋臀中肌；安排在輕鬆日或跑完後", source=O.SRC_UA, tss=strength_tss)
    used = sum(s["minutes"] for s in ss if s["kind"] != "strength")
    left = max(0.0, total - used)
    n_easy = 0 if left < 25 else max(1, min(5, int(round(left / 50.0))))
    for i in range(n_easy):
        m = left / n_easy
        strides = kind == "base" and i == 0 and mode != "recovery_week"
        add(id=f"easy{i + 1}", kind="easy", title="輕鬆跑" + ("＋坡道衝刺 8×10 秒" if strides else ""),
            minutes=int(round(m / 5) * 5), target=tgt.get("z2", ""),
            detail="心率不超過 AeT" + ("；最後 8 趟 10 秒上坡衝刺，走下來恢復" if strides else ""),
            source=O.SRC_UA, tss=m / 60.0 * tph)
    if prefs is not None and prefs.active:
        from backend.engine import plan_prefs as PP
        r = {"road": tph, "trail": tph, "hike": tph, "strength": strength_tss / 35 * 60, **(rates or {})}
        ctx = PP.Ctx(kind=kind, mode=mode, allow_quality=allow_quality, rates=r, aet=aet,
                     slots=sum(bool(x) for x in prefs.days), notes=notes if notes is not None else [])
        ss = PP.shape(ss, total, prefs, ctx)
        PP.place(ss, [monday + dt.timedelta(days=i) for i in range(7)], PP.long_weekday(prefs, long_wd), prefs)
        return ss
    _place(ss, monday, long_wd)
    return ss


def _place(ss: list[dict], monday: dt.date, long_wd: int) -> None:
    days = [monday + dt.timedelta(days=i) for i in range(7)]
    free = list(days)
    main = [s for s in ss if s["kind"] != "strength"]
    long_day = None
    for s in sorted(main, key=lambda s: {"long": 0, "quality": 1, "test": 1}.get(s["kind"], 2)):
        if not free:
            break
        if s["kind"] == "long":
            pick = days[long_wd] if days[long_wd] in free else free[-1]
            long_day = pick
        elif s["kind"] in ("quality", "test"):
            order = [days[i] for i in (1, 2, 3, 0, 4, 5, 6)]
            pick = next((d for d in order if d in free and (long_day is None or abs((d - long_day).days) >= 2)),
                        free[0])
        else:
            pick = free[0]
        s["day"] = pick.isoformat()
        free.remove(pick)
    easy_days = [_d(s["day"]) for s in main if s["kind"] == "easy" and s["day"]]
    taken: set[dt.date] = set()
    for s in [s for s in ss if s["kind"] == "strength"]:
        cands = sorted(d for d in free + easy_days
                       if (long_day is None or d != long_day - dt.timedelta(days=1)) and d not in taken)
        if cands:
            s["day"] = cands[0].isoformat()
            taken.add(cands[0])
            if cands[0] in free:
                free.remove(cands[0])


def _gate_inputs(cur: dict) -> dict:
    """week_plan()'s quality-gate inputs (levels + drift streak). A `cur`
    without them (built before week_plan returned them) falls back to reading
    this week's sessions: a quality session, a non-base phase or a recovery /
    rest week (none by design) -> allowed. A CP-test session says nothing
    (week_plan puts the test in place of the quality session)."""
    g = cur.get("quality_gate")
    if g is not None:
        return {"levels": dict(g.get("levels") or {}), "streak_ok": bool(g.get("streak_ok"))}
    ok = (any(s["kind"] == "quality" for s in cur.get("sessions") or []) or cur.get("phase") != "base"
          or cur.get("mode") not in ("base", "specific"))
    return {"levels": {"intensity": "good" if ok else "na", "drift": "na"}, "streak_ok": ok}


def allow_quality(kind: str, gate: dict, base_q: Optional[dict] = None) -> bool:
    """week_plan()'s rule, evaluated for one projected week's phase: the gate
    (intensity / drift not bad; in base also the drift streak), then in base
    either the unlocked 閾值下 session this week carried (`base_q`) or the
    intensity indicator good for 閾值 3×10. Not copied from this week, so a
    CP-test week or the base drift gate doesn't leak into later weeks."""
    from backend.engine import workout_review as WR
    levels = gate.get("levels") or {}
    if not WR.quality_gate(kind, levels, bool(gate.get("streak_ok"))):
        return False
    if kind == "base":
        return bool(base_q) or levels.get("intensity") == "good"
    return True


def project_weeks(cur: dict, phases: list, until: dt.date, ctlconstant: float = 42.0,
                  atlconstant: float = 7.0, prefs=None) -> list[dict]:
    """Weeks after cur['week'] (a week_plan() result) up to `until` (≤ MAX_WEEKS).
    `ctlconstant` / `atlconstant`: the athlete's (ds.athlete), as for the PMC.
    `prefs`: the 課表偏好 week_plan() used (None / defaults = the original rules)."""
    PR = prefs if prefs is not None and prefs.active else None
    monday = _d(cur["week"]["start"])
    cap = monday + dt.timedelta(weeks=MAX_WEEKS + 1) - dt.timedelta(days=1)
    until = min(until, cap)
    tph = float(cur["target"].get("tss_per_hour") or 50.0)
    th = cur.get("thresholds") or {}
    tgt = target_texts(th)
    long_wd = O.WEEKDAYS.index(cur.get("long_weekday") or "六")
    cur_s = cur.get("sessions") or []
    long_s = next((s for s in cur_s if s.get("id", s.get("gen_key")) == "long" or s["kind"] == "long"), None)
    longest = float(long_s["minutes"]) if long_s else 60.0
    mountain = bool(long_s and "山路" in long_s["title"])
    rates = cur.get("tss_per_category") if PR is not None else None
    gate = _gate_inputs(cur)
    base_q = next((s for s in cur_s if s["kind"] == "quality" and s["title"].startswith("閾值下")), None) \
        if cur.get("phase") == "base" else None
    st = next((s for s in cur_s if s["kind"] == "strength"), None)
    strength_tss = float(st["tss"]) if st else 35 / 60 * 30
    hist = [float(h["hours"]) for h in cur.get("history") or []] + [float(cur["target"]["hours"])]
    build = [False] * (len(hist) - 1) + [cur.get("mode") in ("base", "specific")]
    for i in range(1, len(hist) - 1):
        build[i] = hist[i] >= 0.95 * hist[i - 1] and hist[i] > 0.5
    ctl = float((cur.get("load") or {}).get("ctl_end") or 0.0)
    atl = float((cur.get("load") or {}).get("atl_end") or 0.0)
    out = []
    week = monday + dt.timedelta(weeks=1)
    while week <= until:
        kind = phase_kind(phases, week)
        ev = _next_event_start(phases, week)
        days_to = (ev - week).days if ev else None
        hours, mode, why = week_hours(kind, hist, build, ctl, tph, ctlconstant, days_to)
        notes: list = []
        if PR is not None and PR.weekly_hours is not None and hours > PR.weekly_hours:
            hours = PR.weekly_hours
            why = why + [f"你的每週時數上限 {PR.weekly_hours:g} h"]
        ss = week_sessions(week, kind, mode, hours, tph, tgt, long_wd, longest, mountain,
                           allow_quality(kind, gate, base_q), strength_tss, th.get("aet"), base_q,
                           prefs=PR, rates=rates, notes=notes)
        # a session _place() found no day for has day None: keep it out of the date test
        ss = [s for s in ss if not s["day"] or _d(s["day"]) <= until] if ss else ss
        by_day = {}
        for s in ss:
            if s["day"]:
                by_day[s["day"]] = by_day.get(s["day"], 0.0) + s["tss"]
        planned = [by_day.get((week + dt.timedelta(days=i)).isoformat(), 0.0) for i in range(7)]
        proj = O.project(ctl, atl, planned, ctlconstant, atlconstant)
        ctl0 = ctl
        ctl, atl = proj[-1]["ctl"], proj[-1]["atl"]
        out.append({"start": week.isoformat(), "phase": kind, "mode": mode,
                    "mode_label": MODE_LABELS.get(mode, mode), "hours": hours,
                    "tss": sum(planned), "ctl_start": ctl0, "ctl_end": ctl,
                    "provisional": week > monday + dt.timedelta(weeks=1), "why": why,
                    "sessions": [s for s in ss if s["day"]], **({"notes": notes} if PR is not None else {})})
        long_n = next((s for s in ss if s["id"] == "long"), None)
        if long_n:
            longest = float(long_n["minutes"])
        if PR is not None:
            # what the preferences actually let through (a hard cap can leave less)
            hours = sum(s["minutes"] for s in ss if s["kind"] != "strength" and s["day"]) / 60.0
        build.append(mode in ("base", "specific") and hours >= 0.95 * hist[-1] and hours > 0.5)
        hist.append(hours)
        week += dt.timedelta(weeks=1)
    return out
