"""
訓練依據 › 現在用什麼練 (SP-955) — what the plan runs each kind of session by, today, in one card.

Nothing here decides anything: every basis is engine/target_policy.target_policy's answer (the same call the
課表 texts, the editor and the watch push make) on one representative session per kind, with the week plan's
thresholds (their power_state: engine/power_state.py, SP-950). The target texts are drawn from the same
numbers the week plan's texts are (zones.WORKOUT_TARGETS → zones.target_hr for heart rate, power_zone_plan
.target_band × the CP in effect — None outside ① — for power, hr_profile.walk_cap for the walking cap).

The kinds shown follow 主要訓練項目 (engine/primary_sport.py): 越野 shows the trail days, hill repeats, long
climbs, the steep-hill walk, downhill practice and the B2B second day; 路跑 the road long run and the
marathon-pace segment (the 專項期 long run's, overview.week_plan mp_goal_pace_s).

rows(...)      the per-kind rows (pure: what the caller already has)
summary(...)   the plan summary of the stored-plan inputs (phase and its week, this week's structure, the
               next test) — api/plan_sessions GET /basis adds the Zone 3 / 5 unlock and the power state
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import N_, _

# (key, sports, the representative session target_policy reads, zones.WORKOUT_TARGETS id, name)
KINDS = (
    ("easy", ("trail", "road"), {"kind": "easy", "title": "輕鬆跑"}, "z2", None),
    ("trail_easy", ("trail",), {"kind": "easy", "title": "越野輕鬆跑", "terrain": "trail"}, "trail",
     N_("越野輕鬆跑・恢復跑")),
    ("trail_long", ("trail",), {"kind": "mountain", "title": "山路長天"}, "trail", N_("越野長天")),
    ("long", ("road",), {"kind": "long", "title": "長跑"}, "long", N_("長跑")),
    ("z3", ("trail", "road"), {"kind": "quality", "title": "3 區間歇", "terrain": "road", "_zone": "z3"}, "threshold",
     N_("節奏・3 區間歇（平路）")),
    ("z5", ("trail", "road"), {"kind": "quality", "title": "5 區間歇", "terrain": "road", "_zone": "z5"}, "vo2",
     N_("5 區間歇（平路）")),
    ("hill", ("trail",), {"kind": "quality", "title": "爬坡重複", "terrain": "trail"}, "hill",
     N_("爬坡重複（3–8% 坡）")),
    ("climb", ("trail",), {"kind": "easy", "title": "長爬坡"}, "climb", N_("長爬坡")),
    ("walk", ("trail",), {"id": "steep", "kind": "easy", "title": "陡坡健走"}, None, N_("陡坡健走")),
    ("downhill", ("trail",), {"kind": "easy", "title": "下坡練習"}, "downhill", N_("下坡練習")),
    ("b2b", ("trail",), {"kind": "mountain", "title": "B2B 第二天"}, "trail", N_("B2B 第二天")),
)
EASY_NAME = {"trail": N_("路跑輕鬆跑・恢復跑"), "road": N_("輕鬆跑・恢復跑")}
# the kinds an HR cap guards when they run by power (target_policy's hr_cap: road easy / long)
CAPPED = ("easy", "long")
WHY_EXTRA = {"b2b": N_("累的時候心率比較誠實"), "trail_long": N_("長天後段心率飄移超過 5% 就放慢")}
RUN_KINDS = ("easy", "long", "mountain", "quality", "test", "hike")
LONG_KINDS = ("long", "mountain")


def _row_of(tid: Optional[str]) -> Optional[tuple]:
    from backend.engine.zones import WORKOUT_TARGETS
    return next((r for r in WORKOUT_TARGETS if r[0] == tid), None)


def hr_text(tid: Optional[str], th: dict) -> str:
    """The HR part of kind `tid`'s target (zones.target_hr: the 課表心率區間 in effect; the easy rows a cap)."""
    from backend.engine import overview as O
    from backend.engine.zones import target_hr
    r = _row_of(tid)
    if r is None:
        return ""
    lo, hi = target_hr(tid, r[4], r[5], th.get("lthr"), th.get("aet"), th.get("hr_model"))
    if lo is None and hi is None:
        return ""
    if lo is None:
        return _("心率 ≤ {hi} bpm", hi=f"{hi:.0f}")
    return O._range_text([lo, hi], True)


def power_text(tid: Optional[str], th: dict) -> str:
    """The power part (the 課表功率區間's band × the CP in effect; "" without a CP — ② / ③ have none)."""
    from backend.engine import overview as O
    from backend.engine import power_zone_plan as PZP
    cp = th.get("cp")
    r = _row_of(tid)
    if not cp or r is None or (r[2] is None and r[3] is None):
        return ""
    lo, hi = PZP.target_band(tid, r[2], r[3], th.get("power_model"))
    return O._range_text([None if lo is None else lo * cp, None if hi is None else hi * cp], False)


