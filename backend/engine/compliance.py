"""
Plan compliance: how well a completed session matched what was planned.

Colours in the style of TrainingPeaks and intervals.icu (docs/research/
competitor-charts.md, recommendation 9):
  - TrainingPeaks athlete guide, "Compliance colors": green within ±20 % of the
    planned duration / TSS, yellow 20–50 % off, orange / red further off, red
    for a missed workout.
    https://www.trainingpeaks.com/learn/trainingpeaks-athlete-user-guide/
  - intervals.icu "Compliance" field: actual load × 100 / planned load, time
    when there is no load. https://forum.intervals.icu/t/compliance-activity-field/47805
Here: the worse of the duration and TSS deviations decides the colour (green
≤ 20 % off, yellow ≤ 50 %, red beyond, or the wrong kind of activity); the
headline % is TSS when both sides have it, else duration (intervals.icu).
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import _

# |actual / planned − 1| upper bounds per level (TrainingPeaks ±20 % / 50 %)
COMPLIANCE = {"green": 0.20, "yellow": 0.50}
LEVEL_LABEL = {"green": "符合計畫", "yellow": "有點偏離", "red": "偏離計畫", "missed": "未完成"}

RUN = {"road", "trail"}
# activity categories (engine/overview.CATEGORIES) that count as the planned kind
KIND_OK = {"easy": RUN, "long": RUN | {"hike"}, "quality": RUN, "test": RUN,
           "hike": {"hike", "trail", "walk"}, "strength": {"strength"}}


def _ratio(actual: Optional[float], planned: Optional[float]) -> Optional[float]:
    if actual is None or not planned or planned <= 0:
        return None
    return float(actual) / float(planned)


def level_of(dev: float) -> str:
    if dev <= COMPLIANCE["green"]:
        return "green"
    if dev <= COMPLIANCE["yellow"]:
        return "yellow"
    return "red"


def session_compliance(s: dict, planned_tss: Optional[float] = None) -> Optional[dict]:
    """{level, pct, duration_pct, tss_pct, wrong_type, label} for a done or
    missed stored session (plan_store dict), None for anything else (also the
    課表待確認 notice, which is not training)."""
    if s.get("kind") == "notice":
        return None
    if s.get("state") == "missed":
        return {"level": "missed", "pct": 0, "duration_pct": None, "tss_pct": None,
                "wrong_type": False, "label": LEVEL_LABEL["missed"]}
    a = s.get("done_by") if s.get("state") == "done" else None
    if not isinstance(a, dict):
        return None
    dur = _ratio(a.get("moving_s"), (s.get("minutes") or 0) * 60.0)
    tss = _ratio(a.get("tss"), planned_tss if planned_tss is not None else s.get("tss"))
    cat = a.get("category")
    wrong = bool(cat) and cat not in KIND_OK.get(s.get("kind"), {cat})
    devs = [abs(r - 1.0) for r in (dur, tss) if r is not None]
    level = "red" if wrong else (level_of(max(devs)) if devs else "green")
    head = tss if tss is not None else dur
    return {"level": level, "pct": None if head is None else round(head * 100),
            "duration_pct": None if dur is None else round(dur * 100),
            "tss_pct": None if tss is None else round(tss * 100),
            "wrong_type": wrong, "label": "類型不符" if wrong else LEVEL_LABEL[level]}


ORDER = ("green", "yellow", "red")


def with_plan_check(comp: Optional[dict], vs: Optional[dict]) -> Optional[dict]:
    """The intensity part of a done session (engine/plan_match.compare), on top of time / TSS:
      沒照課表 (off_plan: planned intervals, ran easy — or the other way — or another sport):
        at least yellow (推估: TrainingPeaks colours time / TSS only; the kind is our addition)
      強度不足 / 偏強 (short, SP-216: 50–80 % of the planned intensity, or an easy run up to
        150 % of a quality dose): at least yellow, counted as 部分 — no longer 沒照課表.
    `intensity_pct` (planned hard) joins the time / TSS %; when the intensity fell short it is
    the headline 完成度 (the number that explains the colour)."""
    if not comp or not vs or comp.get("level") == "missed":
        return comp
    if not vs.get("off_plan") and not vs.get("short"):
        return {**comp, "intensity_pct": vs["intensity_pct"]} if vs.get("intensity_pct") is not None else comp
    lv = comp["level"] if comp.get("level") in ORDER else "green"
    level = ORDER[max(ORDER.index(lv), 1)]
    out = {**comp, "level": level, "intensity_pct": vs.get("intensity_pct")}
    if vs.get("off_plan"):
        return {**out, "off_plan": True, "label": "沒照課表", "off_text": vs.get("text") or ""}
    ipct = vs.get("intensity_pct")
    if ipct is not None and (comp.get("pct") is None or ipct < comp["pct"]):
        out["pct"] = ipct
    return {**out, "short": True, "off_text": vs.get("short_text") or "",
            "label": _("強度不足") if vs.get("grade") == "short" else _("強度偏高")}


def week_compliance(planned_tss: float, done_tss: float, planned_hours: float,
                    done_hours: float) -> Optional[dict]:
    """Week 完成度 over the days so far: TSS when planned, else time."""
    r = _ratio(done_tss, planned_tss) if planned_tss > 0 else _ratio(done_hours, planned_hours)
    if r is None:
        return None
    return {"pct": round(r * 100), "level": level_of(abs(r - 1.0))}


# ---------------------------------------------------------------------------
# 課表達成率 dashboard (api/plan_sessions GET /compliance): the stored plan's
# due sessions over a period, each with its status, and the totals / weeks /
# kinds / phase built from them. Pure.
# ---------------------------------------------------------------------------

# a session's status on the dashboard: ✓ 完成 (green), 部分 (yellow / red: the
# time or TSS is > 20 % off), 沒照課表 (planned intensity / sport not run,
# plan_match.compare, or the wrong kind of activity), ✗ 沒做 (missed); open =
# a past day the synced data doesn't cover yet (not counted)
STATUSES = ("done", "partial", "off_plan", "missed", "open")
DONE_STATUSES = ("done", "partial", "off_plan")
SKIP_KINDS = ("notice", "heat_passive")      # the 課表待確認 reminder; a bath / sauna is ticked, not matched
STREAK_RATE = 0.80                           # a week "達標" = ≥ 80 % of its due sessions done (推估)
KIND_ORDER = ("easy", "long", "quality", "test", "hike", "strength")
# 強度課 split by family (SP-79; workout_templates.session_family — the stored one, else read from
# the steps): 有氧間歇 / VO2max 間歇 / 速度, then one with no interval work (None) as 強度課
FAMILY_ORDER = ("aerobic", "vo2max", "speed", None)


def status_of(s: dict, comp: Optional[dict], today: str) -> Optional[str]:
    if s.get("kind") in SKIP_KINDS or not s.get("day"):
        return None
    st = s.get("state")
    if st == "missed":
        return "missed"
    if st == "done" and comp:
        if comp.get("off_plan") or comp.get("wrong_type"):
            return "off_plan"
        return "done" if comp.get("level") == "green" else "partial"
    if st == "active" and s["day"] < today:
        return "open"
    return None                               # future / today not done yet / deleted


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def session_row(s: dict, today: str) -> Optional[dict]:
    """A calendar-shaped session (tss_est, compliance, vs) -> the dashboard row, or
    None when it isn't due."""
    comp = s.get("compliance")
    stt = status_of(s, comp, today)
    if stt is None:
        return None
    a = s.get("done_by") if s.get("state") == "done" and isinstance(s.get("done_by"), dict) else {}
    fam = (s.get("quality_family") or {}).get("id") if s.get("kind") == "quality" else None
    return {"uid": s["uid"], "day": s["day"], "week_start": s.get("week_start"), "kind": s.get("kind"),
            "family": fam,
            "title": s.get("title") or "", "minutes": s.get("minutes") or 0,
            "planned_tss": round(_f(s.get("tss_est", s.get("tss"))), 1),
            "actual_tss": None if a.get("tss") is None else round(_f(a.get("tss")), 1),
            "actual_s": a.get("moving_s"), "category_label": a.get("category_label"),
            "index": a.get("index"), "status": stt,
            "pct": 0 if stt == "missed" else (comp or {}).get("pct"),
            "duration_pct": (comp or {}).get("duration_pct"), "tss_pct": (comp or {}).get("tss_pct"),
            "level": (comp or {}).get("level"), "off_text": (comp or {}).get("off_text") or "",
            "wrong_type": bool((comp or {}).get("wrong_type"))}