def pace_text(tid: Optional[str], th: dict) -> str:
    """A pace target's line, the 課表 card's words (SP-951 target_policy.pace_text: the kind's % CP band at
    the threshold pace) with the HR cap; only the anchor when the kind has no band."""
    from backend.engine import target_policy as TP
    from backend.engine.e_pace import fmt_pace
    tp = th.get("tpace")
    if not tp:
        return _("閾值配速的 %")
    r = _row_of(tid)
    if r is None or not r[2] or not r[3]:
        return _("閾值配速 {p} /km 的 %", p=fmt_pace(tp))
    p = TP.pace_text(r[2], r[3], tp)
    h = hr_text(tid, th)
    cap = h.split("–")[-1].replace("心率 ≤ ", "").replace(" bpm", "").strip() if h else ""
    return p + " · " + _("心率 ≤ {hr} bpm", hr=cap) if cap else p


def walk_text(th: dict) -> str:
    w = th.get("walk_cap") or {}
    if w.get("value"):
        return _("心率 ≤ {hi} bpm 或 RPE ≤ {rpe}", hi=w["value"], rpe=w.get("rpe") or 13)
    return _("RPE ≤ {rpe}", rpe=13)


def target_text(key: str, tid: Optional[str], pol: dict, th: dict) -> str:
    """What the watch / the session text holds for this kind, in the basis target_policy chose."""
    basis = pol["basis"]
    if key == "walk":
        return walk_text(th) if basis == "hr" else ""
    if basis == "power":
        p = power_text(tid, th)
        h = hr_text(tid, th) if key in CAPPED else ""
        return _("{p}，{h} 當保險", p=p, h=h) if p and h else p
    if basis == "hr":
        return hr_text(tid, th)
    if basis == "pace":
        return pace_text(tid, th)
    return ""


def rows(sport: str, th: Optional[dict], prefs=None, mp_goal_pace_s: Optional[float] = None) -> list[dict]:
    """[{key, name, basis, basis_label, target, why, source, fallback, manual}] for `sport` (trail | road).
    `th`: the week plan's thresholds (with power_state); `prefs`: 課表偏好 (target_basis)."""
    from backend.engine import target_policy as TP
    from backend.engine.e_pace import fmt_pace
    th = dict(th or {})
    sport = "road" if sport == "road" else "trail"
    out = []
    for key, sports, sess, tid, name in KINDS:
        if sport not in sports:
            continue
        pol = TP.target_policy(dict(sess), prefs, th)
        # 「（手動）」: the overall 目標依據 or (SP-964) this type's own decided it; `own` = this type's own (進階設定),
        # which the card's 改回自動 clears
        manual = pol["how"] in ("pref", "type")
        why = pol["why"]
        if key in WHY_EXTRA and pol["basis"] == "hr":
            why = _("{why}；{extra}", why=why, extra=_(WHY_EXTRA[key]))
        out.append({"key": key, "name": _(EASY_NAME[sport] if key == "easy" else name), "basis": pol["basis"],
                    "basis_label": _(TP.LABEL.get(pol["basis"], pol["basis"])),
                    "target": target_text(key, tid, pol, th) or (_("只給建議") if key == "climb" else
                                                                 _("看下降量和技術") if key == "downhill" else ""),
                    "why": why, "source": pol["source"], "fallback": pol["fallback"], "manual": manual,
                    "type_key": key if key in TP.TYPE_KEYS else None, "own": pol["how"] == "type"})
    if sport == "road":
        pol = TP.mp_policy(prefs, th, goal=mp_goal_pace_s)
        why = _("專項期的長跑後段加一段馬拉松配速") if pol["how"] == "auto" and not pol["fallback"] else pol["why"]
        out.append({"key": "mp", "name": _("馬拉松配速段"), "basis": pol["basis"],
                    "basis_label": _(TP.LABEL.get(pol["basis"], pol["basis"])),
                    "target": mp_text(pol["basis"], th, mp_goal_pace_s), "why": why,
                    "source": _("A 賽的目標配速；沒有目標時間就從閾值配速推估"), "fallback": pol["fallback"],
                    "manual": pol["how"] == "type", "type_key": "mp", "own": pol["how"] == "type"})
    return out


def mp_text(basis: str, th: dict, goal: Optional[float] = None) -> str:
    """The MP segment's target in `basis` (workout_steps.mp_target's numbers): the goal pace, else threshold
    pace × MP_PACE; HR = MP_HR × LTHR; power = the same speed as % CP (mp_power_band) × CP."""
    from backend.engine import overview as O
    from backend.engine import workout_steps as WS
    from backend.engine.e_pace import fmt_pace
    if basis == "hr":
        lthr = th.get("lthr")
        return O._range_text([WS.MP_HR[0] * lthr, WS.MP_HR[1] * lthr], True) if lthr else ""
    if basis == "power":
        cp = th.get("cp")
        lo, hi = WS.mp_power_band(goal, th.get("tpace"))
        return O._range_text([lo * cp, hi * cp], False) if cp else ""
    if basis == "none":
        return ""
    return (_("{p} /km（目標配速）", p=fmt_pace(goal)) if goal else _("照閾值配速推估"))


# the 進階設定 rows (SP-964): which types each main sport shows, in the card's order
TYPE_ROWS = {"trail": ("easy", "z3", "z5", "hill"), "road": ("easy", "long", "z3", "z5", "mp")}


def type_rows(sport: str, th: Optional[dict], prefs=None, mp_goal_pace_s: Optional[float] = None) -> list[dict]:
    """訓練依據 › 進階設定 (SP-964): one row per choosable type of `sport` — {key, name, choice (its own basis or
    None = follows the overall one), auto (the basis it gets without its own: the overall 目標依據, else 自動),
    auto_label, options (target_policy.TYPE_CHOICES; 功率 left out without a power meter — ③)}."""
    from backend.engine import power_state as PST
    from backend.engine import target_policy as TP
    th = dict(th or {})
    sport = "road" if sport == "road" else "trail"
    reps = {k: dict(sess) for k, _s, sess, _t, _n in KINDS}
    state = TP._state(th, None)[0]
    names = {r["key"]: r["name"] for r in rows(sport, th, prefs, mp_goal_pace_s)}
    out = []
    for key in TYPE_ROWS[sport]:
        bare = TP.without_type(prefs, key)
        auto = (TP.mp_policy(bare, th, goal=mp_goal_pace_s) if key == "mp"
                else TP.target_policy(reps[key], bare, th))["basis"]
        opts = [b for b in TP.TYPE_CHOICES[key] if b != "power" or state != PST.NONE]
        out.append({"key": key, "name": names.get(key) or _(TP.TYPE_KEY_WORD[key]), "choice": TP.type_basis(prefs, key),
                    "auto": auto, "auto_label": _(TP.LABEL.get(auto, auto)), "options": opts})
    return out


PACE_NAME = {"1": N_("恢復"), "2": N_("有氧"), "3": N_("節奏"), "4": N_("閾值下"), "5a": N_("閾值上"),
             "5b": N_("有氧能力"), "5c": N_("無氧能力")}


def pace_zones(tpace: Optional[float]) -> Optional[list[dict]]:
    """Friel's pace zones (zones.FRIEL_PACE, × the threshold pace) for 我的區間 while a session runs by pace:
    [{id, name, fast, slow (s/km; None = open), pct: [fast ×, slow ×]}], the easiest first; None without one."""
    from backend.engine.zones import FRIEL_PACE
    if not tpace:
        return None
    return [{"id": f"Z{z}", "name": _(PACE_NAME.get(z, z)), "fast": None if lo is None else lo * tpace,
             "slow": None if hi is None else hi * tpace, "pct": [lo, hi]} for z, _n, lo, hi in FRIEL_PACE]


def _d(x) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def week_structure(sessions: list[dict]) -> dict:
    """This week's structure: runs, long days, quality sessions, strength."""
    ss = [s for s in sessions or () if s.get("day")]
    return {"runs": sum(1 for s in ss if s.get("kind") in RUN_KINDS),
            "long": sum(1 for s in ss if s.get("kind") in LONG_KINDS),
            "quality": sum(1 for s in ss if s.get("kind") == "quality"),
            "strength": sum(1 for s in ss if s.get("kind") == "strength")}


def next_test(inp: dict) -> Optional[dict]:
    """The next test the plan holds (this week's and the projected weeks' test sessions from today), else
    the first suggested one ({title, day | None, suggested})."""
    today = _d(inp.get("today")) or dt.date.today()
    cur = inp.get("cur") or {}
    pool = list(cur.get("sessions") or [])
    for w in inp.get("weeks") or ():
        pool += list((w or {}).get("sessions") or [])
    tests = sorted((s for s in pool if s.get("kind") == "test" and _d(s.get("day")) and _d(s["day"]) >= today),
                   key=lambda s: s["day"])
    if tests:
        return {"title": tests[0].get("title") or "", "day": tests[0]["day"][:10], "suggested": False}
    sug = cur.get("test_suggestions") or []
    if sug:
        return {"title": sug[0].get("title") or "", "day": None, "suggested": True}
    return None


def summary(inp: dict) -> dict:
    """{phase: {kind, label, week, start, end} | None, mode_label, sport, week: week_structure, next_test}."""
    cur = inp.get("cur") or {}
    ph = inp.get("phase") or None
    phase = None
    if ph:
        monday = _d((cur.get("week") or {}).get("start"))
        start = _d(ph.get("start"))
        n = (monday - (start - dt.timedelta(days=start.weekday()))).days // 7 + 1 if monday and start else None
        phase = {"kind": ph.get("kind"), "label": str(ph.get("label") or ""), "week": n if n and n > 0 else None,
                 "start": ph.get("start"), "end": ph.get("end")}
    return {"phase": phase, "mode_label": str(cur.get("mode_label") or ""),
            "sport": "road" if cur.get("primary_sport") == "road" else "trail",
            "week": week_structure(cur.get("sessions") or []), "next_test": next_test(inp),
            "mp_goal_pace_s": cur.get("mp_goal_pace_s")}