def _count(rows: list[dict]) -> dict:
    n = {k: sum(1 for r in rows if r["status"] == k) for k in STATUSES}
    due = len(rows) - n["open"]
    done = sum(n[k] for k in DONE_STATUSES)
    counted = [r for r in rows if r["status"] != "open"]
    p_tss = sum(r["planned_tss"] for r in counted)
    a_tss = sum(r["actual_tss"] or 0.0 for r in counted)
    p_h = sum(r["minutes"] for r in counted if r["kind"] != "strength") / 60.0
    a_h = sum(_f(r["actual_s"]) for r in counted if r["kind"] != "strength") / 3600.0
    return {**n, "due": due, "completed": done,
            "rate": round(done / due, 3) if due else None,
            "ok_rate": round(n["done"] / due, 3) if due else None,
            "planned_tss": round(p_tss, 1), "actual_tss": round(a_tss, 1),
            "planned_hours": round(p_h, 2), "actual_hours": round(a_h, 2),
            "tss_pct": round(a_tss / p_tss * 100) if p_tss > 0 else None}


def _streak(weeks: list[dict], today: str) -> dict:
    """Consecutive weeks (newest first) with ≥ STREAK_RATE of their due sessions done;
    the running week only counts once it has reached it (it isn't over yet)."""
    n, best, cur, broke = 0, 0, 0, False
    for w in sorted(weeks, key=lambda w: w["start"]):
        if not w["due"]:
            continue
        ok = (w["rate"] or 0) >= STREAK_RATE
        cur = cur + 1 if ok else 0
        best = max(best, cur)
    for w in sorted(weeks, key=lambda w: w["start"], reverse=True):
        if not w["due"]:
            continue
        ok = (w["rate"] or 0) >= STREAK_RATE
        if w["end"] >= today and not ok:
            continue                       # this week is still going
        if not ok:
            broke = True
            break
        n += 1
    return {"weeks": n, "best": best, "threshold": STREAK_RATE, "broken": broke}


def dashboard(sessions: list[dict], week_rows: list[dict], today: str, start: str, end: str,
              phase: Optional[dict] = None, phase_sessions: Optional[list[dict]] = None,
              day_rows: Optional[list[dict]] = None) -> dict:
    """`sessions`: calendar-shaped stored sessions in [start, end] (deleted /
    superseded already left out); `week_rows`: api/plan_sessions._week_rows of the
    range (planned vs done hours / TSS per week); `day_rows`: the same per day
    (api/plan_sessions._day_rows; the page sums them into months); `phase_sessions`:
    the same shape over the current phase (its progress)."""
    rows = [r for r in (session_row(s, today) for s in sessions if start <= (s.get("day") or "") <= end) if r]
    rows.sort(key=lambda r: (r["day"], r["uid"]), reverse=True)

    def bucket(w: dict) -> dict:
        c = _count([r for r in rows if w["start"] <= r["day"] <= w["end"]])
        return {"start": w["start"], "end": w["end"], "phase": w.get("phase"), "phase_label": w.get("phase_label"),
                "planned_tss": round(_f(w.get("planned_tss")), 1), "done_tss": round(_f(w.get("done_tss")), 1),
                "planned_hours": round(_f(w.get("planned_hours")), 2), "done_hours": round(_f(w.get("done_hours")), 2),
                "compliance": w.get("compliance"), "due": c["due"], "completed": c["completed"],
                "ok": c["done"], "rate": c["rate"], "current": w["start"] <= today <= w["end"]}
    weeks = [bucket(w) for w in week_rows]
    days = [bucket(w) for w in (day_rows or [])]
    kinds = []
    for k in KIND_ORDER + tuple(sorted({r["kind"] for r in rows} - set(KIND_ORDER))):
        for fam in FAMILY_ORDER if k == "quality" else (None,):
            kr = [r for r in rows if r["kind"] == k and r.get("family") == fam]
            if kr:
                c = _count(kr)
                kinds.append({"kind": k, **({"family": fam} if k == "quality" else {}),
                              **{x: c[x] for x in ("due", "completed", "done", "partial", "off_plan", "missed",
                                                   "rate", "tss_pct", "planned_tss", "actual_tss")}})
    out = {"start": start, "end": end, "today": today, "totals": _count(rows), "sessions": rows,
           "weeks": weeks, "days": days, "streak": _streak(weeks, today), "by_kind": kinds, "phase": None,
           "levels": COMPLIANCE}
    if phase:
        a, b = phase["start"], phase["end"]
        ps = [s for s in (phase_sessions or []) if a <= (s.get("day") or "") <= b and s.get("kind") not in SKIP_KINDS
              and s.get("state") in ("active", "done", "missed")]
        pr = [r for r in (session_row(s, today) for s in ps) if r]
        c = _count(pr)
        import datetime as _dt
        da, db_ = _dt.date.fromisoformat(a), _dt.date.fromisoformat(b)
        total = (db_ - da).days + 1
        gone = min(total, max(0, (_dt.date.fromisoformat(today) - da).days))
        out["phase"] = {**{k: phase.get(k) for k in ("kind", "label", "start", "end")},
                        "days_total": total, "days_done": gone, "time_pct": round(gone / total * 100) if total else None,
                        "sessions_total": len(ps), "sessions_left": sum(1 for s in ps if s.get("state") == "active"
                                                                         and s["day"] >= today),
                        "due": c["due"], "completed": c["completed"], "rate": c["rate"],
                        "planned_tss": round(sum(_f(s.get("tss_est", s.get("tss"))) for s in ps), 1),
                        "actual_tss": c["actual_tss"]}
    return out
